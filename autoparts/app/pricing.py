"""Ціноутворення: закупівельна ціна постачальника -> роздрібна ціна на сайті."""
import math


def load_rules(con):
    return [dict(r) for r in con.execute(
        "SELECT * FROM markup_rules ORDER BY (brand_norm='') ASC, min_price ASC")]


def markup_pct(price_in, brand_norm, supplier_markup, rules):
    """Пріоритет: правило для бренду в діапазоні цін -> загальне правило в діапазоні -> націнка постачальника."""
    for r in rules:
        if r["brand_norm"] and r["brand_norm"] != brand_norm:
            continue
        if r["min_price"] <= price_in < r["max_price"]:
            return r["pct"]
    return supplier_markup


def sell_price(price_in, brand_norm, supplier_markup, rules):
    pct = markup_pct(price_in, brand_norm, supplier_markup, rules)
    return float(math.ceil(price_in * (1 + pct / 100.0)))  # округлення вгору до гривні
