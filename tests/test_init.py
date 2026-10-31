"""Tests for Plex Camera setup."""

from unittest.mock import MagicMock

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
