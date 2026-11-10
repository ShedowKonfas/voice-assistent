"""Логирование в файл: при запуске через .pyw/exe консоли нет, и без лога
невозможно понять, почему ассистент молчит.
"""
from __future__ import annotations

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler

from paths import app_file

LOG_PATH = app_file(os.path.join("logs", "glados.log"))
_FORMAT = "%(asctime)s %(levelname)-8s [%(threadName)s] %(name)s: %(message)s"


def setup_logging(debug: bool = False) -> None:
    """Настраивает лог в logs/glados.log (+ консоль в режиме отладки)."""
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)

    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    formatter = logging.Formatter(_FORMAT)

    file_handler = RotatingFileHandler(
        LOG_PATH, maxBytes=1_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if debug or (sys.stderr is not None and sys.stderr.isatty()):
        console = logging.StreamHandler()
        console.setFormatter(formatter)
        root.addHandler(console)

    sys.excepthook = _log_uncaught
    threading.excepthook = _log_thread_uncaught

    # сторонние библиотеки любят спамить в DEBUG
    for noisy in ("gtts", "urllib3", "aiohttp", "asyncio", "comtypes", "PIL", "pygame"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    logging.getLogger("comtypes.client._code_cache").setLevel(logging.WARNING)


def _log_uncaught(exc_type, exc_value, exc_tb) -> None:  # noqa: ANN001
    logging.getLogger("uncaught").critical(
        "Необработанная ошибка", exc_info=(exc_type, exc_value, exc_tb)
    )


def _log_thread_uncaught(args) -> None:  # noqa: ANN001
    logging.getLogger("uncaught").critical(
        "Необработанная ошибка в потоке %s",
        getattr(args, "thread", "?"),
        exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
    )
