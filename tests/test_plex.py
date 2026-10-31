"""Tests for the bridge to the Plex integration."""

from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.plex_camera.plex import PlexBridge, PlexItem
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .conftest import PLEX_URL, SERVER_ID, make_movie

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
