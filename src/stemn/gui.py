"""stemn desktop app: drop a loop in, listen to the stems, drag them into Ableton."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QMimeData, QObject, QPointF, QRectF, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSlider,
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
STEM_COLORS = {
    "kick": "#ff6b4a",
    "snare": "#ffd23f",
    "hihat": "#3ddcff",
    "toms": "#a77bff",
    "ride": "#5cf29a",
    "crash": "#ff6fd8",
    "cymbals": "#5cf29a",
}
QUALITIES = [("Normal", "normal"), ("Schnell", "fast"), ("Beste", "best")]
GAIN_MIN_DB, GAIN_MAX_DB = -30.0, 6.0
ENVELOPE_BINS = 4096

STYLE = """
QWidget { background: #111216; color: #e6e7ea; font-size: 12px; }
QFrame#topbar { background: #191a20; border-bottom: 1px solid #262830; }
QFrame#panel { background: #16171c; border-left: 1px solid #262830; }
QFrame#panel QLabel, QFrame#topbar QLabel { background: transparent; }
QLabel#logo { font-size: 18px; font-weight: 700; letter-spacing: 2px; color: #f5a524; }
QLabel#time { font-family: Menlo, Consolas, monospace; font-size: 13px; color: #cfd1d6; }
QLabel#muted { color: #8a8d95; background: transparent; }
QLabel#file { font-family: Menlo, Consolas, monospace; color: #cfd1d6; }
QPushButton { background: #23252c; border: 1px solid #33363e; border-radius: 5px; padding: 5px 10px; }
QPushButton:hover { background: #2c2f37; }
QPushButton:disabled { color: #5d6068; }
QPushButton#transport { min-width: 30px; font-size: 13px; }
QPushButton#transport:checked { background: #f5a524; color: #111; border-color: #f5a524; }
QPushButton#small { padding: 2px 0; min-width: 24px; max-width: 24px; font-size: 11px; font-weight: 600; }
QPushButton#small:checked[role="mute"] { background: #b8433a; border-color: #d9584e; color: #fff; }
QPushButton#small:checked[role="solo"] { background: #d9a520; border-color: #f5c242; color: #111; }
QFrame QLabel#grip { background: #263248; border-radius: 4px; padding: 3px 7px; color: #cfe0ff; }
QFrame QLabel#grip:hover { background: #33456a; }
QFrame QLabel#grip:disabled { background: #1f232c; color: #555; }
QFrame QLabel#db { font-family: Menlo, Consolas, monospace; background: #1d1f25; border: 1px solid #2a2c34;
            border-radius: 4px; padding: 2px 4px; min-width: 56px; }
QComboBox { background: #1d1f25; border: 1px solid #2a2c34; border-radius: 5px; padding: 4px 8px; }
QSlider::groove:horizontal { height: 4px; background: #2a2c34; border-radius: 2px; }
QSlider::handle:horizontal { width: 12px; height: 12px; margin: -5px 0; border-radius: 6px; background: #e6e7ea; }
"""


def db_to_gain(db: float) -> float:
    return 0.0 if db <= GAIN_MIN_DB else float(10 ** (db / 20))


def fmt_time(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m):02d}:{s:06.3f}"


# --------------------------------------------------------------------------- audio


class Player:
    """Loops the stems in sync; gain/mute/solo changes take effect immediately."""

    def __init__(self) -> None:
        self.tracks: dict[str, np.ndarray] = {}
        self.gains: dict[str, float] = {}
        self.sr = 44100
        self.pos = 0
        self.length = 0
        self.peak = 0.0
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

    def seek(self, frame: int) -> None:
        with self._lock:
            self.pos = int(min(max(frame, 0), max(self.length - 1, 0)))

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
            self.peak = max(self.peak * 0.85, float(np.abs(out).max()) if frames else 0.0)

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

    def pause(self) -> None:
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            finally:
                self.stream = None
        self.peak = 0.0

    def stop(self) -> None:
        self.pause()
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


# --------------------------------------------------------------------------- small widgets


def start_file_drag(widget: QWidget, path: Path) -> None:
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path))])
    drag = QDrag(widget)
    drag.setMimeData(mime)
    drag.exec(Qt.CopyAction)


class DragGrip(QLabel):
    """Grab this and drop it into Ableton (or Finder/Explorer)."""

    def __init__(self, text: str, path_provider, tooltip: str) -> None:
        super().__init__(text)
        self.setObjectName("grip")
        self.setCursor(Qt.OpenHandCursor)
        self.setToolTip(tooltip)
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


class Meter(QWidget):
    """Horizontal output level meter (-60 … 0 dBFS)."""

    def __init__(self) -> None:
        super().__init__()
        self.level = 0.0
        self.setFixedSize(150, 10)

    def set_peak(self, peak: float) -> None:
        db = 20 * np.log10(max(peak, 1e-6))
        level = min(max((db + 60) / 60, 0.0), 1.0)
        if abs(level - self.level) > 0.005:
            self.level = level
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        p.fillRect(r, QColor("#23252c"))
        grad = QLinearGradient(0, 0, r.width(), 0)
        grad.setColorAt(0.0, QColor("#3ddc84"))
        grad.setColorAt(0.8, QColor("#f5d142"))
        grad.setColorAt(1.0, QColor("#ff5a4a"))
        p.fillRect(QRectF(0, 0, r.width() * self.level, r.height()), grad)


class StemStrip(QFrame):
    """One stem in the side panel: colour, name, gain fader, dB, S, M, drag."""

    changed = Signal()

    def __init__(self, stem: str, path: Path) -> None:
        super().__init__()
        self.stem, self.path = stem, path
        color = STEM_COLORS.get(stem, "#cccccc")
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 4, 0, 4)
        lay.setSpacing(2)

        head = QHBoxLayout()
        dot = QLabel("●")
        dot.setStyleSheet(f"color:{color}; font-size:13px;")
        name = QLabel(STEM_LABELS.get(stem, stem.title()))
        name.setStyleSheet("font-weight:600;")
        self.hint = QLabel("")
        self.hint.setObjectName("muted")
        grip = DragGrip("⠿ ziehen", self._drag_path, "Diese Spur in Ableton ins Arrangement oder auf eine Spur ziehen")
        head.addWidget(dot)
        head.addWidget(name)
        head.addWidget(self.hint, 1)
        head.addWidget(grip)
        lay.addLayout(head)

        row = QHBoxLayout()
        self.fader = QSlider(Qt.Horizontal)
        self.fader.setRange(int(GAIN_MIN_DB * 10), int(GAIN_MAX_DB * 10))
        self.fader.setValue(0)
        self.fader.setStyleSheet(
            f"QSlider::sub-page:horizontal {{ background:{color}; border-radius:2px; }}"
            f"QSlider::handle:horizontal {{ background:{color}; }}"
        )
        self.fader.setToolTip("Lautstärke (Doppelklick auf den dB-Wert setzt 0 dB)")
        self.db = QLabel("+0.0 dB")
        self.db.setObjectName("db")
        self.db.setAlignment(Qt.AlignCenter)
        self.solo = QPushButton("S")
        self.solo.setObjectName("small")
        self.solo.setProperty("role", "solo")
        self.solo.setCheckable(True)
        self.solo.setToolTip("Solo")
        self.mute = QPushButton("M")
        self.mute.setObjectName("small")
        self.mute.setProperty("role", "mute")
        self.mute.setCheckable(True)
        self.mute.setToolTip("Stumm")
        row.addWidget(self.fader, 1)
        row.addWidget(self.db)
        row.addWidget(self.solo)
        row.addWidget(self.mute)
        lay.addLayout(row)

        self.fader.valueChanged.connect(self._on_fader)
        self.solo.toggled.connect(self._on_toggled)
        self.mute.toggled.connect(self._on_toggled)
        self.db.installEventFilter(self)

        try:
            audio, _ = sf.read(str(path), dtype="float32")
            peak = float(np.abs(audio).max()) if audio.size else 0.0
            if 20 * np.log10(max(peak, 1e-9)) < -35:
                self.hint.setText("fast leer")
        except Exception:
            pass

    def eventFilter(self, obj, event):
        from PySide6.QtCore import QEvent

        if obj is self.db and event.type() == QEvent.MouseButtonDblClick:
            self.fader.setValue(0)
            return True
        return super().eventFilter(obj, event)

    @property
    def gain_db(self) -> float:
        return self.fader.value() / 10

    def _on_fader(self, value: int) -> None:
        db = value / 10
        self.db.setText("−∞ dB" if db <= GAIN_MIN_DB else f"{db:+.1f} dB")
        self.changed.emit()

    def _on_toggled(self, _checked: bool) -> None:
        self.changed.emit()

    def _drag_path(self) -> Path:
        return self.path


# --------------------------------------------------------------------------- waveform


class WaveformView(QWidget):
    """All stems drawn on top of each other in their colours, with playhead."""

    seek = Signal(float)  # 0..1
    dropped = Signal(Path)

    def __init__(self) -> None:
        super().__init__()
        self.setAcceptDrops(True)
        self.setMinimumSize(420, 260)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        self.envelopes: dict[str, np.ndarray] = {}
        self.draw_order: list[str] = []
        self.active: set[str] = set()
        self.position = 0.0
        self.duration = 0.0
        self.message = "Drum-Loop hier reinziehen\noder oben auf „Import“ klicken"
        self._hover = False
        self._paths: dict[str, QPainterPath] = {}
        self._paths_size = None

    # data
    def set_stems(self, stems: dict[str, Path]) -> None:
        envs, energy = {}, {}
        for stem, path in stems.items():
            audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
            mono = np.abs(audio).max(axis=1)
            self.duration = len(mono) / sr
            n = min(ENVELOPE_BINS, max(len(mono), 1))
            edges = np.linspace(0, len(mono), n + 1).astype(int)
            envs[stem] = np.maximum.reduceat(mono, edges[:-1]) if len(mono) else np.zeros(1, np.float32)
            energy[stem] = float((mono**2).sum())
        self.envelopes = envs
        self.draw_order = sorted(envs, key=lambda s: energy[s], reverse=True)  # loud ones at the back
        self.active = set(envs)
        self.message = ""
        self._paths_size = None
        self.update()

    def clear(self, message: str) -> None:
        self.envelopes, self.draw_order, self.active = {}, [], set()
        self.position = 0.0
        self.duration = 0.0
        self.message = message
        self._paths_size = None
        self.update()

    def _build_paths(self) -> None:
        w, h = max(self.width(), 1), self.height()
        mid = h / 2
        self._paths = {}
        for stem, env in self.envelopes.items():
            edges = np.linspace(0, len(env), w + 1).astype(int)
            edges = np.minimum(edges, len(env) - 1)
            cols = np.maximum.reduceat(env, edges[:-1])
            amp = np.clip(cols, 0, 1) * (mid - 4)
            xs = np.arange(len(amp), dtype=float)
            path = QPainterPath(QPointF(0, mid))
            for x, y in zip(xs, amp):
                path.lineTo(QPointF(x, mid - y))
            for x, y in zip(xs[::-1], amp[::-1]):
                path.lineTo(QPointF(x, mid + y))
            path.closeSubpath()
            self._paths[stem] = path
        self._paths_size = (w, h)

    # painting
    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.fillRect(r, QColor("#0b0c0f"))
        mid = r.height() / 2

        if self.duration > 0:  # time grid
            step = 1.0 if self.duration <= 20 else 5.0
            t = step
            while t < self.duration:
                x = r.width() * t / self.duration
                p.setPen(QPen(QColor("#1c1e24"), 1))
                p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
                p.setPen(QColor("#4a4d55"))
                p.drawText(QPointF(x + 3, 12), f"{t:g}s")
                t += step
        p.setPen(QPen(QColor("#22242b"), 1))
        p.drawLine(QPointF(0, mid), QPointF(r.width(), mid))

        if self._hover:
            p.fillRect(r.adjusted(4, 4, -4, -4), QColor(245, 165, 36, 25))
            p.setPen(QPen(QColor("#f5a524"), 2, Qt.DashLine))
            p.drawRoundedRect(QRectF(r.adjusted(4, 4, -4, -4)), 8, 8)

        if self.message:
            p.setPen(QColor("#8a8d95"))
            f = QFont(self.font())
            f.setPointSize(15)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, self.message)
            return

        if self._paths_size != (r.width(), r.height()):
            self._build_paths()
        for stem in self.draw_order:
            color = QColor(STEM_COLORS.get(stem, "#cccccc"))
            if stem in self.active:
                color.setAlpha(205)
            else:
                color = QColor(58, 60, 68, 90)
            p.fillPath(self._paths[stem], color)

        x = self.position * r.width()  # playhead
        p.setPen(QPen(QColor("#ffffff"), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        p.setBrush(QColor("#ffffff"))
        p.drawEllipse(QPointF(x, 6), 4, 4)

    # interaction
    def mousePressEvent(self, e):
        if self.envelopes:
            self.seek.emit(min(max(e.position().x() / max(self.width(), 1), 0.0), 1.0))

    def mouseMoveEvent(self, e):
        if e.buttons() & Qt.LeftButton:
            self.mousePressEvent(e)

    @staticmethod
    def _audio_path(e) -> Path | None:
        for url in e.mimeData().urls():
            p = Path(url.toLocalFile())
            if p.suffix.lower() in AUDIO_EXT:
                return p
        return None

    def dragEnterEvent(self, e):
        if self._audio_path(e):
            self._hover = True
            self.update()
            e.acceptProposedAction()

    def dragLeaveEvent(self, _e):
        self._hover = False
        self.update()

    def dropEvent(self, e):
        self._hover = False
        self.update()
        p = self._audio_path(e)
        if p:
            self.dropped.emit(p)


# --------------------------------------------------------------------------- window


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"stemn {__version__}")
        self.resize(1120, 560)
        self.setMinimumSize(860, 420)
        self.player = Player()
        self.rows: list[StemStrip] = []
        self.source: Path | None = None
        self.stems: dict[str, Path] = {}
        self._thread: QThread | None = None
        self._last_pos = -1

        root = QWidget()
        self.setCentralWidget(root)
        outer = QVBoxLayout(root)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        outer.addWidget(self._build_topbar())

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        wave_box = QVBoxLayout()
        wave_box.setContentsMargins(10, 10, 10, 10)
        self.wave = WaveformView()
        self.wave.dropped.connect(self.start)
        self.wave.seek.connect(self.on_seek)
        wave_box.addWidget(self.wave)
        self.status = QLabel("")
        self.status.setObjectName("muted")
        wave_box.addWidget(self.status)
        body.addLayout(wave_box, 1)
        body.addWidget(self._build_panel())
        outer.addLayout(body, 1)

        self.timer = QTimer(self)
        self.timer.setInterval(33)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        self._set_loaded(False)

        if self.model_box.count() == 0:
            self.import_btn.setEnabled(False)
            self.wave.clear("Kein Modell gefunden.\nIst die Installation vollständig?")

    # ---- layout
    def _build_topbar(self) -> QFrame:
        bar = QFrame()
        bar.setObjectName("topbar")
        lay = QHBoxLayout(bar)
        lay.setContentsMargins(14, 8, 14, 8)
        lay.setSpacing(10)
        logo = QLabel("STEMN")
        logo.setObjectName("logo")
        lay.addWidget(logo)
        lay.addSpacing(16)

        self.time = QLabel("00:00.000 / 00:00.000")
        self.time.setObjectName("time")
        lay.addWidget(self.time)

        self.to_start_btn = QPushButton("⏮")
        self.to_start_btn.setObjectName("transport")
        self.to_start_btn.setToolTip("Zum Anfang")
        self.to_start_btn.clicked.connect(self._to_start)
        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("transport")
        self.play_btn.setCheckable(True)
        self.play_btn.setToolTip("Abspielen / Pause (Leertaste)")
        self.play_btn.setShortcut(Qt.Key_Space)
        self.play_btn.clicked.connect(self.toggle_play)
        lay.addWidget(self.to_start_btn)
        lay.addWidget(self.play_btn)
        lay.addSpacing(8)
        self.meter = Meter()
        lay.addWidget(self.meter)
        lay.addStretch(1)

        self.import_btn = QPushButton("Import")
        self.import_btn.setToolTip("Loop öffnen (oder einfach in die Wellenform ziehen)")
        self.import_btn.clicked.connect(self.choose_file)
        self.export_btn = QPushButton("Export")
        self.export_btn.setToolTip("Hörbaren Mix mit den eingestellten Pegeln als WAV speichern")
        self.export_btn.clicked.connect(self.export_dialog)
        self.folder_btn = QPushButton("Ordner")
        self.folder_btn.setToolTip("Ordner mit allen Spuren öffnen")
        self.folder_btn.clicked.connect(self.open_folder)
        self.mix_grip = DragGrip("⠿  Mix ziehen", self.export_mix, "Hörbaren Mix direkt in Ableton ziehen")
        for w in (self.import_btn, self.export_btn, self.folder_btn, self.mix_grip):
            lay.addWidget(w)
        return bar

    def _build_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(320)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(14, 12, 14, 12)
        lay.setSpacing(8)

        self.file_label = QLabel("Keine Datei")
        self.file_label.setObjectName("file")
        self.file_label.setWordWrap(True)
        lay.addWidget(self.file_label)

        lay.addWidget(self._caption("Modell"))
        self.model_box = QComboBox()
        for m in discover_models().values():
            if m.is_available():
                self.model_box.addItem(m.name, m.id)
        lay.addWidget(self.model_box)
        lay.addWidget(self._caption("Qualität"))
        self.quality_box = QComboBox()
        for label, value in QUALITIES:
            self.quality_box.addItem(label, value)
        lay.addWidget(self.quality_box)
        lay.addSpacing(6)
        lay.addWidget(self._caption("Spuren"))

        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(2)
        lay.addLayout(self.rows_box)
        lay.addStretch(1)
        return panel

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("muted")
        return lbl

    def _set_loaded(self, loaded: bool) -> None:
        for w in (self.play_btn, self.to_start_btn, self.export_btn, self.folder_btn, self.mix_grip):
            w.setEnabled(loaded)

    # ---- separation
    def choose_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Loop öffnen", "", "Audio (*.wav *.aif *.aiff *.flac)")
        if f:
            self.start(Path(f))

    def start(self, path: Path) -> None:
        if self._thread is not None:
            return
        self.player.stop()
        self.play_btn.setChecked(False)
        self.play_btn.setText("▶")
        self.source = path
        self.clear_rows()
        self.file_label.setText(path.name)
        self.wave.clear("Trenne Spuren …")
        self.status.setText("Das dauert je nach Rechner einige Sekunden.")
        self.import_btn.setEnabled(False)

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
        self.import_btn.setEnabled(True)

    def on_failed(self, msg: str) -> None:
        self.wave.clear("Fehler beim Trennen.\nAnderen Loop reinziehen.")
        self.status.setText(msg)
        QMessageBox.warning(self, "stemn", msg)

    def on_done(self, stems: dict) -> None:
        self.stems = {k: Path(v) for k, v in stems.items()}
        for stem in sorted(self.stems, key=lambda s: STEM_ORDER.index(s) if s in STEM_ORDER else 99):
            row = StemStrip(stem, self.stems[stem])
            row.changed.connect(self.update_gains)
            self.rows_box.addWidget(row)
            self.rows.append(row)
        self.player.load(self.stems)
        self.wave.set_stems(self.stems)
        self._set_loaded(True)
        folder = next(iter(self.stems.values())).parent
        self.status.setText(f"Fertig. Spuren liegen in „{folder.name}“. Neuen Loop reinziehen zum Ersetzen.")
        if self.source:
            self.file_label.setText(self.source.name)
        self.update_gains()

    def clear_rows(self) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.stems = {}
        self._set_loaded(False)

    # ---- mixing / playback
    def audible(self) -> list[str]:
        solo = [r.stem for r in self.rows if r.solo.isChecked()]
        if solo:
            return solo
        return [r.stem for r in self.rows if not r.mute.isChecked()]

    def current_gains(self) -> dict[str, float]:
        on = set(self.audible())
        return {r.stem: db_to_gain(r.gain_db) if r.stem in on else 0.0 for r in self.rows}

    def update_gains(self) -> None:
        gains = self.current_gains()
        self.player.set_gains(gains)
        self.wave.active = {s for s, g in gains.items() if g > 0}
        self.wave.update()

    def toggle_play(self) -> None:
        if self.player.playing:
            self.player.pause()
            self.play_btn.setChecked(False)
            self.play_btn.setText("▶")
            return
        self.update_gains()
        if self.player.play():
            self.play_btn.setChecked(True)
            self.play_btn.setText("❚❚")
        else:
            self.play_btn.setChecked(False)
            QMessageBox.warning(self, "stemn", f"Wiedergabe nicht möglich: {self.player.error}")

    def _to_start(self) -> None:
        self.on_seek(0.0)

    def on_seek(self, frac: float) -> None:
        self.player.seek(int(frac * self.player.length))
        self.tick()

    def tick(self) -> None:
        length = self.player.length
        self.meter.set_peak(self.player.peak if self.player.playing else 0.0)
        if not length:
            self.time.setText("00:00.000 / 00:00.000")
            return
        pos = self.player.pos
        if pos == self._last_pos:
            return
        self._last_pos = pos
        sr = self.player.sr
        self.time.setText(f"{fmt_time(pos / sr)} / {fmt_time(length / sr)}")
        self.wave.position = pos / length
        self.wave.update()

    # ---- export
    def render_mix(self) -> tuple[np.ndarray, int] | None:
        mix, sr = None, 44100
        for stem, g in self.current_gains().items():
            if g <= 0:
                continue
            audio, sr = sf.read(str(self.stems[stem]), dtype="float32", always_2d=True)
            audio = audio * g
            mix = audio if mix is None else mix + audio
        return None if mix is None else (mix, sr)

    def export_mix(self) -> Path | None:
        """File for dragging: the stem itself if only one is audible at 0 dB, else a rendered mix."""
        gains = {s: g for s, g in self.current_gains().items() if g > 0}
        if not gains:
            return None
        if len(gains) == 1:
            ((stem, g),) = gains.items()
            if abs(g - 1.0) < 1e-6:
                return self.stems[stem]
        mix, sr = self.render_mix()
        folder = next(iter(self.stems.values())).parent
        # never reuse a stem's own file name, or the stem would be overwritten
        levels = {r.stem: r.gain_db for r in self.rows if r.stem in gains}
        tag = "+".join(s if abs(levels[s]) < 0.05 else f"{s}{levels[s]:+.1f}dB" for s in gains)
        target = folder / f"{self.source.stem} - mix {tag}.wav"
        sf.write(str(target), mix, sr, subtype="PCM_24")
        return target

    def export_dialog(self) -> None:
        rendered = self.render_mix()
        if rendered is None:
            QMessageBox.information(self, "stemn", "Alle Spuren sind stumm.")
            return
        suggestion = next(iter(self.stems.values())).parent / f"{self.source.stem} - mix.wav"
        f, _ = QFileDialog.getSaveFileName(self, "Mix exportieren", str(suggestion), "WAV (*.wav)")
        if f:
            mix, sr = rendered
            sf.write(f, mix, sr, subtype="PCM_24")
            self.status.setText(f"Gespeichert: {f}")

    def open_folder(self) -> None:
        if self.stems:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(next(iter(self.stems.values())).parent)))

    def closeEvent(self, e):
        self.timer.stop()
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
