"""
Дозаполнение с лендинга: текст неполный → если есть ссылка и не хватает выплаты/курса,
открываем сайт и добираем только недостающее.

Курс с самой страницы обменника не берём: на лендинге рядом висят BTC и оба
направления USDT↔RUB, модель регулярно подставляет не то (см. отчёт, тест
8 скупых notes.md). Недостающий курс — только таблица BestChange.

Если ссылки нет или сайт не открылся — исходный разбор не падает.
BestChange пробуем даже когда Selenium не смог открыть страницу.
"""

import asyncio
import logging

from typing import List, Optional

from backend.models.schemas import CompetitorAnalysis
from backend.services.openai_service import openai_service
from backend.services.parser_service import parser_service
from backend.services.rate_lookup_service import rate_lookup_service
from backend.services.table_service import table_service
from backend.utils import domain_from_url, is_usdt_rub_rate, merge_payout_methods, trust_from_flags

logger = logging.getLogger(__name__)

# Корни слов, а не целые фразы: модель каждый раз формулирует по-новому
# («затрудняет анализ» → «затрудняет полноценный анализ»), и точная фраза перестаёт
# совпадать. За три теста подряд список приходилось дополнять именно из-за этого.
# Корень ловит все падежи и вставленные слова сразу.
INSUFFICIENT_MARKERS = (
    "недостаточ",      # недостаточно / недостаточной
    "отсутств",        # отсутствует / отсутствуют / отсутствие
    "затрудня",        # затрудняет / затрудняют (в т.ч. «затрудняет полноценный анализ»)
    "мало данных",
    "не хватает",
    "нет информации",
    "не предоставлен",
    "не указан",
)


class EnrichmentService:
    async def from_landing_if_needed(
        self, analysis: CompetitorAnalysis, text: str
    ) -> CompetitorAnalysis:
        self._drop_implausible_rate(analysis)

        url = table_service.first_url_in_text(text)
        if not url:
            return analysis

        missing_payout = not analysis.payout_methods
        missing_rate = analysis.exchanger_rate is None
        # «нет ни одного флага» негодный признак: модель выжимает флаг даже из одной
        # строки заметки («есть расчётный счёт» → зелёный флаг), и на сайт мы уже не идём.
        # Честный признак того, что страницу ещё не смотрели, — пустой design_score:
        # он появляется только после разбора скриншота.
        landing_not_read = analysis.design_score is None
        if (analysis.rate_source or "") == "вписан руками":
            missing_rate = False

        original_summary = analysis.summary or ""
        original_red = list(analysis.red_flags)
        original_green = list(analysis.green_flags)
        web_analysis = None

        # сайт нужен ради выплаты и флагов; за курсом туда не ходим
        if missing_payout or landing_not_read:
            web_analysis, landing_note = await self._read_landing(url)
            if web_analysis:
                self._merge_landing(analysis, web_analysis)
            if landing_note:
                analysis.enrichment_note = landing_note

        await self._fill_from_bestchange(analysis, url, fill_rate=missing_rate)

        self._refresh_summary_and_trust(
            analysis,
            web_analysis,
            original_summary,
            original_red,
            original_green,
        )
        return analysis

    @staticmethod
    def _drop_implausible_rate(analysis: CompetitorAnalysis) -> None:
        """Убираем курс биткоина / сумму сделки, если модель всё же его подставила."""
        if analysis.exchanger_rate is None:
            return
        if (analysis.rate_source or "") == "вписан руками":
            return
        if is_usdt_rub_rate(analysis.exchanger_rate):
            return
        logger.warning(
            "Отбрасываю неправдоподобный курс %s (%s)",
            analysis.exchanger_rate,
            analysis.rate_source or "без источника",
        )
        analysis.exchanger_rate = None
        analysis.rate_source = None

    async def _read_landing(self, url: str):
        try:
            parsed = await asyncio.to_thread(parser_service.parse, url)
        except Exception:
            logger.warning("Дозагрузка с лендинга %s сорвалась", url, exc_info=True)
            return None, f"Не удалось открыть сайт {url}"
        if parsed.error:
            logger.warning("Лендинг %s не открылся: %s", url, parsed.error)
            return None, f"Сайт {url} не открылся: {parsed.error}"

        try:
            web = await openai_service.analyze_website_screenshot(
                screenshot_base64=parsed.screenshot_base64,
                page_text=parsed.text_excerpt,
                url=url,
            )
            return web, None
        except Exception:
            logger.warning("Разбор лендинга %s для дозаполнения сорвался", url, exc_info=True)
            return None, f"Не удалось разобрать страницу {url}"

    @staticmethod
    def _merge_landing(analysis: CompetitorAnalysis, web: CompetitorAnalysis) -> None:
        analysis.payout_methods = merge_payout_methods(
            analysis.payout_methods, web.payout_methods
        )
        analysis.red_flags = list(dict.fromkeys(
            analysis.red_flags + [f"с сайта: {flag}" for flag in web.red_flags]
        ))
        analysis.green_flags = list(dict.fromkeys(
            analysis.green_flags + [f"с сайта: {flag}" for flag in web.green_flags]
        ))
        if not analysis.design_score and web.design_score:
            analysis.design_score = web.design_score
        if not analysis.detected_name and web.detected_name:
            analysis.detected_name = web.detected_name
        if not analysis.strengths and web.strengths:
            analysis.strengths = web.strengths
        if not analysis.weaknesses and web.weaknesses:
            analysis.weaknesses = web.weaknesses
        if not analysis.unique_offers and web.unique_offers:
            analysis.unique_offers = web.unique_offers
        if not analysis.recommendations and web.recommendations:
            analysis.recommendations = web.recommendations

    async def _fill_from_bestchange(
        self, analysis: CompetitorAnalysis, url: str, fill_rate: bool
    ) -> None:
        """
        Каталог выплат — по всем таблицам, где обменник есть.
        Курс — только из счёта / расчётного / наличных, и только если его ещё нет.
        """
        domain = domain_from_url(url)
        name = analysis.detected_name or ""
        try:
            found_rows = await asyncio.to_thread(
                rate_lookup_service.matches, name, domain, 1000
            )
        except Exception:
            logger.warning("BestChange для %s сорвался", url, exc_info=True)
            return

        rate_lookup_service.apply_listing(
            analysis, found_rows, amount=1000, fill_rate=fill_rate
        )

    def _refresh_summary_and_trust(
        self,
        analysis: CompetitorAnalysis,
        web: Optional[CompetitorAnalysis],
        original_summary: str,
        original_red: List[str],
        original_green: List[str],
    ) -> None:
        """
        Первый проход по скупой заметке пишет «данных не хватает» и trust=5.
        После сайта/BestChange это уже неправда — переписываем резюме и балл.
        """
        facts = self._facts_summary(analysis)
        if self._looks_insufficient(original_summary):
            site_summary = (web.summary or "").strip() if web else ""
            if site_summary and not self._looks_insufficient(site_summary):
                analysis.summary = f"{site_summary} {facts}".strip()
            elif facts:
                analysis.summary = facts

        had_no_flags = not original_red and not original_green
        if not had_no_flags:
            return

        web_trust = web.trust_score if web else 0
        if web_trust > 0:
            analysis.trust_score = web_trust
        elif analysis.green_flags or analysis.red_flags:
            analysis.trust_score = trust_from_flags(
                len(analysis.green_flags), len(analysis.red_flags)
            )

    @staticmethod
    def _looks_insufficient(summary: str) -> bool:
        low = (summary or "").lower()
        return any(marker in low for marker in INSUFFICIENT_MARKERS)

    @staticmethod
    def _facts_summary(analysis: CompetitorAnalysis) -> str:
        bits = []
        if analysis.payout_methods:
            bits.append("Способы выплаты: " + ", ".join(analysis.payout_methods) + ".")
        if analysis.exchanger_rate is not None:
            source = analysis.rate_source or "источник не указан"
            bits.append(f"Курс {analysis.exchanger_rate} ₽/USDT ({source}).")
        greens = len(analysis.green_flags)
        reds = len(analysis.red_flags)
        if greens or reds:
            bits.append(
                f"После дозаполнения: {greens} зелёных и {reds} красных признаков."
            )
        return " ".join(bits)


enrichment_service = EnrichmentService()
