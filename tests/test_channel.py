"""Tests for the channel's ffmpeg capability check."""

from pathlib import Path
import shutil

import pytest

from custom_components.plex_camera.channel import async_check_ffmpeg

FFMPEG = shutil.which("ffmpeg")


async def test_ffmpeg_missing(tmp_path: Path) -> None:
    """A binary that doesn't exist is reported as missing."""
    assert await async_check_ffmpeg(str(tmp_path / "no-ffmpeg")) == "ffmpeg_missing"


async def test_ffmpeg_unsupported(tmp_path: Path) -> None:
    """An ffmpeg whose test encode fails (e.g. no libx264) is unsupported."""
    fake = tmp_path / "ffmpeg"
    fake.write_text("#!/bin/sh\necho 'Unknown encoder libx264' >&2\nexit 1\n")
    fake.chmod(0o755)
    assert await async_check_ffmpeg(str(fake)) == "ffmpeg_unsupported"


@pytest.mark.skipif(FFMPEG is None, reason="ffmpeg not installed")
async def test_ffmpeg_capable() -> None:
    """A full ffmpeg build passes."""
    assert await async_check_ffmpeg(FFMPEG) is None
