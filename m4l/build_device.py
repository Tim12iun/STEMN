"""Builds ``stemn.amxd`` (Max Audio Effect) from the scripts in this folder.

    python m4l/build_device.py            -> m4l/dist/stemn/
    python m4l/build_device.py --install  -> also into LOOMN-MAX4LIVE/stemn

The device is not frozen: the three .js files must lie next to stemn.amxd,
Max finds them in the device's own folder.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = ["stemn-ui.js", "stemn-live.js", "stemn-runner.js"]
WIDTH, HEIGHT = 340, 169
INSTALL_DIR = Path.home() / "Documents/MAX for LIVE 3rd Party/LOOMN-MAX4LIVE/stemn"
DOCS = ["LIESMICH stemn.txt"]


def box(id_, maxclass, rect, text=None, inlets=1, outlets=0, outlettype=None, **attrs):
    b = {"id": id_, "maxclass": maxclass, "numinlets": inlets, "numoutlets": outlets, "patching_rect": rect}
    if outlets:
        b["outlettype"] = outlettype or [""] * outlets
    if text is not None:
        b["text"] = text
    b.update(attrs)
    return {"box": b}


def line(src, outlet, dst, inlet):
    return {"patchline": {"source": [src, outlet], "destination": [dst, inlet]}}


def patcher() -> dict:
    transparent = [0.0, 0.0, 0.0, 0.0]
    boxes = [
        # audio passes through untouched
        box("obj-in", "newobj", [20, 20, 60, 22], "plugin~", inlets=2, outlets=2, outlettype=["signal", "signal"]),
        box("obj-out", "newobj", [20, 300, 60, 22], "plugout~", inlets=2),
        # surface
        box(
            "obj-ui",
            "jsui",
            [420, 20, WIDTH, HEIGHT],
            inlets=1,
            outlets=1,
            filename="stemn-ui.js",
            jsarguments=[],
            parameter_enable=0,
            presentation=1,
            presentation_rect=[0, 0, WIDTH, HEIGHT],
        ),
        # invisible drop target on top of the source field
        box(
            "obj-drop",
            "live.drop",
            [420, 210, WIDTH - 24, 44],
            inlets=1,
            outlets=2,
            parameter_enable=0,
            presentation=1,
            presentation_rect=[12, 32, WIDTH - 24, 44],
            legend="",
            bgcolor=transparent,
            bordercolor=transparent,
            focusbordercolor=[1.0, 0.353, 0.122, 1.0],
            textcolor=transparent,
        ),
        box("obj-prep", "newobj", [120, 120, 80, 22], "prepend drop", inlets=1, outlets=1),
        box("obj-dev", "newobj", [120, 60, 90, 22], "live.thisdevice", inlets=1, outlets=3, outlettype=["bang", "int", "int"]),
        box("obj-live", "newobj", [120, 180, 110, 22], "js stemn-live.js", inlets=1, outlets=2),
        box(
            "obj-node",
            "newobj",
            [120, 240, 230, 22],
            "node.script stemn-runner.js @autostart 1 @watch 0",
            inlets=1,
            outlets=2,
            saved_object_attributes={"autostart": 1, "defer": 0, "watch": 0},
        ),
        box("obj-defer", "newobj", [120, 280, 60, 22], "deferlow", inlets=1, outlets=1),
    ]
    lines = [
        line("obj-in", 0, "obj-out", 0),
        line("obj-in", 1, "obj-out", 1),
        line("obj-dev", 0, "obj-live", 0),
        line("obj-drop", 0, "obj-prep", 0),
        line("obj-prep", 0, "obj-live", 0),
        line("obj-ui", 0, "obj-live", 0),
        line("obj-live", 0, "obj-node", 0),
        line("obj-live", 1, "obj-ui", 0),
        line("obj-node", 0, "obj-defer", 0),
        line("obj-defer", 0, "obj-live", 0),
    ]
    return {
        "patcher": {
            "fileversion": 1,
            "appversion": {"major": 8, "minor": 6, "revision": 0, "architecture": "x64", "modernui": 1},
            "classnamespace": "box",
            "rect": [100.0, 100.0, 820.0, 400.0],
            "openrect": [0.0, 0.0, float(WIDTH), float(HEIGHT)],
            "bglocked": 0,
            "openinpresentation": 1,
            "default_fontsize": 10.0,
            "default_fontface": 0,
            "default_fontname": "Arial Bold",
            "gridonopen": 1,
            "gridsize": [8.0, 8.0],
            "gridsnaponopen": 1,
            "objectsnaponopen": 1,
            "statusbarvisible": 2,
            "toolbarvisible": 1,
            "lefttoolbarpinned": 0,
            "toptoolbarpinned": 0,
            "righttoolbarpinned": 0,
            "bottomtoolbarpinned": 0,
            "toolbars_unpinned_last_save": 0,
            "tallnewobj": 0,
            "boxanimatetime": 200,
            "enablehscroll": 1,
            "enablevscroll": 1,
            "devicewidth": float(WIDTH),
            "description": "stemn: Drum-Loops in Kick, Snare, Hi-Hat, Toms, Ride und Crash trennen",
            "digest": "",
            "tags": "",
            "style": "",
            "subpatcher_template": "",
            "assistshowspatchername": 0,
            "boxes": boxes,
            "lines": lines,
            "dependency_cache": [{"name": s, "type": "TEXT", "implicit": 1} for s in SCRIPTS],
            "latency": 0,
            "is_mpe": 0,
            "external_mpe_tuning": 0,
            "minimum_live_version": "",
            "minimum_max_version": "",
            "platform_compatibility": 0,
            "autosave": 0,
        }
    }


def amxd_bytes(patch: dict) -> bytes:
    """Unfrozen Max for Live device: 'ampf' header, device type 'aaaa' (audio effect), patcher JSON."""
    body = json.dumps(patch, indent="\t", ensure_ascii=False).encode("utf-8") + b"\n\x00"
    return b"ampf" + struct.pack("<I", 4) + b"aaaa" + b"meta" + struct.pack("<I", 4) + b"\x00\x00\x00\x00" + b"ptch" + struct.pack("<I", len(body)) + body


def build(target: Path) -> Path:
    target.mkdir(parents=True, exist_ok=True)
    (target / "stemn.amxd").write_bytes(amxd_bytes(patcher()))
    for s in SCRIPTS + DOCS:
        shutil.copy2(HERE / s, target / s)
    return target


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--install", action="store_true", help="auch in den Ordner LOOMN-MAX4LIVE legen")
    p.add_argument("--out", default=str(HERE / "dist" / "stemn"))
    args = p.parse_args()
    print(build(Path(args.out)))
    if args.install:
        print(build(INSTALL_DIR))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
