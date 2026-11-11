"""Імпорт прайс-листів (CSV / XLSX) з довільними назвами колонок."""
import csv
import io
import json
import re

from .db import norm_article, norm_brand

# Синоніми назв колонок — щоб більшість прайсів імпортувались без налаштувань
SYNONYMS = {
    "brand": ["бренд", "виробник", "производитель", "brand", "manufacturer", "марка", "фірма", "фирма"],
    "article": ["артикул", "код", "номер", "article", "partnumber", "part number", "код товару", "каталожний номер",
                "каталожный номер", "sku"],
    "name": ["назва", "найменування", "наименование", "название", "name", "description", "опис"],
    "price": ["ціна", "цена", "price", "вартість", "стоимость", "ціна грн", "цена грн", "ціна, грн", "цена, грн"],
    "qty": ["кількість", "количество", "наявність", "наличие", "залишок", "остаток", "qty", "stock", "quantity"],
    "delivery_days": ["термін", "срок", "доставка", "днів", "дней", "delivery"],
}
REQUIRED = ("brand", "article", "price")


def _read_rows(filename: str, content: bytes):
    if filename.lower().endswith((".xlsx", ".xlsm")):
        from openpyxl import load_workbook
        wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        ws = wb.worksheets[0]
        for row in ws.iter_rows(values_only=True):
            yield ["" if v is None else str(v).strip() for v in row]
        return
    for enc in ("utf-8-sig", "cp1251"):
        try:
            text = content.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    # Роздільник: «;», таб або «|», якщо є в першому рядку, інакше кома
    # (кома часто трапляється в цінах «265,00» і назвах колонок «Ціна, грн»).
    first = next((line for line in text.splitlines() if line.strip()), "")
    delim = next((d for d in ";\t|" if d in first), ",")
    for row in csv.reader(io.StringIO(text), delimiter=delim):
        yield [c.strip() for c in row]


def _find_header(rows, column_map):
    """Шукає рядок-заголовок у перших 20 рядках і повертає (індекс, {поле: номер колонки})."""
    for i, row in enumerate(rows[:20]):
        low = [c.lower().strip() for c in row]
        mapping = {}
        for field in SYNONYMS:
            wanted = column_map.get(field)
            names = [wanted.lower().strip()] if wanted else SYNONYMS[field]
            for j, cell in enumerate(low):
                if cell in names and j not in mapping.values():
                    mapping[field] = j
                    break
        if all(f in mapping for f in REQUIRED):
            return i, mapping
    return None, None


def parse_number(s):
    s = str(s or "").replace("\xa0", "").replace(" ", "").replace(",", ".")
    m = re.search(r"\d+(\.\d+)?", s)
    return float(m.group()) if m else None


def parse_pricelist(filename: str, content: bytes, column_map: dict | None = None):
    column_map = {k: v for k, v in (column_map or {}).items() if v}
    rows = list(_read_rows(filename, content))
    hi, mp = _find_header(rows, column_map)
    if hi is None:
        raise ValueError("Не знайдено заголовок з колонками бренд / артикул / ціна. "
                         "Вкажіть назви колонок у налаштуваннях постачальника.")
    offers, skipped = [], 0
    for row in rows[hi + 1:]:
        get = lambda f: row[mp[f]] if f in mp and mp[f] < len(row) else ""
        brand, article, price = get("brand"), get("article"), parse_number(get("price"))
        if not brand or not norm_article(article) or not price:
            skipped += 1
            continue
        qty = parse_number(get("qty"))
        dd = parse_number(get("delivery_days"))
        offers.append({
            "brand": brand.strip(), "brand_norm": norm_brand(brand), "article": article.strip(),
            "article_norm": norm_article(article), "name": get("name"), "price": round(price, 2),
            "qty": int(qty) if qty is not None else 0, "delivery_days": int(dd) if dd is not None else None,
        })
    return offers, skipped


def import_offers(con, supplier_id: int, offers: list[dict]):
    """Повне оновлення: старі пропозиції постачальника замінюються новими."""
    con.execute("DELETE FROM offers WHERE supplier_id=?", (supplier_id,))
    con.executemany(
        "INSERT INTO offers(supplier_id,brand,brand_norm,article,article_norm,name,price,qty,delivery_days)"
        " VALUES(?,?,?,?,?,?,?,?,?)",
        [(supplier_id, o["brand"], o["brand_norm"], o["article"], o["article_norm"], o["name"], o["price"],
          o["qty"], o["delivery_days"]) for o in offers])
    con.execute("UPDATE suppliers SET last_import=CURRENT_TIMESTAMP WHERE id=?", (supplier_id,))


def import_crosses(con, filename, content):
    """CSV/XLSX з колонками: brand1, article1, brand2, article2 (заголовок обов'язковий)."""
    rows = list(_read_rows(filename, content))
    n = 0
    for row in rows[1:]:
        if len(row) < 4 or not row[1] or not row[3]:
            continue
        b1, a1, b2, a2 = norm_brand(row[0]), norm_article(row[1]), norm_brand(row[2]), norm_article(row[3])
        for x in ((b1, a1, b2, a2), (b2, a2, b1, a1)):  # зберігаємо в обидва боки
            n += con.execute("INSERT OR IGNORE INTO crosses VALUES(?,?,?,?)", x).rowcount
    return n


def import_applicability(con, filename, content):
    """CSV/XLSX: make, model, year_from, year_to, engine, category, brand, article."""
    rows = list(_read_rows(filename, content))
    n = 0
    for row in rows[1:]:
        if len(row) < 8 or not row[0] or not row[7]:
            continue
        make, model, yf, yt, engine, cat, brand, art = row[:8]
        con.execute("INSERT OR IGNORE INTO makes(name) VALUES(?)", (make.strip(),))
        make_id = con.execute("SELECT id FROM makes WHERE name=?", (make.strip(),)).fetchone()[0]
        yf = int(parse_number(yf)) if parse_number(yf) else None
        yt = int(parse_number(yt)) if parse_number(yt) else None
        con.execute("INSERT OR IGNORE INTO models(make_id,name,year_from,year_to,engine) VALUES(?,?,?,?,?)",
                    (make_id, model.strip(), yf, yt, engine.strip()))
        model_id = con.execute(
            "SELECT id FROM models WHERE make_id=? AND name=? AND year_from IS ? AND year_to IS ? AND engine=?",
            (make_id, model.strip(), yf, yt, engine.strip())).fetchone()[0]
        con.execute("INSERT OR IGNORE INTO categories(name) VALUES(?)", (cat.strip(),))
        cat_id = con.execute("SELECT id FROM categories WHERE name=?", (cat.strip(),)).fetchone()[0]
        n += con.execute("INSERT OR IGNORE INTO applicability VALUES(?,?,?,?)",
                         (model_id, cat_id, norm_brand(brand), norm_article(art))).rowcount
    return n


def column_map_of(supplier_row):
    try:
        return json.loads(supplier_row["column_map"] or "{}")
    except json.JSONDecodeError:
        return {}
