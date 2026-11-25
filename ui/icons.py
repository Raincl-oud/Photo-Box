"""Minimalist crisp 1.5px stroke QPainterPath vector icons and PhotoBox brand mark."""
from __future__ import annotations

import math
from typing import Dict

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap

ICON_NAMES = (
    "folder",
    "folder_plus",
    "plus",
    "minus",
    "trash",
    "undo",
    "skip",
    "prev",
    "next",
    "arrow_left",
    "arrow_right",
    "chevron_left",
    "chevron_right",
    "chevron_down",
    "sidebar",
    "sidebar_expand",
    "sidebar_collapse",
    "check",
    "check_circle",
    "x",
    "close",
    "edit",
    "refresh",
    "reset",
    "sort",
    "search",
    "settings",
    "sliders",
    "play",
    "pause",
    "volume",
    "mute",
    "video",
    "image",
    "camera",
    "copy",
    "palette",
    "keyboard",
    "globe",
    "info",
    "zoom_in",
    "zoom_out",
    "fit",
    "eye",
    "eye_off",
    "tag",
    "sparkles",
    "gallery",
)


def _build_icon_path(name: str) -> QPainterPath:
    """Build normalized 24x24 stroke QPainterPath for an icon name."""
    p = QPainterPath()
    key = name.strip().lower()

    if key == "close":
        key = "x"
    elif key == "reset":
        key = "refresh"

    if key == "folder":
        p.moveTo(3, 7)
        p.lineTo(3, 18)
        p.quadTo(3, 19.5, 4.5, 19.5)
        p.lineTo(19.5, 19.5)
        p.quadTo(21, 19.5, 21, 18)
        p.lineTo(21, 9)
        p.quadTo(21, 7.5, 19.5, 7.5)
        p.lineTo(12, 7.5)
        p.lineTo(10, 5)
        p.lineTo(4.5, 5)
        p.quadTo(3, 5, 3, 6.5)
        p.closeSubpath()
    elif key == "folder_plus":
        p = _build_icon_path("folder")
        p.moveTo(12, 11)
        p.lineTo(12, 16)
        p.moveTo(9.5, 13.5)
        p.lineTo(14.5, 13.5)
    elif key == "plus":
        p.moveTo(12, 5)
        p.lineTo(12, 19)
        p.moveTo(5, 12)
        p.lineTo(19, 12)
    elif key == "minus":
        p.moveTo(5, 12)
        p.lineTo(19, 12)
    elif key == "x":
        p.moveTo(6, 6)
        p.lineTo(18, 18)
        p.moveTo(18, 6)
        p.lineTo(6, 18)
    elif key == "check":
        p.moveTo(5, 12.5)
        p.lineTo(9.5, 17)
        p.lineTo(19, 7.5)
    elif key == "check_circle":
        p.addEllipse(QRectF(3.5, 3.5, 17, 17))
        p.moveTo(8, 12.2)
        p.lineTo(11, 15.2)
        p.lineTo(16.5, 9.2)
    elif key == "trash":
        p.moveTo(4, 6.5)
        p.lineTo(20, 6.5)
        p.moveTo(9.5, 6.5)
        p.lineTo(9.5, 4.5)
        p.lineTo(14.5, 4.5)
        p.lineTo(14.5, 6.5)
        p.moveTo(6.5, 6.5)
        p.lineTo(7.5, 19.5)
        p.lineTo(16.5, 19.5)
        p.lineTo(17.5, 6.5)
        p.moveTo(10, 10)
        p.lineTo(10, 16)
        p.moveTo(14, 10)
        p.lineTo(14, 16)
    elif key == "undo":
        p.moveTo(8, 8)
        p.lineTo(4, 12)
        p.lineTo(8, 16)
        p.moveTo(4, 12)
        p.lineTo(14.5, 12)
        p.arcMoveTo(QRectF(10, 12, 9.5, 7), 90)
        p.arcTo(QRectF(10, 12, 9.5, 7), 90, -180)
        p.lineTo(11, 19)
    elif key in ("chevron_left", "prev"):
        p.moveTo(15, 6)
        p.lineTo(9, 12)
        p.lineTo(15, 18)
    elif key in ("chevron_right", "next"):
        p.moveTo(9, 6)
        p.lineTo(15, 12)
        p.lineTo(9, 18)
    elif key == "chevron_down":
        p.moveTo(6, 9)
        p.lineTo(12, 15)
        p.lineTo(18, 9)
    elif key == "arrow_left":
        p.moveTo(19, 12)
        p.lineTo(5, 12)
        p.moveTo(10, 7)
        p.lineTo(5, 12)
        p.lineTo(10, 17)
    elif key in ("arrow_right", "skip"):
        p.moveTo(5, 12)
        p.lineTo(19, 12)
        p.moveTo(14, 7)
        p.lineTo(19, 12)
        p.lineTo(14, 17)
    elif key in ("sidebar", "sidebar_expand", "sidebar_collapse"):
        p.addRoundedRect(QRectF(3.5, 4.5, 17, 15), 2.5, 2.5)
        p.moveTo(9.5, 4.5)
        p.lineTo(9.5, 19.5)
        if key == "sidebar_expand":
            p.moveTo(13, 9.5)
            p.lineTo(15.5, 12)
            p.lineTo(13, 14.5)
        elif key == "sidebar_collapse":
            p.moveTo(15.5, 9.5)
            p.lineTo(13, 12)
            p.lineTo(15.5, 14.5)
    elif key == "edit":
        p.moveTo(14.5, 5.5)
        p.lineTo(18.5, 9.5)
        p.lineTo(8.5, 19.5)
        p.lineTo(4.5, 19.5)
        p.lineTo(4.5, 15.5)
        p.closeSubpath()
    elif key == "refresh":
        p.arcMoveTo(QRectF(4.5, 4.5, 15, 15), 40)
        p.arcTo(QRectF(4.5, 4.5, 15, 15), 40, 290)
        p.moveTo(16.5, 4.5)
        p.lineTo(18.5, 8.5)
        p.lineTo(14.5, 8.5)
    elif key == "sort":
        p.moveTo(4, 7)
        p.lineTo(14, 7)
        p.moveTo(4, 12)
        p.lineTo(11, 12)
        p.moveTo(4, 17)
        p.lineTo(8, 17)
        p.moveTo(17, 6)
        p.lineTo(17, 18)
        p.moveTo(14.5, 15.5)
        p.lineTo(17, 18)
        p.lineTo(19.5, 15.5)
    elif key == "search":
        p.addEllipse(QRectF(4.5, 4.5, 11.5, 11.5))
        p.moveTo(14.5, 14.5)
        p.lineTo(19.5, 19.5)
    elif key == "settings":
        p.addEllipse(QRectF(9, 9, 6, 6))
        p.addEllipse(QRectF(4.5, 4.5, 15, 15))
        for deg in range(0, 360, 45):
            rad = math.radians(deg)
            x1 = 12 + 7.4 * math.cos(rad)
            y1 = 12 + 7.4 * math.sin(rad)
            x2 = 12 + 9.6 * math.cos(rad)
            y2 = 12 + 9.6 * math.sin(rad)
            p.moveTo(x1, y1)
            p.lineTo(x2, y2)
    elif key == "sliders":
        p.moveTo(4, 7)
        p.lineTo(20, 7)
        p.addEllipse(QRectF(7.5, 5, 4, 4))
        p.moveTo(4, 12)
        p.lineTo(20, 12)
        p.addEllipse(QRectF(13.5, 10, 4, 4))
        p.moveTo(4, 17)
        p.lineTo(20, 17)
        p.addEllipse(QRectF(9, 15, 4, 4))
    elif key == "play":
        p.moveTo(7, 5)
        p.lineTo(19, 12)
        p.lineTo(7, 19)
        p.closeSubpath()
    elif key == "pause":
        p.addRoundedRect(QRectF(6.5, 5, 3.8, 14), 1, 1)
        p.addRoundedRect(QRectF(13.7, 5, 3.8, 14), 1, 1)
    elif key == "volume":
        p.moveTo(4, 9.5)
        p.lineTo(8, 9.5)
        p.lineTo(13, 5.5)
        p.lineTo(13, 18.5)
        p.lineTo(8, 14.5)
        p.lineTo(4, 14.5)
        p.closeSubpath()
        p.arcMoveTo(QRectF(12, 8.5, 5, 7), 60)
        p.arcTo(QRectF(12, 8.5, 5, 7), 60, -120)
        p.arcMoveTo(QRectF(13, 6, 7.5, 12), 55)
        p.arcTo(QRectF(13, 6, 7.5, 12), 55, -110)
    elif key == "mute":
        p.moveTo(4, 9.5)
        p.lineTo(8, 9.5)
        p.lineTo(13, 5.5)
        p.lineTo(13, 18.5)
        p.lineTo(8, 14.5)
        p.lineTo(4, 14.5)
        p.closeSubpath()
        p.moveTo(16, 9.5)
        p.lineTo(20.5, 14.5)
        p.moveTo(20.5, 9.5)
        p.lineTo(16, 14.5)
    elif key == "video":
        p.addRoundedRect(QRectF(3.5, 6.5, 13, 11), 2, 2)
        p.moveTo(16.5, 10)
        p.lineTo(21, 7.5)
        p.lineTo(21, 16.5)
        p.lineTo(16.5, 14)
    elif key == "image":
        p.addRoundedRect(QRectF(3.5, 4.5, 17, 15), 2, 2)
        p.addEllipse(QRectF(7, 7.5, 2.8, 2.8))
        p.moveTo(4, 17)
        p.lineTo(10, 12)
        p.lineTo(14, 15.5)
        p.lineTo(17, 13)
        p.lineTo(20, 17)
    elif key == "camera":
        p.moveTo(4, 8)
        p.lineTo(7.5, 8)
        p.lineTo(9, 5.5)
        p.lineTo(15, 5.5)
        p.lineTo(16.5, 8)
        p.lineTo(20, 8)
        p.lineTo(20, 18.5)
        p.lineTo(4, 18.5)
        p.closeSubpath()
        p.addEllipse(QRectF(9, 10, 6, 6))
    elif key == "copy":
        p.addRoundedRect(QRectF(8.5, 8.5, 11, 11.5), 2, 2)
        p.moveTo(15.5, 8.5)
        p.lineTo(15.5, 5.5)
        p.quadTo(15.5, 4.2, 14.2, 4.2)
        p.lineTo(5.8, 4.2)
        p.quadTo(4.5, 4.2, 4.5, 5.5)
        p.lineTo(4.5, 14.5)
        p.quadTo(4.5, 15.8, 5.8, 15.8)
        p.lineTo(8.5, 15.8)
    elif key == "palette":
        p.arcMoveTo(QRectF(4, 4, 16, 16), 0)
        p.arcTo(QRectF(4, 4, 16, 16), 0, 290)
        p.quadTo(14.5, 13.5, 15.5, 15.5)
        p.quadTo(20, 15, 20, 12)
        p.addEllipse(QRectF(8.2, 8.5, 1.8, 1.8))
        p.addEllipse(QRectF(12.2, 7.2, 1.8, 1.8))
        p.addEllipse(QRectF(15.5, 9.8, 1.8, 1.8))
    elif key == "keyboard":
        p.addRoundedRect(QRectF(3, 6, 18, 12), 2.5, 2.5)
        p.moveTo(6.5, 10)
        p.lineTo(8, 10)
        p.moveTo(11, 10)
        p.lineTo(12.5, 10)
        p.moveTo(15.5, 10)
        p.lineTo(17, 10)
        p.moveTo(7, 14.2)
        p.lineTo(17, 14.2)
    elif key == "globe":
        p.addEllipse(QRectF(3.5, 3.5, 17, 17))
        p.addEllipse(QRectF(8.2, 3.5, 7.6, 17))
        p.moveTo(3.8, 12)
        p.lineTo(20.2, 12)
        p.moveTo(5.2, 8)
        p.lineTo(18.8, 8)
        p.moveTo(5.2, 16)
        p.lineTo(18.8, 16)
    elif key == "info":
        p.addEllipse(QRectF(3.5, 3.5, 17, 17))
        p.moveTo(12, 10.5)
        p.lineTo(12, 16.5)
        p.moveTo(12, 7.5)
        p.lineTo(12, 8)
    elif key == "zoom_in":
        p.addEllipse(QRectF(4.5, 4.5, 11.5, 11.5))
        p.moveTo(14.5, 14.5)
        p.lineTo(19.5, 19.5)
        p.moveTo(10.2, 7.5)
        p.lineTo(10.2, 13)
        p.moveTo(7.5, 10.2)
        p.lineTo(13, 10.2)
    elif key == "zoom_out":
        p.addEllipse(QRectF(4.5, 4.5, 11.5, 11.5))
        p.moveTo(14.5, 14.5)
        p.lineTo(19.5, 19.5)
        p.moveTo(7.5, 10.2)
        p.lineTo(13, 10.2)
    elif key == "fit":
        p.moveTo(4.5, 8.5)
        p.lineTo(4.5, 4.5)
        p.lineTo(8.5, 4.5)
        p.moveTo(15.5, 4.5)
        p.lineTo(19.5, 4.5)
        p.lineTo(19.5, 8.5)
        p.moveTo(19.5, 15.5)
        p.lineTo(19.5, 19.5)
        p.lineTo(15.5, 19.5)
        p.moveTo(8.5, 19.5)
        p.lineTo(4.5, 19.5)
        p.lineTo(4.5, 15.5)
    elif key == "eye":
        p.moveTo(3, 12)
        p.quadTo(12, 5, 21, 12)
        p.quadTo(12, 19, 3, 12)
        p.addEllipse(QRectF(9.5, 9.5, 5, 5))
    elif key == "eye_off":
        p = _build_icon_path("eye")
        p.moveTo(4.5, 4.5)
        p.lineTo(19.5, 19.5)
    elif key == "tag":
        p.moveTo(4, 12)
        p.lineTo(12, 4)
        p.lineTo(19.5, 4)
        p.lineTo(19.5, 11.5)
        p.lineTo(11.5, 19.5)
        p.closeSubpath()
        p.addEllipse(QRectF(14.5, 7, 2.2, 2.2))
    elif key == "sparkles":
        p.moveTo(12, 3.5)
        p.lineTo(14, 10)
        p.lineTo(20.5, 12)
        p.lineTo(14, 14)
        p.lineTo(12, 20.5)
        p.lineTo(10, 14)
        p.lineTo(3.5, 12)
        p.lineTo(10, 10)
        p.closeSubpath()
    elif key == "gallery":
        p.addRoundedRect(QRectF(3.5, 4.0, 7.0, 7.0), 1.6, 1.6)
        p.addRoundedRect(QRectF(13.5, 4.0, 7.0, 7.0), 1.6, 1.6)
        p.addRoundedRect(QRectF(3.5, 13.0, 7.0, 7.0), 1.6, 1.6)
        p.addRoundedRect(QRectF(13.5, 13.0, 7.0, 7.0), 1.6, 1.6)

    return p


def draw_icon(
    painter: QPainter,
    name: str,
    rect: QRectF,
    color: str | QColor,
    stroke_width: float = 1.5,
) -> None:
    """Draw a vector icon scaled smoothly inside rect."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    qcolor = QColor(color) if isinstance(color, str) else color
    pen = QPen(qcolor, stroke_width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)

    scale = min(rect.width(), rect.height()) / 24.0
    tx = rect.left() + (rect.width() - 24.0 * scale) / 2.0
    ty = rect.top() + (rect.height() - 24.0 * scale) / 2.0
    painter.translate(QPointF(tx, ty))
    painter.scale(scale, scale)
    painter.drawPath(_build_icon_path(name))
    painter.restore()


_ICON_CACHE: Dict[tuple[str, str, int, float], QPixmap] = {}


def render_pixmap(
    name: str,
    size: int = 16,
    color: str | QColor = "#F4F4F5",
    device_pixel_ratio: float = 2.0,
    stroke_width: float = 1.5,
) -> QPixmap:
    """Render an icon onto a crisp Retina-ready QPixmap."""
    if isinstance(size, str) and isinstance(color, int):
        # Gracefully accept swapped (color, size) positional if ever called.
        size, color = color, size
    qcolor = QColor(color) if isinstance(color, str) else color
    dpr = max(1.0, float(device_pixel_ratio))
    cache_key = (name, qcolor.name(QColor.NameFormat.HexArgb), int(size), dpr, float(stroke_width))
    cached = _ICON_CACHE.get(cache_key)
    if cached is not None:
        return cached

    px = max(1, int(round(size * dpr)))
    pix = QPixmap(px, px)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    draw_icon(painter, name, QRectF(0, 0, size, size), qcolor, stroke_width=stroke_width)
    painter.end()
    _ICON_CACHE[cache_key] = pix
    return pix


def render_icon(
    name: str,
    size: int = 16,
    color: str | QColor = "#F4F4F5",
    stroke_width: float = 1.5,
) -> QIcon:
    """Render a crisp vector QIcon."""
    return QIcon(
        render_pixmap(
            name,
            size=size,
            color=color,
            device_pixel_ratio=2.0,
            stroke_width=stroke_width,
        )
    )


_BRAND_LOGO_CACHE: Dict[tuple[int, float, bool], QPixmap] = {}


def draw_brand_logo(
    painter: QPainter,
    rect: QRectF,
    dark_mode: bool = True,
) -> None:
    """Paint the Optical Aperture Box brand badge smoothly inside rect."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    side = min(rect.width(), rect.height())
    scale = side / 24.0
    tx = rect.left() + (rect.width() - 24.0 * scale) / 2.0
    ty = rect.top() + (rect.height() - 24.0 * scale) / 2.0
    painter.translate(QPointF(tx, ty))
    painter.scale(scale, scale)

    badge = QRectF(1.0, 1.0, 22.0, 22.0)
    bg = QColor("#141519")
    bevel = QColor("#2C303A" if dark_mode else "#3A3F4B")
    painter.setPen(QPen(bevel, 1.0))
    painter.setBrush(bg)
    painter.drawRoundedRect(badge, 5.2, 5.2)

    # Subtle inner recessed darkroom rim following the rounded square contour
    painter.setPen(QPen(QColor(255, 255, 255, 18), 0.75))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawRoundedRect(badge.adjusted(0.85, 0.85, -0.85, -0.85), 4.4, 4.4)

    # Outer precision viewfinder corner brackets (PhotoBox optical frame)
    bp = QPainterPath()
    # Top-left bracket
    bp.moveTo(5.0, 8.6)
    bp.lineTo(5.0, 5.8)
    bp.quadTo(5.0, 5.0, 5.8, 5.0)
    bp.lineTo(8.6, 5.0)
    # Top-right bracket
    bp.moveTo(15.4, 5.0)
    bp.lineTo(18.2, 5.0)
    bp.quadTo(19.0, 5.0, 19.0, 5.8)
    bp.lineTo(19.0, 8.6)
    # Bottom-right bracket
    bp.moveTo(19.0, 15.4)
    bp.lineTo(19.0, 18.2)
    bp.quadTo(19.0, 19.0, 18.2, 19.0)
    bp.lineTo(15.4, 19.0)
    # Bottom-left bracket
    bp.moveTo(8.6, 19.0)
    bp.lineTo(5.8, 19.0)
    bp.quadTo(5.0, 19.0, 5.0, 18.2)
    bp.lineTo(5.0, 15.4)

    painter.setPen(
        QPen(
            QColor("#F4F4F5"),
            1.45,
            Qt.PenStyle.SolidLine,
            Qt.PenCapStyle.RoundCap,
            Qt.PenJoinStyle.RoundJoin,
        )
    )
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(bp)

    # Subtle inner geometric frame connecting viewfinder zone
    painter.setPen(QPen(QColor("#3F4451"), 0.8))
    painter.drawRoundedRect(QRectF(6.8, 6.8, 10.4, 10.4), 2.0, 2.0)

    # Optical Amber aperture lens ring + crisp center focal diamond/dot
    amber = QColor("#F59E0B")
    painter.setPen(QPen(amber, 1.75))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawEllipse(QRectF(8.1, 8.1, 7.8, 7.8))

    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(amber)
    painter.drawEllipse(QRectF(10.8, 10.8, 2.4, 2.4))
    painter.setBrush(QColor("#FDE68A"))
    painter.drawEllipse(QRectF(13.6, 8.9, 1.15, 1.15))

    painter.restore()


def render_brand_logo(
    size: int = 24,
    device_pixel_ratio: float = 2.0,
    dark_mode: bool = True,
) -> QPixmap:
    """Render the PhotoBox brand emblem onto a Retina-ready QPixmap."""
    dpr = max(1.0, float(device_pixel_ratio))
    s = max(12, int(size))
    key = (s, round(dpr, 2), bool(dark_mode))
    cached = _BRAND_LOGO_CACHE.get(key)
    if cached is not None:
        return cached

    px = max(1, int(round(s * dpr)))
    pix = QPixmap(px, px)
    pix.setDevicePixelRatio(dpr)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    draw_brand_logo(painter, QRectF(0.0, 0.0, float(s), float(s)), dark_mode=dark_mode)
    painter.end()
    _BRAND_LOGO_CACHE[key] = pix
    return pix
