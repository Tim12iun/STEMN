import numpy as np
import soundfile as sf

from stemn import dereverb
from stemn.cli import build_parser


def test_restore_bass_keeps_low_end_from_original():
    sr = 44100
    t = np.arange(sr) / sr
    low = np.sin(2 * np.pi * 60 * t)
    high = np.sin(2 * np.pi * 2000 * t)
    original = np.stack([low + high] * 2, 1).astype(np.float32)
    processed = np.stack([0.1 * low + 0.5 * high] * 2, 1).astype(np.float32)  # model ate the bass
    out = dereverb.restore_bass(original, processed, sr)
    mid = slice(sr // 4, 3 * sr // 4)
    assert np.allclose(out[mid, 0], (low + 0.5 * high)[mid], atol=0.02)


def test_restore_bass_is_transparent_when_model_changes_nothing():
    x = np.random.default_rng(0).standard_normal((20000, 2)).astype(np.float32)
    assert np.allclose(dereverb.restore_bass(x, x, 44100), x, atol=1e-4)


def test_output_path_goes_to_stems_folder(tmp_path):
    stem = tmp_path / "Loop stems" / "Loop - kick.wav"
    assert dereverb.output_path(stem) == tmp_path / "Loop stems" / "Loop - kick dereverb.wav"
    loop = tmp_path / "Loop.wav"
    assert dereverb.output_path(loop) == tmp_path / "Loop stems" / "Loop dereverb.wav"


def test_model_lives_outside_the_separation_models(tmp_path, monkeypatch):
    monkeypatch.setenv("STEMN_MODELS_DIR", str(tmp_path / "models"))
    monkeypatch.delenv("STEMN_DEREVERB_DIR", raising=False)
    assert dereverb.model_dir() == tmp_path / "tools" / dereverb.MODEL_ID
    assert not dereverb.is_installed()


def test_cli_has_dereverb_command():
    args = build_parser().parse_args(["dereverb", "loop.wav", "-o", "out.wav"])
    assert args.input == "loop.wav" and args.output == "out.wav"
