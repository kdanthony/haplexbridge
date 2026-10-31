"""Tests for the bridge to the Plex integration."""

from unittest.mock import MagicMock

import aiohttp
from plexapi.exceptions import NotFound
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.plex_stream_bridge.plex import (
    PlexBridge,
    PlexItem,
    _pick_video,
    redact_token,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import PLEX_URL, SERVER_ID, make_episode, make_movie, make_show

START_URL = f"{PLEX_URL}/video/:/transcode/universal/start.m3u8?path=/library/metadata/1&session=s1"
DECISION_URL = f"{PLEX_URL}/video/:/transcode/universal/decision"


@pytest.fixture
def item() -> PlexItem:
    movie = make_movie()
    movie.getStreamURL.return_value = START_URL
    return PlexItem.from_video(SERVER_ID, movie)


@pytest.fixture
async def bridge(
    hass: HomeAssistant, mock_get_plex_server: MagicMock, aioclient_mock: AiohttpClientMocker
) -> PlexBridge:
    return PlexBridge(hass, SERVER_ID, "client-1", async_get_clientsession(hass))


async def test_hls_url_requests_decision_first(
    bridge: PlexBridge, item: PlexItem, aioclient_mock: AiohttpClientMocker
) -> None:
    """Plex gets a transcode decision request for the same session before start."""
    aioclient_mock.get(DECISION_URL, json={"MediaContainer": {}})

    url = await bridge.async_hls_url(item, "s1", 90, "1920x1080", 8000)

    assert url == START_URL
    assert aioclient_mock.call_count == 1
    _, decision_url, _, _ = aioclient_mock.mock_calls[0]
    assert decision_url.query["session"] == "s1"
    params = item.video.getStreamURL.call_args.kwargs
    assert params["offset"] == 90
    assert params["protocol"] == "hls"
    assert params["X-Plex-Client-Identifier"] == "client-1"


async def test_hls_url_decision_refused(
    bridge: PlexBridge, item: PlexItem, aioclient_mock: AiohttpClientMocker
) -> None:
    """A refused decision fails the feed instead of starting a doomed transcode."""
    aioclient_mock.get(DECISION_URL, status=400)

    with pytest.raises(HomeAssistantError) as err:
        await bridge.async_hls_url(item, "s1", 0, "1920x1080", 8000)
    assert err.value.translation_key == "transcode_refused"


def test_redact_token() -> None:
    """Tokens are masked wherever they appear in a URL."""
    text = "Error opening input file https://plex/x.m3u8?a=1&X-Plex-Token=abc123&b=2."
    assert redact_token(text) == (
        "Error opening input file https://plex/x.m3u8?a=1&X-Plex-Token=**REDACTED**&b=2."
    )


def test_pick_video_rules() -> None:
    """Lists, shows and seasons reduce to one playable video."""
    with pytest.raises(HomeAssistantError):
        _pick_video([])
    episodes = [make_episode(11, 1), make_episode(12, 2)]
    show = make_show(None)
    show.episodes.return_value = episodes
    assert _pick_video(show) is episodes[0]
    with pytest.raises(HomeAssistantError):
        _pick_video(object())


async def test_server_unavailable(hass: HomeAssistant, mock_get_plex_server: MagicMock) -> None:
    """A Plex server missing from the Plex integration is reported clearly."""
    mock_get_plex_server.side_effect = KeyError(SERVER_ID)
    bridge = PlexBridge(hass, SERVER_ID, "client-1", MagicMock())
    with pytest.raises(HomeAssistantError) as err:
        await bridge.async_resolve("movie", f"plex://{SERVER_ID}/1")
    assert err.value.translation_key == "server_unavailable"


async def test_lookup_failed(
    hass: HomeAssistant, bridge: PlexBridge, mock_payload: MagicMock
) -> None:
    """plexapi errors while resolving media become a translated error."""
    mock_payload.side_effect = NotFound("gone")
    with pytest.raises(HomeAssistantError) as err:
        await bridge.async_resolve("movie", f"plex://{SERVER_ID}/1")
    assert err.value.translation_key == "lookup_failed"


async def test_decision_unreachable(
    bridge: PlexBridge, item: PlexItem, aioclient_mock: AiohttpClientMocker
) -> None:
    """A Plex server that can't be reached fails the feed."""
    aioclient_mock.get(DECISION_URL, exc=aiohttp.ClientError())
    with pytest.raises(HomeAssistantError) as err:
        await bridge.async_hls_url(item, "s1", 0, "1920x1080", 8000)
    assert err.value.translation_key == "server_unavailable"


async def test_image_failures(bridge: PlexBridge, aioclient_mock: AiohttpClientMocker) -> None:
    """Image fetch failures return None instead of raising."""
    url = f"{PLEX_URL}/photo"
    aioclient_mock.get(url, status=404)
    assert await bridge.async_image("/art", 10, 10) is None
    aioclient_mock.clear_requests()
    aioclient_mock.get(url, exc=aiohttp.ClientError())
    assert await bridge.async_image("/art", 10, 10) is None


async def test_stop_transcode_errors_ignored(
    bridge: PlexBridge, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    """Stopping a transcode is best effort and never logs the token."""
    aioclient_mock.get(
        f"{PLEX_URL}/video/:/transcode/universal/stop", exc=aiohttp.ClientError("X-Plex-Token=abc")
    )
    caplog.set_level("DEBUG")
    await bridge.async_stop_transcode("s1")
    assert "abc" not in caplog.text
