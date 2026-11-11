"""Заповнює БД демо-даними: постачальники Омега і «Склад 2», прайси, аналоги, підбір за авто.
Запуск: python seed_demo.py   (ціни в прайсах — вигадані, лише для перевірки роботи сайту)."""
import os

from app.db import get_db, init_db
from app.importer import import_applicability, import_crosses, import_offers, parse_pricelist

HERE = os.path.dirname(os.path.abspath(__file__))


def read(name):
    with open(os.path.join(HERE, "sample_data", name), "rb") as f:
        return f.read()


init_db()
with get_db() as con:
    for code, name, markup, days, fname in [("omega", "Омега", 22, 1, "omega_demo.csv"),
                                            ("sklad2", "Склад 2", 25, 2, "sklad2_demo.csv")]:
        con.execute("INSERT OR IGNORE INTO suppliers(code,name,markup_pct,delivery_days) VALUES(?,?,?,?)",
                    (code, name, markup, days))
        sid = con.execute("SELECT id FROM suppliers WHERE code=?", (code,)).fetchone()[0]
        offers, skipped = parse_pricelist(fname, read(fname))
        import_offers(con, sid, offers)
        print(f"{name}: {len(offers)} позицій, пропущено {skipped}")
    print("Кроси:", import_crosses(con, "c.csv", read("crosses_demo.csv")))
    print("Застосовність:", import_applicability(con, "a.csv", read("applicability_demo.csv")))
