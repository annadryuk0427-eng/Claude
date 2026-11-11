"""LiqPay (checkout API v3). Ключі: налаштування liqpay_public_key / liqpay_private_key."""
import base64
import hashlib
import json

LIQPAY_CHECKOUT_URL = "https://www.liqpay.ua/api/3/checkout"


def _sign(private_key: str, data: str) -> str:
    return base64.b64encode(hashlib.sha1((private_key + data + private_key).encode()).digest()).decode()


def liqpay_form(public_key, private_key, order_id, amount, description, result_url, server_url, sandbox=False):
    params = {
        "version": 3, "public_key": public_key, "action": "pay", "amount": f"{amount:.2f}",
        "currency": "UAH", "description": description, "order_id": f"order-{order_id}",
        "result_url": result_url, "server_url": server_url, "language": "uk",
    }
    if sandbox:
        params["sandbox"] = 1
    data = base64.b64encode(json.dumps(params).encode()).decode()
    return {"data": data, "signature": _sign(private_key, data)}


def liqpay_verify(private_key, data, signature):
    if _sign(private_key, data) != signature:
        return None
    return json.loads(base64.b64decode(data))
