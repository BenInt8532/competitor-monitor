"""
Сбор отзывов о конкуренте с внешних площадок и разбор их на красные/зелёные флаги.

Как работает:
1. По домену собираем ссылки на BestChange — страницу отзывов и страницу
   финансовых претензий (формат подтверждён вручную, см. config.BESTCHANGE_SOURCES).
2. Дополнительно ищем сторонние площадки через поиск (без API-ключа).
   Сайты «помогу вернуть деньги» отбрасываем сразу — они огульно обвиняют всех подряд.
3. Открываем страницы одним браузером, собираем текст.
4. Отдаём собранный текст модели с отдельным промптом под отзывы.
"""

import asyncio
import logging
from typing import List, Optional, Tuple

from backend.config import (
    settings,
    BESTCHANGE_SOURCES,
    REVIEW_BLACKLIST_DOMAINS,
    REVIEW_SEARCH_QUERY,
)
from backend.models.schemas import ReviewInsight, ReviewSourceInfo
from backend.services.parser_service import parser_service
from backend.utils import bestchange_slug, domain_from_url, normalize_url

logger = logging.getLogger(__name__)

# Признаки того, что страница открылась, но отзывов там нет
EMPTY_MARKERS = ("пока нет отзывов", "страница не найдена", "404 not found", "ничего не найдено")
MIN_USEFUL_CHARS = 400


class ReviewsService:
    def _is_blacklisted(self, url: str) -> bool:
        haystack = url.lower()
        return any(bad in haystack for bad in REVIEW_BLACKLIST_DOMAINS)

    def _planned_sources(self, url: str, extra_urls: List[str]) -> List[Tuple[str, str]]:
        """Ссылки, которые точно знаем заранее: BestChange + переданные руками."""
        slug = bestchange_slug(url)
        sources: List[Tuple[str, str]] = []
        if slug:
            for name, template in BESTCHANGE_SOURCES:
                sources.append((name, template.format(slug=slug)))
        for extra in extra_urls or []:
            normalized = normalize_url(extra)
            if normalized and not self._is_blacklisted(normalized):
                sources.append(("Указано вручную", normalized))
        return sources

    def _find_external_sources(self, url: str, driver=None) -> List[Tuple[str, str]]:
        """Ищем сторонние площадки с отзывами поиском по домену."""
        domain = domain_from_url(url)
        if not domain:
            return []

        try:
            found = parser_service.search_links(
                REVIEW_SEARCH_QUERY.format(domain=domain),
                limit=settings.max_external_review_sources * 3,
                timeout=settings.review_page_timeout,
                driver=driver,
            )
        except Exception:
            logger.exception("Поиск сторонних площадок сорвался для %s", domain)
            return []

        picked: List[Tuple[str, str]] = []
        for link in found:
            link_domain = domain_from_url(link)
            # сам сайт обменника и bestchange уже учтены отдельно
            if not link_domain or link_domain == domain or "bestchange" in link_domain:
                continue
            if self._is_blacklisted(link):
                continue
            picked.append((f"Сторонняя площадка ({link_domain})", link))
            if len(picked) >= settings.max_external_review_sources:
                break
        return picked

    def collect(self, url: str, extra_urls: Optional[List[str]] = None):
        """Блокирующий сбор текста отзывов. Вызывать через asyncio.to_thread."""
        planned = self._planned_sources(url, extra_urls or [])
        # без заранее известных ссылок и без домена искать нечего — браузер не поднимаем
        if not planned and not domain_from_url(url):
            return [], []

        # один браузер на поиск площадок И на чтение страниц — раньше их было два
        with parser_service.session() as driver:
            sources = planned + self._find_external_sources(url, driver=driver)
            if not sources:
                return [], []
            pages = parser_service.parse_many(
                [src_url for _, src_url in sources],
                timeout=settings.review_page_timeout,
                text_limit=settings.review_text_limit,
                driver=driver,
            )

        infos: List[ReviewSourceInfo] = []
        texts: List[str] = []

        for (name, src_url), page in zip(sources, pages):
            if page.error:
                infos.append(ReviewSourceInfo(name=name, url=src_url, status="ошибка", note=page.error))
                continue

            text = (page.text_excerpt or "").strip()
            lowered = text.lower()

            if len(text) < MIN_USEFUL_CHARS or any(marker in lowered for marker in EMPTY_MARKERS):
                infos.append(
                    ReviewSourceInfo(
                        name=name,
                        url=src_url,
                        status="пусто",
                        chars=len(text),
                        note="Страница открылась, но отзывов на ней не видно",
                    )
                )
                continue

            infos.append(ReviewSourceInfo(name=name, url=src_url, status="ok", chars=len(text)))
            texts.append(f"=== ИСТОЧНИК: {name} ({src_url}) ===\n{text}")

        return texts, infos

    async def analyze(self, url: str, extra_urls: Optional[List[str]] = None) -> ReviewInsight:
        from backend.services.openai_service import openai_service

        texts, infos = await asyncio.to_thread(self.collect, url, extra_urls)

        if not texts:
            return ReviewInsight(
                reviews_found=False,
                summary="Отзывы не найдены: площадки не открылись или на них нет текста отзывов.",
                sources=infos,
            )

        insight = await openai_service.analyze_reviews(
            reviews_text="\n\n".join(texts),
            domain=domain_from_url(url),
        )
        insight.reviews_found = True
        insight.sources = infos
        return insight


reviews_service = ReviewsService()
