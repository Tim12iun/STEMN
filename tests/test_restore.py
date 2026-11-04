"""Restoration step (Apollo) with a tiny random network: loading, shapes, chunking."""

import numpy as np
import pytest
import yaml

torch = pytest.importorskip("torch")

from stemn import restore  # noqa: E402
from stemn.apollo import Apollo  # noqa: E402

TINY = {"sr": 44100, "win": 20, "feature_dim": 16, "layer": 1}


def _make(tmp_path, prefix="", wrap=True):
    folder = tmp_path / "apollo-tiny"
    folder.mkdir()
    net = Apollo(**TINY)
    state = {prefix + k: v for k, v in net.state_dict().items()}
    torch.save({"state_dict": state} if wrap else state, folder / "tiny.ckpt")
    manifest = {"id": "apollo-tiny", "name": "Tiny", "arch": "apollo", "checkpoint": "tiny.ckpt", "model": TINY}
    (folder / restore.RESTORE_MANIFEST).write_text(yaml.safe_dump(manifest), encoding="utf-8")
    return folder


@pytest.mark.parametrize("prefix,wrap", [("", False), ("audio_model.", True), ("model.", True)])
def test_loads_checkpoint_formats(tmp_path, prefix, wrap):
    folder = _make(tmp_path, prefix, wrap)
    spec = restore.load_restore_manifest(folder)
    r = restore.Restorer(spec)
    audio = np.random.default_rng(0).standard_normal((44100, 2)).astype(np.float32) * 0.1
    out = r.restore(audio)
    assert out.shape == audio.shape and out.dtype == np.float32
    assert np.isfinite(out).all()


def test_chunked_matches_length_and_has_no_gaps(tmp_path, monkeypatch):
    monkeypatch.setattr(restore, "CHUNK_SECONDS", 0.5)
    monkeypatch.setattr(restore, "FADE_SECONDS", 0.1)
    r = restore.Restorer(restore.load_restore_manifest(_make(tmp_path)))
    audio = np.random.default_rng(1).standard_normal((int(44100 * 1.7), 1)).astype(np.float32) * 0.1
    seen = []
    out = r.restore(audio, progress=seen.append)
    assert out.shape == audio.shape
    assert seen[-1] == pytest.approx(1.0) and len(seen) == len(restore._windows(len(audio), 22050, 4410))


def test_silence_stays_silent(tmp_path):
    r = restore.Restorer(restore.load_restore_manifest(_make(tmp_path)))
    out = r.restore(np.zeros((1000, 2), np.float32))
    assert not out.any()


def test_windows_cover_everything():
    for frames in (10, 100, 101, 250, 1000):
        starts = restore._windows(frames, 100, 20)
        covered = np.zeros(frames, bool)
        for s in starts:
            covered[s : s + 100] = True
        assert covered.all() and starts[-1] + min(100, frames) == frames


def test_wrong_settings_give_clear_error(tmp_path):
    folder = _make(tmp_path)
    data = yaml.safe_load((folder / restore.RESTORE_MANIFEST).read_text())
    data["model"]["layer"] = 2
    (folder / restore.RESTORE_MANIFEST).write_text(yaml.safe_dump(data))
    with pytest.raises(restore.RestoreError, match="passt nicht"):
        restore.Restorer(restore.load_restore_manifest(folder))


def test_discover_skips_broken_manifest(tmp_path):
    _make(tmp_path)
    bad = tmp_path / "bad"
    bad.mkdir()
    (bad / restore.RESTORE_MANIFEST).write_text("arch: other\ncheckpoint: x.ckpt\n")
    found = restore.discover_restorers([(tmp_path, False)])
    assert list(found) == ["apollo-tiny"]


def test_bundled_restorer_manifest_is_valid():
    found = restore.discover_restorers([(restore.bundled_models_dir(), True)])
    assert "apollo-uni" in found
    assert found["apollo-uni"].model["feature_dim"] == 384


def test_restore_progress_keeps_moving_while_apollo_works(tmp_path):
    """A loop shorter than one chunk must not sit at one value until the stem is done."""
    import time

    import soundfile as sf

    from stemn.engine import Engine

    class SlowRestorer:
        sample_rate = 44100

        def restore(self, audio, progress=None):
            time.sleep(1.5)
            return audio

    path = tmp_path / "stem_kick.wav"
    sf.write(path, np.full((44100, 2), 0.1, np.float32), 44100)
    engine = Engine()
    engine._restore_rate = 1.0
    seen = []
    out = engine._restore_file(SlowRestorer(), path, seen.append)
    assert out.is_file()
    ticks = seen[:-1]
    assert len(ticks) >= 2 and ticks == sorted(ticks) and 0 < ticks[-1] <= 0.9
    assert seen[-1] == 1.0
    assert 1.0 < engine._restore_rate < 3.0  # learned from this stem
