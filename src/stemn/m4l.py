"""Keeps the Max for Live device up to date.

The packaged app carries the device (``stemn/m4l_device``, built from ``m4l/`` in the
repo). Every start of the app copies it into its own folder ``stemn`` next to the
other loomn devices (``~/Documents/MAX for LIVE 3rd Party/LOOMN-MAX4LIVE``) when a
file is missing or differs, so an app update also updates the device.
Without that folder it goes into the Ableton User Library.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

BUNDLED = Path(__file__).resolve().parent / "m4l_device"
FILES = ["stemn.amxd", "stemn-ui.js", "stemn-live.js", "stemn-runner.js", "LIESMICH stemn.txt"]


def loomn_folder() -> Path:
    return Path.home() / "Documents" / "MAX for LIVE 3rd Party" / "LOOMN-MAX4LIVE"


def user_library() -> Path:
    return Path.home() / "Music" / "Ableton" / "User Library"


def default_library() -> Path:
    return loomn_folder() if loomn_folder().is_dir() else user_library()


def device_dir(library: Path | None = None) -> Path:
    library = library or default_library()
    if library.name == "User Library":
        return library / "Presets" / "Audio Effects" / "Max Audio Effect" / "stemn"
    return library / "stemn"


def install_device(library: Path | None = None, source: Path = BUNDLED) -> list[str]:
    """Copy the device into its folder; returns the names of the files written.

    Does nothing without Ableton (no User Library) or without a bundled device.
    """
    default = library is None
    library = library or default_library()
    if sys.platform != "darwin" and default:
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
