"""Camera entities: the live video of each channel."""

from __future__ import annotations

from homeassistant.components.camera import Camera, CameraEntityFeature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import PlexCameraConfigEntry
from .channel import Channel, PlaybackState
from .const import DOMAIN
from .entity import PlexChannelEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PlexCameraConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add a camera per channel."""
    async_add_entities(PlexChannelCamera(entry, channel) for channel in entry.runtime_data.channels)


class PlexChannelCamera(PlexChannelEntity, Camera):
    """A channel's video as a streaming camera."""

    _attr_name = None
    _attr_brand = "Plex"
    _attr_supported_features = CameraEntityFeature.STREAM

    def __init__(self, entry: PlexCameraConfigEntry, channel: Channel) -> None:
        """Initialize."""
        super().__init__(entry, channel, "camera")
        self._stream_url = (
            f"{entry.runtime_data.stream_base}/api/{DOMAIN}/{entry.entry_id}"
            f"/{channel.number}/stream.ts?token={channel.access_token}"
        )
        self._image_key: tuple[str, int, int] | None = None
        self._image: bytes | None = None

    @property
    def is_streaming(self) -> bool:
        """Report streaming while something is playing."""
        return self._channel.state is PlaybackState.PLAYING

    async def stream_source(self) -> str:
        """Return the channel's real-time MPEG-TS feed served by this integration."""
        return self._stream_url

    async def async_camera_image(
        self, width: int | None = None, height: int | None = None
    ) -> bytes | None:
        """Use the playing item's artwork as the still image."""
        item = self._channel.item
        if item is None or not (path := item.art or item.thumb):
            return None
        key = (path, width or 1280, height or 720)
        if key != self._image_key:
            # Dashboards refresh stills every few seconds; artwork rarely changes.
            self._image = await self._channel.bridge.async_image(*key)
            self._image_key = key if self._image else None
        return self._image
