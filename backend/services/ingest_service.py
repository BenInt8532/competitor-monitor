"""
Запись разбора и разбор загруженных таблиц.

Роуты в main.py только принимают запрос. Сюда вынесено то, что не про HTTP:
имя из файла, запись в историю+память, разбор таблицы конкурентов, удаление.
"""

import re
from pathlib import Path
from typing import List, Optional

from backend.models.schemas import FileAnalysisResponse, TableRowInfo
from backend.services.history_service import history_service
from backend.services.memory_service import memory_service
from backend.services.rate_lookup_service import rate_lookup_service
from backend.utils import merge_payout_methods


class IngestService:
    def name_from_filename(self, filename: str) -> str:
        """
        Запасное имя, если модель не нашла название внутри файла.
        Служебные куски вроде «competitor-5-», «screenshot» выбрасываем,
        иначе один обменник попадёт в память под разными именами.
        """
        stem = Path(filename or "").stem
        stem = re.sub(r"^competitor[-_ ]*\d+[-_ ]*", "", stem, flags=re.IGNORECASE)
        stem = re.sub(
            r"[-_ ]*(screenshot|screen|shot|landing|page|about|o[-_ ]servise|\d{4}-\d{2}-\d{2})[-_ ]*",
            " ",
            stem,
            flags=re.IGNORECASE,
        )
        cleaned = " ".join(stem.replace("_", " ").replace("-", " ").split())
        return cleaned[:60] or "Без имени"

    def record(
        self,
        *,
        request_type: str,
        competitor_name: str,
        request_summary: str,
        response_summary: str,
        kind: str,
        label: str,
        source_url: Optional[str] = None,
        trust_score: Optional[int] = None,
        design_score: Optional[int] = None,
        red_flags: Optional[List[str]] = None,
        red_flag_categories: Optional[List[str]] = None,
        green_flags: Optional[List[str]] = None,
        payout_methods: Optional[List[str]] = None,
        exchanger_rate: Optional[float] = None,
        rate_source: Optional[str] = None,
        summary: str = "",
        source_id: Optional[str] = None,
        name_is_reliable: bool = True,
        identity_hints: Optional[List[str]] = None,
        remember: bool = True,
    ) -> Optional[dict]:
        """Пишет разбор и в историю, и в память — одними и теми же полями."""
        payout_methods = merge_payout_methods(payout_methods)
        history_service.add_entry(
            request_type=request_type,
            competitor_name=competitor_name,
            source_url=source_url,
            request_summary=request_summary,
            response_summary=response_summary,
            trust_score=trust_score,
            design_score=design_score,
            red_flags=red_flags,
            green_flags=green_flags,
            payout_methods=payout_methods,
            exchanger_rate=exchanger_rate,
            rate_source=rate_source,
        )
        if not remember or not competitor_name:
            return None
        return memory_service.remember(
            competitor_name=competitor_name,
            kind=kind,
            label=label,
            summary=summary,
            source_id=source_id,
            source_url=source_url,
            name_is_reliable=name_is_reliable,
            trust_score=trust_score,
            design_score=design_score,
            red_flags=red_flags,
            red_flag_categories=red_flag_categories,
            green_flags=green_flags,
            payout_methods=payout_methods,
            exchanger_rate=exchanger_rate,
            rate_source=rate_source,
            identity_hints=identity_hints,
        )

    def record_from_analysis(self, analysis, **kwargs):
        """
        Запись готового разбора: оценки и флаги берём из объекта,
        в kwargs — только то, чего в разборе нет (имя, файл, тип запроса).

        Подходит и для текста, и для картинки, и для отзывов:
        резюме ищется в summary, а если его нет — в description.
        Поле, переданное в kwargs, перекрывает значение из разбора
        (так к отзывам подмешивается префикс «отзывы:»).
        """
        summary = kwargs.pop("summary", None)
        if summary is None:
            summary = (
                getattr(analysis, "summary", None)
                or getattr(analysis, "description", "")
                or ""
            )
        response_summary = kwargs.pop("response_summary", summary)
        fields = dict(
            trust_score=getattr(analysis, "trust_score", None),
            design_score=getattr(analysis, "design_score", None),
            red_flags=getattr(analysis, "red_flags", None),
            red_flag_categories=getattr(analysis, "red_flag_categories", None),
            green_flags=getattr(analysis, "green_flags", None),
            payout_methods=getattr(analysis, "payout_methods", None),
            exchanger_rate=getattr(analysis, "exchanger_rate", None),
            rate_source=getattr(analysis, "rate_source", None),
            summary=summary,
            response_summary=response_summary,
        )
        fields.update(kwargs)
        return self.record(**fields)

    def remember_bestchange(self, competitor_name: str, rows, amount: int) -> bool:
        """
        Пишет в профиль курс и способы выплаты с BestChange.
        False — этого обменника нет ни в одной таблице.
        """
        payouts = rate_lookup_service.payouts_from_rows(rows)
        row = rate_lookup_service.rate_from_rows(rows)
        if not row and not payouts:
            return False
        if row:
            memory_service.remember(
                competitor_name=competitor_name,
                kind="rate",
                label=f"BestChange, {row.direction}",
                source_id=f"rate:{row.slug or row.exchanger_name}",
                summary=f"Курс {row.rate} ₽ за 1 USDT при сумме {amount} USDT",
                exchanger_rate=row.rate,
                rate_source=f"BestChange, {row.direction}, {amount} USDT",
                payout_methods=payouts,
            )
        else:
            memory_service.remember(
                competitor_name=competitor_name,
                kind="rate",
                label="BestChange, способы выплаты",
                source_id=f"payouts:{competitor_name}",
                summary="Способы выплаты по таблицам BestChange",
                payout_methods=payouts,
            )
        return True

    def ingest_table(self, table, filename: str, content: bytes) -> FileAnalysisResponse:
        """Таблица со ссылками — список конкурентов, не один документ."""
        file_hash = memory_service.source_id(content)
        rows_out = []
        queued = []

        for row in table.rows:
            self.record(
                request_type="table",
                competitor_name=row.competitor_name,
                request_summary=f"таблица: {filename} #{row.row_index}",
                response_summary=row.notes or row.competitor_name,
                kind="table",
                label=f"{filename} · строка {row.row_index}",
                summary=row.notes or f"Строка таблицы: {row.competitor_name}",
                source_id=f"table:{file_hash}:{row.row_index}",
                source_url=row.url,
                name_is_reliable=True,
                payout_methods=row.payout_methods,
                exchanger_rate=row.exchanger_rate,
                rate_source=f"таблица {filename}" if row.exchanger_rate else None,
            )
            rows_out.append(
                TableRowInfo(
                    competitor_name=row.competitor_name,
                    url=row.url,
                    exchanger_rate=row.exchanger_rate,
                    notes=row.notes or None,
                    payout_methods=row.payout_methods,
                )
            )
            if row.url:
                queued.append(row.url)

        names = [row.competitor_name for row in table.rows[:3]]
        label = f"таблица: {len(table.rows)} конкурентов"
        if names:
            label += f" ({', '.join(names)}{'…' if len(table.rows) > 3 else ''})"

        return FileAnalysisResponse(
            success=True,
            kind="table",
            competitor_name=label,
            table_rows=rows_out,
            queued_urls=queued,
        )

    def remove_competitor(self, competitor_name: str) -> dict:
        """Убирает обменника из памяти и из истории."""
        profile = memory_service.get_profile(competitor_name)
        names = [competitor_name]
        domains = []
        if profile:
            names.append(profile.get("competitor_name") or "")
            names.extend(profile.get("aliases") or [])
            domains.extend(profile.get("domains") or [])

        forgotten = memory_service.forget(competitor_name)
        removed_history = history_service.remove_competitor(names, domains)
        return {
            "success": True,
            "found": forgotten or removed_history > 0,
            "cleared": profile["competitor_name"] if profile else competitor_name,
            "history_removed": removed_history,
        }


ingest_service = IngestService()
