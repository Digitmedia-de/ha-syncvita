"""Tests for the backfill action (needs the recorder)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.syncvita.const import DOMAIN

from .conftest import API, WEIGHT_ATTRS, setup_integration

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    recorder_mock: Recorder, enable_custom_integrations: None
) -> None:
    """Start the recorder before hass, then allow custom integrations."""


async def _record_history(hass: HomeAssistant, freezer: FrozenDateTimeFactory) -> None:
    await hass.config.async_set_time_zone("Europe/Berlin")
    for moment, value in (
        ("2026-10-08 06:00:00+00:00", "80.0"),
        ("2026-10-08 07:00:00+00:00", "81.0"),
        ("2026-10-09 07:00:00+00:00", "unavailable"),
        ("2026-10-09 07:05:00+00:00", "81.5"),
        ("2026-10-10 07:00:00+00:00", "82.0"),
    ):
        freezer.move_to(moment)
        hass.states.async_set("sensor.scale_weight", value, WEIGHT_ATTRS)
        await hass.async_block_till_done()
    await async_wait_recording_done(hass)


async def test_backfill_sends_unsent_history_once(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _record_history(hass, freezer)
    aioclient_mock.post(f"{API}/bulk", status=201, json={"success": True})
    await setup_integration(hass, mock_config_entry)
    mock_config_entry.runtime_data.store.mark_sent(
        "sensor.scale_weight", "2026-10-08T09:00:00|81.0", dt_util.utcnow()
    )
    call = {
        "config_entry_id": mock_config_entry.entry_id,
        "start": "2026-10-08",
        "measures": ["weight"],
    }
    response = await hass.services.async_call(
        DOMAIN, "backfill", call, blocking=True, return_response=True
    )
    assert response == {"sent": 1, "skipped": 1, "failed": 0}
    assert aioclient_mock.mock_calls[-1][2]["data"] == [
        {
            "type": "weight",
            "date": "2026-10-10",
            "time": "09:00:00",
            "values": {"weight": 82.0},
        }
    ]

    response = await hass.services.async_call(
        DOMAIN, "backfill", call, blocking=True, return_response=True
    )
    assert response == {"sent": 0, "skipped": 2, "failed": 0}


async def test_backfill_without_response_and_error(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
) -> None:
    await _record_history(hass, freezer)
    aioclient_mock.post(f"{API}/bulk", status=500, json={})
    await setup_integration(hass, mock_config_entry)
    with pytest.raises(HomeAssistantError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "backfill",
            {"config_entry_id": mock_config_entry.entry_id, "start": "2026-10-08"},
            blocking=True,
        )
    assert exc_info.value.translation_key == "send_failed"
