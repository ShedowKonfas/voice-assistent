"""Слух: один постоянный поток микрофона вместо переоткрытия на каждом витке.

Пока ассистент говорит, поток ставится на паузу — иначе он слышит сам себя.
"""
from __future__ import annotations

import logging
import time

import speech_recognition as sr

log = logging.getLogger(__name__)


class Ears:
    def __init__(self, config: dict):
        assistant = config.get("assistant", {})
        self._mic_name = str(assistant.get("mic_name") or "").strip()
        self._mic_index = assistant.get("mic_index")
        self._phrase_limit = float(assistant.get("phrase_limit", 8.0))

        self._recognizer = sr.Recognizer()
        self._recognizer.dynamic_energy_threshold = True
        self._recognizer.pause_threshold = 0.8
        self._recognizer.non_speaking_duration = 0.5

        threshold = assistant.get("energy_threshold")
        if threshold:
            self._recognizer.energy_threshold = float(threshold)
            self._recognizer.dynamic_energy_threshold = False

        self._microphone: sr.Microphone | None = None
        self._source: sr.AudioSource | None = None

    # ------------------------------------------------------------------ setup
    def start(self) -> None:
        index = self._resolve_device()
        self._microphone = sr.Microphone(device_index=index)
        self._source = self._microphone.__enter__()
        log.info("Микрофон: %s", self._device_name(index))

        # Калибровка шума — один раз, а не на каждой итерации
        try:
            self._recognizer.adjust_for_ambient_noise(self._source, duration=0.8)
            log.info("Порог энергии: %.0f", self._recognizer.energy_threshold)
        except Exception:  # noqa: BLE001
            log.exception("Не удалось откалибровать микрофон")

    def stop(self) -> None:
        if self._microphone is not None:
            try:
                self._microphone.__exit__(None, None, None)
            except Exception:  # noqa: BLE001
                log.exception("Ошибка при закрытии микрофона")
            finally:
                self._microphone = None
                self._source = None

    def _resolve_device(self) -> int | None:
        if self._mic_index is not None:
            return int(self._mic_index)
        if not self._mic_name:
            return None
        try:
            names = sr.Microphone.list_microphone_names()
        except Exception:  # noqa: BLE001
            log.exception("Не удалось получить список микрофонов")
            return None

        needle = self._mic_name.lower()
        for index, name in enumerate(names):
            if needle in (name or "").lower():
                log.info("Выбран микрофон по имени: %s (%s)", name, index)
                return index
        log.warning(
            "Микрофон с именем %r не найден, использую устройство по умолчанию", self._mic_name
        )
        return None

    @staticmethod
    def _device_name(index: int | None) -> str:
        try:
            names = sr.Microphone.list_microphone_names()
            if index is None:
                return f"по умолчанию (индекс 1): {names[1]}"
            return f"{index}: {names[index]}"
        except Exception:  # noqa: BLE001
            return "неизвестно"

    # ----------------------------------------------------------------- pause
    def _pyaudio_stream(self):  # noqa: ANN202
        stream = getattr(self._source, "stream", None)
        return getattr(stream, "pyaudio_stream", stream)

    def pause(self) -> None:
        """Глушит микрофон на время речи ассистента."""
        stream = self._pyaudio_stream()
        if stream is None:
            return
        try:
            if stream.is_active():
                stream.stop_stream()
        except Exception:  # noqa: BLE001
            log.debug("Не удалось приостановить поток микрофона", exc_info=True)

    def resume(self) -> None:
        stream = self._pyaudio_stream()
        if stream is None:
            return
        try:
            if not stream.is_active():
                stream.start_stream()
            # выбрасываем звук, накопившийся за время паузы
            self._drain(stream)
        except Exception:  # noqa: BLE001
            log.debug("Не удалось возобновить поток микрофона", exc_info=True)

    @staticmethod
    def _drain(stream) -> None:  # noqa: ANN001
        try:
            while stream.get_read_available() > 0:
                stream.read(stream.get_read_available(), exception_on_overflow=False)
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------- listening
    def listen(self, timeout: float, phrase_limit: float | None = None) -> str:
        """Слушает фразу. Возвращает распознанный текст ('' если тишина/ошибка)."""
        if self._source is None:
            return ""

        try:
            audio = self._recognizer.listen(
                self._source,
                timeout=timeout,
                phrase_time_limit=phrase_limit or self._phrase_limit,
            )
        except sr.WaitTimeoutError:
            return ""
        except Exception:  # noqa: BLE001
            log.exception("Ошибка записи с микрофона")
            time.sleep(0.3)
            return ""

        try:
            text = self._recognizer.recognize_google(audio, language="ru-RU")
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            log.warning("Google-распознавание недоступно: %s", exc)
            return ""
        except Exception:  # noqa: BLE001
            log.exception("Ошибка распознавания")
            return ""

        log.info("Услышал: %s", text)
        return text


def list_microphones() -> list[str]:
    try:
        return sr.Microphone.list_microphone_names()
    except Exception:  # noqa: BLE001
        log.exception("Не удалось получить список микрофонов")
        return []
