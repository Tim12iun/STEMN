"""Keeps the Max for Live device in the Ableton User Library up to date.

The packaged app carries the device (``stemn/m4l_device``, built from ``m4l/`` in the
repo). Every start of the app copies it into the User Library when a file is
missing or differs, so an app update also updates the device.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent / "m4l_device"
FILES = ["stemn.amxd", "stemn-ui.js", "stemn-live.js", "stemn-runner.js"]


def user_library() -> Path:
    return Path.home() / "Music" / "Ableton" / "User Library"


def device_dir(library: Path | None = None) -> Path:
    return (library or user_library()) / "Presets" / "Audio Effects" / "Max Audio Effect" / "stemn"


def install_device(library: Path | None = None, source: Path = BUNDLED) -> list[str]:
    """Copy the device into the User Library; returns the names of the files written.

    Does nothing without Ableton (no User Library) or without a bundled device.
    """
    library = library or user_library()
    if sys.platform != "darwin" and library == user_library():
        return []  # Mac first; Windows follows later
    if not library.is_dir() or not all((source / f).is_file() for f in FILES):
        return []
    target = device_dir(library)
    written = []
    for name in FILES:
        src, dst = source / name, target / name
        if dst.is_file() and dst.read_bytes() == src.read_bytes():
            continue
        target.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + ".part")
        shutil.copyfile(src, tmp)
        tmp.replace(dst)
        written.append(name)
    return written
