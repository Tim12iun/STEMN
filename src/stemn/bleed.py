"""Reduce cross-talk ("bleed") between separated stems.

The model estimates, per frequency and moment, how much of the loop belongs to
each instrument. Where several drums sound at once, every stem keeps a little
of the others. Here all stems are compared with each other: each
time-frequency point goes more decisively to the stem that is loudest there
(masks raised to a power and renormalised, applied to the sum of all stems).
The stems still add up to the same loop.

With ``mix`` (the original loop) the stems are made to add up to exactly that
loop: whatever the separation lost (e.g. a hi-hat tail under a kick) is handed
back to the stems in proportion to how much each owns that point.
"""

from __future__ import annotations

import numpy as np

# 1 = unchanged. Higher = drier stems, but quiet tails can get thinner.
BLEED_POWER = {"off": 1.0, "light": 2.0, "strong": 4.0}
N_FFT = 2048
HOP = 512


def reduce_bleed(stems: dict[str, np.ndarray], level: str = "light", mix: np.ndarray | None = None) -> dict[str, np.ndarray]:
    """``stems``: {name: (frames, channels) float32}, all same rate. Returns the cleaned stems.

    ``mix`` (frames, channels), optional: the original loop at the same rate; the
    returned stems then sum to it.
    """
    if level not in BLEED_POWER:
        raise ValueError(f"Stufe '{level}' unbekannt ({', '.join(BLEED_POWER)})")
    power = BLEED_POWER[level]
    if len(stems) < 2 or (power == 1.0 and mix is None):
        return {k: v.copy() for k, v in stems.items()}

    import torch

    names = list(stems)
    frames = min(v.shape[0] for v in stems.values())
    channels = max(v.shape[1] for v in stems.values())
    data = np.zeros((len(names), channels, frames), dtype=np.float32)
    for i, n in enumerate(names):
        a = stems[n][:frames]
        data[i] = (a if a.shape[1] == channels else np.repeat(a[:, :1], channels, axis=1)).T

    window = torch.hann_window(N_FFT)
    x = torch.from_numpy(data).reshape(-1, frames)
    spec = torch.stft(x, N_FFT, HOP, window=window, return_complex=True)
    spec = spec.reshape(len(names), channels, *spec.shape[-2:])
    total = spec.sum(0)
    mag = spec.abs().pow(power)
    masks = mag / (mag.sum(0, keepdim=True) + 1e-12)
    if mix is not None:
        m = np.zeros((channels, frames), dtype=np.float32)
        mm = np.asarray(mix, dtype=np.float32)[:frames]
        if mm.ndim == 1:
            mm = mm[:, None]
        m[:, : mm.shape[0]] = (mm if mm.shape[1] == channels else np.repeat(mm[:, :1], channels, axis=1)).T
        target = torch.stft(torch.from_numpy(m), N_FFT, HOP, window=window, return_complex=True)
        if power == 1.0:
            # keep the stems as they are, only hand back what is missing
            parts = spec + masks * (target - total)
        else:
            parts = masks * target
    else:
        parts = masks * total
    out = torch.istft(parts.reshape(-1, *spec.shape[-2:]), N_FFT, HOP, window=window, length=frames)
    out = out.reshape(len(names), channels, frames).numpy()
    return {n: np.ascontiguousarray(out[i].T) for i, n in enumerate(names)}
