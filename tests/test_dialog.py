"""Проверка логики диалога на «фальшивых» микрофоне и озвучке (без звука и сети)."""
from __future__ import annotations

import copy
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import main as app  # noqa: E402
from config import load_config  # noqa: E402


class StopTest(Exception):
    pass


class FakeSpeaker:
    def __init__(self) -> None:
        self.spoken: list[str] = []

    def say(self, text: str, interrupt: bool = False) -> None:
        if text:
            self.spoken.append(text)

    def wait(self, timeout: float = 30) -> None:
        pass

    def stop(self) -> None:
        pass

    def shutdown(self) -> None:
        pass


class FakeEars:
    def __init__(self, phrases: list[str]) -> None:
        self.phrases = list(phrases)
        self.paused = 0

    def start(self) -> None:
        pass

    def stop(self) -> None:
        pass

    def pause(self) -> None:
        self.paused += 1

    def resume(self) -> None:
        pass

    def listen(self, timeout: float, phrase_limit: float | None = None) -> str:
        if not self.phrases:
            raise StopTest
        return self.phrases.pop(0)


def test_config() -> dict:
    config = copy.deepcopy(load_config())
    config["assistant"]["startup_message"] = ""
    return config


class DialogTests(unittest.TestCase):
    def run_flow(self, phrases: list[str]) -> tuple[list[str], int]:
        speaker = FakeSpeaker()
        ears = FakeEars(phrases)
        with self.assertRaises(StopTest):
            app.run_assistant(test_config(), use_tray=False, speaker=speaker, ears=ears)
        return speaker.spoken, ears.paused

    def test_wake_then_command_then_exit(self) -> None:
        spoken, paused = self.run_flow(["гладос", "привет", "хватит"])
        self.assertEqual(spoken, ["Да, я вас слушаю.", "Приветствую.", "Хорошо."])
        self.assertGreater(paused, 0, "микрофон должен глушиться на время речи")

    def test_command_in_same_phrase(self) -> None:
        spoken, _ = self.run_flow(["гладос привет"])
        self.assertEqual(spoken, ["Приветствую."])

    def test_unknown_command_gets_answer(self) -> None:
        spoken, _ = self.run_flow(["гладос", "свари мне кофе"])
        self.assertEqual(spoken, ["Да, я вас слушаю.", "Не поняла команду."])

    def test_silence_returns_to_idle(self) -> None:
        spoken, _ = self.run_flow(["гладос", ""])
        self.assertEqual(spoken, ["Да, я вас слушаю."])

    def test_dialog_accepts_command_without_wake_word(self) -> None:
        spoken, _ = self.run_flow(["гладос", "как дела", "привет"])
        self.assertEqual(
            spoken, ["Да, я вас слушаю.", "Все системы в норме. Спасибо, что спросили.", "Приветствую."]
        )


if __name__ == "__main__":
    unittest.main()
