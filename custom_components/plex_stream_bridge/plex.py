"""Bridge to the Plex integration's server connection."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import partial
import logging
import re
from typing import Any

import aiohttp
from plexapi.exceptions import PlexApiException
from plexapi.server import PlexServer as PlexAPIServer
from plexapi.video import Episode, Movie, Season, Show, Video
import requests

# Same cross-integration entry points Sonos and Cast use for plex:// media.
from homeassistant.components.plex import PlexServer as HAPlexServer
from homeassistant.components.plex.services import (  # pylint: disable=home-assistant-component-root-import
    get_plex_server,
    process_plex_payload,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from .const import (
    DOMAIN,
    PLEX_DEVICE_NAME,
    PLEX_PRODUCT,
    PLEX_PROFILE_EXTRA,
    PLEX_URI_SCHEME,
)

_LOGGER = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"(X-Plex-Token=)[^&\s\"']+", re.IGNORECASE)


def redact_token(text: str) -> str:
    """Mask Plex tokens in text that may end up in logs (ffmpeg prints its URLs)."""
    return _TOKEN_RE.sub(r"\1**REDACTED**", text)


@dataclass(frozen=True, kw_only=True)
class PlexItem:
    """Snapshot of a playable Plex video."""

    server_id: str
    rating_key: str
    media_type: str
    title: str
    duration: float  # seconds
    year: int | None = None
    show_title: str | None = None
    season: int | None = None
    episode: int | None = None
    thumb: str | None = None
    art: str | None = None
    video: Video = field(compare=False, repr=False)

    @classmethod
    def from_video(cls, server_id: str, video: Video) -> PlexItem:
        """Build from a plexapi video; may do I/O, so run in the executor."""
        is_episode = isinstance(video, Episode)
        return cls(
            server_id=server_id,
            rating_key=str(video.ratingKey),
            media_type=str(video.TYPE),
            title=video.title,
            duration=(video.duration or 0) / 1000,
            year=video.year,
            show_title=video.grandparentTitle if is_episode else None,
            season=video.parentIndex if is_episode else None,
            episode=video.index if is_episode else None,
            thumb=video.thumb,
            art=video.art or (video.grandparentArt if is_episode else None),
            video=video,
        )

    @property
    def media_content_id(self) -> str:
        """The media browser ID for this item."""
        return f"{PLEX_URI_SCHEME}{self.server_id}/{self.rating_key}"

    @property
    def label(self) -> str:
        """Human-readable label for logs."""
        if self.show_title:
            return f"{self.show_title} S{self.season or 0:02d}E{self.episode or 0:02d}"
        return f"{self.title} ({self.year})" if self.year else self.title


def _pick_video(media: Any) -> Video:
    """Reduce a Plex lookup result to one playable video."""
    if isinstance(media, list):
        videos = [item for item in media if isinstance(item, (Movie, Episode))]
        if not videos:
            raise _unsupported(media)
        return next((video for video in videos if not video.isPlayed), videos[0])
    if isinstance(media, (Show, Season)):
        episode: Episode | None = media.onDeck()
        if episode is not None:
            return episode
        return _pick_video(media.episodes())
    if isinstance(media, (Movie, Episode)):
        return media
    raise _unsupported(media)


def _unsupported(media: Any) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key="unsupported_media",
        translation_placeholders={"type": getattr(media, "TYPE", type(media).__name__)},
    )


class PlexBridge:
    """Access one Plex server through the Plex integration's connection."""

    def __init__(
        self,
        hass: HomeAssistant,
        server_id: str,
        client_id: str,
        session: aiohttp.ClientSession,
    ) -> None:
        """Initialize."""
        self._hass = hass
        self.server_id = server_id
        self._client_id = client_id
        self._session = session
        self.available = True

    def _ha_server(self) -> HAPlexServer:
        """Look up the Plex integration's server on each use to survive reloads."""
        try:
            return get_plex_server(self._hass, plex_server_id=self.server_id)
        except (HomeAssistantError, KeyError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="server_unavailable"
            ) from err

    @property
    def _server(self) -> PlexAPIServer:
        server: PlexAPIServer = self._ha_server().plex_server
        return server

    async def async_resolve(self, media_type: str, media_id: str) -> tuple[PlexItem, float]:
        """Resolve a play_media request to an item and start offset in seconds."""
        ha_server = self._ha_server()

        def resolve() -> tuple[PlexItem, float]:
            result = process_plex_payload(
                self._hass,
                media_type,
                media_id,
                default_plex_server=ha_server,
                supports_playqueues=False,
            )
            item = PlexItem.from_video(self.server_id, _pick_video(result.media))
            return item, result.offset / 1000

        try:
            return await self._hass.async_add_executor_job(resolve)
        except (PlexApiException, requests.RequestException) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="lookup_failed",
                translation_placeholders={"error": str(err)},
            ) from err

    async def async_hls_url(
        self,
        item: PlexItem,
        session_id: str,
        offset: float,
        max_resolution: str,
        max_bitrate: int,
    ) -> str:
        """Universal-transcoder HLS URL that yields H.264 video."""
        params = {
            "protocol": "hls",
            "offset": int(offset),
            "copyts": 0,
            "videoResolution": max_resolution,
            "maxVideoBitrate": max_bitrate,
            "directPlay": 0,
            "directStream": 1,
            "directStreamAudio": 0,
            "videoQuality": 100,
            "subtitles": "burn",
            "location": "lan",
            "session": session_id,
            "X-Plex-Session-Identifier": session_id,
            "X-Plex-Client-Identifier": self._client_id,
            "X-Plex-Product": PLEX_PRODUCT,
            "X-Plex-Device-Name": PLEX_DEVICE_NAME,
            "X-Plex-Client-Profile-Extra": PLEX_PROFILE_EXTRA,
        }
        url: str = await self._hass.async_add_executor_job(
            partial(item.video.getStreamURL, **params)
        )
        await self._async_transcode_decision(url)
        return url

    async def _async_transcode_decision(self, start_url: str) -> None:
        """
        Ask Plex for a transcode decision, as Plex clients do before starting.

        Plex rejects start requests (400) for a new session that skipped this.
        """
        decision_url = start_url.replace("/start.m3u8?", "/decision?", 1)
        try:
            async with self._session.get(
                decision_url,
                headers={"Accept": "application/json"},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                status = resp.status
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="server_unavailable"
            ) from err
        if status != 200:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="transcode_refused",
                translation_placeholders={"status": str(status)},
            )

    def image_url(self, path: str, width: int, height: int) -> str:
        """URL for a server-side resized image (no I/O)."""
        url: str = self._server.transcodeImage(path, height, width, minSize=1, upscale=1)
        return url

    async def async_image(self, path: str, width: int, height: int) -> bytes | None:
        """Fetch a resized image."""
        try:
            async with self._session.get(
                self.image_url(path, width, height),
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return None
                return await resp.read()
        except (aiohttp.ClientError, TimeoutError, HomeAssistantError) as err:
            _LOGGER.debug("Could not fetch %s: %s", path, redact_token(str(err)))
            return None

    async def async_stop_transcode(self, session_id: str) -> None:
        """Tear down a transcode session (best effort)."""
        try:
            url = self._server.url(
                f"/video/:/transcode/universal/stop?session={session_id}", includeToken=True
            )
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)):
                pass
        except (aiohttp.ClientError, TimeoutError, HomeAssistantError) as err:
            _LOGGER.debug("Could not stop transcode %s: %s", session_id, redact_token(str(err)))
