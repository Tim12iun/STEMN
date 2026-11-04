"""stemn desktop app: drop a loop in, listen to the stems, drag them into Ableton."""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import numpy as np
import soundfile as sf
from PySide6.QtCore import QMimeData, QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QDesktopServices, QDrag, QFont, QLinearGradient, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .models import discover_models
from .restore import discover_restorers

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
QPushButton#primary[lit="false"] {{ background: {RAISED2}; color: {TEXT}; }}
QPushButton#primary[lit="false"]:hover {{ background: {LINE}; }}
QPushButton#primary[lit="false"]:disabled {{ background: {RAISED}; color: {MUTED}; }}
QPushButton#icon {{ font-size: 17px; padding: 3px 0; }}
QPushButton#transport {{ min-width: 38px; max-width: 38px; padding: 6px 0; }}
QPushButton#transport:checked {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
QPushButton#small {{ padding: 2px 0; min-width: 26px; max-width: 26px; font-weight: 700; }}
QPushButton#small:checked {{ border: 1px solid {ACCENT}; color: {ACCENT}; background: {RAISED}; }}
QPushButton#toggle {{ text-align: left; padding: 5px 10px; border: 1px solid transparent; }}
QPushButton#toggle:checked {{ border: 1px solid {ACCENT}; color: {ACCENT}; }}
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
    """Loops the stems in sync; gain/mute/solo changes take effect immediately.

    All tracks stay in memory. Whenever a gain changes the mix is computed once
    in the window thread; the audio callback only copies from that buffer, so
    playback stays smooth even while the window redraws or a separation runs.
    """

    BLOCK = 512
    LATENCY = 0.06  # seconds; small enough to feel instant, large enough not to crackle

    def __init__(self) -> None:
        self.tracks: dict[str, np.ndarray] = {}
        self.gains: dict[str, float] = {}
        self.mix = np.zeros((0, 2), np.float32)
        self.sr = 44100
        self.pos = 0
        self.length = 0
        self.peak = 0.0
        self.stream = None
        self.error: str | None = None
        self._running = False  # a test harness may call _callback without a device
        self._stream_sr = None
        self._since_seek = 0  # frames played since the last jump
        self._lock = threading.Lock()

    def load(self, files: dict[str, Path]) -> None:
        self.stop()
        tracks = {}
        for stem, path in files.items():
            audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
            if audio.shape[1] == 1:
                audio = np.repeat(audio, 2, axis=1)
            tracks[stem] = np.ascontiguousarray(audio[:, :2])
            self.sr = sr
        length = min((len(a) for a in tracks.values()), default=0)
        self.tracks = {k: a[:length] for k, a in tracks.items()}
        self.gains = {k: 1.0 for k in tracks}
        mix = self._render()
        with self._lock:
            self.mix, self.length, self.pos = mix, length, 0

    def _render(self) -> np.ndarray:
        mix = None
        for stem, audio in self.tracks.items():
            g = self.gains.get(stem, 0.0)
            if g:
                mix = audio * g if mix is None else mix + audio * g
        if mix is None:
            length = min((len(a) for a in self.tracks.values()), default=0)
            mix = np.zeros((length, 2), np.float32)
        return mix

    def set_gains(self, gains: dict[str, float]) -> None:
        if all(self.gains.get(k) == v for k, v in gains.items()):
            return
        self.gains.update(gains)
        mix = self._render()
        with self._lock:
            self.mix = mix

    def seek(self, frame: int) -> None:
        with self._lock:
            self.pos = int(min(max(frame, 0), max(self.length - 1, 0)))
            self._since_seek = 0

    def _callback(self, out, frames, _time, _status):
        if not self._running and self.stream is not None:
            out.fill(0)
            return
        with self._lock:
            mix, length, pos = self.mix, self.length, self.pos
            if not length:
                out.fill(0)
                return
            self._since_seek += frames
            written = 0
            while written < frames:
                n = min(frames - written, length - pos)
                out[written : written + n] = mix[pos : pos + n]
                written += n
                pos = (pos + n) % length
            self.pos = pos
        self.peak = max(self.peak * 0.85, float(np.abs(out).max()) if frames else 0.0)

    @property
    def heard_pos(self) -> int:
        """Position that is audible now (the device buffer runs slightly ahead)."""
        pos = self.pos
        if self._running and self.stream is not None and self.length:
            try:
                ahead = int(float(self.stream.latency) * self.sr)
            except Exception:
                ahead = 0
            pos -= min(ahead, self._since_seek)  # right after a jump the new spot is what you hear
            pos %= self.length
        return pos

    @property
    def playing(self) -> bool:
        return self._running

    def _ensure_stream(self) -> None:
        """The audio device stays open, so Space starts and stops without delay."""
        if self.stream is not None and self._stream_sr == self.sr:
            return
        self.close()
        import sounddevice as sd

        self.stream = sd.OutputStream(
            samplerate=self.sr,
            channels=2,
            dtype="float32",
            blocksize=self.BLOCK,
            latency=self.LATENCY,
            callback=self._callback,
        )
        self.stream.start()
        self._stream_sr = self.sr

    def play(self) -> bool:
        if self._running or not self.length:
            return self._running
        try:
            self._ensure_stream()
        except Exception as exc:  # no audio device / PortAudio missing
            self.error = str(exc)
            self.stream = None
            return False
        self._running = True
        return True

    def pause(self) -> None:
        """Stop where it is audible right now; the next play continues exactly there."""
        if self._running:
            pos = self.heard_pos
            self._running = False
            with self._lock:
                self.pos = pos
        self.peak = 0.0

    def stop(self) -> None:
        self.pause()
        self.pos = 0

    def close(self) -> None:
        self._running = False
        if self.stream is not None:
            try:
                self.stream.stop()
                self.stream.close()
            except Exception:
                pass
            finally:
                self.stream = None


# --------------------------------------------------------------------------- worker


QUALITY = "best"  # the app always separates in the best quality


def separate_command(
    path: Path, model_id: str | None, result_file: Path, progress_file: Path, restore: bool = False, bleed: str = "off"
) -> list[str]:
    """Command line that runs one separation in its own process.

    A crash deep inside torch (native abort) then only ends that process,
    and the window can report it instead of closing.
    """
    if getattr(sys, "frozen", False):
        cmd = [sys.executable, "separate"]
    else:
        cmd = [sys.executable, "-m", "stemn.cli", "separate"]
    cmd += [str(path), "-q", QUALITY, "--result-file", str(result_file), "--progress-file", str(progress_file)]
    if model_id:
        cmd += ["-m", model_id]
    if bleed != "off":
        cmd += ["--bleed", bleed]
    if restore:
        cmd.append("--restore")
    return cmd


def _read_progress(path: Path) -> float | None:
    try:
        return min(max(float(path.read_text(encoding="utf-8")), 0.0), 1.0)
    except (OSError, ValueError):
        return None


def run_separation(path: Path, model_id: str | None, progress=None, restore: bool = False, bleed: str = "off") -> dict:
    """Run the separation in a child process. Returns {stem: path} or raises RuntimeError.

    ``progress`` (optional) is called with values from 0 to 1 while it runs.
    """
    import json
    import subprocess
    import tempfile
    import time

    with tempfile.TemporaryDirectory(prefix="stemn-") as tmp:
        result_file = Path(tmp) / "result.json"
        progress_file = Path(tmp) / "progress.txt"
        stderr_file = Path(tmp) / "stderr.txt"
        kwargs = {}
        if sys.platform == "win32":
            kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        with open(stderr_file, "wb") as err:
            proc = subprocess.Popen(
                separate_command(path, model_id, result_file, progress_file, restore, bleed),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=err,
                **kwargs,
            )
            last = None
            while proc.poll() is None:
                value = _read_progress(progress_file)
                if progress and value is not None and value != last:
                    progress(value)
                    last = value
                time.sleep(0.15)
        data = {}
        if result_file.is_file():
            try:
                data = json.loads(result_file.read_text(encoding="utf-8"))
            except ValueError:
                data = {}
        stderr = stderr_file.read_bytes() if stderr_file.is_file() else b""
    if "error" in data:
        raise RuntimeError(data["error"])
    if proc.returncode == 0 and "stems" in data:
        return next(iter(data["stems"].values()), {})
    tail = stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
    detail = ("\n" + "\n".join(tail)) if tail else ""
    raise RuntimeError(f"Die Trennung ist abgestuerzt (Code {proc.returncode}).{detail}")


class SeparateWorker(QObject):
    finished = Signal(dict)
    failed = Signal(str)
    progress = Signal(float)

    def __init__(self, path: Path, model_id: str | None, restore: bool = False, bleed: str = "off") -> None:
        super().__init__()
        self.path, self.model_id, self.restore, self.bleed = path, model_id, restore, bleed

    def run(self) -> None:
        try:
            self.finished.emit(run_separation(self.path, self.model_id, self.progress.emit, self.restore, self.bleed))
        except Exception as exc:
            self.failed.emit(str(exc))


# --------------------------------------------------------------------------- small widgets


def start_file_drag(widget: QWidget, path) -> None:
    paths = path if isinstance(path, (list, tuple)) else [path]
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(p)) for p in paths])
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


class StemGrip(DragGrip):
    """Clearly visible handle on every stem row: a small field with a dot grid.

    Grab it and drop it into Ableton (or Finder/Explorer): only this one stem moves.
    """

    def __init__(self, color: str, path_provider, tooltip: str) -> None:
        super().__init__("", path_provider, tooltip)
        self.setObjectName("stemgrip")
        self.color = QColor(color)
        self._hover = False
        self.setFixedSize(34, 28)

    def enterEvent(self, e):
        self._hover = True
        self.update()

    def leaveEvent(self, e):
        self._hover = False
        self.update()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        p.setPen(QPen(QColor(TEXT if self._hover else LINE), 1))
        p.setBrush(QColor(RAISED2 if self._hover else RAISED))
        p.drawRoundedRect(r, 6, 6)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(TEXT) if self._hover else self.color)
        cx, cy = r.center().x(), r.center().y()
        for dx in (-3.5, 3.5):
            for dy in (-6, 0, 6):
                p.drawEllipse(QPointF(cx + dx, cy + dy), 1.7, 1.7)


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


class StemName(QWidget):
    """Colour field and name of a stem; a click switches the stem on or off.

    on: colour field filled, name bright white
    off: colour field empty, name grey and struck through
    nearly empty stem: name greyed out (with tooltip)
    """

    toggled = Signal(bool)
    clicked = Signal()

    def __init__(self, stem: str, color: str) -> None:
        super().__init__()
        self.text = STEM_LABELS.get(stem, stem.title())
        self.color = QColor(color)
        self._checked = True
        self.faint = False  # nearly empty
        self.setFixedSize(84, 24)
        self.setCursor(Qt.PointingHandCursor)
        self.setToolTip("Klick: Spur an/aus")

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, on: bool) -> None:
        if on != self._checked:
            self._checked = on
            self.update()
            self.toggled.emit(on)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton:
            self.clicked.emit()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        box = QRectF(1, (self.height() - 14) / 2, 14, 14)
        if self._checked:
            p.setPen(Qt.NoPen)
            p.setBrush(self.color)
        else:
            dim = QColor(self.color)
            dim.setAlpha(110)
            p.setPen(QPen(dim, 1.5))
            p.setBrush(Qt.NoBrush)
        p.drawRoundedRect(box, 3, 3)
        font = QFont(FONT_FAMILY, 15, QFont.DemiBold)
        font.setStrikeOut(not self._checked)
        p.setFont(font)
        if not self._checked:
            p.setPen(QColor(MUTED))
        elif self.faint:
            p.setPen(QColor("#B4B0A9"))
        else:
            p.setPen(QColor(TEXT))
        p.drawText(QRectF(box.right() + 9, 0, self.width() - box.right() - 9, self.height()), Qt.AlignVCenter | Qt.AlignLeft, self.text)


class StemStrip(QFrame):
    """One stem in the side panel: colour + name (on/off), gain fader, dB, S, drag handle."""

    changed = Signal()
    name_clicked = Signal(str)
    solo_clicked = Signal(str)

    def __init__(self, stem: str, path: Path) -> None:
        super().__init__()
        self.setObjectName("strip")
        self.stem, self.path = stem, path
        color = STEM_COLORS.get(stem, MUTED)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 2, 0, 2)
        lay.setSpacing(6)

        self.check = StemName(stem, color)
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
        self.solo.setToolTip("Solo: nur diese Spur · noch mal drücken: vorheriger Zustand")
        grip = StemGrip(color, self._drag_path, "Greifen und ziehen: nur diese Spur in Ableton ablegen (Arrangement oder Spur)")
        self.grip = grip
        lay.addWidget(self.check)
        lay.addWidget(self.fader, 1)
        for w in (self.db, self.solo, grip):
            lay.addWidget(w)

        self.fader.valueChanged.connect(self._on_fader)
        self.check.toggled.connect(self._on_toggled)
        self.check.clicked.connect(lambda: self.name_clicked.emit(self.stem))
        self.solo.clicked.connect(lambda: self.solo_clicked.emit(self.stem))
        self.db.installEventFilter(self)

        try:
            audio, _ = sf.read(str(path), dtype="float32")
            peak = float(np.abs(audio).max()) if audio.size else 0.0
            if 20 * np.log10(max(peak, 1e-9)) < -35:
                self.check.faint = True
                self.check.setToolTip("Klick: Spur an/aus · fast leer: in diesem Loop kommt das Instrument kaum vor")
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
        self.progress: float | None = None  # set while separating
        self.busy_text = ""
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
        self.progress = None
        self._paths_size = None
        self.update()

    def set_source(self, path: Path) -> None:
        """Show the dropped loop (grey) right away, with the progress bar on top."""
        try:
            self.set_stems({"_source": path})
        except Exception:
            self.clear("")
        self.position = 0.0
        self.set_progress(0.0, "Modell wird geladen")

    def set_progress(self, value: float, text: str) -> None:
        self.progress = min(max(value, 0.0), 1.0)
        self.busy_text = text
        self.update()

    def clear(self, message: str) -> None:
        self.progress = None
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
    def _static_key(self):
        return (self.width(), self.height(), self.devicePixelRatioF(), tuple(self.draw_order), frozenset(self.active), self.duration)

    def _render_static(self):
        """Background, grid and all waveforms, drawn once into a pixmap.

        While playing only this picture and the playhead are drawn, which keeps
        the window light so playback does not stutter.
        """
        from PySide6.QtGui import QPixmap

        dpr = self.devicePixelRatioF()
        pix = QPixmap(int(self.width() * dpr), int(self.height() * dpr))
        pix.setDevicePixelRatio(dpr)
        pix.fill(Qt.transparent)
        p = QPainter(pix)
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
        if self.envelopes:
            if self._paths_size != (r.width(), r.height()):
                self._build_paths()
            for stem in self.draw_order:
                color = QColor(STEM_COLORS.get(stem, MUTED))
                if stem in self.active:
                    color.setAlpha(215)
                else:
                    color = QColor(50, 49, 47, 160)
                p.fillPath(self._paths[stem], color)
        p.end()
        self._static = pix
        self._static_for = self._static_key()

    def paintEvent(self, _e):
        if getattr(self, "_static_for", None) != self._static_key():
            self._render_static()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        r = self.rect()
        p.drawPixmap(0, 0, self._static)

        if self._hover:
            p.fillRect(r.adjusted(4, 4, -4, -4), QColor(255, 90, 31, 18))
            p.setPen(QPen(QColor(ACCENT), 2, Qt.DashLine))
            p.drawRoundedRect(QRectF(r.adjusted(4, 4, -4, -4)), 8, 8)

        if self.message:
            p.setPen(QColor(MUTED))
            p.setFont(QFont(FONT_FAMILY, 17, QFont.Medium))
            p.drawText(r, Qt.AlignCenter, self.message)
        if self.progress is not None:
            self._paint_progress(p, r)
            return
        if not self.envelopes:
            return

        x = self.position * r.width()  # playhead
        p.setPen(QPen(QColor(ACCENT), 1.5))
        p.drawLine(QPointF(x, 0), QPointF(x, r.height()))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(ACCENT))
        p.drawEllipse(QPointF(x, 6), 4, 4)

    def _paint_progress(self, p: QPainter, r) -> None:
        dim = QPainterPath()
        dim.addRoundedRect(QRectF(r), 12, 12)
        p.fillPath(dim, QColor(20, 20, 20, 150))  # dim the loop underneath
        w = min(380.0, r.width() - 80.0)
        box = QRectF(r.center().x() - w / 2 - 20, r.center().y() - 38, w + 40, 76)
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(RAISED))
        p.drawRoundedRect(box, 10, 10)
        p.setPen(QColor(TEXT))
        p.setFont(QFont(FONT_FAMILY, 15, QFont.DemiBold))
        p.drawText(QRectF(box.left() + 20, box.top() + 12, w * 0.7, 24), Qt.AlignLeft | Qt.AlignVCenter, self.busy_text)
        p.setFont(tabular_font(15))
        p.drawText(QRectF(box.left() + 20, box.top() + 12, w, 24), Qt.AlignRight | Qt.AlignVCenter, f"{round(self.progress * 100)} %")
        bar = QRectF(box.left() + 20, box.top() + 48, w, 6)
        p.setBrush(QColor(LINE))
        p.drawRoundedRect(bar, 3, 3)
        if self.progress > 0:
            p.setBrush(QColor(ACCENT))
            p.drawRoundedRect(QRectF(bar.left(), bar.top(), max(bar.width() * self.progress, 6), bar.height()), 3, 3)

    # interaction
    def mousePressEvent(self, e):
        if self.envelopes and self.progress is None:
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
        self.update_state: dict = {"phase": "idle"}
        self._update_thread: QThread | None = None
        self._solo: str | None = None
        self._before_solo: dict[str, bool] | None = None
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
        self._no_focus()

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
        from PySide6.QtGui import QKeySequence, QShortcut

        space = QShortcut(QKeySequence(Qt.Key_Space), self)
        space.setContext(Qt.WindowShortcut)
        space.activated.connect(self.toggle_play)
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
        self.mix_grip = DragGrip("⠿ Mix", self.export_mix, "Ziehen: alle eingeschalteten Spuren zusammen als eine Datei")
        self.single_grip = DragGrip(
            "⠿ Einzeln",
            self.export_singles,
            "Ziehen: alle eingeschalteten Spuren als einzelne Dateien.\n"
            "In Ableton beim Loslassen ⌘ (Mac) bzw. Strg (Windows) halten: jede Spur landet auf einer eigenen Spur.",
        )
        self.separate_btn = QPushButton("TRENNEN")
        self.separate_btn.setObjectName("primary")
        self.separate_btn.setToolTip("Loop in Kick, Snare, Hi-Hat, Toms, Ride und Crash zerlegen · ⌘T")
        self.separate_btn.setShortcut("Ctrl+T")
        self.separate_btn.clicked.connect(self.separate_current)
        self.export_btn = QPushButton(" EXPORTIEREN")
        self.export_btn.setObjectName("primary")
        self.export_btn.setIconSize(QSize(14, 14))
        self.export_btn.setToolTip("Hörbaren Mix mit den eingestellten Pegeln als WAV speichern · ⌘E")
        self.export_btn.setShortcut("Ctrl+E")
        self.export_btn.clicked.connect(self.export_dialog)
        self.gear_btn = GearButton()
        self.gear_btn.clicked.connect(self.open_settings)
        for w in (self.import_btn, self.folder_btn, self.mix_grip, self.single_grip, self.separate_btn, self.export_btn):
            lay.addWidget(w)
        lay.addSpacing(6)
        lay.addWidget(self.gear_btn)
        # both always keep their size; only one of them is orange at a time
        self.separate_btn.setFixedWidth(self.separate_btn.sizeHint().width())
        self.export_btn.setFixedWidth(self.export_btn.sizeHint().width() + 20)
        self._separated_with = None  # settings of the stems on screen
        return bar

    def _separation_key(self):
        restore = self.restore_btn.isChecked() and not self.restore_btn.isHidden()
        return (self.source, self.model_box.currentData(), restore, self.bleed_switch.currentData())

    def _update_main_buttons(self) -> None:
        """TRENNEN glows when (re)separating is needed, otherwise EXPORTIEREN glows."""
        if not hasattr(self, "bleed_switch"):
            return  # panel not built yet
        loaded = bool(self.stems)
        needs = self.source is not None and (not loaded or self._separation_key() != self._separated_with)
        for btn, lit in ((self.separate_btn, needs), (self.export_btn, loaded and not needs)):
            if btn.property("lit") != lit:
                btn.setProperty("lit", lit)
                btn.style().unpolish(btn)
                btn.style().polish(btn)
        color = "#141414" if (loaded and not needs) else (TEXT if loaded else MUTED)
        self.export_btn.setIcon(download_icon(color))

    def _build_panel(self) -> QFrame:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(360)
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

        # damp cross-talk: every stem keeps less of the other drums
        lay.addWidget(self._caption("ÜBERSPRECHEN"))
        self.bleed_switch = SegmentSwitch([("Aus", "off"), ("Leicht", "light"), ("Stark", "strong")], "off")
        self.bleed_switch.setToolTip("Dämpft in jeder Spur, was hörbar zu einer anderen Spur gehört. Stark = trockener, leise Ausklänge können dünner werden.")
        lay.addWidget(self.bleed_switch)
        lay.addSpacing(8)
        self.bleed_switch.group.buttonClicked.connect(lambda _b: self._update_main_buttons())
        self.model_box.currentIndexChanged.connect(lambda _i: self._update_main_buttons())

        # optional: regenerate missing highs/details per stem after separating
        self.restore_btn = QPushButton("Apollo · Fehlendes ergänzen")
        self.restore_btn.setObjectName("toggle")
        self.restore_btn.setCheckable(True)
        self.restore_btn.setToolTip("Repariert jede Spur nach dem Trennen: ergänzt fehlende Höhen und Details. Dauert länger.")
        self.restore_btn.toggled.connect(lambda _on: self._update_main_buttons())
        has_restorer = any(r.is_available() for r in discover_restorers().values())
        restore_caption = self._caption("REPARIEREN")
        for w in (restore_caption, self.restore_btn):
            w.setVisible(has_restorer)
            lay.addWidget(w)
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

    def _no_focus(self) -> None:
        """No button, fader or list takes the keyboard: Space always means play/stop."""
        for w in self.findChildren(QWidget):
            w.setFocusPolicy(Qt.NoFocus)

    def _set_loaded(self, loaded: bool) -> None:
        """loaded = stems exist. Before that only the loop itself can be played."""
        has_audio = loaded or self.player.length > 0
        for w in (self.play_btn, self.to_start_btn):
            w.setEnabled(has_audio)
        for w in (self.export_btn, self.folder_btn, self.mix_grip, self.single_grip):
            w.setEnabled(loaded)
        self.separate_btn.setEnabled(self.source is not None and self._thread is None)
        self._update_main_buttons()

    # ---- separation
    def choose_file(self) -> None:
        f, _ = QFileDialog.getOpenFileName(self, "Loop öffnen", "", "Audio (*.wav *.aif *.aiff *.flac)")
        if f:
            self.start(Path(f))

    def start(self, path: Path) -> None:
        """Load a dropped/opened loop: show it and make it playable. Separation comes with TRENNEN."""
        if self._thread is not None:
            return
        self._stop_playback()
        self.source = path
        self.clear_rows()
        self.file_label.setText(path.name)
        try:
            self.player.load({"_source": path})
            self.wave.set_stems({"_source": path})
        except Exception as exc:
            self.source = None
            self.player.load({})
            self.wave.clear("Datei lässt sich nicht lesen.\nAnderen Loop reinziehen.")
            self.status.setText(str(exc))
            self._set_loaded(False)
            return
        self.wave.position = 0.0
        self._set_loaded(False)
        self.status.setText("Leertaste spielt den Loop · TRENNEN zerlegt ihn in Spuren (beste Qualität)")

    def separate_current(self) -> None:
        if self._thread is not None or self.source is None:
            return
        self.wave.set_progress(0.0, "Modell wird geladen")
        self.status.setText("Trennt in bester Qualität · der Loop lässt sich währenddessen weiter abspielen")
        self.import_btn.setEnabled(False)
        self.separate_btn.setEnabled(False)

        self._thread = QThread(self)
        self._restoring = self.restore_btn.isChecked() and not self.restore_btn.isHidden()
        self._pending_key = self._separation_key()
        self._worker = SeparateWorker(self.source, self.model_box.currentData(), self._restoring, self.bleed_switch.currentData())
        self._worker.progress.connect(self.on_progress)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self.on_done)
        self._worker.failed.connect(self.on_failed)
        self._worker.finished.connect(self._thread.quit)
        self._worker.failed.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup_thread)
        self._thread.start()

    def _stop_playback(self) -> None:
        self.player.stop()
        self.play_btn.setChecked(False)
        self.play_btn.setText("▶")

    def on_progress(self, value: float) -> None:
        restoring = getattr(self, "_restoring", False) and value >= 0.55
        text = "Modell wird geladen" if value < 0.08 else "Repariere Spuren" if restoring and value < 0.95 else "Trenne Spuren" if value < 0.95 else "Spuren werden gespeichert"
        self.wave.set_progress(value, text)

    def _cleanup_thread(self) -> None:
        self._thread = None
        self.import_btn.setEnabled(True)
        self.separate_btn.setEnabled(self.source is not None)

    def on_failed(self, msg: str) -> None:
        self.wave.progress = None
        self.wave.update()
        self.status.setText(f"Fehler beim Trennen: {msg}")
        QMessageBox.warning(self, "stemn", msg)

    def on_done(self, stems: dict) -> None:
        self.stems = {k: Path(v) for k, v in stems.items()}
        self.empty_rows.hide()
        for stem in sorted(self.stems, key=lambda s: STEM_ORDER.index(s) if s in STEM_ORDER else 99):
            row = StemStrip(stem, self.stems[stem])
            row.changed.connect(self.update_gains)
            row.name_clicked.connect(self.toggle_stem)
            row.solo_clicked.connect(self.toggle_solo)
            self.rows_box.addWidget(row)
            self.rows.append(row)
        was_playing = self.player.playing
        pos = self.player.pos
        self._stop_playback()
        self.player.load(self.stems)
        self.player.seek(pos)
        self.wave.set_stems(self.stems)
        self._separated_with = getattr(self, "_pending_key", None) or self._separation_key()
        self._set_loaded(True)
        folder = next(iter(self.stems.values())).parent
        self.status.setText("Klick auf den Namen: Spur an/aus · S: nur diese Spur · Griff rechts: Spur nach Ableton ziehen")
        if self.source:
            self.file_label.setText(self.source.name)
        self.update_gains()
        self._no_focus()
        if was_playing:
            self.toggle_play()

    def clear_rows(self) -> None:
        for row in self.rows:
            row.setParent(None)
            row.deleteLater()
        self.rows = []
        self.stems = {}
        self._solo, self._before_solo = None, None
        self.empty_rows.show()
        self._set_loaded(False)

    # ---- mixing / playback
    def audible(self) -> list[str]:
        return [r.stem for r in self.rows if r.check.isChecked()]

    def _set_on(self, states: dict[str, bool]) -> None:
        for r in self.rows:
            r.check.blockSignals(True)
            r.check.setChecked(states.get(r.stem, True))
            r.check.blockSignals(False)
        self.update_gains()

    def _set_solo_light(self, stem: str | None) -> None:
        self._solo = stem
        for r in self.rows:
            r.solo.blockSignals(True)
            r.solo.setChecked(r.stem == stem)
            r.solo.blockSignals(False)

    def toggle_solo(self, stem: str) -> None:
        """S: only this stem on, S lights up. S again: back to how it was before."""
        if self._solo == stem:
            before = self._before_solo or {}
            self._set_solo_light(None)
            self._set_on(before)
            return
        if self._solo is None:
            self._before_solo = {r.stem: r.check.isChecked() for r in self.rows}
        self._set_solo_light(stem)
        self._set_on({r.stem: r.stem == stem for r in self.rows})

    def toggle_stem(self, stem: str) -> None:
        """Click on a name: that stem on/off. Ends a solo (its S goes dark)."""
        row = next(r for r in self.rows if r.stem == stem)
        self._set_solo_light(None)
        row.check.setChecked(not row.check.isChecked())

    def current_gains(self) -> dict[str, float]:
        on = set(self.audible())
        return {r.stem: db_to_gain(r.gain_db) if r.stem in on else 0.0 for r in self.rows}

    def update_gains(self) -> None:
        if not self.rows:  # only the original loop is loaded
            return
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
        pos = self.player.heard_pos
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

    def export_singles(self) -> list[Path]:
        """Files for dragging: every stem that is on as its own file, with its fader level."""
        files = []
        for r in self.rows:
            if not r.check.isChecked():
                continue
            g = db_to_gain(r.gain_db)
            if g <= 0:
                continue
            if abs(r.gain_db) < 0.05:
                files.append(r.path)
                continue
            audio, sr = sf.read(str(r.path), dtype="float32", always_2d=True)
            target = r.path.parent / f"{self.source.stem} - {r.stem} {r.gain_db:+.1f}dB.wav"
            sf.write(str(target), np.clip(audio * g, -1, 1), sr, subtype="PCM_24")
            files.append(target)
        return files

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

    # ---- updates
    def startup_update_check(self) -> None:
        """Only look; installing is always the user's choice (Einstellungen)."""
        self.check_updates(install=False)

    def check_updates(self, install: bool) -> None:
        if self._update_thread is not None:
            return
        self._installing = install
        self._set_update_state(phase="checking")
        self._update_thread = QThread(self)
        self._update_worker = UpdateWorker(install)
        self._update_worker.moveToThread(self._update_thread)
        self._update_thread.started.connect(self._update_worker.run)
        self._update_worker.checked.connect(self._on_update_checked)
        self._update_worker.progress.connect(lambda v: self._set_update_state(phase="downloading", progress=v))
        self._update_worker.installed.connect(self._on_update_installed)
        self._update_worker.checked.connect(lambda v, err: (not v or err or not install) and self._update_thread.quit())
        self._update_worker.installed.connect(lambda *_: self._update_thread.quit())
        self._update_thread.finished.connect(self._update_done)
        self._update_thread.start()

    def _update_done(self) -> None:
        self._update_thread = None

    def _on_update_checked(self, version: str, error: str) -> None:
        if error:
            self._set_update_state(phase="error", error=error)
        elif not version:
            self._set_update_state(phase="current")
        else:
            self._set_update_state(phase="downloading" if self._installing else "available", version=version)

    def _on_update_installed(self, version: str, error: str) -> None:
        if error:
            self._set_update_state(phase="error", error=error, version=version)
        else:
            self._set_update_state(phase="ready", version=version)

    update_changed = Signal()

    def _set_update_state(self, **state) -> None:
        if "phase" in state and state["phase"] not in ("downloading",):
            state.setdefault("progress", 0.0)
        self.update_state.update(state)
        phase = self.update_state["phase"]
        version = self.update_state.get("version")
        tips = {
            "available": f"Update auf {version} verfügbar",
            "downloading": f"Update auf {version} wird geladen",
            "ready": f"Update auf {version} geladen: in den Einstellungen neu starten",
        }
        self.gear_btn.set_badge(phase in tips, tips.get(phase, ""))
        self.update_changed.emit()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self)
        dlg.exec()
        self.update_changed.disconnect(dlg.refresh)
        dlg.deleteLater()

    def restart_for_update(self) -> None:
        from . import updater

        root = updater.install_root()
        if root is not None:
            updater.relaunch_flag(root).write_text("1", encoding="utf-8")
        self.close()
        QApplication.instance().quit()

    def closeEvent(self, e):
        self.timer.stop()
        self.player.close()
        super().closeEvent(e)


class UpdateWorker(QObject):
    """Talks to GitHub in the background: look for a new version, or download it."""

    checked = Signal(str, str)  # new version ("" = up to date), error ("" = none)
    progress = Signal(float)
    installed = Signal(str, str)  # version, error

    def __init__(self, install: bool) -> None:
        super().__init__()
        self.install = install

    def run(self) -> None:
        from . import updater

        try:
            rel = updater.check()
        except Exception as exc:
            self.checked.emit("", str(exc))
            return
        self.checked.emit(rel.version if rel else "", "")
        if not (rel and self.install):
            return
        root = updater.install_root()
        if root is None:
            self.installed.emit(rel.version, "Aus dem Quellcode gestartet: bitte 'git pull' ausführen.")
            return
        try:
            staged = updater.prepare(rel, root, lambda done, total: self.progress.emit(done / total if total else 0.0))
            updater.apply(staged, root)
            self.installed.emit(rel.version, "")
        except Exception as exc:
            self.installed.emit(rel.version, str(exc))


def download_icon(color: str) -> "QIcon":
    """Small arrow into a tray, drawn so it matches the text colour of the button."""
    from PySide6.QtGui import QIcon, QPixmap

    pm = QPixmap(28, 28)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    pen = QPen(QColor(color), 3)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(14, 3, 14, 17)
    p.drawPolyline([QPointF(8, 11), QPointF(14, 17), QPointF(20, 11)])
    p.drawPolyline([QPointF(4, 18), QPointF(4, 24), QPointF(24, 24), QPointF(24, 18)])
    p.end()
    return QIcon(pm)


class GearButton(QPushButton):
    """Settings button, top right. An orange dot shows that an update is waiting."""

    def __init__(self) -> None:
        super().__init__("⚙")
        self.setObjectName("icon")
        self.setToolTip("Einstellungen")
        self.setFixedWidth(40)
        self.badge = False

    def set_badge(self, on: bool, tooltip: str = "") -> None:
        self.badge = on
        self.setToolTip(tooltip or "Einstellungen")
        self.update()

    def paintEvent(self, e):
        super().paintEvent(e)
        if self.badge:
            p = QPainter(self)
            p.setRenderHint(QPainter.Antialiasing)
            p.setPen(QPen(QColor(BG), 2))
            p.setBrush(QColor(ACCENT))
            p.drawEllipse(QPointF(self.width() - 8, 8), 4.5, 4.5)


class SettingsDialog(QDialog):
    """Version and updates: search, download, restart. Nothing is installed by itself."""

    def __init__(self, win: "MainWindow") -> None:
        super().__init__(win)
        from . import updater

        self.win = win
        self.setWindowTitle("Einstellungen")
        self.setMinimumWidth(420)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 18, 20, 18)
        lay.setSpacing(10)

        lay.addWidget(MainWindow._caption("VERSION"))
        lay.addWidget(QLabel(f"stemn {__version__}"))
        lay.addSpacing(6)
        lay.addWidget(MainWindow._caption("UPDATES"))
        self.status = QLabel()
        self.status.setWordWrap(True)
        lay.addWidget(self.status)
        self.bar = QProgressBar()
        self.bar.setRange(0, 1000)
        self.bar.setTextVisible(False)
        self.bar.setFixedHeight(6)
        lay.addWidget(self.bar)
        row = QHBoxLayout()
        self.search_btn = QPushButton("Nach Updates suchen")
        self.search_btn.clicked.connect(lambda: win.check_updates(install=False))
        self.install_btn = QPushButton("JETZT AKTUALISIEREN")
        self.install_btn.setObjectName("primary")
        self.install_btn.clicked.connect(self.install_now)
        row.addWidget(self.search_btn)
        row.addStretch(1)
        row.addWidget(self.install_btn)
        lay.addLayout(row)
        hint = QLabel("stemn sucht beim Start nach Updates und zeigt sie am Zahnrad an. Installiert wird nur, wenn du es hier auslöst.")
        hint.setObjectName("muted")
        hint.setWordWrap(True)
        lay.addWidget(hint)
        for w in self.findChildren(QWidget):
            w.setFocusPolicy(Qt.NoFocus)
        win.update_changed.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        st = self.win.update_state
        busy = st["phase"] in ("checking", "downloading")
        self.search_btn.setEnabled(not busy)
        self.bar.setVisible(st["phase"] == "downloading")
        self.bar.setValue(int(st.get("progress", 0) * 1000))
        self.install_btn.setVisible(st["phase"] in ("available", "ready"))
        self.install_btn.setText("NEU STARTEN" if st["phase"] == "ready" else "JETZT AKTUALISIEREN")
        texts = {
            "idle": "Noch nicht gesucht.",
            "checking": "Suche nach Updates …",
            "current": f"stemn {__version__} ist aktuell.",
            "available": f"Version {st.get('version')} ist verfügbar.",
            "downloading": f"Version {st.get('version')} wird geladen …",
            "ready": f"Version {st.get('version')} ist geladen. „Neu starten“ installiert sie und öffnet stemn wieder.",
            "error": f"Update nicht möglich: {st.get('error')}",
        }
        self.status.setText(texts.get(st["phase"], ""))

    def install_now(self) -> None:
        if self.win.update_state["phase"] == "ready":
            self.win.restart_for_update()
        else:
            self.win.check_updates(install=True)


def main(argv: list[str] | None = None) -> int:
    app = QApplication.instance() or QApplication(sys.argv if argv is None else argv)
    app.setApplicationName("stemn")
    load_fonts()
    app.setStyleSheet(STYLE)
    icon = Path(__file__).parent / "assets" / "stemn.png"
    if icon.is_file():
        from PySide6.QtGui import QIcon

        app.setWindowIcon(QIcon(str(icon)))
    win = MainWindow()
    win.show()
    QTimer.singleShot(1500, win.startup_update_check)
    if argv and len(argv) > 1 and Path(argv[1]).is_file():
        QTimer.singleShot(0, lambda: win.start(Path(argv[1])))
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
