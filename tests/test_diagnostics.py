"""Tests for Plex Stream Bridge diagnostics."""

import json
from unittest.mock import MagicMock

from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)

from homeassistant.components.plex.models import PlexMediaSearchResult
from homeassistant.core import HomeAssistant

from .conftest import SERVER_ID, make_movie


async def test_diagnostics(
    hass: HomeAssistant,
    init_integration: MockConfigEntry,
    mock_payload: MagicMock,
    hass_client,
) -> None:
    """Diagnostics describe the channels without leaking tokens."""
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

    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, init_integration)

    assert diagnostics["entry"]["data"] == {
        "server_id": SERVER_ID,
        "client_identifier": "**REDACTED**",
    }
    assert diagnostics["plex_available"] is True
    channel = diagnostics["channels"][0]
    assert channel["state"] == "playing"
    assert channel["item"] == {"rating_key": "1", "media_type": "movie", "duration": 8160}
    assert channel["verify_tls"] is True
    access_token = init_integration.runtime_data.channels[0].access_token
    assert access_token not in json.dumps(diagnostics)
