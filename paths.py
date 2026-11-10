"""Пути к ресурсам проекта.

Работает и из исходников, и из PyInstaller-сборки (--onefile).
"""
from __future__ import annotations

import os
import sys


def _base_dir() -> str:
    if getattr(sys, "frozen", False):
        # В onefile-сборке ресурсы распакованы во временную папку _MEIPASS
        return getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


def _app_dir() -> str:
    """Папка для изменяемых файлов (config.json, logs) — рядом с exe/скриптом."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return BASE_DIR


BASE_DIR = _base_dir()
APP_DIR = _app_dir()


def asset(name: str) -> str:
    """Полный путь к файлу-ресурсу (картинки, pdf) из папки проекта."""
    return os.path.join(BASE_DIR, name)


def app_file(name: str) -> str:
    """Полный путь к изменяемому файлу (конфиг, логи)."""
    return os.path.join(APP_DIR, name)
