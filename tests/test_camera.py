"""Tests for the Plex Stream Bridge camera and its stream endpoint."""

import asyncio
from pathlib import Path
import shutil
import subprocess
from unittest.mock import MagicMock, patch

import av
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.plex_stream_bridge.channel import PlaybackState
from homeassistant.components.camera import async_get_image, async_get_stream_source
from homeassistant.components.plex.models import PlexMediaSearchResult
from homeassistant.core import HomeAssistant

from .conftest import PLEX_URL, SERVER_ID, make_movie

CAMERA = "camera.plex_channel_1"
FFMPEG = shutil.which("ffmpeg")
needs_ffmpeg = pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")


async def test_stream_source(hass: HomeAssistant, init_integration: MockConfigEntry) -> None:
    """The camera points the stream worker at the channel feed."""
    channel = init_integration.runtime_data.channels[0]
    source = await async_get_stream_source(hass, CAMERA)
    assert source == (
        f"http://127.0.0.1:8123/api/plex_stream_bridge/{init_integration.entry_id}"
        f"/1/stream.ts?token={channel.access_token}"
    )


async def test_camera_image(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_payload: MagicMock,
    aioclient_mock: AiohttpClientMocker,
) -> None:
    """The still image is the playing item's artwork, cached per size."""
    mock_payload.return_value = PlexMediaSearchResult(make_movie())
    await hass.services.async_call(
        "media_player",
        "play_media",
        {
            "entity_id": "media_player.plex_channel_1",
            "media_content_type": "movie",
            "media_content_id": f"plex://{SERVER_ID}/1",
        },
        blocking=True,
    )
    url = f"{PLEX_URL}/photo?url=/library/metadata/1/art/1&w=1280&h=720"
    aioclient_mock.get(url, content=b"art", headers={"Content-Type": "image/jpeg"})

    assert (await async_get_image(hass, CAMERA)).content == b"art"
    assert (await async_get_image(hass, CAMERA)).content == b"art"
    assert (
        sum(str(call[1]).startswith(f"{PLEX_URL}/photo") for call in aioclient_mock.mock_calls) == 1
    )


@pytest.mark.parametrize(
    ("path", "status"),
    [
        ("missing-entry/1/stream.ts?token=x", 404),
        ("{entry_id}/9/stream.ts?token=x", 404),
        ("{entry_id}/1/stream.ts?token=wrong", 401),
        ("{entry_id}/1/stream.ts", 401),
    ],
)
async def test_stream_view_rejects(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    hass_client_no_auth,
    path: str,
    status: int,
) -> None:
    """The feed needs a known channel and its token."""
    client = await hass_client_no_auth()
    resp = await client.get(
        f"/api/plex_stream_bridge/{path.format(entry_id=init_integration.entry_id)}"
    )
    assert resp.status == status


@pytest.fixture
def hls_playlist(tmp_path: Path) -> str:
    """A 6 second H.264/AAC HLS stream with B-frames, like Plex's transcoder sends."""
    playlist = tmp_path / "index.m3u8"
    subprocess.run(
        [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc=size=320x180:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000",
            "-t",
            "6",
            "-c:v",
            "libx264",
            "-bf",
            "3",
            "-g",
            "25",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-f",
            "hls",
            "-hls_time",
            "2",
            "-hls_playlist_type",
            "vod",
            str(playlist),
        ],
        check=True,
    )
    return str(playlist)


async def _read(client, url: str, seconds: float, path: Path) -> tuple[float, bool]:
    """Read a feed for up to `seconds`; return elapsed time and whether it hit EOF."""
    loop = asyncio.get_running_loop()
    start = loop.time()
    eof = False
    async with client.get(url) as resp:
        assert resp.status == 200
        with path.open("wb") as file:
            while (remaining := seconds - (loop.time() - start)) > 0:
                try:
                    chunk = await asyncio.wait_for(resp.content.read(65536), remaining)
                except TimeoutError:
                    break
                if not chunk:
                    eof = True
                    break
                file.write(chunk)
    return loop.time() - start, eof


def _probe(path: Path | str) -> tuple[list[str], float, int]:
    """Codecs, video duration and B-frame count of a captured stream."""
    with av.open(str(path)) as container:
        codecs = sorted(stream.codec_context.name for stream in container.streams)
        frames = list(container.decode(video=0))
    pts = [float(frame.time) for frame in frames if frame.time is not None]
    b_frames = sum(frame.pict_type == av.video.frame.PictureType.B for frame in frames)
    return codecs, max(pts) - min(pts), b_frames


@needs_ffmpeg
async def test_feed_end_to_end(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_payload: MagicMock,
    hass_client_no_auth,
    hls_playlist: str,
    tmp_path: Path,
) -> None:
    """Slate when idle, real-time Plex video shared by readers, idle again at the end."""
    channel = init_integration.runtime_data.channels[0]
    channel._ffmpeg = FFMPEG
    assert _probe(hls_playlist)[2] > 0, "the stand-in Plex stream should have B-frames"
    client = await hass_client_no_auth()
    url = f"/api/plex_stream_bridge/{init_integration.entry_id}/1/stream.ts?token={channel.access_token}"

    _, eof = await _read(client, url, 2, tmp_path / "idle.ts")
    assert not eof
    assert _probe(tmp_path / "idle.ts")[0] == ["aac", "h264"]

    movie = make_movie()
    movie.duration = 6000
    mock_payload.return_value = PlexMediaSearchResult(movie)
    with patch.object(channel.bridge, "async_hls_url", return_value=hls_playlist) as hls_url:
        await hass.services.async_call(
            "media_player",
            "play_media",
            {
                "entity_id": "media_player.plex_channel_1",
                "media_content_type": "movie",
                "media_content_id": f"plex://{SERVER_ID}/1",
            },
            blocking=True,
        )
        (elapsed, _), _ = await asyncio.gather(
            _read(client, url, 3, tmp_path / "a.ts"),
            _read(client, url, 3, tmp_path / "b.ts"),
        )
        codecs, duration, b_frames = _probe(tmp_path / "a.ts")
        assert codecs == ["aac", "h264"]
        assert b_frames == 0, "WebRTC can't reorder B-frames, so the feed must not have any"
        assert duration < elapsed + 2, "feed must be paced to real time"
        assert hls_url.await_count == 1, "concurrent readers share one Plex session"

        elapsed, eof = await _read(client, url, 15, tmp_path / "rest.ts")
        assert eof
        await hass.async_block_till_done()

    assert channel.state is PlaybackState.IDLE
    assert hass.states.get("media_player.plex_channel_1").state == "idle"
