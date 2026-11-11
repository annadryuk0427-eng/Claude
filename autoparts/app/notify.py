"""Сповіщення про нові замовлення в Telegram (налаштування telegram_bot_token, telegram_chat_id)."""
import json
import logging
import urllib.request

log = logging.getLogger("notify")


def telegram(token, chat_id, text):
    if not token or not chat_id:
        return
    try:
        body = json.dumps({"chat_id": chat_id, "text": text}).encode()
        req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        log.exception("Не вдалося надіслати сповіщення в Telegram")
