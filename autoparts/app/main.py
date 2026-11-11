"""Веб-застосунок: вітрина (пошук, підбір за авто, кошик, оформлення) + адмінка."""
import csv
import io
import json
import os
import secrets

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from . import novaposhta, payments, shop
from .db import (ORDER_STATUSES, SUPPLIER_ITEM_STATUSES, get_db, get_setting, init_db, norm_brand,
                 set_setting)
from .importer import column_map_of, import_applicability, import_crosses, import_offers, parse_pricelist
from .notify import telegram
from .suppliers import adapter_for

HERE = os.path.dirname(os.path.abspath(__file__))
SHOP_NAME = os.environ.get("SHOP_NAME", "АвтоДеталі")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "admin")

app = FastAPI(title=SHOP_NAME, docs_url=None, redoc_url=None)
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SECRET_KEY", "dev-secret-change-me"),
                   max_age=60 * 60 * 24 * 30)
app.mount("/static", StaticFiles(directory=os.path.join(HERE, "static")), name="static")
templates = Jinja2Templates(directory=os.path.join(HERE, "templates"))
templates.env.globals.update(SHOP_NAME=SHOP_NAME, ORDER_STATUSES=ORDER_STATUSES,
                             SUPPLIER_ITEM_STATUSES=SUPPLIER_ITEM_STATUSES)
templates.env.filters["uah"] = lambda v: f"{v:,.0f}".replace(",", " ") + " ₴"

SETTING_KEYS = ["np_api_key", "liqpay_public_key", "liqpay_private_key", "liqpay_sandbox", "site_url",
                "telegram_bot_token", "telegram_chat_id", "shop_phone"]


@app.on_event("startup")
def _startup():
    init_db()


def render(request, name, **ctx):
    cart = request.session.get("cart", {})
    ctx.setdefault("cart_count", sum(cart.values()))
    ctx["request"] = request
    return templates.TemplateResponse(request, name, ctx)


def redirect(url):
    return RedirectResponse(url, status_code=303)


# ───────────────────────────── Вітрина ─────────────────────────────

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    with get_db() as con:
        makes = con.execute("SELECT * FROM makes ORDER BY name").fetchall()
        n_offers = con.execute("SELECT COUNT(*) FROM offers").fetchone()[0]
    return render(request, "index.html", makes=makes, n_offers=n_offers)


@app.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = ""):
    with get_db() as con:
        exact, analogs, by_name = shop.search(con, q)
    return render(request, "search.html", q=q, exact=exact, analogs=analogs, by_name=by_name)


@app.get("/catalog", response_class=HTMLResponse)
def catalog(request: Request, make: int | None = None, model: int | None = None, category: int | None = None):
    with get_db() as con:
        makes = con.execute("SELECT * FROM makes ORDER BY name").fetchall()
        models = con.execute("SELECT * FROM models WHERE make_id=? ORDER BY name, year_from",
                             (make,)).fetchall() if make else []
        categories = con.execute(
            "SELECT c.id, c.name, COUNT(*) AS n FROM applicability a JOIN categories c ON c.id=a.category_id"
            " WHERE a.model_id=? GROUP BY c.id ORDER BY c.name", (model,)).fetchall() if model else []
        parts = []
        if model and category:
            keys = con.execute("SELECT brand_norm, article_norm FROM applicability WHERE model_id=? AND category_id=?",
                               (model, category)).fetchall()
            parts = sorted(shop.best_offers_for(con, [(k[0], k[1]) for k in keys]), key=lambda x: x["sell"])
        sel_make = con.execute("SELECT * FROM makes WHERE id=?", (make,)).fetchone() if make else None
        sel_model = con.execute("SELECT * FROM models WHERE id=?", (model,)).fetchone() if model else None
        sel_cat = con.execute("SELECT * FROM categories WHERE id=?", (category,)).fetchone() if category else None
    return render(request, "catalog.html", makes=makes, models=models, categories=categories, parts=parts,
                  make=sel_make, model=sel_model, category=sel_cat)


@app.post("/cart/add")
def cart_add(request: Request, offer_id: int = Form(...), qty: int = Form(1), back: str = Form("/cart")):
    cart = request.session.get("cart", {})
    cart[str(offer_id)] = cart.get(str(offer_id), 0) + max(1, qty)
    request.session["cart"] = cart
    return redirect(back if back.startswith("/") else "/cart")


@app.post("/cart/update")
async def cart_update(request: Request):
    form = await request.form()
    cart = {}
    for k, v in form.items():
        if k.startswith("qty_") and v.isdigit() and int(v) > 0:
            cart[k[4:]] = int(v)
    request.session["cart"] = cart
    return redirect("/cart")


@app.get("/cart", response_class=HTMLResponse)
def cart_page(request: Request):
    with get_db() as con:
        lines, total = shop.cart_lines(con, request.session.get("cart", {}))
    return render(request, "cart.html", lines=lines, total=total)


@app.get("/checkout", response_class=HTMLResponse)
def checkout_page(request: Request):
    with get_db() as con:
        lines, total = shop.cart_lines(con, request.session.get("cart", {}))
        np_enabled = bool(get_setting(con, "np_api_key"))
        liqpay_enabled = bool(get_setting(con, "liqpay_public_key") and get_setting(con, "liqpay_private_key"))
    if not lines:
        return redirect("/cart")
    return render(request, "checkout.html", lines=lines, total=total, np_enabled=np_enabled,
                  liqpay_enabled=liqpay_enabled, error=None)


@app.post("/checkout")
async def checkout_submit(request: Request):
    form = dict(await request.form())
    if not form.get("customer_name", "").strip() or len("".join(c for c in form.get("phone", "") if c.isdigit())) < 10:
        return redirect("/checkout")
    form.setdefault("payment_method", "cod")
    if form["payment_method"] not in ("cod", "liqpay"):
        form["payment_method"] = "cod"
    with get_db() as con:
        order_id, total = shop.create_order(con, request.session.get("cart", {}), form)
        token, chat = get_setting(con, "telegram_bot_token"), get_setting(con, "telegram_chat_id")
    request.session["cart"] = {}
    request.session.setdefault("my_orders", []).append(order_id)
    telegram(token, chat, f"🛒 Нове замовлення №{order_id} на {total:.0f} грн\n{form['customer_name']}, {form['phone']}")
    return redirect(f"/order/{order_id}")


@app.get("/order/{order_id}", response_class=HTMLResponse)
def order_page(request: Request, order_id: int):
    if order_id not in request.session.get("my_orders", []) and not request.session.get("admin"):
        raise HTTPException(404)
    with get_db() as con:
        order = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        items = con.execute("SELECT * FROM order_items WHERE order_id=?", (order_id,)).fetchall()
        liqpay = None
        if order["payment_method"] == "liqpay" and order["payment_status"] != "paid":
            pub, priv = get_setting(con, "liqpay_public_key"), get_setting(con, "liqpay_private_key")
            site = get_setting(con, "site_url") or str(request.base_url).rstrip("/")
            if pub and priv:
                liqpay = payments.liqpay_form(pub, priv, order_id, order["total"], f"Замовлення №{order_id}",
                                              f"{site}/order/{order_id}", f"{site}/payment/liqpay/callback",
                                              sandbox=get_setting(con, "liqpay_sandbox") == "1")
    return render(request, "order.html", order=order, items=items, liqpay=liqpay,
                  liqpay_url=payments.LIQPAY_CHECKOUT_URL)


@app.post("/payment/liqpay/callback")
def liqpay_callback(data: str = Form(...), signature: str = Form(...)):
    with get_db() as con:
        payload = payments.liqpay_verify(get_setting(con, "liqpay_private_key"), data, signature)
        if not payload:
            raise HTTPException(400, "bad signature")
        order_id = int(str(payload.get("order_id", "")).removeprefix("order-") or 0)
        status = "paid" if payload.get("status") in ("success", "sandbox") else payload.get("status", "pending")
        con.execute("UPDATE orders SET payment_status=? WHERE id=?", (status, order_id))
    return {"ok": True}


@app.get("/api/np/cities")
def np_cities(q: str = ""):
    with get_db() as con:
        key = get_setting(con, "np_api_key")
    if not key or len(q) < 2:
        return []
    try:
        return novaposhta.cities(key, q)
    except Exception:
        return []


@app.get("/api/np/warehouses")
def np_warehouses(city_ref: str, q: str = ""):
    with get_db() as con:
        key = get_setting(con, "np_api_key")
    if not key:
        return []
    try:
        return novaposhta.warehouses(key, city_ref, q)
    except Exception:
        return []


# ───────────────────────────── Адмінка ─────────────────────────────

def require_admin(request: Request):
    if not request.session.get("admin"):
        raise HTTPException(303, headers={"Location": "/admin/login"})


@app.get("/admin/login", response_class=HTMLResponse)
def admin_login_page(request: Request):
    return render(request, "admin/login.html", error=None)


@app.post("/admin/login")
def admin_login(request: Request, password: str = Form(...)):
    if secrets.compare_digest(password, ADMIN_PASSWORD):
        request.session["admin"] = True
        return redirect("/admin")
    return render(request, "admin/login.html", error="Невірний пароль")


@app.get("/admin/logout")
def admin_logout(request: Request):
    request.session.pop("admin", None)
    return redirect("/")


@app.get("/admin", response_class=HTMLResponse)
def admin_orders(request: Request, status: str = ""):
    require_admin(request)
    with get_db() as con:
        sql = "SELECT * FROM orders" + (" WHERE status=?" if status else "") + " ORDER BY id DESC LIMIT 300"
        orders = con.execute(sql, (status,) if status else ()).fetchall()
        stats = con.execute(
            "SELECT COUNT(*) n, COALESCE(SUM(o.total),0) revenue,"
            " COALESCE((SELECT SUM(i.qty*(i.price_sell-i.price_in)) FROM order_items i JOIN orders o2"
            "  ON o2.id=i.order_id WHERE o2.status!='canceled'),0) margin"
            " FROM orders o WHERE o.status!='canceled'").fetchone()
    return render(request, "admin/orders.html", orders=orders, status=status, stats=stats,
                  default_password=ADMIN_PASSWORD == "admin")


@app.get("/admin/orders/{order_id}", response_class=HTMLResponse)
def admin_order(request: Request, order_id: int, msg: str = ""):
    require_admin(request)
    with get_db() as con:
        order = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        if not order:
            raise HTTPException(404)
        items = con.execute("SELECT * FROM order_items WHERE order_id=? ORDER BY supplier_name", (order_id,)).fetchall()
    groups = {}
    for it in items:
        groups.setdefault((it["supplier_id"], it["supplier_name"]), []).append(it)
    return render(request, "admin/order.html", order=order, groups=groups, msg=msg,
                  cost=sum(i["qty"] * i["price_in"] for i in items))


@app.post("/admin/orders/{order_id}")
def admin_order_update(request: Request, order_id: int, status: str = Form(...), payment_status: str = Form(...),
                       ttn: str = Form("")):
    require_admin(request)
    with get_db() as con:
        con.execute("UPDATE orders SET status=?, payment_status=?, ttn=? WHERE id=?",
                    (status, payment_status, ttn.strip(), order_id))
    return redirect(f"/admin/orders/{order_id}")


@app.post("/admin/orders/{order_id}/items/{item_id}")
def admin_item_update(request: Request, order_id: int, item_id: int, supplier_status: str = Form(...),
                      supplier_ref: str = Form("")):
    require_admin(request)
    with get_db() as con:
        con.execute("UPDATE order_items SET supplier_status=?, supplier_ref=? WHERE id=? AND order_id=?",
                    (supplier_status, supplier_ref.strip(), item_id, order_id))
    return redirect(f"/admin/orders/{order_id}")


@app.get("/admin/orders/{order_id}/supplier/{supplier_id}.csv")
def admin_supplier_csv(request: Request, order_id: int, supplier_id: int):
    """Файл для передачі замовлення постачальнику (кабінет / email)."""
    require_admin(request)
    with get_db() as con:
        order = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        items = con.execute("SELECT * FROM order_items WHERE order_id=? AND supplier_id=?",
                            (order_id, supplier_id)).fetchall()
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(["Бренд", "Артикул", "Назва", "Кількість", "Ціна закупівлі", "Сума"])
    for i in items:
        w.writerow([i["brand"], i["article"], i["name"], i["qty"], f"{i['price_in']:.2f}",
                    f"{i['qty'] * i['price_in']:.2f}"])
    w.writerow([])
    w.writerow(["Отримувач (дропшипінг):", order["customer_name"], order["phone"]])
    w.writerow(["Доставка НП:", order["city"], order["np_branch"]])
    if order["payment_method"] == "cod" and order["payment_status"] != "paid":
        w.writerow(["Накладений платіж:", f"{sum(i['qty'] * i['price_sell'] for i in items):.2f}"])
    return Response(buf.getvalue().encode("utf-8-sig"), media_type="text/csv",
                    headers={"Content-Disposition": f'attachment; filename="order{order_id}_supplier{supplier_id}.csv"'})


@app.post("/admin/orders/{order_id}/send/{supplier_id}")
def admin_send_to_supplier(request: Request, order_id: int, supplier_id: int):
    require_admin(request)
    with get_db() as con:
        supplier = con.execute("SELECT * FROM suppliers WHERE id=?", (supplier_id,)).fetchone()
        order = con.execute("SELECT * FROM orders WHERE id=?", (order_id,)).fetchone()
        items = [dict(i) | {"order": dict(order)} for i in con.execute(
            "SELECT * FROM order_items WHERE order_id=? AND supplier_id=?", (order_id, supplier_id))]
        try:
            ref = adapter_for(supplier).place_order(items)
        except Exception as e:
            return redirect(f"/admin/orders/{order_id}?msg=Помилка API постачальника: {e}")
        if not ref:
            return redirect(f"/admin/orders/{order_id}?msg=У постачальника {supplier['name']} немає API замовлень —"
                            f" завантажте CSV і передайте вручну")
        con.execute("UPDATE order_items SET supplier_status='sent', supplier_ref=? WHERE order_id=? AND supplier_id=?",
                    (ref, order_id, supplier_id))
    return redirect(f"/admin/orders/{order_id}?msg=Передано, номер у постачальника: {ref}")


@app.get("/admin/suppliers", response_class=HTMLResponse)
def admin_suppliers(request: Request, msg: str = ""):
    require_admin(request)
    with get_db() as con:
        suppliers = con.execute(
            "SELECT s.*, (SELECT COUNT(*) FROM offers o WHERE o.supplier_id=s.id) AS n_offers"
            " FROM suppliers s ORDER BY s.name").fetchall()
    return render(request, "admin/suppliers.html", suppliers=suppliers, msg=msg)


@app.get("/admin/suppliers/{sid}", response_class=HTMLResponse)
def admin_supplier_edit(request: Request, sid: str, msg: str = ""):
    require_admin(request)
    with get_db() as con:
        s = None if sid == "new" else con.execute("SELECT * FROM suppliers WHERE id=?", (int(sid),)).fetchone()
    cmap = column_map_of(s) if s else {}
    return render(request, "admin/supplier.html", s=s, cmap=cmap, msg=msg)


@app.post("/admin/suppliers/{sid}")
async def admin_supplier_save(request: Request, sid: str):
    require_admin(request)
    f = await request.form()
    cmap = {k: f.get(f"col_{k}", "").strip() for k in ("brand", "article", "name", "price", "qty", "delivery_days")}
    api_config = f.get("api_config", "").strip() or "{}"
    try:
        json.loads(api_config)
    except json.JSONDecodeError:
        return redirect(f"/admin/suppliers/{sid}?msg=Помилка: api_config має бути коректним JSON")
    vals = (f["code"].strip().lower(), f["name"].strip(), f.get("kind", "pricelist"),
            float(f.get("markup_pct") or 0), int(f.get("delivery_days") or 1), 1 if f.get("active") else 0,
            f.get("email", "").strip(), json.dumps(cmap, ensure_ascii=False), api_config)
    with get_db() as con:
        if sid == "new":
            sid = con.execute("INSERT INTO suppliers(code,name,kind,markup_pct,delivery_days,active,email,column_map,"
                              "api_config) VALUES(?,?,?,?,?,?,?,?,?)", vals).lastrowid
        else:
            con.execute("UPDATE suppliers SET code=?,name=?,kind=?,markup_pct=?,delivery_days=?,active=?,email=?,"
                        "column_map=?,api_config=? WHERE id=?", vals + (int(sid),))
    return redirect(f"/admin/suppliers/{sid}?msg=Збережено")


@app.post("/admin/suppliers/{sid}/import")
async def admin_supplier_import(request: Request, sid: int, file: UploadFile = File(...)):
    require_admin(request)
    content = await file.read()
    with get_db() as con:
        s = con.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
        try:
            offers, skipped = parse_pricelist(file.filename or "file.csv", content, column_map_of(s))
        except Exception as e:
            return redirect(f"/admin/suppliers/{sid}?msg=Помилка імпорту: {e}")
        import_offers(con, sid, offers)
    return redirect(f"/admin/suppliers/{sid}?msg=Імпортовано {len(offers)} позицій, пропущено {skipped}")


@app.post("/admin/suppliers/{sid}/fetch")
def admin_supplier_fetch(request: Request, sid: int):
    require_admin(request)
    with get_db() as con:
        s = con.execute("SELECT * FROM suppliers WHERE id=?", (sid,)).fetchone()
        try:
            got = adapter_for(s).fetch_pricelist()
            if not got:
                return redirect(f"/admin/suppliers/{sid}?msg=Не задано pricelist_url в api_config")
            offers, skipped = parse_pricelist(got[1], got[0], column_map_of(s))
        except Exception as e:
            return redirect(f"/admin/suppliers/{sid}?msg=Помилка завантаження: {e}")
        import_offers(con, sid, offers)
    return redirect(f"/admin/suppliers/{sid}?msg=Завантажено {len(offers)} позицій, пропущено {skipped}")


@app.get("/admin/markups", response_class=HTMLResponse)
def admin_markups(request: Request):
    require_admin(request)
    with get_db() as con:
        rules = con.execute("SELECT * FROM markup_rules ORDER BY brand_norm, min_price").fetchall()
    return render(request, "admin/markups.html", rules=rules)


@app.post("/admin/markups")
def admin_markup_add(request: Request, brand: str = Form(""), min_price: float = Form(0),
                     max_price: float = Form(1e12), pct: float = Form(...)):
    require_admin(request)
    with get_db() as con:
        con.execute("INSERT INTO markup_rules(brand_norm,min_price,max_price,pct) VALUES(?,?,?,?)",
                    (norm_brand(brand), min_price, max_price, pct))
    return redirect("/admin/markups")


@app.post("/admin/markups/{rid}/delete")
def admin_markup_delete(request: Request, rid: int):
    require_admin(request)
    with get_db() as con:
        con.execute("DELETE FROM markup_rules WHERE id=?", (rid,))
    return redirect("/admin/markups")


@app.get("/admin/catalog", response_class=HTMLResponse)
def admin_catalog(request: Request, msg: str = ""):
    require_admin(request)
    with get_db() as con:
        counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                  for t in ("makes", "models", "categories", "applicability", "crosses", "offers")}
    return render(request, "admin/catalog.html", counts=counts, msg=msg)


@app.post("/admin/catalog/{kind}")
async def admin_catalog_import(request: Request, kind: str, file: UploadFile = File(...)):
    require_admin(request)
    content = await file.read()
    with get_db() as con:
        try:
            if kind == "crosses":
                n = import_crosses(con, file.filename or "f.csv", content)
            elif kind == "applicability":
                n = import_applicability(con, file.filename or "f.csv", content)
            else:
                raise HTTPException(404)
        except HTTPException:
            raise
        except Exception as e:
            return redirect(f"/admin/catalog?msg=Помилка: {e}")
    return redirect(f"/admin/catalog?msg=Додано записів: {n}")


@app.get("/admin/settings", response_class=HTMLResponse)
def admin_settings(request: Request, msg: str = ""):
    require_admin(request)
    with get_db() as con:
        values = {k: get_setting(con, k) for k in SETTING_KEYS}
    return render(request, "admin/settings.html", values=values, msg=msg)


@app.post("/admin/settings")
async def admin_settings_save(request: Request):
    require_admin(request)
    f = await request.form()
    with get_db() as con:
        for k in SETTING_KEYS:
            set_setting(con, k, f.get(k, "").strip())
    return redirect("/admin/settings?msg=Збережено")


@app.get("/health")
def health():
    return JSONResponse({"ok": True})
