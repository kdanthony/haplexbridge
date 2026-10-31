# Plex Stream Bridge for Home Assistant

> [!NOTE]
> **AI-generated.** The code, tests and documentation in this repository were
> written by an AI model (Anthropic's Claude, using Claude Code). The maintainer
> directed the work and tested it on a real Home Assistant installation, but the
> code hasn't had an independent human review. Read it before relying on it, and
> report problems as issues.

Watch Plex movies and TV inside a Home Assistant dashboard. Each **channel**
is a device with two entities:

- a **media player** you control with the standard media browser and
  play/pause/seek/stop controls, and
- a **camera** whose live stream is the video, so any card that shows a live
  camera (picture-entity, picture-glance, the more-info dialog) becomes the
  screen.

Plex Stream Bridge builds on the built-in [Plex integration](https://www.home-assistant.io/integrations/plex/):
it uses that integration's server connection, credentials, media browser and
`plex://` media IDs, the same way the Sonos and Google Cast integrations do.
It has no credentials of its own.

## Requirements

- Home Assistant 2026.8 or newer
- The Plex integration set up and loaded for your server
- `stream` and `ffmpeg` (both part of `default_config`)
- An ffmpeg binary with the **libx264** encoder
- A Plex server that can transcode (software transcoding is free; hardware
  transcoding needs Plex Pass)

### Install types

| Install | Works out of the box? |
| --- | --- |
| Home Assistant OS, Supervised, Container | Yes. The official image ships ffmpeg with libx264, and go2rtc. |
| Home Assistant Core (Python venv) | Install ffmpeg yourself, with libx264. Some distribution builds (such as Fedora's `ffmpeg-free`) leave it out. Without go2rtc, cameras play over HLS, so you can turn off video re-encoding. |

Setup checks ffmpeg with a one-frame test encode and fails with an explanation
if it's missing or lacks libx264. A non-default binary is set with `ffmpeg_bin`
in the [ffmpeg configuration](https://www.home-assistant.io/integrations/ffmpeg/).

### Hardware

Re-encoding (on by default, see below) takes about a third to one core of an
Intel N95 while a channel is watched. Low-power ARM boards such as a Raspberry
Pi 4 or Home Assistant Green/Yellow will likely struggle to re-encode 1080p in
real time; lower the maximum resolution to 720p or 480p, or turn re-encoding
off if you watch over HLS. This is an estimate, not a measurement.

### Networking

HA's stream worker and go2rtc read each channel's feed from Home Assistant
itself, on the address it listens on: `127.0.0.1` by default, or the first
`server_host` if you've set one. If HA serves HTTPS directly, the internal URL is
used instead. If neither works for your setup, set **Home Assistant URL for the
stream worker** in the options.

## How it works

```
Plex Media Server ──HLS (H.264)──▶ ffmpeg -re ──MPEG-TS──▶ /api/plex_stream_bridge/… ──▶ HA stream / go2rtc ──▶ dashboard
  (transcodes)                     (paces to               (one feed shared            (HLS / WebRTC)
                                    real time)              by every reader)
```

- Plex's universal transcoder turns the item into H.264 HLS.
- HA's stream worker reads sources as fast as it can, which would race through
  a video file. Each channel runs one ffmpeg process with `-re` so the feed plays
  at real time and re-encodes the video without B-frames (see below) and the
  audio to AAC. It serves the result from a local endpoint protected by a
  per-channel token, which the camera reports as its `stream_source`.
- A channel behaves like TV: once something plays, its position advances with
  the clock. Nothing runs and Plex isn't transcoding while nobody is watching.
  When you open the dashboard again, the feed resumes at the current position.
- An idle channel shows a black slate and a paused channel shows the item's
  artwork, so the camera stream never errors out and backs off.

### How data updates

Nothing is polled.

- **Channel state** (what's playing, playing/paused/idle, position) lives in
  Home Assistant and changes only when you act on a channel or an item ends,
  so the media player and camera update immediately. The position is reported
  with a timestamp and the frontend advances it on its own.
- **Plex is contacted on demand**: when you browse, when you play something
  (to look it up), when a feed starts (to start a transcode), and for artwork,
  which is fetched once per item and size and cached.
- **Availability** follows the Plex integration: channels are unavailable
  whenever it doesn't have the server loaded, with no extra connection checks.
- **Video** is only fetched while someone is watching a channel.

## Use cases

- **A TV on any dashboard.** Put a channel on a wall tablet or kiosk display
  and play something without a smart TV or Plex client in that room.
- **Picture-in-picture while cooking or working.** Keep a show running in a
  corner of your usual dashboard.
- **Scheduled viewing.** Start the next episode of a show at a set time, for
  example cartoons in the kids' room after school.
- **React to the house.** Pause when the doorbell rings or someone comes in,
  and resume afterwards.

## Install

### With HACS (recommended)

1. In Home Assistant, open **HACS → ⋮ → Custom repositories**.
2. Add `https://github.com/kdanthony/haplexbridge` with the type **Integration**.
3. Find **Plex Stream Bridge** in HACS, select **Download**, and restart Home
   Assistant.
4. **Settings → Devices & services → Add integration → Plex Stream Bridge**, and
   pick your Plex server.

HACS offers updates when a new release is published.

### Manually

1. Copy `custom_components/plex_stream_bridge` into your Home Assistant
   `config/custom_components/` folder.
2. Restart Home Assistant, then add the integration as in step 4 above.

### After installing

Optional: open the integration and select **Configure** to change the options
below.

The channels appear as devices under your Plex server's device. Each channel is
available while the Plex integration has the server loaded, and unavailable
otherwise.

### Options

| Option | Default | What it does |
| --- | --- | --- |
| Number of channels | 1 | Channels (1–4). Each is a camera and a media player that play one video at a time. Lowering it removes the extra channels' devices and entities. |
| Maximum resolution | 1080p | The largest size Plex sends (480p, 720p, 1080p). |
| Maximum video bitrate | 8000 kbps | Caps both Plex's output and the re-encode. |
| Re-encode video for smooth WebRTC playback | On | See [Smooth playback and CPU use](#smooth-playback-and-cpu-use). |
| Home Assistant URL for the stream worker | (empty) | Only needed when HA can't reach itself at the address it listens on (see [Networking](#networking)). |

## Remove

1. **Settings → Devices & services → Plex Stream Bridge → ⋮ → Delete.** This
   removes the channels' devices and entities; nothing in Plex changes.
2. To uninstall, remove it in HACS (or delete `custom_components/plex_stream_bridge`)
   and restart Home Assistant.

## Playing something

- **From the UI:** open `media_player.plex_channel_1` and use **Browse media**.
- **From automations:** use `media_player.play_media` with any `plex://` media
  ID the Plex integration accepts, including its search payloads:

```yaml
action: media_player.play_media
target:
  entity_id: media_player.plex_channel_1
data:
  media_content_type: movie
  media_content_id: 'plex://{"library_name": "Movies", "title": "The Matrix", "resume": 1}'
```

Shows and seasons play their On Deck (next unwatched) episode. Add `"resume": 1`
to continue from Plex's saved position, or `"offset": <seconds>` to start at a
given point. Music isn't supported.

Pause, play, seek and stop are the standard `media_player` actions.

### Automation examples

Play the next unwatched episode of a show on weekdays at 16:00:

```yaml
triggers:
  - trigger: time
    at: "16:00:00"
    weekday: [mon, tue, wed, thu, fri]
actions:
  - action: media_player.play_media
    target:
      entity_id: media_player.plex_channel_1
    data:
      media_content_type: show
      media_content_id: 'plex://{"library_name": "Kids TV", "show.title": "Bluey"}'
```

Pause while the doorbell rings, then resume a minute later:

```yaml
triggers:
  - trigger: state
    entity_id: binary_sensor.doorbell
    to: "on"
conditions:
  - condition: state
    entity_id: media_player.plex_channel_1
    state: playing
actions:
  - action: media_player.media_pause
    target:
      entity_id: media_player.plex_channel_1
  - delay: "00:01:00"
  - action: media_player.media_play
    target:
      entity_id: media_player.plex_channel_1
```

## Dashboard example

```yaml
type: vertical-stack
cards:
  - type: picture-entity
    entity: camera.plex_channel_1
    camera_view: live
    show_name: false
    show_state: false
  - type: media-control
    entity: media_player.plex_channel_1
```

## Smooth playback and CPU use

Camera cards play over WebRTC (via go2rtc) when they can. Browsers can't
reorder H.264 B-frames over WebRTC, and Plex passes them through, which makes
the picture stutter and jump. So by default each channel re-encodes the video
with libx264 (`ultrafast`, no B-frames, a keyframe every 2 seconds, capped at
your maximum bitrate).

That costs some CPU on the Home Assistant host while someone is watching a
channel; nothing runs otherwise. Measured on an Intel N95: 20–36% of one of its
four cores for typical TV and home video, up to about one core for demanding
1080p sources.
Turn off **Re-encode video for smooth WebRTC playback** to pass Plex's video
through unchanged if you only watch over HLS or your media has no B-frames.

## Troubleshooting

- **Setup fails with an ffmpeg error.** The message says whether ffmpeg is
  missing or can't encode with libx264; see [Install types](#install-types).
- **Setup keeps retrying.** The Plex integration hasn't finished loading the
  server yet; this resolves itself once Plex is loaded (up to a minute after a
  restart).
- **Channels are unavailable.** The Plex integration doesn't have the server
  loaded. Check the Plex integration first.
- **The camera shows nothing or keeps reconnecting.** Turn on debug logging for
  Plex Stream Bridge and open the camera: the log shows the feed starting and
  any error from Plex or ffmpeg (Plex tokens are masked). A refused transcode
  usually means the Plex server is busy or can't transcode the file.
- **The picture stutters or jumps.** Make sure **Re-encode video for smooth
  WebRTC playback** is on.
- **Live-action looks soft.** Check the file's resolution in Plex; standard
  definition looks soft when stretched to a large card, more so than animation.
- **Diagnostics.** **⋮ → Download diagnostics** on the integration gives channel
  state with identifiers redacted; it never contains tokens or Plex URLs.

## Security

- Plex Stream Bridge has no credentials of its own; it uses the Plex
  integration's connection.
- Each channel's feed is served at `/api/plex_stream_bridge/…` without Home
  Assistant login, because HA's stream worker and go2rtc can't send it. Access
  needs that channel's random 256-bit token, which is regenerated on every
  restart and only given to HA's own stream components. It's passed in a
  parameter HA masks when it logs stream URLs.
- ffmpeg connects to Plex with TLS verification whenever the Plex integration
  verifies SSL, and Plex inputs are limited to network protocols.
- ffmpeg receives the Plex stream URL, which includes your Plex token, on its
  command line. Other processes that can see Home Assistant's process list
  could read it while a channel plays. On Home Assistant OS that means only
  add-ons granted host process access.

## Limitations

- **Changes take a few seconds.** Play, pause, seek and stop end the current
  feed so the camera stream reconnects to the new content. HA's stream worker
  waits about 10 seconds before reconnecting.
- **Live stream, not a player.** HLS adds a few seconds of latency, and the
  camera card has no scrub bar; use the media player's seek.
- **Multiple Plex servers.** The media browser shows the Plex integration's
  first server, as it does for Sonos and Cast.
- Watch progress isn't reported back to Plex.

## Development

```bash
pip install -r requirements_test.txt
pytest --cov=custom_components/plex_stream_bridge
ruff check . && ruff format --check .
mypy --strict --implicit-reexport --allow-untyped-calls --ignore-missing-imports custom_components/plex_stream_bridge
```

The end-to-end feed test needs `ffmpeg` on the PATH; it is skipped otherwise.
GitHub Actions run the same checks (`tests.yml`), plus HACS validation and
hassfest (`validate.yml`), on every push.

### Releasing

1. Bump `version` in `custom_components/plex_stream_bridge/manifest.json` (and
   `pyproject.toml`).
2. Commit, push, and publish a GitHub release whose tag is that version with a
   `v` prefix (for example `v0.4.0`). HACS offers the five latest releases.

## License

Licensed under the [Apache License 2.0](LICENSE), the same license as Home
Assistant. As noted at the top, this project's code was AI-generated.
