"""
Главный файл FastAPI — эндпоинты приложения "AI-анализатор конкурентов: крипто-обменники".

Черновик ассистента по образцу main.py из cursor_.md (демо-проект урока PEm08),
расширен под нишу и требования Ben. Тестирует и доводит до рабочего состояния
Ben в Cursor (см. otchet-PEm08-multimodalnoe-prilozhenie.md).
"""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, Response

from backend.config import BUNDLE_ROOT, settings
from backend.models.schemas import (
    TextAnalysisRequest, TextAnalysisResponse,
    FileAnalysisResponse,
    ParseUrlRequest, ParseUrlResponse, ParsedContent,
    BatchAnalyzeRequest, BatchAnalyzeResponse,
    ReviewsRequest, ReviewsResponse,
    UrlListRequest, UrlListResponse, UrlListItem,
    HistoryResponse,
    SummaryResponse,
    CompetitorsResponse, MergeProfilesRequest, RenameProfileRequest,
    RemoveCompetitorRequest,
)
from backend.services.openai_service import openai_service
from backend.services.parser_service import parser_service
from backend.services.reviews_service import reviews_service
from backend.services.file_service import file_service
from backend.services.table_service import table_service
from backend.services.memory_service import memory_service
from backend.services.rate_lookup_service import rate_lookup_service
from backend.services.history_service import history_service
from backend.services.ingest_service import ingest_service
from backend.services.enrichment_service import enrichment_service
from backend.services.rates_service import rates_service
from backend.services.summary_service import summary_service
from backend.utils import (
    competitor_name_from_url,
    domain_from_url,
    normalize_url,
    parse_url_list,
)

logger = logging.getLogger(__name__)

app = FastAPI(
    title="Competitor Monitor — крипто-обменники",
    description="AI-анализатор конкурентов для ниши крипто-обменников (ДЗ урока PEm08)",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

FRONTEND_DIR = BUNDLE_ROOT / "frontend"
app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.middleware("http")
async def no_cache_for_static(request, call_next):
    """
    Браузер любит держать старый styles.css/app.js и показывать интерфейс,
    которого в коде уже нет. В рабочем приложении кеш нужен, здесь — только мешает.
    """
    response = await call_next(request)
    if request.url.path.startswith("/static") or request.url.path == "/":
        response.headers["Cache-Control"] = "no-store, must-revalidate"
    return response


@app.get("/")
async def root():
    return FileResponse(str(FRONTEND_DIR / "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/config")
async def get_config():
    """Настройки, которые нужны интерфейсу."""
    return {
        "max_urls_per_batch": settings.max_urls_per_batch,
        "supported_formats": file_service.supported_formats,
    }


# === Анализ текста ===

@app.post("/analyze/text", response_model=TextAnalysisResponse)
async def analyze_text(request: TextAnalysisRequest):
    try:
        analysis = await openai_service.analyze_text(request.text)
        if request.exchanger_rate is not None:
            analysis.exchanger_rate = request.exchanger_rate
            analysis.rate_source = "вписан руками"
        analysis = await enrichment_service.from_landing_if_needed(analysis, request.text)
        # в текстовом разделе имя вводится руками — если не ввели, в память не кладём,
        # иначе получится профиль-помойка «Без имени» из разных обменников
        name = request.competitor_name or analysis.detected_name
        ingest_service.record_from_analysis(
            analysis,
            request_type="text",
            competitor_name=name or "",
            request_summary=request.text[:200],
            kind="text",
            label="вставленный текст",
            remember=bool(name),
        )
        return TextAnalysisResponse(success=True, analysis=analysis)
    except Exception as e:
        logger.exception("Анализ текста сорвался")
        return TextAnalysisResponse(success=False, error=str(e))


# === Анализ файла: скриншот или PDF ===

@app.post("/analyze/file", response_model=FileAnalysisResponse)
async def analyze_file(file: UploadFile = File(...)):
    """
    Принимает скриншот, PDF, сохранённую страницу сайта или текстовую заметку.
    Название обменника не спрашиваем — модель читает его из содержимого
    (логотип, домен, заголовок страницы, шапка документа).
    """
    filename = file.filename or ""
    try:
        content = await file.read()
        kind = file_service.detect_kind(filename, file.content_type)

        if kind == "unknown":
            return FileAnalysisResponse(
                success=False,
                error=f"Не знаю такой формат. Поддерживаются: {file_service.supported_formats}",
            )

        # --- картинка: читает vision-модель ---
        if kind == "image":
            mime_type = file.content_type or "image/jpeg"
            if file_service.needs_jpeg_conversion(filename, file.content_type):
                # фото с айфона (HEIC) модель не принимает — переводим в JPEG
                content = await asyncio.to_thread(file_service.to_jpeg, content)
                mime_type = "image/jpeg"

            image_b64 = base64.b64encode(content).decode("utf-8")
            analysis = await openai_service.analyze_image(image_b64, mime_type=mime_type)
            competitor_name = analysis.detected_name or ingest_service.name_from_filename(filename)
            image_greens = analysis.green_flags or analysis.safety_claims
            profile = ingest_service.record_from_analysis(
                analysis,
                request_type="image",
                competitor_name=competitor_name,
                request_summary=filename or "изображение",
                response_summary=analysis.description[:200],
                kind="image",
                label=filename or "изображение",
                summary=analysis.description,
                source_id=memory_service.source_id(content),
                name_is_reliable=bool(analysis.detected_name),
                green_flags=image_greens,
                identity_hints=[filename, ingest_service.name_from_filename(filename)],
            )
            return FileAnalysisResponse(
                success=True,
                kind="image",
                competitor_name=profile["display_name"],
                image_analysis=analysis,
                profile=memory_service.get_profile(profile["key"]),
            )

        # --- таблица конкурентов: XLSX / CSV, иногда Word с таблицей внутри ---
        if kind in {"xlsx", "csv", "docx"}:
            table = await asyncio.to_thread(table_service.parse, content, filename, kind)
            if table.is_competitor_table:
                return ingest_service.ingest_table(table, filename, content)

        # --- всё остальное превращаем в текст ---
        text, page_title = await asyncio.to_thread(
            file_service.extract_text, content, filename, kind
        )

        if len(text.strip()) < 50:
            hint = {
                "pdf": "В PDF не нашлось текста — похоже, это скан. Сохрани страницу картинкой, "
                       "её прочитает vision-модель.",
                "html": "В сохранённой странице не нашлось текста — возможно, содержимое "
                        "подгружается скриптами. Проще снять скриншот.",
            }.get(kind, "В файле слишком мало текста для анализа.")
            return FileAnalysisResponse(success=False, kind=kind, error=hint)

        if page_title:
            text = f"Заголовок страницы: {page_title}\n\n{text}"

        analysis = await openai_service.analyze_text(text)
        analysis = await enrichment_service.from_landing_if_needed(analysis, text)
        competitor_name = analysis.detected_name or page_title or ingest_service.name_from_filename(filename)
        profile = ingest_service.record_from_analysis(
            analysis,
            request_type=kind,
            competitor_name=competitor_name,
            request_summary=f"{kind}: {filename}",
            kind=kind,
            label=filename or kind,
            source_id=memory_service.source_id(content),
            name_is_reliable=bool(analysis.detected_name or page_title),
            rate_source=analysis.rate_source or ("найден в файле" if analysis.exchanger_rate else None),
            identity_hints=[filename, ingest_service.name_from_filename(filename), page_title or ""],
        )
        return FileAnalysisResponse(
            success=True,
            kind=kind,
            competitor_name=profile["display_name"],
            text_analysis=analysis,
            profile=memory_service.get_profile(profile["key"]),
        )
    except Exception as e:
        logger.exception("Анализ файла %s сорвался", filename)
        return FileAnalysisResponse(success=False, error=str(e))


# === Разбор вставленного списка ссылок ===

@app.post("/urls/parse", response_model=UrlListResponse)
async def parse_urls(request: UrlListRequest):
    """
    Принимает текст со ссылками (строки/запятые/пробелы), возвращает чистый список
    с уже определёнными именами конкурентов — руками вводить название не нужно.
    """
    limit = settings.max_urls_per_batch
    urls = parse_url_list(request.raw, limit=limit)
    all_found = parse_url_list(request.raw, limit=1000)

    items = [UrlListItem(url=url, competitor_name=competitor_name_from_url(url)) for url in urls]
    return UrlListResponse(
        items=items,
        total=len(items),
        limit=limit,
        skipped=max(0, len(all_found) - len(items)),
    )


# === Анализ сайта по URL (Selenium + отзывы) ===

@app.post("/analyze/url", response_model=ParseUrlResponse)
async def analyze_url(request: ParseUrlRequest):
    url = normalize_url(request.url)
    if not domain_from_url(url):
        return ParseUrlResponse(success=False, error=f"Не похоже на ссылку: {request.url}")

    # имя берём из домена, если его не передали явно
    competitor_name = request.competitor_name or competitor_name_from_url(url)

    try:
        # лендинг и отзывы тянем параллельно — это два разных браузера, но так вдвое быстрее
        tasks = [asyncio.to_thread(parser_service.parse, url)]
        if request.include_reviews:
            tasks.append(reviews_service.analyze(url))

        finished = await asyncio.gather(*tasks, return_exceptions=True)

        parsed = finished[0]
        if isinstance(parsed, Exception):
            raise parsed
        if parsed.error:
            return ParseUrlResponse(success=False, error=f"{competitor_name}: {parsed.error}")

        reviews = None
        if request.include_reviews:
            candidate = finished[1]
            if isinstance(candidate, Exception):
                logger.warning("Сбор отзывов для %s сорвался: %s", url, candidate)
            else:
                reviews = candidate

        text_analysis = await openai_service.analyze_website_screenshot(
            screenshot_base64=parsed.screenshot_base64,
            page_text=parsed.text_excerpt,
            url=url,
        )

        found_rows = await asyncio.to_thread(
            rate_lookup_service.matches, competitor_name, domain_from_url(url), request.rate_amount
        )
        # курс с лендинга не берём: модель путает BTC и обратное направление RUB→USDT.
        rate_lookup_service.apply_listing(
            text_analysis,
            found_rows,
            amount=request.rate_amount,
            replace_rate=True,
            manual_rate=request.exchanger_rate,
        )

        # флаги из отзывов помечаем отдельно, чтобы в сводке было видно их происхождение
        red_flags = list(text_analysis.red_flags)
        green_flags = list(text_analysis.green_flags)
        if reviews and reviews.reviews_found:
            red_flags += [f"отзывы: {flag}" for flag in reviews.red_flags]
            green_flags += [f"отзывы: {flag}" for flag in reviews.green_flags]

        profile = ingest_service.record_from_analysis(
            text_analysis,
            request_type="url",
            competitor_name=competitor_name,
            request_summary=url,
            kind="url",
            label=domain_from_url(url) or url,
            source_id=f"url:{domain_from_url(url)}",
            source_url=url,
            red_flags=red_flags,
            green_flags=green_flags,
        )

        data = ParsedContent(
            url=url,
            competitor_name=profile["display_name"],
            title=parsed.title,
            h1=parsed.h1,
            page_text_excerpt=parsed.text_excerpt,
            screenshot_base64=parsed.screenshot_base64,
            text_analysis=text_analysis,
            reviews=reviews,
        )
        return ParseUrlResponse(success=True, data=data)
    except Exception as e:
        logger.exception("Анализ сайта %s сорвался", url)
        return ParseUrlResponse(success=False, error=f"{competitor_name}: {e}")


# === Только отзывы (BestChange и сторонние площадки) ===

@app.post("/analyze/reviews", response_model=ReviewsResponse)
async def analyze_reviews(request: ReviewsRequest):
    url = normalize_url(request.url)
    if not domain_from_url(url):
        return ReviewsResponse(success=False, error=f"Не похоже на ссылку: {request.url}")

    competitor_name = competitor_name_from_url(url)
    try:
        insight = await reviews_service.analyze(url, request.extra_urls)

        if insight.reviews_found:
            ingest_service.record_from_analysis(
                insight,
                request_type="reviews",
                competitor_name=competitor_name,
                request_summary=f"отзывы: {url}",
                kind="reviews",
                label=f"отзывы: {domain_from_url(url)}",
                source_id=f"reviews:{domain_from_url(url)}",
                source_url=url,
                trust_score=insight.reviews_trust_score,
                red_flags=[f"отзывы: {flag}" for flag in insight.red_flags],
                green_flags=[f"отзывы: {flag}" for flag in insight.green_flags],
            )

        return ReviewsResponse(success=True, competitor_name=competitor_name, reviews=insight)
    except Exception as e:
        logger.exception("Сбор отзывов для %s сорвался", url)
        return ReviewsResponse(success=False, competitor_name=competitor_name, error=str(e))


# === Batch-анализ всех подпапок data/ ===

@app.post("/analyze/batch", response_model=BatchAnalyzeResponse)
async def analyze_batch(request: BatchAnalyzeRequest):
    data_dir = Path(request.data_dir or settings.data_dir)
    if not data_dir.exists():
        raise HTTPException(status_code=404, detail=f"Папка не найдена: {data_dir}")

    results = []
    errors = []

    for competitor_dir in sorted(data_dir.iterdir()):
        if not competitor_dir.is_dir():
            continue
        competitor_name = competitor_dir.name

        notes_file = competitor_dir / "notes.md"
        if not notes_file.exists():
            errors.append(f"{competitor_name}: нет notes.md, пропущен")
            continue

        try:
            text = notes_file.read_text(encoding="utf-8")
            analysis = await openai_service.analyze_text(text)
            analysis = await enrichment_service.from_landing_if_needed(analysis, text)
            ingest_service.record_from_analysis(
                analysis,
                request_type="batch",
                competitor_name=analysis.detected_name or competitor_name,
                request_summary=f"batch: {notes_file.name}",
                kind="batch",
                label=f"{competitor_dir.name}/notes.md",
                source_id=f"batch:{competitor_dir.name}",
                name_is_reliable=bool(analysis.detected_name),
            )
            results.append(ParsedContent(
                url=competitor_name,
                competitor_name=competitor_name,
                text_analysis=analysis,
            ))
        except Exception as e:
            errors.append(f"{competitor_name}: {e}")

    return BatchAnalyzeResponse(success=len(errors) == 0, results=results, errors=errors)


# === Курс ЦБ ===

@app.get("/rate")
async def get_rate():
    try:
        rate = await rates_service.get_usd_rate()
        return rate
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Не удалось получить курс ЦБ: {e}")


# === Сводная таблица сравнения ===

@app.get("/summary", response_model=SummaryResponse)
async def get_summary():
    return await summary_service.build_summary()


@app.get("/summary/export")
async def export_summary():
    """Скачать сводную таблицу CSV — чтобы приложить к ДЗ без скриншота."""
    data = await summary_service.build_summary()
    filename = summary_service.csv_filename()
    return Response(
        content=summary_service.to_csv(data),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/summary/rates/refresh")
async def refresh_rates(amount: int = 1000):
    """
    Тянет курсы USDT/RUB для всех известных конкурентов с таблиц направлений BestChange.
    Отдельной кнопкой, а не при открытии вкладки: это единственная медленная операция
    (поход в браузере, секунд двадцать), таблица сама по себе строится мгновенно.
    """
    profiles = memory_service.get_profiles()
    if not profiles:
        return {"success": True, "updated": 0, "checked": 0, "not_found": []}

    updated = 0
    not_found = []
    for profile in profiles:
        name = profile["competitor_name"]
        domain = profile["domains"][0] if profile["domains"] else ""
        try:
            found_rows = await asyncio.to_thread(
                rate_lookup_service.matches, name, domain, amount
            )
        except Exception as e:
            logger.warning("Курс для %s не получен: %s", name, e)
            not_found.append(name)
            continue

        if ingest_service.remember_bestchange(name, found_rows, amount):
            updated += 1
        else:
            not_found.append(name)

    return {
        "success": True,
        "updated": updated,
        "checked": len(profiles),
        "not_found": not_found,
        "amount": amount,
    }


# === Память по конкурентам ===

@app.get("/competitors", response_model=CompetitorsResponse)
async def get_competitors():
    """Накопленные профили: всё, что собрано по каждому обменнику из разных материалов."""
    profiles = memory_service.get_profiles()
    profiles.sort(key=lambda p: p.get("last_seen") or "", reverse=True)
    return CompetitorsResponse(profiles=profiles, total=len(profiles))


@app.post("/competitors/merge")
async def merge_competitors(request: MergeProfilesRequest):
    """Склеить два профиля, если автоматика не поняла, что это один обменник."""
    ok = memory_service.merge(request.source_name, request.target_name)
    if not ok:
        raise HTTPException(status_code=400, detail="Нечего склеивать: профили не найдены или совпадают")
    return {"success": True}


@app.post("/competitors/rename")
async def rename_competitor(request: RenameProfileRequest):
    ok = memory_service.rename(request.competitor_name, request.new_name)
    if not ok:
        raise HTTPException(status_code=404, detail="Профиль не найден")
    return {"success": True}


@app.delete("/competitors")
async def clear_competitors(competitor_name: Optional[str] = None):
    """Без параметра стирает всю память, с именем — конкурента целиком (сравнение, история, курсы)."""
    if competitor_name:
        result = ingest_service.remove_competitor(competitor_name)
        if not result["found"]:
            raise HTTPException(status_code=404, detail="Такого конкурента нет")
        return result

    memory_service.clear()
    history_service.clear_history()
    return {"success": True, "cleared": "все профили"}


@app.post("/competitors/remove")
async def remove_competitor(request: RemoveCompetitorRequest):
    """Убирает одного обменника отовсюду: сравнение, история, накопленный профиль."""
    result = ingest_service.remove_competitor(request.competitor_name)
    if not result["found"]:
        raise HTTPException(status_code=404, detail="Такого конкурента нет")
    return result


# === История ===

@app.get("/history", response_model=HistoryResponse)
async def get_history():
    items = history_service.get_history()
    return HistoryResponse(items=items, total=len(items))


@app.delete("/history")
async def clear_history():
    history_service.clear_history()
    return {"success": True}
