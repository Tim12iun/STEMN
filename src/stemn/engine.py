"""Separation engine: runs a drum-separation model and writes one WAV per stem.

The output files are sample-exact to the input (same length and sample rate),
so a stem can be dropped onto the same position in a DAW and stays on the grid.
"""

from __future__ import annotations

import logging
import os
import re
import tempfile
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np
import soundfile as sf

from .models import ModelSpec, canonical_stem, get_model

log = logging.getLogger("stemn")

# Quality presets: number of overlapping windows per chunk. 2 sounds the same as 4
# in our tests (<0.01 % difference) at half the time.
QUALITY_OVERLAP = {"fast": 1, "normal": 2, "best": 4}

# Virtual stems that are built by summing model stems if the model has no such stem.
VIRTUAL_STEMS = {"cymbals": ("ride", "crash")}


def _safe_name(text: str) -> str:
    return re.sub(r"[^\w\-. ]+", "_", text).strip() or "audio"


def match_length(audio: np.ndarray, frames: int) -> np.ndarray:
    """Pad with silence or trim so ``audio`` (frames, channels) has exactly ``frames``."""
    if audio.shape[0] == frames:
        return audio
    if audio.shape[0] > frames:
        return audio[:frames]
    pad = np.zeros((frames - audio.shape[0],) + audio.shape[1:], dtype=audio.dtype)
    return np.concatenate([audio, pad], axis=0)


def match_channels(audio: np.ndarray, channels: int) -> np.ndarray:
    if audio.ndim == 1:
        audio = audio[:, None]
    if audio.shape[1] == channels:
        return audio
    if channels == 1:
        return audio.mean(axis=1, keepdims=True)
    return np.repeat(audio[:, :1], channels, axis=1)


def conform(stem_file: Path, reference: sf._SoundFileInfo, target: Path) -> Path:
    """Rewrite ``stem_file`` to the reference's sample rate, channel count and length."""
    audio, sr = sf.read(str(stem_file), dtype="float32", always_2d=True)
    if sr != reference.samplerate:
        import librosa  # dependency of audio-separator

        audio = librosa.resample(audio.T, orig_sr=sr, target_sr=reference.samplerate).T
    audio = match_channels(audio, reference.channels)
    audio = match_length(audio, reference.frames)
    sf.write(str(target), audio, reference.samplerate, subtype="PCM_24")
    return target


class Engine:
    """Keeps one model loaded so repeated calls (Max for Live, app) are fast."""

    def __init__(self) -> None:
        self._separator = None
        self._model_id: str | None = None
        self._lock = threading.Lock()

    @staticmethod
    def _overlap(quality: str) -> int:
        if quality not in QUALITY_OVERLAP:
            raise ValueError(f"Qualitaet '{quality}' unbekannt ({', '.join(QUALITY_OVERLAP)})")
        return QUALITY_OVERLAP[quality]

    def _load(self, spec: ModelSpec, work_dir: Path, overlap: int):
        from .backend import LocalSeparator

        if self._separator is None:
            self._separator = LocalSeparator(
                output_dir=str(work_dir),
                model_file_dir=str(spec.folder),
                output_format="WAV",
                normalization_threshold=1.0,  # keep original levels; stems should sum back to the loop
                log_level=logging.ERROR,
            )
        if self._model_id != spec.id:
            self._separator.register(spec)
            self._separator.load_model(model_filename=spec.checkpoint.name)
            self._model_id = spec.id
        self._separator.set_output_dir(work_dir)
        if hasattr(self._separator.model_instance, "overlap"):
            self._separator.model_instance.overlap = overlap
        return self._separator

    def separate(
        self,
        input_path: str | Path,
        stems: list[str] | None = None,
        model_id: str | None = None,
        output_dir: str | Path | None = None,
        quality: str = "best",
        progress=None,
        bleed: str = "off",
        status=None,
    ) -> dict[str, Path]:
        """Separate ``input_path``; return ``{canonical_stem: wav_path}``.

        ``stems`` limits the files written (e.g. ``["kick"]``); ``None`` writes all.
        ``progress`` is called with values from 0 to 1.
        ``bleed`` ("off", "light", "strong") damps cross-talk between the stems first.
        ``status`` (optional) gets short German lines on what is happening, e.g. time left.
        """
        report = progress or (lambda _v: None)
        report(0.0)
        overlap = self._overlap(quality)
        from .bleed import BLEED_POWER

        if bleed not in BLEED_POWER:
            raise ValueError(f"Uebersprechen '{bleed}' unbekannt ({', '.join(BLEED_POWER)})")
        input_path = Path(input_path).expanduser().resolve()
        if not input_path.is_file():
            raise FileNotFoundError(f"Datei nicht gefunden: {input_path}")
        spec = get_model(model_id)
        wanted = [canonical_stem(s) for s in stems] if stems else None
        available = set(spec.canonical_stems) | {v for v, parts in VIRTUAL_STEMS.items() if set(parts) <= set(spec.canonical_stems)}
        if wanted:
            unknown = [s for s in wanted if s not in available]
            if unknown:
                raise ValueError(f"Modell '{spec.id}' kennt {', '.join(unknown)} nicht. Verfuegbar: {', '.join(sorted(available))}")

        out_dir = Path(output_dir).expanduser().resolve() if output_dir else input_path.parent / f"{input_path.stem} stems"
        out_dir.mkdir(parents=True, exist_ok=True)
        reference = sf.info(str(input_path))
        base = _safe_name(input_path.stem)

        # the app hands its own folder in, so a stopped run leaves nothing behind
        work_root = os.environ.get("STEMN_WORK_DIR") or None
        with self._lock, tempfile.TemporaryDirectory(prefix="stemn-", dir=work_root) as tmp:
            work = Path(tmp)
            separator = self._load(spec, work, overlap)
            report(0.08)
            names = {raw: f"stem_{canonical_stem(raw)}" for raw in spec.stems}
            split_end = 0.95
            with _track_chunks(report, 0.08, split_end):
                separator.separate(str(input_path), custom_output_names=names)
            report(split_end)

            raw_files = {canonical_stem(p.stem.removeprefix("stem_")): p for p in work.glob("stem_*")}
            # always: the stems add up to exactly the original loop (nothing the separation lost goes missing)
            raw_files = _reduce_bleed_files(raw_files, bleed, input_path)
            result: dict[str, Path] = {}
            for stem, path in raw_files.items():
                if wanted is None or stem in wanted:
                    result[stem] = conform(path, reference, out_dir / f"{base} - {stem}.wav")

            for virtual, parts in VIRTUAL_STEMS.items():
                if virtual in raw_files or (wanted is not None and virtual not in wanted):
                    continue
                if not all(p in raw_files for p in parts):
                    continue
                if wanted is None:
                    continue  # do not duplicate ride/crash when writing everything
                mix = None
                for part in parts:
                    audio, _ = sf.read(str(raw_files[part]), dtype="float32", always_2d=True)
                    mix = audio if mix is None else mix[: len(audio)] + audio[: len(mix)]
                tmp_mix = work / f"mix_{virtual}.wav"
                sf.write(str(tmp_mix), mix, sf.info(str(raw_files[parts[0]])).samplerate, subtype="FLOAT")
                result[virtual] = conform(tmp_mix, reference, out_dir / f"{base} - {virtual}.wav")

        report(1.0)
        return result


def _reduce_bleed_files(raw_files: dict[str, Path], level: str, original: Path | None = None) -> dict[str, Path]:
    from .bleed import reduce_bleed

    audio, rate = {}, None
    for stem, path in raw_files.items():
        audio[stem], rate = sf.read(str(path), dtype="float32", always_2d=True)
    mix = None
    if original is not None:
        try:
            mix, sr = sf.read(str(original), dtype="float32", always_2d=True)
            if sr != rate:
                import librosa

                mix = librosa.resample(mix.T, orig_sr=sr, target_sr=rate).T
        except Exception:
            mix = None  # unreadable by soundfile (e.g. mp3): skip, the stems stay as separated
    cleaned = reduce_bleed(audio, level, mix=mix)
    out = {}
    for stem, path in raw_files.items():
        target = path.with_name(path.stem + "_clean.wav")
        sf.write(str(target), cleaned[stem], rate, subtype="FLOAT")
        out[stem] = target
    return out


@contextmanager
def _track_chunks(report, start: float, end: float):
    """Report progress while audio-separator works through its chunks.

    audio-separator counts its chunks with tqdm; stemn swaps that counter for
    one that calls ``report``.
    """
    try:
        from audio_separator.separator.architectures import mdxc_separator as module
    except Exception:  # pragma: no cover - other architectures: no fine progress
        yield
        return

    original = module.tqdm

    def counting(iterable, *args, **kwargs):
        items = list(iterable)
        total = max(len(items), 1)
        for i, item in enumerate(items):
            report(start + (end - start) * i / total)
            yield item
        report(end)

    module.tqdm = counting
    try:
        yield
    finally:
        module.tqdm = original


_default_engine: Engine | None = None


def default_engine() -> Engine:
    global _default_engine
    if _default_engine is None:
        _default_engine = Engine()
    return _default_engine


def separate(
    input_path, stems=None, model_id=None, output_dir=None, quality="best", progress=None, bleed="off", status=None
) -> dict[str, Path]:
    return default_engine().separate(
        input_path, stems=stems, model_id=model_id, output_dir=output_dir, quality=quality, progress=progress, bleed=bleed, status=status
    )
