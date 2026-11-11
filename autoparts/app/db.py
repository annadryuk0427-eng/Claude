"""SQLite: схема і хелпери. Один файл БД — data/shop.db (шлях можна змінити через SHOP_DB)."""
import os
import re
import sqlite3
from contextlib import contextmanager

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("SHOP_DB", os.path.join(BASE_DIR, "data", "shop.db"))

SCHEMA = """
CREATE TABLE IF NOT EXISTS suppliers (
    id INTEGER PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    kind TEXT NOT NULL DEFAULT 'pricelist',      -- pricelist | api
    markup_pct REAL NOT NULL DEFAULT 25,
    delivery_days INTEGER NOT NULL DEFAULT 1,
    active INTEGER NOT NULL DEFAULT 1,
    email TEXT DEFAULT '',
    column_map TEXT DEFAULT '{}',                 -- JSON: поле -> назва колонки у прайсі
    api_config TEXT DEFAULT '{}',                 -- JSON: url, логін, ключ тощо
    last_import TEXT
);
CREATE TABLE IF NOT EXISTS offers (
    id INTEGER PRIMARY KEY,
    supplier_id INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE CASCADE,
    brand TEXT NOT NULL,
    brand_norm TEXT NOT NULL,
    article TEXT NOT NULL,
    article_norm TEXT NOT NULL,
    name TEXT DEFAULT '',
    price REAL NOT NULL,                          -- закупівельна ціна, грн
    qty INTEGER NOT NULL DEFAULT 0,
    delivery_days INTEGER,
    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_offers_article ON offers(article_norm);
CREATE INDEX IF NOT EXISTS ix_offers_supplier ON offers(supplier_id);

CREATE TABLE IF NOT EXISTS crosses (                -- аналоги: (бренд, артикул) <-> (бренд, артикул)
    brand1 TEXT NOT NULL, article1 TEXT NOT NULL,
    brand2 TEXT NOT NULL, article2 TEXT NOT NULL,
    UNIQUE(brand1, article1, brand2, article2)
);
CREATE INDEX IF NOT EXISTS ix_crosses_a1 ON crosses(article1);

CREATE TABLE IF NOT EXISTS markup_rules (           -- загальні правила націнки (мають пріоритет над націнкою постачальника)
    id INTEGER PRIMARY KEY,
    brand_norm TEXT DEFAULT '',                    -- порожньо = будь-який бренд
    min_price REAL NOT NULL DEFAULT 0,
    max_price REAL NOT NULL DEFAULT 1e12,
    pct REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS makes (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS models (
    id INTEGER PRIMARY KEY,
    make_id INTEGER NOT NULL REFERENCES makes(id) ON DELETE CASCADE,
    name TEXT NOT NULL, year_from INTEGER, year_to INTEGER, engine TEXT DEFAULT '',
    UNIQUE(make_id, name, year_from, year_to, engine)
);
CREATE TABLE IF NOT EXISTS categories (id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL);
CREATE TABLE IF NOT EXISTS applicability (          -- яка деталь підходить до якої моделі
    model_id INTEGER NOT NULL REFERENCES models(id) ON DELETE CASCADE,
    category_id INTEGER NOT NULL REFERENCES categories(id) ON DELETE CASCADE,
    brand_norm TEXT NOT NULL, article_norm TEXT NOT NULL,
    UNIQUE(model_id, category_id, brand_norm, article_norm)
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP,
    customer_name TEXT NOT NULL, phone TEXT NOT NULL, email TEXT DEFAULT '',
    city TEXT DEFAULT '', np_branch TEXT DEFAULT '', comment TEXT DEFAULT '',
    payment_method TEXT NOT NULL,                  -- cod | liqpay
    payment_status TEXT NOT NULL DEFAULT 'pending',
    status TEXT NOT NULL DEFAULT 'new',
    ttn TEXT DEFAULT '',
    total REAL NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS order_items (
    id INTEGER PRIMARY KEY,
    order_id INTEGER NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    supplier_id INTEGER REFERENCES suppliers(id),
    supplier_name TEXT, brand TEXT, article TEXT, name TEXT,
    qty INTEGER NOT NULL, price_in REAL NOT NULL, price_sell REAL NOT NULL,
    supplier_status TEXT NOT NULL DEFAULT 'not_sent',  -- not_sent | sent | confirmed | shipped
    supplier_ref TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
"""

ORDER_STATUSES = {
    "new": "Нове", "confirmed": "Підтверджене", "ordered": "Замовлено у постачальника",
    "shipped": "Відправлене", "done": "Виконане", "canceled": "Скасоване",
}
SUPPLIER_ITEM_STATUSES = {
    "not_sent": "Не передано", "sent": "Передано", "confirmed": "Підтверджено", "shipped": "Відвантажено",
}


def norm_article(s) -> str:
    """Артикул без пробілів, дефісів, крапок, слешів — у верхньому регістрі."""
    return re.sub(r"[^0-9A-ZА-ЯІЇЄҐ]", "", str(s or "").upper())


def norm_brand(s) -> str:
    return re.sub(r"[^0-9A-ZА-ЯІЇЄҐ]", "", str(s or "").upper())


def connect():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    # Вбудований LOWER/LIKE у SQLite не працює з кирилицею — реєструємо свою функцію
    con.create_function("ulower", 1, lambda v: str(v or "").lower(), deterministic=True)
    return con


@contextmanager
def get_db():
    con = connect()
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db():
    with get_db() as con:
        con.executescript(SCHEMA)


def get_setting(con, key, default=""):
    row = con.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row and row["value"] is not None else os.environ.get(key.upper(), default)


def set_setting(con, key, value):
    con.execute("INSERT INTO settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value))
