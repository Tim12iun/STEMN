import numpy as np
import soundfile as sf

from stemn.engine import conform, match_channels, match_length


def test_match_length_pads_and_trims():
    a = np.ones((10, 2), dtype=np.float32)
    assert match_length(a, 15).shape == (15, 2)
    assert match_length(a, 15)[10:].sum() == 0
    assert match_length(a, 4).shape == (4, 2)


def test_match_channels():
    stereo = np.stack([np.ones(5), np.zeros(5)], axis=1)
    assert match_channels(stereo, 1).shape == (5, 1)
    assert np.allclose(match_channels(stereo, 1)[:, 0], 0.5)
    assert match_channels(np.ones((5, 1)), 2).shape == (5, 2)


def test_conform_matches_reference(tmp_path):
    ref = tmp_path / "ref.wav"
    sf.write(ref, np.zeros((48000, 2), dtype=np.float32), 48000)
    stem = tmp_path / "stem.wav"
    sf.write(stem, np.full((44200, 2), 0.25, dtype=np.float32), 44100)
    out = conform(stem, sf.info(str(ref)), tmp_path / "out.wav")
    info = sf.info(str(out))
    assert (info.samplerate, info.channels, info.frames) == (48000, 2, 48000)
