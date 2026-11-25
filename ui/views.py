"""Optical Instrument views, sculpted 3D keycaps, Floating Console Deck, and in-window overlays for PhotoBox v0.5.0."""
from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from PyQt6.QtCore import (
    QEvent,
    QPoint,
    QPointF,
    QRectF,
    QSize,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QIntValidator,
    QKeyEvent,
    QPainter,
    QPainterPath,
    QPen,
)
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QFrame,
    QGraphicsOpacityEffect,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMenu,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from core.session_manager import (
    APP_VERSION,
    DEFAULT_CATEGORY_COLOR,
    DEFAULT_KEYBINDS,
    DEFAULT_TRASH_COLOR,
    DEFAULT_VISIBLE_METADATA_FIELDS,
    METADATA_FIELD_KEYS,
    SORT_MODES,
    AppSettings,
    DefaultCategorySpec,
    SessionState,
    count_subfolders,
    has_subfolders,
    next_available_keybind,
    sanitize_category_name,
    sanitize_trash_category_name,
    validate_keybind,
)
from ui.flow_layout import CenteredFlowLayout
from ui.filmstrip import FilmstripBar
from ui.gallery_sidebar import SessionGallerySidebar
from ui.i18n import get_language, set_language, tr
from ui.icons import draw_brand_logo, draw_icon, render_brand_logo, render_icon
from ui.motion import (
    ValueAnimator,
    WidgetSlideFadeTransition,
    animate_sidebar_width,
    ease_out,
    ease_overshoot,
    lerp_color,
    lerp_float,
    prefers_reduced_motion,
)
from ui.photo_canvas import PhotoCanvas
from ui.theme import (
    CATEGORY_COLOR_PRESETS,
    MONO_FONT_FAMILY,
    PALETTE,
    UI_FONT_FAMILY,
)


def _escape_mnemonic(text: str) -> str:
    """Escape '&' as '&&' for Qt buttons/checkboxes/QMenu actions so mnemonics never trigger."""
    return str(text).replace("&", "&&")


def _contrast_ink(bg_hex: str) -> str:
    """Choose dark or white ink based on WCAG perceived luminance for guaranteed contrast."""
    c = QColor(bg_hex)
    if not c.isValid():
        return "#09090B"
    lum = 0.299 * c.red() + 0.587 * c.green() + 0.114 * c.blue()
    return "#09090B" if lum > 150 else "#FFFFFF"


def _hex_with_alpha(hex_str: str, alpha: int) -> QColor:
    c = QColor(hex_str)
    if not c.isValid():
        c = QColor(PALETTE["accent"])
    c.setAlpha(max(0, min(255, alpha)))
    return c


def _format_qt_key_token(event: QKeyEvent) -> Optional[str]:
    """Translate a QKeyEvent into a canonical PhotoBox key token string."""
    key = event.key()
    mods = event.modifiers()
    if key in (
        Qt.Key.Key_Control,
        Qt.Key.Key_Shift,
        Qt.Key.Key_Alt,
        Qt.Key.Key_Meta,
        Qt.Key.Key_unknown,
    ):
        return None
    if mods & Qt.KeyboardModifier.ControlModifier:
        if key == Qt.Key.Key_Z:
            return "CTRL+Z"
        return None

    special_map = {
        Qt.Key.Key_Tab: "TAB",
        Qt.Key.Key_Backtab: "TAB",
        Qt.Key.Key_Space: "SPACE",
        Qt.Key.Key_Backspace: "BACKSPACE",
        Qt.Key.Key_Delete: "DEL",
        Qt.Key.Key_Left: "LEFT",
        Qt.Key.Key_Right: "RIGHT",
        Qt.Key.Key_Up: "UP",
        Qt.Key.Key_Down: "DOWN",
        Qt.Key.Key_Return: "RETURN",
        Qt.Key.Key_Enter: "ENTER",
        Qt.Key.Key_Escape: "ESCAPE",
        Qt.Key.Key_Insert: "INS",
        Qt.Key.Key_Home: "HOME",
        Qt.Key.Key_End: "END",
        Qt.Key.Key_PageUp: "PGUP",
        Qt.Key.Key_PageDown: "PGDN",
    }
    if key in special_map:
        return special_map[key]
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F12:
        return f"F{key - Qt.Key.Key_F1 + 1}"

    text = (event.text() or "").strip()
    if len(text) == 1 and ord(text) >= 32:
        return text.upper()
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return chr(key).upper()
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(key)
    return None


def _draw_dashed_rounded_rect(
    painter: QPainter,
    rect: QRectF,
    radius: float,
    stroke_color: QColor,
    fill_color: Optional[QColor] = None,
    width: float = 1.25,
) -> None:
    """Paint a clean dashed rounded border using QPainterPath (no QSS dashed artifacts)."""
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    path = QPainterPath()
    path.addRoundedRect(rect, radius, radius)
    if fill_color is not None:
        painter.fillPath(path, fill_color)
    pen = QPen(stroke_color, width, Qt.PenStyle.DashLine, Qt.PenCapStyle.RoundCap)
    pen.setDashPattern([4.5, 3.5])
    painter.setPen(pen)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.drawPath(path)
    painter.restore()


# ---------------------------------------------------------------------------
# Sculpted 3D Keycaps & Custom Dashed Console Widgets
# ---------------------------------------------------------------------------


class CategoryKeycapButton(QPushButton):
    """3D tactile instrument keycap with integrated accent key cap, count pill, & active LED."""

    rename_requested = pyqtSignal(str)
    keybind_change_requested = pyqtSignal(str)
    color_change_requested = pyqtSignal(str)
    revert_requested = pyqtSignal(str)

    def __init__(
        self,
        category: str,
        key_label: str = "",
        count: int = 0,
        accent_color: Optional[str] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.category = category
        self.key_label = (key_label or "").upper()
        self.count = int(count)
        self.accent_color = accent_color or DEFAULT_CATEGORY_COLOR
        self._is_active = False
        self._pulse_amount = 0.0
        self._hover_t = 0.0
        self._press_t = 0.0
        self._active_t = 0.0
        self._count_bump_t = 0.0
        self._pulse_anim = ValueAnimator(0.0, parent=self)
        self._hover_anim = ValueAnimator(0.0, parent=self)
        self._press_anim = ValueAnimator(0.0, parent=self)
        self._active_anim = ValueAnimator(0.0, parent=self)
        self._count_bump_anim = ValueAnimator(0.0, parent=self)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)
        self.setFixedHeight(38)
        self._recalculate_size()
        self._refresh_tooltip()

    def set_category_name(self, name: str) -> None:
        self.category = name
        self._recalculate_size()
        self._refresh_tooltip()
        self.update()

    def set_keybind(self, key_label: str) -> None:
        self.key_label = (key_label or "").upper()
        self._recalculate_size()
        self._refresh_tooltip()
        self.update()

    def set_count(self, count: int) -> None:
        new_count = max(0, int(count))
        changed = new_count != self.count
        self.count = new_count
        self._recalculate_size()
        if changed and self.isVisible() and not prefers_reduced_motion():
            self._count_bump_anim.animate_to(
                0.0,
                duration_ms=155,
                curve=ease_out(),
                start_from=1.0,
                on_value=self._set_count_bump,
                non_vestibular=True,
            )
        self.update()

    def set_accent_color(self, hex_color: str) -> None:
        self.accent_color = hex_color or DEFAULT_CATEGORY_COLOR
        self.update()

    def set_active(self, active: bool) -> None:
        flag = bool(active)
        if self._is_active != flag:
            self._is_active = flag
            self._active_anim.animate_to(
                1.0 if flag else 0.0,
                duration_ms=160,
                curve=ease_out(),
                on_value=self._set_active_t,
                non_vestibular=True,
            )

    def trigger_pulse(self, duration_ms: int = 140) -> None:
        if prefers_reduced_motion():
            return
        down_ms = max(65, int(duration_ms * 0.6))
        up_ms = max(95, int(duration_ms))
        self._pulse_anim.animate_to(
            1.0,
            duration_ms=down_ms,
            curve=ease_out(),
            on_value=self._set_pulse,
            on_done=lambda: self._pulse_anim.animate_to(
                0.0, duration_ms=up_ms, curve=ease_out(), on_value=self._set_pulse
            ),
        )

    def _set_pulse(self, val: float) -> None:
        self._pulse_amount = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_hover_t(self, val: float) -> None:
        self._hover_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_press_t(self, val: float) -> None:
        self._press_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_active_t(self, val: float) -> None:
        self._active_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_count_bump(self, val: float) -> None:
        self._count_bump_t = max(0.0, min(1.0, float(val)))
        self.update()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        super().enterEvent(event)
        self._hover_anim.animate_to(
            1.0,
            duration_ms=115,
            curve=ease_out(),
            on_value=self._set_hover_t,
            non_vestibular=True,
        )

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self._hover_anim.animate_to(
            0.0,
            duration_ms=155,
            curve=ease_out(),
            on_value=self._set_hover_t,
            non_vestibular=True,
        )
        if self._press_t > 0.0:
            self._press_anim.animate_to(
                0.0, duration_ms=120, curve=ease_out(), on_value=self._set_press_t
            )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_anim.animate_to(
                1.0, duration_ms=75, curve=ease_out(), on_value=self._set_press_t
            )
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._press_t > 0.0 or self.isDown():
            self._press_anim.animate_to(
                0.0, duration_ms=135, curve=ease_out(), on_value=self._set_press_t
            )
        super().mouseReleaseEvent(event)

    def _refresh_tooltip(self) -> None:
        if self.key_label:
            self.setToolTip(
                tr("sorting.cat_tip", category=self.category, key=self.key_label)
            )
        else:
            self.setToolTip(tr("sorting.cat_tip_nokey", category=self.category))

    def _recalculate_size(self) -> None:
        key_w = 0
        if self.key_label:
            kf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.Bold)
            key_w = max(22, QFontMetrics(kf).horizontalAdvance(self.key_label) + 12) + 8
        lf = QFont(UI_FONT_FAMILY, 10, QFont.Weight.DemiBold)
        lbl_w = QFontMetrics(lf).horizontalAdvance(self.category)
        cf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.Medium)
        cnt_text = str(self.count)
        cnt_w = max(20, QFontMetrics(cf).horizontalAdvance(cnt_text) + 10)
        total_w = max(84, 10 + key_w + lbl_w + 8 + cnt_w + 10)
        self.setFixedWidth(min(260, total_w))

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 38)

    def _show_context_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        act_rename = menu.addAction(tr("sorting.menu.rename"))
        act_keybind = menu.addAction(tr("sorting.menu.keybind"))
        act_color = menu.addAction(tr("sorting.menu.color"))
        menu.addSeparator()
        act_revert = menu.addAction(tr("sorting.menu.revert"))
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act_rename:
            self.rename_requested.emit(self.category)
        elif chosen == act_keybind:
            self.keybind_change_requested.emit(self.category)
        elif chosen == act_color:
            self.color_change_requested.emit(self.category)
        elif chosen == act_revert:
            self.revert_requested.emit(self.category)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        press_t = max(
            self._press_t,
            1.0 if self.isDown() else 0.0,
            self._pulse_amount * 0.85,
        )
        hover_t = max(self._hover_t, 1.0 if self.underMouse() else 0.0)
        active_t = max(self._active_t, 1.0 if (self._is_active and self._active_t == 0.0 and prefers_reduced_motion()) else 0.0)
        highlight_t = max(hover_t, active_t, self._pulse_amount)

        sunk = lerp_float(0.0, 1.5, press_t)
        lip_h = lerp_float(2.5, 1.0, press_t)
        body_rect = QRectF(1.0, 1.0 + sunk, self.width() - 2.0, self.height() - 2.0 - lip_h - sunk)
        lip_rect = QRectF(1.0, body_rect.top() + 2.0, self.width() - 2.0, self.height() - 3.0 - sunk)
        radius = 8.0

        accent = QColor(self.accent_color or PALETTE["accent"])
        if not accent.isValid():
            accent = QColor(PALETTE["accent"])

        # Bottom sculpted 3D lip
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["bg_keycap_lip"]))
        painter.drawRoundedRect(lip_rect, radius, radius)

        # Keycap surface fill (smooth hover/active/pulse interpolation)
        base_fill = lerp_color(
            QColor(PALETTE["bg_keycap"]),
            QColor(PALETTE["bg_keycap_hover"]),
            highlight_t,
        )
        painter.setBrush(base_fill)
        painter.drawRoundedRect(body_rect, radius, radius)

        tint_strength = max(active_t, self._pulse_amount * 0.85)
        if tint_strength > 0.02:
            tint = QColor(accent)
            tint.setAlpha(int(46.0 * tint_strength))
            painter.setBrush(tint)
            painter.drawRoundedRect(body_rect, radius, radius)

        # Top specular highlight hairline
        spec_pen = QPen(QColor(PALETTE["specular"]), 1.0)
        painter.setPen(spec_pen)
        painter.drawLine(
            QPointF(body_rect.left() + radius, body_rect.top() + 1.0),
            QPointF(body_rect.right() - radius, body_rect.top() + 1.0),
        )

        # Crisp border (smoothly blended to accent border when active or hovered)
        idle_to_hover = lerp_color(
            QColor(PALETTE["border_keycap"]),
            QColor(PALETTE["border_strong"]),
            hover_t,
        )
        active_mix = max(active_t, self._pulse_amount)
        border_c = lerp_color(idle_to_hover, accent, active_mix)
        border_w = lerp_float(lerp_float(1.0, 1.25, hover_t), 1.6, active_mix)
        painter.setPen(QPen(border_c, border_w))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body_rect, radius, radius)

        # Active Optical LED bar along top center (interpolated width & alpha)
        if active_t > 0.02:
            led_w = min(22.0, body_rect.width() * 0.28) * lerp_float(0.35, 1.0, active_t)
            led_rect = QRectF(body_rect.center().x() - led_w / 2.0, body_rect.top() + 2.0, led_w, 2.5)
            led_color = QColor(accent)
            led_color.setAlpha(int(255.0 * active_t))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(led_color)
            painter.drawRoundedRect(led_rect, 1.2, 1.2)

        x_cursor = body_rect.left() + 8.0
        cy = body_rect.center().y()

        # Left [Key] cap integrated with category accent color
        if self.key_label:
            kf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.Bold)
            painter.setFont(kf)
            kw = max(22.0, float(QFontMetrics(kf).horizontalAdvance(self.key_label) + 12))
            krect = QRectF(x_cursor, cy - 10.5, kw, 21.0)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(accent)
            painter.drawRoundedRect(krect, 5.0, 5.0)
            painter.setPen(QColor(_contrast_ink(accent.name())))
            painter.drawText(krect, Qt.AlignmentFlag.AlignCenter, self.key_label)
            x_cursor += kw + 8.0

        # Right count pill first to compute available text width cleanly
        cf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.DemiBold)
        painter.setFont(cf)
        cnt_str = str(self.count)
        cw = max(20.0, float(QFontMetrics(cf).horizontalAdvance(cnt_str) + 10))
        base_crect = QRectF(body_rect.right() - cw - 7.0, cy - 9.5, cw, 19.0)
        bump = self._count_bump_t
        if bump > 0.01:
            scale = 1.0 + 0.14 * bump
            bw = cw * scale
            bh = 19.0 * scale
            crect = QRectF(
                base_crect.center().x() - bw / 2.0,
                base_crect.center().y() - bh / 2.0,
                bw,
                bh,
            )
            pill_bg = lerp_color(QColor(PALETTE["bg_input"]), accent, 0.28 * bump)
            pill_fg = lerp_color(QColor(PALETTE["text_secondary"]), QColor(PALETTE["text_primary"]), bump)
        else:
            crect = base_crect
            pill_bg = QColor(PALETTE["bg_input"])
            pill_fg = QColor(PALETTE["text_secondary"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pill_bg)
        painter.drawRoundedRect(crect, 5.0, 5.0)
        painter.setPen(pill_fg)
        painter.drawText(crect, Qt.AlignmentFlag.AlignCenter, cnt_str)

        # Category text (middle, safely elided)
        lf = QFont(UI_FONT_FAMILY, 10, QFont.Weight.DemiBold)
        painter.setFont(lf)
        avail_w = max(24.0, base_crect.left() - x_cursor - 6.0)
        elided = QFontMetrics(lf).elidedText(
            self.category, Qt.TextElideMode.ElideRight, int(avail_w)
        )
        painter.setPen(QColor(PALETTE["text_primary"]))
        painter.drawText(
            QRectF(x_cursor, body_rect.top(), avail_w, body_rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )
        painter.end()


class TrashKeycapButton(QPushButton):
    """Pinned left-most 3D Trash console keycap button."""

    def __init__(
        self,
        category: str = "_Buang",
        key_label: str = "DEL",
        count: int = 0,
        accent_color: str = DEFAULT_TRASH_COLOR,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.category = category
        self.key_label = (key_label or "DEL").upper()
        self.count = int(count)
        self.accent_color = accent_color or DEFAULT_TRASH_COLOR
        self._is_active = False
        self._pulse_amount = 0.0
        self._hover_t = 0.0
        self._press_t = 0.0
        self._active_t = 0.0
        self._count_bump_t = 0.0
        self._pulse_anim = ValueAnimator(0.0, parent=self)
        self._hover_anim = ValueAnimator(0.0, parent=self)
        self._press_anim = ValueAnimator(0.0, parent=self)
        self._active_anim = ValueAnimator(0.0, parent=self)
        self._count_bump_anim = ValueAnimator(0.0, parent=self)

        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(38)
        self._recalculate_size()
        self._refresh_tooltip()

    def set_trash_spec(self, name: str, key_label: str, color: str) -> None:
        self.category = name or "_Buang"
        self.key_label = (key_label or "DEL").upper()
        self.accent_color = color or DEFAULT_TRASH_COLOR
        self._recalculate_size()
        self._refresh_tooltip()
        self.update()

    def set_count(self, count: int) -> None:
        new_count = max(0, int(count))
        changed = new_count != self.count
        self.count = new_count
        self._recalculate_size()
        if changed and self.isVisible() and not prefers_reduced_motion():
            self._count_bump_anim.animate_to(
                0.0,
                duration_ms=155,
                curve=ease_out(),
                start_from=1.0,
                on_value=self._set_count_bump,
                non_vestibular=True,
            )
        self.update()

    def set_active(self, active: bool) -> None:
        flag = bool(active)
        if self._is_active != flag:
            self._is_active = flag
            self._active_anim.animate_to(
                1.0 if flag else 0.0,
                duration_ms=160,
                curve=ease_out(),
                on_value=self._set_active_t,
                non_vestibular=True,
            )

    def trigger_pulse(self, duration_ms: int = 140) -> None:
        if prefers_reduced_motion():
            return
        down_ms = max(65, int(duration_ms * 0.6))
        up_ms = max(95, int(duration_ms))
        self._pulse_anim.animate_to(
            1.0,
            duration_ms=down_ms,
            curve=ease_out(),
            on_value=self._set_pulse,
            on_done=lambda: self._pulse_anim.animate_to(
                0.0, duration_ms=up_ms, curve=ease_out(), on_value=self._set_pulse
            ),
        )

    def _set_pulse(self, val: float) -> None:
        self._pulse_amount = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_hover_t(self, val: float) -> None:
        self._hover_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_press_t(self, val: float) -> None:
        self._press_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_active_t(self, val: float) -> None:
        self._active_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_count_bump(self, val: float) -> None:
        self._count_bump_t = max(0.0, min(1.0, float(val)))
        self.update()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        super().enterEvent(event)
        self._hover_anim.animate_to(
            1.0,
            duration_ms=115,
            curve=ease_out(),
            on_value=self._set_hover_t,
            non_vestibular=True,
        )

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self._hover_anim.animate_to(
            0.0,
            duration_ms=155,
            curve=ease_out(),
            on_value=self._set_hover_t,
            non_vestibular=True,
        )
        if self._press_t > 0.0:
            self._press_anim.animate_to(
                0.0, duration_ms=120, curve=ease_out(), on_value=self._set_press_t
            )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_anim.animate_to(
                1.0, duration_ms=75, curve=ease_out(), on_value=self._set_press_t
            )
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._press_t > 0.0 or self.isDown():
            self._press_anim.animate_to(
                0.0, duration_ms=135, curve=ease_out(), on_value=self._set_press_t
            )
        super().mouseReleaseEvent(event)

    def _refresh_tooltip(self) -> None:
        self.setToolTip(
            tr("sorting.trash_tip", trash=self.category, key=self.key_label)
        )

    def _recalculate_size(self) -> None:
        kf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.Bold)
        kw = max(28, QFontMetrics(kf).horizontalAdvance(self.key_label) + 12)
        lf = QFont(UI_FONT_FAMILY, 10, QFont.Weight.DemiBold)
        lw = QFontMetrics(lf).horizontalAdvance(self.category)
        cf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.Medium)
        cw = max(20, QFontMetrics(cf).horizontalAdvance(str(self.count)) + 10)
        total_w = max(112, 8 + kw + 7 + 15 + 5 + lw + 8 + cw + 9)
        self.setFixedWidth(min(260, total_w))

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 38)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        press_t = max(
            self._press_t,
            1.0 if self.isDown() else 0.0,
            self._pulse_amount * 0.85,
        )
        hover_t = max(self._hover_t, 1.0 if self.underMouse() else 0.0)
        active_t = max(self._active_t, 1.0 if (self._is_active and self._active_t == 0.0 and prefers_reduced_motion()) else 0.0)
        highlight_t = max(hover_t, active_t, self._pulse_amount)

        sunk = lerp_float(0.0, 1.5, press_t)
        lip_h = lerp_float(2.5, 1.0, press_t)
        body_rect = QRectF(1.0, 1.0 + sunk, self.width() - 2.0, self.height() - 2.0 - lip_h - sunk)
        lip_rect = QRectF(1.0, body_rect.top() + 2.0, self.width() - 2.0, self.height() - 3.0 - sunk)
        radius = 8.0

        danger = QColor(self.accent_color or PALETTE["danger"])
        if not danger.isValid():
            danger = QColor(PALETTE["danger"])

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(PALETTE["bg_keycap_lip"]))
        painter.drawRoundedRect(lip_rect, radius, radius)

        surf_bg = lerp_color(
            QColor(PALETTE["bg_keycap"]),
            QColor(PALETTE["bg_keycap_hover"]),
            highlight_t,
        )
        painter.setBrush(surf_bg)
        painter.drawRoundedRect(body_rect, radius, radius)

        tint_alpha = int(lerp_float(20.0, 48.0, highlight_t))
        tint = QColor(danger)
        tint.setAlpha(tint_alpha)
        painter.setBrush(tint)
        painter.drawRoundedRect(body_rect, radius, radius)

        border_mix = max(active_t, hover_t * 0.85, self._pulse_amount)
        border_c = lerp_color(QColor(PALETTE["border_trash"]), danger, border_mix)
        border_w = lerp_float(1.1, 1.55, max(active_t, self._pulse_amount))
        painter.setPen(QPen(border_c, border_w))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(body_rect, radius, radius)

        if active_t > 0.02:
            led_w = min(22.0, body_rect.width() * 0.28) * lerp_float(0.35, 1.0, active_t)
            led_color = QColor(danger)
            led_color.setAlpha(int(255.0 * active_t))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(led_color)
            painter.drawRoundedRect(
                QRectF(body_rect.center().x() - led_w / 2.0, body_rect.top() + 2.0, led_w, 2.5),
                1.2,
                1.2,
            )

        x = body_rect.left() + 8.0
        cy = body_rect.center().y()

        kf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.Bold)
        painter.setFont(kf)
        kw = max(28.0, float(QFontMetrics(kf).horizontalAdvance(self.key_label) + 12))
        krect = QRectF(x, cy - 10.5, kw, 21.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(danger)
        painter.drawRoundedRect(krect, 5.0, 5.0)
        painter.setPen(QColor(_contrast_ink(danger.name())))
        painter.drawText(krect, Qt.AlignmentFlag.AlignCenter, self.key_label)
        x += kw + 7.0

        draw_icon(painter, "trash", QRectF(x, cy - 7.0, 14.0, 14.0), danger, stroke_width=1.6)
        x += 18.0

        cf = QFont(MONO_FONT_FAMILY, 9, QFont.Weight.DemiBold)
        painter.setFont(cf)
        cnt_str = str(self.count)
        cw = max(20.0, float(QFontMetrics(cf).horizontalAdvance(cnt_str) + 10))
        base_crect = QRectF(body_rect.right() - cw - 7.0, cy - 9.5, cw, 19.0)
        bump = self._count_bump_t
        if bump > 0.01:
            scale = 1.0 + 0.14 * bump
            bw = cw * scale
            bh = 19.0 * scale
            crect = QRectF(
                base_crect.center().x() - bw / 2.0,
                base_crect.center().y() - bh / 2.0,
                bw,
                bh,
            )
            pill_bg = lerp_color(QColor(PALETTE["bg_input"]), danger, 0.28 * bump)
            pill_fg = lerp_color(QColor(PALETTE["text_secondary"]), QColor(PALETTE["text_primary"]), bump)
        else:
            crect = base_crect
            pill_bg = QColor(PALETTE["bg_input"])
            pill_fg = QColor(PALETTE["text_secondary"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(pill_bg)
        painter.drawRoundedRect(crect, 5.0, 5.0)
        painter.setPen(pill_fg)
        painter.drawText(crect, Qt.AlignmentFlag.AlignCenter, cnt_str)

        lf = QFont(UI_FONT_FAMILY, 10, QFont.Weight.DemiBold)
        painter.setFont(lf)
        avail_w = max(24.0, base_crect.left() - x - 6.0)
        elided = QFontMetrics(lf).elidedText(
            self.category, Qt.TextElideMode.ElideRight, int(avail_w)
        )
        painter.setPen(QColor(PALETTE["text_primary"]))
        painter.drawText(
            QRectF(x, body_rect.top(), avail_w, body_rect.height()),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            elided,
        )
        painter.end()


class _DashedActionButton(QPushButton):
    """Custom-painted dashed '+ New Category' button with ZERO QSS dashed corner artifacts."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(38)
        self._hover_t = 0.0
        self._press_t = 0.0
        self._hover_anim = ValueAnimator(0.0, parent=self)
        self._press_anim = ValueAnimator(0.0, parent=self)
        self.refresh_texts()

    def _set_hover_t(self, val: float) -> None:
        self._hover_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_press_t(self, val: float) -> None:
        self._press_t = max(0.0, min(1.0, float(val)))
        self.update()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        super().enterEvent(event)
        self._hover_anim.animate_to(
            1.0, duration_ms=115, curve=ease_out(), on_value=self._set_hover_t, non_vestibular=True
        )

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self._hover_anim.animate_to(
            0.0, duration_ms=155, curve=ease_out(), on_value=self._set_hover_t, non_vestibular=True
        )
        if self._press_t > 0.0:
            self._press_anim.animate_to(0.0, duration_ms=120, curve=ease_out(), on_value=self._set_press_t)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._press_anim.animate_to(1.0, duration_ms=75, curve=ease_out(), on_value=self._set_press_t)
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if self._press_t > 0.0 or self.isDown():
            self._press_anim.animate_to(0.0, duration_ms=135, curve=ease_out(), on_value=self._set_press_t)
        super().mouseReleaseEvent(event)

    def refresh_texts(self) -> None:
        self._label = tr("sorting.new_category")
        f = QFont(UI_FONT_FAMILY, 10, QFont.Weight.Medium)
        w = max(112, QFontMetrics(f).horizontalAdvance(self._label) + 42)
        self.setFixedWidth(w)
        self.update()

    def sizeHint(self) -> QSize:
        return QSize(self.width(), 38)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        sunk = 1.0 * max(self._press_t, 1.0 if self.isDown() else 0.0)
        rect = QRectF(1.5, 1.5 + sunk, self.width() - 3.0, self.height() - 3.0 - sunk)
        t = max(self._hover_t, 1.0 if self.underMouse() else 0.0, self._press_t)
        stroke = lerp_color(QColor(PALETTE["border_strong"]), QColor(PALETTE["accent"]), t)
        fill = _hex_with_alpha(PALETTE["accent"], int(24.0 * t)) if t > 0.02 else None
        _draw_dashed_rounded_rect(painter, rect, 8.0, stroke, fill_color=fill, width=1.25)

        fg = lerp_color(QColor(PALETTE["text_secondary"]), QColor(PALETTE["accent"]), t)
        f = QFont(UI_FONT_FAMILY, 10, QFont.Weight.Medium)
        painter.setFont(f)
        text_w = QFontMetrics(f).horizontalAdvance(self._label)
        content_w = 14.0 + 6.0 + text_w
        start_x = (self.width() - content_w) / 2.0
        cy = self.height() / 2.0 + sunk * 0.5
        draw_icon(painter, "plus", QRectF(start_x, cy - 7.0, 14.0, 14.0), fg, stroke_width=1.6)
        painter.setPen(fg)
        painter.drawText(
            QRectF(start_x + 20.0, sunk * 0.5, text_w + 4.0, float(self.height()) - sunk * 0.5),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._label,
        )
        painter.end()


# ---------------------------------------------------------------------------
# Header Widgets: CounterPill + Viewfinder Metadata Capsule & Hover Card
# ---------------------------------------------------------------------------


class CounterPill(QWidget):
    """Clickable '20 / 150' frame counter turning into inline jump QLineEdit on click."""

    jump_requested = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current = 1
        self._total = 0

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(0)

        self.pill_btn = QPushButton("0 / 0", self)
        self.pill_btn.setObjectName("CounterPillBtn")
        self.pill_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.pill_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.pill_btn.setFixedHeight(28)
        self.pill_btn.setToolTip(tr("sorting.counter_tip"))
        self.pill_btn.clicked.connect(self._enter_edit_mode)
        row.addWidget(self.pill_btn)

        self.jump_input = QLineEdit(self)
        self.jump_input.setFixedSize(64, 28)
        self.jump_input.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.jump_input.returnPressed.connect(self._submit_jump)
        self.jump_input.installEventFilter(self)
        self.jump_input.hide()
        row.addWidget(self.jump_input)

    def set_progress(self, current: int, total: int) -> None:
        self._total = max(0, int(total))
        self._current = max(0, min(self._total, int(current)))
        self.pill_btn.setText(f"{self._current} / {self._total}")
        self.pill_btn.setEnabled(self._total > 0)
        if not self.jump_input.isVisible():
            self.pill_btn.show()

    def is_editing(self) -> bool:
        return self.jump_input.isVisible()

    def refresh_texts(self) -> None:
        self.pill_btn.setToolTip(tr("sorting.counter_tip"))

    def _enter_edit_mode(self) -> None:
        if self._total <= 0:
            return
        self.jump_input.setValidator(QIntValidator(1, self._total, self.jump_input))
        self.jump_input.setText(str(max(1, self._current)))
        self.pill_btn.hide()
        self.jump_input.show()
        self.jump_input.setFocus(Qt.FocusReason.MouseFocusReason)
        self.jump_input.selectAll()

    def _exit_edit_mode(self) -> None:
        self.jump_input.hide()
        self.pill_btn.show()

    def _submit_jump(self) -> None:
        txt = self.jump_input.text().strip()
        self._exit_edit_mode()
        if not txt or self._total <= 0:
            return
        try:
            num = int(txt)
        except ValueError:
            return
        clamped = max(1, min(self._total, num))
        self.jump_requested.emit(clamped - 1)

    def eventFilter(self, watched, event) -> bool:  # type: ignore[override]
        if watched is self.jump_input:
            if event.type() == QEvent.Type.KeyPress and event.key() == Qt.Key.Key_Escape:
                self._exit_edit_mode()
                return True
            if event.type() == QEvent.Type.FocusOut:
                self._exit_edit_mode()
        return super().eventFilter(watched, event)


class _MetadataHeaderChip(QPushButton):
    """Compact viewfinder telemetry capsule in SortingHeader with hover & click signals."""

    hover_entered = pyqtSignal()
    hover_left = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetadataCapsuleBtn")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setFixedHeight(28)
        self._raw_summary = tr("meta.capsule_empty")
        self.setMaximumWidth(310)
        self.setIcon(render_icon("camera", 13, PALETTE["accent"]))
        self.setIconSize(QSize(13, 13))
        self.setToolTip(tr("meta.capsule_tip"))
        self.setText(self._raw_summary)

    def set_summary(self, parts: Sequence[str]) -> None:
        clean = [p for p in parts if p]
        self._raw_summary = " · ".join(clean) if clean else tr("meta.capsule_empty")
        fm = QFontMetrics(self.font())
        elided = fm.elidedText(self._raw_summary, Qt.TextElideMode.ElideRight, 275)
        self.setText(_escape_mnemonic(elided))

    def refresh_theme(self) -> None:
        self.setIcon(render_icon("camera", 13, PALETTE["accent"]))

    def refresh_texts(self) -> None:
        self.setToolTip(tr("meta.capsule_tip"))

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self.hover_entered.emit()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self.hover_left.emit()
        super().leaveEvent(event)


class _MetadataHoverLayer(QFrame):
    """Floating quick EXIF readout card anchored neatly below _MetadataHeaderChip."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MetadataHoverCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(320)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)

        self.title_lbl = QLabel(self)
        self.title_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.title_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {PALETTE['text_primary']};"
        )
        layout.addWidget(self.title_lbl)

        self.grid_host = QWidget(self)
        self.grid = QGridLayout(self.grid_host)
        self.grid.setContentsMargins(0, 2, 0, 0)
        self.grid.setHorizontalSpacing(14)
        self.grid.setVerticalSpacing(4)
        self.grid.setColumnStretch(1, 1)
        layout.addWidget(self.grid_host)
        self._open_anim = ValueAnimator(0.0, parent=self)
        self.hide()

    def show_animated(self, target_pos: QPoint) -> None:
        self._open_anim.stop_silent(0.0)
        if prefers_reduced_motion():
            self.setGraphicsEffect(None)
            self.move(target_pos)
            self.show()
            self.raise_()
            return
        eff = self.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(eff)
        eff.setOpacity(0.0)
        start_y = target_pos.y() - 6
        end_y = target_pos.y()
        self.move(target_pos.x(), start_y)
        self.show()
        self.raise_()

        def _tick(val: float) -> None:
            if not self.isVisible():
                return
            t = max(0.0, min(1.0, float(val)))
            cur_eff = self.graphicsEffect()
            if isinstance(cur_eff, QGraphicsOpacityEffect):
                cur_eff.setOpacity(t)
            self.move(target_pos.x(), int(round(lerp_float(float(start_y), float(end_y), t))))

        def _done() -> None:
            self.setGraphicsEffect(None)

        self._open_anim.animate_to(
            1.0,
            duration_ms=130,
            curve=ease_out(),
            start_from=0.0,
            on_value=_tick,
            on_done=_done,
            non_vestibular=True,
        )

    def hide_immediate(self) -> None:
        self._open_anim.stop_silent(0.0)
        self.setGraphicsEffect(None)
        self.hide()

    def set_metadata(self, meta: Dict[str, object]) -> None:
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.title_lbl.setText(str(meta.get("name") or tr("meta.overlay_title")))
        self.title_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {PALETTE['text_primary']};"
        )

        quick_order = (
            "camera",
            "lens",
            "date_taken",
            "focal_length",
            "shutter_speed",
            "aperture",
            "iso",
            "dimensions",
            "size",
            "format",
            "video_codec",
            "fps",
            "duration",
        )
        row_idx = 0
        for key in quick_order:
            val = str(meta.get(key) or "").strip()
            if not val:
                continue
            k_lbl = QLabel(tr(f"meta.field.{key}"), self.grid_host)
            k_lbl.setStyleSheet(
                f"font-size: 11px; color: {PALETTE['text_muted']}; font-weight: 500;"
            )
            v_lbl = QLabel(val, self.grid_host)
            v_lbl.setTextFormat(Qt.TextFormat.PlainText)
            v_lbl.setStyleSheet(
                f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; "
                f"color: {PALETTE['text_primary']};"
            )
            v_lbl.setWordWrap(True)
            self.grid.addWidget(k_lbl, row_idx, 0, Qt.AlignmentFlag.AlignTop)
            self.grid.addWidget(v_lbl, row_idx, 1, Qt.AlignmentFlag.AlignTop)
            row_idx += 1

        if row_idx == 0:
            none_lbl = QLabel(tr("meta.none"), self.grid_host)
            none_lbl.setStyleSheet(f"font-size: 11px; color: {PALETTE['text_muted']};")
            self.grid.addWidget(none_lbl, 0, 0, 1, 2)

        self.adjustSize()


# ---------------------------------------------------------------------------
# In-Window Overlay Framework & Modals
# ---------------------------------------------------------------------------


class _InWindowOverlayBase(QFrame):
    """Modal overlay rendered directly inside MainWindow (never as a separate OS dialog)."""

    closed = pyqtSignal()

    def __init__(self, parent: QWidget, card_width: int = 460, card_max_height: int = 580) -> None:
        super().__init__(parent)
        self.setObjectName("InWindowOverlayBackdrop")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._card_width = card_width
        self._card_max_height = card_max_height
        self._backdrop_closes = True
        self._open_t = 0.0
        self._open_anim = ValueAnimator(0.0, parent=self)

        self._outer_layout = QVBoxLayout(self)
        self._outer_layout.setContentsMargins(24, 24, 24, 24)
        self._outer_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.card = QFrame(self)
        self.card.setObjectName("InWindowOverlayCard")
        self.card.setFixedWidth(min(card_width, max(320, parent.width() - 48)))
        self.card.setMaximumHeight(min(card_max_height, max(240, parent.height() - 48)))
        self._outer_layout.addWidget(self.card, 0, Qt.AlignmentFlag.AlignCenter)

        self.card_layout = QVBoxLayout(self.card)
        self.card_layout.setContentsMargins(22, 20, 22, 20)
        self.card_layout.setSpacing(14)

    def sync_bounds(self) -> None:
        p = self.parentWidget()
        if p is not None:
            self.setGeometry(p.rect())
            self.card.setFixedWidth(min(self._card_width, max(320, p.width() - 48)))
            self.card.setMaximumHeight(min(self._card_max_height, max(240, p.height() - 48)))

    def _apply_open_t(self, val: float) -> None:
        t = max(0.0, min(1.0, float(val)))
        self._open_t = t
        dy = int(round(8.0 * (1.0 - t)))
        self._outer_layout.setContentsMargins(24, 24 + dy, 24, max(16, 24 - dy))
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(6, 7, 10, int(round(176.0 * self._open_t))))
        painter.end()

    def open_overlay(self) -> None:
        self.sync_bounds()
        self.show()
        self.raise_()
        self.setFocus(Qt.FocusReason.PopupFocusReason)
        if prefers_reduced_motion():
            self._apply_open_t(1.0)
            return
        self._open_anim.animate_to(
            1.0,
            duration_ms=165,
            curve=ease_out(),
            start_from=0.0,
            on_value=self._apply_open_t,
        )

    def close_overlay(self) -> None:
        self._open_anim.stop_silent(0.0)
        self._apply_open_t(0.0)
        self.hide()
        self.closed.emit()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self._backdrop_closes and not self.card.geometry().contains(event.pos()):
            self.close_overlay()
            return
        super().mousePressEvent(event)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.close_overlay()
            event.accept()
            return
        super().keyPressEvent(event)


class FullMetadataOverlay(_InWindowOverlayBase):
    """In-window comprehensive metadata inspector with Copy All button."""

    def __init__(self, metadata: Dict[str, object], parent: QWidget) -> None:
        super().__init__(parent, card_width=520, card_max_height=600)
        self._metadata = dict(metadata)

        hdr = QHBoxLayout()
        icon_lbl = QLabel(self.card)
        icon_lbl.setPixmap(render_icon("camera", 18, PALETTE["accent"]).pixmap(18, 18))
        hdr.addWidget(icon_lbl)
        self.title_lbl = QLabel(str(metadata.get("name") or tr("meta.overlay_title")), self.card)
        self.title_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.title_lbl.setStyleSheet(
            f"font-size: 15px; font-weight: 600; color: {PALETTE['text_primary']};"
        )
        hdr.addWidget(self.title_lbl, 1)
        close_x = QPushButton(self.card)
        close_x.setObjectName("GhostIconButton")
        close_x.setIcon(render_icon("close", 15, PALETTE["text_secondary"]))
        close_x.setFixedSize(28, 28)
        close_x.clicked.connect(self.close_overlay)
        hdr.addWidget(close_x)
        self.card_layout.addLayout(hdr)

        scroll = QScrollArea(self.card)
        scroll.setWidgetResizable(True)
        scroll.setMinimumHeight(260)
        content = QWidget(scroll)
        grid = QGridLayout(content)
        grid.setContentsMargins(2, 2, 6, 2)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(8)
        grid.setColumnStretch(1, 1)

        order = ("name",) + METADATA_FIELD_KEYS
        self._copy_lines: List[str] = []
        row = 0
        for key in order:
            val = str(metadata.get(key) or "").strip()
            if not val:
                continue
            label_key = "meta.field.file" if key == "name" else f"meta.field.{key}"
            label_txt = tr(label_key)
            self._copy_lines.append(f"{label_txt}: {val}")

            k_lbl = QLabel(label_txt, content)
            k_lbl.setStyleSheet(
                f"font-size: 12px; color: {PALETTE['text_secondary']}; font-weight: 500;"
            )
            v_lbl = QLabel(val, content)
            v_lbl.setTextFormat(Qt.TextFormat.PlainText)
            v_lbl.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v_lbl.setWordWrap(True)
            v_lbl.setStyleSheet(
                f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 12px; "
                f"color: {PALETTE['text_primary']};"
            )
            grid.addWidget(k_lbl, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(v_lbl, row, 1, Qt.AlignmentFlag.AlignTop)
            row += 1

        if row == 0:
            empty = QLabel(tr("meta.none"), content)
            empty.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_muted']};")
            grid.addWidget(empty, 0, 0, 1, 2)

        scroll.setWidget(content)
        self.card_layout.addWidget(scroll, 1)

        footer = QHBoxLayout()
        self.copy_btn = QPushButton(tr("meta.copy_all"), self.card)
        self.copy_btn.setIcon(render_icon("copy", 14, PALETTE["text_primary"]))
        self.copy_btn.clicked.connect(self._copy_all)
        footer.addWidget(self.copy_btn)
        footer.addStretch(1)
        close_btn = QPushButton(tr("common.close"), self.card)
        close_btn.setObjectName("PrimaryButton")
        close_btn.clicked.connect(self.close_overlay)
        footer.addWidget(close_btn)
        self.card_layout.addLayout(footer)

    def _copy_all(self) -> None:
        cb = QApplication.clipboard()
        if cb is not None:
            cb.setText("\n".join(self._copy_lines))
        self.copy_btn.setText(tr("meta.copied"))
        self.copy_btn.setIcon(render_icon("check", 14, PALETTE["success"]))
        QTimer.singleShot(
            1400,
            lambda: (
                self.copy_btn.setText(tr("meta.copy_all")),
                self.copy_btn.setIcon(render_icon("copy", 14, PALETTE["text_primary"])),
            ),
        )


class KeyCaptureOverlay(_InWindowOverlayBase):
    """In-window keybind capture dialog validating against reserved & existing bindings."""

    key_captured = pyqtSignal(str)

    def __init__(
        self,
        target_label: str,
        existing_map: Optional[Dict[str, str]] = None,
        ignore_category: Optional[str] = None,
        allow_del: bool = False,
        parent: Optional[QWidget] = None,
    ) -> None:
        assert parent is not None
        super().__init__(parent, card_width=430, card_max_height=340)
        self._existing_map = dict(existing_map or {})
        self._ignore_category = ignore_category
        self._allow_del = allow_del
        self._captured_key: Optional[str] = None

        title = QLabel(tr("keycap.title"), self.card)
        title.setStyleSheet(
            f"font-size: 15px; font-weight: 600; color: {PALETTE['text_primary']};"
        )
        self.card_layout.addWidget(title)

        prompt = QLabel(tr("keycap.prompt", name=target_label), self.card)
        prompt.setWordWrap(True)
        prompt.setTextFormat(Qt.TextFormat.PlainText)
        prompt.setStyleSheet(f"font-size: 13px; color: {PALETTE['text_secondary']};")
        self.card_layout.addWidget(prompt)

        self.preview_box = QLabel("...", self.card)
        self.preview_box.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_box.setFixedHeight(54)
        self.preview_box.setStyleSheet(
            f"background-color: {PALETTE['bg_input']}; "
            f"border: 1.5px solid {PALETTE['accent']}; border-radius: 10px; "
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 18px; "
            f"font-weight: 700; color: {PALETTE['accent']};"
        )
        self.card_layout.addWidget(self.preview_box)

        self.error_lbl = QLabel("", self.card)
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 500; color: {PALETTE['danger']};"
        )
        self.error_lbl.hide()
        self.card_layout.addWidget(self.error_lbl)

        hint = QLabel(tr("keycap.hint"), self.card)
        hint.setWordWrap(True)
        hint.setStyleSheet(f"font-size: 11px; color: {PALETTE['text_muted']};")
        self.card_layout.addWidget(hint)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton(tr("common.cancel"), self.card)
        cancel_btn.clicked.connect(self.close_overlay)
        footer.addWidget(cancel_btn)
        self.card_layout.addLayout(footer)

    def captured_key(self) -> Optional[str]:
        return self._captured_key

    def accept_token(self, token: str) -> bool:
        """Validate token; emit key_captured and close on success."""
        try:
            clean = validate_keybind(
                token,
                existing_map=self._existing_map,
                ignore_category=self._ignore_category,
                allow_del=self._allow_del,
            )
        except ValueError as exc:
            self.error_lbl.setText(str(exc))
            self.error_lbl.show()
            return False
        self._captured_key = clean
        self.preview_box.setText(clean)
        self.error_lbl.hide()
        self.key_captured.emit(clean)
        self.close_overlay()
        return True

    def keyPressEvent(self, event: QKeyEvent) -> None:  # type: ignore[override]
        if event.isAutoRepeat():
            event.accept()
            return
        if event.key() == Qt.Key.Key_Escape:
            self.close_overlay()
            event.accept()
            return
        token = _format_qt_key_token(event)
        if token is None:
            event.accept()
            return
        self.accept_token(token)
        event.accept()


class _TextPromptOverlay(_InWindowOverlayBase):
    """In-window category creation / rename modal replacing detached QDialog."""

    submitted = pyqtSignal(str)

    def __init__(
        self,
        title: str,
        label: str,
        initial_text: str = "",
        placeholder: str = "",
        validator_fn: Optional[Callable[[str], str]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        assert parent is not None
        super().__init__(parent, card_width=420, card_max_height=290)
        self._validator_fn = validator_fn

        t_lbl = QLabel(title, self.card)
        t_lbl.setTextFormat(Qt.TextFormat.PlainText)
        t_lbl.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {PALETTE['text_primary']};")
        self.card_layout.addWidget(t_lbl)

        desc = QLabel(label, self.card)
        desc.setWordWrap(True)
        desc.setTextFormat(Qt.TextFormat.PlainText)
        desc.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        self.card_layout.addWidget(desc)

        self.input_edit = QLineEdit(initial_text, self.card)
        self.input_edit.setPlaceholderText(placeholder)
        self.input_edit.returnPressed.connect(self._on_submit)
        self.card_layout.addWidget(self.input_edit)

        self.error_lbl = QLabel("", self.card)
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet(f"font-size: 11px; color: {PALETTE['danger']};")
        self.error_lbl.hide()
        self.card_layout.addWidget(self.error_lbl)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton(tr("common.cancel"), self.card)
        cancel_btn.clicked.connect(self.close_overlay)
        footer.addWidget(cancel_btn)
        self.save_btn = QPushButton(tr("common.save"), self.card)
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self._on_submit)
        footer.addWidget(self.save_btn)
        self.card_layout.addLayout(footer)

    def open_overlay(self) -> None:
        super().open_overlay()
        self.input_edit.setFocus(Qt.FocusReason.PopupFocusReason)
        self.input_edit.selectAll()

    def _on_submit(self) -> None:
        raw = self.input_edit.text().strip()
        try:
            final_val = self._validator_fn(raw) if self._validator_fn else raw
        except ValueError as exc:
            self.error_lbl.setText(str(exc))
            self.error_lbl.show()
            return
        self.submitted.emit(final_val)
        self.close_overlay()


class _ColorPickerOverlay(_InWindowOverlayBase):
    """In-window preset swatch + custom color selector."""

    color_selected = pyqtSignal(str)

    def __init__(
        self,
        title: str,
        current_color: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        assert parent is not None
        super().__init__(parent, card_width=400, card_max_height=340)
        self._selected = current_color or DEFAULT_CATEGORY_COLOR

        t_lbl = QLabel(title, self.card)
        t_lbl.setTextFormat(Qt.TextFormat.PlainText)
        t_lbl.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {PALETTE['text_primary']};")
        self.card_layout.addWidget(t_lbl)

        prompt = QLabel(tr("color.prompt"), self.card)
        prompt.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        self.card_layout.addWidget(prompt)

        grid = QGridLayout()
        grid.setSpacing(10)
        for idx, (cname, chex) in enumerate(CATEGORY_COLOR_PRESETS):
            btn = QPushButton(self.card)
            btn.setFixedSize(40, 40)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setToolTip(f"{cname} ({chex})")
            active_ring = chex.upper() == self._selected.upper()
            border_css = f"3px solid {PALETTE['text_primary']}" if active_ring else f"1px solid {PALETTE['border']}"
            btn.setStyleSheet(
                f"QPushButton {{ background-color: {chex}; border: {border_css}; border-radius: 10px; }}"
            )
            btn.clicked.connect(lambda _=False, hx=chex: self._choose(hx))
            grid.addWidget(btn, idx // 4, idx % 4)
        self.card_layout.addLayout(grid)

        custom_row = QHBoxLayout()
        self.active_lbl = QLabel(tr("color.active", hex=self._selected.upper()), self.card)
        self.active_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; color: {PALETTE['text_secondary']};"
        )
        custom_row.addWidget(self.active_lbl, 1)
        custom_btn = QPushButton(tr("color.custom"), self.card)
        custom_btn.clicked.connect(self._pick_custom)
        custom_row.addWidget(custom_btn)
        self.card_layout.addLayout(custom_row)

        footer = QHBoxLayout()
        footer.addStretch(1)
        cancel_btn = QPushButton(tr("common.cancel"), self.card)
        cancel_btn.clicked.connect(self.close_overlay)
        footer.addWidget(cancel_btn)
        self.card_layout.addLayout(footer)

    def _choose(self, hex_code: str) -> None:
        self._selected = hex_code.upper()
        self.color_selected.emit(self._selected)
        self.close_overlay()

    def _pick_custom(self) -> None:
        chosen = QColorDialog.getColor(QColor(self._selected), self)
        if chosen.isValid():
            self._choose(chosen.name().upper())


class _ConfirmOverlay(_InWindowOverlayBase):
    """In-window clean confirmation overlay."""

    confirmed = pyqtSignal()

    def __init__(
        self,
        title: str,
        body: str,
        danger: bool = True,
        parent: Optional[QWidget] = None,
    ) -> None:
        assert parent is not None
        super().__init__(parent, card_width=430, card_max_height=310)

        t_lbl = QLabel(title, self.card)
        t_lbl.setTextFormat(Qt.TextFormat.PlainText)
        t_lbl.setStyleSheet(f"font-size: 15px; font-weight: 600; color: {PALETTE['text_primary']};")
        self.card_layout.addWidget(t_lbl)

        b_lbl = QLabel(body, self.card)
        b_lbl.setWordWrap(True)
        b_lbl.setTextFormat(Qt.TextFormat.PlainText)
        b_lbl.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        self.card_layout.addWidget(b_lbl)

        footer = QHBoxLayout()
        footer.addStretch(1)
        no_btn = QPushButton(tr("common.cancel"), self.card)
        no_btn.clicked.connect(self.close_overlay)
        footer.addWidget(no_btn)
        yes_btn = QPushButton(tr("common.delete") if danger else tr("common.yes"), self.card)
        yes_btn.setObjectName("DangerButton" if danger else "PrimaryButton")
        yes_btn.clicked.connect(lambda: (self.confirmed.emit(), self.close_overlay()))
        footer.addWidget(yes_btn)
        self.card_layout.addLayout(footer)


# ---------------------------------------------------------------------------
# In-Window 7-Menu SettingsOverlay
# ---------------------------------------------------------------------------


class _ToggleSwitch(QCheckBox):
    """Animated pill toggle switch preserving full QCheckBox API compatibility."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__("", parent)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedSize(40, 22)
        self._thumb_t = 0.0
        self._thumb_anim = ValueAnimator(0.0, parent=self)
        self.toggled.connect(self._on_toggled_animate)

    def _set_thumb_t(self, val: float) -> None:
        self._thumb_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _on_toggled_animate(self, checked: bool) -> None:
        target = 1.0 if checked else 0.0
        if self.isVisible() and not prefers_reduced_motion():
            self._thumb_anim.animate_to(
                target,
                duration_ms=120,
                curve=ease_out(),
                on_value=self._set_thumb_t,
                non_vestibular=True,
            )
        else:
            self._thumb_anim.stop_silent(target)
            self._thumb_t = target
            self.update()

    def sizeHint(self) -> QSize:
        return QSize(40, 22)

    def minimumSizeHint(self) -> QSize:
        return QSize(40, 22)

    def hitButton(self, pos: QPoint) -> bool:
        return self.rect().contains(pos)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        t = self._thumb_t
        if self.isChecked() and t == 0.0 and not self.isVisible():
            t = 1.0
        track = QRectF(1.0, 2.0, 38.0, 18.0)
        bg = lerp_color(QColor(PALETTE["bg_input"]), QColor(PALETTE["accent"]), t)
        border = lerp_color(QColor(PALETTE["border_strong"]), QColor(PALETTE["accent"]), t)
        if not self.isEnabled():
            bg.setAlpha(120)
            border.setAlpha(120)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(track, 9.0, 9.0)

        kx = lerp_float(3.0, 23.0, t)
        knob = QRectF(kx, 4.0, 14.0, 14.0)
        knob_c = QColor("#FFFFFF") if self.isChecked() else QColor(PALETTE["text_secondary"])
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(knob_c)
        painter.drawEllipse(knob)
        painter.end()


class _SettingRow(QWidget):
    """Structured setting row pairing title + optional muted subtitle with right-aligned control."""

    def __init__(
        self,
        title: str,
        subtitle: str = "",
        control: Optional[QWidget] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(16, 11, 16, 11)
        row.setSpacing(14)

        text_col = QVBoxLayout()
        text_col.setContentsMargins(0, 0, 0, 0)
        text_col.setSpacing(2)

        self.title_lbl = QLabel(title, self)
        self.title_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.title_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {PALETTE['text_primary']}; background: transparent;"
        )
        text_col.addWidget(self.title_lbl)

        self.sub_lbl: Optional[QLabel] = None
        if subtitle:
            self.sub_lbl = QLabel(subtitle, self)
            self.sub_lbl.setTextFormat(Qt.TextFormat.PlainText)
            self.sub_lbl.setWordWrap(True)
            self.sub_lbl.setStyleSheet(
                f"font-size: 11px; color: {PALETTE['text_muted']}; background: transparent;"
            )
            text_col.addWidget(self.sub_lbl)

        row.addLayout(text_col, 1)
        if control is not None:
            row.addWidget(control, 0, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)


class _SettingGroupCard(QFrame):
    """Rounded setting container separating stacked rows cleanly with 1px hairlines."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingGroupCard")
        self._col = QVBoxLayout(self)
        self._col.setContentsMargins(0, 0, 0, 0)
        self._col.setSpacing(0)

    def add_row(self, widget: QWidget) -> Optional[QFrame]:
        divider: Optional[QFrame] = None
        if self._col.count() > 0:
            divider = QFrame(self)
            divider.setObjectName("HairlineDivider")
            divider.setFixedHeight(1)
            self._col.addWidget(divider)
        self._col.addWidget(widget)
        return divider


class _SettingsNavPanel(QFrame):
    """Left settings navigation column with an animated sliding active indicator pill."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SettingsNavPanel")
        self._active_btn: Optional[QPushButton] = None
        self._pill_y: float = 0.0
        self._pill_h: float = 0.0
        self._pill_anim = ValueAnimator(0.0, parent=self)

    def _set_pill_y(self, val: float) -> None:
        self._pill_y = float(val)
        self.update()

    def set_active_button(self, btn: Optional[QPushButton], animate: bool = True) -> None:
        self._active_btn = btn
        if btn is None:
            self._pill_anim.stop_silent(0.0)
            self._pill_h = 0.0
            self.update()
            return
        th = float(btn.height())
        ty = float(btn.y())
        if th <= 2.0:
            self._pill_h = 0.0
            return
        if animate and self.isVisible() and self._pill_h > 2.0 and not prefers_reduced_motion():
            self._pill_h = th
            self._pill_anim.animate_to(
                ty,
                duration_ms=155,
                curve=ease_out(),
                start_from=self._pill_y,
                on_value=self._set_pill_y,
                non_vestibular=True,
            )
        else:
            self._pill_anim.stop_silent(ty)
            self._pill_y = ty
            self._pill_h = th
            self.update()

    def _snap_to_active(self) -> None:
        btn = self._active_btn
        if btn is not None and btn.height() > 2:
            self._pill_anim.stop_silent(float(btn.y()))
            self._pill_y = float(btn.y())
            self._pill_h = float(btn.height())
            self.update()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._snap_to_active()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        if self._pill_anim.state() != ValueAnimator.State.Running:
            self._snap_to_active()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        super().paintEvent(event)
        btn = self._active_btn
        if btn is not None and self._pill_h <= 2.0 and btn.height() > 2:
            self._pill_y = float(btn.y())
            self._pill_h = float(btn.height())
        if self._pill_h <= 2.0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        x = float(btn.x()) if btn is not None and btn.width() > 4 else 12.0
        w = float(btn.width()) if btn is not None and btn.width() > 4 else max(24.0, float(self.width()) - 24.0)
        accent = QColor(PALETTE["accent"])
        pill = QRectF(x + 0.5, self._pill_y + 0.5, max(8.0, w - 1.0), max(8.0, self._pill_h - 1.0))
        painter.setPen(QPen(accent, 1.0))
        painter.setBrush(_hex_with_alpha(PALETTE["accent"], 28))
        painter.drawRoundedRect(pill, 8.0, 8.0)

        bar = QRectF(x + 3.0, self._pill_y + 6.0, 3.0, max(6.0, self._pill_h - 12.0))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(accent)
        painter.drawRoundedRect(bar, 1.5, 1.5)
        painter.end()


class SettingsOverlay(_InWindowOverlayBase):
    """7-menu Optical Instrument settings modal rendered in-window."""

    settings_saved = pyqtSignal(AppSettings)
    language_preview_requested = pyqtSignal(str)
    open_key_capture_requested = pyqtSignal(object)  # unused external fallback

    def __init__(self, settings: AppSettings, parent: QWidget) -> None:
        super().__init__(parent, card_width=880, card_max_height=630)
        self._initial_language = settings.language
        self._draft = copy.deepcopy(settings.normalized())
        self._nav_buttons: List[QPushButton] = []
        self._sub_overlay: Optional[_InWindowOverlayBase] = None
        self.card.setMinimumHeight(min(540, max(320, parent.height() - 64)))

        # Override card margins for split left-nav + right stack layout
        self.card_layout.setContentsMargins(0, 0, 0, 0)
        self.card_layout.setSpacing(0)

        shell = QHBoxLayout()
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)

        # Left Navigation Panel (7 menus)
        self.nav_panel = _SettingsNavPanel(self.card)
        self.nav_panel.setFixedWidth(212)
        nav_col = QVBoxLayout(self.nav_panel)
        nav_col.setContentsMargins(12, 18, 12, 18)
        nav_col.setSpacing(4)

        self.nav_title_lbl = QLabel(tr("settings.title"), self.nav_panel)
        self.nav_title_lbl.setStyleSheet(
            f"font-size: 16px; font-weight: 700; color: {PALETTE['text_primary']}; padding: 0 6px 10px 6px;"
        )
        nav_col.addWidget(self.nav_title_lbl)

        self._nav_keys = [
            ("globe", "settings.nav.general"),
            ("tag", "settings.nav.categories"),
            ("keyboard", "settings.nav.keybinds"),
            ("video", "settings.nav.video"),
            ("camera", "settings.nav.metadata"),
            ("palette", "settings.nav.theme"),
            ("info", "settings.nav.about"),
        ]
        for idx, (icon_name, i18n_key) in enumerate(self._nav_keys):
            btn = QPushButton(_escape_mnemonic(tr(i18n_key)), self.nav_panel)
            btn.setObjectName("SettingsNavButton")
            btn.setIcon(render_icon(icon_name, 14, PALETTE["text_secondary"]))
            btn.setIconSize(QSize(14, 14))
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, i=idx: self.select_page(i))
            self._nav_buttons.append(btn)
            nav_col.addWidget(btn)
        nav_col.addStretch(1)
        shell.addWidget(self.nav_panel)

        # Right Page Content + Top Close + Hairline Footer
        right_outer = QVBoxLayout()
        right_outer.setContentsMargins(0, 0, 0, 0)
        right_outer.setSpacing(0)

        content_col = QVBoxLayout()
        content_col.setContentsMargins(24, 14, 24, 14)
        content_col.setSpacing(8)

        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        top_bar.addStretch(1)
        self.close_btn = QPushButton(self.card)
        self.close_btn.setObjectName("GhostIconButton")
        self.close_btn.setFixedSize(28, 28)
        self.close_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.close_btn.setIcon(render_icon("close", 13, PALETTE["text_secondary"]))
        self.close_btn.setToolTip(tr("common.close"))
        self.close_btn.clicked.connect(self._on_cancel)
        top_bar.addWidget(self.close_btn)
        content_col.addLayout(top_bar)

        self.pages = QStackedWidget(self.card)
        self._page_transition = WidgetSlideFadeTransition(self.pages, duration_ms=145)
        content_col.addWidget(self.pages, 1)
        right_outer.addLayout(content_col, 1)

        footer_div = QFrame(self.card)
        footer_div.setObjectName("HairlineDivider")
        footer_div.setFixedHeight(1)
        right_outer.addWidget(footer_div)

        footer = QHBoxLayout()
        footer.setContentsMargins(24, 12, 24, 16)
        footer.setSpacing(10)
        footer.addStretch(1)
        self.cancel_btn = QPushButton(tr("common.cancel"), self.card)
        self.cancel_btn.clicked.connect(self._on_cancel)
        footer.addWidget(self.cancel_btn)
        self.save_btn = QPushButton(tr("settings.save"), self.card)
        self.save_btn.setObjectName("PrimaryButton")
        self.save_btn.clicked.connect(self._on_save)
        footer.addWidget(self.save_btn)
        right_outer.addLayout(footer)

        shell.addLayout(right_outer, 1)
        self.card_layout.addLayout(shell)

        self._build_all_pages()
        self.select_page(0)

    def _build_all_pages(self) -> None:
        while self.pages.count():
            w = self.pages.widget(0)
            self.pages.removeWidget(w)
            w.deleteLater()

        self.pages.addWidget(self._build_general_page())
        self.pages.addWidget(self._build_categories_page())
        self.pages.addWidget(self._build_keybinds_page())
        self.pages.addWidget(self._build_video_page())
        self.pages.addWidget(self._build_metadata_page())
        self.pages.addWidget(self._build_theme_page())
        self.pages.addWidget(self._build_about_page())

    def select_page(self, index: int) -> None:
        if self.pages.count() <= 0:
            return
        idx = max(0, min(self.pages.count() - 1, index))
        cur = self.pages.currentIndex()
        direction = 1 if idx > cur else (-1 if idx < cur else 0)
        if self.isVisible() and cur >= 0 and cur != idx:
            outgoing_w = self.pages.currentWidget()
            incoming_w = self.pages.widget(idx)
            self._page_transition.transition_to(
                lambda: self.pages.setCurrentIndex(idx),
                outgoing_w,
                incoming_w,
                direction=direction or 1,
            )
        else:
            self._page_transition.cancel()
            self.pages.setCurrentIndex(idx)
        for i, btn in enumerate(self._nav_buttons):
            active = i == idx
            btn.setProperty("active", "true" if active else "false")
            icon_name = self._nav_keys[i][0]
            btn.setIcon(
                render_icon(
                    icon_name,
                    14,
                    PALETTE["accent"] if active else PALETTE["text_secondary"],
                )
            )
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        if 0 <= idx < len(self._nav_buttons):
            self.nav_panel.set_active_button(self._nav_buttons[idx], animate=self.isVisible())

    def close_overlay(self) -> None:
        self._page_transition.cancel()
        self.nav_panel._pill_anim.stop_silent(self.nav_panel._pill_y)
        super().close_overlay()

    def _wrap_scroll(self, inner: QWidget) -> QScrollArea:
        sa = QScrollArea(self.pages)
        sa.setWidgetResizable(True)
        sa.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sa.setWidget(inner)
        return sa

    def _make_page_header(self, layout: QVBoxLayout, title_key: str, desc_key: str) -> None:
        h = QLabel(tr(title_key))
        h.setStyleSheet(f"font-size: 16px; font-weight: 700; color: {PALETTE['text_primary']};")
        d = QLabel(tr(desc_key))
        d.setWordWrap(True)
        d.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        layout.addWidget(h)
        layout.addWidget(d)

    # --- Page 0: General & Language ---
    def _build_general_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(14)
        self._make_page_header(col, "settings.general.heading", "settings.general.desc")

        group = _SettingGroupCard(page)
        lang_host = QWidget(group)
        lang_row = QHBoxLayout(lang_host)
        lang_row.setContentsMargins(0, 0, 0, 0)
        lang_row.setSpacing(6)
        self.lang_en_btn = QPushButton("English", lang_host)
        self.lang_id_btn = QPushButton("Bahasa Indonesia", lang_host)
        for btn, code in ((self.lang_en_btn, "en"), (self.lang_id_btn, "id")):
            btn.setObjectName("SegmentedButton")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setProperty("active", "true" if self._draft.language == code else "false")
            btn.clicked.connect(lambda _=False, c=code: self._on_pick_language(c))
            lang_row.addWidget(btn)
        group.add_row(
            _SettingRow(
                tr("settings.general.lang_label"),
                tr("settings.general.lang_sub"),
                control=lang_host,
                parent=group,
            )
        )
        col.addWidget(group)
        col.addStretch(1)
        return self._wrap_scroll(page)

    def _on_pick_language(self, lang: str) -> None:
        self._draft.language = "id" if lang == "id" else "en"
        set_language(self._draft.language)
        self.language_preview_requested.emit(self._draft.language)
        cur_idx = self.pages.currentIndex()
        self._page_transition.cancel()
        self._refresh_settings_texts()
        self._build_all_pages()
        self.select_page(cur_idx)

    def _refresh_settings_texts(self) -> None:
        self.nav_title_lbl.setText(tr("settings.title"))
        for i, (_icon, i18n_key) in enumerate(self._nav_keys):
            self._nav_buttons[i].setText(_escape_mnemonic(tr(i18n_key)))
        self.close_btn.setToolTip(tr("common.close"))
        self.cancel_btn.setText(tr("common.cancel"))
        self.save_btn.setText(tr("settings.save"))

    # --- Page 1: Default Categories (Trash Pinned + Custom Default Categories) ---
    def _build_categories_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(12)
        self._make_page_header(col, "settings.categories.heading", "settings.categories.desc")

        # Pinned Trash Category Card
        trash_card = _SettingGroupCard(page)
        t_row_w = QWidget(trash_card)
        trow = QHBoxLayout(t_row_w)
        trow.setContentsMargins(14, 10, 14, 10)
        trow.setSpacing(10)

        t_key_btn = QPushButton(self._draft.trash_category.key or "DEL", t_row_w)
        t_key_btn.setObjectName("KeyBadgeButton")
        t_key_btn.clicked.connect(self._edit_trash_keybind)
        trow.addWidget(t_key_btn)

        t_color_btn = QPushButton(t_row_w)
        t_color_btn.setFixedSize(22, 22)
        t_color_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        t_color_btn.setStyleSheet(
            f"background-color: {self._draft.trash_category.color}; border-radius: 11px; border: 1px solid {PALETTE['border']};"
        )
        t_color_btn.clicked.connect(self._edit_trash_color)
        trow.addWidget(t_color_btn)

        self.trash_name_edit = QLineEdit(self._draft.trash_category.name, t_row_w)
        self.trash_name_edit.editingFinished.connect(self._commit_trash_name)
        trow.addWidget(self.trash_name_edit, 1)

        t_badge = QLabel(tr("settings.categories.trash_badge"), t_row_w)
        t_badge.setStyleSheet(
            f"font-size: 10px; font-weight: 600; color: {PALETTE['danger']}; "
            f"background-color: {PALETTE['danger_soft']}; padding: 3px 8px; border-radius: 6px;"
        )
        trow.addWidget(t_badge)
        trash_card.add_row(t_row_w)
        col.addWidget(trash_card)

        self.cat_rows_host = _SettingGroupCard(page)
        self.cat_rows_layout = self.cat_rows_host._col
        col.addWidget(self.cat_rows_host)
        self._rebuild_default_category_rows()

        self.cat_err_lbl = QLabel("", page)
        self.cat_err_lbl.setStyleSheet(f"font-size: 11px; color: {PALETTE['danger']};")
        self.cat_err_lbl.hide()
        col.addWidget(self.cat_err_lbl)

        add_btn = QPushButton(_escape_mnemonic(tr("settings.categories.add")), page)
        add_btn.setIcon(render_icon("plus", 13, PALETTE["accent"]))
        add_btn.clicked.connect(self._add_default_category)
        col.addWidget(add_btn, 0, Qt.AlignmentFlag.AlignLeft)
        col.addStretch(1)
        return self._wrap_scroll(page)

    def _rebuild_default_category_rows(self) -> None:
        while self.cat_rows_layout.count():
            item = self.cat_rows_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self.cat_rows_host.setVisible(len(self._draft.default_categories) > 0)
        for idx, spec in enumerate(self._draft.default_categories):
            row_w = QWidget(self.cat_rows_host)
            row = QHBoxLayout(row_w)
            row.setContentsMargins(14, 9, 14, 9)
            row.setSpacing(10)

            k_btn = QPushButton(spec.key or "?", row_w)
            k_btn.setObjectName("KeyBadgeButton")
            k_btn.clicked.connect(lambda _=False, i=idx: self._edit_default_cat_key(i))
            row.addWidget(k_btn)

            c_btn = QPushButton(row_w)
            c_btn.setFixedSize(22, 22)
            c_btn.setCursor(Qt.CursorShape.PointingHandCursor)
            c_btn.setStyleSheet(
                f"background-color: {spec.color}; border-radius: 11px; border: 1px solid {PALETTE['border']};"
            )
            c_btn.clicked.connect(lambda _=False, i=idx: self._edit_default_cat_color(i))
            row.addWidget(c_btn)

            name_edit = QLineEdit(spec.name, row_w)
            name_edit.editingFinished.connect(
                lambda i=idx, le=name_edit: self._rename_default_cat(i, le)
            )
            row.addWidget(name_edit, 1)

            rm_btn = QPushButton(row_w)
            rm_btn.setObjectName("GhostIconButton")
            rm_btn.setIcon(render_icon("close", 12, PALETTE["text_secondary"]))
            rm_btn.setFixedSize(24, 24)
            rm_btn.clicked.connect(lambda _=False, i=idx: self._remove_default_cat(i))
            row.addWidget(rm_btn)

            self.cat_rows_host.add_row(row_w)

    def _commit_trash_name(self) -> None:
        try:
            clean = sanitize_trash_category_name(self.trash_name_edit.text())
            self._draft.trash_category.name = clean
            self.cat_err_lbl.hide()
        except ValueError:
            self.trash_name_edit.setText(self._draft.trash_category.name)

    def _edit_trash_keybind(self) -> None:
        used = {s.name: s.key for s in self._draft.default_categories if s.key}
        ov = KeyCaptureOverlay(
            self._draft.trash_category.name,
            existing_map=used,
            allow_del=True,
            parent=self.parentWidget(),
        )
        ov.key_captured.connect(self._on_trash_key_captured)
        self._open_sub_overlay(ov)

    def _on_trash_key_captured(self, token: str) -> None:
        self._draft.trash_category.key = token
        self._build_all_pages()
        self.select_page(1)

    def _edit_trash_color(self) -> None:
        ov = _ColorPickerOverlay(
            tr("sorting.dialog.color_title", name=self._draft.trash_category.name),
            self._draft.trash_category.color,
            parent=self.parentWidget(),
        )
        ov.color_selected.connect(self._on_trash_color_selected)
        self._open_sub_overlay(ov)

    def _on_trash_color_selected(self, hex_c: str) -> None:
        self._draft.trash_category.color = hex_c
        self._build_all_pages()
        self.select_page(1)

    def _add_default_category(self) -> None:
        ov = _TextPromptOverlay(
            tr("settings.categories.add"),
            tr("sorting.dialog.new_cat_label"),
            placeholder=tr("sorting.dialog.new_cat_placeholder"),
            validator_fn=lambda s: sanitize_category_name(s, allow_trash=False),
            parent=self.parentWidget(),
        )
        ov.submitted.connect(self._on_default_cat_added)
        self._open_sub_overlay(ov)

    def _on_default_cat_added(self, clean_name: str) -> None:
        existing_lower = {s.name.lower() for s in self._draft.default_categories}
        existing_lower.add(self._draft.trash_category.name.lower())
        if clean_name.lower() in existing_lower:
            self.cat_err_lbl.setText(tr("settings.categories.dup_err", name=clean_name))
            self.cat_err_lbl.show()
            return
        used = {s.key for s in self._draft.default_categories if s.key}
        if self._draft.trash_category.key:
            used.add(self._draft.trash_category.key)
        slots = self._draft.active_keybind_slots()
        next_key = next((k for k in slots if k not in used), "")
        color_idx = len(self._draft.default_categories) % len(CATEGORY_COLOR_PRESETS)
        self._draft.default_categories.append(
            DefaultCategorySpec(clean_name, next_key, CATEGORY_COLOR_PRESETS[color_idx][1])
        )
        self.cat_err_lbl.hide()
        self._rebuild_default_category_rows()

    def _rename_default_cat(self, idx: int, edit: QLineEdit) -> None:
        if not (0 <= idx < len(self._draft.default_categories)):
            return
        try:
            clean = sanitize_category_name(edit.text(), allow_trash=False)
        except ValueError:
            edit.setText(self._draft.default_categories[idx].name)
            return
        for i, spec in enumerate(self._draft.default_categories):
            if i != idx and spec.name.lower() == clean.lower():
                self.cat_err_lbl.setText(tr("settings.categories.dup_err", name=clean))
                self.cat_err_lbl.show()
                edit.setText(self._draft.default_categories[idx].name)
                return
        if clean.lower() == self._draft.trash_category.name.lower():
            edit.setText(self._draft.default_categories[idx].name)
            return
        self.cat_err_lbl.hide()
        self._draft.default_categories[idx].name = clean

    def _edit_default_cat_key(self, idx: int) -> None:
        spec = self._draft.default_categories[idx]
        used = {
            s.name: s.key
            for i, s in enumerate(self._draft.default_categories)
            if i != idx and s.key
        }
        if self._draft.trash_category.key:
            used[self._draft.trash_category.name] = self._draft.trash_category.key
        ov = KeyCaptureOverlay(
            spec.name, existing_map=used, allow_del=False, parent=self.parentWidget()
        )
        ov.key_captured.connect(lambda tok, i=idx: self._on_default_cat_key(i, tok))
        self._open_sub_overlay(ov)

    def _on_default_cat_key(self, idx: int, token: str) -> None:
        if 0 <= idx < len(self._draft.default_categories):
            self._draft.default_categories[idx].key = token
            self._rebuild_default_category_rows()

    def _edit_default_cat_color(self, idx: int) -> None:
        spec = self._draft.default_categories[idx]
        ov = _ColorPickerOverlay(
            tr("sorting.dialog.color_title", name=spec.name),
            spec.color,
            parent=self.parentWidget(),
        )
        ov.color_selected.connect(lambda hx, i=idx: self._on_default_cat_color(i, hx))
        self._open_sub_overlay(ov)

    def _on_default_cat_color(self, idx: int, hex_c: str) -> None:
        if 0 <= idx < len(self._draft.default_categories):
            self._draft.default_categories[idx].color = hex_c
            self._rebuild_default_category_rows()

    def _remove_default_cat(self, idx: int) -> None:
        if 0 <= idx < len(self._draft.default_categories):
            self._draft.default_categories.pop(idx)
            self._rebuild_default_category_rows()

    # --- Page 2: Named Custom Keybind Profiles ---
    def _build_keybinds_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(12)
        self._make_page_header(col, "settings.keybinds.heading", "settings.keybinds.desc")

        is_custom_active = self._draft.keybind_profile != "default"
        if not hasattr(self, "_keybind_editor_expanded"):
            self._keybind_editor_expanded = is_custom_active
        show_custom = bool(is_custom_active or self._keybind_editor_expanded)

        top_group = _SettingGroupCard(page)

        mode_host = QWidget(top_group)
        mode_row = QHBoxLayout(mode_host)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self.kb_default_btn = QPushButton(tr("settings.keybinds.default_btn"), mode_host)
        self.kb_default_btn.setObjectName("SegmentedButton")
        self.kb_default_btn.setProperty("active", "true" if not show_custom else "false")
        self.kb_default_btn.clicked.connect(self._activate_default_keybind_profile)
        mode_row.addWidget(self.kb_default_btn)

        self.kb_custom_btn = QPushButton(tr("settings.keybinds.custom_btn"), mode_host)
        self.kb_custom_btn.setObjectName("SegmentedButton")
        self.kb_custom_btn.setProperty("active", "true" if show_custom else "false")
        self.kb_custom_btn.clicked.connect(self._open_custom_keybind_editor)
        mode_row.addWidget(self.kb_custom_btn)

        top_group.add_row(
            _SettingRow(
                tr("settings.keybinds.active_label"),
                tr("settings.keybinds.profile_sub"),
                control=mode_host,
                parent=top_group,
            )
        )

        # Saved custom profiles selector row
        saved_host = QWidget(top_group)
        saved_row = QHBoxLayout(saved_host)
        saved_row.setContentsMargins(0, 0, 0, 0)
        saved_row.setSpacing(8)
        self.profile_combo = QComboBox(saved_host)
        self.profile_combo.setMinimumWidth(145)
        self.profile_combo.addItem("default")
        for pname in sorted(self._draft.custom_keybind_profiles.keys()):
            self.profile_combo.addItem(pname)
        idx = self.profile_combo.findText(self._draft.keybind_profile)
        if idx >= 0:
            self.profile_combo.setCurrentIndex(idx)
        self.profile_combo.currentTextChanged.connect(self._on_profile_selected)
        saved_row.addWidget(self.profile_combo)

        self.del_profile_btn = QPushButton(tr("settings.keybinds.delete_profile"), saved_host)
        self.del_profile_btn.setObjectName("DangerButton")
        self.del_profile_btn.setVisible(self.profile_combo.currentText() != "default")
        self.del_profile_btn.clicked.connect(self._delete_selected_profile)
        saved_row.addWidget(self.del_profile_btn)

        top_group.add_row(
            _SettingRow(
                tr("settings.keybinds.saved_label"),
                tr("settings.keybinds.saved_sub"),
                control=saved_host,
                parent=top_group,
            )
        )
        col.addWidget(top_group)

        # Inline Profile Name + 9-slot Editor Card (hidden when Default selected cleanly)
        self.editor_card = _SettingGroupCard(page)
        editor_inner = QWidget(self.editor_card)
        ecol = QVBoxLayout(editor_inner)
        ecol.setContentsMargins(16, 14, 16, 14)
        ecol.setSpacing(12)

        name_row = QHBoxLayout()
        n_lbl = QLabel(tr("settings.keybinds.profile_name"), editor_inner)
        n_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {PALETTE['text_secondary']};")
        name_row.addWidget(n_lbl)
        initial_name = "" if self._draft.keybind_profile == "default" else self._draft.keybind_profile
        self.profile_name_edit = QLineEdit(initial_name, editor_inner)
        self.profile_name_edit.setPlaceholderText(tr("settings.keybinds.profile_name_ph"))
        name_row.addWidget(self.profile_name_edit, 1)
        self.save_profile_btn = QPushButton(tr("settings.keybinds.save_profile"), editor_inner)
        self.save_profile_btn.setObjectName("PrimaryButton")
        self.save_profile_btn.clicked.connect(self._save_named_profile)
        name_row.addWidget(self.save_profile_btn)
        ecol.addLayout(name_row)

        slots_grid = QGridLayout()
        slots_grid.setSpacing(8)
        self._editing_slots = list(self._draft.active_keybind_slots())
        self.slot_buttons: List[QPushButton] = []
        for s_idx in range(9):
            slot_box = QHBoxLayout()
            sl = QLabel(tr("settings.keybinds.slot_label", num=s_idx + 1), editor_inner)
            sl.setStyleSheet(f"font-size: 11px; color: {PALETTE['text_muted']};")
            sb = QPushButton(self._editing_slots[s_idx], editor_inner)
            sb.setObjectName("KeyBadgeButton")
            sb.setCursor(Qt.CursorShape.PointingHandCursor)
            sb.clicked.connect(lambda _=False, i=s_idx: self._capture_profile_slot(i))
            self.slot_buttons.append(sb)
            slot_box.addWidget(sl)
            slot_box.addWidget(sb, 1)
            slots_grid.addLayout(slot_box, s_idx // 3, s_idx % 3)
        ecol.addLayout(slots_grid)

        self.kb_err_lbl = QLabel("", editor_inner)
        self.kb_err_lbl.setStyleSheet(f"font-size: 11px; color: {PALETTE['danger']};")
        self.kb_err_lbl.hide()
        ecol.addWidget(self.kb_err_lbl)

        reset_row = QHBoxLayout()
        reset_btn = QPushButton(tr("settings.keybinds.reset_slots"), editor_inner)
        reset_btn.clicked.connect(self._reset_profile_slots)
        reset_row.addWidget(reset_btn)
        reset_row.addStretch(1)
        ecol.addLayout(reset_row)

        self.editor_card.add_row(editor_inner)
        self.editor_card.setVisible(show_custom)
        col.addWidget(self.editor_card)
        col.addStretch(1)
        return self._wrap_scroll(page)

    def _sync_keybind_mode_buttons(self, custom_active: bool) -> None:
        self.kb_default_btn.setProperty("active", "false" if custom_active else "true")
        self.kb_custom_btn.setProperty("active", "true" if custom_active else "false")
        for btn in (self.kb_default_btn, self.kb_custom_btn):
            btn.style().unpolish(btn)
            btn.style().polish(btn)
        self.editor_card.setVisible(bool(custom_active))

    def _activate_default_keybind_profile(self) -> None:
        self._draft.keybind_profile = "default"
        self._keybind_editor_expanded = False
        self._sync_keybind_mode_buttons(False)
        idx = self.profile_combo.findText("default")
        if idx >= 0:
            self.profile_combo.blockSignals(True)
            self.profile_combo.setCurrentIndex(idx)
            self.profile_combo.blockSignals(False)
        self.del_profile_btn.setVisible(False)

    def _open_custom_keybind_editor(self) -> None:
        self._keybind_editor_expanded = True
        self._sync_keybind_mode_buttons(True)
        self.profile_name_edit.setFocus(Qt.FocusReason.MouseFocusReason)

    def _on_profile_selected(self, name: str) -> None:
        if not name:
            return
        self._draft.keybind_profile = name
        is_def = name == "default"
        self._keybind_editor_expanded = not is_def
        self.del_profile_btn.setVisible(not is_def)
        self._sync_keybind_mode_buttons(not is_def)
        if not is_def:
            self.profile_name_edit.setText(name)
            self._editing_slots = list(
                self._draft.custom_keybind_profiles.get(name, DEFAULT_KEYBINDS)
            )
        else:
            self._editing_slots = list(DEFAULT_KEYBINDS)
        for i, sb in enumerate(self.slot_buttons):
            sb.setText(self._editing_slots[i])

    def _capture_profile_slot(self, slot_idx: int) -> None:
        used = {
            f"Slot {i + 1}": tok
            for i, tok in enumerate(self._editing_slots)
            if i != slot_idx and tok
        }
        ov = KeyCaptureOverlay(
            tr("settings.keybinds.slot_label", num=slot_idx + 1),
            existing_map=used,
            allow_del=False,
            parent=self.parentWidget(),
        )
        ov.key_captured.connect(lambda tok, i=slot_idx: self._on_slot_captured(i, tok))
        self._open_sub_overlay(ov)

    def _on_slot_captured(self, slot_idx: int, token: str) -> None:
        self._editing_slots[slot_idx] = token
        self.slot_buttons[slot_idx].setText(token)
        self._draft.custom_keybinds = list(self._editing_slots)

    def _reset_profile_slots(self) -> None:
        self._editing_slots = list(DEFAULT_KEYBINDS)
        for i, sb in enumerate(self.slot_buttons):
            sb.setText(self._editing_slots[i])

    def _save_named_profile(self) -> None:
        pname = self.profile_name_edit.text().strip()
        if not pname or pname.lower() == "default":
            self.kb_err_lbl.setText(tr("settings.keybinds.err_name"))
            self.kb_err_lbl.show()
            return
        self.kb_err_lbl.hide()
        self._draft.custom_keybind_profiles[pname] = list(self._editing_slots)
        self._draft.custom_keybinds = list(self._editing_slots)
        self._draft.keybind_profile = pname
        self._build_all_pages()
        self.select_page(2)

    def _delete_selected_profile(self) -> None:
        name = self.profile_combo.currentText()
        if not name or name == "default":
            return
        self._draft.custom_keybind_profiles.pop(name, None)
        if self._draft.keybind_profile == name:
            self._draft.keybind_profile = "default"
        self._build_all_pages()
        self.select_page(2)

    # --- Page 3: Video & Playback (Autoplay + 1-60s Seek + Arrow Mode) ---
    def _build_video_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(14)
        self._make_page_header(col, "settings.video.heading", "settings.video.desc")

        group = _SettingGroupCard(page)

        self.autoplay_cb = _ToggleSwitch(group)
        self.autoplay_cb.setChecked(self._draft.video_autoplay)
        self.autoplay_cb.toggled.connect(
            lambda v: setattr(self._draft, "video_autoplay", bool(v))
        )
        group.add_row(
            _SettingRow(
                tr("settings.video.autoplay"),
                tr("settings.video.autoplay_sub"),
                control=self.autoplay_cb,
                parent=group,
            )
        )

        seek_host = QWidget(group)
        seek_row = QHBoxLayout(seek_host)
        seek_row.setContentsMargins(0, 0, 0, 0)
        seek_row.setSpacing(8)
        self.seek_slider = QSlider(Qt.Orientation.Horizontal, seek_host)
        self.seek_slider.setFixedWidth(116)
        self.seek_slider.setRange(1, 60)
        self.seek_slider.setValue(self._draft.video_seek_seconds)
        self.seek_spin = QSpinBox(seek_host)
        self.seek_spin.setFixedWidth(64)
        self.seek_spin.setRange(1, 60)
        self.seek_spin.setSuffix("s")
        self.seek_spin.setValue(self._draft.video_seek_seconds)

        def _sync_seek(val: int) -> None:
            v = max(1, min(60, int(val)))
            self._draft.video_seek_seconds = v
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(v)
            self.seek_slider.blockSignals(False)
            self.seek_spin.blockSignals(True)
            self.seek_spin.setValue(v)
            self.seek_spin.blockSignals(False)

        self.seek_slider.valueChanged.connect(_sync_seek)
        self.seek_spin.valueChanged.connect(_sync_seek)
        seek_row.addWidget(self.seek_slider)
        seek_row.addWidget(self.seek_spin)
        group.add_row(
            _SettingRow(
                tr("settings.video.seek_label"),
                tr("settings.video.seek_sub"),
                control=seek_host,
                parent=group,
            )
        )

        arrow_host = QWidget(group)
        arrow_row = QHBoxLayout(arrow_host)
        arrow_row.setContentsMargins(0, 0, 0, 0)
        arrow_row.setSpacing(6)
        self.arrow_seek_btn = QPushButton(tr("settings.video.arrow_seek"), arrow_host)
        self.arrow_skip_btn = QPushButton(tr("settings.video.arrow_skip"), arrow_host)
        for btn, mode in (
            (self.arrow_seek_btn, "seek"),
            (self.arrow_skip_btn, "skip"),
        ):
            btn.setObjectName("SegmentedButton")
            btn.setStyleSheet("padding: 6px 10px; font-size: 11px;")
            btn.setProperty("active", "true" if self._draft.video_arrow_mode == mode else "false")
            btn.clicked.connect(lambda _=False, m=mode: self._set_video_arrow_mode(m))
            arrow_row.addWidget(btn)
        group.add_row(
            _SettingRow(
                tr("settings.video.arrow_label"),
                tr("settings.video.arrow_sub"),
                control=arrow_host,
                parent=group,
            )
        )

        col.addWidget(group)
        col.addStretch(1)
        return self._wrap_scroll(page)

    def _set_video_arrow_mode(self, mode: str) -> None:
        self._draft.video_arrow_mode = "skip" if mode == "skip" else "seek"
        for btn, m in ((self.arrow_seek_btn, "seek"), (self.arrow_skip_btn, "skip")):
            btn.setProperty("active", "true" if self._draft.video_arrow_mode == m else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    # --- Page 4: Metadata Display Checkboxes ---
    def _build_metadata_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(14)
        self._make_page_header(col, "settings.meta.heading", "settings.meta.desc")

        group = _SettingGroupCard(page)
        grid_host = QWidget(group)
        grid = QGridLayout(grid_host)
        grid.setContentsMargins(16, 14, 16, 14)
        grid.setHorizontalSpacing(18)
        grid.setVerticalSpacing(10)
        self.meta_checkboxes: Dict[str, QCheckBox] = {}
        active_set = set(self._draft.visible_metadata_fields or DEFAULT_VISIBLE_METADATA_FIELDS)
        for idx, field in enumerate(METADATA_FIELD_KEYS):
            cb = QCheckBox(_escape_mnemonic(tr(f"meta.field.{field}")), grid_host)
            cb.setChecked(field in active_set)
            cb.toggled.connect(lambda _v, f=field: self._sync_visible_meta())
            self.meta_checkboxes[field] = cb
            grid.addWidget(cb, idx // 2, idx % 2)
        group.add_row(grid_host)
        col.addWidget(group)
        col.addStretch(1)
        return self._wrap_scroll(page)

    def _sync_visible_meta(self) -> None:
        self._draft.visible_metadata_fields = [
            f for f in METADATA_FIELD_KEYS if self.meta_checkboxes[f].isChecked()
        ]

    # --- Page 5: Theme & Background (Conditional BG Controls) ---
    def _build_theme_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(14)
        self._make_page_header(col, "settings.theme.heading", "settings.theme.desc")

        group = _SettingGroupCard(page)

        mode_host = QWidget(group)
        mode_row = QHBoxLayout(mode_host)
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(6)
        self.theme_black_btn = QPushButton(tr("settings.theme.black"), mode_host)
        self.theme_white_btn = QPushButton(tr("settings.theme.white"), mode_host)
        for btn, mode in ((self.theme_black_btn, "black"), (self.theme_white_btn, "white")):
            btn.setObjectName("SegmentedButton")
            btn.setStyleSheet("padding: 6px 10px; font-size: 11px;")
            btn.setProperty("active", "true" if self._draft.theme_mode == mode else "false")
            btn.clicked.connect(lambda _=False, m=mode: self._set_theme_mode(m))
            mode_row.addWidget(btn)
        group.add_row(
            _SettingRow(
                tr("settings.theme.mode_label"),
                tr("settings.theme.mode_sub"),
                control=mode_host,
                parent=group,
            )
        )

        bg_row_w = QWidget(group)
        bg_row_l = QHBoxLayout(bg_row_w)
        bg_row_l.setContentsMargins(16, 11, 16, 11)
        bg_row_l.setSpacing(14)

        bg_text = QVBoxLayout()
        bg_text.setSpacing(2)
        bg_title = QLabel(tr("settings.theme.upload_bg").rstrip("."), bg_row_w)
        bg_title.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {PALETTE['text_primary']}; background: transparent;"
        )
        bg_text.addWidget(bg_title)
        bg_sub = QLabel(tr("settings.theme.bg_sub"), bg_row_w)
        bg_sub.setWordWrap(True)
        bg_sub.setStyleSheet(f"font-size: 11px; color: {PALETTE['text_muted']}; background: transparent;")
        bg_text.addWidget(bg_sub)
        self.bg_path_lbl = QLabel("", bg_row_w)
        self.bg_path_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.bg_path_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; color: {PALETTE['accent']}; background: transparent;"
        )
        bg_text.addWidget(self.bg_path_lbl)
        bg_row_l.addLayout(bg_text, 1)

        upload_ctrl = QHBoxLayout()
        upload_ctrl.setSpacing(6)
        self.upload_bg_btn = QPushButton(tr("settings.theme.upload_bg"), bg_row_w)
        self.upload_bg_btn.setIcon(render_icon("image", 14, PALETTE["text_primary"]))
        self.upload_bg_btn.clicked.connect(self._choose_custom_bg)
        upload_ctrl.addWidget(self.upload_bg_btn)
        self.reset_bg_btn = QPushButton(tr("common.reset"), bg_row_w)
        self.reset_bg_btn.clicked.connect(self._reset_custom_bg)
        upload_ctrl.addWidget(self.reset_bg_btn)
        bg_row_l.addLayout(upload_ctrl)
        group.add_row(bg_row_w)

        dim_ctrl = QWidget(group)
        dim_ctrl_l = QHBoxLayout(dim_ctrl)
        dim_ctrl_l.setContentsMargins(0, 0, 0, 0)
        dim_ctrl_l.setSpacing(8)
        self.dim_slider = QSlider(Qt.Orientation.Horizontal, dim_ctrl)
        self.dim_slider.setFixedWidth(138)
        self.dim_slider.setRange(20, 90)
        self.dim_slider.setValue(self._draft.custom_bg_dim)
        self.dim_val_lbl = QLabel(f"{self._draft.custom_bg_dim}%", dim_ctrl)
        self.dim_val_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 12px; color: {PALETTE['accent']};"
        )
        self.dim_slider.valueChanged.connect(self._on_dim_changed)
        dim_ctrl_l.addWidget(self.dim_slider)
        dim_ctrl_l.addWidget(self.dim_val_lbl)

        self.dim_host = _SettingRow(
            tr("settings.theme.dim_label"),
            tr("settings.theme.dim_sub"),
            control=dim_ctrl,
            parent=group,
        )
        self._dim_divider = group.add_row(self.dim_host)

        col.addWidget(group)
        self._sync_custom_bg_visibility()
        col.addStretch(1)
        return self._wrap_scroll(page)

    def _set_theme_mode(self, mode: str) -> None:
        self._draft.theme_mode = "white" if mode == "white" else "black"
        for btn, m in ((self.theme_black_btn, "black"), (self.theme_white_btn, "white")):
            btn.setProperty("active", "true" if self._draft.theme_mode == m else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _choose_custom_bg(self) -> None:
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            tr("settings.theme.choose_bg"),
            "",
            "Images (*.png *.jpg *.jpeg *.webp *.bmp)",
        )
        if chosen:
            self.set_custom_bg_path(chosen)

    def set_custom_bg_path(self, path: str) -> None:
        self._draft.custom_bg_path = str(path or "").strip()
        self._sync_custom_bg_visibility()

    def _reset_custom_bg(self) -> None:
        self._draft.custom_bg_path = ""
        self._sync_custom_bg_visibility()

    def _on_dim_changed(self, val: int) -> None:
        self._draft.custom_bg_dim = max(20, min(90, int(val)))
        self.dim_val_lbl.setText(f"{self._draft.custom_bg_dim}%")

    def _sync_custom_bg_visibility(self) -> None:
        has_bg = bool(self._draft.custom_bg_path.strip())
        self.bg_path_lbl.setVisible(has_bg)
        self.dim_host.setVisible(has_bg)
        if getattr(self, "_dim_divider", None) is not None:
            self._dim_divider.setVisible(has_bg)
        self.reset_bg_btn.setVisible(has_bg)
        if has_bg:
            self.bg_path_lbl.setText(
                tr("settings.theme.bg_file", name=os.path.basename(self._draft.custom_bg_path))
            )

    # --- Page 6: About ---
    def _build_about_page(self) -> QWidget:
        page = QWidget()
        col = QVBoxLayout(page)
        col.setContentsMargins(0, 0, 6, 0)
        col.setSpacing(14)

        hero = _SettingGroupCard(page)

        top_w = QWidget(hero)
        top_row = QHBoxLayout(top_w)
        top_row.setContentsMargins(18, 16, 18, 16)
        top_row.setSpacing(14)

        self.about_logo_lbl = QLabel(top_w)
        self.about_logo_lbl.setFixedSize(44, 44)
        self.about_logo_lbl.setPixmap(
            render_brand_logo(44, device_pixel_ratio=2.0, dark_mode=(PALETTE["mode"] == "black"))
        )
        top_row.addWidget(self.about_logo_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        badge = QLabel(f"PHOTOBOX v{APP_VERSION}", top_w)
        badge.setStyleSheet(
            f"font-size: 17px; font-weight: 700; letter-spacing: 1.4px; color: {PALETTE['accent']};"
        )
        title_col.addWidget(badge)
        sub = QLabel(tr("app.subtitle"), top_w)
        sub.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        title_col.addWidget(sub)
        top_row.addLayout(title_col, 1)
        hero.add_row(top_w)

        body_w = QWidget(hero)
        body_col = QVBoxLayout(body_w)
        body_col.setContentsMargins(18, 14, 18, 16)
        body_col.setSpacing(10)

        desc = QLabel(tr("settings.about.desc"), body_w)
        desc.setWordWrap(True)
        desc.setStyleSheet(f"font-size: 13px; color: {PALETTE['text_secondary']};")
        body_col.addWidget(desc)

        credit = QLabel(tr("settings.about.credit"), body_w)
        credit.setStyleSheet(f"font-size: 13px; font-weight: 600; color: {PALETTE['text_primary']};")
        body_col.addWidget(credit)

        self.about_link_lbl = QLabel(
            f'<a href="https://github.com/Raincl-oud" style="color: {PALETTE["accent"]}; text-decoration: none;">'
            "https://github.com/Raincl-oud</a>",
            body_w,
        )
        self.about_link_lbl.setOpenExternalLinks(True)
        body_col.addWidget(self.about_link_lbl)
        hero.add_row(body_w)

        col.addWidget(hero)
        col.addStretch(1)
        return self._wrap_scroll(page)

    def refresh_theme(self) -> None:
        self.close_btn.setIcon(render_icon("close", 13, PALETTE["text_secondary"]))
        if hasattr(self, "about_logo_lbl"):
            self.about_logo_lbl.setPixmap(
                render_brand_logo(44, device_pixel_ratio=2.0, dark_mode=(PALETTE["mode"] == "black"))
            )
        if hasattr(self, "about_link_lbl"):
            self.about_link_lbl.setText(
                f'<a href="https://github.com/Raincl-oud" style="color: {PALETTE["accent"]}; text-decoration: none;">'
                "https://github.com/Raincl-oud</a>"
            )

    def _open_sub_overlay(self, overlay: _InWindowOverlayBase) -> None:
        if self._sub_overlay is not None:
            self._sub_overlay.deleteLater()
        self._sub_overlay = overlay
        overlay.closed.connect(lambda: setattr(self, "_sub_overlay", None))
        overlay.closed.connect(overlay.deleteLater)
        overlay.open_overlay()

    def sync_bounds(self) -> None:
        super().sync_bounds()
        p = self.parentWidget()
        if p is not None:
            self.card.setMinimumHeight(min(520, max(320, p.height() - 64)))
        if self._sub_overlay is not None and self._sub_overlay.isVisible():
            self._sub_overlay.sync_bounds()

    def result_settings(self) -> AppSettings:
        return self._draft.normalized()

    def _on_cancel(self) -> None:
        if get_language() != self._initial_language:
            set_language(self._initial_language)
            self.language_preview_requested.emit(self._initial_language)
        self.close_overlay()

    def _on_save(self) -> None:
        self.settings_saved.emit(self.result_settings())
        self.close_overlay()


# ---------------------------------------------------------------------------
# Splash Screen & Multi-Session Sidebar
# ---------------------------------------------------------------------------


class SplashScreen(QWidget):
    """Centered frameless Optical Instrument splash screen during boot."""

    BOOT_STEPS = (
        (18, "splash.boot.0"),
        (48, "splash.boot.1"),
        (78, "splash.boot.2"),
        (100, "splash.boot.3"),
    )

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowFlags(
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.SplashScreen
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setFixedSize(460, 260)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)

        card = QFrame(self)
        card.setObjectName("SplashCard")
        col = QVBoxLayout(card)
        col.setContentsMargins(28, 26, 28, 24)
        col.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        icon_lbl = QLabel(card)
        icon_lbl.setFixedSize(34, 34)
        icon_lbl.setPixmap(
            render_brand_logo(34, device_pixel_ratio=2.0, dark_mode=(PALETTE["mode"] == "black"))
        )
        top.addWidget(icon_lbl)

        title = QLabel("PHOTOBOX", card)
        title.setObjectName("BrandTitle")
        top.addWidget(title)
        ver = QLabel(f"v{APP_VERSION}", card)
        ver.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; "
            f"font-weight: 700; color: {PALETTE['accent']};"
        )
        top.addWidget(ver)
        top.addStretch(1)
        col.addLayout(top)

        sub = QLabel(tr("app.subtitle"), card)
        sub.setStyleSheet(f"font-size: 13px; color: {PALETTE['text_secondary']};")
        col.addWidget(sub)

        col.addStretch(1)

        self.status_lbl = QLabel(tr(self.BOOT_STEPS[0][1]), card)
        self.status_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; color: {PALETTE['text_muted']};"
        )
        col.addWidget(self.status_lbl)

        self.progress = QProgressBar(card)
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.setTextVisible(False)
        self.progress.setFixedHeight(5)
        self.progress.setStyleSheet(
            f"QProgressBar {{ background: {PALETTE['bg_input']}; border-radius: 2px; border: none; }}"
            f"QProgressBar::chunk {{ background: {PALETTE['accent']}; border-radius: 2px; }}"
        )
        col.addWidget(self.progress)
        root.addWidget(card)

    def set_step(self, pct: int, msg_or_key: str) -> None:
        self.progress.setValue(max(0, min(100, int(pct))))
        text = tr(msg_or_key) if msg_or_key.startswith("splash.") else msg_or_key
        self.status_lbl.setText(text)


class _SessionRowWidget(QFrame):
    clicked = pyqtSignal(str)
    delete_clicked = pyqtSignal(str)

    def __init__(self, session: SessionState, active: bool, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionRowCard")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.session_id = session.id
        self._active = active
        self._hover_t = 0.0
        self._active_t = 1.0 if active else 0.0
        self._hover_anim = ValueAnimator(0.0, parent=self)
        self._active_anim = ValueAnimator(self._active_t, parent=self)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFixedHeight(54)

        row = QHBoxLayout(self)
        row.setContentsMargins(12, 6, 8, 6)
        row.setSpacing(6)

        text_col = QVBoxLayout()
        text_col.setSpacing(1)
        self.name_lbl = QLabel("", self)
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        text_col.addWidget(self.name_lbl)

        self.stat_lbl = QLabel("", self)
        self.stat_lbl.setTextFormat(Qt.TextFormat.PlainText)
        text_col.addWidget(self.stat_lbl)
        row.addLayout(text_col, 1)

        self.del_btn = QPushButton(self)
        self.del_btn.setObjectName("GhostIconButton")
        self.del_btn.setFixedSize(24, 24)
        self.del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.session_id))
        row.addWidget(self.del_btn)

        self.update_session(session, active, force_style=True)

    def _set_hover_t(self, val: float) -> None:
        self._hover_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _set_active_t(self, val: float) -> None:
        self._active_t = max(0.0, min(1.0, float(val)))
        self.update()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        super().enterEvent(event)
        self._hover_anim.animate_to(
            1.0, duration_ms=120, curve=ease_out(), on_value=self._set_hover_t, non_vestibular=True
        )

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        super().leaveEvent(event)
        self._hover_anim.animate_to(
            0.0, duration_ms=155, curve=ease_out(), on_value=self._set_hover_t, non_vestibular=True
        )

    def update_session(
        self, session: SessionState, active: bool, force_style: bool = False
    ) -> None:
        self.session_id = session.id
        self.name_lbl.setText(session.name)

        done_cnt = sum(1 for e in session.timeline if e.status == "sorted")
        rem_cnt = max(0, len(session.timeline) - done_cnt)
        status_txt = (
            tr("sidebar.status_done", sorted=done_cnt)
            if (len(session.timeline) > 0 and rem_cnt == 0)
            else tr("sidebar.status_progress", remaining=rem_cnt, sorted=done_cnt)
        )
        self.stat_lbl.setText(status_txt)
        self.del_btn.setToolTip(tr("sidebar.remove_tip"))

        state_changed = self._active != active
        self._active = active
        if state_changed and not force_style:
            self._active_anim.animate_to(
                1.0 if active else 0.0,
                duration_ms=160,
                curve=ease_out(),
                on_value=self._set_active_t,
                non_vestibular=True,
            )
        elif force_style:
            target = 1.0 if active else 0.0
            self._active_anim.stop_silent(target)
            self._active_t = target
            self.name_lbl.setStyleSheet(
                f"font-size: 12px; font-weight: 600; color: {PALETTE['text_primary']}; border: none; background: transparent;"
            )
            self.stat_lbl.setStyleSheet(
                f"font-size: 11px; color: {PALETTE['text_muted']}; border: none; background: transparent;"
            )
            self.del_btn.setIcon(render_icon("close", 12, PALETTE["text_muted"]))
        self.update()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        card_c = QColor(PALETTE["bg_card"])
        hover_c = QColor(PALETTE["bg_hover"])
        if hover_c.alpha() < 255:
            idle_c = lerp_color(card_c, hover_c, self._hover_t * 0.45) if self._hover_t > 0.0 else card_c
        else:
            idle_c = lerp_color(card_c, hover_c, self._hover_t)
        active_c = QColor(PALETTE["accent_soft"])
        bg = lerp_color(idle_c, active_c, self._active_t)
        border_idle = lerp_color(
            QColor(PALETTE["border_subtle"]),
            QColor(PALETTE["border_strong"]),
            self._hover_t,
        )
        border = lerp_color(border_idle, QColor(PALETTE["accent"]), self._active_t)
        painter.setPen(QPen(border, 1.0))
        painter.setBrush(bg)
        painter.drawRoundedRect(rect, 9.0, 9.0)

        if self._active_t > 0.02:
            bar_h = lerp_float(10.0, max(18.0, float(self.height()) - 16.0), self._active_t)
            bar_y = (float(self.height()) - bar_h) / 2.0
            bar_c = QColor(PALETTE["accent"])
            bar_c.setAlpha(int(round(255.0 * self._active_t)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bar_c)
            painter.drawRoundedRect(QRectF(2.5, bar_y, 3.0, bar_h), 1.5, 1.5)
        painter.end()

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.session_id)
        super().mousePressEvent(event)


class SessionSidebar(QFrame):
    """Collapsible multi-session sidebar drawer."""

    TARGET_WIDTH = 264

    add_session_requested = pyqtSignal()
    session_selected = pyqtSignal(str)
    session_delete_requested = pyqtSignal(str)
    collapse_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SessionSidebar")
        self.setFixedWidth(self.TARGET_WIDTH)

        self._sessions: List[SessionState] = []
        self._active_id: Optional[str] = None
        self._row_widgets: Dict[str, _SessionRowWidget] = {}

        col = QVBoxLayout(self)
        col.setContentsMargins(14, 16, 14, 16)
        col.setSpacing(12)

        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        self.brand_logo_lbl = QLabel(self)
        self.brand_logo_lbl.setFixedSize(22, 22)
        self.brand_logo_lbl.setPixmap(
            render_brand_logo(22, device_pixel_ratio=2.0, dark_mode=(PALETTE["mode"] == "black"))
        )
        hdr.addWidget(self.brand_logo_lbl)
        brand = QLabel("PHOTOBOX", self)
        brand.setStyleSheet(
            f"font-size: 14px; font-weight: 700; letter-spacing: 1.8px; color: {PALETTE['text_primary']};"
        )
        hdr.addWidget(brand)
        hdr.addStretch(1)
        self.collapse_btn = QPushButton(self)
        self.collapse_btn.setObjectName("GhostIconButton")
        self.collapse_btn.setFixedSize(28, 28)
        self.collapse_btn.setIcon(render_icon("sidebar_collapse", 15, PALETTE["text_secondary"]))
        self.collapse_btn.setToolTip(tr("sidebar.collapse_tip"))
        self.collapse_btn.clicked.connect(self.collapse_requested.emit)
        hdr.addWidget(self.collapse_btn)
        col.addLayout(hdr)

        self.add_btn = QPushButton(tr("sidebar.add_session"), self)
        self.add_btn.setObjectName("PrimaryButton")
        self.add_btn.setIcon(render_icon("plus", 14, PALETTE["accent_text_on_fill"]))
        self.add_btn.clicked.connect(self.add_session_requested.emit)
        col.addWidget(self.add_btn)

        self.scroll = QScrollArea(self)
        self.scroll.setWidgetResizable(True)
        self.list_host = QWidget(self.scroll)
        self.list_layout = QVBoxLayout(self.list_host)
        self.list_layout.setContentsMargins(0, 0, 2, 0)
        self.list_layout.setSpacing(6)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_host)
        col.addWidget(self.scroll, 1)

        self.empty_lbl = QLabel(tr("sidebar.empty"), self)
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_muted']};")
        col.addWidget(self.empty_lbl)

    def set_sessions(self, sessions: List[SessionState], active_id: Optional[str]) -> None:
        self._sessions = list(sessions)
        self._active_id = active_id
        new_ids = [s.id for s in self._sessions]
        if new_ids == list(self._row_widgets.keys()):
            # Ultra-fast O(N) in-place update without tearing down QSS widgets!
            self.empty_lbl.setVisible(len(self._sessions) == 0)
            for sess in self._sessions:
                self._row_widgets[sess.id].update_session(sess, active=(sess.id == active_id))
            return
        self._rebuild(force_style=False)

    def _rebuild(self, force_style: bool = True) -> None:
        while self.list_layout.count() > 1:
            item = self.list_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()
        self._row_widgets.clear()
        self.empty_lbl.setVisible(len(self._sessions) == 0)
        for sess in self._sessions:
            row = _SessionRowWidget(sess, active=(sess.id == self._active_id), parent=self.list_host)
            row.clicked.connect(self.session_selected.emit)
            row.delete_clicked.connect(self.session_delete_requested.emit)
            self._row_widgets[sess.id] = row
            self.list_layout.insertWidget(self.list_layout.count() - 1, row)

    def refresh_theme(self) -> None:
        self.brand_logo_lbl.setPixmap(
            render_brand_logo(22, device_pixel_ratio=2.0, dark_mode=(PALETTE["mode"] == "black"))
        )
        self.collapse_btn.setIcon(render_icon("sidebar_collapse", 15, PALETTE["text_secondary"]))
        self.add_btn.setIcon(render_icon("plus", 14, PALETTE["accent_text_on_fill"]))
        self.empty_lbl.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_muted']};")
        for sess in self._sessions:
            w = self._row_widgets.get(sess.id)
            if w is not None:
                w.update_session(sess, active=(sess.id == self._active_id), force_style=True)

    def refresh_texts(self) -> None:
        self.collapse_btn.setToolTip(tr("sidebar.collapse_tip"))
        self.add_btn.setText(_escape_mnemonic(tr("sidebar.add_session")))
        self.empty_lbl.setText(tr("sidebar.empty"))
        for sess in self._sessions:
            w = self._row_widgets.get(sess.id)
            if w is not None:
                w.update_session(sess, active=(sess.id == self._active_id))


# ---------------------------------------------------------------------------
# Session Setup View (Pinned Far-Left Trash + Category Chips + Auto-Expand)
# ---------------------------------------------------------------------------


class _SetupTrashChip(QFrame):
    """Pinned left-most permanent Trash badge in SessionSetupView."""

    def __init__(
        self,
        name: str = "_Buang",
        key_label: str = "DEL",
        color: str = DEFAULT_TRASH_COLOR,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setFixedHeight(36)
        self._color = color or DEFAULT_TRASH_COLOR
        self.setStyleSheet(
            f"QFrame {{ background-color: {PALETTE['bg_keycap']}; "
            f"border: 1px solid {PALETTE['border_trash']}; border-radius: 8px; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 10, 4)
        row.setSpacing(6)

        self.key_lbl = QLabel((key_label or "DEL").upper(), self)
        self.key_lbl.setStyleSheet(
            f"background-color: {self._color}; color: {_contrast_ink(self._color)}; "
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; font-weight: 700; "
            f"padding: 2px 7px; border-radius: 5px; border: none;"
        )
        row.addWidget(self.key_lbl)

        self.icon_lbl = QLabel(self)
        self.icon_lbl.setPixmap(render_icon("trash", 13, self._color).pixmap(13, 13))
        self.icon_lbl.setStyleSheet("border: none; background: transparent;")
        row.addWidget(self.icon_lbl)

        self.name_lbl = QLabel(name or "_Buang", self)
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.name_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {PALETTE['text_primary']}; border: none; background: transparent;"
        )
        row.addWidget(self.name_lbl)

    def update_spec(self, name: str, key_label: str, color: str) -> None:
        self._color = color or DEFAULT_TRASH_COLOR
        self.key_lbl.setText((key_label or "DEL").upper())
        self.key_lbl.setStyleSheet(
            f"background-color: {self._color}; color: {_contrast_ink(self._color)}; "
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; font-weight: 700; "
            f"padding: 2px 7px; border-radius: 5px; border: none;"
        )
        self.icon_lbl.setPixmap(render_icon("trash", 13, self._color).pixmap(13, 13))
        self.name_lbl.setText(name or "_Buang")
        self.name_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {PALETTE['text_primary']}; border: none; background: transparent;"
        )


class _SetupCategoryChip(QFrame):
    """Editable category chip in SessionSetupView with integrated colored keycap + remove x."""

    key_clicked = pyqtSignal(str)
    color_clicked = pyqtSignal(str)
    remove_clicked = pyqtSignal(str)

    def __init__(
        self,
        name: str,
        key_token: str,
        color: str,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.category = name
        self.key_token = key_token
        self.color = color or DEFAULT_CATEGORY_COLOR
        self._entrance_anim = ValueAnimator(1.0, parent=self)
        self.setFixedHeight(36)
        self.setStyleSheet(
            f"QFrame {{ background-color: {PALETTE['bg_keycap']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 8px; }}"
        )
        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(6)

        self.key_btn = QPushButton(key_token or "?", self)
        self.key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.key_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.key_btn.setToolTip(tr("setup.chip_key_tip", name=name))
        self.key_btn.setStyleSheet(
            f"QPushButton {{ background-color: {self.color}; color: {_contrast_ink(self.color)}; "
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; font-weight: 700; "
            f"padding: 2px 8px; border-radius: 5px; border: none; min-width: 24px; }}"
        )
        self.key_btn.clicked.connect(lambda: self.key_clicked.emit(self.category))
        row.addWidget(self.key_btn)

        self.name_lbl = QLabel(name, self)
        self.name_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.name_lbl.setStyleSheet(
            f"font-size: 12px; font-weight: 600; color: {PALETTE['text_primary']}; border: none; background: transparent;"
        )
        row.addWidget(self.name_lbl)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_chip_menu)

        rm_btn = QPushButton(self)
        rm_btn.setObjectName("GhostIconButton")
        rm_btn.setFixedSize(22, 22)
        rm_btn.setIcon(render_icon("close", 11, PALETTE["text_secondary"]))
        rm_btn.clicked.connect(lambda: self.remove_clicked.emit(self.category))
        row.addWidget(rm_btn)

    def play_entrance(self) -> None:
        if prefers_reduced_motion():
            return
        eff = self.graphicsEffect()
        if not isinstance(eff, QGraphicsOpacityEffect):
            eff = QGraphicsOpacityEffect(self)
            self.setGraphicsEffect(eff)
        eff.setOpacity(0.0)

        def _tick(val: float) -> None:
            cur_eff = self.graphicsEffect()
            if isinstance(cur_eff, QGraphicsOpacityEffect):
                cur_eff.setOpacity(max(0.0, min(1.0, float(val))))

        def _done() -> None:
            self.setGraphicsEffect(None)

        self._entrance_anim.animate_to(
            1.0,
            duration_ms=145,
            curve=ease_out(),
            start_from=0.0,
            on_value=_tick,
            on_done=_done,
            non_vestibular=True,
        )

    def _show_chip_menu(self, pos: QPoint) -> None:
        menu = QMenu(self)
        act_key = menu.addAction(tr("sorting.menu.keybind"))
        act_color = menu.addAction(tr("sorting.menu.color"))
        chosen = menu.exec(self.mapToGlobal(pos))
        if chosen == act_key:
            self.key_clicked.emit(self.category)
        elif chosen == act_color:
            self.color_clicked.emit(self.category)


class _AutoExpandingCategoryBox(QWidget):
    """Custom-painted dashed inline box whose QLineEdit grows dynamically with user text."""

    key_clicked = pyqtSignal()
    add_submitted = pyqtSignal(str, str)

    def __init__(self, initial_key: str = "1", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.key_token = initial_key or ""
        self._focus_t = 0.0
        self._focus_anim = ValueAnimator(0.0, parent=self)
        self.setFixedHeight(36)

        row = QHBoxLayout(self)
        row.setContentsMargins(6, 4, 6, 4)
        row.setSpacing(6)

        self.key_btn = QPushButton(self.key_token or "+", self)
        self.key_btn.setObjectName("KeyBadgeButton")
        self.key_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.key_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.key_btn.setToolTip(tr("setup.inline_tip"))
        self.key_btn.clicked.connect(self.key_clicked.emit)
        row.addWidget(self.key_btn)

        self.name_input = QLineEdit(self)
        self.name_input.setObjectName("InlineCategoryInput")
        self.name_input.setPlaceholderText(tr("setup.inline_placeholder"))
        self.name_input.setMinimumWidth(116)
        self.name_input.setMaximumWidth(280)
        self.name_input.installEventFilter(self)
        self.name_input.textChanged.connect(self._adjust_input_width)
        self.name_input.returnPressed.connect(self._emit_add)
        row.addWidget(self.name_input)

        self.add_btn = QPushButton(self)
        self.add_btn.setObjectName("GhostIconButton")
        self.add_btn.setFixedSize(24, 24)
        self.add_btn.setIcon(render_icon("plus", 12, PALETTE["accent"]))
        self.add_btn.setToolTip(tr("setup.inline_add_tip"))
        self.add_btn.clicked.connect(self._emit_add)
        row.addWidget(self.add_btn)

        self._adjust_input_width("")

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self._adjust_input_width(self.name_input.text())

    def _adjust_input_width(self, text: str) -> None:
        sample = text or self.name_input.placeholderText()
        fm = QFontMetrics(self.name_input.font())
        target = max(116, min(280, fm.horizontalAdvance(sample) + 26))
        self.name_input.setFixedWidth(target)
        self.adjustSize()
        self.updateGeometry()

    def set_key_token(self, token: str) -> None:
        self.key_token = (token or "").upper()
        self.key_btn.setText(self.key_token or "?")
        self.adjustSize()

    def clear_and_reset(self, next_key: str) -> None:
        self.name_input.clear()
        self.set_key_token(next_key)
        self._adjust_input_width("")

    def refresh_texts(self) -> None:
        self.name_input.setPlaceholderText(tr("setup.inline_placeholder"))
        self.key_btn.setToolTip(tr("setup.inline_tip"))
        self.add_btn.setToolTip(tr("setup.inline_add_tip"))
        self._adjust_input_width(self.name_input.text())

    def refresh_theme(self) -> None:
        self.add_btn.setIcon(render_icon("plus", 12, PALETTE["accent"]))
        self.update()

    def _emit_add(self) -> None:
        txt = self.name_input.text().strip()
        if txt:
            self.add_submitted.emit(txt, self.key_token)

    def _set_focus_t(self, val: float) -> None:
        self._focus_t = max(0.0, min(1.0, float(val)))
        self.update()

    def eventFilter(self, obj, event) -> bool:  # type: ignore[override]
        if obj is self.name_input:
            etype = event.type()
            if etype == QEvent.Type.FocusIn:
                self._focus_anim.animate_to(
                    1.0,
                    duration_ms=140,
                    curve=ease_out(),
                    on_value=self._set_focus_t,
                    non_vestibular=True,
                )
            elif etype == QEvent.Type.FocusOut:
                self._focus_anim.animate_to(
                    0.0,
                    duration_ms=140,
                    curve=ease_out(),
                    on_value=self._set_focus_t,
                    non_vestibular=True,
                )
        return super().eventFilter(obj, event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        rect = QRectF(1.0, 1.0, self.width() - 2.0, self.height() - 2.0)
        focus_t = max(self._focus_t, 1.0 if (self.name_input.hasFocus() and prefers_reduced_motion()) else 0.0)
        stroke = lerp_color(QColor(PALETTE["border_strong"]), QColor(PALETTE["accent"]), focus_t)
        width = lerp_float(1.2, 1.65, focus_t)
        fill = QColor(PALETTE["bg_input"])
        _draw_dashed_rounded_rect(painter, rect, 8.0, stroke, fill_color=fill, width=width)
        painter.end()


class SessionSetupView(QWidget):
    """Multi-session setup screen with pinned far-left Trash, auto-expanding inline box & Deep Search."""

    sidebar_expand_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    overlay_requested = pyqtSignal(object)
    session_started = pyqtSignal(str, str, list, bool, dict)
    resume_existing_requested = pyqtSignal(str)

    def __init__(
        self,
        settings_provider: Optional[Callable[[], AppSettings]] = None,
        duplicate_checker: Optional[Callable[[str], Optional[SessionState]]] = None,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setObjectName("SetupRoot")
        self._settings_provider = settings_provider or AppSettings
        self._duplicate_checker = duplicate_checker or (lambda _p: None)
        self._categories: List[str] = []
        self._category_keys: Dict[str, str] = {}
        self._category_colors: Dict[str, str] = {}
        self._existing_match: Optional[SessionState] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 14, 24, 24)
        root.setSpacing(12)

        # Top bar (sidebar expand button left, settings button far top-right)
        top_bar = QHBoxLayout()
        self.sidebar_expand_btn = QPushButton(self)
        self.sidebar_expand_btn.setObjectName("GhostIconButton")
        self.sidebar_expand_btn.setFixedSize(32, 32)
        self.sidebar_expand_btn.setIcon(render_icon("sidebar_expand", 16, PALETTE["text_secondary"]))
        self.sidebar_expand_btn.setToolTip(tr("sidebar.expand_tip"))
        self.sidebar_expand_btn.clicked.connect(self.sidebar_expand_requested.emit)
        self.sidebar_expand_btn.hide()
        top_bar.addWidget(self.sidebar_expand_btn)
        top_bar.addStretch(1)

        self.settings_btn = QPushButton(self)
        self.settings_btn.setObjectName("GhostIconButton")
        self.settings_btn.setFixedSize(32, 32)
        self.settings_btn.setIcon(render_icon("settings", 16, PALETTE["text_secondary"]))
        self.settings_btn.setToolTip(tr("sorting.settings_tip"))
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        top_bar.addWidget(self.settings_btn)
        root.addLayout(top_bar)

        # Centered Card Container
        center_row = QHBoxLayout()
        center_row.addStretch(1)
        self.card = QFrame(self)
        self.card.setObjectName("ElevatedCard")
        self.card.setMaximumWidth(740)
        self.card.setMinimumWidth(520)
        card_col = QVBoxLayout(self.card)
        card_col.setContentsMargins(26, 24, 26, 24)
        card_col.setSpacing(14)

        self.heading_lbl = QLabel(tr("setup.heading"), self.card)
        self.heading_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {PALETTE['text_primary']};"
        )
        card_col.addWidget(self.heading_lbl)

        self.lead_lbl = QLabel(tr("setup.lead"), self.card)
        self.lead_lbl.setWordWrap(True)
        self.lead_lbl.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        card_col.addWidget(self.lead_lbl)

        # Source Folder row
        self.src_lbl = QLabel(tr("setup.source_folder"), self.card)
        self.src_lbl.setObjectName("FieldLabel")
        card_col.addWidget(self.src_lbl)

        folder_row = QHBoxLayout()
        self.folder_edit = QLineEdit(self.card)
        self.folder_edit.setPlaceholderText(tr("setup.folder_placeholder"))
        self.folder_edit.textChanged.connect(self._on_folder_changed)
        folder_row.addWidget(self.folder_edit, 1)

        self.browse_btn = QPushButton(tr("setup.browse"), self.card)
        self.browse_btn.setIcon(render_icon("folder", 14, PALETTE["text_primary"]))
        self.browse_btn.clicked.connect(self._browse_folder)
        folder_row.addWidget(self.browse_btn)
        card_col.addLayout(folder_row)

        self.deep_search_cb = QCheckBox(_escape_mnemonic(tr("setup.deep_search")), self.card)
        self.deep_search_cb.hide()
        self.deep_search_cb.toggled.connect(self._on_deep_search_toggled)
        card_col.addWidget(self.deep_search_cb)

        self.dup_notice_lbl = QLabel("", self.card)
        self.dup_notice_lbl.setWordWrap(True)
        self.dup_notice_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.dup_notice_lbl.setStyleSheet(
            f"background-color: {PALETTE['accent_soft']}; color: {PALETTE['accent']}; "
            f"border: 1px solid {PALETTE['accent']}; border-radius: 7px; padding: 8px 10px; font-size: 12px;"
        )
        self.dup_notice_lbl.hide()
        card_col.addWidget(self.dup_notice_lbl)

        # Session Name
        self.name_lbl = QLabel(tr("setup.session_name"), self.card)
        self.name_lbl.setObjectName("FieldLabel")
        card_col.addWidget(self.name_lbl)

        self.name_edit = QLineEdit(self.card)
        self.name_edit.setPlaceholderText(tr("setup.name_placeholder"))
        card_col.addWidget(self.name_edit)

        # Target Categories Header + Load Existing Subfolders Button
        cat_hdr = QHBoxLayout()
        self.cats_lbl = QLabel(tr("setup.target_categories"), self.card)
        self.cats_lbl.setObjectName("FieldLabel")
        cat_hdr.addWidget(self.cats_lbl)
        cat_hdr.addStretch(1)
        self.load_subfolders_btn = QPushButton("", self.card)
        self.load_subfolders_btn.setIcon(render_icon("folder_plus", 14, PALETTE["accent"]))
        self.load_subfolders_btn.hide()
        self.load_subfolders_btn.clicked.connect(self._load_existing_subfolders)
        cat_hdr.addWidget(self.load_subfolders_btn)
        card_col.addLayout(cat_hdr)

        # Chips Flow Container (Trash PINNED AT INDEX 0 -> User Chips -> Inline Auto-Expanding Box)
        self.chips_container = QWidget(self.card)
        self.chips_layout = CenteredFlowLayout(self.chips_container, h_spacing=8, v_spacing=8)
        self.chips_layout.setContentsMargins(0, 4, 0, 4)

        self.trash_chip = _SetupTrashChip(parent=self.chips_container)
        self.inline_box = _AutoExpandingCategoryBox(initial_key="1", parent=self.chips_container)
        self.inline_box.key_clicked.connect(self._on_inline_key_clicked)
        self.inline_box.add_submitted.connect(self._on_inline_add_submitted)
        card_col.addWidget(self.chips_container)

        self.hint_lbl = QLabel("", self.card)
        self.hint_lbl.setWordWrap(True)
        self.hint_lbl.setStyleSheet(f"font-size: 11px; color: {PALETTE['text_muted']};")
        card_col.addWidget(self.hint_lbl)

        self.error_lbl = QLabel("", self.card)
        self.error_lbl.setWordWrap(True)
        self.error_lbl.setStyleSheet(f"font-size: 12px; color: {PALETTE['danger']};")
        self.error_lbl.hide()
        card_col.addWidget(self.error_lbl)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.start_btn = QPushButton(tr("setup.start_sorting"), self.card)
        self.start_btn.setObjectName("PrimaryButton")
        self.start_btn.setMinimumHeight(38)
        self.start_btn.clicked.connect(self._on_start_clicked)
        action_row.addWidget(self.start_btn)
        card_col.addLayout(action_row)

        center_row.addWidget(self.card, 1)
        center_row.addStretch(1)
        root.addLayout(center_row, 1)

        self.reset_defaults()

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.sidebar_expand_btn.setVisible(bool(collapsed))

    def apply_settings(self, settings: AppSettings) -> None:
        norm = settings.normalized()
        self.trash_chip.update_spec(
            norm.trash_category.name, norm.trash_category.key, norm.trash_category.color
        )
        self.hint_lbl.setText(tr("setup.hint", trash=norm.trash_category.name))
        self._refresh_chips()

    def reset_defaults(self) -> None:
        settings = self._settings_provider().normalized()
        self._categories = []
        self._category_keys = {}
        self._category_colors = {}
        self._existing_match = None
        self.folder_edit.clear()
        self.name_edit.clear()
        self.deep_search_cb.setChecked(False)
        self.deep_search_cb.hide()
        self.load_subfolders_btn.hide()
        self.dup_notice_lbl.hide()
        self.error_lbl.hide()
        self.start_btn.setText(tr("setup.start_sorting"))

        slots = settings.active_keybind_slots()
        for idx, spec in enumerate(settings.default_categories):
            name = spec.name
            if name.lower() == settings.trash_category.name.lower():
                continue
            self._categories.append(name)
            key = spec.key or (slots[idx] if idx < len(slots) else "")
            if key:
                self._category_keys[name] = key
            self._category_colors[name] = spec.color or DEFAULT_CATEGORY_COLOR

        self.trash_chip.update_spec(
            settings.trash_category.name,
            settings.trash_category.key,
            settings.trash_category.color,
        )
        self.hint_lbl.setText(tr("setup.hint", trash=settings.trash_category.name))
        self._refresh_chips()

    def _next_available_key(self) -> str:
        settings = self._settings_provider().normalized()
        used = set(self._category_keys.values())
        if settings.trash_category.key:
            used.add(settings.trash_category.key)
        return next_available_keybind(used, settings.active_keybind_slots()) or ""

    def _refresh_chips(self, animate_category: Optional[str] = None) -> None:
        # Immediately hide & detach transient chips so no stale frame paints at (0,0)
        while self.chips_layout.count():
            item = self.chips_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                if w in (self.trash_chip, self.inline_box):
                    self.chips_layout.removeWidget(w)
                else:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()

        settings = self._settings_provider().normalized()
        # Index 0: Trash chip pinned on far left!
        self.trash_chip.update_spec(
            settings.trash_category.name,
            settings.trash_category.key,
            settings.trash_category.color,
        )
        self.chips_layout.addWidget(self.trash_chip)
        self.trash_chip.show()

        # Index 1..N: Configured category chips
        for idx, cat in enumerate(self._categories):
            key = self._category_keys.get(cat, "")
            color = self._category_colors.get(
                cat, CATEGORY_COLOR_PRESETS[idx % len(CATEGORY_COLOR_PRESETS)][1]
            )
            chip = _SetupCategoryChip(cat, key, color, parent=self.chips_container)
            chip.key_clicked.connect(self._on_chip_key_clicked)
            chip.color_clicked.connect(self._on_chip_color_clicked)
            chip.remove_clicked.connect(self._on_chip_remove)
            self.chips_layout.addWidget(chip)
            if animate_category and cat == animate_category:
                chip.play_entrance()

        # Index N+1: Auto-expanding inline category input box
        self.inline_box.set_key_token(self._next_available_key())
        self.chips_layout.addWidget(self.inline_box)
        self.inline_box.show()
        self.chips_container.updateGeometry()

    def _browse_folder(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, tr("setup.browse_dialog"), "")
        if chosen:
            self.folder_edit.setText(chosen)

    def _on_folder_changed(self, path_str: str) -> None:
        clean = path_str.strip()
        if clean and os.path.isdir(clean):
            if not self.name_edit.text().strip():
                self.name_edit.setText(Path(clean).name or clean)
            match = self._duplicate_checker(clean)
            self._existing_match = match
            if match is not None:
                self.dup_notice_lbl.setText(tr("setup.duplicate_notice", name=match.name))
                self.dup_notice_lbl.show()
                self.start_btn.setText(tr("setup.continue_session"))
            else:
                self.dup_notice_lbl.hide()
                self.start_btn.setText(tr("setup.start_sorting"))

            trash_name = self._settings_provider().normalized().trash_category.name
            sub_cnt = count_subfolders(clean, trash_folder=trash_name)
            if sub_cnt > 0 and has_subfolders(clean, trash_folder=trash_name):
                self.deep_search_cb.show()
                self.load_subfolders_btn.setText(
                    _escape_mnemonic(tr("setup.load_subfolders", count=sub_cnt))
                )
                self.load_subfolders_btn.setVisible(not self.deep_search_cb.isChecked())
            else:
                self.deep_search_cb.setChecked(False)
                self.deep_search_cb.hide()
                self.load_subfolders_btn.hide()
        else:
            self._existing_match = None
            self.dup_notice_lbl.hide()
            self.deep_search_cb.setChecked(False)
            self.deep_search_cb.hide()
            self.load_subfolders_btn.hide()
            self.start_btn.setText(tr("setup.start_sorting"))

    def _on_deep_search_toggled(self, checked: bool) -> None:
        clean = self.folder_edit.text().strip()
        trash_name = self._settings_provider().normalized().trash_category.name
        has_sub = bool(
            clean
            and os.path.isdir(clean)
            and has_subfolders(clean, trash_folder=trash_name)
        )
        self.load_subfolders_btn.setVisible(has_sub and not checked)

    def _load_existing_subfolders(self) -> None:
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            return
        settings = self._settings_provider().normalized()
        trash_lower = settings.trash_category.name.lower()
        existing_lower = {c.lower() for c in self._categories}
        for entry in sorted(Path(folder).iterdir(), key=lambda p: p.name.lower()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            if entry.name.lower() in (trash_lower, "_buang"):
                continue
            try:
                clean = sanitize_category_name(entry.name, allow_trash=False)
            except ValueError:
                continue
            if clean.lower() not in existing_lower:
                self._categories.append(clean)
                existing_lower.add(clean.lower())
                nk = self._next_available_key()
                if nk:
                    self._category_keys[clean] = nk
                self._category_colors[clean] = CATEGORY_COLOR_PRESETS[
                    (len(self._categories) - 1) % len(CATEGORY_COLOR_PRESETS)
                ][1]
        self._refresh_chips()

    def _setup_existing_key_map(self) -> Dict[str, str]:
        used = dict(self._category_keys)
        trash = self._settings_provider().normalized().trash_category
        if trash.key:
            used[trash.name] = trash.key
        return used

    def _on_inline_key_clicked(self) -> None:
        ov = KeyCaptureOverlay(
            tr("sorting.new_category"),
            existing_map=self._setup_existing_key_map(),
            allow_del=False,
            parent=self.window(),
        )
        ov.key_captured.connect(self.inline_box.set_key_token)
        self.overlay_requested.emit(ov)

    def _on_inline_add_submitted(self, raw_name: str, key_token: str) -> None:
        settings = self._settings_provider().normalized()
        try:
            clean = sanitize_category_name(raw_name, allow_trash=False)
        except ValueError as exc:
            self.error_lbl.setText(str(exc))
            self.error_lbl.show()
            return
        if clean.lower() == settings.trash_category.name.lower() or any(
            c.lower() == clean.lower() for c in self._categories
        ):
            self.error_lbl.setText(tr("setup.err_duplicate_cat", name=clean))
            self.error_lbl.show()
            return
        self.error_lbl.hide()
        self._categories.append(clean)
        tok = (key_token or self._next_available_key()).upper()
        if tok:
            try:
                tok = validate_keybind(tok, existing_map=self._category_keys)
                self._category_keys[clean] = tok
            except ValueError:
                pass
        self._category_colors[clean] = CATEGORY_COLOR_PRESETS[
            (len(self._categories) - 1) % len(CATEGORY_COLOR_PRESETS)
        ][1]
        self.inline_box.clear_and_reset(self._next_available_key())
        self._refresh_chips(animate_category=clean)

    def _on_chip_key_clicked(self, category: str) -> None:
        ov = KeyCaptureOverlay(
            category,
            existing_map=self._setup_existing_key_map(),
            ignore_category=category,
            allow_del=False,
            parent=self.window(),
        )
        ov.key_captured.connect(lambda tok, c=category: self._apply_chip_key(c, tok))
        self.overlay_requested.emit(ov)

    def _apply_chip_key(self, category: str, token: str) -> None:
        self._category_keys[category] = token
        self._refresh_chips()

    def _on_chip_color_clicked(self, category: str) -> None:
        ov = _ColorPickerOverlay(
            tr("sorting.dialog.color_title", name=category),
            self._category_colors.get(category, DEFAULT_CATEGORY_COLOR),
            parent=self.window(),
        )
        ov.color_selected.connect(lambda hx, c=category: self._apply_chip_color(c, hx))
        self.overlay_requested.emit(ov)

    def _apply_chip_color(self, category: str, hex_c: str) -> None:
        self._category_colors[category] = hex_c
        self._refresh_chips()

    def _on_chip_remove(self, category: str) -> None:
        self._categories = [c for c in self._categories if c != category]
        self._category_keys.pop(category, None)
        self._category_colors.pop(category, None)
        self._refresh_chips()

    def _on_start_clicked(self) -> None:
        if self._existing_match is not None:
            self.resume_existing_requested.emit(self._existing_match.id)
            return
        folder = self.folder_edit.text().strip()
        if not folder or not os.path.isdir(folder):
            self.error_lbl.setText(tr("err.invalid_folder"))
            self.error_lbl.show()
            return
        pending_inline = self.inline_box.name_input.text().strip()
        if pending_inline:
            self._on_inline_add_submitted(pending_inline, self.inline_box.key_token)
        name = self.name_edit.text().strip() or Path(folder).name or "Session"
        meta = {}
        for cat in self._categories:
            entry: Dict[str, str] = {}
            if cat in self._category_keys:
                entry["key"] = self._category_keys[cat]
            if cat in self._category_colors:
                entry["color"] = self._category_colors[cat]
            if entry:
                meta[cat] = entry
        self.error_lbl.hide()
        self.session_started.emit(
            name,
            folder,
            list(self._categories),
            bool(self.deep_search_cb.isChecked()),
            meta,
        )

    def refresh_theme(self) -> None:
        self.sidebar_expand_btn.setIcon(render_icon("sidebar_expand", 16, PALETTE["text_secondary"]))
        self.settings_btn.setIcon(render_icon("settings", 16, PALETTE["text_secondary"]))
        self.browse_btn.setIcon(render_icon("folder", 14, PALETTE["text_primary"]))
        self.load_subfolders_btn.setIcon(render_icon("folder_plus", 14, PALETTE["accent"]))
        self.heading_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {PALETTE['text_primary']};"
        )
        self.lead_lbl.setStyleSheet(f"font-size: 12px; color: {PALETTE['text_secondary']};")
        self.hint_lbl.setStyleSheet(f"font-size: 11px; color: {PALETTE['text_muted']};")
        self.inline_box.refresh_theme()
        self._refresh_chips()

    def refresh_texts(self) -> None:
        settings = self._settings_provider().normalized()
        self.sidebar_expand_btn.setToolTip(tr("sidebar.expand_tip"))
        self.settings_btn.setToolTip(tr("sorting.settings_tip"))
        self.heading_lbl.setText(tr("setup.heading"))
        self.lead_lbl.setText(tr("setup.lead"))
        self.src_lbl.setText(tr("setup.source_folder"))
        self.folder_edit.setPlaceholderText(tr("setup.folder_placeholder"))
        self.browse_btn.setText(tr("setup.browse"))
        self.deep_search_cb.setText(_escape_mnemonic(tr("setup.deep_search")))
        self.name_lbl.setText(tr("setup.session_name"))
        self.name_edit.setPlaceholderText(tr("setup.name_placeholder"))
        self.cats_lbl.setText(tr("setup.target_categories"))
        self.inline_box.refresh_texts()
        self.hint_lbl.setText(tr("setup.hint", trash=settings.trash_category.name))
        self.start_btn.setText(
            tr("setup.continue_session") if self._existing_match else tr("setup.start_sorting")
        )
        folder = self.folder_edit.text().strip()
        if folder and os.path.isdir(folder):
            sub_cnt = count_subfolders(folder)
            if sub_cnt > 0:
                self.load_subfolders_btn.setText(
                    _escape_mnemonic(tr("setup.load_subfolders", count=sub_cnt))
                )
        self._refresh_chips()


# ---------------------------------------------------------------------------
# Floating Console Deck Sorting View
# ---------------------------------------------------------------------------


class SortingView(QWidget):
    """Floating Console Deck sorting workspace with HUD Header, Canvas, Filmstrip, & Keycap Deck."""

    sidebar_expand_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    undo_requested = pyqtSignal()
    prev_requested = pyqtSignal()
    skip_requested = pyqtSignal()
    jump_requested = pyqtSignal(int)
    sort_changed = pyqtSignal(str)
    move_requested = pyqtSignal(str)
    trash_requested = pyqtSignal()
    new_category_requested = pyqtSignal()
    category_rename_requested = pyqtSignal(str)
    category_keybind_requested = pyqtSignal(str)
    category_color_requested = pyqtSignal(str)
    category_revert_requested = pyqtSignal(str)
    metadata_clicked = pyqtSignal()
    gallery_toggle_requested = pyqtSignal()
    gallery_item_clicked = pyqtSignal(int)
    gallery_preview_requested = pyqtSignal(int)
    gallery_bulk_move_requested = pyqtSignal(list, str)
    gallery_width_committed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("SortingRoot")
        self._category_buttons: Dict[str, CategoryKeycapButton] = {}
        self._trash_button: Optional[TrashKeycapButton] = None
        self._add_cat_btn: Optional[_DashedActionButton] = None
        self._current_metadata: Dict[str, object] = {}
        self._gallery_open: bool = True
        self._gallery_anim = ValueAnimator(float(SessionGallerySidebar.DEFAULT_WIDTH), parent=self)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 10, 14, 10)
        root.setSpacing(6)

        # 1. Floating Top HUD Bar
        self.header = QFrame(self)
        self.header.setObjectName("SortingHeader")
        self.header.setFixedHeight(44)
        hdr = QHBoxLayout(self.header)
        hdr.setContentsMargins(10, 4, 10, 4)
        hdr.setSpacing(8)

        self.sidebar_expand_btn = QPushButton(self.header)
        self.sidebar_expand_btn.setObjectName("GhostIconButton")
        self.sidebar_expand_btn.setFixedSize(30, 30)
        self.sidebar_expand_btn.setIcon(render_icon("sidebar_expand", 15, PALETTE["text_secondary"]))
        self.sidebar_expand_btn.setToolTip(tr("sidebar.expand_tip"))
        self.sidebar_expand_btn.clicked.connect(self.sidebar_expand_requested.emit)
        self.sidebar_expand_btn.hide()
        hdr.addWidget(self.sidebar_expand_btn)

        self.filename_lbl = QLabel("", self.header)
        self.filename_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.filename_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {PALETTE['text_primary']}; background: transparent;"
        )
        self.filename_lbl.setMaximumWidth(190)
        hdr.addWidget(self.filename_lbl)

        self.props_lbl = QLabel("", self.header)
        self.props_lbl.setTextFormat(Qt.TextFormat.PlainText)
        self.props_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; color: {PALETTE['text_muted']}; background: transparent;"
        )
        self.props_lbl.hide()
        hdr.addWidget(self.props_lbl)

        self.meta_capsule = _MetadataHeaderChip(self.header)
        self.meta_capsule.clicked.connect(self.metadata_clicked.emit)
        self.meta_capsule.hover_entered.connect(self._show_metadata_hover)
        self.meta_capsule.hover_left.connect(self._hide_metadata_hover)
        hdr.addWidget(self.meta_capsule)

        hdr.addStretch(1)

        self.counter_pill = CounterPill(self.header)
        self.counter_pill.jump_requested.connect(self.jump_requested.emit)
        hdr.addWidget(self.counter_pill)

        self.sort_by_lbl = QLabel(tr("sorting.sort_by"), self.header)
        self.sort_by_lbl.setStyleSheet(
            f"font-size: 12px; color: {PALETTE['text_secondary']}; background: transparent;"
        )
        hdr.addWidget(self.sort_by_lbl)

        self.sort_combo = QComboBox(self.header)
        self.sort_combo.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self._populate_sort_combo()
        self.sort_combo.currentIndexChanged.connect(self._on_sort_combo_changed)
        hdr.addWidget(self.sort_combo)

        self.undo_btn = QPushButton(tr("sorting.undo"), self.header)
        self.undo_btn.setIcon(render_icon("undo", 13, PALETTE["text_primary"]))
        self.undo_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.undo_btn.setToolTip(tr("sorting.undo_tip"))
        self.undo_btn.clicked.connect(self.undo_requested.emit)
        hdr.addWidget(self.undo_btn)

        self.settings_btn = QPushButton(self.header)
        self.settings_btn.setObjectName("GhostIconButton")
        self.settings_btn.setFixedSize(30, 30)
        self.settings_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.settings_btn.setIcon(render_icon("settings", 15, PALETTE["text_secondary"]))
        self.settings_btn.setToolTip(tr("sorting.settings_tip"))
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        hdr.addWidget(self.settings_btn)

        self.gallery_toggle_btn = QPushButton(self.header)
        self.gallery_toggle_btn.setObjectName("GhostIconButton")
        self.gallery_toggle_btn.setFixedSize(30, 30)
        self.gallery_toggle_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.gallery_toggle_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.gallery_toggle_btn.setToolTip(tr("gallery.toggle_tip"))
        self.gallery_toggle_btn.clicked.connect(self.gallery_toggle_requested.emit)
        hdr.addWidget(self.gallery_toggle_btn)

        root.addWidget(self.header)

        # Horizontal Body Below Full-Width Header: [Center Stage | Right Gallery]
        self.body_host = QWidget(self)
        body_row = QHBoxLayout(self.body_host)
        body_row.setContentsMargins(0, 0, 0, 0)
        body_row.setSpacing(10)

        self.center_stage = QWidget(self.body_host)
        self.center_stage.setMinimumWidth(SessionGallerySidebar.MIN_CENTER_WIDTH)
        stage_col = QVBoxLayout(self.center_stage)
        stage_col.setContentsMargins(0, 0, 0, 0)
        stage_col.setSpacing(6)

        # 2. Central Viewport Canvas
        self.canvas = PhotoCanvas(self.center_stage)
        self.canvas.setObjectName("PhotoCanvasFrame")
        stage_col.addWidget(self.canvas, 1)

        # 3. Compact Centered 5-Slot Filmstrip Tray
        self.filmstrip = FilmstripBar(self.center_stage)
        stage_col.addWidget(self.filmstrip)

        # 4. Floating Keycap Console Deck
        self.category_bar = QFrame(self.center_stage)
        self.category_bar.setObjectName("CategoryBarContainer")
        deck_row = QHBoxLayout(self.category_bar)
        deck_row.setContentsMargins(10, 8, 10, 8)
        deck_row.setSpacing(10)

        self.prev_btn = QPushButton(tr("sorting.prev"), self.category_bar)
        self.prev_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.prev_btn.setIcon(render_icon("chevron_left", 14, PALETTE["text_secondary"]))
        self.prev_btn.setFixedHeight(38)
        self.prev_btn.setToolTip(tr("sorting.prev_tip"))
        self.prev_btn.clicked.connect(self.prev_requested.emit)
        deck_row.addWidget(self.prev_btn)

        self.chips_host = QWidget(self.category_bar)
        self.chips_host.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.category_flow = CenteredFlowLayout(self.chips_host, h_spacing=8, v_spacing=8)
        self.category_flow.setContentsMargins(4, 0, 4, 0)
        deck_row.addWidget(self.chips_host, 1)

        self.skip_btn = QPushButton(tr("sorting.next"), self.category_bar)
        self.next_btn = self.skip_btn
        self.skip_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.skip_btn.setIcon(render_icon("chevron_right", 14, PALETTE["text_secondary"]))
        self.skip_btn.setLayoutDirection(Qt.LayoutDirection.RightToLeft)
        self.skip_btn.setFixedHeight(38)
        self.skip_btn.setToolTip(tr("sorting.skip_tip"))
        self.skip_btn.clicked.connect(self.skip_requested.emit)
        deck_row.addWidget(self.skip_btn)

        stage_col.addWidget(self.category_bar)
        body_row.addWidget(self.center_stage, 1)

        # 5. Resizable Collapsible Right Session Gallery
        self.gallery_sidebar = SessionGallerySidebar(self.body_host)
        self.gallery_sidebar.setFixedWidth(SessionGallerySidebar.DEFAULT_WIDTH)
        self.gallery_sidebar.item_clicked.connect(self.gallery_item_clicked.emit)
        self.gallery_sidebar.preview_requested.connect(self.gallery_preview_requested.emit)
        self.gallery_sidebar.bulk_move_requested.connect(self.gallery_bulk_move_requested.emit)
        self.gallery_sidebar.collapse_requested.connect(self.gallery_toggle_requested.emit)
        self.gallery_sidebar.width_committed.connect(self.gallery_width_committed.emit)
        body_row.addWidget(self.gallery_sidebar)

        root.addWidget(self.body_host, 1)

        # Floating hover metadata readout card parented to SortingView
        self.meta_hover_card = _MetadataHoverLayer(self)
        self._sync_gallery_toggle_style()

    def is_gallery_open(self) -> bool:
        return bool(self._gallery_open)

    def _compute_max_gallery_width(self) -> int:
        avail_body = (
            self.body_host.width()
            if self.body_host.width() > 0
            else max(740, self.width() - 28)
        )
        return max(
            SessionGallerySidebar.MIN_WIDTH,
            min(
                SessionGallerySidebar.MAX_WIDTH,
                avail_body - SessionGallerySidebar.MIN_CENTER_WIDTH - 10,
            ),
        )

    def _sync_gallery_toggle_style(self) -> None:
        self.gallery_toggle_btn.setProperty("active", "true" if self._gallery_open else "false")
        self.gallery_toggle_btn.style().unpolish(self.gallery_toggle_btn)
        self.gallery_toggle_btn.style().polish(self.gallery_toggle_btn)
        self.gallery_toggle_btn.setIcon(
            render_icon(
                "gallery",
                15,
                PALETTE["accent"] if self._gallery_open else PALETTE["text_secondary"],
            )
        )

    def apply_gallery_preferences(
        self, is_open: bool, width: int, animate: bool = False
    ) -> None:
        self._gallery_open = bool(is_open)
        max_w = self._compute_max_gallery_width()
        self.gallery_sidebar.set_max_allowed_width(max_w)
        target_w = self.gallery_sidebar.set_preferred_width(width)
        self._sync_gallery_toggle_style()

        if not self._gallery_open:
            self.gallery_sidebar.clear_selection(sync_to_active=True)

        if animate and self.isVisible():
            animate_sidebar_width(
                self._gallery_anim,
                self.gallery_sidebar,
                expand=self._gallery_open,
                target_width=target_w,
                duration_ms=180,
            )
        else:
            self._gallery_anim.stop_silent(float(target_w if self._gallery_open else 0))
            if self._gallery_open:
                self.gallery_sidebar.setFixedWidth(target_w)
                self.gallery_sidebar.show()
            else:
                self.gallery_sidebar.setFixedWidth(0)
                self.gallery_sidebar.hide()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        max_w = self._compute_max_gallery_width()
        self.gallery_sidebar.set_max_allowed_width(max_w)
        if self._gallery_open and self._gallery_anim.state() != ValueAnimator.State.Running:
            clamped = self.gallery_sidebar.clamp_width(self.gallery_sidebar.preferred_width())
            if self.gallery_sidebar.width() != clamped:
                self.gallery_sidebar.setFixedWidth(clamped)

    def _populate_sort_combo(self) -> None:
        cur_data = self.sort_combo.currentData()
        self.sort_combo.blockSignals(True)
        self.sort_combo.clear()
        for mode in SORT_MODES:
            self.sort_combo.addItem(tr(f"sort.{mode}"), mode)
        if cur_data:
            idx = self.sort_combo.findData(cur_data)
            if idx >= 0:
                self.sort_combo.setCurrentIndex(idx)
        self.sort_combo.blockSignals(False)

    def _on_sort_combo_changed(self, idx: int) -> None:
        mode = self.sort_combo.itemData(idx)
        if mode:
            self.sort_changed.emit(str(mode))

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.sidebar_expand_btn.setVisible(bool(collapsed))

    def set_sort_mode(self, mode: str) -> None:
        self.sort_combo.blockSignals(True)
        idx = self.sort_combo.findData(mode)
        if idx >= 0:
            self.sort_combo.setCurrentIndex(idx)
        self.sort_combo.blockSignals(False)

    def set_filename(self, filename: str) -> None:
        fm = QFontMetrics(self.filename_lbl.font())
        elided = fm.elidedText(filename or tr("sorting.no_media"), Qt.TextElideMode.ElideMiddle, 175)
        self.filename_lbl.setText(elided)
        self.filename_lbl.setToolTip(filename or "")

    def set_metadata(self, meta: Dict[str, object], visible_fields: Sequence[str]) -> None:
        self._current_metadata = dict(meta or {})
        dim_raw = str(self._current_metadata.get("dimensions") or "").replace(" px", "").strip()
        size_raw = str(self._current_metadata.get("size") or "").strip()
        props_parts = [p for p in (dim_raw, size_raw) if p]
        props_txt = " · ".join(props_parts)
        self.props_lbl.setText(props_txt)
        self.props_lbl.setVisible(bool(props_txt))

        parts: List[str] = []
        for field in visible_fields:
            val = str(self._current_metadata.get(field) or "").strip()
            if val:
                parts.append(val)
        self.meta_capsule.set_summary(parts)
        if self.meta_hover_card.isVisible():
            self.meta_hover_card.set_metadata(self._current_metadata)

    def _show_metadata_hover(self) -> None:
        if not self._current_metadata:
            return
        self.meta_hover_card.set_metadata(self._current_metadata)
        pos = self.meta_capsule.mapTo(self, QPoint(0, self.meta_capsule.height() + 8))
        x = max(14, min(self.width() - self.meta_hover_card.width() - 14, pos.x()))
        y = max(56, pos.y())
        self.meta_hover_card.show_animated(QPoint(x, y))

    def _hide_metadata_hover(self) -> None:
        self.meta_hover_card.hide_immediate()

    def set_navigation_state(
        self,
        can_prev: bool,
        can_skip: bool,
        can_undo: bool,
    ) -> None:
        self.prev_btn.setEnabled(bool(can_prev))
        self.prev_btn.setToolTip(tr("sorting.prev_tip") if can_prev else tr("sorting.prev_first_tip"))
        self.skip_btn.setEnabled(bool(can_skip))
        self.undo_btn.setEnabled(bool(can_undo))
        self.undo_btn.setToolTip(tr("sorting.undo_tip") if can_undo else tr("sorting.undo_empty_tip"))

    def update_categories(
        self,
        categories: List[str],
        counts: Dict[str, int],
        keybinds: Dict[str, str],
        colors: Dict[str, str],
        trash_name: str = "_Buang",
        trash_key: str = "DEL",
        trash_color: str = DEFAULT_TRASH_COLOR,
        active_category: Optional[str] = None,
    ) -> None:
        """Update Console Deck in-place: [0] Pinned Trash -> Categories -> [+ New Category]."""
        signature_changed = (
            self._trash_button is None
            or self._add_cat_btn is None
            or list(self._category_buttons.keys()) != list(categories)
        )
        if signature_changed:
            while self.category_flow.count():
                item = self.category_flow.takeAt(0)
                w = item.widget()
                if w is not None:
                    w.hide()
                    w.setParent(None)
                    w.deleteLater()
            self._category_buttons.clear()

            # Index 0: Pinned Leftmost Trash Keycap
            tbtn = TrashKeycapButton(
                category=trash_name,
                key_label=trash_key,
                count=counts.get(trash_name, 0),
                accent_color=trash_color,
                parent=self.chips_host,
            )
            tbtn.clicked.connect(self.trash_requested.emit)
            self._trash_button = tbtn
            self.category_flow.addWidget(tbtn)

            # Index 1..N: User categories
            for idx, cat in enumerate(categories):
                cbtn = CategoryKeycapButton(
                    category=cat,
                    key_label=keybinds.get(cat, ""),
                    count=counts.get(cat, 0),
                    accent_color=colors.get(
                        cat, CATEGORY_COLOR_PRESETS[idx % len(CATEGORY_COLOR_PRESETS)][1]
                    ),
                    parent=self.chips_host,
                )
                cbtn.clicked.connect(lambda _=False, c=cat: self.move_requested.emit(c))
                cbtn.rename_requested.connect(self.category_rename_requested.emit)
                cbtn.keybind_change_requested.connect(self.category_keybind_requested.emit)
                cbtn.color_change_requested.connect(self.category_color_requested.emit)
                cbtn.revert_requested.connect(self.category_revert_requested.emit)
                self._category_buttons[cat] = cbtn
                self.category_flow.addWidget(cbtn)

            # Index N+1: Custom-painted dashed '+ New Category'
            add_btn = _DashedActionButton(self.chips_host)
            add_btn.clicked.connect(self.new_category_requested.emit)
            self._add_cat_btn = add_btn
            self.category_flow.addWidget(add_btn)
        else:
            assert self._trash_button is not None
            self._trash_button.set_trash_spec(trash_name, trash_key, trash_color)
            self._trash_button.set_count(counts.get(trash_name, 0))
            for idx, cat in enumerate(categories):
                btn = self._category_buttons[cat]
                btn.set_keybind(keybinds.get(cat, ""))
                btn.set_count(counts.get(cat, 0))
                btn.set_accent_color(
                    colors.get(
                        cat, CATEGORY_COLOR_PRESETS[idx % len(CATEGORY_COLOR_PRESETS)][1]
                    )
                )

        # Highlight active category LED when currently viewed item is sorted
        assert self._trash_button is not None
        self._trash_button.set_active(active_category == trash_name)
        for cat, btn in self._category_buttons.items():
            btn.set_active(active_category == cat)

    def keycap_for(self, category: str, trash_name: str = "_Buang") -> Optional[QPushButton]:
        if category == trash_name:
            return self._trash_button
        return self._category_buttons.get(category)

    def refresh_theme(self) -> None:
        self.canvas.refresh_theme()
        self.filmstrip.refresh_theme()
        self.gallery_sidebar.refresh_theme()
        self.meta_capsule.refresh_theme()
        self.sidebar_expand_btn.setIcon(render_icon("sidebar_expand", 15, PALETTE["text_secondary"]))
        self.undo_btn.setIcon(render_icon("undo", 13, PALETTE["text_primary"]))
        self.settings_btn.setIcon(render_icon("settings", 15, PALETTE["text_secondary"]))
        self._sync_gallery_toggle_style()
        self.prev_btn.setIcon(render_icon("chevron_left", 14, PALETTE["text_secondary"]))
        self.skip_btn.setIcon(render_icon("chevron_right", 14, PALETTE["text_secondary"]))
        self.filename_lbl.setStyleSheet(
            f"font-size: 13px; font-weight: 600; color: {PALETTE['text_primary']}; background: transparent;"
        )
        self.props_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 11px; color: {PALETTE['text_muted']}; background: transparent;"
        )
        self.sort_by_lbl.setStyleSheet(
            f"font-size: 12px; color: {PALETTE['text_secondary']}; background: transparent;"
        )
        if self._trash_button:
            self._trash_button.update()
        for btn in self._category_buttons.values():
            btn.update()
        if self._add_cat_btn:
            self._add_cat_btn.update()

    def refresh_texts(self) -> None:
        self.sidebar_expand_btn.setToolTip(tr("sidebar.expand_tip"))
        self.sort_by_lbl.setText(tr("sorting.sort_by"))
        self._populate_sort_combo()
        self.undo_btn.setText(tr("sorting.undo"))
        self.undo_btn.setToolTip(tr("sorting.undo_tip"))
        self.settings_btn.setToolTip(tr("sorting.settings_tip"))
        self.gallery_toggle_btn.setToolTip(tr("gallery.toggle_tip"))
        self.prev_btn.setText(tr("sorting.prev"))
        self.skip_btn.setText(tr("sorting.next"))
        self.skip_btn.setToolTip(tr("sorting.skip_tip"))
        self.counter_pill.refresh_texts()
        self.meta_capsule.refresh_texts()
        self.canvas.refresh_texts()
        self.filmstrip.refresh_texts()
        self.gallery_sidebar.refresh_texts()
        if self._add_cat_btn:
            self._add_cat_btn.refresh_texts()


# ---------------------------------------------------------------------------
# Session Complete View
# ---------------------------------------------------------------------------


class SessionCompleteView(QWidget):
    """Summary screen shown when every item in a session has been categorized."""

    sidebar_expand_requested = pyqtSignal()
    settings_requested = pyqtSignal()
    open_folder_requested = pyqtSignal()
    undo_last_requested = pyqtSignal()
    delete_session_requested = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("CompleteRoot")
        self._last_session: Optional[SessionState] = None
        self._last_counts: Dict[str, int] = {}
        self._last_trash_name: str = "_Buang"

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 14, 24, 24)
        root.setSpacing(12)

        top_bar = QHBoxLayout()
        self.sidebar_expand_btn = QPushButton(self)
        self.sidebar_expand_btn.setObjectName("GhostIconButton")
        self.sidebar_expand_btn.setFixedSize(32, 32)
        self.sidebar_expand_btn.setIcon(render_icon("sidebar_expand", 16, PALETTE["text_secondary"]))
        self.sidebar_expand_btn.clicked.connect(self.sidebar_expand_requested.emit)
        self.sidebar_expand_btn.hide()
        top_bar.addWidget(self.sidebar_expand_btn)
        top_bar.addStretch(1)
        self.settings_btn = QPushButton(self)
        self.settings_btn.setObjectName("GhostIconButton")
        self.settings_btn.setFixedSize(32, 32)
        self.settings_btn.setIcon(render_icon("settings", 16, PALETTE["text_secondary"]))
        self.settings_btn.clicked.connect(self.settings_requested.emit)
        top_bar.addWidget(self.settings_btn)
        root.addLayout(top_bar)

        center = QHBoxLayout()
        center.addStretch(1)
        self.card = QFrame(self)
        self.card.setObjectName("ElevatedCard")
        self.card.setMaximumWidth(640)
        self.card.setMinimumWidth(460)
        col = QVBoxLayout(self.card)
        col.setContentsMargins(28, 26, 28, 26)
        col.setSpacing(14)

        badge = QLabel(self.card)
        badge.setPixmap(render_icon("check_circle", 34, PALETTE["success"]).pixmap(34, 34))
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        col.addWidget(badge)
        self._badge_lbl = badge

        self.title_lbl = QLabel(tr("complete.title"), self.card)
        self.title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.title_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {PALETTE['text_primary']};"
        )
        col.addWidget(self.title_lbl)

        self.sub_lbl = QLabel("", self.card)
        self.sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.sub_lbl.setWordWrap(True)
        self.sub_lbl.setStyleSheet(f"font-size: 13px; color: {PALETTE['text_secondary']};")
        col.addWidget(self.sub_lbl)

        self.breakdown_host = QWidget(self.card)
        self.breakdown_layout = QVBoxLayout(self.breakdown_host)
        self.breakdown_layout.setContentsMargins(0, 4, 0, 4)
        self.breakdown_layout.setSpacing(6)
        col.addWidget(self.breakdown_host)

        actions = QHBoxLayout()
        self.open_btn = QPushButton(tr("complete.open_folder"), self.card)
        self.open_btn.setObjectName("PrimaryButton")
        self.open_btn.setIcon(render_icon("folder", 14, PALETTE["accent_text_on_fill"]))
        self.open_btn.clicked.connect(self.open_folder_requested.emit)
        actions.addWidget(self.open_btn)

        self.undo_btn = QPushButton(tr("complete.undo_last"), self.card)
        self.undo_btn.setIcon(render_icon("undo", 14, PALETTE["text_primary"]))
        self.undo_btn.clicked.connect(self.undo_last_requested.emit)
        actions.addWidget(self.undo_btn)

        self.delete_btn = QPushButton(tr("complete.delete_session"), self.card)
        self.delete_btn.setObjectName("DangerButton")
        self.delete_btn.setIcon(render_icon("trash", 14, PALETTE["danger"]))
        self.delete_btn.clicked.connect(self.delete_session_requested.emit)
        actions.addWidget(self.delete_btn)
        col.addLayout(actions)

        center.addWidget(self.card, 1)
        center.addStretch(1)
        root.addLayout(center, 1)

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        self.sidebar_expand_btn.setVisible(bool(collapsed))

    def set_summary(
        self,
        session: SessionState,
        counts: Dict[str, int],
        trash_name: str = "_Buang",
    ) -> None:
        self._last_session = session
        self._last_counts = dict(counts)
        self._last_trash_name = trash_name
        total = len(session.timeline)
        self.sub_lbl.setText(tr("complete.subtitle", name=session.name, total=total))
        self.undo_btn.setEnabled(bool(session.undo_stack))

        while self.breakdown_layout.count():
            item = self.breakdown_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.hide()
                w.setParent(None)
                w.deleteLater()

        order = [trash_name] + list(session.categories)
        for cat in order:
            cnt = counts.get(cat, 0)
            row_f = QFrame(self.breakdown_host)
            row_f.setStyleSheet(
                f"QFrame {{ background-color: {PALETTE['bg_input']}; border: 1px solid {PALETTE['border_subtle']}; border-radius: 7px; }}"
            )
            r = QHBoxLayout(row_f)
            r.setContentsMargins(12, 7, 12, 7)
            c_lbl = QLabel(cat, row_f)
            c_lbl.setTextFormat(Qt.TextFormat.PlainText)
            fg = PALETTE["danger"] if cat == trash_name else PALETTE["text_primary"]
            c_lbl.setStyleSheet(f"font-size: 12px; font-weight: 600; color: {fg}; border: none; background: transparent;")
            r.addWidget(c_lbl, 1)
            n_lbl = QLabel(tr("complete.item_count", count=cnt), row_f)
            n_lbl.setStyleSheet(
                f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 12px; color: {PALETTE['text_secondary']}; border: none; background: transparent;"
            )
            r.addWidget(n_lbl)
            self.breakdown_layout.addWidget(row_f)

    def refresh_theme(self) -> None:
        self._badge_lbl.setPixmap(render_icon("check_circle", 34, PALETTE["success"]).pixmap(34, 34))
        self.sidebar_expand_btn.setIcon(render_icon("sidebar_expand", 16, PALETTE["text_secondary"]))
        self.settings_btn.setIcon(render_icon("settings", 16, PALETTE["text_secondary"]))
        self.open_btn.setIcon(render_icon("folder", 14, PALETTE["accent_text_on_fill"]))
        self.undo_btn.setIcon(render_icon("undo", 14, PALETTE["text_primary"]))
        self.delete_btn.setIcon(render_icon("trash", 14, PALETTE["danger"]))
        self.title_lbl.setStyleSheet(
            f"font-size: 20px; font-weight: 700; color: {PALETTE['text_primary']};"
        )
        self.sub_lbl.setStyleSheet(f"font-size: 13px; color: {PALETTE['text_secondary']};")
        if self._last_session is not None:
            self.set_summary(self._last_session, self._last_counts, self._last_trash_name)

    def refresh_texts(self) -> None:
        self.title_lbl.setText(tr("complete.title"))
        self.open_btn.setText(tr("complete.open_folder"))
        self.undo_btn.setText(tr("complete.undo_last"))
        self.delete_btn.setText(tr("complete.delete_session"))
        if self._last_session is not None:
            self.set_summary(self._last_session, self._last_counts, self._last_trash_name)


# Backward-compatible aliases so external callers remain seamless
KeyCaptureDialog = KeyCaptureOverlay
SettingsDialog = SettingsOverlay
