"""Tests for the diagnostic sensors and the queue button."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest

from .conftest import API, WEIGHT_ATTRS, entity_id_for, setup_integration

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )

pytestmark = pytest.mark.usefixtures("source_states")


def _state(hass: HomeAssistant, entity_id: str) -> str:
    state = hass.states.get(entity_id)
    assert state is not None
    return state.state


async def test_sensors_after_successful_send(  # noqa: PLR0917
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
    freezer: FrozenDateTimeFactory,
) -> None:
    freezer.move_to("2026-10-10 05:30:00+00:00")
    aioclient_mock.post(f"{API}/weight", status=201, json={"success": True})
    await setup_integration(hass, mock_config_entry)
    hass.states.async_set("sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    await hass.async_block_till_done()

    def eid(key: str) -> str:
        return entity_id_for(entity_registry, mock_config_entry, "sensor", key)

    assert _state(hass, eid("sent_values")) == "1"
    assert _state(hass, eid("last_transfer")) == "2026-10-10T05:30:00+00:00"
    assert _state(hass, eid("queue")) == "0"
    assert _state(hass, eid("last_error")) == "unknown"

    rate = entity_registry.async_get(eid("rate_limit_remaining"))
    assert rate is not None
    assert rate.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    device = device_registry.async_get_device(
        {("syncvita", mock_config_entry.entry_id)}
    )
    assert device is not None
    assert device.name == "Syncvita Tim"
    assert device.entry_type is dr.DeviceEntryType.SERVICE


async def test_sensors_after_error_and_button(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    entity_registry: er.EntityRegistry,
) -> None:
    aioclient_mock.post(
        f"{API}/weight",
        status=500,
        json={
            "success": False,
            "error": {"code": "DATABASE_ERROR", "message": "db down"},
        },
    )
    await setup_integration(hass, mock_config_entry)
    hass.states.async_set("sensor.scale_weight", "81.0", WEIGHT_ATTRS)
    await hass.async_block_till_done()

    queue_id = entity_id_for(entity_registry, mock_config_entry, "sensor", "queue")
    error_id = entity_id_for(entity_registry, mock_config_entry, "sensor", "last_error")
    queue = hass.states.get(queue_id)
    assert queue is not None
    assert queue.state == "1"
    assert queue.attributes["oldest"] is not None
    error = hass.states.get(error_id)
    assert error is not None
    assert error.state == "db down"
    assert error.attributes["http_status"] == 500

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{API}/bulk", status=201, json={"success": True})
    button_id = entity_id_for(
        entity_registry, mock_config_entry, "button", "process_queue"
    )
    await hass.services.async_call(
        "button", "press", {"entity_id": button_id}, blocking=True
    )
    await hass.async_block_till_done()
    assert _state(hass, queue_id) == "0"
    assert _state(hass, error_id) == "unknown"
