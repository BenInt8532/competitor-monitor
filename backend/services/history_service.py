"""
Хранение истории всех анализов в JSON-файле (как в уроке) — просто и без БД,
достаточно для MVP/ДЗ. summary_service.py читает эту историю для сводной таблицы.
"""

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from backend.config import settings
from backend.models.schemas import HistoryItem
from backend.utils import clean_name, domain_from_url, match_key


class HistoryService:
    def __init__(self):
        self.file_path = Path(settings.history_file)
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.file_path.exists():
            self._save([])

    def _load(self) -> List[dict]:
        try:
            return json.loads(self.file_path.read_text(encoding="utf-8"))
        except (FileNotFoundError, json.JSONDecodeError):
            return []

    def _save(self, items: List[dict]) -> None:
        self.file_path.write_text(
            json.dumps(items, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )

    def add_entry(
        self,
        request_type: str,
        request_summary: str,
        response_summary: str,
        competitor_name: Optional[str] = None,
        source_url: Optional[str] = None,
        rate_source: Optional[str] = None,
        trust_score: Optional[int] = None,
        design_score: Optional[int] = None,
        red_flags: Optional[List[str]] = None,
        green_flags: Optional[List[str]] = None,
        payout_methods: Optional[List[str]] = None,
        exchanger_rate: Optional[float] = None,
    ) -> HistoryItem:
        item = HistoryItem(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            request_type=request_type,
            competitor_name=competitor_name,
            source_url=source_url,
            rate_source=rate_source,
            request_summary=request_summary,
            response_summary=response_summary,
            trust_score=trust_score,
            design_score=design_score,
            red_flags=red_flags or [],
            green_flags=green_flags or [],
            payout_methods=payout_methods or [],
            exchanger_rate=exchanger_rate,
        )
        items = self._load()
        items.append(json.loads(item.model_dump_json()))
        # ограничиваем размер файла историей последних N записей
        items = items[-settings.max_history_items:]
        self._save(items)
        return item

    def get_history(self) -> List[HistoryItem]:
        return [HistoryItem(**item) for item in self._load()]

    def clear_history(self) -> None:
        self._save([])

    def remove_competitor(self, names: List[str], domains: Optional[List[str]] = None) -> int:
        """
        Стирает все записи истории про одного обменника.
        Сверяем и по имени, и по домену: в истории он мог называться GrumBot,
        а в другой записи — grumbot.net.
        """
        name_keys = {match_key(clean_name(name)) for name in names if name}
        domain_set = {d.lower() for d in (domains or []) if d}
        if not name_keys and not domain_set:
            return 0

        kept = []
        removed = 0
        for item in self._load():
            item_key = match_key(clean_name(item.get("competitor_name") or ""))
            item_domain = domain_from_url(item.get("source_url") or "")
            same_name = item_key and item_key in name_keys
            same_domain = item_domain and item_domain in domain_set
            if same_name or same_domain:
                removed += 1
            else:
                kept.append(item)
        if removed:
            self._save(kept)
        return removed


history_service = HistoryService()
