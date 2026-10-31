"""HTTP endpoint that serves each channel's live MPEG-TS feed."""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, cast

from aiohttp import web

from homeassistant.components.http import KEY_HASS, HomeAssistantView
from homeassistant.config_entries import ConfigEntryState

from .const import DOMAIN, STREAM_AUTH_PARAM

if TYPE_CHECKING:
    from . import PlexStreamBridgeConfigEntry


class PlexStreamBridgeStreamView(HomeAssistantView):
    """
    Serve /api/plex_stream_bridge/<entry>/<channel>/stream.ts?auth=...

    The camera hands this URL to HA's stream worker (and go2rtc), which cannot
    send HA auth headers, so each channel has its own random access token. It's
    passed as ``auth`` because HA's stream component redacts that parameter
    (with ``user`` and ``password``) when it logs the URL.
    """

    url = f"/api/{DOMAIN}/{{entry_id}}/{{number}}/stream.ts"
    name = f"api:{DOMAIN}:stream"
    requires_auth = False

    async def get(self, request: web.Request, entry_id: str, number: str) -> web.StreamResponse:
        """Stream a channel."""
        entry = request.app[KEY_HASS].config_entries.async_get_entry(entry_id)
        if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
            raise web.HTTPNotFound
        channels = cast("PlexStreamBridgeConfigEntry", entry).runtime_data.channels
        if not number.isdigit() or not 0 < int(number) <= len(channels):
            raise web.HTTPNotFound
        channel = channels[int(number) - 1]
        if not secrets.compare_digest(
            request.query.get(STREAM_AUTH_PARAM, ""), channel.access_token
        ):
            raise web.HTTPUnauthorized
        return await channel.async_serve(request)
