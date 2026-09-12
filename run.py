"""Запуск сервера разработки: python run.py (или uvicorn backend.main:app --reload)"""

import uvicorn

from backend.config import settings

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host=settings.api_host, port=settings.api_port, reload=True)
