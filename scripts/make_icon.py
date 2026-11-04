"""Draws the app icon (loomn style) and writes PNG, ICNS and ICO.

Squircle on #0A0A0C, "stemn" white and centred, orange dot #FF5A1F.
Run: python scripts/make_icon.py   (needs PySide6 and Pillow)
"""

import math
import sys
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetricsF, QGuiApplication, QImage, QPainter, QPainterPath

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "src" / "stemn" / "assets"


def squircle(rect: QRectF, n: float = 5.0, steps: int = 720) -> QPainterPath:
    cx, cy, a, b = rect.center().x(), rect.center().y(), rect.width() / 2, rect.height() / 2
    path = QPainterPath()
    for i in range(steps + 1):
        t = 2 * math.pi * i / steps
        c, s = math.cos(t), math.sin(t)
        x = cx + a * math.copysign(abs(c) ** (2 / n), c)
        y = cy + b * math.copysign(abs(s) ** (2 / n), s)
        path.moveTo(x, y) if i == 0 else path.lineTo(x, y)
    path.closeSubpath()
    return path


def draw(size: int = 1024, inset: float = 100 / 1024) -> QImage:
    img = QImage(size, size, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    m = size * inset
    shape = QRectF(m, m, size - 2 * m, size - 2 * m)
    p.fillPath(squircle(shape), QColor("#0A0A0C"))

    font = QFont("Barlow Condensed", 10, QFont.Bold)
    font.setPixelSize(int(shape.width() * 0.34))
    fm = QFontMetricsF(font)
    text = "stemn"
    w = fm.horizontalAdvance(text)
    dot_r = font.pixelSize() * 0.075
    total = w + dot_r * 2.6
    x = shape.center().x() - total / 2
    baseline = shape.center().y() + fm.xHeight() / 2
    p.setFont(font)
    p.setPen(QColor("#F0EEE9"))
    p.drawText(QPointF(x, baseline), text)
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#FF5A1F"))
    p.drawEllipse(QPointF(x + w + dot_r * 1.4, baseline - fm.capHeight() + dot_r), dot_r, dot_r)
    p.end()
    return img


def main() -> int:
    app = QGuiApplication(sys.argv)  # noqa: F841
    for f in (ROOT / "src" / "stemn" / "fonts").glob("*.ttf"):
        QFontDatabase.addApplicationFont(str(f))
    OUT.mkdir(parents=True, exist_ok=True)
    png = OUT / "stemn.png"
    draw(1024).save(str(png))
    # Windows icons have no Mac margin: fill more of the square
    win_png = OUT / "stemn-win.png"
    draw(1024, inset=24 / 1024).save(str(win_png))

    from PIL import Image

    Image.open(png).save(OUT / "stemn.icns")
    Image.open(win_png).save(OUT / "stemn.ico", sizes=[(s, s) for s in (16, 24, 32, 48, 64, 128, 256)])
    print("geschrieben:", *sorted(x.name for x in OUT.iterdir()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
