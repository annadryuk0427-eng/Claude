"""Логіка магазину: пошук пропозицій, кошик, створення замовлення."""
import logging

from .db import norm_article, norm_brand
from .pricing import load_rules, sell_price
from .suppliers import adapter_for

log = logging.getLogger("shop")

OFFER_SQL = """
SELECT o.*, s.name AS supplier_name, s.markup_pct, s.code AS supplier_code,
       COALESCE(o.delivery_days, s.delivery_days) AS days
FROM offers o JOIN suppliers s ON s.id = o.supplier_id
WHERE s.active = 1 AND {where}
"""


def _priced(rows, rules):
    out = []
    for r in rows:
        d = dict(r)
        d["sell"] = sell_price(d["price"], d["brand_norm"], d["markup_pct"], rules)
        out.append(d)
    return out


def refresh_live(con, article_norm):
    """Опитує постачальників з API і кешує їхні пропозиції в таблиці offers."""
    for s in con.execute("SELECT * FROM suppliers WHERE active=1 AND kind='api'").fetchall():
        try:
            found = adapter_for(s).search(article_norm)
        except Exception:  # недоступність одного постачальника не має ламати пошук
            log.exception("Помилка пошуку в постачальника %s", s["code"])
            continue
        con.execute("DELETE FROM offers WHERE supplier_id=? AND article_norm=?", (s["id"], article_norm))
        for o in found:
            con.execute(
                "INSERT INTO offers(supplier_id,brand,brand_norm,article,article_norm,name,price,qty,delivery_days)"
                " VALUES(?,?,?,?,?,?,?,?,?)",
                (s["id"], o["brand"], norm_brand(o["brand"]), o["article"], norm_article(o["article"]),
                 o.get("name", ""), o["price"], o.get("qty", 0), o.get("delivery_days")))


def search(con, query: str):
    """Повертає (точні збіги, аналоги, збіги за назвою)."""
    qn = norm_article(query)
    rules = load_rules(con)
    if not qn:
        return [], [], []
    refresh_live(con, qn)
    exact = sorted(_priced(con.execute(OFFER_SQL.format(where="o.article_norm=?"), (qn,)).fetchall(), rules),
                   key=lambda x: x["sell"])

    analog_keys = con.execute(
        "SELECT DISTINCT brand2, article2 FROM crosses WHERE article1=?", (qn,)).fetchall()
    analogs = []
    for k in analog_keys:
        analogs += _priced(con.execute(
            OFFER_SQL.format(where="o.article_norm=? AND o.brand_norm=?"), (k["article2"], k["brand2"])).fetchall(),
            rules)
    analogs.sort(key=lambda x: x["sell"])

    by_name = []
    if not exact and not analogs and len(query.strip()) >= 3:
        like = f"%{query.strip().lower()}%"
        by_name = sorted(_priced(con.execute(
            OFFER_SQL.format(where="(ulower(o.name) LIKE ? OR ulower(o.article) LIKE ?)") + " LIMIT 200",
            (like, like)).fetchall(), rules), key=lambda x: x["sell"])
    return exact, analogs, by_name


def offers_by_ids(con, ids):
    if not ids:
        return []
    rules = load_rules(con)
    q = OFFER_SQL.format(where=f"o.id IN ({','.join('?' * len(ids))})")
    return _priced(con.execute(q, list(ids)).fetchall(), rules)


def best_offers_for(con, keys):
    """keys: [(brand_norm, article_norm)] -> найдешевша пропозиція по кожній деталі (для каталогу за авто)."""
    rules = load_rules(con)
    out = []
    for b, a in keys:
        rows = _priced(con.execute(OFFER_SQL.format(where="o.article_norm=? AND o.brand_norm=?"), (a, b)).fetchall(),
                       rules)
        if rows:
            best = min(rows, key=lambda x: x["sell"])
            best["n_offers"] = len(rows)
            out.append(best)
    return out


def cart_lines(con, cart: dict):
    offers = {o["id"]: o for o in offers_by_ids(con, [int(k) for k in cart])}
    lines = []
    for oid, qty in cart.items():
        o = offers.get(int(oid))
        if o:
            lines.append({**o, "cart_qty": qty, "line_total": o["sell"] * qty})
    return lines, sum(line["line_total"] for line in lines)


def create_order(con, cart, form):
    lines, total = cart_lines(con, cart)
    if not lines:
        raise ValueError("Кошик порожній або товари більше недоступні")
    cur = con.execute(
        "INSERT INTO orders(customer_name,phone,email,city,np_branch,comment,payment_method,total)"
        " VALUES(?,?,?,?,?,?,?,?)",
        (form["customer_name"], form["phone"], form.get("email", ""), form.get("city", ""),
         form.get("np_branch", ""), form.get("comment", ""), form["payment_method"], total))
    order_id = cur.lastrowid
    for line in lines:
        con.execute(
            "INSERT INTO order_items(order_id,supplier_id,supplier_name,brand,article,name,qty,price_in,price_sell)"
            " VALUES(?,?,?,?,?,?,?,?,?)",
            (order_id, line["supplier_id"], line["supplier_name"], line["brand"], line["article"], line["name"],
             line["cart_qty"], line["price"], line["sell"]))
    return order_id, total
