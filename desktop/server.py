"""
Поднимает тот же FastAPI, что и сайт, в фоне.
Окно PyQt ходит на 127.0.0.1 — как браузер, только без Chrome у пользователя.

Порт выбирается сам:
- по умолчанию 8000, можно задать снаружи переменной CM_PORT;
- если на порту уже висит НАШ актуальный сервер — просто подключаемся к нему;
- если порт занят кем-то другим — берём ближайший свободный, а не падаем.
"""

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path
from urllib.request import urlopen

HOST = "127.0.0.1"
# Порт по умолчанию. Переопределяется так: CM_PORT=8010 competitionmonitor.exe
DEFAULT_PORT = int(os.getenv("CM_PORT", "8000"))
# Фактический порт: уточняется в start(), окно берёт его через base_url()
PORT = DEFAULT_PORT


def project_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent.parent


def base_url() -> str:
    """Адрес API. Спрашивать каждый раз, а не запоминать при импорте."""
    return f"http://{HOST}:{PORT}"


def _listening(port: int) -> bool:
    """Кто-то вообще слушает порт (кто именно — неизвестно)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, port)) == 0


def _our_server(port: int) -> bool:
    """
    На порту именно наш актуальный сервер?

    По одному `/health` судить нельзя: он отвечает и у старых сборок, и у зависшего
    процесса, который держит порт. На этом уже обожглись — окно молча подключалось
    к чужому серверу, а новые вкладки отдавали 404, и выглядело это как поломка
    сборки. Поэтому проверяем маркер актуального API: поле supported_formats
    в /config появилось вместе с нынешним набором вкладок.
    """
    try:
        with urlopen(f"http://{HOST}:{port}/config", timeout=1.0) as response:
            if response.status != 200:
                return False
            data = json.loads(response.read().decode("utf-8"))
    except Exception:
        return False
    return isinstance(data, dict) and "supported_formats" in data


def _free_port(first: int, attempts: int = 20) -> int:
    for candidate in range(first, first + attempts):
        if not _listening(candidate):
            return candidate
    raise RuntimeError(
        f"Не нашёл свободный порт в диапазоне {first}–{first + attempts - 1}. "
        "Закрой лишние серверы или задай свой: CM_PORT=9000"
    )


def start() -> None:
    """Если наш сервер уже крутится — подключаемся. Иначе поднимаем свой."""
    global PORT

    if _our_server(DEFAULT_PORT):
        PORT = DEFAULT_PORT
        return

    # порт свободен — занимаем его; занят чужим — уходим на соседний
    PORT = DEFAULT_PORT if not _listening(DEFAULT_PORT) else _free_port(DEFAULT_PORT + 1)

    bundle = project_root()
    writable = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else bundle
    os.chdir(writable)
    if str(bundle) not in sys.path:
        sys.path.insert(0, str(bundle))

    chosen = PORT

    def _run():
        import uvicorn

        uvicorn.run(
            "backend.main:app",
            host=HOST,
            port=chosen,
            log_level="warning",
            reload=False,
        )

    thread = threading.Thread(target=_run, daemon=True, name="uvicorn")
    thread.start()

    # ждём не «порт открылся», а «наш сервер отвечает» — иначе можно уйти дальше
    # раньше, чем приложение реально готово принимать запросы
    for _ in range(80):
        if _our_server(chosen):
            return
        time.sleep(0.25)
    raise RuntimeError(f"Не удалось запустить сервер на порту {chosen}")
