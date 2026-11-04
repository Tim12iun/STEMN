"""stemn desktop app: drop a loop in, listen to the stems, drag them into Ableton."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QMimeData, QObject, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QDrag, QFont
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .models import discover_models

AUDIO_EXT = {".wav", ".aif", ".aiff", ".flac"}
STEM_LABELS = {
    "kick": "Kick",
    "snare": "Snare",
    "hihat": "Hi-Hat",
    "toms": "Toms",
    "ride": "Ride",
    "crash": "Crash",
    "cymbals": "Becken",
}
STEM_ORDER = ["kick", "snare", "hihat", "toms", "ride", "crash", "cymbals"]
QUALITIES = [("Normal", "normal"), ("Schnell", "fast"), ("Beste", "best")]

STYLE = """
QWidget { background: #17181b; color: #e8e8ea; font-size: 13px; }
QFrame#drop { border: 2px dashed #4a4d55; border-radius: 10px; background: #1e2024; }
QFrame#drop[hover="true"] { border-color: #f5a524; background: #24262b; }
QFrame#row { background: #1f2125; border-radius: 8px; }
QFrame#row QLabel { background: transparent; }
QPushButton { background: #2a2d33; border: 1px solid #3a3d44; border-radius: 6px; padding: 6px 12px; }
QPushButton:hover { background: #33363d; }
QPushButton:checked#mute { background: #6b2b2b; border-color: #a33; }
QPushButton:checked#solo { background: #6b5a1f; border-color: #d9a520; }
QPushButton:disabled { color: #666; }
QLabel#grip { background: #2d3a52; border-radius: 6px; padding: 6px 10px; color: #cfe0ff; }
QLabel#grip:hover { background: #3a4b6b; }
QComboBox { background: #2a2d33; border: 1px solid #3a3d44; border-radius: 6px; padding: 4px 8px; }
QProgressBar { background: #2a2d33; border: none; border-radius: 4px; height: 8px; }
QProgressBar::chunk { background: #f5a524; border-radius: 4px; }
"""


# --------------------------------------------------------------------------- audio


class Player:
    """Loops the stems in sync; mute/solo changes take effect immediately."""

    def __init__(self) -> None:
        self.tracks: dict[str, np.ndarray] = {}
        self.gains: dict[str, float] = {}
        self.sr = 44100
        self.pos = 0
        self.length = 0
        self.stream = None
        self.error: str | None = None
        self._lock = threading.Lock()

    def load(self, files: dict[str, Path]) -> None:
        self.stop()
        tracks = {}
        for stem, path in files.items():
            audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
            if audio.shape[1] == 1:
                audio = np.repeat(audio, 2, axis=1)
            tracks[stem] = audio[:, :2]
            self.sr = sr
        with self._lock:
            self.tracks = tracks
            self.gains = {k: 1.0 for k in tracks}
            self.length = min((len(a) for a in tracks.values()), default=0)
            self.pos = 0

    def set_gains(self, gains: dict[str, float]) -> None:
        with self._lock:
            self.gains.update(gains)

    def _callback(self, out, frames, _time, _status):
        with self._lock:
            out.fill(0)
            if not self.length:
                return
            written = 0
            while written < frames:
                n = min(frames - written, self.length - self.pos)
                for stem, audio in self.tracks.items():
                    g = self.gains.get(stem, 0.0)
                    if g:
                        out[written : written + n] += g * audio[self.pos : self.pos + n]
                written += n
                self.pos = (self.pos + n) % self.length

    @property
    def playing(self) -> bool:
        return self.stream is not None

    def play(self) -> bool:
        if self.playing or not self.length:
            return self.playing
        try:
            import sounddevice as sd

            self.stream = sd.OutputStream(samplerate=self.sr, channels=2, dtype="float32", callback=self._callback)
            self.stream.start()
            return True
        except Exception as exc:  # no audio device / PortAudio missing
            self.error = str(exc)
            self.stream = None
            return False

    def stop(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        self.pos = 0


# --------------------------------------------------------------------------- worker


class SeparateWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)

    def __init__(self, path: Path, model_id: str | None, quality: str) -> None:
        super().__init__()
        self.path, self.model_id, self.quality = path, model_id, quality

    def run(self) -> None:
        try:
            from .engine import default_engine

            out = default_engine().separate(self.path, model_id=self.model_id, quality=self.quality)
            self.finished.emit(out)
        except Exception as exc:
            self.failed.emit(str(exc))


# --------------------------------------------------------------------------- widgets


def start_file_drag(widget: QWidget, path: Path) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    drag = QDrag(widget)
    drag.setMimeData(mime)
    drag.exec(Qt.CopyAction)


class DragGrip(QLabel):
    """Grab this and drop it into Ableton (or Finder/Explorer)."""

    def __init__(self, text: str, path_provider) -> None:
        super().__init__(text)
        self.setObjectName("grip")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip("In Ableton ins Arrangement oder auf eine Spur ziehen")
        self._path_provider = path_provider
        self._press = None

    def mousePressEvent(self, e):
        self._press = e.position()

    def mouseMoveEvent(self, e):
        if self._press is None or (e.position() - self._press).manhattanLength() < 6:
            return
        self._press = None
        path = self._path_provider()
        if path:
            start_file_drag(self, path)


class StemRow(QFrame):
    changed = Signal()

    def __init__(self, stem: str, path: Path) -> None:
        super().__init__()
        self.setObjectName("row")
        self.stem, self.path = stem, path
        lay = QHBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 8)
        name = QLabel(STEM_LABELS.get(stem, stem.title()))
        name.setFont(QFont(name.font().family(), 14, QFont.DemiBold))
        name.setMinimumWidth(90)
        self.level = QLabel("")
        self.level.setStyleSheet("color:#8a8d95;")
        self.mute = QPushButton("M")
        self.mute.setObjectName("mute")
        self.mute.setCheckable(True)
        self.mute.setToolTip("Stummschalten")
        self.solo = QPushButton("S")
        self.solo.setObjectName("solo")
        self.solo.setCheckable(True)
        self.solo.setToolTip("Solo")
        for b in (self.mute, self.solo):
            b.setFixedWidth(36)
            b.toggled.connect(self._on_toggled)
        grip = DragGrip("⠿  ziehen", self._drag_path)
        lay.addWidget(name)
        lay.addWidget(self.level, 1)
        lay.addWidget(self.mute)
        lay.addWidget(self.solo)
        lay.addWidget(grip)
        try:
            audio, _ = sf.read(str(path), dtype="float32")
            peak = float(np.abs(audio).max()) if audio.size else 0.0
            db = 20 * np.log10(max(peak, 1e-9))
            self.level.setText("fast leer" if db < -35 else f"Pegel {db:.0f} dB")
        except Exception:
            pass


    def _on_toggled(self, _checked: bool) -> None:
        self.changed.emit()

    def _drag_path(self) -> Path:
        return self.path


class DropArea(QFrame):
    dropped = Signal(Path)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("drop")
        self.setAcceptDrops(True)
        self.setMinimumHeight(110)
        self.setCursor(Qt.PointingHandCursor)
        lay = QVBoxLayout(self)
        self.label = QLabel("Drum-Loop hier reinziehen\noder klicken zum Öffnen")
        self.label.setAlignment(Qt.AlignCenter)
        self.label.setStyleSheet("font-size:15px; color:#b8bac0; background:transparent;")
        lay.addWidget(self.label)

    def _set_hover(self, on: bool) -> None:
        self.setProperty("hover", "true" if on else "false")
        self.style().unpolish(self)
        self.style().polish(self)

    @staticmethod
    def _audio_path(e) -> Path | None:
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.suffix.lower() in AUDIO_EXT:
                return p
        return None

    def dragEnterEvent(self, e):
        if self._audio_path(e):
            self._set_hover(True)
            e.acceptProposedAction()

    def dragLeaveEvent(self, e):
        self._set_hover(False)

    def dropEvent(self, e):
        self._set_hover(False)
        p = self._audio_path(e)
        if p:
            self.dropped.emit(p)

    def mousePressEvent(self, e):
        f, _ = QFileDialog.getOpenFileName(self, "Loop öffnen", "", "Audio (*.wav *.aif *.aiff *.flac)")
        if f:
            self.dropped.emit(Path(f))


# --------------------------------------------------------------------------- window


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"stemn {__version__}")
        self.resize(560, 620)
        self.player = Player()
        self.rows: list[StemRow] = []
        self.source: Path | None = None
        self.stems: dict[str, Path] = {}
        self._thread: QThread | None = None

        root = QWidget()
        self.setCentralWidget(root)
        lay = QVBoxLayout(root)
        lay.setContentsMargins(18, 18, 18, 18)
        lay.setSpacing(12)

        self.drop = DropArea()
        self.drop.dropped.connect(self.start)
        lay.addWidget(self.drop)

        opts = QHBoxLayout()
        self.model_box = QComboBox()
        for m in discover_models().values():
            if m.is_available():
                self.model_box.addItem(m.name, m.id)
        self.quality_box = QComboBox()
        for label, value in QUALITIES:
            self.quality_box.addItem(label, value)
        opts.addWidget(QLabel("Modell"))
        opts.addWidget(self.model_box, 1)
        opts.addWidget(QLabel("Qualität"))
        opts.addWidget(self.quality_box)
        lay.addLayout(opts)

        self.progress = QProgressBar()
        self.progress.setTextVisible(False)
        self.progress.setRange(0, 0)
        self.progress.hide()
        self.status = QLabel("")
        self.status.setStyleSheet("color:#8a8d95;")
        lay.addWidget(self.progress)
        lay.addWidget(self.status)

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(6)
        lay.addLayout(self.rows_box)
        lay.addStretch(1)

        bar = QHBoxLayout()
        self.play_btn = QPushButton("▶  Abspielen")
        self.play_btn.setEnabled(False)
        self.play_btn.clicked.connect(self.toggle_play)
        self.mix_grip = DragGrip("⠿  Mix ziehen", self.export_mix)
        self.mix_grip.setToolTip("Alle hörbaren Spuren als eine Datei ins Arrangement ziehen")
        self.mix_grip.setEnabled(False)
        self.folder_btn = QPushButton("Ordner öffnen")
        self.folder_btn.setEnabled(False)
        self.folder_btn.clicked.connect(self.open_folder)
        bar.addWidget(self.play_btn)
        bar.addStretch(1)
        bar.addWidget(self.folder_btn)
        bar.addWidget(self.mix_grip)
        lay.addLayout(bar)

        if self.model_box.count() == 0:
            self.drop.setEnabled(False)
            self.status.setText("Kein Modell gefunden. Installation unvollständig?")

    # ---- separation
    def start(self, path: Path) -> None:
        if self._thread is not None:
            return
        self.player.stop()
        self.play_btn.setText("▶  Abspielen")
        self.source = path
        self.clear_rows()
        self.drop.label.setText(path.name)
        self.status.setText("Trenne Spuren … das dauert je nach Rechner einige Sekunden.")
        self.progress.show()
        self.drop.setEnabled(False)

        self._thread = QThread(self)
        self._worker = SeparateWorker(path, self.model_box.currentData(), self.quality_box.currentData())
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self.on_done)
        self._worker.failed.connect(self.on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _cleanup_thread(self) -> None:
        self._thread = None
        self.drop.setEnabled(True)
        self.progress.hide()

    def on_failed(self, msg: str) -> None:
        self.status.setText("Fehler beim Trennen.")
        QMessageBox.warning(self, "stemn", msg)

    def on_done(self, stems: dict) -> None:
        self.stems = {k: Path(v) for k, v in stems.items()}
        for stem in sorted(self.stems, key=lambda s: STEM_ORDER.index(s) if s in STEM_ORDER else 99):
            row = StemRow(stem, self.stems[stem])
            row.changed.connect(self.update_gains)
            self.rows_box.addWidget(row)
            self.rows.append(row)
        self.player.load(self.stems)
        self.play_btn.setEnabled(True)
        self.mix_grip.setEnabled(True)
        self.folder_btn.setEnabled(True)
        self.status.setText(f"Fertig. Spuren liegen in „{next(iter(self.stems.values())).parent.name}“.")
        self.drop.label.setText(f"{self.source.name}\n\nNeuen Loop reinziehen zum Ersetzen")

    def clear_rows(self) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.stems = {}
        for w in (self.play_btn, self.mix_grip, self.folder_btn):
            w.setEnabled(False)

    # ---- playback
    def audible(self) -> list[str]:
        solo = [r.stem for r in self.rows if r.solo.isChecked()]
        if solo:
            return solo
        return [r.stem for r in self.rows if not r.mute.isChecked()]

    def update_gains(self) -> None:
        on = set(self.audible())
        self.player.set_gains({r.stem: 1.0 if r.stem in on else 0.0 for r in self.rows})

    def toggle_play(self) -> None:
        if self.player.playing:
            self.player.stop()
            self.play_btn.setText("▶  Abspielen")
            return
        self.update_gains()
        if self.player.play():
            self.play_btn.setText("■  Stop")
        else:
            QMessageBox.warning(self, "stemn", f"Wiedergabe nicht möglich: {self.player.error}")

    # ---- export
    def export_mix(self) -> Path | None:
        stems = self.audible()
        if not stems:
            return None
        if len(stems) == 1:
            return self.stems[stems[0]]
        mix = None
        sr = 44100
        for s in stems:
            audio, sr = sf.read(str(self.stems[s]), dtype="float32", always_2d=True)
            mix = audio if mix is None else mix + audio
        folder = next(iter(self.stems.values())).parent
        target = folder / f"{self.source.stem} - {'+'.join(stems)}.wav"
        sf.write(str(target), mix, sr, subtype="PCM_24")
        return target

    def open_folder(self) -> None:
        if self.stems:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(next(iter(self.stems.values())).parent)))

    def closeEvent(self, e):
        self.player.stop()
        super().closeEvent(e)


def _background_update() -> None:
    try:
        from .server import _auto_update

        _auto_update()
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("stemn")
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    threading.Thread(target=_background_update, daemon=True).start()
    if argv and len(argv) > 1 and Path(argv[1]).is_file():
        QTimer.singleShot(0, lambda: win.start(Path(argv[1])))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
