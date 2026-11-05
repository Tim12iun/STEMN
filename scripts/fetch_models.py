"""Download the built-in model files listed in models/*/stemn-model.yaml.

Used by the build (GitHub Actions) and for local development. Checks SHA-256
and skips files that are already present and correct.

    python scripts/fetch_models.py
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "models"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch(url: str, target: Path, expected: str) -> None:
    if target.is_file() and sha256(target) == expected:
        print(f"ok        {target.name}")
        return
    print(f"download  {target.name}")
    tmp = target.with_suffix(target.suffix + ".part")
    with urllib.request.urlopen(url, timeout=300) as resp, open(tmp, "wb") as out:
        while chunk := resp.read(1 << 20):
            out.write(chunk)
    actual = sha256(tmp)
    if actual != expected:
        tmp.unlink()
        raise SystemExit(f"Pruefsumme falsch fuer {target.name}: {actual} != {expected}")
    tmp.replace(target)


def main() -> int:
    manifests = sorted(ROOT.glob("*/stemn-model.yaml"))
    if not manifests:
        print("Keine Modelle gefunden", file=sys.stderr)
        return 1
    for manifest in manifests:
        data = yaml.safe_load(manifest.read_text(encoding="utf-8")) or {}
        for item in data.get("downloads") or []:
            fetch(item["url"], manifest.parent / item["file"], item["sha256"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
