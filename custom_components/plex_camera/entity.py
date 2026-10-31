"""Base entity for Plex Camera channels."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity

from .channel import Channel
from .const import DOMAIN

if TYPE_CHECKING:
    from . import PlexCameraConfigEntry


class PlexChannelEntity(Entity):
    """An entity that follows one channel's state."""

    _attr_has_entity_name = True
    _attr_should_poll = False

    def __init__(self, entry: PlexCameraConfigEntry, channel: Channel, key: str) -> None:
        """Initialize."""
        super().__init__()
        self._channel = channel
        device_id = f"{entry.unique_id}_{channel.number}"
        self._attr_unique_id = f"{device_id}_{key}"
        device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            translation_key="channel",
            translation_placeholders={"number": str(channel.number)},
            manufacturer="Plex",
            model="Virtual channel",
            entry_type=DeviceEntryType.SERVICE,
        )
        if via_device_id := entry.runtime_data.via_device_id:
            device_info["via_device_id"] = via_device_id
        self._attr_device_info = device_info

    async def async_added_to_hass(self) -> None:
        """Follow channel state changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self._channel.async_add_listener(self.async_write_ha_state))
