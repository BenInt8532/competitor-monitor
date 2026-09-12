"""
Pydantic-модели проекта: что приложение принимает на вход и что отдаёт в ответ.

Черновик подготовлен ассистентом по образцу из cursor_.md (демо-проект урока PEm08),
расширен под нишу «крипто-обменники» — Ben дорабатывает/тестирует в Cursor.
"""

from datetime import datetime
from typing import Optional, List
from pydantic import BaseModel, Field


# === Запросы ===

class TextAnalysisRequest(BaseModel):
    """Запрос на анализ текста (описание/notes.md/вставленные отзывы конкурента)"""
    text: str = Field(..., min_length=10, description="Текст для анализа")
    competitor_name: Optional[str] = Field(None, description="Имя конкурента — для группировки в истории/сводке")
    exchanger_rate: Optional[float] = Field(
        None, description="Курс обменника ₽/USDT, если известен (Ben берёт из notes.md/BestChange) — для сравнения с курсом ЦБ"
    )


class ParseUrlRequest(BaseModel):
    """Запрос на анализ сайта по URL (Selenium: скриншот + текст)"""
    url: str = Field(..., description="URL сайта обменника")
    competitor_name: Optional[str] = Field(
        None, description="Имя конкурента. Если не задано — берём автоматически из домена ссылки"
    )
    exchanger_rate: Optional[float] = Field(None, description="Курс обменника ₽/USDT, если известен")
    include_reviews: bool = Field(
        True, description="Искать отзывы на BestChange и сторонних площадках и разбирать их на флаги"
    )
    rate_amount: int = Field(
        1000, ge=1, description="Сумма сделки в USDT, для которой смотрим курс (у многих курс зависит от объёма)"
    )


class ReviewsRequest(BaseModel):
    """Запрос на разбор только отзывов (без анализа самого лендинга)"""
    url: str = Field(..., description="Ссылка на обменник или его домен — по нему ищем отзывы")
    extra_urls: List[str] = Field(
        default_factory=list, description="Дополнительные ссылки на страницы отзывов, если знаешь их вручную"
    )


class BatchAnalyzeRequest(BaseModel):
    """Запрос на анализ всех подпапок data/ разом"""
    data_dir: Optional[str] = Field(None, description="Путь к data/, если отличается от дефолтного в config.py")


# === Ответы: анализ ===

class CompetitorAnalysis(BaseModel):
    """Структурированный анализ конкурента (текст)"""
    strengths: List[str] = Field(default_factory=list, description="Сильные стороны")
    weaknesses: List[str] = Field(default_factory=list, description="Слабые стороны")
    unique_offers: List[str] = Field(default_factory=list, description="Уникальные предложения")
    recommendations: List[str] = Field(default_factory=list, description="Рекомендации")
    summary: str = Field("", description="Общее резюме")
    detected_name: Optional[str] = Field(
        None, description="Название обменника, распознанное в самом тексте/документе"
    )
    exchanger_rate: Optional[float] = Field(
        None, description="Курс ₽ за 1 USDT, если он прямо указан в тексте/на странице"
    )
    design_score: Optional[int] = Field(
        None, ge=0, le=10, description="Оценка дизайна — приходит из разбора скриншота страницы"
    )
    rate_source: Optional[str] = Field(
        None, description="Откуда взят курс: страница обменника, BestChange или введён руками"
    )
    enrichment_note: Optional[str] = Field(
        None, description="Что пошло не так при попытке дозаполнить данные с сайта"
    )

    # --- поля под нишу «крипто-обменники" ---
    payout_methods: List[str] = Field(
        default_factory=list,
        description="Все найденные способы выплаты: СБП, карта, банки, перевод, расчётный счёт, наличные",
    )
    trust_score: int = Field(0, ge=0, le=10, description="Оценка доверия/безопасности (0-10)")
    red_flags: List[str] = Field(
        default_factory=list,
        description="Найденные опасные признаки, с краткой цитатой/пояснением откуда взято",
    )
    red_flag_categories: List[str] = Field(
        default_factory=list,
        description=(
            "Категория каждого красного флага, в том же порядке, что red_flags. "
            "Нужна, чтобы код мог взвесить тяжесть: блокировка карты весит куда больше, "
            "чем «выплата только через СБП» (см. FLAG_CAPS в utils.py)"
        ),
    )
    green_flags: List[str] = Field(
        default_factory=list,
        description="Найденные безопасные признаки, с краткой цитатой/пояснением откуда взято",
    )
    trust_reason: Optional[str] = Field(
        None,
        description="Почему оценка такая: например «не выше 3/10: жалобы на блокировку карты»",
    )


class ImageAnalysis(BaseModel):
    """Анализ изображения (скриншот лендинга/калькулятора)"""
    description: str = Field("", description="Описание изображения")
    detected_name: Optional[str] = Field(
        None, description="Название обменника, считанное с картинки (логотип, адрес сайта в шапке)"
    )
    marketing_insights: List[str] = Field(default_factory=list, description="Маркетинговые инсайты")
    design_score: int = Field(0, ge=0, le=10, description="Оценка визуального стиля (0-10)")
    visual_style_analysis: str = Field("", description="Анализ визуального стиля")
    safety_claims: List[str] = Field(
        default_factory=list,
        description="Маркетинговые фразы про безопасность, видимые на скрине (напр. «одним платежом»)",
    )
    recommendations: List[str] = Field(default_factory=list, description="Рекомендации")
    trust_score: Optional[int] = Field(
        None, ge=0, le=10, description="Доверие по скрину с учётом известного имени/адреса сайта"
    )
    red_flags: List[str] = Field(default_factory=list, description="Опасные признаки, видимые на скрине")
    red_flag_categories: List[str] = Field(
        default_factory=list,
        description="Категория каждого красного флага, в том же порядке, что red_flags",
    )
    green_flags: List[str] = Field(default_factory=list, description="Безопасные признаки, видимые на скрине")
    payout_methods: List[str] = Field(default_factory=list, description="Способы выплаты, видимые на скрине")


class ReviewSourceInfo(BaseModel):
    """Одна площадка с отзывами: откуда брали и что получилось"""
    name: str = Field(..., description="Название источника, напр. 'BestChange — отзывы'")
    url: str
    status: str = Field(..., description="ok / пусто / ошибка / пропущен")
    chars: int = Field(0, description="Сколько символов текста удалось собрать")
    note: Optional[str] = Field(None, description="Пояснение, если что-то пошло не так")


class ReviewInsight(BaseModel):
    """Разбор отзывов с внешних площадок (BestChange и др.)"""
    reviews_found: bool = Field(False, description="Удалось ли вообще найти отзывы")
    summary: str = Field("", description="Краткий вывод по отзывам")
    red_flags: List[str] = Field(default_factory=list, description="Опасные признаки из отзывов")
    red_flag_categories: List[str] = Field(
        default_factory=list,
        description="Категория каждого красного флага, в том же порядке, что red_flags",
    )
    green_flags: List[str] = Field(default_factory=list, description="Хорошие признаки из отзывов")
    complaint_quotes: List[str] = Field(
        default_factory=list, description="Дословные цитаты жалоб с датой/суммой, если есть"
    )
    positive_quotes: List[str] = Field(default_factory=list, description="Дословные цитаты позитивных отзывов")
    reviews_trust_score: int = Field(0, ge=0, le=10, description="Оценка доверия только по отзывам (0-10)")
    ignored_claims: List[str] = Field(
        default_factory=list,
        description="Что модель отбросила как голословное обвинение без конкретики",
    )
    sources: List[ReviewSourceInfo] = Field(default_factory=list, description="Какие площадки реально опрошены")


class ParsedContent(BaseModel):
    """Результат парсинга страницы (Selenium)"""
    url: str
    competitor_name: Optional[str] = Field(None, description="Имя, определённое автоматически из домена")
    title: Optional[str] = None
    h1: Optional[str] = None
    page_text_excerpt: Optional[str] = None
    screenshot_base64: Optional[str] = None
    text_analysis: Optional[CompetitorAnalysis] = None
    image_analysis: Optional[ImageAnalysis] = None
    reviews: Optional[ReviewInsight] = None
    error: Optional[str] = None


# === Ответы: обёртки success/error (как в уроке) ===

class TextAnalysisResponse(BaseModel):
    success: bool
    analysis: Optional[CompetitorAnalysis] = None
    error: Optional[str] = None


class ProfileSource(BaseModel):
    """Один материал, из которого собран профиль: файл, ссылка, заметка"""
    id: str
    kind: str
    label: str
    url: Optional[str] = None
    at: Optional[str] = None
    summary: Optional[str] = None
    trust_score: Optional[int] = None
    design_score: Optional[int] = None


class ProfileFlag(BaseModel):
    """Флаг с историей: сколько раз встретился и в каких материалах"""
    text: str
    count: int = 1
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    sources: List[str] = Field(default_factory=list)


class CompetitorProfile(BaseModel):
    """
    Накопленный профиль обменника: всё, что про него собрано из разных материалов.
    Скриншот, Word-файл и отзывы про один обменник складываются сюда вместе.
    """
    key: str = Field(..., description="Ключ сопоставления: имя в латинице без символов")
    competitor_name: str
    aliases: List[str] = Field(default_factory=list, description="Под какими именами встречался")
    domains: List[str] = Field(default_factory=list)
    first_seen: Optional[str] = None
    last_seen: Optional[str] = None
    sources: List[ProfileSource] = Field(default_factory=list)
    sources_count: int = 0
    trust_score: Optional[float] = Field(None, description="Среднее по материалам, где оценка была")
    trust_observations: int = 0
    design_score: Optional[float] = None
    design_observations: int = 0
    red_flags: List[ProfileFlag] = Field(default_factory=list)
    green_flags: List[ProfileFlag] = Field(default_factory=list)
    payout_methods: List[ProfileFlag] = Field(default_factory=list)
    exchanger_rate: Optional[float] = None
    rate_source: Optional[str] = None


class CompetitorsResponse(BaseModel):
    profiles: List[CompetitorProfile] = Field(default_factory=list)
    total: int = 0


class MergeProfilesRequest(BaseModel):
    """Склейка двух профилей руками, если автоматика не поняла, что это один обменник"""
    source_name: str = Field(..., description="Профиль, который вливаем")
    target_name: str = Field(..., description="Профиль, в который вливаем")


class RenameProfileRequest(BaseModel):
    competitor_name: str
    new_name: str


class RemoveCompetitorRequest(BaseModel):
    competitor_name: str = Field(..., description="Имя обменника, как в таблице сравнения")


class TableRowInfo(BaseModel):
    """Одна строка сравнительной таблицы: конкурент + то, что удалось вытащить из ячеек"""
    competitor_name: str
    url: Optional[str] = None
    exchanger_rate: Optional[float] = None
    notes: Optional[str] = None
    payout_methods: List[str] = Field(default_factory=list)


class FileAnalysisResponse(BaseModel):
    """Ответ на загрузку файла: скриншот или PDF — решает сервер по типу файла"""
    success: bool
    kind: Optional[str] = Field(None, description="image / pdf / docx / html / xlsx / csv / table / text")
    competitor_name: Optional[str] = Field(None, description="Имя, определённое автоматически по содержимому")
    image_analysis: Optional[ImageAnalysis] = None
    text_analysis: Optional[CompetitorAnalysis] = None
    profile: Optional[CompetitorProfile] = Field(
        None, description="Профиль конкурента со всем, что накоплено по нему из других материалов"
    )
    table_rows: List[TableRowInfo] = Field(
        default_factory=list,
        description="Если файл оказался таблицей конкурентов — разобранные строки",
    )
    queued_urls: List[str] = Field(
        default_factory=list,
        description="Ссылки из таблицы, которые нужно разобрать как обычные сайты",
    )
    error: Optional[str] = None


class ParseUrlResponse(BaseModel):
    success: bool
    data: Optional[ParsedContent] = None
    error: Optional[str] = None


class BatchAnalyzeResponse(BaseModel):
    success: bool
    results: List[ParsedContent] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)


class ReviewsResponse(BaseModel):
    success: bool
    competitor_name: Optional[str] = None
    reviews: Optional[ReviewInsight] = None
    error: Optional[str] = None


class UrlListRequest(BaseModel):
    """Разбор вставленного списка ссылок: сервер сам чистит и проверяет их"""
    raw: str = Field(..., description="Текст со ссылками: по строкам, через запятую или пробел")


class UrlListItem(BaseModel):
    url: str
    competitor_name: str


class UrlListResponse(BaseModel):
    items: List[UrlListItem] = Field(default_factory=list)
    total: int = 0
    limit: int = 0
    skipped: int = Field(0, description="Сколько ссылок отброшено сверх лимита")


# === История ===

class HistoryItem(BaseModel):
    id: str
    timestamp: datetime
    request_type: str  # "text", "image", "url", "batch", "reviews"
    competitor_name: Optional[str] = None
    source_url: Optional[str] = None
    rate_source: Optional[str] = None
    request_summary: str
    response_summary: str
    # сырые оценки — нужны для сводной таблицы (summary_service.py)
    trust_score: Optional[int] = None
    design_score: Optional[int] = None
    red_flags: List[str] = Field(default_factory=list)
    green_flags: List[str] = Field(default_factory=list)
    payout_methods: List[str] = Field(default_factory=list)
    exchanger_rate: Optional[float] = None


class HistoryResponse(BaseModel):
    items: List[HistoryItem]
    total: int


# === Курс ЦБ и сводная таблица ===

class RateInfo(BaseModel):
    """Официальный курс USD/RUB от ЦБ РФ"""
    usd_rub: float = Field(..., description="Официальный курс, ₽ за 1 USD")
    date: str = Field(..., description="Дата курса (как в ответе ЦБ)")
    source: str = Field("cbr.ru", description="Источник")


class CompetitorSummaryRow(BaseModel):
    """Одна строка сводной таблицы сравнения конкурентов"""
    competitor_name: str
    source_url: Optional[str] = None
    trust_score: Optional[float] = None
    trust_note: Optional[str] = Field(
        None, description="Откуда цифра или почему прочерк: модель / флаги / только скрин"
    )
    design_score: Optional[float] = None
    design_note: Optional[str] = Field(
        None, description="Что значит оценка дизайна для пользователя, не «просто красиво»"
    )
    sources_count: int = Field(0, description="Из скольких материалов собрана строка")
    materials: List[str] = Field(default_factory=list, description="Какие именно файлы и разборы вошли в строку")
    aliases: List[str] = Field(default_factory=list, description="Под какими именами встречался")
    payout_methods: List[str] = Field(default_factory=list)
    red_flags: List[str] = Field(default_factory=list)
    green_flags: List[str] = Field(default_factory=list)
    exchanger_rate: Optional[float] = Field(None, description="Курс обменника, если найден/указан")
    rate_spread: Optional[float] = Field(None, description="exchanger_rate - курс ЦБ; чем меньше, тем выгоднее")
    rate_source: Optional[str] = Field(None, description="Откуда взялся курс")
    rate_fetched_at: Optional[str] = Field(None, description="Когда курс записали в профиль")
    last_analyzed_at: Optional[datetime] = None


class SummaryResponse(BaseModel):
    rows: List[CompetitorSummaryRow]
    cbr_rate: Optional[RateInfo] = None
