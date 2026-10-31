"""Media player entities: control what each channel plays."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components import plex
from homeassistant.components.media_player import (
    BrowseMedia,
    MediaPlayerDeviceClass,
    MediaPlayerEntity,
    MediaPlayerEntityFeature,
    MediaPlayerState,
    MediaType,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PlexCameraConfigEntry
from .channel import Channel, PlaybackState
from .const import DOMAIN
from .entity import PlexChannelEntity

PARALLEL_UPDATES = 0

STATE_MAP = {
    PlaybackState.IDLE: MediaPlayerState.IDLE,
    PlaybackState.PLAYING: MediaPlayerState.PLAYING,
    PlaybackState.PAUSED: MediaPlayerState.PAUSED,
}
MEDIA_TYPE_MAP = {"movie": MediaType.MOVIE, "episode": MediaType.EPISODE}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlexCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a media player per channel."""
    async_add_entities(
        PlexChannelMediaPlayer(entry, channel) for channel in entry.runtime_data.channels
    )


class PlexChannelMediaPlayer(PlexChannelEntity, MediaPlayerEntity):
    """Plays Plex media on a channel; the camera shows the video."""

    _attr_name = None
    _attr_device_class = MediaPlayerDeviceClass.TV
    _attr_supported_features = (
        MediaPlayerEntityFeature.BROWSE_MEDIA
        | MediaPlayerEntityFeature.PLAY_MEDIA
        | MediaPlayerEntityFeature.PLAY
        | MediaPlayerEntityFeature.PAUSE
        | MediaPlayerEntityFeature.STOP
        | MediaPlayerEntityFeature.SEEK
    )

    def __init__(self, entry: PlexCameraConfigEntry, channel: Channel) -> None:
        """Initialize."""
        super().__init__(entry, channel, "media_player")

    @property
    def state(self) -> MediaPlayerState:
        """Playback state."""
        return STATE_MAP[self._channel.state]

    @property
    def media_content_id(self) -> str | None:
        """Media browser ID of the playing item."""
        return item.media_content_id if (item := self._channel.item) else None

    @property
    def media_content_type(self) -> MediaType | None:
        """Movie or episode."""
        return MEDIA_TYPE_MAP.get(item.media_type) if (item := self._channel.item) else None

    @property
    def media_title(self) -> str | None:
        """Title of the playing item."""
        return item.title if (item := self._channel.item) else None

    @property
    def media_series_title(self) -> str | None:
        """Show title for episodes."""
        return item.show_title if (item := self._channel.item) else None

    @property
    def media_season(self) -> str | None:
        """Season number for episodes."""
        item = self._channel.item
        return str(item.season) if item and item.season is not None else None

    @property
    def media_episode(self) -> str | None:
        """Episode number for episodes."""
        item = self._channel.item
        return str(item.episode) if item and item.episode is not None else None

    @property
    def media_duration(self) -> int | None:
        """Duration in seconds."""
        return int(item.duration) if (item := self._channel.item) else None

    @property
    def media_position(self) -> int | None:
        """Position at media_position_updated_at; the frontend extrapolates."""
        return int(self._channel.anchor_position) if self._channel.item else None

    @property
    def media_position_updated_at(self) -> datetime | None:
        """When the position was last valid."""
        return self._channel.updated_at if self._channel.item else None

    @property
    def media_image_url(self) -> str | None:
        """Poster for the playing item; proxied by HA since it carries a token."""
        item = self._channel.item
        if item is None or not item.thumb:
            return None
        try:
            return self._channel.bridge.image_url(item.thumb, 500, 750)
        except HomeAssistantError:
            return None

    @property
    def media_image_hash(self) -> str | None:
        """Hash on the item, not the token-bearing URL."""
        item = self._channel.item
        return f"{item.rating_key}-{item.thumb}" if item and item.thumb else None

    async def async_play_media(
        self, media_type: MediaType | str, media_id: str, **kwargs: Any
    ) -> None:
        """Play a plex:// media ID from the media browser or a Plex search payload."""
        if not plex.is_plex_media_id(media_id):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="not_plex_media",
                translation_placeholders={"media_id": media_id},
            )
        item, offset = await self._channel.bridge.async_resolve(media_type, media_id)
        await self._channel.async_play(item, offset)

    async def async_media_play(self) -> None:
        """Resume playback."""
        await self._channel.async_resume()

    async def async_media_pause(self) -> None:
        """Pause playback."""
        await self._channel.async_pause()

    async def async_media_stop(self) -> None:
        """Stop playback."""
        await self._channel.async_stop()

    async def async_media_seek(self, position: float) -> None:
        """Seek to a position in seconds."""
        await self._channel.async_seek(position)

    async def async_browse_media(
        self,
        media_content_type: MediaType | str | None = None,
        media_content_id: str | None = None,
    ) -> BrowseMedia:
        """Browse the Plex integration's libraries."""
        result: BrowseMedia = await plex.async_browse_media(
            self.hass, media_content_type, media_content_id, platform=DOMAIN
        )
        return result
