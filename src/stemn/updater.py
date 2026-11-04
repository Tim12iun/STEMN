"""Self-update from GitHub Releases.

Each release carries two assets per platform:

* ``stemn-<platform>.zip``      full package incl. model (first install)
* ``stemn-<platform>-app.zip``  the program only (small, used for updates)

The updater downloads the small package, keeps the models of the current
install if they are unchanged (same ``stemn-model.yaml``) and falls back to the
full package otherwise. The new version is unpacked next to the current one and
swapped in when stemn is not running (on Windows via a helper script that waits
until the process has exited).

Access to a private repository needs a read-only token. It is looked up in this
order: ``STEMN_GITHUB_TOKEN``, the user config file, the token baked in at build
time (``stemn/_build_token.py``).
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

from . import __version__

REPO = os.environ.get("STEMN_UPDATE_REPO", "Tim12iun/stemn")
API = "https://api.github.com"


def platform_tag() -> str | None:
    machine = platform.machine().lower()
    if sys.platform == "darwin" and machine in ("arm64", "aarch64"):
        return "macos-arm64"
    if sys.platform == "win32":
        return "windows-x64"
    return None


def config_path() -> Path:
    from .models import user_models_dir

    return user_models_dir().parent / "config.json"


def load_config() -> dict:
    try:
        return json.loads(config_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_config(cfg: dict) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def get_token() -> str | None:
    token = os.environ.get("STEMN_GITHUB_TOKEN") or load_config().get("github_token")
    if token:
        return token
    try:
        from ._build_token import TOKEN  # written by the build workflow, not in git

        return TOKEN or None
    except ImportError:
        return None


def parse_version(text: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", text.lstrip("vV").split("-")[0])
    return tuple(int(n) for n in nums[:3]) or (0,)


@dataclass
class Release:
    version: str
    assets: dict[str, str]  # name -> API asset url
    notes: str = ""

    def asset_for(self, tag: str, app_only: bool) -> str | None:
        name = f"stemn-{tag}-app.zip" if app_only else f"stemn-{tag}.zip"
        return self.assets.get(name)


class _DropAuthOnRedirect(urllib.request.HTTPRedirectHandler):
    """Release downloads redirect to a storage host that rejects the GitHub token."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new is not None and urllib.parse.urlsplit(newurl).netloc != urllib.parse.urlsplit(req.full_url).netloc:
            new.remove_header("Authorization")
        return new


_opener = urllib.request.build_opener(_DropAuthOnRedirect)


def _request(url: str, accept: str = "application/vnd.github+json"):
    headers = {"Accept": accept, "User-Agent": f"stemn/{__version__}"}
    token = get_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return _opener.open(urllib.request.Request(url, headers=headers), timeout=60)


def latest_release() -> Release | None:
    try:
        with _request(f"{API}/repos/{REPO}/releases/latest") as resp:
            data = json.load(resp)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403, 404):
            raise UpdateError(
                "Kein Zugriff auf die Updates. Fehlt der GitHub-Token? "
                "Mit 'stemn update --token <TOKEN>' eintragen."
            ) from exc
        raise UpdateError(f"GitHub antwortet mit {exc.code}") from exc
    except OSError as exc:
        raise UpdateError(f"Keine Verbindung zu GitHub: {exc}") from exc
    assets = {a["name"]: a["url"] for a in data.get("assets", [])}
    return Release(version=data.get("tag_name", "0"), assets=assets, notes=data.get("body") or "")


class UpdateError(Exception):
    pass


def check() -> Release | None:
    """Return the newer release, or None if this version is current."""
    rel = latest_release()
    if rel and parse_version(rel.version) > parse_version(__version__):
        return rel
    return None


def install_root() -> Path | None:
    """Folder of the installed (PyInstaller) app, or None when run from source."""
    if not getattr(sys, "frozen", False):
        return None
    exe = Path(sys.executable).resolve()
    for parent in exe.parents:  # macOS: replace the whole stemn.app bundle
        if parent.suffix == ".app":
            return parent
    return exe.parent


def _download(url: str, target: Path, progress=None) -> None:
    with _request(url, accept="application/octet-stream") as resp, open(target, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while chunk := resp.read(1 << 20):
            out.write(chunk)
            done += len(chunk)
            if progress:
                progress(done, total)


def _manifests(models_dir: Path) -> dict[str, str]:
    return {p.parent.name: p.read_text(encoding="utf-8") for p in models_dir.glob("*/stemn-model.yaml")}


def _models_dir(root: Path) -> Path | None:
    """The real (non-symlinked) models folder inside an install.

    Windows: <root>/_internal/models, macOS: stemn.app/Contents/Resources/models.
    """
    for manifest in sorted(root.rglob("stemn-model.yaml")):
        candidate = manifest.parent.parent
        if not any(p.is_symlink() for p in (candidate, *candidate.parents) if root in p.parents or p == root):
            return candidate
    return None


def prepare(rel: Release, root: Path, progress=None) -> Path:
    """Download and unpack ``rel`` next to ``root``. Returns the staged folder."""
    tag = platform_tag()
    if tag is None:
        raise UpdateError("Fuer dieses System gibt es keine fertigen Pakete.")
    staged = root.parent / f"{root.name}.new"
    shutil.rmtree(staged, ignore_errors=True)

    with tempfile.TemporaryDirectory(prefix="stemn-update-") as tmp:
        tmp = Path(tmp)
        app_url = rel.asset_for(tag, app_only=True)
        full_url = rel.asset_for(tag, app_only=False)
        if app_url:
            _download(app_url, tmp / "app.zip", progress)
            _extract(tmp / "app.zip", tmp / "x")
            new_root = _single_child(tmp / "x")
            old_models = _models_dir(root)
            new_models = _models_dir(new_root)
            # app package ships manifests only; reuse the old model files if unchanged
            if old_models and new_models and _manifests(old_models) == _manifests(new_models):
                shutil.rmtree(new_models, ignore_errors=True)
                shutil.copytree(old_models, new_models)
            elif full_url:
                shutil.rmtree(tmp / "x")
                _download(full_url, tmp / "full.zip", progress)
                _extract(tmp / "full.zip", tmp / "x")
                new_root = _single_child(tmp / "x")
            else:
                raise UpdateError("Das Update braucht ein neues Modell, aber das volle Paket fehlt.")
        elif full_url:
            _download(full_url, tmp / "full.zip", progress)
            _extract(tmp / "full.zip", tmp / "x")
            new_root = _single_child(tmp / "x")
        else:
            raise UpdateError(f"Release {rel.version} hat kein Paket fuer {tag}.")
        shutil.move(str(new_root), staged)
    return staged


def _extract(zip_path: Path, target: Path) -> None:
    with zipfile.ZipFile(zip_path) as zf:
        for member in zf.namelist():
            dest = (target / member).resolve()
            if not str(dest).startswith(str(target.resolve())):
                raise UpdateError("Ungueltiges Update-Paket")
        if sys.platform == "darwin":
            # ditto keeps symlinks and permissions inside the .app bundle; zipfile does not
            target.mkdir(parents=True, exist_ok=True)
            subprocess.run(["ditto", "-x", "-k", str(zip_path), str(target)], check=True)
        else:
            zf.extractall(target)


def _single_child(folder: Path) -> Path:
    children = [p for p in folder.iterdir() if not p.name.startswith(".")]
    return children[0] if len(children) == 1 and children[0].is_dir() else folder


def apply(staged: Path, root: Path) -> str:
    """Swap ``staged`` in for ``root``. Returns a message for the user."""
    backup = root.parent / f"{root.name}.old"
    if sys.platform == "win32":
        script = root.parent / "stemn-update.cmd"
        script.write_text(
            "@echo off\r\n"
            f":wait\r\ntasklist /FI \"PID eq {os.getpid()}\" | find \"{os.getpid()}\" >nul && (timeout /t 1 >nul & goto wait)\r\n"
            f"if exist \"{backup}\" rmdir /s /q \"{backup}\"\r\n"
            f"move \"{root}\" \"{backup}\" >nul\r\n"
            f"move \"{staged}\" \"{root}\" >nul\r\n"
            f"rmdir /s /q \"{backup}\"\r\n"
            "del \"%~f0\"\r\n",
            encoding="utf-8",
        )
        subprocess.Popen(["cmd", "/c", str(script)], creationflags=0x00000008 | 0x00000200)  # detached, new group
        return "Update wird installiert, sobald stemn beendet ist."
    # Python loads modules lazily from the install folder, so never swap it while
    # this process runs: a small shell script waits for us to exit.
    script = root.parent / "stemn-update.sh"
    script.write_text(
        "#!/bin/sh\n"
        f"while kill -0 {os.getpid()} 2>/dev/null; do sleep 1; done\n"
        f'rm -rf "{backup}"\n'
        f'mv "{root}" "{backup}" && mv "{staged}" "{root}" && rm -rf "{backup}"\n'
        'rm -f "$0"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    subprocess.Popen(["/bin/sh", str(script)], start_new_session=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return "Update wird installiert, sobald stemn beendet ist."


def run_update(progress=None) -> str:
    rel = check()
    if rel is None:
        return f"stemn {__version__} ist aktuell."
    root = install_root()
    if root is None:
        return f"Neue Version {rel.version} verfuegbar. Aus dem Quellcode: 'git pull' ausfuehren."
    staged = prepare(rel, root, progress)
    return f"Version {rel.version}: " + apply(staged, root)
