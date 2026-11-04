"""Optional second step after separation: restore each stem with Apollo.

Separation leaves gaps where the drums overlapped (dull highs, thin toms,
"watery" tails). A restoration model regenerates such missing detail from what
it learned. stemn runs it per stem, chunked, on the CPU.

Restoration models live next to the separation models, each in its own folder
with a ``stemn-restore.yaml`` manifest::

    id: apollo-uni
    name: Apollo Universal (Lew)
    arch: apollo
    checkpoint: apollo_model_uni.ckpt
    model: {sr: 44100, win: 20, feature_dim: 384, layer: 6}
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from .models import bundled_models_dir, user_models_dir

RESTORE_MANIFEST = "stemn-restore.yaml"
DEFAULT_RESTORER_ID = "apollo-uni"
CHUNK_SECONDS = 10.0  # longer loops are processed in pieces ...
FADE_SECONDS = 0.5  # ... that overlap and crossfade, so no seam is audible


class RestoreError(Exception):
    pass


@dataclass
class RestorerSpec:
    id: str
    name: str
    folder: Path
    checkpoint: Path
    model: dict = field(default_factory=dict)
    builtin: bool = False

    @property
    def sample_rate(self) -> int:
        return int(self.model.get("sr", 44100))

    def is_available(self) -> bool:
        return self.checkpoint.is_file()


def load_restore_manifest(folder: Path, builtin: bool = False) -> RestorerSpec:
    path = folder / RESTORE_MANIFEST
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise RestoreError(f"{path}: {exc}") from exc
    if str(data.get("arch", "")).lower() != "apollo":
        raise RestoreError(f"{path}: nur arch 'apollo' wird unterstuetzt")
    if not data.get("checkpoint"):
        raise RestoreError(f"{path}: 'checkpoint' fehlt")
    model = dict(data.get("model") or {})
    for key in ("sr", "win", "feature_dim", "layer"):
        if key not in model:
            raise RestoreError(f"{path}: model.{key} fehlt")
    return RestorerSpec(
        id=str(data.get("id") or folder.name),
        name=str(data.get("name") or folder.name),
        folder=folder,
        checkpoint=folder / data["checkpoint"],
        model=model,
        builtin=builtin,
    )


def discover_restorers(dirs: list[tuple[Path, bool]] | None = None) -> dict[str, RestorerSpec]:
    if dirs is None:
        dirs = [(bundled_models_dir(), True), (user_models_dir(), False)]
    found: dict[str, RestorerSpec] = {}
    for root, builtin in dirs:
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if (p / RESTORE_MANIFEST).is_file()):
            try:
                spec = load_restore_manifest(folder, builtin=builtin)
            except RestoreError as exc:
                print(f"stemn: Reparatur-Modell uebersprungen: {exc}", file=sys.stderr)
                continue
            found.setdefault(spec.id, spec)
    return found


def get_restorer(restorer_id: str | None = None) -> RestorerSpec:
    found = discover_restorers()
    if restorer_id is None:
        ready = [r for r in found.values() if r.is_available()]
        default = found.get(DEFAULT_RESTORER_ID)
        restorer_id = DEFAULT_RESTORER_ID if default and default.is_available() else (ready[0].id if ready else None)
    if restorer_id is None or restorer_id not in found:
        raise RestoreError("Kein Reparatur-Modell gefunden. Ist die Installation vollstaendig?")
    spec = found[restorer_id]
    if not spec.is_available():
        raise RestoreError(f"Modelldatei fuer '{spec.id}' fehlt in {spec.folder}")
    return spec


def _state_dict(checkpoint: Path) -> dict:
    import torch

    try:
        data = torch.load(str(checkpoint), map_location="cpu", weights_only=True)
    except Exception:
        # Lightning checkpoints carry extra objects; the file is checked by SHA-256 at build time.
        data = torch.load(str(checkpoint), map_location="cpu", weights_only=False)
    if isinstance(data, dict) and isinstance(data.get("state_dict"), dict):
        data = data["state_dict"]
    if not isinstance(data, dict):
        raise RestoreError(f"{checkpoint.name}: unbekanntes Format")
    return data


def _strip_prefixes(state: dict, expected: set[str]) -> dict:
    """Training wrappers prefix the weights (``audio_model.``, ``model.``, ``module.``)."""
    if set(state) >= expected:
        return {k: v for k, v in state.items() if k in expected}
    for prefix in ("audio_model.", "model.", "module.", "net."):
        stripped = {k[len(prefix):]: v for k, v in state.items() if k.startswith(prefix)}
        if stripped and set(stripped) >= expected:
            return {k: v for k, v in stripped.items() if k in expected}
    return state


def _windows(frames: int, chunk: int, fade: int) -> list[int]:
    """Start positions of overlapping chunks that cover ``frames``."""
    if frames <= chunk:
        return [0]
    step = chunk - fade
    starts = list(range(0, frames - chunk, step))
    starts.append(frames - chunk)
    return starts


class Restorer:
    """Loads Apollo once and restores stems (numpy arrays, frames x channels)."""

    def __init__(self, spec: RestorerSpec) -> None:
        import torch

        from .apollo import Apollo

        self.spec = spec
        self.sample_rate = spec.sample_rate
        net = Apollo(**{k: spec.model[k] for k in ("sr", "win", "feature_dim", "layer")})
        expected = set(net.state_dict())
        state = _strip_prefixes(_state_dict(spec.checkpoint), expected)
        missing = expected - set(state)
        if missing:
            raise RestoreError(
                f"{spec.checkpoint.name} passt nicht zu den Modell-Einstellungen ({len(missing)} Gewichte fehlen, z.B. {sorted(missing)[0]})"
            )
        net.load_state_dict(state)
        net.eval()
        self._net = net
        self._torch = torch

    def restore(self, audio: np.ndarray, progress=None) -> np.ndarray:
        """Return the restored audio, same shape as ``audio`` (frames, channels), float32."""
        torch = self._torch
        audio = np.asarray(audio, dtype=np.float32)
        if audio.ndim == 1:
            audio = audio[:, None]
        frames, channels = audio.shape
        if frames == 0 or not np.any(audio):
            return audio.copy()  # silence stays silence
        chunk = int(CHUNK_SECONDS * self.sample_rate)
        fade = int(FADE_SECONDS * self.sample_rate)
        starts = _windows(frames, chunk, fade)
        out = np.zeros_like(audio)
        weight = np.zeros((frames, 1), dtype=np.float32)
        with torch.inference_mode():
            for i, start in enumerate(starts):
                piece = audio[start : start + chunk]
                n = piece.shape[0]
                x = torch.from_numpy(np.ascontiguousarray(piece.T)).unsqueeze(0)  # 1, ch, n
                y = self._net(x)[0].numpy().T[:n]
                w = np.ones((n, 1), dtype=np.float32)
                if len(starts) > 1:
                    ramp = np.linspace(0.0, 1.0, min(fade, n), dtype=np.float32)[:, None]
                    if start > 0:
                        w[: len(ramp)] = ramp
                    if start + n < frames:
                        w[n - len(ramp) :] = ramp[::-1]
                out[start : start + n] += y * w
                weight[start : start + n] += w
                if progress:
                    progress((i + 1) / len(starts))
        out /= np.maximum(weight, 1e-6)
        return out.astype(np.float32)
