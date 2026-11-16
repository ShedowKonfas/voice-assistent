"""Управление Home Assistant по REST API.

Модуль сам разбирает фразы вида «включи телевизор», «запусти робот-пылесос»,
«какая температура в спальне»: находит сущность по имени, зоне или alias и
вызывает нужный сервис Home Assistant.

Настройки (config.local.json → home_assistant):
    enabled          — включить поддержку;
    base_url         — адрес, например http://192.168.3.209:8123;
    token            — долгоживущий токен доступа;
    verify_ssl       — проверять сертификат (для самоподписанного — false);
    timeout          — таймаут HTTP, секунды;
    refresh_seconds  — сколько кэшировать список сущностей;
    state_wait_seconds — сколько ждать обновления состояния после команды;
    use_areas        — подтягивать зоны (area) через /api/template;
    aliases          — {"как говорю": "entity_id"}.
"""
from __future__ import annotations

import difflib
import logging
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from wake import normalize

log = logging.getLogger(__name__)

# Возвращается, когда фраза не относится к Home Assistant: её должен обработать
# обычный Dispatcher (например, «громче» — громкость Windows).
NOT_HANDLED = object()


# --------------------------------------------------------------------- ошибки
class HomeAssistantError(Exception):
    """Базовая ошибка Home Assistant с готовым текстом для озвучки."""


class HomeAssistantConnectionError(HomeAssistantError):
    def __init__(self) -> None:
        super().__init__("Нет связи с Home Assistant. Проверьте адрес и сеть.")


class HomeAssistantAuthError(HomeAssistantError):
    def __init__(self) -> None:
        super().__init__("Home Assistant не принял токен.")


class HomeAssistantApiError(HomeAssistantError):
    def __init__(self, status: int, detail: str = "") -> None:
        self.status = status
        self.detail = detail
        message = f"Home Assistant вернул ошибку {status}."
        if detail:
            message += f" {detail}"
        super().__init__(message)


# ------------------------------------------------------------------ сущности
@dataclass
class Entity:
    entity_id: str
    name: str
    state: str
    attributes: dict[str, Any] = field(default_factory=dict)
    area: str = ""

    @property
    def domain(self) -> str:
        return self.entity_id.split(".", 1)[0]

    @property
    def label(self) -> str:
        """Имя для озвучки; зона добавляется, если её нет в имени."""
        if self.area and normalize(self.area) not in normalize(self.name):
            return f"{self.name} ({self.area})"
        return self.name or self.entity_id


@dataclass(frozen=True)
class Match:
    score: float
    entity: Entity


@dataclass(frozen=True)
class Intent:
    action: str
    value: float | str | None = None


# ------------------------------------------------------------ нормализация
_ENDINGS = tuple(sorted(
    (
        "иями", "ями", "ами", "ыми", "ими", "ому", "ему", "ого", "его",
        "ов", "ев", "ий", "ый", "ой", "ая", "яя", "ое", "ее", "ые", "ие",
        "ам", "ям", "ах", "ях", "ом", "ем", "ую", "юю", "ей",
        "а", "я", "о", "е", "ы", "и", "у", "ю", "ь",
    ),
    key=len,
    reverse=True,
))

STOP_WORDS = {
    "в", "во", "на", "с", "со", "у", "к", "ко", "по", "для", "от", "до",
    "из", "за", "под", "над", "при", "о", "об", "и", "а", "но", "или",
    "же", "ли", "бы", "мне", "меня", "мой", "моя", "мое", "мои", "наш",
    "наша", "наше", "наши", "пожалуйста", "плиз", "давай", "сейчас",
    "теперь", "там", "тут", "это", "этот", "эта", "эти",
    # глаголы и служебные слова команд
    "включи", "включить", "выключи", "выключить", "зажги", "погаси",
    "потуши", "открой", "открыть", "закрой", "закрыть", "запусти",
    "запустить", "останови", "остановить", "переключи", "переключить",
    "поставь", "установи", "сделай", "продолжи", "возобнови", "прибавь",
    "убавь", "увеличь", "уменьши", "верни", "найди", "запри", "отопри",
    "что", "как", "какой", "какая", "какое", "какие", "статус", "состояние",
}

# Домены, которыми можно управлять.
CONTROLLABLE_DOMAINS = {
    "light", "switch", "input_boolean", "fan", "media_player", "vacuum",
    "climate", "water_heater", "cover", "lock", "scene", "script",
    "automation", "remote", "humidifier", "siren",
}

# Слова, по которым понятно, что речь о конкретном типе устройства.
CATEGORY_DOMAINS: dict[str, set[str]] = {
    "телевизор": {"media_player"}, "телик": {"media_player"},
    "тв": {"media_player"}, "tv": {"media_player"},
    "пылесос": {"vacuum"}, "робот": {"vacuum"},
    "свет": {"light"}, "освещение": {"light"}, "лампа": {"light"},
    "люстра": {"light"}, "подсветка": {"light"}, "торшер": {"light"},
    "розетка": {"switch"}, "выключатель": {"switch"},
    "кондиционер": {"climate"}, "термостат": {"climate"},
    "обогреватель": {"climate"}, "котел": {"climate", "water_heater"},
    "шторы": {"cover"}, "жалюзи": {"cover"}, "ворота": {"cover"},
    "замок": {"lock"}, "колонка": {"media_player"}, "динамик": {"media_player"},
}

STATUS_DOMAIN_HINTS: tuple[tuple[str, set[str]], ...] = (
    (r"\bтемператур", {"sensor", "climate", "water_heater", "weather"}),
    (r"\bвлажност", {"sensor", "weather", "humidifier"}),
    (r"\b(заряд|батаре)", {"vacuum", "sensor"}),
    (r"\b(громкост|источник|звук|трек|композиц)", {"media_player"}),
    (r"\bяркост", {"light"}),
    (r"\b(позици|положени)", {"cover"}),
)

# Русские названия режимов → английские значения из fan_speed_list.
OPTION_SYNONYMS: dict[str, set[str]] = {
    "турбо": {"turbo", "max", "maximum", "boost"},
    "максимальный": {"max", "maximum", "turbo", "boost"},
    "максимум": {"max", "maximum", "turbo", "boost"},
    "тихий": {"quiet", "silent", "min", "minimum", "low"},
    "тише": {"quiet", "silent", "min", "minimum", "low"},
    "минимум": {"min", "minimum", "quiet", "low"},
    "средний": {"medium", "normal", "balanced"},
    "эконом": {"eco", "energy", "low"},
}


def _stem(word: str) -> str:
    """Очень простой стеммер для русских слов (без внешних зависимостей)."""
    if len(word) <= 3:
        return word
    for ending in _ENDINGS:
        if word.endswith(ending) and len(word) - len(ending) >= 3:
            return word[: -len(ending)]
    return word


def _tokens(text: str) -> list[str]:
    return [t for t in normalize(text).split() if t not in STOP_WORDS]


def _stems(text: str) -> set[str]:
    return {_stem(t) for t in _tokens(text)}


def _stem_match(left: str, right: str) -> bool:
    if left == right:
        return True
    if len(left) >= 4 and len(right) >= 4:
        if left.startswith(right) or right.startswith(left):
            return True
        return difflib.SequenceMatcher(None, left, right).ratio() >= 0.84
    return False


def _has(text: str, pattern: str) -> bool:
    return re.search(pattern, text) is not None


def _first_number(text: str, allow_negative: bool = False) -> float | None:
    pattern = r"-?\d+(?:[.,]\d+)?" if allow_negative else r"\d+(?:[.,]\d+)?"
    found = re.search(pattern, text)
    if not found:
        return None
    try:
        return float(found.group(0).replace(",", "."))
    except ValueError:
        return None


def detect_category(text: str) -> set[str]:
    """Домены по словам вроде «телевизор» или «пылесос»."""
    stems = _stems(text)
    domains: set[str] = set()
    for word, word_domains in CATEGORY_DOMAINS.items():
        word_stem = _stem(word)
        if any(_stem_match(word_stem, stem) for stem in stems):
            domains |= word_domains
    return domains


def _status_domains(text: str) -> set[str]:
    for pattern, domains in STATUS_DOMAIN_HINTS:
        if _has(text, pattern):
            return set(domains)
    return set()


def parse_intent(text: str) -> Intent:
    """Разбирает фразу в действие. Фраза уже нормализована Dispatcher-ом."""
    if _has(text, r"\b(где|найди|ищи)\b.*\b(робот|пылесос)\b") or _has(text, r"\blocate\b"):
        return Intent("vacuum_locate")
    if _has(text, r"\b(на базу|на док|вернись|вернуть|домой|док)\b"):
        return Intent("vacuum_dock")
    if _has(text, r"\b(уберись|убери|пропылесось|пропылесосить)\b"):
        return Intent("vacuum_start")
    if _has(text, r"\bуборк"):
        if _has(text, r"\b(останови|стоп|прекрати)"):
            return Intent("vacuum_stop")
        if _has(text, r"\b(пауза|паузу|приостанови|приостановить)"):
            return Intent("vacuum_pause")
        if _has(text, r"\b(запусти|начни|старт|продолжи|возобнови)"):
            return Intent("vacuum_start")

    number = _first_number(text)
    if _has(text, r"\bгромкост") and number is not None:
        return Intent("volume_set", number)
    if _has(text, r"\bяркост") and number is not None:
        return Intent("brightness_set", number)
    if _has(text, r"\bтемператур") and _first_number(text, allow_negative=True) is not None:
        return Intent("temperature_set", _first_number(text, allow_negative=True))

    if _has(text, r"\b(запри|запереть)\b") or _has(text, r"\bзакрой\b.*\bзамок\b"):
        return Intent("lock")
    if _has(text, r"\b(отопри|отпереть)\b") or _has(text, r"\bоткрой\b.*\bзамок\b"):
        return Intent("unlock")

    if _has(text, r"\b(без звука|замут|мьют|mute|выключи звук|убери звук|отключи звук)\b"):
        return Intent("mute_on")
    if _has(text, r"\b(верни звук|включи звук|размут|unmute)\b"):
        return Intent("mute_off")
    if _has(text, r"\b(громче|прибавь|увеличь)\b") or _has(text, r"\bгромкость\b.*\bвыше\b"):
        return Intent("volume_up")
    if _has(text, r"\b(тише|убавь|уменьши)\b") or _has(text, r"\bгромкость\b.*\bниже\b"):
        return Intent("volume_down")

    if _has(text, r"\b(пауза|паузу|приостанови|приостановить)\b"):
        return Intent("pause")
    if _has(text, r"\b(продолжи|продолжить|возобнови|возобновить|играй|воспроизведи|плей|play)\b"):
        return Intent("play")
    if _has(text, r"\b(стоп|останови|остановить)\b"):
        return Intent("stop")
    if _has(text, r"\b(следующ\w*|некст\w*)\b"):
        return Intent("next")
    if _has(text, r"\b(предыдущ\w*)\b"):
        return Intent("prev")

    fan = re.search(r"\b(турбо|максимальн\w*|максимум|тих\w*|минимум|эконом\w*|средн\w*)\b", text)
    if fan:
        return Intent("fan_set", fan.group(1))

    if _has(text, r"\b(переключи|переключить|toggle)\b"):
        return Intent("toggle")
    if _has(text, r"\b(включи|включить|зажги|подними|открой|открыть|запусти|запустить)\b"):
        return Intent("on")
    if _has(text, r"\b(выключи|выключить|погаси|потуши|отключи|отключить|заглуши|закрой|закрыть)\b"):
        return Intent("off")

    if _has(
        text,
        r"\b(что с|статус|состояние|как дела|как он|как она|включен|выключен|горит|"
        r"работает|играет|убирается|заряд|источник|громкость|влажность|температура|"
        r"яркость|открыт|закрыт|заперт)\b",
    ):
        return Intent("status")

    return Intent("unknown")


def _intent_domains(intent: Intent) -> set[str] | None:
    action = intent.action
    if action in {"volume_set", "volume_up", "volume_down", "mute_on", "mute_off", "next", "prev"}:
        return {"media_player"}
    if action in {"pause", "play", "stop"}:
        return {"media_player", "vacuum", "remote"}
    if action in {"vacuum_start", "vacuum_pause", "vacuum_stop", "vacuum_dock", "vacuum_locate"}:
        return {"vacuum"}
    if action == "fan_set":
        return {"vacuum", "fan"}
    if action == "brightness_set":
        return {"light"}
    if action == "temperature_set":
        return {"climate", "water_heater"}
    if action in {"lock", "unlock"}:
        return {"lock"}
    if action in {"on", "off", "toggle"}:
        return set(CONTROLLABLE_DOMAINS)
    return None


def _best_option(query: str, options: list[str]) -> str | None:
    """Нечётко выбирает источник/режим из списка, который отдал HA."""
    query_norm = normalize(query)
    query_stems = _stems(query_norm)
    best: str | None = None
    best_score = 0.0

    for option in options:
        option_norm = normalize(str(option))
        if not option_norm:
            continue
        option_stems = {_stem(t) for t in option_norm.split() if t not in STOP_WORDS}

        if option_norm in query_norm or query_norm in option_norm:
            score = 1.0
        else:
            matched = sum(
                1 for stem in option_stems if any(_stem_match(stem, q) for q in query_stems)
            )
            score = matched / len(option_stems) if option_stems else 0.0
            score = max(
                score,
                difflib.SequenceMatcher(None, option_norm, query_norm).ratio(),
            )
            for russian, aliases in OPTION_SYNONYMS.items():
                russian_stem = _stem(russian)
                if (
                    any(_stem_match(russian_stem, q) for q in query_stems)
                    and any(alias in option_norm for alias in aliases)
                ):
                    score = max(score, 1.0)

        if score > best_score:
            best_score = score
            best = str(option)

    return best if best_score >= 0.6 else None


def _plural(number: float, one: str, few: str, many: str) -> str:
    value = abs(int(round(number)))
    if value % 10 == 1 and value % 100 != 11:
        return one
    if 2 <= value % 10 <= 4 and not 12 <= value % 100 <= 14:
        return few
    return many


def _degrees(value: float) -> str:
    text = f"{value:g}".replace(".", ",")
    return f"{text} {_plural(value, 'градус', 'градуса', 'градусов')}"


def _percents(value: float) -> str:
    text = f"{round(value):g}"
    return f"{text} {_plural(value, 'процент', 'процента', 'процентов')}"


def _format_number(value: Any) -> str:
    try:
        return f"{float(value):g}".replace(".", ",")
    except (TypeError, ValueError):
        return str(value)


def describe_state(entity: Entity) -> str:
    """Текст состояния устройства для озвучки."""
    label = entity.label
    state = entity.state
    attrs = entity.attributes

    if state in {"unavailable", "unknown", ""}:
        return f"{label}: нет данных."

    domain = entity.domain

    if domain == "media_player":
        words = {
            "playing": "играет", "paused": "на паузе", "idle": "включено",
            "off": "выключено", "on": "включено", "standby": "в режиме ожидания",
            "buffering": "загружается",
        }
        parts = [f"{label}: {words.get(state, state)}"]
        volume = attrs.get("volume_level")
        if volume is not None:
            try:
                parts.append(f"громкость {_percents(float(volume) * 100)}")
            except (TypeError, ValueError):
                pass
        if attrs.get("is_volume_muted"):
            parts.append("звук выключен")
        source = attrs.get("source") or attrs.get("app_name")
        if source:
            parts.append(f"источник {source}")
        return ", ".join(parts) + "."

    if domain == "vacuum":
        words = {
            "cleaning": "убирается", "docked": "на базе", "paused": "на паузе",
            "returning": "возвращается на базу", "error": "ошибка",
            "idle": "простаивает", "off": "выключено", "on": "включено",
        }
        parts = [f"{label}: {words.get(state, state)}"]
        battery = attrs.get("battery_level")
        if battery is not None:
            try:
                parts.append(f"заряд {_percents(float(battery))}")
            except (TypeError, ValueError):
                pass
        fan = attrs.get("fan_speed")
        if fan:
            parts.append(f"режим {fan}")
        return ", ".join(parts) + "."

    if domain == "light":
        if state != "on":
            return f"{label}: выключено."
        parts = [f"{label}: включено"]
        brightness = attrs.get("brightness")
        if brightness is not None:
            try:
                parts.append(f"яркость {_percents(float(brightness) / 255 * 100)}")
            except (TypeError, ValueError):
                pass
        return ", ".join(parts) + "."

    if domain in {"switch", "input_boolean", "fan", "remote", "siren"}:
        return f"{label}: {'включено' if state == 'on' else 'выключено'}."

    if domain == "climate":
        words = {
            "heating": "обогревает", "cooling": "охлаждает", "drying": "осушает",
            "fan": "вентилирует", "idle": "в режиме ожидания", "off": "выключено",
            "heat": "обогревает", "cool": "охлаждает", "auto": "автоматический режим",
        }
        action = attrs.get("hvac_action") or state
        parts = [f"{label}: {words.get(action, action)}"]
        current = attrs.get("current_temperature")
        if current is not None:
            try:
                parts.append(f"сейчас {_degrees(float(current))}")
            except (TypeError, ValueError):
                pass
        target = attrs.get("temperature")
        if target is not None:
            try:
                parts.append(f"цель {_degrees(float(target))}")
            except (TypeError, ValueError):
                pass
        return ", ".join(parts) + "."

    if domain == "cover":
        words = {
            "open": "открыто", "closed": "закрыто", "opening": "открывается",
            "closing": "закрывается",
        }
        parts = [f"{label}: {words.get(state, state)}"]
        position = attrs.get("current_position")
        if position is not None:
            try:
                parts.append(f"позиция {_percents(float(position))}")
            except (TypeError, ValueError):
                pass
        return ", ".join(parts) + "."

    if domain == "lock":
        words = {
            "locked": "заперто", "unlocked": "открыто", "locking": "запирается",
            "unlocking": "отпирается", "jammed": "заклинило",
        }
        return f"{label}: {words.get(state, state)}."

    if domain == "binary_sensor":
        device_class = attrs.get("device_class")
        if device_class in {"door", "window", "opening", "garage_door"}:
            return f"{label}: {'открыто' if state == 'on' else 'закрыто'}."
        if device_class == "motion":
            return f"{label}: {'есть движение' if state == 'on' else 'движения нет'}."
        if device_class == "moisture":
            return f"{label}: {'есть протечка' if state == 'on' else 'сухо'}."
        if device_class == "smoke":
            return f"{label}: {'есть дым' if state == 'on' else 'дыма нет'}."
        if device_class in {"occupancy", "presence"}:
            return f"{label}: {'есть присутствие' if state == 'on' else 'никого'}."
        if device_class == "lock":
            return f"{label}: {'заперто' if state == 'on' else 'открыто'}."
        if device_class == "problem":
            return f"{label}: {'есть проблема' if state == 'on' else 'проблем нет'}."
        return f"{label}: {'сработал' if state == 'on' else 'в норме'}."

    if domain == "sensor":
        unit = attrs.get("unit_of_measurement") or ""
        try:
            value = float(state)
        except (TypeError, ValueError):
            return f"{label}: {state}."
        if unit in {"°C", "C", "℃", "°Celsius"}:
            return f"{label}: {_degrees(value)}."
        if unit == "%":
            return f"{label}: {_percents(value)}."
        if unit:
            return f"{label}: {_format_number(value)} {unit}."
        return f"{label}: {_format_number(value)}."

    return f"{label}: {state}."


# ------------------------------------------------------------------- клиент
class HomeAssistantClient:
    """Тонкая обёртка над REST API Home Assistant."""

    def __init__(self, config: dict, session: Any | None = None) -> None:
        settings = config.get("home_assistant", {}) or {}
        self.base_url = str(settings.get("base_url") or "").strip().rstrip("/")
        if self.base_url and "://" not in self.base_url:
            self.base_url = f"http://{self.base_url}"
        self.token = str(settings.get("token") or "").strip()
        self.verify_ssl = bool(settings.get("verify_ssl", True))
        self.timeout = float(settings.get("timeout", 8))
        self.refresh_seconds = float(settings.get("refresh_seconds", 15))
        self.state_wait = float(settings.get("state_wait_seconds", 0.8))
        self.use_areas = bool(settings.get("use_areas", True))
        self.aliases = {
            normalize(str(phrase)): str(entity_id)
            for phrase, entity_id in (settings.get("aliases") or {}).items()
            if str(phrase).strip() and str(entity_id).strip()
        }

        self._session = session or requests.Session()
        self._entities: dict[str, Entity] = {}
        self._fetched_at = 0.0
        self._lock = threading.Lock()

    @property
    def configured(self) -> bool:
        return bool(self.base_url and self.token)

    # ----------------------------------------------------------------- HTTP
    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        try:
            response = self._session.request(
                method,
                url,
                headers=headers,
                timeout=self.timeout,
                verify=self.verify_ssl,
                **kwargs,
            )
        except requests.RequestException as exc:
            log.warning("Home Assistant недоступен (%s): %s", url, exc)
            raise HomeAssistantConnectionError() from exc

        if response.status_code in (401, 403):
            raise HomeAssistantAuthError()
        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
            except ValueError:
                payload = None
            if isinstance(payload, dict):
                detail = str(payload.get("message") or payload.get("error") or "")
            if not detail:
                detail = (response.text or "").strip()[:200]
            raise HomeAssistantApiError(response.status_code, detail)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return None

    def api_config(self) -> dict[str, Any]:
        data = self._request("GET", "/api/config")
        return data if isinstance(data, dict) else {}

    def _fetch_states(self) -> dict[str, Entity]:
        data = self._request("GET", "/api/states")
        if not isinstance(data, list):
            raise HomeAssistantApiError(200, "Неожиданный ответ списка устройств.")

        entities: dict[str, Entity] = {}
        for item in data:
            if not isinstance(item, dict):
                continue
            entity_id = str(item.get("entity_id") or "")
            if not entity_id:
                continue
            attrs = item.get("attributes") or {}
            entities[entity_id] = Entity(
                entity_id=entity_id,
                name=str(attrs.get("friendly_name") or entity_id),
                state=str(item.get("state") or ""),
                attributes=attrs if isinstance(attrs, dict) else {},
            )

        if self.use_areas:
            try:
                for entity_id, area in self._fetch_areas().items():
                    if entity_id in entities and area:
                        entities[entity_id].area = area
            except HomeAssistantError as exc:
                log.warning("Не удалось получить зоны Home Assistant: %s", exc)

        return entities

    def _fetch_areas(self) -> dict[str, str]:
        template = (
            "{% for s in states %}{{ s.entity_id }}\t{{ area_name(s.entity_id) }}\n{% endfor %}"
        )
        data = self._request("POST", "/api/template", json={"template": template})
        if not isinstance(data, str):
            return {}
        areas: dict[str, str] = {}
        for line in data.splitlines():
            parts = line.split("\t")
            if len(parts) >= 2 and parts[1].strip():
                areas[parts[0].strip()] = parts[1].strip()
        return areas

    def states(self, force: bool = False) -> list[Entity]:
        with self._lock:
            fresh = time.monotonic() - self._fetched_at < self.refresh_seconds
            if self._entities and fresh and not force:
                return list(self._entities.values())

            entities = self._fetch_states()
            self._entities = entities
            self._fetched_at = time.monotonic()
            return list(entities.values())

    def _get_state(self, entity_id: str) -> Entity:
        data = self._request("GET", f"/api/states/{entity_id}")
        if not isinstance(data, dict):
            raise HomeAssistantApiError(200, f"Нет данных для {entity_id}.")
        attrs = data.get("attributes") or {}
        existing = self._entities.get(entity_id)
        return Entity(
            entity_id=entity_id,
            name=str(attrs.get("friendly_name") or entity_id),
            state=str(data.get("state") or ""),
            attributes=attrs if isinstance(attrs, dict) else {},
            area=existing.area if existing else "",
        )

    def _remember(self, entity: Entity) -> Entity:
        previous = self._entities.get(entity.entity_id)
        if previous is not None and not entity.area:
            entity.area = previous.area
        self._entities[entity.entity_id] = entity
        return entity

    # ------------------------------------------------------------ сущности
    def entities_in(self, domains: set[str] | None = None) -> list[Entity]:
        entities = self.states()
        if domains:
            entities = [entity for entity in entities if entity.domain in domains]
        return entities

    def has_domain(self, domains: set[str] | None) -> bool:
        if not domains:
            return False
        return any(entity.domain in domains for entity in self.states())

    def _alias_score(self, phrase: str, text_norm: str, text_stems: set[str]) -> float:
        if f" {phrase} " in f" {text_norm} ":
            return 10.0 + len(phrase.split()) / 100
        phrase_stems = {_stem(token) for token in phrase.split() if token not in STOP_WORDS}
        if phrase_stems and all(
            any(_stem_match(stem, query) for query in text_stems) for stem in phrase_stems
        ):
            return 9.0 + len(phrase.split()) / 100
        return 0.0

    def alias_present(self, text: str) -> bool:
        """Есть ли в фразе alias — без обращения к серверу."""
        text_norm = normalize(text)
        text_stems = _stems(text_norm)
        return any(
            self._alias_score(phrase, text_norm, text_stems) > 0
            for phrase in self.aliases
        )

    def resolve(self, text: str, domains: set[str] | None = None) -> list[Match]:
        """Ищет устройства по alias, имени и зоне. Возвращает лучшие совпадения."""
        entities = self.states()
        if domains:
            entities = [entity for entity in entities if entity.domain in domains]
            if not entities:
                return []

        text_norm = normalize(text)
        text_stems = _stems(text_norm)

        # 1. Alias из конфига — самый надёжный способ.
        alias_hits: list[Match] = []
        for phrase, entity_id in self.aliases.items():
            score = self._alias_score(phrase, text_norm, text_stems)
            if score <= 0:
                continue
            entity = next((e for e in entities if e.entity_id == entity_id), None)
            if entity is None:
                try:
                    entity = self._remember(self._get_state(entity_id))
                except HomeAssistantError:
                    continue
                if domains and entity.domain not in domains:
                    continue
            alias_hits.append(Match(score, entity))
        if alias_hits:
            alias_hits.sort(key=lambda match: match.score, reverse=True)
            return alias_hits[:3]

        # 2. Совпадение слов имени/зоны.
        query_stems = _stems(text_norm)
        matches: list[Match] = []
        for entity in entities:
            score = _entity_score(entity, query_stems)
            if score >= 0.45:
                matches.append(Match(score, entity))
        matches.sort(key=lambda match: match.score, reverse=True)
        return matches[:5]

    def call_service(
        self,
        domain: str,
        service: str,
        entity_id: str,
        data: dict[str, Any] | None = None,
    ) -> Entity | None:
        payload: dict[str, Any] = {"entity_id": entity_id}
        if data:
            payload.update(data)
        self._request("POST", f"/api/services/{domain}/{service}", json=payload)

        before = self._entities.get(entity_id)
        deadline = time.monotonic() + self.state_wait
        last: Entity | None = None
        while True:
            try:
                last = self._remember(self._get_state(entity_id))
            except HomeAssistantError as exc:
                log.debug("Не удалось перечитать %s: %s", entity_id, exc)
                return None

            if before is None:
                return last
            if last.state != before.state or last.attributes != before.attributes:
                return last
            if time.monotonic() >= deadline:
                return last
            time.sleep(0.2)


def _entity_score(entity: Entity, query_stems: set[str]) -> float:
    weights: dict[str, float] = {}
    for token in _tokens(entity.name):
        stem = _stem(token)
        weights[stem] = max(weights.get(stem, 0.0), 1.0)
    for token in _tokens(entity.area):
        stem = _stem(token)
        weights[stem] = max(weights.get(stem, 0.0), 0.9)

    if not weights:
        tail = entity.entity_id.split(".", 1)[-1].replace("_", " ")
        for token in _tokens(tail):
            weights[_stem(token)] = 1.0
    if not weights:
        return 0.0

    matched = 0.0
    for stem, weight in weights.items():
        if any(_stem_match(stem, query) for query in query_stems):
            matched += weight
    return matched / sum(weights.values())


# --------------------------------------------------------------- контроллер
class HomeAssistant:
    """Связывает Home Assistant с командами ассистента."""

    def __init__(self, config: dict, session: Any | None = None) -> None:
        settings = config.get("home_assistant", {}) or {}
        self.enabled = bool(settings.get("enabled", False))
        self.client = HomeAssistantClient(config, session=session)
        self._warned = False

    @property
    def configured(self) -> bool:
        return self.enabled and self.client.configured

    @property
    def base_url(self) -> str:
        return self.client.base_url

    def status(self) -> str:
        info = self.client.api_config()
        entities = self.client.states(force=True)
        version = info.get("version") or "неизвестна"
        name = info.get("location_name") or "Home Assistant"
        return f"{name}: версия {version}, устройств в базе: {len(entities)}."

    def entities(self, domains: set[str] | None = None) -> list[Entity]:
        entities = self.client.entities_in(domains)
        return sorted(entities, key=lambda entity: (entity.domain, entity.name.lower()))

    def handle(self, text: str) -> Any:
        """Возвращает ответ, None («выполнено молча») или NOT_HANDLED."""
        if not self.enabled:
            return NOT_HANDLED
        if not self.client.configured:
            if not self._warned:
                log.warning(
                    "Home Assistant включён, но не задан base_url или token в config.local.json"
                )
                self._warned = True
            return NOT_HANDLED

        try:
            return self._handle(text)
        except HomeAssistantApiError as exc:
            # Детали ответа сервера бывают на английском — озвучиваем только
            # по-русски, а текст ошибки оставляем в логе.
            log.warning("Home Assistant: %s (%s)", exc, exc.detail or "без деталей")
            return f"Home Assistant вернул ошибку {exc.status}. Подробности в логе."
        except HomeAssistantError as exc:
            log.warning("Home Assistant: %s", exc)
            return str(exc)
        except Exception:  # noqa: BLE001
            log.exception("Ошибка при обращении к Home Assistant")
            return "Не получилось обратиться к Home Assistant, подробности в логе."

    # ------------------------------------------------------------ обработка
    def _handle(self, text: str) -> Any:
        intent = parse_intent(text)
        category = detect_category(text)
        status_domains = _status_domains(text) if intent.action == "status" else set()

        # Не дёргаем Home Assistant на обычные команды вроде «пауза», «громче»
        # или «выключи звук»: они должны остаться командами для компьютера.
        # Особенно важно, когда HA выключен — иначе любая фраза получала бы
        # «Нет связи» вместо нормальной работы.
        strong_action = intent.action in {
            "vacuum_start", "vacuum_pause", "vacuum_stop", "vacuum_dock",
            "vacuum_locate", "fan_set",
        }
        if not (
            category
            or status_domains
            or strong_action
            or self.client.alias_present(text)
        ):
            return NOT_HANDLED

        if intent.action == "status":
            domains = category or status_domains
        else:
            domains = category or _intent_domains(intent)

        matches = self.client.resolve(text, domains=domains)

        # Если имя не совпало, но подходящее устройство в домене одно —
        # берём его. Так работают «запусти уборку» и «включи телевизор»,
        # даже если устройство называется, например, Samsung Q80.
        if not matches and domains and (intent.action != "status" or category):
            candidates = self.client.entities_in(domains)
            if len(candidates) == 1:
                matches = [Match(0.5, candidates[0])]

        if not matches:
            if category:
                return "Не нашла такое устройство в Home Assistant."
            if intent.action in {
                "vacuum_start", "vacuum_pause", "vacuum_stop", "vacuum_dock", "vacuum_locate",
            } and self.client.has_domain({"vacuum"}):
                return "Не нашла робот-пылесос в Home Assistant."
            return NOT_HANDLED

        entity, ambiguous = _choose(matches)
        if ambiguous:
            names = ", ".join(
                f"{match.entity.label} ({match.entity.entity_id})" for match in ambiguous
            )
            return f"Уточните устройство: {names}?"

        # «Переключи телевизор на HDMI 1» — это выбор источника, а не toggle.
        if entity.domain == "media_player" and intent.action in {"on", "off", "toggle", "status"}:
            source = _extract_source(entity, text)
            if source and intent.action != "status":
                intent = Intent("source_set", source)

        if intent.action == "unknown":
            intent = Intent("status")

        if intent.action == "status":
            return describe_state(entity)

        if intent.action in {"volume_set", "brightness_set"}:
            value = float(intent.value) if intent.value is not None else -1
            if not 0 <= value <= 100:
                return "Значение должно быть от 0 до 100."

        service, data = _map_service(entity, intent)
        if not service:
            return f"{entity.label}: не поддерживает такую команду."

        try:
            updated = self.client.call_service(entity.domain, service, entity.entity_id, data)
        except HomeAssistantApiError as exc:
            # Некоторые пылесосы не умеют turn_off/turn_on — пробуем stop/start.
            fallback = _fallback_service(entity, service)
            if not fallback:
                raise exc
            updated = self.client.call_service(
                entity.domain, fallback, entity.entity_id, None
            )
            service = fallback

        return _confirmation(entity, updated or entity, intent, service)


def _choose(matches: list[Match]) -> tuple[Entity | None, list[Match]]:
    if not matches:
        return None, []
    if len(matches) == 1:
        return matches[0].entity, []
    top, second = matches[0], matches[1]
    if top.score - second.score >= 0.15:
        return top.entity, []
    return None, matches[:3]


def _extract_source(entity: Entity, text: str) -> str | None:
    options = entity.attributes.get("source_list") or []
    if not isinstance(options, list) or not options:
        return None
    found = re.search(r"\b(?:на|источник|канал)\s+(.+)$", text)
    if not found:
        return None
    return _best_option(found.group(1), [str(option) for option in options])


def _map_service(entity: Entity, intent: Intent) -> tuple[str | None, dict[str, Any]]:
    domain = entity.domain
    action = intent.action

    if action == "on":
        if domain == "vacuum":
            return "start", {}
        if domain == "lock":
            return "lock", {}
        if domain == "cover":
            return "open_cover", {}
        if domain in {"scene", "script"}:
            return "turn_on", {}
        return "turn_on", {}

    if action == "off":
        if domain == "vacuum":
            return "stop", {}
        if domain == "lock":
            return "unlock", {}
        if domain == "cover":
            return "close_cover", {}
        return "turn_off", {}

    if action == "lock":
        return ("lock", {}) if domain == "lock" else (None, {})
    if action == "unlock":
        return ("unlock", {}) if domain == "lock" else (None, {})

    if action == "toggle":
        if domain in {"lock"}:
            return ("unlock" if entity.state == "locked" else "lock"), {}
        if domain in {"light", "switch", "input_boolean", "fan", "media_player", "cover", "automation"}:
            return "toggle", {}
        return ("turn_off" if entity.state == "on" else "turn_on"), {}

    if action == "pause":
        if domain == "vacuum":
            return "pause", {}
        if domain == "media_player":
            return "media_pause", {}
        return None, {}

    if action == "play":
        if domain == "vacuum":
            return "start", {}
        if domain == "media_player":
            return "media_play", {}
        return None, {}

    if action == "stop":
        if domain == "vacuum":
            return "stop", {}
        if domain == "media_player":
            return "media_stop", {}
        if domain == "cover":
            return "stop_cover", {}
        return None, {}

    if action == "next":
        return ("media_next_track", {}) if domain == "media_player" else (None, {})
    if action == "prev":
        return ("media_previous_track", {}) if domain == "media_player" else (None, {})

    if action == "volume_set":
        if domain != "media_player":
            return None, {}
        return "volume_set", {"volume_level": float(intent.value or 0) / 100}

    if action == "volume_up":
        return ("volume_up", {}) if domain == "media_player" else (None, {})
    if action == "volume_down":
        return ("volume_down", {}) if domain == "media_player" else (None, {})
    if action == "mute_on":
        return ("volume_mute", {"is_volume_muted": True}) if domain == "media_player" else (None, {})
    if action == "mute_off":
        return ("volume_mute", {"is_volume_muted": False}) if domain == "media_player" else (None, {})

    if action == "brightness_set":
        if domain != "light":
            return None, {}
        return "turn_on", {"brightness_pct": float(intent.value or 0)}

    if action == "temperature_set":
        if domain not in {"climate", "water_heater"}:
            return None, {}
        return "set_temperature", {"temperature": float(intent.value or 0)}

    if action == "vacuum_start":
        return ("start", {}) if domain == "vacuum" else (None, {})
    if action == "vacuum_pause":
        return ("pause", {}) if domain == "vacuum" else (None, {})
    if action == "vacuum_stop":
        return ("stop", {}) if domain == "vacuum" else (None, {})
    if action == "vacuum_dock":
        return ("return_to_base", {}) if domain == "vacuum" else (None, {})
    if action == "vacuum_locate":
        return ("locate", {}) if domain == "vacuum" else (None, {})

    if action == "fan_set":
        if domain == "vacuum":
            options = entity.attributes.get("fan_speed_list") or []
            value = _best_option(str(intent.value or ""), [str(o) for o in options]) or str(intent.value or "")
            return "set_fan_speed", {"fan_speed": value}
        if domain == "fan":
            return "set_percentage", {"percentage": 100 if "макс" in str(intent.value) else 50}
        return None, {}

    if action == "source_set":
        return ("select_source", {"source": str(intent.value)}) if domain == "media_player" else (None, {})

    return None, {}


def _fallback_service(entity: Entity, service: str) -> str | None:
    if entity.domain != "vacuum":
        return None
    if service == "turn_off":
        return "stop"
    if service == "turn_on":
        return "start"
    return None


def _confirmation(entity: Entity, updated: Entity, intent: Intent, service: str) -> str:
    label = entity.label
    action = intent.action

    if action == "on":
        if entity.domain == "vacuum":
            return "Запускаю уборку."
        if entity.domain == "scene":
            return f"Активирую сцену: {label}."
        if entity.domain == "script":
            return f"Запускаю сценарий: {label}."
        return f"Включаю: {label}."
    if action == "off":
        if entity.domain == "vacuum":
            return "Останавливаю уборку."
        return f"Выключаю: {label}."
    if action == "lock":
        return f"Запираю: {label}."
    if action == "unlock":
        return f"Отпираю: {label}."
    if action == "toggle":
        return f"Переключаю: {label}."
    if action == "pause":
        return f"Ставлю на паузу: {label}."
    if action == "play":
        return f"Продолжаю: {label}."
    if action == "stop":
        return f"Останавливаю: {label}."
    if action == "next":
        return f"Следующий: {label}."
    if action == "prev":
        return f"Предыдущий: {label}."
    if action == "volume_set":
        return f"Громкость {label}: {_percents(float(intent.value or 0))}."
    if action == "volume_up":
        return f"Прибавляю громкость: {label}."
    if action == "volume_down":
        return f"Убавляю громкость: {label}."
    if action == "mute_on":
        return f"Выключаю звук: {label}."
    if action == "mute_off":
        return f"Включаю звук: {label}."
    if action == "brightness_set":
        return f"Яркость {label}: {_percents(float(intent.value or 0))}."
    if action == "temperature_set":
        return f"Температура {label}: {_degrees(float(intent.value or 0))}."
    if action == "vacuum_start":
        return "Запускаю уборку."
    if action == "vacuum_pause":
        return "Ставлю робот-пылесос на паузу."
    if action == "vacuum_stop":
        return "Останавливаю робот-пылесос."
    if action == "vacuum_dock":
        return "Отправляю робот-пылесос на базу."
    if action == "vacuum_locate":
        return "Ищу робот-пылесос."
    if action == "fan_set":
        return f"Режим {intent.value}: {label}."
    if action == "source_set":
        return f"Переключаю на {intent.value}."
    return describe_state(updated)
