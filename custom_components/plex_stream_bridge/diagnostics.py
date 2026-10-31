"""Diagnostics for Plex Stream Bridge."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import PlexStreamBridgeConfigEntry
from .const import CONF_CLIENT_ID

TO_REDACT = {CONF_CLIENT_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PlexStreamBridgeConfigEntry
) -> dict[str, Any]:
    """Return channel state; never the stream tokens or Plex URLs."""
    data = entry.runtime_data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
        },
        "plex_available": data.bridge.available,
        "stream_base": data.stream_base,
        "channels": [channel.as_diagnostics() for channel in data.channels],
    }
