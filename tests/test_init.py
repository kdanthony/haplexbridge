"""Tests for Plex Camera setup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.plex_camera.const import DOMAIN, PLEX_DOMAIN
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .conftest import SERVER_ID


async def test_setup_and_unload(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    loaded_plex_entry: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
) -> None:
    """Each channel gets a camera and media player under the Plex server device."""
    assert init_integration.state is ConfigEntryState.LOADED
    assert hass.states.get("camera.plex_channel_1") is not None
    assert hass.states.get("media_player.plex_channel_1").state == "idle"

    device = device_registry.async_get_device_by_identifier(
        (DOMAIN, f"{SERVER_ID}_1"), init_integration.entry_id
    )
    plex_device = device_registry.async_get_device_by_identifier(
        (PLEX_DOMAIN, SERVER_ID), loaded_plex_entry.entry_id
    )
    assert device.name == "Plex channel 1"
    assert device.via_device_id == plex_device.id

    assert await hass.config_entries.async_unload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert init_integration.state is ConfigEntryState.NOT_LOADED


async def test_plex_entry_missing(
    hass: HomeAssistant, mock_get_plex_server: MagicMock, config_entry: MockConfigEntry
) -> None:
    """Setup fails permanently if the Plex server was removed."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR


async def test_plex_not_loaded(
    hass: HomeAssistant,
    plex_entry: MockConfigEntry,
    mock_get_plex_server: MagicMock,
    config_entry: MockConfigEntry,
) -> None:
    """Setup retries while the Plex entry is still loading."""
    await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_RETRY


@pytest.mark.parametrize("problem", ["ffmpeg_missing", "ffmpeg_unsupported"])
async def test_ffmpeg_not_capable(
    hass: HomeAssistant,
    loaded_plex_entry: MockConfigEntry,
    mock_get_plex_server: MagicMock,
    config_entry: MockConfigEntry,
    problem: str,
) -> None:
    """Setup fails with a clear reason when ffmpeg can't produce feeds."""
    with patch("custom_components.plex_camera.async_check_ffmpeg", return_value=problem):
        await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    assert config_entry.error_reason_translation_key == problem


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("0.0.0.0", "http://127.0.0.1:8123"),
        ("192.168.1.5", "http://192.168.1.5:8123"),
        ("fd00::5", "http://[fd00::5]:8123"),
    ],
)
async def test_stream_base_follows_bind_address(
    hass: HomeAssistant,
    loaded_plex_entry: MockConfigEntry,
    mock_get_plex_server: MagicMock,
    mock_ffmpeg_check: AsyncMock,
    config_entry: MockConfigEntry,
    host: str,
    expected: str,
) -> None:
    """The camera reaches the feed on the address HA listens on."""
    hass.config.api.host = host
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.runtime_data.stream_base == expected
