"""Diagnostics must never contain the API key or health values."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

import pytest

from custom_components.syncvita.diagnostics import async_get_config_entry_diagnostics

from .conftest import API, API_KEY, WEIGHT_ATTRS, mock_export_api, setup_integration

if TYPE_CHECKING:
    from freezegun.api import FrozenDateTimeFactory
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )


@pytest.mark.usefixtures("source_states")
async def test_diagnostics_are_redacted(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    export_config_entry: MockConfigEntry,
    freezer: FrozenDateTimeFactory,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    freezer.move_to("2026-10-10 10:00:00+00:00")
    mock_export_api(aioclient_mock)
    aioclient_mock.post(
        f"{API}/weight",
        status=401,
        json={
            "success": False,
            "error": {"code": "UNAUTHORIZED", "message": "Invalid API key"},
        },
    )
    await setup_integration(hass, export_config_entry)
    hass.states.async_set("sensor.scale_weight", "81.37", WEIGHT_ATTRS)
    await hass.async_block_till_done()

    diagnostics = await async_get_config_entry_diagnostics(hass, export_config_entry)
    text = json.dumps(diagnostics, default=str)
    assert API_KEY not in text
    assert API_KEY not in caplog.text
    assert "81.37" not in text
    assert "8421" not in text
    assert diagnostics["entry"]["data"]["api_key"] == "**REDACTED**"
    assert diagnostics["entry"]["title"] == "**REDACTED**"
    assert diagnostics["queue"]["size"] == 1
    assert diagnostics["queue"]["per_measure"] == {"weight": 1}
    assert diagnostics["export"]["metrics"]["step_count"]["count"] == 9012
