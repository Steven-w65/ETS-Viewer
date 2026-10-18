"""Small line icons drawn locally with Qt at display resolution."""
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap


def icon(name: str, color: str, size=18) -> QIcon:
    pixmap = QPixmap(size * 2, size * 2)
    pixmap.setDevicePixelRatio(2)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.scale(size / 20, size / 20)
    painter.setPen(QPen(QColor(color), 1.35, Qt.PenStyle.SolidLine,
                        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    def line(x1, y1, x2, y2):
        painter.drawLine(QPointF(x1, y1), QPointF(x2, y2))
    if name == "folder":
        painter.drawRoundedRect(QRectF(2, 5, 16, 12), 2, 2)
        line(3, 5, 3, 3)
        line(3, 3, 8, 3)
        line(8, 3, 10, 5)
    elif name == "sidebar":
        painter.drawRoundedRect(QRectF(2, 3, 16, 14), 2, 2)
        line(7, 3, 7, 17)
    elif name in {"back", "up", "down", "next"}:
        points = {"back": ((12, 5), (7, 10), (12, 15)), "next": ((8, 5), (13, 10), (8, 15)),
                  "up": ((5, 12), (10, 7), (15, 12)), "down": ((5, 8), (10, 13), (15, 8))}[name]
        line(*points[0], *points[1])
        line(*points[1], *points[2])
    elif name == "download":
        line(10, 2, 10, 12)
        line(6, 8, 10, 12)
        line(10, 12, 14, 8)
        line(3, 13, 3, 17)
        line(3, 17, 17, 17)
        line(17, 17, 17, 13)
    elif name == "settings":
        painter.drawEllipse(QRectF(6.5, 6.5, 7, 7))
        for x, y in ((4, 4), (16, 4), (4, 16), (16, 16)):
            line(x, y, 10 + (x - 10) * .65, 10 + (y - 10) * .65)
        for x, y in ((10, 2), (10, 18), (2, 10), (18, 10)):
            line(x, y, 10 + (x - 10) * .7, 10 + (y - 10) * .7)
    else:
        painter.drawEllipse(QRectF(3, 3, 14, 14))
        line(10, 9, 10, 14)
        painter.drawPoint(QPointF(10, 6))
    painter.end()
    return QIcon(pixmap)
