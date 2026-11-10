"""Быстрые тесты без микрофона и без звука.

Запуск:  python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from commands import Dispatcher  # noqa: E402
from config import load_config  # noqa: E402
from wake import is_exit_phrase, normalize, strip_wake_word  # noqa: E402

CONFIG = load_config()
WAKE = CONFIG["assistant"]["wake_words"]
EXIT = CONFIG["assistant"]["exit_words"]


class WakeWordTests(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(normalize("Гладос, КАКАЯ погода?!"), "гладос какая погода")
        self.assertEqual(normalize("ёлка"), "елка")

    def test_wake_and_command_in_one_phrase(self) -> None:
        found, command = strip_wake_word("Гладос, какая погода", WAKE)
        self.assertTrue(found)
        self.assertEqual(command, "какая погода")

    def test_wake_word_variants(self) -> None:
        for phrase in ("Глаша, включи музыку", "Кладос погода", "Гладосс, стоп"):
            found, command = strip_wake_word(phrase, WAKE)
            self.assertTrue(found, phrase)
            self.assertTrue(command, phrase)

    def test_wake_word_split_by_recognizer(self) -> None:
        found, command = strip_wake_word("гло дос погода", WAKE)
        self.assertTrue(found)
        self.assertEqual(command, "погода")

    def test_plain_phrase_has_no_wake_word(self) -> None:
        found, _ = strip_wake_word("какая сегодня погода", WAKE)
        self.assertFalse(found)

    def test_exit_phrases(self) -> None:
        self.assertTrue(is_exit_phrase("ладно, хватит", EXIT))
        self.assertFalse(is_exit_phrase("включи музыку", EXIT))


class DispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dispatcher = Dispatcher(CONFIG)

    def match(self, text: str) -> str | None:
        normalized = " ".join(text.lower().replace("ё", "е").split())
        for command in self.dispatcher.commands:
            if command.matches(normalized):
                return command.name
        return None

    def test_no_duplicate_names(self) -> None:
        names = [command.name for command in self.dispatcher.commands]
        self.assertEqual(len(names), len(set(names)))

    def test_unmute_is_not_mute(self) -> None:
        self.assertEqual(self.match("размут"), "unmute")
        self.assertEqual(self.match("включи микрофон"), "unmute")
        self.assertEqual(self.match("мут"), "mute")
        self.assertEqual(self.match("выключи микро"), "mute")

    def test_stop_is_only_pause(self) -> None:
        self.assertEqual(self.match("стоп"), "player_pause")
        self.assertEqual(self.match("пробел"), "space")

    def test_sound_commands(self) -> None:
        self.assertEqual(self.match("выключи звук"), "system_mute")
        self.assertEqual(self.match("прибавь звук"), "volume_up")
        self.assertEqual(self.match("убавь звук"), "volume_down")
        self.assertEqual(self.match("звук"), "player_sound")

    def test_seek_before_track(self) -> None:
        self.assertEqual(self.match("перемотай назад"), "seek_left")
        self.assertEqual(self.match("перемотай вперед"), "seek_right")
        self.assertEqual(self.match("назад"), "prev_track")
        self.assertEqual(self.match("вперед"), "next_track")

    def test_minimize_variants(self) -> None:
        self.assertEqual(self.match("сверни все"), "minimize_all")
        self.assertEqual(self.match("сверни"), "minimize")

    def test_shutdown_guard(self) -> None:
        self.assertEqual(self.match("заблокируй выключение"), "block_shutdown")
        self.assertEqual(self.match("разблокируй выключение"), "unblock_shutdown")

    def test_currency_codes(self) -> None:
        self.assertEqual(Dispatcher._currency_code("долларов"), "USD")
        self.assertEqual(Dispatcher._currency_code("рублях"), "RUB")
        self.assertEqual(Dispatcher._currency_code("usd"), "USD")
        self.assertIsNone(Dispatcher._currency_code("непонятно"))


if __name__ == "__main__":
    unittest.main()
