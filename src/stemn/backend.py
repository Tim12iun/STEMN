"""Thin adapter around python-audio-separator.

audio-separator normally downloads models from its own catalogue. stemn instead
loads models only from local folders (built-in or imported by the user), so it
works offline and accepts models that are not in that catalogue.
"""

from __future__ import annotations

import logging
from pathlib import Path

from audio_separator.separator import Separator

from .models import ModelSpec

_ARCH_TO_TYPE = {"mdxc": "MDXC", "demucs": "Demucs"}


class LocalSeparator(Separator):
    def __init__(self, **kwargs):
        kwargs.setdefault("log_level", logging.WARNING)
        kwargs.setdefault("use_soundfile", True)  # no ffmpeg needed for WAV/AIFF/FLAC
        super().__init__(**kwargs)
        self._specs: dict[str, ModelSpec] = {}

    # ffmpeg is only needed for mp3/m4a; stemn writes WAV via soundfile.
    def check_ffmpeg_installed(self):  # noqa: D401
        return None

    def register(self, spec: ModelSpec) -> None:
        self._specs[spec.checkpoint.name] = spec
        self.model_file_dir = str(spec.folder)

    def download_model_files(self, model_filename):
        spec = self._specs.get(model_filename)
        if spec is None:
            raise ValueError(f"Modell {model_filename} ist nicht registriert")
        model_type = _ARCH_TO_TYPE[spec.arch]
        if spec.arch == "demucs":
            # Demucs models are described by their YAML; the .th files sit next to it.
            return spec.config.name, model_type, spec.name, str(spec.config), None
        self.model_friendly_name = spec.name
        self.model_is_uvr_vip = False
        return spec.checkpoint.name, model_type, spec.name, str(spec.checkpoint), str(spec.config)

    def set_output_dir(self, path: Path) -> None:
        self.output_dir = str(path)
        if self.model_instance is not None:
            self.model_instance.output_dir = str(path)
