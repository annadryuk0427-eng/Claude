"""Нова Пошта API 2.0: пошук міст і відділень. Ключ — налаштування np_api_key."""
import json
import urllib.request

NP_URL = "https://api.novaposhta.ua/v2.0/json/"


def _call(api_key, model, method, props):
    body = json.dumps({"apiKey": api_key, "modelName": model, "calledMethod": method,
                       "methodProperties": props}).encode()
    req = urllib.request.Request(NP_URL, data=body, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read()).get("data", [])


def cities(api_key, q):
    data = _call(api_key, "Address", "searchSettlements", {"CityName": q, "Limit": "15"})
    if not data:
        return []
    return [{"name": a["Present"], "ref": a["DeliveryCity"]} for a in data[0].get("Addresses", [])]


def warehouses(api_key, city_ref, q=""):
    data = _call(api_key, "Address", "getWarehouses", {"CityRef": city_ref, "FindByString": q, "Limit": "50"})
    return [{"name": w["Description"], "ref": w["Ref"]} for w in data]
