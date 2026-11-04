"""Model registry: built-in (bundled) models plus models the user imports later.

Every model lives in its own folder with a ``stemn-model.yaml`` manifest::

    name: MDX23C DrumSep (aufr33 & jarredou)
    arch: mdxc            # mdxc (MDX23C / Roformer) or demucs
    checkpoint: MDX23C-DrumSep-aufr33-jarredou.ckpt
    config: config_drumsep_mdx23c.yaml
    stems: [kick, snare, toms, hh, ride, crash]   # optional, read from config

Users add models with ``stemn models import`` (or by dropping such a folder into
the user model directory); no code changes are needed.
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

MANIFEST = "stemn-model.yaml"
SUPPORTED_ARCHS = ("mdxc", "demucs")
DEFAULT_MODEL_ID = "drumsep-mdx23c"

# Canonical stem names and the names different models use for them.
STEM_ALIASES: dict[str, tuple[str, ...]] = {
    "kick": ("kick", "bombo", "bd", "bassdrum", "bass drum"),
    "snare": ("snare", "redoblante", "sd"),
    "hihat": ("hh", "hihat", "hi-hat", "hi hat", "hat", "hats"),
    "toms": ("toms", "tom"),
    "ride": ("ride",),
    "crash": ("crash",),
    "cymbals": ("cymbals", "platillos", "cymbal", "overheads"),
}


def canonical_stem(name: str) -> str:
    """Map a model's stem name (e.g. ``hh``, ``Bombo``) to stemn's canonical name."""
    key = name.strip().lower()
    for canonical, aliases in STEM_ALIASES.items():
        if key == canonical or key in aliases:
            return canonical
    return re.sub(r"[^a-z0-9]+", "-", key).strip("-") or "stem"


@dataclass
class ModelSpec:
    id: str
    name: str
    arch: str
    folder: Path
    checkpoint: Path
    config: Path
    stems: list[str] = field(default_factory=list)  # names as the model reports them
    builtin: bool = False

    @property
    def canonical_stems(self) -> list[str]:
        return [canonical_stem(s) for s in self.stems]

    def is_available(self) -> bool:
        return self.checkpoint.is_file() and self.config.is_file()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "arch": self.arch,
            "stems": self.canonical_stems,
            "builtin": self.builtin,
            "available": self.is_available(),
            "folder": str(self.folder),
        }


class ModelError(Exception):
    pass


def _stems_from_config(config_path: Path) -> list[str]:
    try:
        with open(config_path, encoding="utf-8") as fh:
            data = yaml.load(fh, Loader=yaml.FullLoader) or {}
    except (OSError, yaml.YAMLError):
        return []
    training = data.get("training") or {}
    stems = training.get("instruments") or data.get("sources") or []
    return [str(s) for s in stems]


def load_manifest(folder: Path, builtin: bool = False) -> ModelSpec:
    manifest_path = folder / MANIFEST
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise ModelError(f"{manifest_path}: {exc}") from exc

    for key in ("arch", "checkpoint", "config"):
        if not data.get(key):
            raise ModelError(f"{manifest_path}: '{key}' fehlt")
    arch = str(data["arch"]).lower()
    if arch not in SUPPORTED_ARCHS:
        raise ModelError(f"{manifest_path}: arch '{arch}' wird nicht unterstuetzt ({', '.join(SUPPORTED_ARCHS)})")

    config = folder / data["config"]
    stems = [str(s) for s in data.get("stems") or []] or _stems_from_config(config)
    return ModelSpec(
        id=str(data.get("id") or folder.name),
        name=str(data.get("name") or folder.name),
        arch=arch,
        folder=folder,
        checkpoint=folder / data["checkpoint"],
        config=config,
        stems=stems,
        builtin=builtin,
    )


def bundled_models_dir() -> Path:
    """Models shipped with the app (installer) or the source checkout."""
    env = os.environ.get("STEMN_BUNDLED_MODELS")
    if env:
        return Path(env)
    frozen_root = getattr(sys, "_MEIPASS", None)  # PyInstaller
    if frozen_root:
        return Path(frozen_root) / "models"
    return Path(__file__).resolve().parents[2] / "models"


def user_models_dir() -> Path:
    env = os.environ.get("STEMN_MODELS_DIR")
    if env:
        return Path(env)
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "stemn" / "models"


def discover_models(dirs: list[tuple[Path, bool]] | None = None) -> dict[str, ModelSpec]:
    """Return all models found, keyed by id. User models never shadow built-ins."""
    if dirs is None:
        dirs = [(bundled_models_dir(), True), (user_models_dir(), False)]
    found: dict[str, ModelSpec] = {}
    for root, builtin in dirs:
        if not root.is_dir():
            continue
        for folder in sorted(p for p in root.iterdir() if (p / MANIFEST).is_file()):
            try:
                spec = load_manifest(folder, builtin=builtin)
            except ModelError as exc:
                print(f"stemn: Modell uebersprungen: {exc}", file=sys.stderr)
                continue
            if spec.id in found:
                continue
            found[spec.id] = spec
    return found


def get_model(model_id: str | None = None, models: dict[str, ModelSpec] | None = None) -> ModelSpec:
    models = discover_models() if models is None else models
    if not models:
        raise ModelError("Keine Modelle gefunden. Ist die Installation vollstaendig?")
    if model_id is None:
        model_id = DEFAULT_MODEL_ID if DEFAULT_MODEL_ID in models else next(iter(models))
    if model_id not in models:
        raise ModelError(f"Unbekanntes Modell '{model_id}'. Verfuegbar: {', '.join(models)}")
    spec = models[model_id]
    if not spec.is_available():
        raise ModelError(f"Modelldateien fuer '{model_id}' fehlen in {spec.folder}")
    return spec


def import_model(
    checkpoint: Path,
    config: Path,
    name: str | None = None,
    model_id: str | None = None,
    arch: str = "mdxc",
    target_root: Path | None = None,
) -> ModelSpec:
    """Copy a checkpoint + config into the user model folder and write a manifest."""
    checkpoint, config = Path(checkpoint), Path(config)
    for f in (checkpoint, config):
        if not f.is_file():
            raise ModelError(f"Datei nicht gefunden: {f}")
    arch = arch.lower()
    if arch not in SUPPORTED_ARCHS:
        raise ModelError(f"arch '{arch}' wird nicht unterstuetzt ({', '.join(SUPPORTED_ARCHS)})")
    stems = _stems_from_config(config)
    if not stems:
        raise ModelError(f"In {config.name} stehen keine Stems (training.instruments oder sources).")

    model_id = model_id or re.sub(r"[^a-z0-9]+", "-", (name or checkpoint.stem).lower()).strip("-")
    root = target_root or user_models_dir()
    folder = root / model_id
    if folder.exists():
        raise ModelError(f"Modell '{model_id}' existiert schon in {folder}")
    folder.mkdir(parents=True)
    shutil.copy2(checkpoint, folder / checkpoint.name)
    shutil.copy2(config, folder / config.name)
    manifest = {
        "id": model_id,
        "name": name or checkpoint.stem,
        "arch": arch,
        "checkpoint": checkpoint.name,
        "config": config.name,
    }
    with open(folder / MANIFEST, "w", encoding="utf-8") as fh:
        yaml.safe_dump(manifest, fh, sort_keys=False, allow_unicode=True)
    return load_manifest(folder)
