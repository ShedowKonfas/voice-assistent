"""Озвучка ответов.

Почему так сложно: в системе может не быть русского голоса SAPI (именно из-за
этого pyttsx3 выдаёт пустой файл), а edge-tts может блокироваться провайдером.
Поэтому движки идут цепочкой: edge-tts -> gTTS -> pyttsx3 (если есть ru-голос).

Озвучка идёт в отдельном потоке, чтобы не блокировать прослушивание микрофона.
"""
from __future__ import annotations

import asyncio
import logging
import os
import queue
import tempfile
import threading
import time

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from config import get  # noqa: E402

log = logging.getLogger(__name__)

_ENGINE_COOLDOWN = 300.0  # сколько секунд не пробовать движок после ошибки
_EDGE_TIMEOUT = 5.0       # edge-tts может «висеть» при блокировке провайдера


class TTSError(Exception):
    """Движок не смог озвучить текст."""


class Speaker:
    """Потокобезопасная озвучка с очередью и прерыванием."""

    def __init__(self, config: dict):
        self._voice = config.get("voice", {})
        self._volume = float(self._voice.get("volume", 1.0))

        order = str(self._voice.get("engine", "auto")).lower()
        full_chain = ["edge", "gtts", "pyttsx3"]
        if order in full_chain:
            self._engines = [order] + [e for e in full_chain if e != order]
        else:
            self._engines = full_chain

        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._pending = 0
        self._speaking = threading.Event()
        self._stop_event = threading.Event()
        self._shutdown = threading.Event()
        self._failed_at: dict[str, float] = {}
        self._pyttsx3_engine = None
        self._mixer_ready = False
        self._tmp_index = 0
        self._thread: threading.Thread | None = None

    # ------------------------------------------------------------------ API
    def start(self) -> "Speaker":
        if self._thread and self._thread.is_alive():
            return self
        self._ensure_mixer()
        self._thread = threading.Thread(target=self._worker, name="tts", daemon=True)
        self._thread.start()
        log.info("Озвучка запущена, порядок движков: %s", ", ".join(self._engines))
        return self

    def say(self, text: str, interrupt: bool = False) -> None:
        """Ставит текст в очередь. interrupt=True обрывает текущую фразу."""
        text = (text or "").strip()
        if not text:
            return
        if interrupt:
            self.stop()
        with self._lock:
            self._pending += 1
        log.info("Скажу: %s", text)
        self._queue.put(text)

    def stop(self) -> None:
        """Прерывает текущую фразу и очищает очередь."""
        self._stop_event.set()
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break
            else:
                with self._lock:
                    self._pending = max(0, self._pending - 1)
        if self._mixer_ready:
            try:
                import pygame

                if pygame.mixer.get_init():
                    pygame.mixer.music.fadeout(120)
            except Exception:  # noqa: BLE001
                pass

    def wait(self, timeout: float = 30.0) -> None:
        """Ждёт, пока договорит (нужно перед включением микрофона)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                pending = self._pending
            if pending == 0 and not self._speaking.is_set():
                return
            time.sleep(0.03)

    def shutdown(self) -> None:
        self.stop()
        self._shutdown.set()
        self._queue.put("")  # разбудить воркер
        if self._thread:
            self._thread.join(timeout=3)
        if self._mixer_ready:
            try:
                import pygame

                if pygame.mixer.get_init():
                    pygame.mixer.quit()
            except Exception:  # noqa: BLE001
                pass

    # --------------------------------------------------------------- worker
    def _worker(self) -> None:
        while not self._shutdown.is_set():
            try:
                text = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue

            if self._shutdown.is_set():
                break
            if not text:
                continue

            self._stop_event.clear()
            self._speaking.set()
            try:
                self._say_now(text)
            except Exception:  # noqa: BLE001
                log.exception("Не удалось озвучить текст")
            finally:
                self._speaking.clear()
                with self._lock:
                    self._pending = max(0, self._pending - 1)

    def _say_now(self, text: str) -> None:
        errors: list[str] = []
        for engine in self._engines:
            if not self._engine_available(engine):
                continue
            try:
                self._run_engine(engine, text)
                log.info("Озвучено движком %s", engine)
                return
            except TTSError as exc:
                self._failed_at[engine] = time.monotonic()
                errors.append(f"{engine}: {exc}")
                log.warning("Движок озвучки %s не сработал: %s", engine, exc)
            except Exception as exc:  # noqa: BLE001
                self._failed_at[engine] = time.monotonic()
                errors.append(f"{engine}: {exc!r}")
                log.exception("Ошибка движка озвучки %s", engine)

        log.error("Ни один движок озвучки не сработал (%s)", "; ".join(errors) or "нет движков")
        self._beep()

    def _engine_available(self, engine: str) -> bool:
        failed_at = self._failed_at.get(engine)
        if failed_at is None:
            return True
        if engine == "pyttsx3":
            return False  # без русского голоса повторять бессмысленно
        return (time.monotonic() - failed_at) > _ENGINE_COOLDOWN

    def _run_engine(self, engine: str, text: str) -> None:
        if engine == "edge":
            self._speak_edge(text)
        elif engine == "gtts":
            self._speak_gtts(text)
        elif engine == "pyttsx3":
            self._speak_pyttsx3(text)
        else:
            raise TTSError(f"неизвестный движок {engine}")

    # -------------------------------------------------------------- engines
    def _speak_edge(self, text: str) -> None:
        try:
            import edge_tts
        except ImportError as exc:
            raise TTSError("edge-tts не установлен") from exc

        path = self._temp_file(".mp3")
        voice = self._voice.get("edge_voice", "ru-RU-SvetlanaNeural")
        rate = self._voice.get("edge_rate", "+0%")
        pitch = self._voice.get("edge_pitch", "+0Hz")

        async def _synth() -> None:
            communicate = edge_tts.Communicate(text, voice, rate=rate, pitch=pitch)
            await asyncio.wait_for(communicate.save(path), timeout=_EDGE_TIMEOUT)

        try:
            asyncio.run(_synth())
        except Exception as exc:  # noqa: BLE001
            self._remove(path)
            raise TTSError(f"нет связи с сервером синтеза ({exc})") from exc
        self._play_and_cleanup(path)

    def _speak_gtts(self, text: str) -> None:
        try:
            from gtts import gTTS
        except ImportError as exc:
            raise TTSError("gTTS не установлен") from exc

        path = self._temp_file(".mp3")
        tld = self._voice.get("gtts_tld", "ru")
        try:
            gTTS(text=text, lang="ru", tld=tld).save(path)
        except Exception as exc:  # noqa: BLE001
            self._remove(path)
            raise TTSError(f"gTTS недоступен ({exc})") from exc
        self._play_and_cleanup(path)

    def _speak_pyttsx3(self, text: str) -> None:
        try:
            import pyttsx3
        except ImportError as exc:
            raise TTSError("pyttsx3 не установлен") from exc

        if self._pyttsx3_engine is None:
            engine = pyttsx3.init()
            voices = engine.getProperty("voices") or []
            chosen = None
            wanted = (self._voice.get("pyttsx3_voice") or "").lower()
            for candidate in voices:
                languages = " ".join(candidate.languages or []).lower()
                name = (candidate.name or "").lower()
                if wanted and wanted in name:
                    chosen = candidate
                    break
                if "ru" in languages or "russian" in name:
                    chosen = candidate
                    break
            if chosen is None:
                raise TTSError(
                    "в Windows не установлен русский голос SAPI "
                    "(Параметры -> Время и язык -> Речь)"
                )
            engine.setProperty("voice", chosen.id)
            engine.setProperty("rate", int(self._voice.get("pyttsx3_rate", 190)))
            self._pyttsx3_engine = engine
            log.info("pyttsx3 голос: %s", chosen.name)

        self._pyttsx3_engine.say(text)
        self._pyttsx3_engine.runAndWait()

    # ------------------------------------------------------------- playback
    def _ensure_mixer(self) -> None:
        if self._mixer_ready:
            return
        try:
            import pygame

            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.set_volume(self._volume)
            self._mixer_ready = True
        except Exception:  # noqa: BLE001
            log.exception("Не удалось инициализировать аудиовыход (pygame.mixer)")

    def _play_and_cleanup(self, path: str) -> None:
        try:
            self._play(path)
        finally:
            self._remove(path)

    @staticmethod
    def _remove(path: str) -> None:
        try:
            os.remove(path)
        except OSError:
            pass

    def _play(self, path: str) -> None:
        if not os.path.exists(path) or os.path.getsize(path) < 1024:
            raise TTSError("получен пустой аудиофайл (нет подходящего голоса?)")

        self._ensure_mixer()
        if not self._mixer_ready:
            raise TTSError("аудиовыход недоступен")

        import pygame

        try:
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
        except Exception as exc:  # noqa: BLE001
            raise TTSError(f"не удалось воспроизвести ({exc})") from exc

        while pygame.mixer.music.get_busy():
            if self._stop_event.is_set():
                pygame.mixer.music.stop()
                return
            time.sleep(0.05)

    def _temp_file(self, suffix: str) -> str:
        with self._lock:
            self._tmp_index += 1
            index = self._tmp_index
        return os.path.join(
            tempfile.gettempdir(), f"glados_tts_{os.getpid()}_{index}{suffix}"
        )

    @staticmethod
    def _beep() -> None:
        try:
            import winsound

            winsound.Beep(880, 150)
        except Exception:  # noqa: BLE001
            pass


def describe_engines(config: dict) -> str:
    """Короткая диагностика для --test-tts и логов."""
    return (
        f"engine={get(config, 'voice.engine', 'auto')}, "
        f"edge={get(config, 'voice.edge_voice', '')}, "
        f"gtts_tld={get(config, 'voice.gtts_tld', '')}"
    )
