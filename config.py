"""Настройки ассистента.

Читаются из двух файлов рядом со скриптом:
  * config.json       — общие настройки, лежит в репозитории;
  * config.local.json — личные данные (API-ключи), в репозиторий не попадает.

Значения из config.local.json перекрывают config.json.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any

from paths import app_file, asset

log = logging.getLogger(__name__)

CONFIG_PATH = app_file("config.json")
LOCAL_CONFIG_PATH = app_file("config.local.json")

DEFAULTS: dict[str, Any] = {
    "assistant": {
        "name": "Гладос",
        "wake_words": [
            "гладос", "гладас", "гладус", "кладос", "глатос",
            "глаша", "глашу", "glados", "gladas",
        ],
        "exit_words": ["хватит", "отбой", "замолчи", "отстань", "пока", "до свидания"],
        "ack": "Да, я вас слушаю.",
        "startup_message": "Система запущена.",
        "followup_seconds": 8,
        "wake_timeout": 2.0,
        "command_timeout": 6.0,
        "phrase_limit": 8.0,
        "mic_name": "",
        "mic_index": None,
        "energy_threshold": None,
    },
    "voice": {
        "engine": "auto",
        "edge_voice": "ru-RU-SvetlanaNeural",
        "edge_rate": "-4%",
        "edge_pitch": "-8Hz",
        "gtts_tld": "ru",
        "pyttsx3_voice": "",
        "pyttsx3_rate": 190,
        "volume": 1.0,
    },
    "api": {
        "openweather_key": "",
        "currency_key": "",
    },
    "city": "Анапа",
    "shutdown_enabled": True,
    "apps": {
        "steam": "D:\\Program Files\\Steam\\steam.exe",
        "discord": "",
        "telegram": "",
        "vlc": "C:\\Program Files\\VideoLAN\\VLC\\vlc.exe",
    },
    "hotkeys": {
        "browser": ["win", "1"],
        "terminal": ["win", "6"],
        "zapret": ["ctrl", "f5"],
        "bot": ["ctrl", "home"],
    },
    "links": {
        "youtube": "https://www.youtube.com/",
        "avito": "https://www.avito.ru/",
        "music": "https://vk.com/music",
        "mail": "https://mail.google.com/",
        "yandex": "https://www.yandex.ru/",
        "twitch": "https://www.twitch.tv/",
        "ozon": "https://www.ozon.ru/",
        "wildberries": "https://www.wildberries.ru/",
        "rick_and_morty": "https://rickandmorty.mult-fan.tv/",
    },
    "files": {
        "theory": "teor.pdf",
        "rick_and_morty_shortcut": "RaM.lnk",
    },
}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _read_json(path: str) -> dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Не удалось прочитать %s: %s", path, exc)
        return {}


def load_config() -> dict[str, Any]:
    config = copy.deepcopy(DEFAULTS)

    # 1. конфиг из сборки (PyInstaller кладёт его в _MEIPASS),
    # 2. конфиг рядом с exe/скриптом,
    # 3. личный конфиг с ключами.
    seen: set[str] = set()
    for path in (asset("config.json"), CONFIG_PATH, LOCAL_CONFIG_PATH):
        normalized = os.path.normcase(os.path.abspath(path))
        if normalized in seen:
            continue
        seen.add(normalized)
        data = _read_json(path)
        if data:
            config = _deep_merge(config, data)
    return config


def save_default_config(path: str = CONFIG_PATH) -> None:
    """Создаёт config.json рядом с exe/скриптом, если его ещё нет."""
    if os.path.exists(path):
        return
    try:
        bundled = asset("config.json")
        if os.path.exists(bundled):
            with open(bundled, "r", encoding="utf-8") as src:
                data = json.load(src)
        else:
            data = DEFAULTS
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2)
        log.info("Создан файл настроек %s", path)
    except (OSError, json.JSONDecodeError) as exc:
        log.error("Не удалось создать %s: %s", path, exc)


def get(config: dict[str, Any], dotted: str, default: Any = None) -> Any:
    """config["voice"]["engine"] -> get(config, "voice.engine")."""
    node: Any = config
    for part in dotted.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node
