"""
Курс ЦБ РФ (USD/RUB) — бесплатный публичный XML API, без ключа и регистрации.
Обновляется раз в сутки, поэтому кэшируем на несколько часов (см. config.cbr_rate_cache_hours).
"""

import logging
from datetime import datetime, timedelta
from typing import Optional
from xml.etree import ElementTree

import httpx

from backend.config import settings
from backend.models.schemas import RateInfo

logger = logging.getLogger(__name__)


class RatesService:
    def __init__(self):
        self._cache: Optional[RateInfo] = None
        self._cached_at: Optional[datetime] = None

    async def get_usd_rate(self) -> RateInfo:
        if self._is_cache_fresh():
            return self._cache

        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(settings.cbr_rate_url)
            response.raise_for_status()

        root = ElementTree.fromstring(response.content)
        date_attr = root.attrib.get("Date", "")

        usd_value = None
        for valute in root.findall("Valute"):
            char_code = valute.findtext("CharCode")
            if char_code == "USD":
                value_str = valute.findtext("Value", "").replace(",", ".")
                usd_value = float(value_str)
                break

        if usd_value is None:
            raise ValueError("Не нашёл курс USD в ответе ЦБ РФ — проверь формат XML вручную")

        rate = RateInfo(usd_rub=usd_value, date=date_attr, source="cbr.ru")
        self._cache = rate
        self._cached_at = datetime.now()
        return rate

    def _is_cache_fresh(self) -> bool:
        if self._cache is None or self._cached_at is None:
            return False
        return datetime.now() - self._cached_at < timedelta(hours=settings.cbr_rate_cache_hours)


rates_service = RatesService()
