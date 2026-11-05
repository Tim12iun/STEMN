"""Optional second step after separation: restore each stem with Apollo.

Separation leaves gaps where the drums overlapped (dull highs, thin toms,
"watery" tails). A restoration model regenerates such missing detail from what
it learned. stemn runs it per stem, in short overlapping pieces, on the CPU.

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
CHUNK_SECONDS = 3.0  # Apollo works in short pieces (10 s pieces needed ~7 GB RAM, 3 s ~2.5 GB) ...
FADE_SECONDS = 0.3  # ... that overlap and crossfade, so no seam is audible


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


QUIET_PEAK = 10 ** (-50 / 20)  # pieces quieter than -50 dBFS have nothing worth repairing


class _Pointwise:
    """Stand-in for a 1x1 ``Conv1d``: a plain matrix product, which goes through the fast
    BLAS of the platform (Accelerate on the Mac) instead of the generic convolution code."""

    @staticmethod
    def swap(module) -> int:
        import torch
        from torch import nn

        class Pointwise(nn.Module):
            def __init__(self, conv: nn.Conv1d) -> None:
                super().__init__()
                self.register_buffer("weight", conv.weight.detach()[:, :, 0].contiguous())
                self.register_buffer("bias", None if conv.bias is None else conv.bias.detach()[:, None].contiguous())

            def forward(self, x):  # B, C, T
                y = torch.matmul(self.weight, x)
                return y if self.bias is None else y + self.bias

        count = 0
        for name, child in module.named_children():
            if (
                isinstance(child, nn.Conv1d)
                and child.kernel_size == (1,)
                and child.groups == 1
                and child.stride == (1,)
                and child.padding in (0, (0,))
                and child.dilation == (1,)
            ):
                setattr(module, name, Pointwise(child))
                count += 1
            else:
                count += _Pointwise.swap(child)
        return count


def _pick_device(torch):
    """The Mac's graphics chip when it is there, otherwise the processor."""
    try:
        if torch.backends.mps.is_available():
            return torch.device("mps")
    except Exception:
        pass
    return torch.device("cpu")


class Restorer:
    """Loads Apollo once and restores stems (numpy arrays, frames x channels)."""

    def __init__(self, spec: RestorerSpec, device: str | None = None) -> None:
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
        _Pointwise.swap(net)
        self._torch = torch
        self._cpu_net = net
        import os

        device = device or os.environ.get("STEMN_APOLLO_DEVICE") or None
        self.device = torch.device(device) if device else _pick_device(torch)
        self._net = net
        if self.device.type != "cpu":
            try:
                import copy

                self._net = copy.deepcopy(net).to(self.device)
            except Exception:
                self.device, self._net = torch.device("cpu"), net
        print(f"stemn: Apollo rechnet auf {'dem Grafikchip' if self.device.type == 'mps' else 'dem Prozessor'} ({self.device.type})", file=sys.stderr)

    def _run(self, piece: np.ndarray) -> np.ndarray:
        """One piece through the network; falls back to the processor if the graphics chip fails."""
        torch = self._torch
        x = torch.from_numpy(np.ascontiguousarray(piece.T)).unsqueeze(0)  # 1, ch, n
        if self.device.type != "cpu":
            try:
                y = self._net(x.to(self.device))[0].float().cpu().numpy().T
                if np.all(np.isfinite(y)) and float(np.abs(y).max(initial=0.0)) < 8.0:
                    return y
            except Exception:
                pass
            print("stemn: Grafikchip liefert kein brauchbares Ergebnis, rechne auf dem Prozessor weiter", file=sys.stderr)
            self.device, self._net = torch.device("cpu"), self._cpu_net
        return self._net(x)[0].numpy().T

    def restore(self, audio: np.ndarray, progress=None, quiet_peak: float = QUIET_PEAK) -> np.ndarray:
        """Return the restored audio, same shape as ``audio`` (frames, channels), float32.

        Pieces whose peak stays below ``quiet_peak`` are passed through unchanged: there is
        nothing in them to repair, and skipping them saves most of the time on sparse stems.
        """
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
                if float(np.abs(piece).max()) < quiet_peak:
                    y = piece
                else:
                    y = self._run(piece)[:n]
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
