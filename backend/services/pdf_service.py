"""
Чтение PDF: вытаскиваем текст, чтобы отдать его на анализ как обычный текст.

Важно: если PDF — это скан (картинка страницы), текста внутри нет, и распознать
его без OCR нельзя. В таком случае честно возвращаем пустую строку, а не выдумываем.
"""

import io
import logging

from pypdf import PdfReader

logger = logging.getLogger(__name__)


class PdfService:
    def extract_text(self, content: bytes, limit: int = 12000) -> str:
        reader = PdfReader(io.BytesIO(content))
        chunks = []
        total = 0

        for page in reader.pages:
            try:
                text = page.extract_text() or ""
            except Exception:
                logger.warning("Страница PDF не прочиталась", exc_info=True)
                continue

            text = text.strip()
            if not text:
                continue

            chunks.append(text)
            total += len(text)
            if total >= limit:
                break

        return "\n\n".join(chunks)[:limit]


pdf_service = PdfService()
