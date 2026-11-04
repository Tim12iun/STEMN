import zipfile
from pathlib import Path

import pytest

from stemn import updater


def _zip(path: Path, files: dict[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return path


def test_parse_version():
    assert updater.parse_version("v0.2.0") > updater.parse_version("0.1.9")
    assert updater.parse_version("1.0") == (1, 0)


def test_check(monkeypatch):
    monkeypatch.setattr(updater, "__version__", "0.1.0")
    monkeypatch.setattr(updater, "latest_release", lambda: updater.Release("v0.2.0", {}))
    assert updater.check().version == "v0.2.0"
    monkeypatch.setattr(updater, "latest_release", lambda: updater.Release("v0.1.0", {}))
    assert updater.check() is None


def test_token_order(monkeypatch, tmp_path):
    monkeypatch.setenv("STEMN_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("STEMN_GITHUB_TOKEN", raising=False)
    updater.save_config({"github_token": "from-config"})
    assert updater.get_token() == "from-config"
    monkeypatch.setenv("STEMN_GITHUB_TOKEN", "from-env")
    assert updater.get_token() == "from-env"


MANIFEST = b"id: m\narch: mdxc\ncheckpoint: m.ckpt\nconfig: c.yaml\n"


@pytest.fixture
def install(tmp_path, monkeypatch):
    root = tmp_path / "app" / "stemn"
    models = root / "_internal" / "models" / "m"
    models.mkdir(parents=True)
    (models / "stemn-model.yaml").write_bytes(MANIFEST)
    (models / "m.ckpt").write_bytes(b"OLD-WEIGHTS")
    monkeypatch.setattr(updater, "platform_tag", lambda: "macos-arm64")
    return root


def _fake_download(sources):
    def download(url, target, progress=None):
        target.write_bytes(sources[url].read_bytes())
    return download


def test_prepare_reuses_unchanged_model(tmp_path, install, monkeypatch):
    app = _zip(tmp_path / "app.zip", {"stemn/stemn": b"NEW", "stemn/_internal/models/m/stemn-model.yaml": MANIFEST})
    monkeypatch.setattr(updater, "_download", _fake_download({"A": app}))
    rel = updater.Release("v9", {"stemn-macos-arm64-app.zip": "A", "stemn-macos-arm64.zip": "F"})
    staged = updater.prepare(rel, install)
    assert (staged / "stemn").read_bytes() == b"NEW"
    assert (staged / "_internal/models/m/m.ckpt").read_bytes() == b"OLD-WEIGHTS"


def test_prepare_downloads_full_when_model_changed(tmp_path, install, monkeypatch):
    new_manifest = MANIFEST + b"name: v2\n"
    app = _zip(tmp_path / "app.zip", {"stemn/stemn": b"NEW", "stemn/_internal/models/m/stemn-model.yaml": new_manifest})
    full = _zip(
        tmp_path / "full.zip",
        {
            "stemn/stemn": b"NEW",
            "stemn/_internal/models/m/stemn-model.yaml": new_manifest,
            "stemn/_internal/models/m/m.ckpt": b"NEW-WEIGHTS",
        },
    )
    monkeypatch.setattr(updater, "_download", _fake_download({"A": app, "F": full}))
    rel = updater.Release("v9", {"stemn-macos-arm64-app.zip": "A", "stemn-macos-arm64.zip": "F"})
    staged = updater.prepare(rel, install)
    assert (staged / "_internal/models/m/m.ckpt").read_bytes() == b"NEW-WEIGHTS"


def test_extract_rejects_path_traversal(tmp_path):
    bad = _zip(tmp_path / "bad.zip", {"../evil": b"x"})
    with pytest.raises(updater.UpdateError):
        updater._extract(bad, tmp_path / "out")


def test_redirect_drops_token_for_other_host():
    import urllib.request

    req = urllib.request.Request("https://api.github.com/x", headers={"Authorization": "Bearer t"})
    new = updater._DropAuthOnRedirect().redirect_request(req, None, 302, "Found", {}, "https://objects.example.com/y")
    assert not new.has_header("Authorization")
