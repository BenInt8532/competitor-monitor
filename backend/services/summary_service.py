"""
Сводная таблица сравнения конкурентов.

Источник данных — накопленная память (memory_service), а не последняя запись истории.
Разница принципиальная: про одного обменника материалы приходят кусками (скриншот,
Word-файл, отзывы), и раньше каждый новый анализ затирал предыдущий. Теперь в строке
таблицы видно всё, что собрано по обменнику, и из скольких материалов это собрано.

Единой "магической" оценки нет — Ben сравнивает колонки сам (так честнее,
чем выдумывать веса критериев).
"""

import csv
import io
from datetime import datetime
from typing import List, Optional, Tuple

from backend.models.schemas import CompetitorSummaryRow, SummaryResponse
from backend.services.memory_service import memory_service
from backend.services.rates_service import rates_service
from backend.utils import trust_from_flags

KIND_LABEL = {
    "image": "скрин",
    "pdf": "PDF",
    "docx": "Word",
    "html": "страница",
    "xlsx": "таблица",
    "csv": "таблица",
    "table": "таблица",
    "text": "текст",
    "batch": "notes.md",
    "reviews": "отзывы",
    "url": "сайт",
    "rate": "курс BestChange",
}


class SummaryService:
    async def build_summary(self) -> SummaryResponse:
        profiles = memory_service.get_profiles()

        try:
            cbr_rate = await rates_service.get_usd_rate()
        except Exception:
            cbr_rate = None  # ЦБ недоступен — сводку всё равно строим, просто без spread

        rows: List[CompetitorSummaryRow] = []
        for profile in profiles:
            name = profile["competitor_name"]
            rate = profile.get("exchanger_rate")

            spread = None
            if cbr_rate and rate:
                spread = round(rate - cbr_rate.usd_rub, 2)

            trust, trust_note = self._trust(profile)
            materials = self._materials(profile)

            rows.append(
                CompetitorSummaryRow(
                    competitor_name=name,
                    source_url=self._first_url(profile),
                    trust_score=trust,
                    trust_note=trust_note,
                    design_score=profile.get("design_score"),
                    design_note=self._design_note(profile.get("design_score"), materials),
                    sources_count=profile.get("sources_count", 0),
                    materials=materials,
                    aliases=[a for a in profile.get("aliases", []) if a != name],
                    payout_methods=[f["text"] for f in profile.get("payout_methods", [])],
                    red_flags=[f["text"] for f in profile.get("red_flags", [])],
                    green_flags=[f["text"] for f in profile.get("green_flags", [])],
                    exchanger_rate=rate,
                    rate_spread=spread,
                    rate_source=profile.get("rate_source"),
                    rate_fetched_at=profile.get("rate_fetched_at"),
                    last_analyzed_at=profile.get("last_seen"),
                )
            )

        # сортируем по trust_score убыв. (None — в конец)
        rows.sort(key=lambda r: (r.trust_score is None, -(r.trust_score or 0)))

        return SummaryResponse(rows=rows, cbr_rate=cbr_rate)

    @staticmethod
    def _materials(profile: dict) -> List[str]:
        labels = []
        for source in profile.get("sources", []):
            kind = KIND_LABEL.get(source.get("kind") or "", source.get("kind") or "файл")
            name = (source.get("label") or "").strip()
            labels.append(f"{kind}: {name}" if name else kind)
        return labels

    @staticmethod
    def _trust(profile: dict) -> Tuple[Optional[float], str]:
        """
        Откуда берётся цифра.

        1) Модель ставила баллы в текстовом разборе или в отзывах — берём среднее.
           Это суждение GPT по чеклисту безопасности, не формула из учебника.
        2) Баллов не было (часто так у одних скринов), но флаги уже есть —
           считаем простую шкалу: старт 5, плюс 1 за каждый зелёный, минус 2 за красный.
           Так у Coindrop со скринов хотя бы видно «скорее плюс / скорее минус».
        3) Нет ни баллов, ни флагов — прочерк. Не из чего считать.
        """
        model_score = profile.get("trust_score")
        observations = profile.get("trust_observations") or 0
        greens = profile.get("green_flags") or []
        reds = profile.get("red_flags") or []

        # потолок по тяжёлым флагам уже применён в memory_service._shape —
        # здесь только дописываем причину, чтобы она была видна под цифрой
        cap_note = profile.get("trust_reason")

        if model_score is not None and observations:
            note = f"среднее по {observations} текстовым разборам / отзывам"
            return model_score, f"{note}; {cap_note}" if cap_note else note

        if greens or reds:
            categories = [f.get("category") for f in reds if f.get("category")]
            score = trust_from_flags(len(greens), categories, len(reds))
            note = (
                f"по флагам, не модель: старт 5, +1×{len(greens)} зел., "
                f"−1×{len(reds)} кр. "
            )
            if cap_note:
                note += f"{cap_note}. "
            note += "Текста правил или отзывов не разбирали — только то, что видно на материалах"
            return float(score), note

        kinds = {s.get("kind") for s in profile.get("sources", [])}
        if kinds and kinds <= {"image", "rate"}:
            return None, "не считали: в материалах только скрин (и курс). Нужен текст, сайт или отзывы"
        return None, "не считали: нет ни оценки модели, ни флагов"

    @staticmethod
    def _design_note(score: Optional[float], materials: List[str]) -> str:
        """
        Дизайн здесь не «красота», а первый сигнал доверия к сайту
        (Nielsen / Stanford Web Credibility: аккуратный вид vs любительский одностраничник).
        К безопасности выплаты это не относится.
        """
        has_shot = any(item.startswith("скрин") or item.startswith("сайт") for item in materials)
        if score is None:
            return "нет скрина — колонку смотреть нечего"
        if score >= 8:
            meaning = "опрятный лендинг, с первого взгляда похоже на сервис, не на одностраничный скам"
        elif score >= 5:
            meaning = "обычно: читается, но сильных признаков «серьёзной конторы» не видно"
        else:
            meaning = "любительски / грязно — у финтеха это снижает доверие ещё до чтения условий"
        if not has_shot:
            meaning += ". Оценки без скрина быть не должно — перепроверь материалы"
        return meaning

    @staticmethod
    def _first_url(profile: dict):
        for source in profile.get("sources", []):
            if source.get("url"):
                return source["url"]
        domains = profile.get("domains", [])
        return f"https://{domains[0]}" if domains else None

    @staticmethod
    def csv_filename() -> str:
        day = datetime.now().strftime("%Y-%m-%d")
        return f"{day}-sravnenie-konkurentov.csv"

    @staticmethod
    def to_csv(data: SummaryResponse) -> bytes:
        """CSV с точкой с запятой — так Excel на русской Windows открывает без сюрпризов."""
        buf = io.StringIO()
        writer = csv.writer(buf, delimiter=";", lineterminator="\n")
        writer.writerow([
            "Конкурент", "Сайт", "Доверие", "Откуда доверие", "Дизайн",
            "Курс ₽/USDT", "Источник курса", "Курс снят", "К курсу ЦБ",
            "Выплата", "Красные флаги", "Зелёные флаги", "Материалы",
        ])
        for row in data.rows:
            writer.writerow([
                row.competitor_name,
                row.source_url or "",
                row.trust_score if row.trust_score is not None else "",
                row.trust_note or "",
                row.design_score if row.design_score is not None else "",
                row.exchanger_rate if row.exchanger_rate is not None else "",
                row.rate_source or "",
                row.rate_fetched_at or "",
                row.rate_spread if row.rate_spread is not None else "",
                ", ".join(row.payout_methods),
                " | ".join(row.red_flags),
                " | ".join(row.green_flags),
                " · ".join(row.materials),
            ])
        return buf.getvalue().encode("utf-8-sig")


summary_service = SummaryService()
