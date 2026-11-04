"""End-to-end test with the real model. Runs only if the model files are present."""

import numpy as np
import pytest
import soundfile as sf

from stemn.models import DEFAULT_MODEL_ID, discover_models

spec = discover_models().get(DEFAULT_MODEL_ID)
pytestmark = pytest.mark.skipif(spec is None or not spec.is_available(), reason="Modelldateien fehlen (scripts/fetch_models.py)")


def _drum_loop(sr=44100, bars=1, bpm=120):
    beat = int(sr * 60 / bpm)
    n = beat * 4 * bars
    t = np.arange(n) / sr
    out = np.zeros(n, dtype=np.float32)
    rng = np.random.default_rng(0)
    for b in range(4 * bars):
        s = b * beat
        k = np.arange(int(0.25 * sr))
        if b % 2 == 0:  # kick on 1 and 3
            f = 50 + 100 * np.exp(-k / (0.02 * sr))
            out[s : s + len(k)] += 0.9 * np.sin(2 * np.pi * np.cumsum(f) / sr) * np.exp(-k / (0.08 * sr))
        else:  # snare on 2 and 4
            out[s : s + len(k)] += 0.5 * rng.standard_normal(len(k)) * np.exp(-k / (0.05 * sr))
        for h in (0, beat // 2):  # hats on eighths
            hk = np.arange(int(0.04 * sr))
            noise = np.diff(rng.standard_normal(len(hk) + 1))
            out[s + h : s + h + len(hk)] += 0.15 * noise * np.exp(-hk / (0.008 * sr))
    return np.stack([out, out], axis=1) * 0.8, sr


def test_separate_kick_matches_input_length(tmp_path):
    from stemn.engine import separate

    audio, sr = _drum_loop()
    loop = tmp_path / "loop.wav"
    sf.write(loop, audio, sr)
    out = separate(loop, stems=["kick", "hihat"], output_dir=tmp_path / "out")
    assert set(out) == {"kick", "hihat"}
    for path in out.values():
        info = sf.info(str(path))
        assert (info.frames, info.samplerate, info.channels) == (len(audio), sr, 2)


five = discover_models().get("drumsep-mdx23c-5stem")


@pytest.mark.skipif(five is None or not five.is_available(), reason="5-Stem-Modell fehlt")
def test_five_stem_model_writes_cymbals(tmp_path):
    from stemn.engine import separate

    audio, sr = _drum_loop()
    loop = tmp_path / "loop.wav"
    sf.write(loop, audio, sr)
    out = separate(loop, model_id="drumsep-mdx23c-5stem", output_dir=tmp_path / "out")
    assert set(out) == {"kick", "snare", "toms", "hihat", "cymbals"}
    for path in out.values():
        assert sf.info(str(path)).frames == len(audio)


def _restorer_available():
    from stemn.restore import discover_restorers

    spec = discover_restorers().get("apollo-uni")
    return spec is not None and spec.is_available()


@pytest.mark.skipif(not _restorer_available(), reason="Apollo-Modell fehlt")
def test_restore_keeps_length_and_changes_audio(tmp_path):
    import time

    from stemn.engine import separate

    audio, sr = _drum_loop(bars=2)
    loop = tmp_path / "loop.wav"
    sf.write(loop, audio, sr)
    plain = separate(loop, stems=["kick", "hihat"], output_dir=tmp_path / "plain")
    t = time.perf_counter()
    fixed = separate(loop, stems=["kick", "hihat"], output_dir=tmp_path / "fixed", restore=True)
    print(f"\nTrennen + Reparieren: {time.perf_counter() - t:.1f} s fuer {len(audio) / sr:.1f} s Audio")
    for stem in ("kick", "hihat"):
        a, _ = sf.read(str(plain[stem]), dtype="float32")
        b, _ = sf.read(str(fixed[stem]), dtype="float32")
        assert a.shape == b.shape == audio.shape
        assert np.isfinite(b).all() and np.abs(b).max() < 4.0
        assert not np.allclose(a, b, atol=1e-4)
