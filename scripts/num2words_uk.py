"""Сума прописом українською (гривні/копійки)."""
ONES_M = ["", "один", "два", "три", "чотири", "п'ять", "шість", "сім", "вісім", "дев'ять"]
ONES_F = ["", "одна", "дві", "три", "чотири", "п'ять", "шість", "сім", "вісім", "дев'ять"]
TEENS = ["десять", "одинадцять", "дванадцять", "тринадцять", "чотирнадцять", "п'ятнадцять",
         "шістнадцять", "сімнадцять", "вісімнадцять", "дев'ятнадцять"]
TENS = ["", "", "двадцять", "тридцять", "сорок", "п'ятдесят", "шістдесят", "сімдесят", "вісімдесят", "дев'яносто"]
HUNDREDS = ["", "сто", "двісті", "триста", "чотириста", "п'ятсот", "шістсот", "сімсот", "вісімсот", "дев'ятсот"]
APOS = "’"  # як у шаблоні: дев’ятсот


def _plural(n, forms):
    n %= 100
    if 11 <= n <= 19:
        return forms[2]
    n %= 10
    return forms[0] if n == 1 else forms[1] if 2 <= n <= 4 else forms[2]


def _triad(n, fem):
    w = [HUNDREDS[n // 100]]
    t = n % 100
    if 10 <= t <= 19:
        w.append(TEENS[t - 10])
    else:
        w += [TENS[t // 10], (ONES_F if fem else ONES_M)[t % 10]]
    return [x for x in w if x]


def int_words(n, fem=True):
    if n == 0:
        return "нуль"
    parts = []
    for div, forms, f in ((10**9, ("мільярд", "мільярди", "мільярдів"), False),
                          (10**6, ("мільйон", "мільйони", "мільйонів"), False),
                          (10**3, ("тисяча", "тисячі", "тисяч"), True)):
        q = n // div % 1000
        if q:
            parts += _triad(q, f) + [_plural(q, forms)]
    parts += _triad(n % 1000, fem)
    return " ".join(parts).replace("'", APOS)


def words(amount, kop=True):
    cents = round(amount * 100)
    uah, k = divmod(cents, 100)
    s = f"{int_words(uah)} {_plural(uah, ('гривня', 'гривні', 'гривень'))}"
    if kop:
        s += f" {k:02d} {_plural(k, ('копійка', 'копійки', 'копійок'))}"
    return s


def fmt_money(amount):
    s = f"{amount:,.2f}"
    return s.replace(",", " ").replace(".", ",")
