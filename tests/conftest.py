"""Fixtures for Plex Camera tests."""

from collections.abc import Generator
from unittest.mock import AsyncMock, MagicMock, patch

from plexapi.video import Episode, Movie, Show
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.plex_camera.const import (
    CONF_CLIENT_ID,
    CONF_SERVER_IDENTIFIER,
    DOMAIN,
    PLEX_DOMAIN,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

SERVER_ID = "abc123"
PLEX_URL = "http://plex.local:32400"


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Enable custom integrations in all tests."""


@pytest.fixture
def plex_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A Plex integration entry for SERVER_ID."""
    entry = MockConfigEntry(
        domain=PLEX_DOMAIN,
        unique_id=SERVER_ID,
        title="Living Room Plex",
        data={
            "server_id": SERVER_ID,
            "server_config": {"url": PLEX_URL, "token": "secret", "verify_ssl": True},
        },
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded_plex_entry(hass: HomeAssistant, plex_entry: MockConfigEntry) -> MockConfigEntry:
    """Load the Plex entry without talking to a server; add its server device."""
    with (
        patch("homeassistant.components.plex.async_setup_entry", return_value=True),
        patch("homeassistant.components.plex.async_unload_entry", return_value=True),
    ):
        assert await hass.config_entries.async_setup(plex_entry.entry_id)
        await hass.async_block_till_done()
        dr.async_get(hass).async_get_or_create(
            config_entry_id=plex_entry.entry_id, identifiers={(PLEX_DOMAIN, SERVER_ID)}
        )
        yield plex_entry
        await hass.config_entries.async_unload(plex_entry.entry_id)


@pytest.fixture
def plexapi_server() -> MagicMock:
    """The plexapi server behind the Plex integration."""
    server = MagicMock()
    server.transcodeImage.side_effect = lambda path, height, width, **_: (
        f"{PLEX_URL}/photo?url={path}&w={width}&h={height}"
    )
    server.url.side_effect = lambda key, includeToken=None: f"{PLEX_URL}{key}"
    return server


@pytest.fixture
def mock_get_plex_server(plexapi_server: MagicMock) -> Generator[MagicMock]:
    """Patch the Plex integration's server lookup."""
    ha_server = MagicMock(plex_server=plexapi_server)
    with patch(
        "custom_components.plex_camera.plex.get_plex_server", return_value=ha_server
    ) as mock:
        yield mock


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    """A Plex Camera entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=SERVER_ID,
        title="Living Room Plex",
        data={CONF_SERVER_IDENTIFIER: SERVER_ID, CONF_CLIENT_ID: "client-1"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
def mock_ffmpeg_check() -> Generator[AsyncMock]:
    """Treat ffmpeg as capable without running it."""
    with patch("custom_components.plex_camera.async_check_ffmpeg", return_value=None) as mock:
        yield mock


@pytest.fixture
async def init_integration(
    hass: HomeAssistant,
    loaded_plex_entry: MockConfigEntry,
    mock_get_plex_server: MagicMock,
    mock_ffmpeg_check: AsyncMock,
    config_entry: MockConfigEntry,
    aioclient_mock: AiohttpClientMocker,
) -> MockConfigEntry:
    """Set up Plex Camera on top of the Plex integration."""
    aioclient_mock.get(f"{PLEX_URL}/video/:/transcode/universal/stop")
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


def make_movie(rating_key: int = 1, title: str = "The Matrix") -> MagicMock:
    """A plexapi movie."""
    movie = MagicMock(spec=Movie)
    movie.TYPE = "movie"
    movie.ratingKey = rating_key
    movie.title = title
    movie.duration = 8_160_000
    movie.year = 1999
    movie.thumb = f"/library/metadata/{rating_key}/thumb/1"
    movie.art = f"/library/metadata/{rating_key}/art/1"
    movie.viewOffset = 600_000
    movie.isPlayed = False
    movie.getStreamURL = MagicMock(return_value=f"{PLEX_URL}/video/start.m3u8")
    return movie


def make_episode(rating_key: int = 11, index: int = 2) -> MagicMock:
    """A plexapi episode."""
    episode = MagicMock(spec=Episode)
    episode.TYPE = "episode"
    episode.ratingKey = rating_key
    episode.title = "Pilot"
    episode.duration = 1_320_000
    episode.year = 2005
    episode.grandparentTitle = "The Office"
    episode.parentIndex = 1
    episode.index = index
    episode.thumb = f"/library/metadata/{rating_key}/thumb/1"
    episode.art = None
    episode.grandparentArt = "/library/metadata/10/art/1"
    episode.isPlayed = False
    return episode


def make_show(on_deck: MagicMock | None) -> MagicMock:
    """A plexapi show."""
    show = MagicMock(spec=Show)
    show.TYPE = "show"
    show.onDeck.return_value = on_deck
    return show


@pytest.fixture
def mock_payload() -> Generator[MagicMock]:
    """Patch process_plex_payload."""
    with patch("custom_components.plex_camera.plex.process_plex_payload") as mock:
        yield mock


@pytest.fixture
def mock_browse() -> Generator[AsyncMock]:
    """Patch the Plex media browser."""
    with patch("homeassistant.components.plex.async_browse_media", new_callable=AsyncMock) as mock:
        yield mock
