"""Распознавание кодового слова и очистка фраз.

Google редко слышит «Гладос» идеально, поэтому дополнительно сравниваем
слова нечётко (difflib) — иначе ассистент «не отвечает».
"""
from __future__ import annotations

import difflib
import re

_NON_WORD = re.compile(r"[^а-яa-z0-9]+")
_SPACES = re.compile(r"\s+")

# Короткие слова, похожие на кодовое (для нечёткого сравнения с ограничением)
_MIN_FUZZY_LEN = 5
_FUZZY_CUTOFF = 0.78


def normalize(text: str) -> str:
    """Нижний регистр, ё -> е, пунктуация -> пробелы."""
    text = (text or "").lower().replace("ё", "е")
    return _SPACES.sub(" ", _NON_WORD.sub(" ", text)).strip()


def _token_matches(token: str, wake_word: str) -> bool:
    if token == wake_word:
        return True
    if len(wake_word) >= _MIN_FUZZY_LEN and token.startswith(wake_word):
        return True
    if len(token) >= _MIN_FUZZY_LEN and wake_word.startswith(token):
        return True
    if len(token) >= _MIN_FUZZY_LEN and len(wake_word) >= _MIN_FUZZY_LEN:
        return difflib.SequenceMatcher(None, token, wake_word).ratio() >= _FUZZY_CUTOFF
    return False


def strip_wake_word(text: str, wake_words: list[str]) -> tuple[bool, str]:
    """Возвращает (найдено_ли_кодовое_слово, текст_команды_после_него)."""
    normalized = normalize(text)
    if not normalized:
        return False, ""

    tokens = normalized.split()
    wake_words = [normalize(w) for w in wake_words if w.strip()]

    for index, token in enumerate(tokens):
        # «гло дос» — Google иногда делит слово на два
        candidates = [token]
        if index + 1 < len(tokens):
            candidates.append(token + tokens[index + 1])
        for candidate in candidates:
            if any(_token_matches(candidate, wake) for wake in wake_words):
                consumed = 1 if candidate == token else 2
                return True, " ".join(tokens[index + consumed:]).strip()

    return False, normalized


def is_exit_phrase(text: str, exit_words: list[str]) -> bool:
    normalized = normalize(text)
    if not normalized:
        return False
    for word in exit_words:
        word = normalize(word)
        if not word:
            continue
        # по границам слова, иначе «покажи» сработает как «пока»
        pattern = rf"(?<![а-яa-z]){re.escape(word)}(?![а-яa-z])"
        if re.search(pattern, normalized):
            return True
    return False
