"""
Парсинг сайтов через Selenium: открыть страницу, снять скриншот, вытащить текст.

Кроме лендинга обменника этот же сервис открывает страницы отзывов
(BestChange и сторонние площадки) — там скриншот не нужен, только текст.
Браузер запускается долго (несколько секунд), поэтому для нескольких страниц
подряд используем ОДИН браузер (`session()` / `parse_many`).
"""

import base64
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import List, Optional
from urllib.parse import quote_plus, urlparse, parse_qs, unquote

from selenium import webdriver
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager

from backend.config import SEARCH_URL_TEMPLATE

logger = logging.getLogger(__name__)


@dataclass
class ParsedPage:
    url: str
    title: Optional[str]
    h1: Optional[str]
    text_excerpt: Optional[str]
    screenshot_base64: Optional[str]
    error: Optional[str] = None


class ParserService:
    def _create_driver(self) -> webdriver.Chrome:
        options = Options()
        options.add_argument("--headless=new")
        options.add_argument("--window-size=1400,1000")
        options.add_argument("--disable-gpu")
        options.add_argument("--no-sandbox")
        options.add_argument("--log-level=3")
        # обычный user-agent, чтобы не выглядеть как явный бот
        options.add_argument(
            "user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
        )
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)

    @contextmanager
    def session(self):
        """Один браузер на несколько страниц — иначе каждый запуск съедает секунды."""
        driver = None
        try:
            driver = self._create_driver()
            yield driver
        finally:
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    logger.debug("Не удалось корректно закрыть браузер", exc_info=True)

    def _open(
        self,
        driver: webdriver.Chrome,
        url: str,
        timeout: int,
        with_screenshot: bool,
        text_limit: int,
    ) -> ParsedPage:
        try:
            driver.set_page_load_timeout(timeout + 10)
            driver.get(url)
            WebDriverWait(driver, timeout).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )

            title = driver.title
            try:
                h1 = driver.find_element(By.TAG_NAME, "h1").text
            except Exception:
                h1 = None

            body_text = driver.find_element(By.TAG_NAME, "body").text
            text_excerpt = body_text[:text_limit]

            screenshot_b64 = None
            if with_screenshot:
                screenshot_b64 = base64.b64encode(driver.get_screenshot_as_png()).decode("utf-8")

            return ParsedPage(
                url=url,
                title=title,
                h1=h1,
                text_excerpt=text_excerpt,
                screenshot_base64=screenshot_b64,
            )
        except TimeoutException:
            return ParsedPage(url, None, None, None, None, error="Таймаут загрузки страницы")
        except WebDriverException as e:
            logger.warning("Selenium не смог открыть %s: %s", url, e)
            return ParsedPage(url, None, None, None, None, error=f"Ошибка браузера: {e.__class__.__name__}")

    def parse(self, url: str, timeout: int = 15, text_limit: int = 4000) -> ParsedPage:
        """Одна страница со скриншотом — для анализа лендинга конкурента."""
        with self.session() as driver:
            return self._open(driver, url, timeout, with_screenshot=True, text_limit=text_limit)

    def parse_many(
        self,
        urls: List[str],
        timeout: int = 12,
        text_limit: int = 6000,
        driver: Optional[webdriver.Chrome] = None,
    ) -> List[ParsedPage]:
        """
        Несколько страниц подряд без скриншотов — для сбора отзывов.
        Если передан driver — не поднимаем свой браузер (важно, когда вызывающий
        уже открыл сессию и хочет использовать её же).
        """
        if not urls:
            return []

        def _collect(active) -> List[ParsedPage]:
            return [
                self._open(active, url, timeout, with_screenshot=False, text_limit=text_limit)
                for url in urls
            ]

        if driver is not None:
            return _collect(driver)
        with self.session() as own:
            return _collect(own)

    # --- поиск сторонних площадок с отзывами ---

    def search_links(
        self,
        query: str,
        limit: int = 5,
        timeout: int = 12,
        driver: Optional[webdriver.Chrome] = None,
    ) -> List[str]:
        """
        Ищет ссылки через HTML-версию DuckDuckGo (без API-ключа).
        Возвращает обычные ссылки результатов, уже развёрнутые из редиректа.
        Если передан driver — не поднимаем второй браузер (важно для пачки имён).
        """
        search_url = SEARCH_URL_TEMPLATE.format(query=quote_plus(query))

        def _collect(active) -> List[str]:
            page = self._open(active, search_url, timeout, with_screenshot=False, text_limit=100)
            if page.error:
                logger.warning("Поиск не удался (%s): %s", query, page.error)
                return []
            hrefs = []
            for element in active.find_elements(By.CSS_SELECTOR, "a.result__a, a.result__url"):
                href = element.get_attribute("href")
                if href:
                    hrefs.append(href)
            return self._unique_resolved(hrefs, limit)

        if driver is not None:
            return _collect(driver)
        with self.session() as own:
            return _collect(own)

    @staticmethod
    def _unique_resolved(hrefs: List[str], limit: int) -> List[str]:
        links: List[str] = []
        seen = set()
        for href in hrefs:
            resolved = ParserService._resolve_redirect(href)
            if not resolved:
                continue
            key = resolved.rstrip("/").lower()
            if key in seen:
                continue
            seen.add(key)
            links.append(resolved)
            if len(links) >= limit:
                break
        return links

    @staticmethod
    def _resolve_redirect(href: str) -> Optional[str]:
        """DuckDuckGo отдаёт ссылки через свой редирект — достаём настоящий адрес."""
        if not href:
            return None
        parsed = urlparse(href)
        if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
            target = parse_qs(parsed.query).get("uddg", [])
            return unquote(target[0]) if target else None
        if parsed.scheme in ("http", "https"):
            return href
        return None


parser_service = ParserService()
