# Plex Stream Bridge

Home Assistant custom integration (domain `plex_stream_bridge`) that plays Plex
movies and episodes as live camera streams so they can be watched on a
dashboard. Each **channel** is a device ("Plex channel N") with a
`media_player` (control) and a `camera` (the video). The user-facing overview is
in `README.md`; this file covers how the code works and why.

Write code as a Home Assistant core developer would: follow core's current
patterns (see "HA 2026 APIs" below) and don't reintroduce deprecated APIs.

## Layout

```
custom_components/plex_stream_bridge/
  __init__.py      setup/unload; PlexStreamBridgeData runtime data; ffmpeg check; stream URL base
  config_flow.py   pick a loaded Plex integration server; options flow (OptionsFlowWithReload)
  plex.py          PlexBridge: everything that touches Plex, via the Plex integration
  channel.py       Channel: playback state + ffmpeg feed + fan-out; async_check_ffmpeg
  view.py          /api/plex_stream_bridge/<entry_id>/<n>/stream.ts?token=... (requires_auth=False)
  entity.py        PlexChannelEntity base (device info, follows channel state)
  camera.py        PlexChannelCamera: stream_source -> the view; still image = artwork
  media_player.py  PlexChannelMediaPlayer: browse/play_media/play/pause/stop/seek
tests/             pytest-homeassistant-custom-component suite
```

## How it works

- **No Plex credentials of its own.** The config entry stores only the Plex
  server's machine id (`server_id`, the Plex entry's unique_id) and a generated
  `client_identifier`. `PlexBridge` looks up the Plex integration's server on
  each use (`get_plex_server`), so it survives Plex reloads.
- **Cross-integration API**: only the helpers Sonos/Cast use:
  `homeassistant.components.plex.services.process_plex_payload` (called with
  `supports_playqueues=False`, in the executor), `plex.async_browse_media`, and
  `plex.is_plex_media_id`. `process_plex_payload` already handles `plex://`
  IDs, search payloads, `resume` and `offset`.
- **Item resolution** (`_pick_video`): movies/episodes play directly; shows and
  seasons play `onDeck()` or the first unwatched episode; music is rejected
  (`unsupported_media`).
- **Channel = TV channel.** Position is wall-clock based (`_anchor_position` +
  monotonic time) and advances while nobody watches. Nothing runs without a
  reader; the next reader resumes at the current position.
- **Feed** (`Channel._run_feed`): one ffmpeg per channel with `-re` reading
  Plex's HLS (`getStreamURL`), output MPEG-TS to stdout, fanned out to every
  HTTP reader (HA stream worker, go2rtc, probes) in 188-byte-aligned chunks.
  Each feed owns its reader set; play/pause/seek/stop end the feed
  (`_restart_feed`) and readers reconnect, because a new source restarts
  timestamps, which HA's stream worker can't splice. HA's stream worker waits
  ~10 s before reconnecting; that's the known switch delay.
- **Slates**: idle = black (lavfi color + anullsrc), paused = the item's art
  looped. Both are encoded with libx264, so the stream never errors and backs
  off.
- **End and failure handling** (`_handle_play_exit`): exit 0 or near the end →
  idle; three failures within 15 s each → idle with an error log. Kills we
  request (`_kill_requested`) don't count as failures.

## Hard-won facts (verified on real hardware, don't "simplify" away)

- **HA plays cameras over WebRTC via go2rtc by default**, and there's no
  supported per-camera opt-out (go2rtc accepts any http/https stream source).
  go2rtc converts AAC audio to Opus itself, so audio works.
- **WebRTC can't play H.264 B-frames** (out-of-order frames = heavy jitter), and
  **Plex emits B-frames** (H.264 Main) for every client profile tried; profile
  extras and `directStream=0` don't change that. Hence `reencode_video`
  (default on): libx264 `ultrafast`, `zerolatency`, `-bf 0`, CRF 23 capped at
  max bitrate, keyframe every 2 s. Measured cost on an Intel N95: about 20–36%
  of one core in normal playback, up to ~100% for heavy 1080p sources.
- **Plex returns 400 on `start.m3u8` unless `/video/:/transcode/universal/decision`
  is requested first** with the same params. `PlexBridge.async_hls_url` always
  does that; keep it.
- The stop-transcode call can 404 for sessions that already ended; ignore it.
- `X-Plex-Product=Plex Web` + platform `Chrome` is the profile that yields
  H.264 HLS; Xbox/Tizen profiles return 400.
- ffmpeg is checked at setup with a one-frame lavfi→libx264 encode
  (`async_check_ffmpeg`): `ffmpeg_missing` / `ffmpeg_unsupported` raise
  `ConfigEntryError`. HA's ffmpeg dependency doesn't guarantee either.
- Stream URL base: `local_url` option, else `hass.config.api.host` (wildcard →
  `127.0.0.1`, IPv6 bracketed) when not using SSL, else the internal URL.

## HA 2026 APIs (the test harness turns deprecations into errors)

- Config/options flow schemas use **`probatio`**, not voluptuous.
- `DeviceInfo` takes **`via_device_id`** (a device registry id), not
  `via_device`; only set it when the Plex server device exists.
- Look up devices with
  **`async_get_device_by_identifier(identifier, config_entry_id)`**, not
  `async_get_device`, and don't use `device_registry.devices` as a mapping.
- Use `entry.runtime_data` (typed `PlexStreamBridgeConfigEntry`), not
  `hass.data`; `OptionsFlowWithReload` instead of an update listener;
  translated exceptions (`translation_domain`/`translation_key`); device names
  via `translation_key` + placeholders.
- Keep `strings.json` and `translations/en.json` identical (copy after edits).
- Minimum HA version is 2026.8 (`hacs.json`).

## Development

Python 3.14. Use a venv **outside the repo** with the harness pinned to the
target HA version (an unpinned install resolves an old harness that fails to
build `lru-dict`):

```bash
uv venv -p 3.14 ../.venv-psb && uv pip install --python ../.venv-psb/bin/python \
  "pytest-homeassistant-custom-component==0.13.375" "lru-dict>=1.4.1" \
  ha-ffmpeg av numpy PyTurboJPEG PlexAPI==4.18.2 plexauth==0.0.6 plexwebsocket==0.0.14 ruff mypy
```

Before calling any change done, all of these must pass:

```bash
../.venv-psb/bin/python -m pytest -q
../.venv-psb/bin/ruff check . && ../.venv-psb/bin/ruff format --check .
../.venv-psb/bin/mypy --strict --implicit-reexport --allow-untyped-calls \
  --ignore-missing-imports --follow-imports=silent custom_components/plex_stream_bridge
```

Test notes:
- `tests/conftest.py` loads a mock Plex entry with Plex's setup patched out,
  patches `get_plex_server` / `process_plex_payload`, and patches the ffmpeg
  check (`mock_ffmpeg_check`) for setup tests.
- Fixtures that create an aiohttp session must depend on `aioclient_mock`, or
  the session is created before the mock and makes real requests.
- `test_camera.py::test_feed_end_to_end` runs real ffmpeg against a local HLS
  file with B-frames and asserts the feed has none and plays at real time;
  it's skipped without ffmpeg.

## Releasing changes

- Bump `version` in `manifest.json` and `pyproject.toml` together.
- Changing the domain or unique ids is breaking (remove and re-add the entry).
- Deploy = copy `custom_components/plex_stream_bridge` into the HA config's
  `custom_components/`, run a config check, restart HA (Python changes need a
  restart), then wait for the Plex entry to load. This integration depends on
  Plex and waits for it.
- Don't commit or push unless asked; scan diffs for Plex tokens and local
  hostnames first.

@CLAUDE.local.md
