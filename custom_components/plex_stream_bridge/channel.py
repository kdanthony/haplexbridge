"""
A virtual TV channel that turns Plex playback into a live MPEG-TS feed.

Home Assistant's stream component reads a camera source as fast as it can and
keeps only the last few segments, so pointing it straight at a Plex HLS URL
would skip through the video. Each channel therefore runs one ffmpeg process
with ``-re`` that paces the Plex transcode at real time and fans the resulting
MPEG-TS out to every connected reader (HA's stream worker, go2rtc, ...).

When nothing is playing the channel serves a cheap black slate (or the item's
artwork while paused) so the stream never errors out and backs off.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from enum import StrEnum
import logging
import secrets
import time
import uuid

from aiohttp import web

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .plex import PlexBridge, PlexItem

_LOGGER = logging.getLogger(__name__)

TS_PACKET = 188
CHUNK = TS_PACKET * 64
QUEUE_SIZE = 256  # ~3 MB of buffered TS per reader
END_TOLERANCE = 10  # seconds from the end that counts as "finished"
MAX_FAST_FAILURES = 3
FAST_FAILURE_SECONDS = 15
FFMPEG_CHECK_TIMEOUT = 30


class PlaybackState(StrEnum):
    """Channel playback state."""

    IDLE = "idle"
    PLAYING = "playing"
    PAUSED = "paused"


class Channel:
    """One virtual channel; backs a camera and a media player entity."""

    def __init__(
        self,
        hass: HomeAssistant,
        bridge: PlexBridge,
        ffmpeg_binary: str,
        number: int,
        max_resolution: str,
        max_bitrate: int,
        reencode_video: bool,
    ) -> None:
        """Initialize."""
        self.hass = hass
        self.bridge = bridge
        self.number = number
        self.access_token = secrets.token_urlsafe(32)
        self._ffmpeg = ffmpeg_binary
        self._max_resolution = max_resolution
        self._max_bitrate = max_bitrate
        self._reencode_video = reencode_video

        self.item: PlexItem | None = None
        self.state = PlaybackState.IDLE
        self.updated_at = dt_util.utcnow()
        self._anchor_position = 0.0
        self._anchor_time = 0.0
        self._fast_failures = 0
        self._kill_requested = False

        self._readers: set[asyncio.Queue[bytes | None]] = set()
        self._producer: asyncio.Task[None] | None = None
        self._proc: asyncio.subprocess.Process | None = None
        self._listeners: list[Callable[[], None]] = []

    # -- state -----------------------------------------------------------

    @property
    def position(self) -> float:
        """Current playback position in seconds."""
        if self.state is PlaybackState.PLAYING:
            return self._anchor_position + time.monotonic() - self._anchor_time
        return self._anchor_position

    @callback
    def async_add_listener(self, update: Callable[[], None]) -> Callable[[], None]:
        """Register for state changes; returns an unsubscribe callable."""
        self._listeners.append(update)
        return lambda: self._listeners.remove(update)

    @callback
    def _notify(self) -> None:
        for update in list(self._listeners):
            update()

    @property
    def anchor_position(self) -> float:
        """Position at the last state change (``updated_at``)."""
        return self._anchor_position

    def _set(self, state: PlaybackState, position: float) -> None:
        self.state = state
        self._anchor_position = max(0.0, position)
        self._anchor_time = time.monotonic()
        self.updated_at = dt_util.utcnow()

    async def async_play(self, item: PlexItem, offset: float = 0.0) -> None:
        """Start playing an item from an offset."""
        self.item = item
        self._fast_failures = 0
        self._set(PlaybackState.PLAYING, offset)
        self._restart_feed()

    async def async_pause(self) -> None:
        """Freeze playback at the current position."""
        if self.state is PlaybackState.PLAYING:
            self._set(PlaybackState.PAUSED, self.position)
            self._restart_feed()

    async def async_resume(self) -> None:
        """Continue from the paused position."""
        if self.state is PlaybackState.PAUSED:
            self._set(PlaybackState.PLAYING, self.position)
            self._restart_feed()

    async def async_seek(self, position: float) -> None:
        """Jump to a position, keeping the play/pause state."""
        if self.item is not None:
            self._set(self.state, position)
            self._restart_feed()

    async def async_stop(self) -> None:
        """Stop playback and return to the slate."""
        self.item = None
        self._set(PlaybackState.IDLE, 0)
        self._restart_feed()

    async def async_shutdown(self) -> None:
        """Tear down everything (config entry unload)."""
        self._listeners.clear()
        self._close_readers()
        if self._producer:
            self._producer.cancel()

    # -- feed ------------------------------------------------------------

    @callback
    def _restart_feed(self) -> None:
        """
        End the current feed so readers reconnect to the new content.

        The source changes timestamps, which HA's stream worker cannot splice,
        so the cleanest switch is to end the HTTP response and let it reconnect.
        """
        self._close_readers()
        self._notify()

    def _close_readers(self, readers: set[asyncio.Queue[bytes | None]] | None = None) -> None:
        """
        Disconnect a feed's readers (default: the current feed's).

        Each feed owns its reader set; readers that connect after this join a
        fresh set and are picked up by the next feed rather than closed here.
        """
        if readers is None or readers is self._readers:
            self._kill()
            readers, self._readers = self._readers, set()
        for queue in readers:
            _force_put(queue, None)
        readers.clear()

    async def async_serve(self, request: web.Request) -> web.StreamResponse:
        """Stream the channel's MPEG-TS feed to one HTTP reader."""
        queue: asyncio.Queue[bytes | None] = asyncio.Queue(QUEUE_SIZE)
        readers = self._readers
        readers.add(queue)
        if self._producer is None or self._producer.done():
            self._producer = self.hass.async_create_background_task(
                self._run_producer(), f"plex_stream_bridge channel {self.number} feed"
            )

        response = web.StreamResponse(
            headers={"Content-Type": "video/mp2t", "Cache-Control": "no-store"}
        )
        await response.prepare(request)
        try:
            while (chunk := await queue.get()) is not None:
                await response.write(chunk)
        except ConnectionResetError, asyncio.CancelledError:
            pass
        finally:
            readers.discard(queue)
            if not readers:
                self._close_readers(readers)
        return response

    async def _run_producer(self) -> None:
        """Run feeds for as long as anyone is reading."""
        while self._readers:
            await self._run_feed()

    async def _run_feed(self) -> None:
        """Run one ffmpeg process and fan its output out to the readers."""
        state, item, readers = self.state, self.item, self._readers
        session_id = uuid.uuid4().hex
        try:
            cmd = await self._command(state, item, session_id)
        except HomeAssistantError as err:
            _LOGGER.warning("Channel %s cannot start a feed: %s", self.number, err)
            self._close_readers(readers)
            return
        started = time.monotonic()
        self._kill_requested = False
        _LOGGER.debug("Channel %s starting feed (%s)", self.number, state)

        try:
            self._proc = proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as err:
            _LOGGER.error("Could not start ffmpeg: %s", err)
            self._close_readers(readers)
            return

        stderr_task = self.hass.async_create_background_task(
            _drain_stderr(proc, self.number), f"plex_stream_bridge channel {self.number} ffmpeg log"
        )
        try:
            assert proc.stdout is not None
            pending = b""
            while readers:
                if not (data := await proc.stdout.read(CHUNK)):
                    break
                # Hand out whole TS packets so late joiners start on a boundary.
                pending += data
                cut = len(pending) - len(pending) % TS_PACKET
                chunk, pending = pending[:cut], pending[cut:]
                if not chunk:
                    continue
                for queue in list(readers):
                    if not _try_put(queue, chunk):
                        _LOGGER.debug("Dropping slow reader on channel %s", self.number)
                        readers.discard(queue)
                        _force_put(queue, None)
        finally:
            if proc.returncode is None:
                self._kill()
            await proc.wait()
            error = await stderr_task
            if state is PlaybackState.PLAYING and item is not None:
                self.hass.async_create_task(self.bridge.async_stop_transcode(session_id))

        if not self._kill_requested and state is PlaybackState.PLAYING and item is self.item:
            self._handle_play_exit(proc.returncode, time.monotonic() - started, error)
        # A new source restarts timestamps, which HA's stream worker cannot
        # splice, so readers always reconnect between feeds.
        self._close_readers(readers)

    def _kill(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._kill_requested = True
            self._proc.kill()

    def _handle_play_exit(self, returncode: int | None, ran_for: float, error: str) -> None:
        """Decide what a Plex feed that ended on its own means for the channel."""
        if self.item is None:
            return
        if returncode == 0 or self.position >= self.item.duration - END_TOLERANCE:
            _LOGGER.debug("Channel %s finished %s", self.number, self.item.label)
            self.item = None
            self._set(PlaybackState.IDLE, 0)
            self._notify()
            return
        if ran_for >= FAST_FAILURE_SECONDS:
            self._fast_failures = 0
            return  # transient; the next reader resumes from self.position
        self._fast_failures += 1
        if self._fast_failures >= MAX_FAST_FAILURES:
            _LOGGER.error(
                "Channel %s could not stream %s: %s",
                self.number,
                self.item.label,
                error or f"ffmpeg exited with code {returncode}",
            )
            self.item = None
            self._set(PlaybackState.IDLE, 0)
            self._notify()

    def _video_codec(self) -> list[str]:
        """
        Codec arguments for Plex video.

        Plex passes the source's B-frames through, and WebRTC (go2rtc, the
        default camera player) cannot reorder them, so playback stutters.
        Re-encoding without B-frames fixes that; copying is cheaper but only
        smooth over HLS or for sources without B-frames.
        """
        if not self._reencode_video:
            return ["-c:v", "copy"]
        return [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-bf",
            "0",
            "-crf",
            "23",
            "-maxrate",
            f"{self._max_bitrate}k",
            "-bufsize",
            f"{self._max_bitrate * 2}k",
            "-pix_fmt",
            "yuv420p",
            "-force_key_frames",
            "expr:gte(t,n_forced*2)",
        ]

    async def _command(
        self, state: PlaybackState, item: PlexItem | None, session_id: str
    ) -> list[str]:
        """Build the ffmpeg command for the current state."""
        base = [self._ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin"]
        output = [
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-ac",
            "2",
            "-f",
            "mpegts",
            "-mpegts_flags",
            "+resend_headers",
            "pipe:1",
        ]
        if state is PlaybackState.PLAYING and item is not None:
            url = await self.bridge.async_hls_url(
                item,
                session_id,
                self.position,
                self._max_resolution,
                self._max_bitrate,
            )
            return [
                *base,
                "-re",
                "-i",
                url,
                "-map",
                "0:v:0",
                "-map",
                "0:a:0?",
                *self._video_codec(),
                *output,
            ]

        width, height = self._max_resolution.split("x")
        slate = [
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-tune",
            "zerolatency",
            "-pix_fmt",
            "yuv420p",
            "-g",
            "10",
        ]
        silence = ["-re", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
        if state is PlaybackState.PAUSED and item is not None and item.art:
            art = self.bridge.image_url(item.art, int(width), int(height))
            return [
                *base,
                "-re",
                "-loop",
                "1",
                "-framerate",
                "5",
                "-i",
                art,
                *silence,
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2",
                "-map",
                "0:v",
                "-map",
                "1:a",
                *slate,
                *output,
            ]
        return [
            *base,
            "-re",
            "-f",
            "lavfi",
            "-i",
            f"color=c=black:s={width}x{height}:r=5",
            *silence,
            "-map",
            "0:v",
            "-map",
            "1:a",
            *slate,
            *output,
        ]


def _try_put(queue: asyncio.Queue[bytes | None], chunk: bytes) -> bool:
    try:
        queue.put_nowait(chunk)
    except asyncio.QueueFull:
        return False
    return True


def _force_put(queue: asyncio.Queue[bytes | None], item: bytes | None) -> None:
    """Enqueue, discarding buffered data if needed (used for the end marker)."""
    while True:
        try:
            queue.put_nowait(item)
            return
        except asyncio.QueueFull:
            queue.get_nowait()


async def _drain_stderr(proc: asyncio.subprocess.Process, number: int) -> str:
    """Log ffmpeg's stderr; return the last line for error reporting."""
    assert proc.stderr is not None
    last = ""
    async for raw in proc.stderr:
        if line := raw.decode(errors="replace").strip():
            last = line
            _LOGGER.debug("Channel %s ffmpeg: %s", number, line)
    return last


async def async_check_ffmpeg(binary: str) -> str | None:
    """
    Return why ffmpeg can't produce channel feeds, or None if it can.

    Feeds need the lavfi input (idle/paused slates) and the libx264 encoder,
    which some distribution builds of ffmpeg leave out.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            binary,
            "-hide_banner",
            "-loglevel",
            "error",
            "-nostdin",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=64x64:r=1",
            "-frames:v",
            "1",
            "-c:v",
            "libx264",
            "-f",
            "null",
            "-",
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as err:
        _LOGGER.debug("Cannot run %s: %s", binary, err)
        return "ffmpeg_missing"
    try:
        _, stderr = await asyncio.wait_for(proc.communicate(), FFMPEG_CHECK_TIMEOUT)
    except TimeoutError:
        proc.kill()
        await proc.wait()
        return "ffmpeg_unsupported"
    if proc.returncode:
        _LOGGER.debug("%s test encode failed: %s", binary, stderr.decode(errors="replace"))
        return "ffmpeg_unsupported"
    return None
