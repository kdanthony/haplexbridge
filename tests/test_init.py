"""Tests for Plex Stream Bridge setup."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.plex_stream_bridge.const import (
    CONF_CHANNELS,
    CONF_LOCAL_URL,
    DOMAIN,
    PLEX_DOMAIN,
)
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr, entity_registry as er

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
    with patch("custom_components.plex_stream_bridge.async_check_ffmpeg", return_value=problem):
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


async def test_availability_follows_plex(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    loaded_plex_entry: MockConfigEntry,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Channels go unavailable with the Plex server and log each change once."""
    with patch("homeassistant.components.plex.async_unload_entry", return_value=True):
        await hass.config_entries.async_unload(loaded_plex_entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("media_player.plex_channel_1").state == "unavailable"
    assert hass.states.get("camera.plex_channel_1").state == "unavailable"
    assert caplog.text.count("is unavailable") == 1

    with patch("homeassistant.components.plex.async_setup_entry", return_value=True):
        await hass.config_entries.async_setup(loaded_plex_entry.entry_id)
        await hass.async_block_till_done()
    assert hass.states.get("media_player.plex_channel_1").state == "idle"
    assert caplog.text.count("is available again") == 1


async def test_stale_channels_removed(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    device_registry: dr.DeviceRegistry,
    entity_registry: er.EntityRegistry,
) -> None:
    """Lowering the channel count removes the extra channels' devices and entities."""
    hass.config_entries.async_update_entry(init_integration, options={CONF_CHANNELS: 2})
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()
    assert entity_registry.async_get("camera.plex_channel_2") is not None

    hass.config_entries.async_update_entry(init_integration, options={CONF_CHANNELS: 1})
    await hass.config_entries.async_reload(init_integration.entry_id)
    await hass.async_block_till_done()

    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, f"{SERVER_ID}_2"), init_integration.entry_id
        )
        is None
    )
    assert entity_registry.async_get("camera.plex_channel_2") is None
    assert entity_registry.async_get("media_player.plex_channel_2") is None
    assert entity_registry.async_get("camera.plex_channel_1") is not None


@pytest.mark.parametrize(
    ("options", "internal_url", "expected"),
    [
        ({CONF_LOCAL_URL: "http://ha.lan:8123/"}, None, "http://ha.lan:8123"),
        ({}, "https://ha.lan:8123", "https://ha.lan:8123"),
        ({}, None, "http://127.0.0.1:8123"),
    ],
)
async def test_stream_base_override_and_ssl(
    hass: HomeAssistant,
    loaded_plex_entry: MockConfigEntry,
    mock_get_plex_server: MagicMock,
    mock_ffmpeg_check: AsyncMock,
    config_entry: MockConfigEntry,
    options: dict[str, str],
    internal_url: str | None,
    expected: str,
) -> None:
    """The option wins; with HA serving HTTPS the internal URL is used."""
    hass.config_entries.async_update_entry(config_entry, options=options)
    hass.config.api.use_ssl = True
    hass.config.internal_url = internal_url
    with patch.object(hass.http, "server_port", 8123):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
    assert config_entry.runtime_data.stream_base == expected
