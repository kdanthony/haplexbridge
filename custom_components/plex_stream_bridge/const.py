"""Constants for the Plex Stream Bridge integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "plex_stream_bridge"
PLEX_DOMAIN: Final = "plex"
PLEX_URI_SCHEME: Final = "plex://"

# Query parameter carrying a channel's stream token. HA's stream component
# redacts "auth" when it logs a stream URL, so don't rename it to "token".
STREAM_AUTH_PARAM: Final = "auth"

CONF_SERVER_IDENTIFIER: Final = "server_id"
CONF_CLIENT_ID: Final = "client_identifier"
CONF_CHANNELS: Final = "channels"
CONF_MAX_RESOLUTION: Final = "max_resolution"
CONF_MAX_BITRATE: Final = "max_bitrate"
CONF_REENCODE_VIDEO: Final = "reencode_video"
CONF_LOCAL_URL: Final = "local_url"

DEFAULT_CHANNELS: Final = 1
DEFAULT_MAX_RESOLUTION: Final = "1920x1080"
DEFAULT_MAX_BITRATE: Final = 8000  # kbps
DEFAULT_REENCODE_VIDEO: Final = True

RESOLUTIONS: Final = ["854x480", "1280x720", "1920x1080"]

# Plex picks a transcode profile from the product/platform pair. "Plex Web" on
# "Chrome" (plexapi's default platform) reliably yields H.264 over HLS.
PLEX_PRODUCT: Final = "Plex Web"
PLEX_DEVICE_NAME: Final = "Home Assistant Plex Stream Bridge"
PLEX_PROFILE_EXTRA: Final = (
    "add-transcode-target(type=videoProfile&context=streaming&protocol=hls"
    "&container=mpegts&videoCodec=h264&audioCodec=aac)"
)
