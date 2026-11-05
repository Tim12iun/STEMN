"""Cross-talk reduction: stems still add up to the loop, bleed gets quieter."""

import numpy as np
import pytest

pytest.importorskip("torch")

from stemn.bleed import reduce_bleed  # noqa: E402

SR = 44100


def _stems():
    rng = np.random.default_rng(0)
    t = np.arange(SR) / SR
    kick = (np.sin(2 * np.pi * 55 * t) * np.exp(-t * 8)).astype(np.float32)
    hat = (rng.standard_normal(SR) * 0.2).astype(np.float32)
    hat = np.diff(np.concatenate([[0], hat])).astype(np.float32)  # highs only
    # each stem leaks 5 % of the other, like a separation model does
    kick_est = kick + 0.05 * hat
    hat_est = hat + 0.05 * kick
    return {"kick": np.stack([kick_est] * 2, 1), "hihat": np.stack([hat_est] * 2, 1)}, kick, hat


def _highs_db(audio):
    """Energy above 2 kHz: the kick has none, so there it is pure hi-hat bleed."""
    spec = np.abs(np.fft.rfft(audio[:, 0])) ** 2
    freqs = np.fft.rfftfreq(len(audio), 1 / SR)
    return 10 * np.log10(spec[freqs > 2000].sum() + 1e-12)


def test_off_changes_nothing():
    stems, _, _ = _stems()
    out = reduce_bleed(stems, "off")
    for k in stems:
        assert np.array_equal(out[k], stems[k])


@pytest.mark.parametrize("level", ["light", "strong"])
def test_sum_is_kept_and_bleed_drops(level):
    stems, kick, hat = _stems()
    out = reduce_bleed(stems, level)
    assert np.allclose(sum(out.values()), sum(stems.values()), atol=1e-3)
    assert out["kick"].shape == stems["kick"].shape
    assert _highs_db(out["kick"]) < _highs_db(stems["kick"]) - 20


def test_unknown_level():
    stems, _, _ = _stems()
    with pytest.raises(ValueError):
        reduce_bleed(stems, "max")


def test_stems_add_up_to_the_original_loop():
    """What the separation lost (e.g. a hat tail under a kick) goes back to the stems."""
    rng = np.random.default_rng(1)
    sr = 44100
    loop = (rng.standard_normal((sr, 2)) * 0.1).astype(np.float32)
    # a separation that lost 10 % of everything and some highs
    stems = {"kick": loop * 0.5, "hihat": loop * 0.4}
    for level in ("off", "light"):
        out = reduce_bleed(stems, level, mix=loop)
        total = sum(out.values())
        assert np.abs(total - loop).max() < 1e-4
