"""
Разовая миграция: проставить категории тяжести старым красным флагам.

Зачем: до введения весов флаги хранились просто текстом, и потолок оценки
(«блокировка карты → не выше 3») к уже накопленным профилям не применялся.
Новые флаги категорию получают от модели, а старые размечаем здесь по корням слов.

Почему по корням, а не точными фразами: на этом проекте трижды ловили, что модель
каждый раз формулирует по-новому («затрудняет анализ» → «затрудняет полноценный анализ»).
Для разовой миграции такой разметки достаточно — дальше категории ставит модель.

Запуск из папки competitor-monitor:
    .venv/Scripts/python.exe migrate_flag_severity.py
"""

import json
from pathlib import Path

from backend.config import settings
from backend.utils import apply_trust_cap, trust_cap

# Корень слова → категория. Порядок важен: проверяем сверху вниз, первое совпадение выигрывает,
# поэтому сначала самые тяжёлые и специфичные признаки.
RULES = [
    ("card_block", ("блокиров", "заблокир", "блокировк")),
    ("splitting", ("дробл", "нескольк платеж", "частями", "мелкими сумм", "разбива")),
    ("after_payment", ("после отправки", "после оплаты", "после перевода", "доплат",
                       "удержива", "удержал", "20%", "дополнительн документ", "верификаци")),
    ("aml_hold", ("заморозк", "заморозил", "аml", "aml", "kyc")),
    ("bank_access", ("личный кабинет", "телефон в руках", "доступ к банку")),
    ("sbp_only", ("только сбп", "только через сбп")),
    ("delays", ("задержк", "затягива", "не выплат", "регламент")),
    ("too_good_rate", ("курс подозрительн", "слишком выгодн", "выгоднее рынка", "приманк")),
    ("thin_reputation", ("мало отзыв", "маленький резерв", "небольшой резерв", "новый обменник")),
]


def guess_category(text: str) -> str:
    low = (text or "").lower()
    for category, roots in RULES:
        if any(root in low for root in roots):
            return category
    return "other"


def migrate_profiles() -> tuple:
    path = Path(settings.memory_file)
    if not path.exists():
        return 0, 0, []

    profiles = json.loads(path.read_text(encoding="utf-8"))
    marked = 0
    capped = []

    for profile in profiles.values():
        categories = []
        for flag in (profile.get("red_flags") or {}).values():
            if not flag.get("category"):
                flag["category"] = guess_category(flag.get("text", ""))
                marked += 1
            categories.append(flag["category"])

        cap = trust_cap(categories)
        if cap is not None:
            capped.append((profile.get("display_name"), cap, categories))

    path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return len(profiles), marked, capped


def migrate_history() -> tuple:
    """История хранит балл цифрой — пересчитываем его с учётом потолка."""
    path = Path(settings.history_file)
    if not path.exists():
        return 0, 0

    items = json.loads(path.read_text(encoding="utf-8"))
    changed = 0
    for item in items:
        score = item.get("trust_score")
        if score is None:
            continue
        categories = [guess_category(f) for f in (item.get("red_flags") or [])]
        new_score = apply_trust_cap(score, categories)
        if new_score != score:
            item["trust_score_before_severity"] = score
            item["trust_score"] = new_score
            changed += 1

    path.write_text(json.dumps(items, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return len(items), changed


if __name__ == "__main__":
    total_profiles, marked, capped = migrate_profiles()
    print(f"Профилей: {total_profiles}, размечено флагов: {marked}")
    for name, cap, cats in capped:
        heavy = [c for c in cats if c in ("card_block", "splitting", "after_payment", "aml_hold", "bank_access")]
        print(f"  потолок {cap}/10 -> {name} ({', '.join(sorted(set(heavy)))})")

    total_history, changed = migrate_history()
    print(f"Записей истории: {total_history}, пересчитано: {changed}")
