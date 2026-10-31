# Plex Camera for Home Assistant

Watch Plex movies and TV inside a Home Assistant dashboard. Each **channel**
is a device with two entities:

- a **media player** you control with the standard media browser and
  play/pause/seek/stop controls, and
- a **camera** whose live stream is the video, so any card that shows a live
  camera (picture-entity, picture-glance, the more-info dialog) becomes the
  screen.

Plex Camera builds on the built-in [Plex integration](https://www.home-assistant.io/integrations/plex/):
it uses that integration's server connection, credentials, media browser and
`plex://` media IDs, the same way the Sonos and Google Cast integrations do.
It has no credentials of its own.

## Requirements

- Home Assistant 2026.8 or newer
- The Plex integration set up and loaded for your server
- `stream` and `ffmpeg` (both part of `default_config`)

## How it works

```
Plex Media Server ──HLS (H.264)──▶ ffmpeg -re ──MPEG-TS──▶ /api/plex_camera/… ──▶ HA stream / go2rtc ──▶ dashboard
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

## Install

1. Copy `custom_components/plex_camera` into your HA `config/custom_components/`
   folder (or add this repository to HACS as a custom repository).
2. Restart Home Assistant.
3. **Settings → Devices & services → Add integration → Plex Camera**, and pick
   your Plex server.
4. Optional: **Configure** to set the number of channels (1–4), maximum
   resolution and bitrate, or to turn off video re-encoding.

The channels appear as devices under your Plex server's device.

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

That costs about one CPU core on the Home Assistant host while someone is
watching a channel; nothing runs otherwise. (Measured on an Intel N95: ~100% of
one of its four cores, at 720p or 1080p alike, since most of it is decoding.)
Turn off **Re-encode video for smooth WebRTC playback** to pass Plex's video
through unchanged if you only watch over HLS or your media has no B-frames.

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
pip install pytest-homeassistant-custom-component ruff mypy
pytest
ruff check . && ruff format --check .
mypy --strict --implicit-reexport --allow-untyped-calls --ignore-missing-imports custom_components/plex_camera
```

The end-to-end feed test needs `ffmpeg` on the PATH; it is skipped otherwise.
