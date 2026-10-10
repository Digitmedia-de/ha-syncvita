"""Tests for the send_value action."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.exceptions import ServiceValidationError
import pytest

from custom_components.syncvita.const import DOMAIN

from .conftest import API, setup_integration

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry
    from pytest_homeassistant_custom_component.test_util.aiohttp import (
        AiohttpClientMocker,
    )


async def test_send_value(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(f"{API}/heart-rate", status=201, json={"success": True})
    await setup_integration(hass, mock_config_entry)
    await hass.services.async_call(
        DOMAIN,
        "send_value",
        {
            "config_entry_id": mock_config_entry.entry_id,
            "measure": "heart_rate",
            "value": 61.6,
            "measured_at": "2026-10-10T08:15:00+02:00",
        },
        blocking=True,
    )
    assert aioclient_mock.mock_calls[0][2] == {
        "bpm": 62,
        "date": "2026-10-10",
        "time": "08:15:00",
    }


async def test_send_value_naive_time_is_local(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    mock_config_entry: MockConfigEntry,
) -> None:
    aioclient_mock.post(f"{API}/weight", status=201, json={"success": True})
    await setup_integration(hass, mock_config_entry)
    await hass.services.async_call(
        DOMAIN,
        "send_value",
        {
            "config_entry_id": mock_config_entry.entry_id,
            "measure": "weight",
            "value": 80.4,
            "measured_at": "2026-10-10 07:00:00",
        },
        blocking=True,
    )
    assert aioclient_mock.mock_calls[0][2]["time"] == "07:00:00"


async def test_send_value_out_of_range(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "send_value",
            {
                "config_entry_id": mock_config_entry.entry_id,
                "measure": "heart_rate",
                "value": 400,
            },
            blocking=True,
        )
    assert exc_info.value.translation_key == "out_of_range"


async def test_send_value_unknown_entry(
    hass: HomeAssistant, mock_config_entry: MockConfigEntry
) -> None:
    await setup_integration(hass, mock_config_entry)
    with pytest.raises(ServiceValidationError) as exc_info:
        await hass.services.async_call(
            DOMAIN,
            "send_value",
            {"config_entry_id": "nope", "measure": "weight", "value": 80},
            blocking=True,
        )
    assert exc_info.value.translation_key == "entry_not_loaded"
