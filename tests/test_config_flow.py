"""Tests for the Plex Stream Bridge config flow."""

from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.plex_stream_bridge.const import (
    CONF_CHANNELS,
    CONF_CLIENT_ID,
    CONF_MAX_BITRATE,
    CONF_MAX_RESOLUTION,
    CONF_REENCODE_VIDEO,
    CONF_SERVER_IDENTIFIER,
    DOMAIN,
)
from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType

from .conftest import SERVER_ID


async def test_user_flow(hass: HomeAssistant, loaded_plex_entry: MockConfigEntry) -> None:
    """Picking a Plex server creates an entry for it."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_SERVER_IDENTIFIER: SERVER_ID}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Living Room Plex"
    assert result["data"][CONF_SERVER_IDENTIFIER] == SERVER_ID
    assert len(result["data"][CONF_CLIENT_ID]) == 32
    assert result["result"].unique_id == SERVER_ID


async def test_no_plex_servers(hass: HomeAssistant) -> None:
    """Abort when the Plex integration has no servers."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_plex_servers"


async def test_plex_not_loaded(hass: HomeAssistant, plex_entry: MockConfigEntry) -> None:
    """Servers whose Plex entry isn't loaded are not offered."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_plex_servers"


async def test_server_already_configured(
    hass: HomeAssistant, loaded_plex_entry: MockConfigEntry, config_entry: MockConfigEntry
) -> None:
    """Servers that already have channels are not offered."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "no_plex_servers"


async def test_options_flow(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """Changing options reloads the entry with more channels."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            CONF_CHANNELS: 2,
            CONF_MAX_RESOLUTION: "1280x720",
            CONF_MAX_BITRATE: 4000,
            CONF_REENCODE_VIDEO: False,
        },
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert init_integration.options[CONF_CHANNELS] == 2
    assert hass.states.get("media_player.plex_channel_2") is not None
    assert hass.states.get("camera.plex_channel_2") is not None


async def test_options_defaults(
    hass: HomeAssistant, init_integration: MockConfigEntry, mock_get_plex_server: MagicMock
) -> None:
    """The form is pre-filled with defaults."""
    result = await hass.config_entries.options.async_init(init_integration.entry_id)
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema if callable(key.default)}
    assert defaults[CONF_CHANNELS] == 1
    assert defaults[CONF_MAX_RESOLUTION] == "1920x1080"
