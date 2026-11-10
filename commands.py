"""Команды ассистента.

Каждая команда — набор регулярных выражений и обработчик. Срабатывает только
первое совпадение (первая команда в списке), поэтому «размут» больше не
превращается в «мут», а «стоп» — в «пауза + пробел».
"""
from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import webbrowser
from dataclasses import dataclass
from typing import Callable

import pyautogui
import requests

from config import get
from paths import asset

log = logging.getLogger(__name__)

pyautogui.useImageNotFoundException()

Reply = str | None

CURRENCY_ALIASES = {
    "доллар": "USD", "долларов": "USD", "бакс": "USD", "баксов": "USD", "usd": "USD",
    "рубль": "RUB", "рублей": "RUB", "рубля": "RUB", "руб": "RUB", "rub": "RUB",
    "евро": "EUR", "eur": "EUR",
    "гривна": "UAH", "гривен": "UAH", "гривны": "UAH", "uah": "UAH",
    "тенге": "KZT", "kzt": "KZT",
    "юань": "CNY", "юаней": "CNY", "юаня": "CNY", "cny": "CNY",
    "фунт": "GBP", "фунтов": "GBP", "gbp": "GBP",
    "лира": "TRY", "лир": "TRY", "try": "TRY",
    "йена": "JPY", "йен": "JPY", "jpy": "JPY", "yen": "JPY",
    "биткоин": "BTC", "биткоинов": "BTC", "btc": "BTC",
    "эфир": "ETH", "эфириум": "ETH", "eth": "ETH",
}

AMOUNT_RE = re.compile(
    r"(?P<amount>\d+(?:[.,]\d+)?)\s*(?P<from>[а-яa-z]{3,})?\s*(?:в|во)\s*(?P<to>[а-яa-z]{3,})"
)


@dataclass(frozen=True)
class Command:
    name: str
    patterns: tuple[re.Pattern, ...]
    handler: Callable[[str], Reply]
    help: str

    def matches(self, text: str) -> bool:
        return any(pattern.search(text) for pattern in self.patterns)


def _rx(*patterns: str) -> tuple[re.Pattern, ...]:
    return tuple(re.compile(p) for p in patterns)


class Dispatcher:
    def __init__(self, config: dict):
        self.config = config
        self._shutdown_enabled = bool(config.get("shutdown_enabled", True))
        self._links = config.get("links", {})
        self._apps = config.get("apps", {})
        self.commands: list[Command] = self._build_commands()

    # ------------------------------------------------------------------ API
    def handle(self, text: str) -> Reply:
        """Возвращает ответ или None, если команда не распознана.

        Пустая строка означает «команда выполнена, говорить нечего».
        """
        normalized = " ".join(str(text).lower().replace("ё", "е").split())
        if not normalized:
            return None
        for command in self.commands:
            if command.matches(normalized):
                log.info("Команда: %s <- %r", command.name, normalized)
                try:
                    reply = command.handler(normalized)
                except Exception:  # noqa: BLE001
                    log.exception("Команда %s упала", command.name)
                    return "Не получилось выполнить команду, подробности в логе."
                return reply if reply is not None else ""
        log.info("Команда не распознана: %r", normalized)
        return None

    def help_text(self) -> str:
        lines = ["Доступные команды:"]
        lines.extend(f"  - {command.help}" for command in self.commands)
        return "\n".join(lines)

    # ------------------------------------------------------------- команды
    def _build_commands(self) -> list[Command]:
        # Порядок важен: первое совпадение выигрывает.
        return [
            Command("help", _rx(r"\bкоманд", r"\bпомощ", r"что ты умеешь", r"что умеешь"),
                    self._help, "«команды» или «помощь» — список умений"),
            Command("weather", _rx(r"\bпогод"),
                    self._weather, "«погода» — погода в городе из конфига"),
            Command("currency", _rx(r"конверт", r"переведи", r"\bкурс\b", r"сколько стоит"),
                    self._currency, "«конвертируй 100 долларов в рубли»"),
            Command("unmute", _rx(r"размут", r"включи микро"),
                    lambda _t: self._mic_screenshot("unmute.png", "Микрофон включён."),
                    "«размут» — включить микрофон в Discord"),
            Command("mute", _rx(r"\bмут\b", r"замут", r"выключи микро"),
                    lambda _t: self._mic_screenshot("mute.png", "Микрофон выключен."),
                    "«мут» — выключить микрофон в Discord"),
            Command("system_mute", _rx(r"выключи звук", r"без звука", r"заглуши"),
                    self._system_mute, "«выключи звук» — выключить звук системы"),
            Command("volume_up", _rx(r"громче", r"прибавь звук", r"сделай громче"),
                    self._volume_up, "«громче» — прибавить громкость"),
            Command("volume_down", _rx(r"тише", r"убавь звук", r"сделай тише"),
                    self._volume_down, "«тише» — убавить громкость"),
            Command("player_pause", _rx(r"\bпауза\b", r"\bпаузу\b", r"\bстоп\b", r"останови"),
                    self._pause, "«пауза» / «стоп» — пауза в плеере"),
            Command("seek_left", _rx(r"перемотай назад", r"влево", r"\bлево\b"),
                    lambda _t: self._press("left") or "Перематываю назад.",
                    "«перемотай назад» — назад по видео"),
            Command("seek_right", _rx(r"перемотай вперед", r"вправо", r"\bправо\b"),
                    lambda _t: self._press("right") or "Перематываю вперёд.",
                    "«перемотай вперёд» — вперёд по видео"),
            Command("next_track", _rx(r"следующий", r"\bнекст\b", r"\bвперед\b"),
                    self._next_track, "«следующий» / «вперёд» — следующий трек"),
            Command("prev_track", _rx(r"предыдущий", r"\bназад\b", r"верни трек"),
                    self._prev_track, "«предыдущий» / «назад» — предыдущий трек"),
            Command("restart_gui", _rx(r"\brestart\b", r"\bрестарт\b", r"перезапусти приложение"),
                    self._restart_gui, "«рестарт» — нажать кнопку restart на экране"),
            Command("mix", _rx(r"включи микс"),
                    self._mix, "«включи микс» — запустить микс ВК"),
            Command("rick_and_morty", _rx(r"рик и морти", r"рик морти"),
                    self._rick_and_morty, "«рик и морти» — открыть сериал"),

            # Сайты
            Command("youtube", _rx(r"ютуб", r"youtube"),
                    lambda _t: self._open_link("youtube", "YouTube"), "«ютуб» — открыть YouTube"),
            Command("avito", _rx(r"авито", r"\bavito\b"),
                    lambda _t: self._open_link("avito", "Авито"), "«авито» — открыть Авито"),
            Command("music", _rx(r"музык", r"вк музыка"),
                    lambda _t: self._open_link("music", "VK Музыка"), "«музыка» — открыть VK Музыку"),
            Command("mail", _rx(r"почт"),
                    lambda _t: self._open_link("mail", "почту"), "«почта» — открыть Gmail"),
            Command("yandex", _rx(r"яндекс", r"yandex"),
                    lambda _t: self._open_link("yandex", "Яндекс"), "«яндекс» — открыть Яндекс"),
            Command("twitch", _rx(r"твич", r"twitch"),
                    lambda _t: self._open_link("twitch", "Twitch"), "«твич» — открыть Twitch"),
            Command("ozon", _rx(r"озон", r"\bozon\b"),
                    lambda _t: self._open_link("ozon", "Ozon"), "«озон» — открыть Ozon"),
            Command("wildberries", _rx(r"вайлдберриз", r"wildberries", r"\bвб\b"),
                    lambda _t: self._open_link("wildberries", "Wildberries"),
                    "«вайлдберриз» — открыть Wildberries"),

            # Приложения
            Command("steam", _rx(r"\bстим\b", r"\bsteam\b"),
                    self._steam, "«стим» — запустить Steam"),
            Command("discord", _rx(r"дискорд", r"\bдс\b", r"\bdiscord\b", r"\bds\b"),
                    self._discord, "«дискорд» — запустить Discord"),
            Command("telegram", _rx(r"телеграм", r"телега", r"\bтг\b", r"telegram"),
                    self._telegram, "«телеграм» — запустить Telegram"),
            Command("vlc", _rx(r"открой плеер", r"\bvlc\b", r"видеоплеер"),
                    self._vlc, "«открой плеер» — запустить VLC"),
            Command("browser", _rx(r"браузер"),
                    lambda _t: self._hotkey("browser") or "Открываю браузер.",
                    "«браузер» — переключиться на браузер"),
            Command("terminal", _rx(r"дебиан", r"терминал", r"\bcmd\b", r"консол"),
                    lambda _t: self._hotkey("terminal") or "Открываю терминал.",
                    "«терминал» / «дебиан» — переключиться на терминал"),
            Command("zapret", _rx(r"запрет", r"zapter", r"\bдпи\b", r"\bdpi\b"),
                    self._zapret, "«запрет» — включить обход DPI"),

            # Медиа-хоткеи
            Command("space", _rx(r"пробел"),
                    lambda _t: self._press("space") or "Пробел.", "«пробел» — нажать пробел"),
            Command("player_sound", _rx(r"\bзвук\b"),
                    self._player_sound, "«звук» — выключить/включить звук в плеере"),
            Command("fullscreen", _rx(r"разверни"),
                    lambda _t: self._press("f") or "Разворачиваю.", "«разверни» — полный экран"),
            Command("minimize_all", _rx(r"сверни все"),
                    self._minimize_all, "«сверни всё» — свернуть все окна"),
            Command("minimize", _rx(r"сверни"),
                    self._minimize, "«сверни» — свернуть окно"),
            Command("up", _rx(r"\bвверх\b"),
                    lambda _t: self._press("up") or "Вверх.", "«вверх» — стрелка вверх"),
            Command("down", _rx(r"\bвниз\b"),
                    lambda _t: self._press("down") or "Вниз.", "«вниз» — стрелка вниз"),
            Command("close_window", _rx(r"закрой"),
                    self._close_window, "«закрой» — закрыть активное окно"),
            Command("task_manager", _rx(r"диспетчер задач", r"таск менеджер"),
                    self._task_manager, "«диспетчер задач» — открыть диспетчер"),
            Command("bot", _rx(r"запусти бота", r"запусти ботинка", r"\bбот\b", r"ботинок"),
                    lambda _t: self._hotkey("bot") or "Запускаю бота.",
                    "«бот» — запустить бота"),

            # Системные
            Command("shutdown", _rx(r"выключи пк", r"выключи компьютер", r"заверши работу"),
                    self._shutdown, "«выключи пк» — выключить компьютер"),
            Command("block_shutdown", _rx(r"заблокируй выключение"),
                    self._block_shutdown, "«заблокируй выключение» — запретить выключение"),
            Command("unblock_shutdown", _rx(r"разблокируй выключение"),
                    self._unblock_shutdown, "«разблокируй выключение» — разрешить выключение"),

            # Прочее
            Command("click", _rx(r"кликни", r"\bклик\b", r"\bclick\b"),
                    self._click, "«кликни» — клик левой кнопкой"),
            Command("voice_input", _rx(r"голосовой ввод"),
                    self._voice_input, "«голосовой ввод» — включить ввод голосом Windows"),
            Command("backspace", _rx(r"\bсотри\b"),
                    lambda _t: self._press("backspace") or "Стёрла.",
                    "«сотри» — удалить символ"),
            Command("theory", _rx(r"справочные материалы", r"\bтеорию\b"),
                    self._theory, "«справочные материалы» — открыть teor.pdf"),
            Command("greeting", _rx(r"^привет", r"\bздравствуй", r"^хай\b", r"\bhello\b"),
                    lambda _t: "Приветствую.", "«привет» — поздороваться"),
            Command("how_are_you", _rx(r"как дела", r"как ты"),
                    lambda _t: "Все системы в норме. Спасибо, что спросили.",
                    "«как дела» — состояние систем"),
        ]

    # ------------------------------------------------------------ обработчики
    def _help(self, _text: str) -> Reply:
        text = self.help_text()
        print(text)
        log.info("\n%s", text)
        return (
            "Я умею открывать сайты и приложения, управлять музыкой и громкостью, "
            "показывать погоду, конвертировать валюты, управлять микрофоном в Discord "
            "и выключать компьютер. Полный список команд я записала в лог."
        )

    def _weather(self, _text: str) -> Reply:
        key = get(self.config, "api.openweather_key", "")
        city = self.config.get("city", "Анапа")
        if not key:
            return "В конфиге не задан ключ OpenWeather."

        params = {"q": city, "appid": key, "units": "metric", "lang": "ru"}
        try:
            response = requests.get(
                "https://api.openweathermap.org/data/2.5/weather",
                params=params,
                timeout=10,
            )
        except requests.RequestException as exc:
            log.warning("Погода: %s", exc)
            return "Нет связи с сервером погоды."

        if response.status_code == 401:
            return "OpenWeather не принял API-ключ."
        if response.status_code == 404:
            return f"Город {city} не найден."
        if response.status_code != 200:
            return f"Сервер погоды вернул ошибку {response.status_code}."

        try:
            data = response.json()
            temperature = round(data["main"]["temp"])
            feels_like = round(data["main"]["feels_like"])
            description = data["weather"][0]["description"]
        except (KeyError, IndexError, ValueError):
            log.exception("Неожиданный ответ погоды: %s", response.text[:200])
            return "Не смогла разобрать ответ сервера погоды."

        return (
            f"В {city} {temperature} градусов, {description}. "
            f"Ощущается как {feels_like}."
        )

    def _currency(self, text: str) -> Reply:
        match = AMOUNT_RE.search(text)
        if not match:
            return "Скажите так: конвертируй сто долларов в рубли."

        raw_amount = match.group("amount").replace(",", ".")
        try:
            amount = float(raw_amount)
        except ValueError:
            return "Не поняла сумму."

        from_code = self._currency_code(match.group("from")) or "USD"
        to_code = self._currency_code(match.group("to"))
        if not to_code:
            return "Не поняла, в какую валюту переводить."

        try:
            response = requests.get(
                f"https://open.er-api.com/v6/latest/{from_code}", timeout=10
            )
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            log.warning("Курсы валют: %s", exc)
            return "Не удалось получить курсы валют."

        rates = data.get("rates") or {}
        if to_code not in rates:
            return f"Курс {from_code} к {to_code} не найден."

        converted = amount * float(rates[to_code])
        return f"{amount:g} {from_code} — это {converted:.2f} {to_code}."

    @staticmethod
    def _currency_code(word: str | None) -> str | None:
        if not word:
            return None
        word = word.lower()
        if word in CURRENCY_ALIASES:
            return CURRENCY_ALIASES[word]
        for alias, code in CURRENCY_ALIASES.items():
            if len(alias) >= 4 and word.startswith(alias):
                return code
        if re.fullmatch(r"[a-z]{3}", word):
            return word.upper()
        return None

    def _mic_screenshot(self, filename: str, reply: str) -> Reply:
        if self._click_image(filename, confidence=0.9):
            return reply
        return f"Не вижу на экране кнопку {filename}."

    def _system_mute(self, _text: str) -> Reply:
        return self._press("volumemute") or "Звук переключён."

    def _volume_up(self, _text: str) -> Reply:
        return self._press("volumeup", presses=2) or "Громче."

    def _volume_down(self, _text: str) -> Reply:
        return self._press("volumedown", presses=2) or "Тише."

    def _pause(self, _text: str) -> Reply:
        return self._press("playpause") or "Пауза."

    def _next_track(self, _text: str) -> Reply:
        return self._press("nexttrack") or "Следующий трек."

    def _prev_track(self, _text: str) -> Reply:
        return self._press("prevtrack") or "Предыдущий трек."

    def _player_sound(self, _text: str) -> Reply:
        return self._press("m") or "Переключила звук в плеере."

    def _minimize_all(self, _text: str) -> Reply:
        pyautogui.hotkey("win", "m")
        return "Свернула все окна."

    def _minimize(self, _text: str) -> Reply:
        pyautogui.hotkey("win", "down")
        return "Свернула окно."

    def _close_window(self, _text: str) -> Reply:
        pyautogui.hotkey("alt", "f4")
        return None

    def _task_manager(self, _text: str) -> Reply:
        pyautogui.hotkey("ctrl", "shift", "esc")
        return "Открываю диспетчер задач."

    def _voice_input(self, _text: str) -> Reply:
        pyautogui.hotkey("win", "h")
        return "Голосовой ввод включён."

    def _click(self, _text: str) -> Reply:
        pyautogui.click()
        return None

    def _restart_gui(self, _text: str) -> Reply:
        if self._click_image("restart.png", confidence=0.9):
            return None
        return "Не вижу кнопку перезапуска на экране."

    def _mix(self, _text: str) -> Reply:
        if self._click_image("mix.png", confidence=0.95, delay=1.0):
            return None
        return "Не вижу кнопку микса на экране."

    def _zapret(self, _text: str) -> Reply:
        return self._hotkey("zapret") or "Включила обход DPI."

    def _rick_and_morty(self, _text: str) -> Reply:
        self._open_link("rick_and_morty", "Рика и Морти")
        shortcut = get(self.config, "files.rick_and_morty_shortcut", "")
        if shortcut:
            path = asset(shortcut)
            if os.path.exists(path):
                try:
                    os.startfile(path)  # type: ignore[attr-defined]
                except OSError:
                    log.exception("Не удалось открыть %s", path)
        return "Включаю Рика и Морти."

    def _theory(self, _text: str) -> Reply:
        name = get(self.config, "files.theory", "teor.pdf")
        path = asset(name)
        if not os.path.exists(path):
            return "Файл справочных материалов не найден."
        try:
            os.startfile(path)  # type: ignore[attr-defined]
        except OSError:
            log.exception("Не удалось открыть %s", path)
            return "Не удалось открыть справочные материалы."
        return "Открываю справочные материалы."

    def _shutdown(self, _text: str) -> Reply:
        if not self._shutdown_enabled:
            return "Функция выключения заблокирована."
        threading.Timer(4.0, self._do_shutdown).start()
        return "Выключаю компьютер."

    def _do_shutdown(self) -> None:
        log.info("Выключение компьютера")
        subprocess.Popen("shutdown /s /t 2", shell=True)

    def _block_shutdown(self, _text: str) -> Reply:
        self._shutdown_enabled = False
        return "Функция выключения заблокирована."

    def _unblock_shutdown(self, _text: str) -> Reply:
        self._shutdown_enabled = True
        return "Функция выключения разблокирована."

    def _steam(self, _text: str) -> Reply:
        path = get(self.config, "apps.steam", "")
        if path and os.path.exists(path):
            return self._launch(path, "Steam")
        try:
            os.startfile("steam://open/main")  # type: ignore[attr-defined]
            return "Запускаю Steam."
        except OSError:
            log.exception("Steam не найден")
            return "Не нашла Steam. Проверьте путь в конфиге."

    def _discord(self, _text: str) -> Reply:
        path = self._find_discord()
        if not path:
            return "Не нашла Discord. Проверьте путь в конфиге."
        return self._launch(path, "Discord")

    def _telegram(self, _text: str) -> Reply:
        path = get(self.config, "apps.telegram", "")
        if not path or not os.path.exists(path):
            path = os.path.join(
                os.environ.get("APPDATA", ""), "Telegram Desktop", "Telegram.exe"
            )
        if os.path.exists(path):
            return self._launch(path, "Telegram")
        return "Не нашла Telegram. Проверьте путь в конфиге."

    def _vlc(self, _text: str) -> Reply:
        path = get(self.config, "apps.vlc", "")
        if path and os.path.exists(path):
            return self._launch(path, "VLC")
        return "Не нашла VLC. Проверьте путь в конфиге."

    # -------------------------------------------------------------- утилиты
    def _find_discord(self) -> str:
        configured = get(self.config, "apps.discord", "")
        if configured and os.path.exists(configured):
            return configured

        base = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Discord")
        if os.path.isdir(base):
            def version_key(name: str) -> list[int]:
                return [int(p) if p.isdigit() else 0 for p in re.split(r"[.-]", name)]

            folders = sorted(
                (f for f in os.listdir(base) if f.startswith("app-")),
                key=version_key,
                reverse=True,
            )
            for folder in folders:
                exe = os.path.join(base, folder, "Discord.exe")
                if os.path.exists(exe):
                    return exe
        return ""

    def _launch(self, path: str, label: str) -> Reply:
        try:
            subprocess.Popen([path], close_fds=True)
        except OSError:
            log.exception("Не удалось запустить %s", path)
            return f"Не удалось запустить {label}."
        return f"Запускаю {label}."

    def _open_link(self, key: str, label: str) -> Reply:
        url = self._links.get(key)
        if not url:
            return "Ссылка не настроена."
        webbrowser.open_new_tab(url)
        return f"Открываю {label}."

    def _press(self, key: str, presses: int = 1) -> Reply:
        try:
            pyautogui.press(key, presses=presses, interval=0.05)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось нажать %s", key)
            return "Не получилось нажать клавишу."
        return None

    def _hotkey(self, key: str) -> Reply:
        keys = get(self.config, f"hotkeys.{key}", [])
        if not keys:
            log.warning("Хоткей %s не настроен", key)
            return "Хоткей не настроен."
        try:
            pyautogui.hotkey(*keys)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось нажать %s", keys)
            return "Не получилось нажать хоткей."
        return None

    def _click_image(self, filename: str, confidence: float = 0.9, delay: float = 0.0) -> bool:
        path = asset(filename)
        if not os.path.exists(path):
            log.error("Нет файла изображения %s", path)
            return False
        try:
            location = pyautogui.locateOnScreen(path, grayscale=False, confidence=confidence)
        except pyautogui.ImageNotFoundException:
            log.info("Изображение %s не найдено на экране", filename)
            return False
        except Exception:  # noqa: BLE001
            log.exception("Ошибка поиска изображения %s", filename)
            return False

        if not location:
            log.info("Изображение %s не найдено на экране", filename)
            return False

        previous = pyautogui.position()
        try:
            center = pyautogui.center(location)
            pyautogui.moveTo(center, duration=0.2)
            if delay:
                time.sleep(delay)
            pyautogui.click()
            if delay:
                time.sleep(delay)
            pyautogui.moveTo(previous)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка клика по %s", filename)
            return False
        log.info("Клик по %s в %s", filename, location)
        return True
