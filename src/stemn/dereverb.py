"""De-Reverb: takes the room/hall out of a loop or a single stem.

Model: MelBand Roformer De-Reverb by anvuew (trained on vocals; on drums it
cuts the tails well but also thins out the low end). stemn therefore keeps the
lowest frequencies (kick body, below ``BASS_KEEP_HZ``) from the original and
only takes everything above from the model.

The model is large (about 900 MB) and is not part of the app package. It is
downloaded once, on the first use of the D button, into the user folder.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
import threading
import urllib.request
from pathlib import Path

import numpy as np
import soundfile as sf

from .engine import _safe_name, _track_chunks, conform
from .models import ModelSpec, user_models_dir

log = logging.getLogger("stemn")

MODEL_ID = "dereverb-melband-anvuew"
MODEL_NAME = "MelBand Roformer De-Reverb (anvuew)"
_BASE = "https://github.com/nomadkaraoke/python-audio-separator/releases/download/model-configs/"
FILES = {
    "checkpoint": (
        "dereverb_mel_band_roformer_anvuew_sdr_19.1729.ckpt",
        "9262877b87e9ebb0fb808a456b0a411fa677f5df31c8383c1254af531c078970",
    ),
    "config": (
        "dereverb_mel_band_roformer_anvuew.yaml",
        "1599d9ea717ea2b5b3bc55d936f752b8d0f67baaa3de95acd6d03259a2f37784",
    ),
}
BASS_KEEP_HZ = 160.0  # below: original (kick body), above: model; smooth crossover
CROSSOVER_WIDTH = 0.6  # crossover band from about 110 Hz to 210 Hz


def model_dir() -> Path:
    """Outside the models folder, so it never shows up in the separation list."""
    env = os.environ.get("STEMN_DEREVERB_DIR")
    if env:
        return Path(env)
    return user_models_dir().parent / "tools" / MODEL_ID


def model_spec(folder: Path | None = None) -> ModelSpec:
    folder = folder or model_dir()
    return ModelSpec(
        id=MODEL_ID,
        name=MODEL_NAME,
        arch="mdxc",
        folder=folder,
        checkpoint=folder / FILES["checkpoint"][0],
        config=folder / FILES["config"][0],
        stems=["noreverb", "reverb"],
    )


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_installed(folder: Path | None = None) -> bool:
    spec = model_spec(folder)
    return spec.checkpoint.is_file() and spec.config.is_file()


def ensure_model(progress=None, folder: Path | None = None) -> ModelSpec:
    """Download the model files once (checks SHA-256). ``progress`` gets 0..1."""
    spec = model_spec(folder)
    spec.folder.mkdir(parents=True, exist_ok=True)
    report = progress or (lambda _v: None)
    for key in ("config", "checkpoint"):
        name, expected = FILES[key]
        target = spec.folder / name
        if target.is_file():
            continue
        tmp = target.with_suffix(target.suffix + ".part")
        with urllib.request.urlopen(_BASE + name, timeout=60) as resp, open(tmp, "wb") as out:
            total = int(resp.headers.get("Content-Length") or 0)
            done = 0
            while chunk := resp.read(1 << 20):
                out.write(chunk)
                done += len(chunk)
                if total and key == "checkpoint":
                    report(done / total)
        if _sha256(tmp) != expected:
            tmp.unlink(missing_ok=True)
            raise RuntimeError(f"Download von {name} ist beschädigt. Bitte noch einmal versuchen.")
        tmp.replace(target)
    report(1.0)
    return spec


def _crossover_gain(freqs: np.ndarray) -> np.ndarray:
    """1 below the crossover, 0 above, raised-cosine in between (zero phase)."""
    lo = BASS_KEEP_HZ * (1 - CROSSOVER_WIDTH / 2)
    hi = BASS_KEEP_HZ * (1 + CROSSOVER_WIDTH / 2)
    g = np.clip((hi - freqs) / (hi - lo), 0.0, 1.0)
    return 0.5 - 0.5 * np.cos(np.pi * g)


def restore_bass(original: np.ndarray, processed: np.ndarray, sr: int) -> np.ndarray:
    """Low end from ``original``, the rest from ``processed`` (both (frames, channels)).

    The two filters add up to exactly 1, so where the model changes nothing
    the result is the original.
    """
    n = min(len(original), len(processed))
    original, processed = original[:n], processed[:n]
    size = 1 << int(np.ceil(np.log2(max(n, 2))))
    low = _crossover_gain(np.fft.rfftfreq(size, 1 / sr))[:, None]
    a = np.fft.rfft(original, size, axis=0)
    b = np.fft.rfft(processed, size, axis=0)
    out = np.fft.irfft(a * low + b * (1 - low), size, axis=0)[:n]
    return out.astype(np.float32)


def output_path(input_path: Path) -> Path:
    """``Loop - kick.wav`` -> ``Loop - kick dereverb.wav`` in the same folder.

    A loop that is not inside a stems folder gets its file in ``<Loop> stems``.
    """
    input_path = Path(input_path)
    folder = input_path.parent
    if not folder.name.endswith(" stems"):
        folder = folder / f"{input_path.stem} stems"
    return folder / f"{_safe_name(input_path.stem)} dereverb.wav"


class DereverbEngine:
    """Keeps the model loaded between calls."""

    def __init__(self) -> None:
        self._separator = None
        self._lock = threading.Lock()

    def _load(self, spec: ModelSpec, work_dir: Path):
        from .backend import LocalSeparator

        if self._separator is None:
            self._separator = LocalSeparator(
                output_dir=str(work_dir),
                model_file_dir=str(spec.folder),
                output_format="WAV",
                normalization_threshold=1.0,
                log_level=logging.ERROR,
            )
            self._separator.register(spec)
            self._separator.load_model(model_filename=spec.checkpoint.name)
        self._separator.set_output_dir(work_dir)
        return self._separator

    def run(self, input_path, output=None, progress=None, status=None, folder: Path | None = None) -> Path:
        """Remove the reverb from ``input_path``; returns the written file.

        Progress: 0..0.3 download (only the first time), 0.3..0.95 model, then saving.
        """
        report = progress or (lambda _v: None)
        note = status or (lambda _t: None)
        input_path = Path(input_path).expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Datei nicht gefunden: {input_path}")
        target = Path(output) if output else output_path(input_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        report(0.0)
        if not is_installed(folder):
            note("De-Reverb-Modell wird geladen (einmalig, ca. 900 MB)")
            spec = ensure_model(lambda v: report(0.3 * v), folder)
        else:
            spec = model_spec(folder)
        note("")
        report(0.3)
        reference = sf.info(str(input_path))
        original, sr = sf.read(str(input_path), dtype="float32", always_2d=True)
        work_root = os.environ.get("STEMN_WORK_DIR") or None
        with self._lock, tempfile.TemporaryDirectory(prefix="stemn-", dir=work_root) as tmp:
            work = Path(tmp)
            separator = self._load(spec, work)
            report(0.35)
            with _track_chunks(report, 0.35, 0.95):
                separator.separate(str(input_path), custom_output_names={"noreverb": "dry", "reverb": "wet"})
            dry_files = list(work.glob("dry*"))
            if not dry_files:
                raise RuntimeError("Das De-Reverb-Modell hat keine Datei geliefert.")
            # back to the loop's own rate, channels and length first
            dry = conform(dry_files[0], reference, work / "dry_conformed.wav")
            processed, _ = sf.read(str(dry), dtype="float32", always_2d=True)
        out = restore_bass(original, processed, sr)
        sf.write(str(target), out, sr, subtype="PCM_24")
        report(1.0)
        return target


_default: DereverbEngine | None = None


def dereverb(input_path, output=None, progress=None, status=None) -> Path:
    global _default
    if _default is None:
        _default = DereverbEngine()
    return _default.run(input_path, output=output, progress=progress, status=status)
