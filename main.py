"""GLaDOS — голосовой ассистент.

Запуск:
    python main.py                 — обычный режим (трей + голос)
    python main.py --debug         — то же, но с консолью и подробным логом
    python main.py --test-tts      — проверить озвучку
    python main.py --cmd "погода"  — выполнить команду текстом (без микрофона)
    python main.py --list-mics     — список микрофонов
"""
from __future__ import annotations

import argparse
import logging
import sys
import threading
import time

from audio import Ears, list_microphones
from commands import Dispatcher
from config import load_config, save_default_config
from logger import LOG_PATH, setup_logging
from paths import asset
from tts import Speaker, describe_engines
from wake import is_exit_phrase, strip_wake_word

log = logging.getLogger("glados")

DEFAULT_TEST_PHRASE = "Привет. Я Гладос. Система работает, я вас слушаю."


# --------------------------------------------------------------------- утилиты
def speak(ears: Ears | None, speaker: Speaker, text: str | None) -> None:
    """Сказать фразу, не давая микрофону услышать самого себя."""
    if not text:
        return
    if ears is not None:
        ears.pause()
    try:
        speaker.say(text)
        speaker.wait(timeout=90)
    finally:
        if ears is not None:
            ears.resume()
            time.sleep(0.15)


def _start_tray(stop_event: threading.Event, speaker: Speaker) -> None:
    try:
        import pystray
        from PIL import Image
        from pystray import Menu, MenuItem
    except ImportError:
        log.warning("pystray/Pillow не установлены — работаю без иконки в трее")
        return

    icon_path = asset("micro.png")
    try:
        image = Image.open(icon_path)
    except OSError:
        log.warning("Не нашла иконку %s — работаю без трея", icon_path)
        return

    def quit_app(icon, _item) -> None:  # noqa: ANN001
        log.info("Выход из приложения")
        stop_event.set()
        speaker.stop()
        icon.stop()

    def test_voice(_icon, _item) -> None:  # noqa: ANN001
        speaker.say("Проверка связи. Я Гладос.")

    def open_log(_icon, _item) -> None:  # noqa: ANN001
        import os

        os.startfile(LOG_PATH)  # type: ignore[attr-defined]

    menu = Menu(
        MenuItem("Тест голоса", test_voice),
        MenuItem("Открыть лог", open_log),
        MenuItem("Выход", quit_app),
    )
    icon = pystray.Icon("Glados", image, "GLaDOS", menu)
    threading.Thread(target=icon.run, name="tray", daemon=True).start()


# ------------------------------------------------------------------ сценарии
def run_assistant(config: dict, use_tray: bool = True,
                  speaker: Speaker | None = None, ears: Ears | None = None) -> int:
    assistant = config.get("assistant", {})
    wake_words = assistant.get("wake_words", [])
    exit_words = assistant.get("exit_words", [])
    ack = assistant.get("ack", "Да, я вас слушаю.")
    followup = float(assistant.get("followup_seconds", 8))
    wake_timeout = float(assistant.get("wake_timeout", 2.0))
    command_timeout = float(assistant.get("command_timeout", 6.0))

    speaker = speaker or Speaker(config).start()
    dispatcher = Dispatcher(config)
    injected_ears = ears is not None
    ears = ears or Ears(config)
    if not injected_ears:
        try:
            ears.start()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось открыть микрофон")
            speak(None, speaker, "Не удалось открыть микрофон. Смотрите лог.")
            speaker.shutdown()
            return 2

    stop_event = threading.Event()
    if use_tray:
        _start_tray(stop_event, speaker)

    log.info("Гладас запущена. %s", describe_engines(config))
    speak(ears, speaker, assistant.get("startup_message"))

    state = "idle"
    try:
        while not stop_event.is_set():
            if state == "idle":
                heard = ears.listen(timeout=wake_timeout, phrase_limit=5)
                if not heard:
                    continue

                found, command_text = strip_wake_word(heard, wake_words)
                if not found:
                    log.debug("Не кодовое слово, пропускаю: %r", heard)
                    continue

                if command_text:
                    reply = dispatcher.handle(command_text)
                    if reply is None:
                        speak(ears, speaker, "Не поняла команду.")
                    elif reply:
                        speak(ears, speaker, reply)
                else:
                    speak(ears, speaker, ack)
                state = "dialog"
                continue

            # Диалог: следующие команды можно говорить без кодового слова
            heard = ears.listen(timeout=followup, phrase_limit=10)
            if not heard:
                log.info("Тишина — возвращаюсь в ожидание кодового слова")
                state = "idle"
                continue

            if is_exit_phrase(heard, exit_words):
                speak(ears, speaker, "Хорошо.")
                state = "idle"
                continue

            found, command_text = strip_wake_word(heard, wake_words)
            text = command_text if found else heard
            if not text:
                continue

            reply = dispatcher.handle(text)
            if reply is None and len(text.split()) >= 2:
                speak(ears, speaker, "Не поняла команду.")
            elif reply:
                speak(ears, speaker, reply)
    except KeyboardInterrupt:
        log.info("Остановлено с клавиатуры")
    finally:
        speaker.stop()
        ears.stop()
        speaker.shutdown()
        log.info("Гладас выключена")
    return 0


def run_cli_command(config: dict, text: str) -> int:
    dispatcher = Dispatcher(config)
    reply = dispatcher.handle(text)
    if reply is None:
        print("(команда не распознана)")
        return 1
    print(reply if reply else "(выполнено, говорить нечего)")
    return 0


def run_tts_test(config: dict, text: str) -> int:
    speaker = Speaker(config).start()
    print(f"Движки: {describe_engines(config)}")
    speaker.say(text)
    speaker.wait(timeout=60)
    speaker.shutdown()
    print(f"Если звука не было, смотрите лог: {LOG_PATH}")
    return 0


# ---------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="GLaDOS — голосовой ассистент")
    parser.add_argument("--debug", action="store_true", help="консоль и подробный лог")
    parser.add_argument("--no-tray", action="store_true", help="не создавать иконку в трее")
    parser.add_argument("--test-tts", nargs="?", const=DEFAULT_TEST_PHRASE,
                        metavar="ТЕКСТ", help="проверить озвучку")
    parser.add_argument("--say", metavar="ТЕКСТ", help="синоним --test-tts")
    parser.add_argument("--cmd", metavar="ТЕКСТ", help="выполнить команду из текста")
    parser.add_argument("--list-mics", action="store_true", help="список микрофонов")
    parser.add_argument("--list-voices", action="store_true", help="список голосов SAPI")
    parser.add_argument("--list-commands", action="store_true", help="список команд")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging(debug=args.debug)

    from paths import APP_DIR

    log.info("Запуск из %s (python %s)", APP_DIR, sys.version.split()[0])
    save_default_config()
    config = load_config()
    log.info("Настройки загружены, город: %s", config.get("city"))

    if args.list_mics:
        for index, name in enumerate(list_microphones()):
            print(f"{index}: {name}")
        return 0

    if args.list_voices:
        try:
            import pyttsx3

            engine = pyttsx3.init()
            for voice in engine.getProperty("voices") or []:
                print(f"{voice.name} | {voice.languages} | {voice.id}")
        except Exception as exc:  # noqa: BLE001
            print(f"pyttsx3 недоступен: {exc}")
        print(f"\nТекущая настройка: {describe_engines(config)}")
        return 0

    if args.list_commands:
        print(Dispatcher(config).help_text())
        return 0

    if args.cmd:
        return run_cli_command(config, args.cmd)

    if args.test_tts is not None:
        return run_tts_test(config, args.test_tts)

    if args.say:
        return run_tts_test(config, args.say)

    return run_assistant(config, use_tray=not args.no_tray)


if __name__ == "__main__":
    raise SystemExit(main())
