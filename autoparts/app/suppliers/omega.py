"""Адаптер постачальника «Омега».

Поки що Омега працює через прайс-лист (імпорт CSV/XLSX або автозавантаження з посилання,
api_config.pricelist_url). Коли буде B2B-доступ до API Омеги, реалізуйте тут search() і place_order()
за їхньою документацією — решта сайту (пошук, кошик, замовлення, адмінка) змін не потребує.

Очікувані ключі api_config (заповнюються в адмінці, JSON):
    {"pricelist_url": "...", "api_url": "...", "login": "...", "password": "...", "api_key": "..."}
"""
from .base import SupplierAdapter


class OmegaAdapter(SupplierAdapter):
    def search(self, article_norm: str) -> list[dict]:
        if not self.cfg.get("api_url"):
            return []
        # TODO: виклик API Омеги після отримання документації.
        # Має повертати список словників:
        # {"brand", "article", "name", "price" (закупівля, грн), "qty", "delivery_days"}
        return []

    def place_order(self, items: list[dict]) -> str | None:
        # TODO: створення замовлення через API Омеги. Поки що — вручну (CSV для постачальника в адмінці).
        return None
