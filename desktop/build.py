"""
Сборка Windows-.exe: python desktop/build.py

По умолчанию — папка dist/competitionmonitor/ (так надёжнее: рядом лежат DLL).
Один файл: python desktop/build.py onefile -> dist/competitionmonitor.exe
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

DESKTOP = Path(__file__).resolve().parent
ROOT = DESKTOP.parent
FRONTEND = ROOT / "frontend"
APP_NAME = "competitionmonitor"


HIDDEN = [
    "backend",
    "backend.main",
    "backend.config",
    "backend.utils",
    "backend.models",
    "backend.models.schemas",
    "backend.services.openai_service",
    "backend.services.parser_service",
    "backend.services.reviews_service",
    "backend.services.file_service",
    "backend.services.table_service",
    "backend.services.memory_service",
    "backend.services.rate_lookup_service",
    "backend.services.history_service",
    "backend.services.ingest_service",
    "backend.services.enrichment_service",
    "backend.services.rates_service",
    "backend.services.summary_service",
    "backend.services.pdf_service",
    "uvicorn",
    "uvicorn.logging",
    "uvicorn.loops",
    "uvicorn.loops.auto",
    "uvicorn.protocols",
    "uvicorn.protocols.http",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets",
    "uvicorn.protocols.websockets.auto",
    "uvicorn.lifespan",
    "uvicorn.lifespan.on",
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "dotenv",
    "multipart",
    "openai",
    "httpx",
    "anyio",
    "sniffio",
    "h11",
    "requests",
    "selenium",
    "selenium.webdriver",
    "selenium.webdriver.chrome",
    "selenium.webdriver.chrome.service",
    "selenium.webdriver.chrome.options",
    "webdriver_manager",
    "webdriver_manager.chrome",
    "bs4",
    "lxml",
    "lxml.etree",
    "pypdf",
    "docx",
    "openpyxl",
    "PIL",
    "PIL.Image",
    "pillow_heif",
    "PyQt6",
    "PyQt6.QtCore",
    "PyQt6.QtGui",
    "PyQt6.QtWidgets",
]


COLLECT = [
    "uvicorn",
    "fastapi",
    "starlette",
    "pydantic",
    "pydantic_settings",
    "openai",
    "httpx",
    "selenium",
    "webdriver_manager",
    "lxml",
    "PIL",
    "pillow_heif",
    "openpyxl",
    "docx",
]


def _args(onefile: bool) -> list[str]:
    sep = os.pathsep
    args = [
        str(DESKTOP / "main.py"),
        "--name", APP_NAME,
        "--noconfirm",
        "--clean",
        "--windowed",
        "--distpath", str(DESKTOP / "dist"),
        "--workpath", str(DESKTOP / "build"),
        "--specpath", str(DESKTOP),
        "--paths", str(ROOT),
        "--paths", str(DESKTOP),
        "--add-data", f"{FRONTEND}{sep}frontend",
        "--collect-submodules", "backend",
    ]
    args.append("--onefile" if onefile else "--onedir")
    args.append("--noupx")
    for pkg in ("openai", "httpx", "pydantic", "fastapi", "starlette", "uvicorn"):
        args.extend(["--copy-metadata", pkg])
    for name in HIDDEN:
        args.extend(["--hidden-import", name])
    for name in COLLECT:
        args.extend(["--collect-all", name])
    return args


def build_exe(onefile: bool = False) -> None:
    print("=" * 60)
    print("Сборка desktop-приложения (PyQt6 + FastAPI -> .exe)")
    print("=" * 60)

    try:
        import PyInstaller.__main__
        import PyInstaller
        print(f"PyInstaller {PyInstaller.__version__}")
    except ImportError:
        print("PyInstaller не установлен. Из корня проекта:")
        print(r"  .\.venv\Scripts\python.exe -m pip install pyinstaller PyQt6 requests")
        sys.exit(1)

    if not FRONTEND.is_dir():
        print(f"Нет папки frontend: {FRONTEND}")
        sys.exit(1)

    print("Режим:", "один файл" if onefile else "папка с .exe и библиотеками")
    print("Имя:", f"{APP_NAME}.exe")
    PyInstaller.__main__.run(_args(onefile))

    exe = (
        DESKTOP / "dist" / f"{APP_NAME}.exe"
        if onefile
        else DESKTOP / "dist" / APP_NAME / f"{APP_NAME}.exe"
    )
    if not exe.exists():
        print("Сборка прошла, но .exe не найден:", exe)
        sys.exit(1)

    size_mb = exe.stat().st_size / (1024 * 1024)
    print()
    print("=" * 60)
    print("Готово")
    print("=" * 60)
    print(f"Файл: {exe}")
    print(f"Размер: {size_mb:.1f} MB")
    print()
    print("Перед запуском положи .env рядом с .exe (тот же PROXY_API_KEY, что у сайта).")
    print("Нужен установленный Google Chrome.")
    print("Папку data/ для вкладки «Папки data/» положи рядом с .exe или запускай из папки урока.")


def clean() -> None:
    print("Чищу артефакты сборки…")
    for name in ("build", "dist"):
        path = DESKTOP / name
        if path.exists():
            shutil.rmtree(path)
            print("  удалено", name)
    for spec in DESKTOP.glob("*.spec"):
        spec.unlink()
        print("  удалено", spec.name)
    print("Готово")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "clean":
        clean()
    elif cmd == "onefile":
        build_exe(onefile=True)
    else:
        build_exe(onefile=False)
