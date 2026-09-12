"""
Автоматический поиск курса USDT → RUB для конкурентов.

Почему не с лендинга: у большинства обменников курс спрятан в калькуляторе и
появляется только после выбора пары, а вёрстка калькулятора у каждого своя —
универсального парсера не выйдет. Зато на BestChange есть страница направления,
где курсы ВСЕХ обменников лежат одной таблицей: один заход — курсы на всех
конкурентов сразу. Сумму задаём в поле «отдаю», потому что у многих обменников
курс зависит от суммы сделки.
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException

from backend.models.schemas import CompetitorAnalysis
from backend.services.parser_service import parser_service
from backend.utils import is_usdt_rub_rate, match_key, merge_payout_methods

logger = logging.getLogger(__name__)

# Страницы направлений BestChange.
# ПОРЯДОК ВАЖЕН: курс берём из первой таблицы с for_rate=True, где обменник нашёлся
# (см. RATE_DIRECTION_NAMES и rate_from_rows). Приоритет задал Ben:
#   расчётный счёт → банковский перевод → СБП → наличные.
# Раньше порядка не было и курс «прыгал»: у одного обменника, который есть сразу
# в нескольких таблицах, источник менялся от прогона к прогону (85.36 из перевода
# в одном тесте, 86.10 из наличных в следующем).
# Страницы с for_rate=False открываем только ради сбора способов выплаты.
DIRECTION_PAGES = [
    ("Расчётный счёт RUB", "https://www.bestchange.ru/tether-trc20-to-settlement-rub.html", True),
    ("Банковский перевод RUB", "https://www.bestchange.ru/tether-trc20-to-wire-rub.html", True),
    ("СБП", "https://www.bestchange.ru/tether-trc20-to-sbp.html", True),
    ("Наличные RUB", "https://www.bestchange.ru/tether-trc20-to-cash-ruble.html", True),
    ("Сбербанк", "https://www.bestchange.ru/tether-trc20-to-sberbank.html", False),
    ("Т-Банк", "https://www.bestchange.ru/tether-trc20-to-tinkoff.html", False),
    ("Карта Visa/MC", "https://www.bestchange.ru/tether-trc20-to-visa-mastercard-rub.html", False),
]

DIRECTION_TO_PAYOUT = {
    "Расчётный счёт RUB": "расчётный счёт",
    "Банковский перевод RUB": "банковский перевод",
    "СБП": "СБП",
    "Наличные RUB": "наличные",
    "Сбербанк": "Сбербанк",
    "Т-Банк": "Т-Банк",
    "Карта Visa/MC": "карта",
}

RATE_DIRECTION_NAMES = [name for name, _url, for_rate in DIRECTION_PAGES if for_rate]

CACHE_TTL_SECONDS = 30 * 60  # курсы живут недолго, но дёргать BestChange на каждый сайт незачем
RATE_PATTERN = re.compile(r"(\d[\d\s]*[.,]?\d*)")
SLUG_PATTERN = re.compile(r"/([a-z0-9\-]+)-exchanger\.html")


@dataclass
class RateRow:
    exchanger_name: str
    slug: Optional[str]
    rate: float
    reserve: Optional[str]
    reviews: Optional[str]
    direction: str


class RateLookupService:
    def __init__(self):
        self._cache: Dict[int, List[RateRow]] = {}
        self._cached_at: Dict[int, float] = {}

    # --- сбор таблицы ---

    def _parse_number(self, text: str) -> Optional[float]:
        match = RATE_PATTERN.search((text or "").replace("\xa0", " "))
        if not match:
            return None
        raw = match.group(1).replace(" ", "").replace(",", ".")
        try:
            value = float(raw)
        except ValueError:
            return None
        # отсекаем мусор: курс USDT к рублю живёт в разумном коридоре
        return value if 30 < value < 300 else None

    @staticmethod
    def _landed_on_requested_page(landed: str, expected: str) -> bool:
        """Главная BestChange тоже содержит #content_table — чужой редирект нельзя читать."""
        def _norm(value: str) -> str:
            path = (value or "").split("?")[0].rstrip("/").lower()
            return path.replace("://www.", "://")

        return _norm(landed) == _norm(expected)

    def _read_direction(
        self, driver, name: str, url: str, amount: int, need_amount: bool = True
    ) -> List[RateRow]:
        driver.set_page_load_timeout(30)
        driver.get(url)

        # если адрес увели редиректом (битая ссылка направления), на главной BestChange
        # тоже есть #content_table — и мы молча прочитаем чужие строки. Лучше выйти.
        if not self._landed_on_requested_page(driver.current_url, url):
            logger.warning(
                "Направление %s увело на %s — пропускаем, чтобы не прочитать чужую таблицу",
                url, driver.current_url,
            )
            return []
        try:
            WebDriverWait(driver, 15).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "#content_table tbody tr"))
            )
        except TimeoutException:
            logger.warning("Таблица курсов на %s не появилась за 15 сек", url)
            return []

        def _first_rate_text() -> str:
            rows = driver.find_elements(By.CSS_SELECTOR, "#content_table tbody tr")
            if not rows:
                return ""
            cells = rows[0].find_elements(By.TAG_NAME, "td")
            return cells[3].text if len(cells) > 3 else ""

        # сумму вписываем только когда нужен курс; для каталога выплат достаточно
        # факта «обменник есть в этой таблице»
        if need_amount:
            before = _first_rate_text()
            try:
                driver.execute_script(
                    """
                    const input = document.getElementById('give');
                    if (input) {
                      input.value = arguments[0];
                      input.dispatchEvent(new Event('input', {bubbles: true}));
                      input.dispatchEvent(new Event('keyup', {bubbles: true}));
                      input.dispatchEvent(new Event('change', {bubbles: true}));
                    }
                    """,
                    str(amount),
                )
                try:
                    WebDriverWait(driver, 6).until(lambda d: _first_rate_text() != before)
                except TimeoutException:
                    # курс мог и не поменяться от суммы — это нормально, читаем как есть
                    logger.debug("Таблица на %s не изменилась после ввода суммы", url)
            except Exception:
                logger.warning("Не удалось вписать сумму на %s, читаем таблицу как есть", url, exc_info=True)

        rows: List[RateRow] = []
        for element in driver.find_elements(By.CSS_SELECTOR, "#content_table tbody tr"):
            cells = element.find_elements(By.TAG_NAME, "td")
            if len(cells) < 4:
                continue

            exchanger_name = cells[1].text.strip()
            if not exchanger_name:
                continue

            rate = self._parse_number(cells[3].text)
            if rate is None:
                continue

            slug = None
            try:
                href = cells[1].find_element(By.TAG_NAME, "a").get_attribute("href") or ""
                found = SLUG_PATTERN.search(href)
                slug = found.group(1) if found else None
            except Exception:
                slug = None

            rows.append(
                RateRow(
                    exchanger_name=exchanger_name,
                    slug=slug,
                    rate=rate,
                    reserve=cells[4].text.strip() if len(cells) > 4 else None,
                    reviews=cells[5].text.strip() if len(cells) > 5 else None,
                    direction=name,
                )
            )
        return rows

    def collect(self, amount: int = 1000) -> List[RateRow]:
        """Блокирующий сбор курсов по всем направлениям. Вызывать через asyncio.to_thread."""
        cached_at = self._cached_at.get(amount)
        if cached_at and time.time() - cached_at < CACHE_TTL_SECONDS:
            return self._cache.get(amount, [])

        rows: List[RateRow] = []
        try:
            with parser_service.session() as driver:
                for name, url, for_rate in DIRECTION_PAGES:
                    try:
                        rows += self._read_direction(
                            driver, name, url, amount, need_amount=for_rate
                        )
                    except Exception:
                        logger.exception("Направление %s не прочиталось", url)
        except Exception:
            logger.exception("Не удалось открыть браузер для сбора курсов")
            return self._cache.get(amount, [])

        if rows:
            self._cache[amount] = rows
            self._cached_at[amount] = time.time()
        return rows

    # --- сопоставление с конкретным конкурентом ---

    def matches(self, competitor_name: str, domain: str, amount: int = 1000) -> List[RateRow]:
        """Все строки этого обменника во всех таблицах направлений."""
        rows = self.collect(amount)
        if not rows:
            return []

        domain_key = match_key(domain.split(".")[0] if domain else "")
        name_key = match_key(competitor_name)
        candidates = [key for key in (domain_key, name_key) if key]
        if not candidates:
            return []

        found: List[RateRow] = []
        seen = set()
        for row in rows:
            if not self._row_matches(row, candidates):
                continue
            key = (row.direction, row.slug or row.exchanger_name)
            if key in seen:
                continue
            seen.add(key)
            found.append(row)
        return found

    def find(self, competitor_name: str, domain: str, amount: int = 1000) -> Optional[RateRow]:
        """Курс: первое совпадение в таблицах счёта / расчётного счёта / наличных."""
        return self.rate_from_rows(self.matches(competitor_name, domain, amount))

    def payouts_for(self, competitor_name: str, domain: str, amount: int = 1000) -> List[str]:
        """Способы выплаты по факту листинга: в каких таблицах BestChange обменник есть."""
        return self.payouts_from_rows(self.matches(competitor_name, domain, amount))

    @staticmethod
    def payouts_from_rows(rows: List[RateRow]) -> List[str]:
        return merge_payout_methods(
            [DIRECTION_TO_PAYOUT[row.direction] for row in rows if row.direction in DIRECTION_TO_PAYOUT]
        )

    @staticmethod
    def rate_from_rows(rows: List[RateRow]) -> Optional[RateRow]:
        by_direction = {row.direction: row for row in rows}
        for name in RATE_DIRECTION_NAMES:
            if name in by_direction:
                return by_direction[name]
        return None

    def apply_listing(
        self,
        analysis: CompetitorAnalysis,
        rows: List[RateRow],
        *,
        amount: int = 1000,
        fill_rate: bool = True,
        replace_rate: bool = False,
        manual_rate: Optional[float] = None,
    ) -> None:
        """
        Накладывает таблицу BestChange на уже готовый разбор.

        Способы выплаты всегда дописываются (склейка, не замена).
        Курс:
        - руками — как просил пользователь, таблицу не смотрим;
        - replace_rate=True — курс с лендинга выкидываем (модель путает BTC
          и обратное направление), берём только BestChange;
        - иначе — дописываем курс, только если его ещё нет.
        """
        analysis.payout_methods = merge_payout_methods(
            analysis.payout_methods,
            self.payouts_from_rows(rows),
        )
        if manual_rate is not None:
            analysis.exchanger_rate = manual_rate
            analysis.rate_source = "вписан руками"
            return

        if replace_rate:
            if analysis.exchanger_rate is not None and not is_usdt_rub_rate(
                analysis.exchanger_rate
            ):
                logger.warning("Курс с лендинга отброшен: %s", analysis.exchanger_rate)
            analysis.exchanger_rate = None
            analysis.rate_source = None
        elif analysis.exchanger_rate is not None and not is_usdt_rub_rate(
            analysis.exchanger_rate
        ):
            logger.warning(
                "Отбрасываю неправдоподобный курс %s", analysis.exchanger_rate
            )
            analysis.exchanger_rate = None
            analysis.rate_source = None

        if not fill_rate or analysis.exchanger_rate is not None:
            return

        found = self.rate_from_rows(rows)
        if found and is_usdt_rub_rate(found.rate):
            analysis.exchanger_rate = found.rate
            analysis.rate_source = f"BestChange, {found.direction}, {amount} USDT"

    @staticmethod
    def _row_matches(row: RateRow, candidates: List[str]) -> bool:
        if row.slug and match_key(row.slug) in candidates:
            return True
        name_key = match_key(row.exchanger_name)
        if name_key in candidates:
            return True
        if name_key and any(key.startswith(name_key) or name_key.startswith(key) for key in candidates):
            return True
        return False


rate_lookup_service = RateLookupService()
