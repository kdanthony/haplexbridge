"""Tests for the Plex Stream Bridge media player."""

from unittest.mock import AsyncMock, MagicMock

from plexapi.audio import Track
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from homeassistant.components.media_player import (
    ATTR_MEDIA_CONTENT_ID,
    ATTR_MEDIA_CONTENT_TYPE,
    ATTR_MEDIA_DURATION,
    ATTR_MEDIA_EPISODE,
    ATTR_MEDIA_POSITION,
    ATTR_MEDIA_SEASON,
    ATTR_MEDIA_SEEK_POSITION,
    ATTR_MEDIA_SERIES_TITLE,
    ATTR_MEDIA_TITLE,
    DOMAIN as MP_DOMAIN,
    SERVICE_PLAY_MEDIA,
    BrowseMedia,
    MediaClass,
)
from homeassistant.components.plex.models import PlexMediaSearchResult
from homeassistant.const import (
    ATTR_ENTITY_ID,
    SERVICE_MEDIA_PAUSE,
    SERVICE_MEDIA_PLAY,
    SERVICE_MEDIA_SEEK,
    SERVICE_MEDIA_STOP,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from .conftest import SERVER_ID, make_episode, make_movie, make_show

ENTITY_ID = "media_player.plex_channel_1"


async def _play(hass: HomeAssistant, media_id: str, media_type: str = "movie") -> None:
    await hass.services.async_call(
        MP_DOMAIN,
        SERVICE_PLAY_MEDIA,
        {
            ATTR_ENTITY_ID: ENTITY_ID,
            ATTR_MEDIA_CONTENT_TYPE: media_type,
            ATTR_MEDIA_CONTENT_ID: media_id,
        },
        blocking=True,
    )


async def _call(hass: HomeAssistant, service: str, **data: object) -> None:
    await hass.services.async_call(
        MP_DOMAIN, service, {ATTR_ENTITY_ID: ENTITY_ID, **data}, blocking=True
    )


@pytest.mark.usefixtures("init_integration")
async def test_play_movie(hass: HomeAssistant, mock_payload: MagicMock) -> None:
    """Playing a plex:// ID resolves it through the Plex integration."""
    mock_payload.return_value = PlexMediaSearchResult(make_movie(), {"offset": 90})

    await _play(hass, f"plex://{SERVER_ID}/1")

    _, media_type, media_id = mock_payload.call_args.args
    assert (media_type, media_id) == ("movie", f"plex://{SERVER_ID}/1")
    assert mock_payload.call_args.kwargs["supports_playqueues"] is False

    state = hass.states.get(ENTITY_ID)
    assert state.state == "playing"
    assert state.attributes[ATTR_MEDIA_TITLE] == "The Matrix"
    assert state.attributes[ATTR_MEDIA_CONTENT_ID] == f"plex://{SERVER_ID}/1"
    assert state.attributes[ATTR_MEDIA_CONTENT_TYPE] == "movie"
    assert state.attributes[ATTR_MEDIA_DURATION] == 8160
    assert state.attributes[ATTR_MEDIA_POSITION] == 90
    assert state.attributes["entity_picture"].startswith("/api/media_player_proxy/")
    assert hass.states.get("camera.plex_channel_1").state == "streaming"


@pytest.mark.usefixtures("init_integration")
async def test_play_show_uses_on_deck(hass: HomeAssistant, mock_payload: MagicMock) -> None:
    """A show plays its On Deck episode."""
    mock_payload.return_value = PlexMediaSearchResult(make_show(make_episode()))

    await _play(hass, f"plex://{SERVER_ID}/10", "show")

    state = hass.states.get(ENTITY_ID)
    assert state.attributes[ATTR_MEDIA_SERIES_TITLE] == "The Office"
    assert state.attributes[ATTR_MEDIA_SEASON] == "1"
    assert state.attributes[ATTR_MEDIA_EPISODE] == "2"
    assert state.attributes[ATTR_MEDIA_CONTENT_TYPE] == "episode"


@pytest.mark.usefixtures("init_integration")
async def test_play_list_prefers_unwatched(hass: HomeAssistant, mock_payload: MagicMock) -> None:
    """From a list of episodes, the first unwatched one plays."""
    watched, unwatched = make_episode(11, 1), make_episode(12, 2)
    watched.isPlayed = True
    mock_payload.return_value = PlexMediaSearchResult([watched, unwatched])

    await _play(hass, f"plex://{SERVER_ID}/20", "season")

    assert hass.states.get(ENTITY_ID).attributes[ATTR_MEDIA_EPISODE] == "2"


@pytest.mark.usefixtures("init_integration")
async def test_play_non_plex_media(hass: HomeAssistant) -> None:
    """Only Plex media can be played."""
    with pytest.raises(ServiceValidationError) as err:
        await _play(hass, "media-source://media_source/local/video.mp4")
    assert err.value.translation_key == "not_plex_media"


@pytest.mark.usefixtures("init_integration")
async def test_play_music_rejected(hass: HomeAssistant, mock_payload: MagicMock) -> None:
    """Audio can't be shown on a camera."""
    track = MagicMock(spec=Track)
    track.TYPE = "track"
    mock_payload.return_value = PlexMediaSearchResult(track)

    with pytest.raises(HomeAssistantError) as err:
        await _play(hass, f"plex://{SERVER_ID}/30", "music")
    assert err.value.translation_key == "unsupported_media"
    assert hass.states.get(ENTITY_ID).state == "idle"


@pytest.mark.usefixtures("init_integration")
async def test_transport_controls(hass: HomeAssistant, mock_payload: MagicMock) -> None:
    """Pause, play, seek and stop drive the channel."""
    mock_payload.return_value = PlexMediaSearchResult(make_movie())
    await _play(hass, f"plex://{SERVER_ID}/1")

    await _call(hass, SERVICE_MEDIA_PAUSE)
    assert hass.states.get(ENTITY_ID).state == "paused"
    assert hass.states.get("camera.plex_channel_1").state == "idle"

    await _call(hass, SERVICE_MEDIA_SEEK, **{ATTR_MEDIA_SEEK_POSITION: 1200})
    state = hass.states.get(ENTITY_ID)
    assert state.state == "paused"
    assert state.attributes[ATTR_MEDIA_POSITION] == 1200

    await _call(hass, SERVICE_MEDIA_PLAY)
    assert hass.states.get(ENTITY_ID).state == "playing"

    await _call(hass, SERVICE_MEDIA_STOP)
    state = hass.states.get(ENTITY_ID)
    assert state.state == "idle"
    assert ATTR_MEDIA_TITLE not in state.attributes


async def test_browse_media(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_browse: AsyncMock,
    hass_ws_client,
) -> None:
    """Browsing delegates to the Plex integration's media browser."""
    mock_browse.return_value = BrowseMedia(
        title="Plex",
        media_class=MediaClass.DIRECTORY,
        media_content_id="",
        media_content_type="server",
        can_play=False,
        can_expand=True,
    )
    client = await hass_ws_client(hass)
    await client.send_json_auto_id({"type": "media_player/browse_media", "entity_id": ENTITY_ID})
    response = await client.receive_json()
    assert response["success"]
    assert response["result"]["title"] == "Plex"
    mock_browse.assert_awaited_once_with(hass, None, None, platform="plex_stream_bridge")
