"""Тесты Home Assistant без сети: HTTP-слой подменяется фальшивой сессией."""
from __future__ import annotations

import copy
import os
import sys
import unittest
from dataclasses import dataclass
from typing import Any
from unittest.mock import patch

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

from commands import Dispatcher  # noqa: E402
from config import load_config  # noqa: E402
from home_assistant import (  # noqa: E402
    NOT_HANDLED,
    Entity,
    HomeAssistant,
    describe_state,
)


TV = {
    "entity_id": "media_player.living_tv",
    "state": "off",
    "attributes": {
        "friendly_name": "Телевизор",
        "source_list": ["HDMI 1", "HDMI 2", "YouTube"],
        "volume_level": 0.2,
    },
}

BEDROOM_TV = {
    "entity_id": "media_player.bedroom_tv",
    "state": "off",
    "attributes": {"friendly_name": "Телевизор", "source_list": ["HDMI 1"]},
}

VACUUM = {
    "entity_id": "vacuum.robot",
    "state": "docked",
    "attributes": {
        "friendly_name": "Робот-пылесос",
        "battery_level": 87,
        "fan_speed": "balanced",
        "fan_speed_list": ["quiet", "balanced", "turbo"],
    },
}

TEMPERATURE = {
    "entity_id": "sensor.bedroom_temperature",
    "state": "23.4",
    "attributes": {"friendly_name": "Температура", "unit_of_measurement": "°C"},
}


class FakeResponse:
    def __init__(self, status_code: int = 200, data: Any = None) -> None:
        self.status_code = status_code
        self._data = data
        self.text = "" if data is None else str(data)
        self.content = b"{}" if data is not None else b""

    def json(self) -> Any:
        return self._data


@dataclass
class FakeCall:
    method: str
    url: str
    payload: dict[str, Any]


class FakeSession:
    """Минимальная замена requests.Session с состоянием устройств."""

    def __init__(
        self,
        states: list[dict[str, Any]] | None = None,
        areas: dict[str, str] | None = None,
        failure: str | None = None,
    ) -> None:
        self.states = {item["entity_id"]: copy.deepcopy(item) for item in states or []}
        self.areas = areas or {}
        self.failure = failure
        self.calls: list[FakeCall] = []

    def request(self, method: str, url: str, **kwargs: Any) -> FakeResponse:
        payload = kwargs.get("json") or {}
        self.calls.append(FakeCall(method, url, payload))

        if self.failure == "auth":
            return FakeResponse(401, {"message": "Unauthorized"})
        if self.failure == "connection":
            raise requests.ConnectionError("boom")

        if method == "GET" and url.endswith("/api/states"):
            return FakeResponse(200, list(self.states.values()))
        if method == "POST" and url.endswith("/api/template"):
            lines = [f"{entity_id}\t{area}" for entity_id, area in self.areas.items()]
            return FakeResponse(200, "\n".join(lines))
        if method == "GET" and url.endswith("/api/config"):
            return FakeResponse(200, {"version": "2025.1.0", "location_name": "Дом"})
        if method == "POST" and "/api/services/" in url:
            tail = url.split("/api/services/", 1)[1]
            domain, service = tail.split("/", 1)
            entity_id = payload.get("entity_id", "")
            extra = {k: v for k, v in payload.items() if k != "entity_id"}
            self._apply(entity_id, domain, service, extra)
            return FakeResponse(200, [])
        if method == "GET" and "/api/states/" in url:
            entity_id = url.split("/api/states/", 1)[1]
            if entity_id not in self.states:
                return FakeResponse(404, {"message": "not found"})
            return FakeResponse(200, self.states[entity_id])
        return FakeResponse(404, {"message": "unknown"})

    def _apply(
        self, entity_id: str, domain: str, service: str, extra: dict[str, Any]
    ) -> None:
        record = self.states.get(entity_id)
        if record is None:
            return
        attributes = record.setdefault("attributes", {})

        if domain == "media_player":
            states = {
                "turn_on": "on", "turn_off": "off", "media_pause": "paused",
                "media_play": "playing", "media_stop": "idle",
            }
            if service in states:
                record["state"] = states[service]
            elif service == "volume_set":
                attributes["volume_level"] = extra.get("volume_level")
            elif service == "volume_mute":
                attributes["is_volume_muted"] = extra.get("is_volume_muted")
            elif service == "select_source":
                attributes["source"] = extra.get("source")
        elif domain == "vacuum":
            states = {
                "start": "cleaning", "stop": "idle", "pause": "paused",
                "return_to_base": "returning",
            }
            if service in states:
                record["state"] = states[service]
            elif service == "set_fan_speed":
                attributes["fan_speed"] = extra.get("fan_speed")
        elif domain == "light":
            if service == "turn_on":
                record["state"] = "on"
                if "brightness_pct" in extra:
                    attributes["brightness"] = round(extra["brightness_pct"] * 255 / 100)
            elif service == "turn_off":
                record["state"] = "off"
        elif domain == "climate":
            if service == "set_temperature":
                attributes["temperature"] = extra.get("temperature")
        elif domain == "cover":
            if service == "open_cover":
                record["state"] = "open"
            elif service == "close_cover":
                record["state"] = "closed"
        elif domain == "lock":
            if service == "lock":
                record["state"] = "locked"
            elif service == "unlock":
                record["state"] = "unlocked"
        else:
            if service == "turn_on":
                record["state"] = "on"
            elif service == "turn_off":
                record["state"] = "off"


def make_config(**overrides: Any) -> dict[str, Any]:
    settings: dict[str, Any] = {
        "enabled": True,
        "base_url": "http://ha.test:8123",
        "token": "test-token",
        "verify_ssl": True,
        "timeout": 1,
        "refresh_seconds": 0,
        "state_wait_seconds": 0,
        "use_areas": False,
        "aliases": {},
    }
    settings.update(overrides)
    return {"home_assistant": settings}


def full_config(**overrides: Any) -> dict[str, Any]:
    config = copy.deepcopy(load_config())
    config["home_assistant"] = make_config(**overrides)["home_assistant"]
    return config


def controller(session: FakeSession, **overrides: Any) -> HomeAssistant:
    return HomeAssistant(make_config(**overrides), session=session)


def service_calls(session: FakeSession) -> list[FakeCall]:
    return [call for call in session.calls if "/api/services/" in call.url]


class HomeAssistantTests(unittest.TestCase):
    def test_disabled_is_not_handled(self) -> None:
        ha = controller(FakeSession([TV]), enabled=False)
        self.assertIs(ha.handle("включи телевизор"), NOT_HANDLED)

    def test_turn_on_by_name(self) -> None:
        session = FakeSession([TV])
        reply = controller(session).handle("включи телевизор")
        self.assertEqual(reply, "Включаю: Телевизор.")
        calls = service_calls(session)
        self.assertEqual(len(calls), 1)
        self.assertTrue(calls[0].url.endswith("/api/services/media_player/turn_on"))
        self.assertEqual(calls[0].payload["entity_id"], "media_player.living_tv")

    def test_pause_tv_but_plain_pause_is_not_handled(self) -> None:
        session = FakeSession([TV])
        ha = controller(session)
        self.assertIs(ha.handle("пауза"), NOT_HANDLED)

        reply = ha.handle("поставь телевизор на паузу")
        self.assertEqual(reply, "Ставлю на паузу: Телевизор.")
        self.assertTrue(service_calls(session)[-1].url.endswith("/media_player/media_pause"))

    def test_volume_set(self) -> None:
        session = FakeSession([TV])
        reply = controller(session).handle("установи громкость телевизора на 30 процентов")
        self.assertEqual(reply, "Громкость Телевизор: 30 процентов.")
        call = service_calls(session)[-1]
        self.assertTrue(call.url.endswith("/media_player/volume_set"))
        self.assertAlmostEqual(call.payload["volume_level"], 0.3)

    def test_source_select(self) -> None:
        session = FakeSession([TV])
        reply = controller(session).handle("переключи телевизор на hdmi 1")
        self.assertEqual(reply, "Переключаю на HDMI 1.")
        call = service_calls(session)[-1]
        self.assertTrue(call.url.endswith("/media_player/select_source"))
        self.assertEqual(call.payload["source"], "HDMI 1")

    def test_vacuum_start_dock_and_status(self) -> None:
        session = FakeSession([VACUUM])
        ha = controller(session)

        self.assertEqual(ha.handle("запусти робот пылесос"), "Запускаю уборку.")
        self.assertTrue(service_calls(session)[-1].url.endswith("/vacuum/start"))

        self.assertEqual(ha.handle("верни робота на базу"), "Отправляю робот-пылесос на базу.")
        self.assertTrue(service_calls(session)[-1].url.endswith("/vacuum/return_to_base"))

        reply = ha.handle("что с роботом")
        self.assertIn("Робот-пылесос", reply or "")
        self.assertIn("заряд 87 процентов", reply or "")

    def test_vacuum_fan_speed(self) -> None:
        session = FakeSession([VACUUM])
        reply = controller(session).handle("включи турбо на роботе")
        self.assertEqual(reply, "Режим турбо: Робот-пылесос.")
        call = service_calls(session)[-1]
        self.assertTrue(call.url.endswith("/vacuum/set_fan_speed"))
        self.assertEqual(call.payload["fan_speed"], "turbo")

    def test_vacuum_cleaning_commands_without_device_name(self) -> None:
        vacuum = {
            "entity_id": "vacuum.roborock",
            "state": "cleaning",
            "attributes": {"friendly_name": "Roborock S7", "battery_level": 50},
        }
        session = FakeSession([vacuum])
        ha = controller(session)

        self.assertEqual(ha.handle("поставь уборку на паузу"), "Ставлю робот-пылесос на паузу.")
        self.assertTrue(service_calls(session)[-1].url.endswith("/vacuum/pause"))

        self.assertEqual(ha.handle("останови уборку"), "Останавливаю робот-пылесос.")
        self.assertTrue(service_calls(session)[-1].url.endswith("/vacuum/stop"))

    def test_ambiguous_tv_and_area_disambiguation(self) -> None:
        session = FakeSession(
            [BEDROOM_TV, TV],
            areas={"media_player.bedroom_tv": "Спальня", "media_player.living_tv": "Гостиная"},
        )
        ha = controller(session, use_areas=True)

        reply = ha.handle("включи телевизор")
        self.assertIn("Уточните устройство", reply or "")

        reply = ha.handle("включи телевизор в спальне")
        self.assertEqual(reply, "Включаю: Телевизор (Спальня).")
        call = service_calls(session)[-1]
        self.assertEqual(call.payload["entity_id"], "media_player.bedroom_tv")

    def test_auth_error(self) -> None:
        ha = controller(FakeSession([TV], failure="auth"))
        self.assertEqual(ha.handle("включи телевизор"), "Home Assistant не принял токен.")

    def test_connection_error(self) -> None:
        ha = controller(FakeSession([TV], failure="connection"))
        self.assertEqual(
            ha.handle("включи телевизор"),
            "Нет связи с Home Assistant. Проверьте адрес и сеть.",
        )

    def test_temperature_sensor_with_area(self) -> None:
        entity = Entity(
            entity_id="sensor.bedroom_temperature",
            name="Температура",
            state="23.4",
            attributes={"unit_of_measurement": "°C"},
            area="Спальня",
        )
        self.assertEqual(describe_state(entity), "Температура (Спальня): 23,4 градуса.")

    def test_temperature_query_by_area(self) -> None:
        session = FakeSession(
            [TEMPERATURE], areas={"sensor.bedroom_temperature": "Спальня"}
        )
        reply = controller(session, use_areas=True).handle("какая температура в спальне")
        self.assertEqual(reply, "Температура (Спальня): 23,4 градуса.")

    def test_light_brightness(self) -> None:
        light = {
            "entity_id": "light.living_room",
            "state": "off",
            "attributes": {"friendly_name": "Люстра"},
        }
        session = FakeSession([light])
        reply = controller(session).handle("установи яркость люстры на 40 процентов")
        self.assertEqual(reply, "Яркость Люстра: 40 процентов.")
        call = service_calls(session)[-1]
        self.assertTrue(call.url.endswith("/light/turn_on"))
        self.assertEqual(call.payload["brightness_pct"], 40)

    def test_alias_with_russian_case(self) -> None:
        coffee = {
            "entity_id": "switch.coffee",
            "state": "off",
            "attributes": {"friendly_name": "Кофеварка"},
        }
        session = FakeSession([coffee])
        ha = controller(session, aliases={"кофеварка": "switch.coffee"})
        self.assertEqual(ha.handle("включи кофеварку"), "Включаю: Кофеварка.")
        self.assertTrue(service_calls(session)[-1].url.endswith("/switch/turn_on"))

    def test_unavailable_entity(self) -> None:
        entity = Entity("light.lamp", "Лампа", "unavailable", {})
        self.assertEqual(describe_state(entity), "Лампа: нет данных.")


class DispatcherHomeAssistantTests(unittest.TestCase):
    def build(self, session: FakeSession) -> Dispatcher:
        config = full_config(aliases={"телевизор": "media_player.living_tv"})
        dispatcher = Dispatcher(config)
        dispatcher._home_assistant = HomeAssistant(config, session=session)
        return dispatcher

    def test_ha_handles_device_command(self) -> None:
        session = FakeSession([TV])
        reply = self.build(session).handle("включи телевизор")
        self.assertEqual(reply, "Включаю: Телевизор.")
        self.assertTrue(service_calls(session))

    def test_plain_commands_still_work(self) -> None:
        dispatcher = self.build(FakeSession([TV]))
        with patch("commands.pyautogui.press") as press:
            self.assertEqual(dispatcher.handle("пауза"), "Пауза.")
            press.assert_called_once()
        with patch("commands.pyautogui.press") as press:
            self.assertEqual(dispatcher.handle("громче"), "Громче.")
            press.assert_called_once()

    def test_device_specific_volume_does_not_press_keyboard(self) -> None:
        session = FakeSession([TV])
        dispatcher = self.build(session)
        with patch("commands.pyautogui.press") as press:
            reply = dispatcher.handle("прибавь громкость на телевизоре")
        self.assertEqual(reply, "Прибавляю громкость: Телевизор.")
        press.assert_not_called()
        self.assertTrue(service_calls(session)[-1].url.endswith("/media_player/volume_up"))

    def test_ha_down_does_not_break_pc_commands(self) -> None:
        dispatcher = self.build(FakeSession([TV], failure="connection"))
        with patch("commands.pyautogui.press") as press:
            self.assertEqual(dispatcher.handle("пауза"), "Пауза.")
            press.assert_called_once()


if __name__ == "__main__":
    unittest.main()
