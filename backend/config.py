"""
Конфигурация приложения.

.env ищем в двух местах — ключ из файла ассистент не читает:
1. папка проекта (competitor-monitor/.env) — так удобно после клона с GitHub;
2. папка урока PEm08 (на уровень выше) — как было у Ben с начала работы.

Данные (data/, history.json) остаются в папке урока, если она есть.
Если репозиторий открыли отдельно — всё внутри этой папки.
"""

import os
import sys
from pathlib import Path
from pydantic_settings import BaseSettings


def _frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _exe_dir() -> Path:
    return Path(sys.executable).resolve().parent


def _source_project_root() -> Path:
    return Path(__file__).resolve().parent.parent


# Распакованные файлы .exe (frontend, код) vs папка, куда можно писать JSON.
BUNDLE_ROOT = Path(getattr(sys, "_MEIPASS")) if _frozen() else _source_project_root()
PROJECT_ROOT = _exe_dir() if _frozen() else _source_project_root()
LESSON_ROOT = PROJECT_ROOT if _frozen() else PROJECT_ROOT.parent


def _env_file() -> Path:
    # Содержимое .env не читаем в чат — только ищем файл по пути.
    candidates = [PROJECT_ROOT / ".env"]
    if not _frozen():
        candidates.append(LESSON_ROOT / ".env")
    else:
        candidates.append(Path.cwd() / ".env")
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def _data_root() -> Path:
    if _frozen():
        if (Path.cwd() / "data").is_dir():
            return Path.cwd()
        return PROJECT_ROOT
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
