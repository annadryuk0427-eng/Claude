"""Спільний інтерфейс постачальника. Кожен новий постачальник = окремий клас-адаптер."""
import json
import urllib.request


class SupplierAdapter:
    """Базовий адаптер.

    search()      — живий пошук пропозицій за артикулом (для постачальників з API);
                    для прайс-листів пошук іде по таблиці offers, тому тут повертаємо [].
    place_order() — передати замовлення постачальнику; повертає номер замовлення в постачальника
                    або None, якщо замовлення передається вручну (email / кабінет).
    fetch_pricelist() — завантажити свіжий прайс (bytes, ім'я файлу) з URL, якщо він заданий.
    """

    def __init__(self, supplier_row):
        self.s = supplier_row
        try:
            self.cfg = json.loads(supplier_row["api_config"] or "{}")
        except json.JSONDecodeError:
            self.cfg = {}

    def search(self, article_norm: str) -> list[dict]:
        return []

    def place_order(self, items: list[dict]) -> str | None:
        return None

    def fetch_pricelist(self):
        url = self.cfg.get("pricelist_url")
        if not url:
            return None
        req = urllib.request.Request(url, headers={"User-Agent": "autoparts-shop/1.0"})
        with urllib.request.urlopen(req, timeout=120) as r:
            content = r.read()
        name = url.split("?")[0].rsplit("/", 1)[-1] or "pricelist.csv"
        return content, name
