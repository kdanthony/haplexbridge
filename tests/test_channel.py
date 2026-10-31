"""Tests for the channel's ffmpeg capability check."""

import asyncio
from pathlib import Path
import shutil
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.plex_stream_bridge.channel import (
    Channel,
    PlaybackState,
    _drain_stderr,
    _force_put,
    _try_put,
    async_check_ffmpeg,
)
from custom_components.plex_stream_bridge.plex import PlexItem
from homeassistant.core import HomeAssistant

FFMPEG = shutil.which("ffmpeg")


async def test_ffmpeg_missing(tmp_path: Path) -> None:
    """A binary that doesn't exist is reported as missing."""
    assert await async_check_ffmpeg(str(tmp_path / "no-ffmpeg")) == "ffmpeg_missing"


async def test_ffmpeg_unsupported(tmp_path: Path) -> None:
    """An ffmpeg whose test encode fails (e.g. no libx264) is unsupported."""
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\necho 'Unknown encoder libx264' >&2\nexit 1\n")
    fake.chmod(0o755)
    assert await async_check_ffmpeg(str(fake)) == "ffmpeg_unsupported"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
async def test_ffmpeg_capable() -> None:
    """A full ffmpeg build passes."""
    assert await async_check_ffmpeg(FFMPEG) is None


@pytest.fixture
def channel(hass: HomeAssistant) -> Channel:
    """A channel with a mocked Plex bridge."""
    bridge = MagicMock()
    bridge.image_url.return_value = "https://plex.local/photo?X-Plex-Token=t"
    bridge.async_hls_url = AsyncMock(return_value="https://plex.local/start.m3u8?X-Plex-Token=t")
    return Channel(hass, bridge, "ffmpeg", 1, "1280x720", 4000, True, "/ca.pem")


def _item(duration: float = 600) -> PlexItem:
    return PlexItem(
        server_id="s",
        rating_key="1",
        media_type="movie",
        title="T",
        duration=duration,
        art="/art",
        video=MagicMock(),
    )


async def test_play_command_secures_plex_input(channel: Channel) -> None:
    """Plex inputs are network-only and TLS-verified, and video is re-encoded."""
    await channel.async_play(_item())
    cmd = await channel._command(PlaybackState.PLAYING, channel.item, "s1")
    url_at = cmd.index("-i")
    assert cmd[url_at - 6 : url_at] == [
        "-protocol_whitelist",
        "http,https,tls,tcp,crypto",
        "-tls_verify",
        "1",
        "-ca_file",
        "/ca.pem",
    ]
    assert cmd[cmd.index("-c:v") + 1] == "libx264"
    assert cmd[cmd.index("-bf") + 1] == "0"


async def test_plain_http_skips_tls_options(channel: Channel) -> None:
    """ffmpeg rejects TLS options on http inputs, so they're left off."""
    channel.bridge.async_hls_url.return_value = "http://plex.local/start.m3u8"
    channel._reencode_video = False
    await channel.async_play(_item())
    cmd = await channel._command(PlaybackState.PLAYING, channel.item, "s1")
    assert "-tls_verify" not in cmd
    assert "-protocol_whitelist" in cmd
    assert cmd[cmd.index("-c:v") + 1] == "copy"


async def test_paused_command_uses_artwork(channel: Channel) -> None:
    """Paused channels loop the item's artwork, fetched securely."""
    await channel.async_play(_item())
    await channel.async_pause()
    cmd = await channel._command(PlaybackState.PAUSED, channel.item, "s1")
    assert "https://plex.local/photo?X-Plex-Token=t" in cmd
    assert "-tls_verify" in cmd
    assert channel.as_diagnostics()["state"] == "paused"


async def test_play_exit_handling(channel: Channel, caplog: pytest.LogCaptureFixture) -> None:
    """Clean exits finish the item; repeated quick failures give up with a redacted log."""
    await channel.async_play(_item())
    channel._handle_play_exit(1, 60, "")  # long run then failure: transient
    assert channel.state is PlaybackState.PLAYING
    for _ in range(3):
        channel._handle_play_exit(1, 1, "https://plex/x?X-Plex-Token=**REDACTED**: 500")
    assert channel.state is PlaybackState.IDLE
    assert "could not stream" in caplog.text

    await channel.async_play(_item())
    channel._handle_play_exit(0, 600, "")
    assert channel.state is PlaybackState.IDLE
    assert channel.item is None


async def test_stderr_is_redacted(caplog: pytest.LogCaptureFixture) -> None:
    """ffmpeg output that includes Plex URLs never logs the token."""
    caplog.set_level("DEBUG")
    proc = await asyncio.create_subprocess_exec(
        "sh",
        "-c",
        "echo 'Error opening input file https://p/x.m3u8?X-Plex-Token=SECRET.' >&2",
        stderr=asyncio.subprocess.PIPE,
    )
    last = await _drain_stderr(proc, 1)
    await proc.wait()
    assert "SECRET" not in last
    assert "SECRET" not in caplog.text
    assert "X-Plex-Token=**REDACTED**" in last


async def test_ffmpeg_check_timeout(tmp_path: Path) -> None:
    """An ffmpeg that hangs is treated as unsupported."""
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\nsleep 30\n")
    fake.chmod(0o755)
    with patch("custom_components.plex_stream_bridge.channel.FFMPEG_CHECK_TIMEOUT", 0.2):
        assert await async_check_ffmpeg(str(fake)) == "ffmpeg_unsupported"


def test_queue_helpers() -> None:
    """Full reader queues refuse data, and the end marker always gets through."""
    queue: asyncio.Queue[bytes | None] = asyncio.Queue(1)
    assert _try_put(queue, b"a")
    assert not _try_put(queue, b"b")
    _force_put(queue, None)
    assert queue.get_nowait() is None


async def test_play_exit_without_item(channel: Channel) -> None:
    """A feed ending after stop changes nothing."""
    channel._handle_play_exit(1, 1, "")
    assert channel.state is PlaybackState.IDLE
