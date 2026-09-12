"""
Чтение загруженных файлов: PDF, сохранённая страница сайта, текстовые заметки.

Картинки сюда не попадают — их читает vision-модель напрямую (см. openai_service).
Задача сервиса — превратить файл в обычный текст, который можно отдать на анализ.
"""

import email
import io
import json
import logging
from email import policy
from pathlib import Path
from typing import Optional, Tuple

import openpyxl
from bs4 import BeautifulSoup
from docx import Document

from backend.services.pdf_service import pdf_service

logger = logging.getLogger(__name__)

# HEIC — формат фотографий с айфона. Vision-модель его не понимает,
# поэтому такие файлы конвертируем в JPEG (см. to_jpeg).
try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_SUPPORTED = True
except Exception:  # библиотека не встала — просто не принимаем HEIC
    HEIC_SUPPORTED = False
    logger.warning("pillow-heif не загрузился, HEIC приниматься не будет")

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"}
HEIC_EXTENSIONS = {".heic", ".heif"}
HTML_EXTENSIONS = {".html", ".htm"}
MHTML_EXTENSIONS = {".mhtml", ".mht"}
TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".csv", ".json"}
DOCX_EXTENSIONS = {".docx"}
XLSX_EXTENSIONS = {".xlsx", ".xlsm"}

SUPPORTED_HUMAN = (
    "PNG, JPG, WEBP, GIF, HEIC · PDF, DOCX · HTML, MHTML · XLSX, CSV · TXT, MD, JSON"
)


class FileService:
    supported_formats = SUPPORTED_HUMAN

    def detect_kind(self, filename: str, content_type: Optional[str]) -> str:
        """Возвращает: image / pdf / docx / html / xlsx / text / unknown"""
        suffix = Path(filename or "").suffix.lower()
        ctype = (content_type or "").lower()

        if suffix in HEIC_EXTENSIONS or "heic" in ctype or "heif" in ctype:
            return "image" if HEIC_SUPPORTED else "unknown"
        if suffix in IMAGE_EXTENSIONS or ctype.startswith("image/"):
            return "image"
        if suffix == ".pdf" or ctype == "application/pdf":
            return "pdf"
        if suffix in DOCX_EXTENSIONS or "wordprocessingml" in ctype:
            return "docx"
        if suffix in XLSX_EXTENSIONS or "spreadsheetml" in ctype:
            return "xlsx"
        if suffix in HTML_EXTENSIONS or suffix in MHTML_EXTENSIONS or "html" in ctype:
            return "html"
        if suffix == ".csv" or ctype in {"text/csv", "application/csv"}:
            return "csv"
        if suffix in TEXT_EXTENSIONS or ctype.startswith("text/"):
            return "text"
        return "unknown"

    def needs_jpeg_conversion(self, filename: str, content_type: Optional[str]) -> bool:
        suffix = Path(filename or "").suffix.lower()
        ctype = (content_type or "").lower()
        return suffix in HEIC_EXTENSIONS or "heic" in ctype or "heif" in ctype

    def to_jpeg(self, content: bytes) -> bytes:
        """HEIC с айфона → JPEG, потому что vision-модель принимает только обычные форматы."""
        from PIL import Image

        with Image.open(io.BytesIO(content)) as image:
            rgb = image.convert("RGB")
            buffer = io.BytesIO()
            rgb.save(buffer, format="JPEG", quality=88)
            return buffer.getvalue()

    def extract_text(self, content: bytes, filename: str, kind: str, limit: int = 14000) -> Tuple[str, Optional[str]]:
        """
        Достаёт текст из файла. Возвращает (текст, заголовок).
        Заголовок есть у HTML (тег title) — по нему часто видно название обменника.
        """
        if kind == "pdf":
            return self._from_pdf(content, limit), None
        if kind == "docx":
            return self._from_docx(content, limit), None
        if kind == "xlsx":
            return self._from_xlsx(content, limit), None
        if kind == "html":
            return self._from_html(content, filename, limit)
        if kind == "text":
            text = self._decode(content)
            if Path(filename or "").suffix.lower() == ".json":
                text = self.pretty_json(text)
            return text[:limit], None
        raise ValueError(f"Не умею читать этот тип файла: {kind}")

    # --- отдельные форматы ---

    def _from_pdf(self, content: bytes, limit: int) -> str:
        return pdf_service.extract_text(content, limit)

    def _from_docx(self, content: bytes, limit: int) -> str:
        document = Document(io.BytesIO(content))
        chunks = [p.text.strip() for p in document.paragraphs if p.text.strip()]

        # таблицы в Word часто и содержат самое интересное — условия и тарифы
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells if cell.text.strip()]
                if cells:
                    chunks.append(" | ".join(cells))

        return "\n".join(chunks)[:limit]

    def _from_xlsx(self, content: bytes, limit: int) -> str:
        workbook = openpyxl.load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        chunks = []
        total = 0

        for sheet in workbook.worksheets:
            chunks.append(f"# Лист: {sheet.title}")
            for row in sheet.iter_rows(values_only=True):
                cells = ["" if value is None else str(value).strip() for value in row]
                if not any(cells):
                    continue
                line = " | ".join(cells).strip(" |")
                chunks.append(line)
                total += len(line)
                if total >= limit:
                    break
            if total >= limit:
                break

        workbook.close()
        return "\n".join(chunks)[:limit]

    def _from_html(self, content: bytes, filename: str, limit: int) -> Tuple[str, Optional[str]]:
        raw = content
        # Chrome сохраняет «страницу одним файлом» в формате MHTML — это письмо с вложениями
        if Path(filename or "").suffix.lower() in MHTML_EXTENSIONS:
            raw = self._html_from_mhtml(content) or content

        soup = BeautifulSoup(self._decode(raw), "lxml")

        for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
            tag.decompose()

        title = soup.title.get_text(strip=True) if soup.title else None
        text = soup.get_text("\n", strip=True)

        # схлопываем пустые строки, которых в сохранённых страницах всегда много
        lines = [line for line in (l.strip() for l in text.splitlines()) if line]
        return "\n".join(lines)[:limit], title

    @staticmethod
    def _html_from_mhtml(content: bytes) -> Optional[bytes]:
        try:
            message = email.message_from_bytes(content, policy=policy.default)
            for part in message.walk():
                if part.get_content_type() == "text/html":
                    return part.get_payload(decode=True)
        except Exception:
            logger.warning("MHTML не разобрался, пробуем как обычный HTML", exc_info=True)
        return None

    @staticmethod
    def _decode(content: bytes) -> str:
        for encoding in ("utf-8", "cp1251", "latin-1"):
            try:
                return content.decode(encoding)
            except UnicodeDecodeError:
                continue
        return content.decode("utf-8", errors="ignore")

    @staticmethod
    def pretty_json(text: str) -> str:
        """JSON-заметки читаются моделью легче, если их развернуть."""
        try:
            return json.dumps(json.loads(text), ensure_ascii=False, indent=2)
        except Exception:
            return text


file_service = FileService()
