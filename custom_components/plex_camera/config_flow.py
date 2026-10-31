"""Config and options flow for Plex Camera."""

from __future__ import annotations

from typing import Any
import uuid

import probatio

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .const import (
    CONF_CHANNELS,
    CONF_CLIENT_ID,
    CONF_LOCAL_URL,
    CONF_MAX_BITRATE,
    CONF_MAX_RESOLUTION,
    CONF_REENCODE_VIDEO,
    CONF_SERVER_IDENTIFIER,
    DEFAULT_CHANNELS,
    DEFAULT_MAX_BITRATE,
    DEFAULT_MAX_RESOLUTION,
    DEFAULT_REENCODE_VIDEO,
    DOMAIN,
    PLEX_DOMAIN,
    RESOLUTIONS,
)

OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_CHANNELS, default=DEFAULT_CHANNELS): probatio.All(
            NumberSelector(NumberSelectorConfig(min=1, max=4, mode=NumberSelectorMode.BOX)),
            probatio.Coerce(int),
        ),
        probatio.Required(CONF_MAX_RESOLUTION, default=DEFAULT_MAX_RESOLUTION): SelectSelector(
            SelectSelectorConfig(options=RESOLUTIONS, translation_key=CONF_MAX_RESOLUTION)
        ),
        probatio.Required(CONF_MAX_BITRATE, default=DEFAULT_MAX_BITRATE): probatio.All(
            NumberSelector(
                NumberSelectorConfig(
                    min=500,
                    max=40000,
                    step=500,
                    unit_of_measurement="kbps",
                    mode=NumberSelectorMode.BOX,
                )
            ),
            probatio.Coerce(int),
        ),
        probatio.Required(CONF_REENCODE_VIDEO, default=DEFAULT_REENCODE_VIDEO): BooleanSelector(),
        probatio.Optional(CONF_LOCAL_URL): TextSelector(
            TextSelectorConfig(type=TextSelectorType.URL)
        ),
    }
)


class PlexCameraConfigFlow(ConfigFlow, domain=DOMAIN):
    """Add channels to a server already set up in the Plex integration."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Pick one of the Plex integration's servers."""
        configured = self._async_current_ids(include_ignore=False)
        servers = {
            entry.unique_id: entry.title
            for entry in self.hass.config_entries.async_entries(PLEX_DOMAIN)
            if entry.state is ConfigEntryState.LOADED
            and entry.unique_id
            and entry.unique_id not in configured
        }
        if not servers:
            return self.async_abort(reason="no_plex_servers")

        if user_input is not None:
            server_id = user_input[CONF_SERVER_IDENTIFIER]
            await self.async_set_unique_id(server_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=servers[server_id],
                data={CONF_SERVER_IDENTIFIER: server_id, CONF_CLIENT_ID: uuid.uuid4().hex},
            )

        return self.async_show_form(
            step_id="user",
            data_schema=probatio.Schema(
                {
                    probatio.Required(CONF_SERVER_IDENTIFIER): SelectSelector(
                        SelectSelectorConfig(
                            options=[
                                SelectOptionDict(value=server_id, label=title)
                                for server_id, title in servers.items()
                            ]
                        )
                    )
                }
            ),
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> PlexCameraOptionsFlow:
        """Get the options flow."""
        return PlexCameraOptionsFlow()


class PlexCameraOptionsFlow(OptionsFlowWithReload):
    """Tune channel count and stream quality."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        """Show the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, self.config_entry.options
            ),
        )
