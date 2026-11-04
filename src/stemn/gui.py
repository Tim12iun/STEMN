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
# Kennfarben fuer Inhalte (gedaempft, nie Bedienfarben). Orange bleibt der einzige Akzent.
STEM_COLORS = {
    "kick": "#c4694c",
    "snare": "#c2a14e",
    "hihat": "#5f9fb0",
    "toms": "#8a7bb4",
    "ride": "#6fa983",
    "crash": "#ad7299",
    "cymbals": "#6fa983",
}
ACCENT = "#FF5A1F"
BG, SURFACE, RAISED, RAISED2, LINE = "#141414", "#1C1B1A", "#232221", "#2A2928", "#32312F"
TEXT, MUTED, DANGER = "#F0EEE9", "#8F8C86", "#B5483B"
FONT_FAMILY = "Barlow Condensed"
QUALITIES = [("Schnell", "fast"), ("Normal", "normal"), ("Beste", "best")]
GAIN_MIN_DB, GAIN_MAX_DB = -30.0, 6.0
ENVELOPE_BINS = 4096

STYLE = f"""
QWidget {{ background: {BG}; color: {TEXT}; font-family: "{FONT_FAMILY}"; font-size: 15px; font-weight: 500; }}
QToolTip {{ background: {RAISED2}; color: {TEXT}; border: 1px solid {LINE}; }}
QFrame#topbar {{ background: {BG}; }}
QFrame#panel {{ background: {SURFACE}; border-radius: 12px; }}
QFrame#panel QLabel, QFrame#topbar QLabel, QFrame#strip QLabel {{ background: transparent; }}
QFrame#strip {{ background: transparent; }}
QLabel#section {{ color: {MUTED}; font-size: 12px; font-weight: 600; letter-spacing: 1.5px; }}
QLabel#muted {{ color: {MUTED}; background: transparent; }}
QLabel#time {{ color: {TEXT}; }}
QLabel#file {{ color: {TEXT}; }}
QPushButton {{ background: {RAISED}; border: none; border-radius: 6px; padding: 6px 12px; color: {TEXT}; }}
QPushButton:hover {{ background: {RAISED2}; }}
QPushButton:pressed {{ background: #1A1918; }}
QPushButton:disabled {{ color: #5b5955; background: #1d1c1b; }}
QPushButton:focus {{ outline: none; }}
QPushButton#primary {{ background: {ACCENT}; color: #141414; font-weight: 700; letter-spacing: 1px; padding: 6px 16px; }}
QPushButton#primary:hover {{ background: #EC6636; }}
QPushButton#primary:disabled {{ background: #4a2a1e; color: #8a6a5e; }}
QPushButton#transport {{ min-width: 38px; max-width: 38px; padding: 6px 0; }}
QPushButton#transport:checked {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton#small {{ padding: 2px 0; min-width: 26px; max-width: 26px; font-weight: 700; }}
QPushButton#small:checked {{ border: 1px solid {ACCENT}; color: {ACCENT}; background: {RAISED}; }}
QPushButton#segment {{ border-radius: 0; padding: 5px 0; }}
QPushButton#segment:checked {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton#segleft {{ border-top-left-radius: 6px; border-bottom-left-radius: 6px; }}
QFrame QLabel#grip {{ background: {RAISED}; border-radius: 6px; padding: 4px 10px; color: {TEXT}; }}
QFrame QLabel#grip:hover {{ background: {RAISED2}; }}
QFrame QLabel#grip:disabled {{ color: #5b5955; background: #1d1c1b; }}
QFrame QLabel#db {{ color: {TEXT}; background: {RAISED}; border-radius: 6px; padding: 2px 4px; }}
QComboBox {{ background: {RAISED}; border: none; border-radius: 6px; padding: 5px 10px; }}
QComboBox QAbstractItemView {{ background: {RAISED2}; selection-background-color: {RAISED}; selection-color: {ACCENT}; }}
QSlider::groove:horizontal {{ height: 4px; background: {LINE}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {MUTED}; border-radius: 2px; }}
QSlider::handle:horizontal {{ width: 12px; height: 12px; margin: -4px 0; border-radius: 6px; background: {TEXT}; }}
QSlider::handle:horizontal:hover {{ background: {ACCENT}; }}
QMessageBox QPushButton {{ min-width: 90px; }}
"""


def tabular_font(size: int = 15) -> QFont:
    """Digits with fixed width so time and dB values never jump."""
    f = QFont(FONT_FAMILY, size, QFont.Medium)
    try:
        from PySide6.QtGui import QFont as _QF

        f.setFeature(_QF.Tag("tnum"), 1)
    except Exception:
        f = QFont("Menlo" if sys.platform == "darwin" else "Consolas", size - 3)
    return f


def load_fonts() -> None:
    from PySide6.QtGui import QFontDatabase

    folder = Path(__file__).parent / "fonts"
    for f in folder.glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))


def db_to_gain(db: float) -> float:
    return 0.0 if db <= GAIN_MIN_DB else float(10 ** (db / 20))


def fmt_time(seconds: float) -> str:
    m, s = divmod(max(seconds, 0.0), 60)
    return f"{int(m)}:{s:04.1f}"


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


class Logo(QWidget):
    """Tool name in lower case with the orange dot at the top right of the n."""

    def __init__(self, text: str = "stemn") -> None:
        super().__init__()
        self.text = text
        self.font_ = QFont(FONT_FAMILY, 22, QFont.Bold)
        from PySide6.QtGui import QFontMetrics

        fm = QFontMetrics(self.font_)
        self._w = fm.horizontalAdvance(text)
        self.setFixedSize(self._w + 12, fm.height())

    def paintEvent(self, _e):
        from PySide6.QtGui import QFontMetrics

        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setFont(self.font_)
        fm = QFontMetrics(self.font_)
        p.setPen(QColor(TEXT))
        p.drawText(QPointF(0, fm.ascent()), self.text)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        # free-standing, top right of the final n, roughly at ascender height
        p.drawEllipse(QPointF(self._w + 4.5, fm.ascent() - fm.capHeight() + 3.5), 3.2, 3.2)


class Meter(QWidget):
    """Horizontal output level meter (-60 … 0 dBFS)."""

    def __init__(self) -> None:
        super().__init__()
        self.level = 0.0
        self.setFixedSize(140, 6)
        self.setToolTip("Ausgangspegel")

    def set_peak(self, peak: float) -> None:
        db = 20 * np.log10(max(peak, 1e-6))
        level = min(max((db + 60) / 60, 0.0), 1.0)
        if abs(level - self.level) > 0.005:
            self.level = level
            self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        r = self.rect()
        p.setRenderHint(QPainter.Antialiasing)
        path = QPainterPath()
        path.addRoundedRect(QRectF(r), 3, 3)
        p.setClipPath(path)
        p.fillRect(r, QColor(RAISED))
        color = QColor(DANGER) if self.level > 0.98 else QColor(MUTED)
        p.fillRect(QRectF(0, 0, r.width() * self.level, r.height()), color)


class StemStrip(QFrame):
    """One stem in the side panel: colour, name, gain fader, dB, S, M, drag."""

    changed = Signal()

    def __init__(self, stem: str, path: Path) -> None:
        super().__init__()
        self.setObjectName("strip")
        self.stem, self.path = stem, path
        color = STEM_COLORS.get(stem, MUTED)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)

        dot = QLabel("●")
        dot.setStyleSheet(f"color:{color}; font-size:11px;")
        dot.setFixedWidth(12)
        name = QLabel(STEM_LABELS.get(stem, stem.title()))
        name.setStyleSheet("font-weight:600;")
        name.setFixedWidth(52)
        self.hint = name  # "fast leer" is shown via a dimmed name + tooltip
        self.fader = QSlider(Qt.Horizontal)
        self.fader.setRange(int(GAIN_MIN_DB * 10), int(GAIN_MAX_DB * 10))
        self.fader.setValue(0)
        self.fader.setToolTip("Lautstärke · Doppelklick auf den Wert setzt 0 dB")
        self.db = QLabel("+0.0 dB")
        self.db.setObjectName("db")
        self.db.setAlignment(Qt.AlignCenter)
        self.db.setFixedWidth(62)  # longest value "-30.0 dB" fits, nothing jumps
        self.db.setFont(tabular_font(14))
        self.solo = QPushButton("S")
        self.solo.setObjectName("small")
        self.solo.setCheckable(True)
        self.solo.setToolTip("Solo")
        self.mute = QPushButton("M")
        self.mute.setObjectName("small")
        self.mute.setCheckable(True)
        self.mute.setToolTip("Stumm")
        grip = DragGrip("⠿", self._drag_path, "Ziehen: diese Spur in Ableton ins Arrangement oder auf eine Spur ablegen")
        grip.setFixedWidth(28)
        grip.setAlignment(Qt.AlignCenter)
        for w in (dot, name):
            lay.addWidget(w)
        lay.addWidget(self.fader, 1)
        for w in (self.db, self.solo, self.mute, grip):
            lay.addWidget(w)

        self.fader.valueChanged.connect(self._on_fader)
        self.solo.toggled.connect(self._on_toggled)
        self.mute.toggled.connect(self._on_toggled)
        self.db.installEventFilter(self)

        try:
            audio, _ = sf.read(str(path), dtype="float32")
            peak = float(np.abs(audio).max()) if audio.size else 0.0
            if 20 * np.log10(max(peak, 1e-9)) < -35:
                self.hint.setStyleSheet(f"font-weight:600; color:{MUTED};")
                self.hint.setToolTip("Fast leer: in diesem Loop kommt das Instrument kaum vor")
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
        self.message = "Drum-Loop hierher ziehen\noder oben auf „Öffnen“ klicken"
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
        bg = QPainterPath()
        bg.addRoundedRect(QRectF(r), 12, 12)
        p.setClipPath(bg)
        p.fillRect(r, QColor(SURFACE))
        mid = r.height() / 2

        if self.duration > 0:  # time grid
            step = 1.0 if self.duration <= 20 else 5.0
            t = step
            while t < self.duration:
                x = r.width() * t / self.duration
                p.setPen(QPen(QColor(LINE), 1))
                p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
                p.setPen(QColor(MUTED))
                p.drawText(QPointF(x + 3, 12), f"{t:g}s")
                t += step
        p.setPen(QPen(QColor(LINE), 1))
        p.drawLine(QPointF(0, mid), QPointF(r.width(), mid))

        if self._hover:
            p.fillRect(r.adjusted(4, 4, -4, -4), QColor(255, 90, 31, 18))
            p.setPen(QPen(QColor(ACCENT), 2, Qt.DashLine))
            p.drawRoundedRect(QRectF(r.adjusted(4, 4, -4, -4)), 8, 8)

        if self.message:
            p.setPen(QColor(MUTED))
            f = QFont(FONT_FAMILY, 17, QFont.Medium)
            p.setFont(f)
            p.drawText(r, Qt.AlignCenter, self.message)
            return

        if self._paths_size != (r.width(), r.height()):
            self._build_paths()
        for stem in self.draw_order:
            color = QColor(STEM_COLORS.get(stem, MUTED))
            if stem in self.active:
                color.setAlpha(215)
            else:
                color = QColor(50, 49, 47, 160)
            p.fillPath(self._paths[stem], color)

        x = self.position * r.width()  # playhead
        p.setPen(QPen(QColor(ACCENT), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
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


class SegmentSwitch(QWidget):
    """2-4 options side by side; the active one is outlined in orange."""

    def __init__(self, options: list[tuple[str, str]], default: str) -> None:
        super().__init__()
        from PySide6.QtWidgets import QButtonGroup

        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self._values = {}
        for i, (label, value) in enumerate(options):
            b = QPushButton(label)
            b.setObjectName("segment")
            b.setCheckable(True)
            radius = "6px"
            corners = []
            if i == 0:
                corners += [f"border-top-left-radius:{radius}", f"border-bottom-left-radius:{radius}"]
            if i == len(options) - 1:
                corners += [f"border-top-right-radius:{radius}", f"border-bottom-right-radius:{radius}"]
            if corners:
                b.setStyleSheet("QPushButton#segment {" + ";".join(corners) + "}")
            self.group.addButton(b, i)
            self._values[i] = value
            lay.addWidget(b, 1)
            if value == default:
                b.setChecked(True)

    def currentData(self) -> str:
        return self._values.get(self.group.checkedId(), next(iter(self._values.values())))

    def setCurrentData(self, value: str) -> None:
        for i, v in self._values.items():
            if v == value:
                self.group.button(i).setChecked(True)


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
        body.setContentsMargins(12, 6, 12, 12)
        body.setSpacing(12)
        wave_box = QVBoxLayout()
        wave_box.setContentsMargins(0, 0, 0, 0)
        wave_box.setSpacing(8)
        self.wave = WaveformView()
        self.wave.dropped.connect(self.start)
        self.wave.seek.connect(self.on_seek)
        wave_box.addWidget(self.wave)
        self.status = QLabel("Drum-Loop hierher ziehen, am besten eine reine Drum-Spur")
        self.status.setObjectName("muted")
        self.status.setWordWrap(True)
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
        lay.setContentsMargins(16, 10, 16, 6)
        lay.setSpacing(8)
        lay.addWidget(Logo("stemn"))
        lay.addSpacing(24)

        self.time = QLabel("0:00.0 / 0:00.0")
        self.time.setObjectName("time")
        self.time.setFont(tabular_font())
        self.time.setFixedWidth(120)
        lay.addWidget(self.time)

        self.to_start_btn = QPushButton("⏮")
        self.to_start_btn.setObjectName("transport")
        self.to_start_btn.setToolTip("Zum Anfang")
        self.to_start_btn.clicked.connect(self._to_start)
        self.play_btn = QPushButton("▶")
        self.play_btn.setObjectName("transport")
        self.play_btn.setCheckable(True)
        self.play_btn.setToolTip("Abspielen / Pause · Leertaste")
        self.play_btn.setShortcut(Qt.Key_Space)
        self.play_btn.clicked.connect(self.toggle_play)
        lay.addWidget(self.to_start_btn)
        lay.addWidget(self.play_btn)
        lay.addSpacing(12)
        self.meter = Meter()
        lay.addWidget(self.meter)
        lay.addStretch(1)

        self.import_btn = QPushButton("Öffnen")
        self.import_btn.setToolTip("Loop öffnen · ⌘O (oder in die Wellenform ziehen)")
        self.import_btn.setShortcut("Ctrl+O")
        self.import_btn.clicked.connect(self.choose_file)
        self.folder_btn = QPushButton("Ordner")
        self.folder_btn.setToolTip("Ordner mit allen Spuren öffnen")
        self.folder_btn.clicked.connect(self.open_folder)
        self.mix_grip = DragGrip("⠿ Mix ziehen", self.export_mix, "Alle hörbaren Spuren als eine Datei in Ableton ziehen")
        self.export_btn = QPushButton("EXPORTIEREN")
        self.export_btn.setObjectName("primary")
        self.export_btn.setToolTip("Hörbaren Mix mit den eingestellten Pegeln als WAV speichern · ⌘E")
        self.export_btn.setShortcut("Ctrl+E")
        self.export_btn.clicked.connect(self.export_dialog)
        for w in (self.import_btn, self.folder_btn, self.mix_grip, self.export_btn):
            lay.addWidget(w)
        return bar

    def _build_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(330)
        lay = QVBoxLayout(panel)
        lay.setContentsMargins(16, 16, 16, 12)
        lay.setSpacing(8)

        lay.addWidget(self._caption("DATEI"))
        self.file_label = QLabel("Noch keine")
        self.file_label.setObjectName("file")
        self.file_label.setWordWrap(True)
        lay.addWidget(self.file_label)
        lay.addSpacing(8)

        lay.addWidget(self._caption("MODELL"))
        self.model_box = QComboBox()
        for m in discover_models().values():
            if m.is_available():
                self.model_box.addItem(m.name, m.id)
        lay.addWidget(self.model_box)
        lay.addSpacing(8)

        lay.addWidget(self._caption("QUALITÄT"))
        self.quality_box = SegmentSwitch(QUALITIES, default="normal")
        lay.addWidget(self.quality_box)
        lay.addSpacing(8)

        lay.addWidget(self._caption("SPUREN"))
        self.rows_box = QVBoxLayout()
        self.rows_box.setSpacing(4)
        lay.addLayout(self.rows_box)
        self.empty_rows = QLabel("Noch keine")
        self.empty_rows.setObjectName("muted")
        self.rows_box.addWidget(self.empty_rows)
        lay.addStretch(1)
        version = QLabel(f"VERSION {__version__}")
        version.setObjectName("section")
        lay.addWidget(version)
        return panel

    @staticmethod
    def _caption(text: str) -> QLabel:
        lbl = QLabel(text)
        lbl.setObjectName("section")
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
        self.status.setText("Das dauert je nach Rechner ein paar Sekunden.")
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
        self.empty_rows.hide()
        for stem in sorted(self.stems, key=lambda s: STEM_ORDER.index(s) if s in STEM_ORDER else 99):
            row = StemStrip(stem, self.stems[stem])
            row.changed.connect(self.update_gains)
            self.rows_box.addWidget(row)
            self.rows.append(row)
        self.player.load(self.stems)
        self.wave.set_stems(self.stems)
        self._set_loaded(True)
        folder = next(iter(self.stems.values())).parent
        self.status.setText("Spur am ⠿ nach Ableton ziehen · Klick in die Wellenform setzt die Position")
        if self.source:
            self.file_label.setText(self.source.name)
        self.update_gains()

    def clear_rows(self) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.stems = {}
        self.empty_rows.show()
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
            self.time.setText("0:00.0 / 0:00.0")
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
    load_fonts()
    app.setStyleSheet(STYLE)
    win = MainWindow()
    win.show()
    threading.Thread(target=_background_update, daemon=True).start()
    if argv and len(argv) > 1 and Path(argv[1]).is_file():
        QTimer.singleShot(0, lambda: win.start(Path(argv[1])))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
