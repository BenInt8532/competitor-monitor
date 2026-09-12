"""
Мелкие помощники: разбор ссылок и имён конкурентов.

Название конкурента больше не вводится руками — вытаскиваем его из ссылки
(https://coindrop.trade/ -> coindrop), как просил Ben.
"""

import re
from typing import List, Optional
from urllib.parse import urlparse

# Домен « « « «в ячейке или в имени, которое модель приняла за название обменника.
# www. необязателен: и «grumbot.net», и «www.grumbot.net» — это сайт.
DOMAIN_LIKE = re.compile(r"^(?:www\.)?[a-z0-9][a-z0-9\-.]*\.[a-z]{2,6}$", re.IGNORECASE)


def normalize_url(url: str) -> str:
    """Приводит ссылку к виду со схемой: coindrop.trade -> https://coindrop.trade"""
    url = (url or "").strip().strip(",;")
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    return url


def domain_from_url(url: str) -> str:
    """https://www.coindrop.trade/ru/faq -> coindrop.trade"""
    netloc = urlparse(normalize_url(url)).netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    return netloc.split(":")[0]


def competitor_name_from_url(url: str) -> str:
    """
    Имя конкурента из ссылки: https://www.coindrop.trade/ -> CoinDrop.
    Берём первую часть домена без зоны и делаем читаемым.
    """
    domain = domain_from_url(url)
    if not domain:
        return ""
    base = domain.split(".")[0]
    # coin-drop -> Coin-Drop, btcchange24 -> Btcchange24
    return "-".join(part.capitalize() for part in base.split("-"))


def bestchange_slug(url: str) -> str:
    """Слаг обменника для BestChange: atom-exchange.com -> atom-exchange"""
    domain = domain_from_url(url)
    return domain.split(".")[0] if domain else ""


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh",
    "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o",
    "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f", "х": "h", "ц": "c",
    "ч": "ch", "ш": "sh", "щ": "sch", "ъ": "", "ы": "y", "ь": "", "э": "e",
    "ю": "yu", "я": "ya",
}


def clean_name(value: str) -> str:
    """
    «grumbot.net», «https://grumbot.net/» и «BUHTA OBMENA.ME» приводим к имени.
    Модель часто пишет домен с пробелами и капсом — без склейки это другой профиль.
    """
    name = (value or "").strip().strip("/")
    if not name:
        return ""
    bare = name.replace("https://", "").replace("http://", "").split("/")[0]
    if DOMAIN_LIKE.match(bare):
        return competitor_name_from_url(bare) or name
    collapsed = re.sub(r"\s+", "", bare)
    if DOMAIN_LIKE.match(collapsed):
        return competitor_name_from_url(collapsed) or name
    return name


# Курс продажи USDT за рубли живёт примерно здесь (~80–90 на рынке 2026).
# Меньше 30 или больше 200 — это уже другая пара (часто BTC), сумма сделки
# или обратное направление «рубли → USDT», которое модель путает с USDT→RUB.
USDT_RUB_RATE_MIN = 30.0
USDT_RUB_RATE_MAX = 200.0


def is_usdt_rub_rate(value) -> bool:
    """Правдоподобный курс ₽ за 1 USDT, а не биткоин и не сумма сделки."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return False
    return USDT_RUB_RATE_MIN <= number <= USDT_RUB_RATE_MAX


# === Тяжесть красных флагов ===
# Не все красные флаги равны. Жалоба «заблокировали карту после выплаты» — это
# реализовавшийся риск потери денег, а «выплата только через СБП» — неудобство.
# Поэтому тяжёлые флаги не вычитают баллы, а СТАВЯТ ПОТОЛОК: сколько бы зелёных
# признаков ни набралось, выше этой отметки оценка не поднимется.
#
# Почему потолок, а не минус баллы: на живых данных Exdex с жалобами на блокировку
# карт набирал 7 из 10 — семь зелёных «быстро, вежливо, удобно» перевешивали два
# красных. Человек так не рассуждает: если людям блокируют карты, красивый сайт
# и быстрая поддержка значения уже не имеют.
FLAG_CAPS = {
    "card_block": 3,      # жалобы на блокировку карты/счёта после выплаты — главный риск
    "splitting": 4,       # дробление выплаты на несколько мелких платежей
    "after_payment": 4,   # доплата, документы или «ещё одна транзакция» после отправки крипты
    "aml_hold": 4,        # заморозка под AML + комиссия за разморозку/возврат
    "bank_access": 4,     # просят держать личный кабинет банка открытым, «телефон в руках»
}

# Лёгкие флаги: просто минус балл, потолок не ставят.
FLAG_PENALTIES = {
    "sbp_only": 1,        # выплата только через СБП, без реквизитов счёта
    "delays": 1,          # задержки выплат, нарушение своего же регламента
    "too_good_rate": 1,   # курс заметно лучше рынка без объяснения
    "thin_reputation": 1, # мало отзывов / маленький резерв при крупных суммах
    "other": 1,           # всё остальное
}

RED_FLAG_CATEGORIES = tuple(FLAG_CAPS) + tuple(FLAG_PENALTIES)


def trust_cap(red_categories) -> Optional[int]:
    """
    Потолок оценки по самому тяжёлому из найденных флагов.
    None — тяжёлых флагов нет, потолок не нужен.
    """
    caps = [FLAG_CAPS[c] for c in (red_categories or []) if c in FLAG_CAPS]
    return min(caps) if caps else None


def cap_reason(red_categories) -> Optional[str]:
    """Человеческое объяснение, почему оценка ограничена — для карточки и отчёта."""
    names = {
        "card_block": "жалобы на блокировку карты после выплаты",
        "splitting": "дробление выплаты на несколько платежей",
        "after_payment": "требования после отправки крипты",
        "aml_hold": "заморозка под AML с комиссией",
        "bank_access": "просят доступ к личному кабинету банка",
    }
    found = [(FLAG_CAPS[c], names[c]) for c in (red_categories or []) if c in FLAG_CAPS]
    if not found:
        return None
    cap, name = min(found)
    return f"не выше {cap}/10: {name}"


def apply_trust_cap(score, red_categories) -> Optional[int]:
    """Опускает оценку модели до потолка, если найден тяжёлый флаг. Вверх не поднимает."""
    if score is None:
        return score
    cap = trust_cap(red_categories)
    return min(int(score), cap) if cap is not None else int(score)


def trust_from_flags(green_count: int, red_categories=None, red_count: int = 0) -> int:
    """
    Запасная шкала, когда модель балл не поставила: старт 5, +1 за зелёный,
    −1 за лёгкий красный, тяжёлый — потолок (см. FLAG_CAPS).
    red_count оставлен для вызовов, где категорий нет — тогда как раньше, −2 за флаг.
    """
    score = 5 + int(green_count)
    categories = list(red_categories or [])
    if categories:
        score -= sum(FLAG_PENALTIES.get(c, 1) for c in categories if c not in FLAG_CAPS)
    else:
        score -= 2 * int(red_count)

    score = min(score, 10)
    cap = trust_cap(categories)
    if cap is not None:
        score = min(score, cap)
    return max(0, score)


# Короткие стандартные имена способов выплаты. Порядок важен: сначала частные
# (Сбер, СБП), потом общее «банковский перевод», иначе всё схлопнется в одно.
_PAYOUT_RULES = (
    (("сбп", "sbp", "быстрых платеж"), "СБП"),
    (("расчётн", "расчетн"), "расчётный счёт"),
    (("наличн", "cash", "встреч", "курьер", "офис"), "наличные"),
    (("сбер",), "Сбербанк"),
    (("тинькоф", "tinkoff", "т-банк", "т банк"), "Т-Банк"),
    (("альфа", "alfa"), "Альфа-Банк"),
    (("втб", "vtb"), "ВТБ"),
    (("qiwi", "киви"), "QIWI"),
    (("юmoney", "юмани", "yoomoney"), "ЮMoney"),
    (("visa", "master", "карт"), "карта"),
    (("реквизит", "wire", "банковск", "перевод"), "банковский перевод"),
)


def normalize_payout_method(raw: str) -> str:
    """«банк. перевод», «СБП», «на карту Сбера» → одно короткое имя."""
    text = (raw or "").strip()
    if not text or len(text) > 80:
        return ""
    low = text.lower()
    if "карт" in low and any(token in low for token in ("сбер", "тинькоф", "tinkoff", "альфа")):
        # «на карту Сбера» — это Сбер, не общая «карта»
        if "сбер" in low:
            return "Сбербанк"
        if "тинькоф" in low or "tinkoff" in low or "т-банк" in low:
            return "Т-Банк"
        if "альфа" in low:
            return "Альфа-Банк"
    for needles, label in _PAYOUT_RULES:
        if any(needle in low for needle in needles):
            return label
    return text


def merge_payout_methods(*groups) -> list:
    """Склеивает списки способов без повторов, с нормализацией имён."""
    seen = []
    keys = set()
    for group in groups:
        for raw in group or []:
            name = normalize_payout_method(raw)
            if not name:
                continue
            key = match_key(name)
            if key in keys:
                continue
            keys.add(key)
            seen.append(name)
    return seen


def match_key(value: str) -> str:
    """
    Ключ для сопоставления имён обменников из разных источников.
    BestChange пишет «БухтаОбмена», домен даёт «buhtaobmena» — приводим к одному виду:
    нижний регистр, кириллица в латиницу, всё лишнее выброшено.
    """
    lowered = (value or "").strip().lower()
    result = []
    for char in lowered:
        if char in _TRANSLIT:
            result.append(_TRANSLIT[char])
        elif char.isalnum():
            result.append(char)
    return "".join(result)


def parse_url_list(raw: str, limit: int) -> List[str]:
    """
    Разбирает вставленный списком текст в ссылки: по переносам строк,
    запятым и пробелам. Дубликаты убираем, порядок сохраняем.
    """
    if not raw:
        return []

    chunks: List[str] = []
    for line in raw.replace(",", "\n").replace(";", "\n").split("\n"):
        for piece in line.split():
            normalized = normalize_url(piece)
            if normalized and domain_from_url(normalized):
                chunks.append(normalized)

    unique: List[str] = []
    seen = set()
    for url in chunks:
        key = url.rstrip("/").lower()
        if key not in seen:
            seen.add(key)
            unique.append(url)

    return unique[:limit]
