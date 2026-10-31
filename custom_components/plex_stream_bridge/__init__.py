"""Play Plex videos as Home Assistant camera streams."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.ffmpeg import get_ffmpeg_manager
from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_VERIFY_SSL, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.network import NoURLAvailableError, get_url
from homeassistant.helpers.typing import ConfigType

from .channel import Channel, async_check_ffmpeg
from .const import (
    CONF_CHANNELS,
    CONF_CLIENT_ID,
    CONF_LOCAL_URL,
    CONF_MAX_BITRATE,
    CONF_MAX_RESOLUTION,
    CONF_REENCODE_VIDEO,
    CONF_SERVER_IDENTIFIER,
    DEFAULT_CHANNELS,
    DEFAULT_MAX_BITRATE,
    DEFAULT_MAX_RESOLUTION,
    DEFAULT_REENCODE_VIDEO,
    DOMAIN,
    PLEX_DOMAIN,
)
from .plex import PlexBridge
from .view import PlexStreamBridgeStreamView

PLATFORMS = [Platform.CAMERA, Platform.MEDIA_PLAYER]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)
WILDCARD_HOSTS = {"0.0.0.0", "::", ""}


@dataclass
class PlexStreamBridgeData:
    """Runtime data for one Plex server's channels."""

    bridge: PlexBridge
    channels: list[Channel]
    stream_base: str
    via_device_id: str | None


type PlexStreamBridgeConfigEntry = ConfigEntry[PlexStreamBridgeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the stream endpoint shared by all entries."""
    hass.http.register_view(PlexStreamBridgeStreamView())
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PlexStreamBridgeConfigEntry) -> bool:
    """Set up channels for a server from the Plex integration."""
    server_id: str = entry.data[CONF_SERVER_IDENTIFIER]
    plex_entry = hass.config_entries.async_entry_for_domain_unique_id(PLEX_DOMAIN, server_id)
    if plex_entry is None:
        raise ConfigEntryError(translation_domain=DOMAIN, translation_key="plex_entry_missing")
    if plex_entry.state is not ConfigEntryState.LOADED:
        raise ConfigEntryNotReady(translation_domain=DOMAIN, translation_key="plex_not_loaded")

    verify_ssl = plex_entry.data.get("server_config", {}).get(CONF_VERIFY_SSL, True)
    bridge = PlexBridge(
        hass,
        server_id,
        entry.data[CONF_CLIENT_ID],
        async_get_clientsession(hass, verify_ssl=verify_ssl),
    )

    ffmpeg_binary = get_ffmpeg_manager(hass).binary
    if problem := await async_check_ffmpeg(ffmpeg_binary):
        raise ConfigEntryError(
            translation_domain=DOMAIN,
            translation_key=problem,
            translation_placeholders={"binary": ffmpeg_binary},
        )

    options = entry.options
    channels = [
        Channel(
            hass,
            bridge,
            ffmpeg_binary,
            number,
            options.get(CONF_MAX_RESOLUTION, DEFAULT_MAX_RESOLUTION),
            int(options.get(CONF_MAX_BITRATE, DEFAULT_MAX_BITRATE)),
            options.get(CONF_REENCODE_VIDEO, DEFAULT_REENCODE_VIDEO),
        )
        for number in range(1, int(options.get(CONF_CHANNELS, DEFAULT_CHANNELS)) + 1)
    ]
    # Link channels under the Plex server's device when the Plex integration has one.
    plex_device = dr.async_get(hass).async_get_device_by_identifier(
        (PLEX_DOMAIN, server_id), plex_entry.entry_id
    )
    entry.runtime_data = PlexStreamBridgeData(
        bridge,
        channels,
        _stream_base(hass, options.get(CONF_LOCAL_URL)),
        plex_device.id if plex_device else None,
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PlexStreamBridgeConfigEntry) -> bool:
    """Unload a config entry."""
    if unloaded := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        for channel in entry.runtime_data.channels:
            await channel.async_shutdown()
    return unloaded


def _stream_base(hass: HomeAssistant, override: str | None) -> str:
    """URL at which HA itself (and go2rtc) can reach the stream view."""
    if override:
        return override.rstrip("/")
    if (api := hass.config.api) and not api.use_ssl:
        # api.host is the first configured server_host, or a wildcard address.
        host = "127.0.0.1" if api.host in WILDCARD_HOSTS else api.host
        if ":" in host:
            host = f"[{host}]"
        return f"http://{host}:{api.port}"
    try:
        return get_url(hass, allow_external=False, allow_cloud=False, prefer_external=False)
    except NoURLAvailableError:
        return f"http://127.0.0.1:{hass.http.server_port}"
