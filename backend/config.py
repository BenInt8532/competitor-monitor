"""
Конфигурация приложения.

.env ищем в двух местах — ключ из файла ассистент не читает:
1. папка проекта (competitor-monitor/.env) — так удобно после клона с GitHub;
2. папка урока PEm08 (на уровень выше) — как было у Ben с начала работы.

Данные (data/, history.json) остаются в папке урока, если она есть.
Если репозиторий открыли отдельно — всё внутри этой папки.
"""

import os
from pathlib import Path
from pydantic_settings import BaseSettings

# backend/config.py → backend → competitor-monitor
PROJECT_ROOT = Path(__file__).resolve().parent.parent
LESSON_ROOT = PROJECT_ROOT.parent


def _env_file() -> Path:
    for path in (PROJECT_ROOT / ".env", LESSON_ROOT / ".env"):
        if path.is_file():
            return path
    return PROJECT_ROOT / ".env"


def _data_root() -> Path:
    if (LESSON_ROOT / "data").is_dir():
        return LESSON_ROOT
    return PROJECT_ROOT


ENV_FILE = _env_file()
DATA_ROOT = _data_root()
DATA_DIR = DATA_ROOT / "data"

# --- Источники отзывов ---
# Формат страницы обменника на BestChange подтверждён вручную (2026-09-12):
#   https://www.bestchange.ru/<слаг>-exchanger.html?filter=reviews  — отзывы
#   https://www.bestchange.ru/<слаг>-exchanger.html?filter=claim    — финансовые претензии
# Слаг обычно совпадает с первой частью домена: coindrop.trade -> coindrop.
BESTCHANGE_SOURCES = [
    ("BestChange — отзывы", "https://www.bestchange.ru/{slug}-exchanger.html?filter=reviews"),
    ("BestChange — фин. претензии", "https://www.bestchange.ru/{slug}-exchanger.html?filter=claim"),
]

# Поиск сторонних площадок с отзывами: запрос в DuckDuckGo, берём первые ссылки.
SEARCH_URL_TEMPLATE = "https://html.duckduckgo.com/html/?q={query}"
REVIEW_SEARCH_QUERY = "{domain} отзывы обменник"

# Домены «помогу вернуть деньги» — огульно обвиняют любой обменник ради своих услуг.
# Их в сбор отзывов не берём совсем (см. раздел «Грабли» в отчёте урока).
REVIEW_BLACKLIST_DOMAINS = [
    "scammaviss.com",
    "trust-viper.com",
    "cryptorussia.ru",
    "torforex.ru",
    "chargeback",
    "vernut-dengi",
    "antiscam",
    "scam-",
    "-scam",
]


class Settings(BaseSettings):
    # --- ProxyAPI / OpenAI-совместимый доступ ---
    proxy_api_key: str = os.getenv("PROXY_API_KEY", "")
    proxy_api_base_url: str = "https://api.proxyapi.ru/openai/v1"
    openai_model: str = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
    openai_vision_model: str = os.getenv("OPENAI_VISION_MODEL", "gpt-4o-mini")

    # --- сервер ---
    api_host: str = os.getenv("API_HOST", "0.0.0.0")
    api_port: int = int(os.getenv("API_PORT", "8000"))

    # --- история ---
    history_file: str = str(DATA_ROOT / "history.json")
    max_history_items: int = 200

    # --- память по конкурентам (карточка копится между запусками) ---
    memory_file: str = str(DATA_ROOT / "competitors.json")
    results_file: str = str(DATA_ROOT / "2026-09-12-analiz-konkurentov.json")
    max_score_points: int = 30  # сколько замеров оценок хранить для динамики
    max_flags_per_profile: int = 40

    # --- курс ЦБ РФ ---
    cbr_rate_url: str = "https://www.cbr.ru/scripts/XML_daily.asp"
    cbr_rate_cache_hours: int = 12  # ЦБ обновляет раз в сутки, кэшируем с запасом

    # --- данные конкурентов ---
    data_dir: str = str(DATA_DIR)

    # --- очередь сайтов и сбор отзывов ---
    max_urls_per_batch: int = 10  # ДЗ: 1-10 сайтов за раз
    max_external_review_sources: int = 3  # сколько сторонних площадок тянуть помимо BestChange
    review_page_timeout: int = 12  # сек на загрузку одной страницы отзывов
    review_text_limit: int = 6000  # сколько символов отзывов отдаём модели с одного источника

    class Config:
        env_file = str(ENV_FILE)
        extra = "ignore"


settings = Settings()
