"""Resizable viewport-aware Right Session Gallery with range selection & bulk categorize."""
from __future__ import annotations

import os
from collections import OrderedDict
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from PyQt6.QtCore import (
    QPoint,
    QPointF,
    QRect,
    QRectF,
    QSize,
    Qt,
    QThreadPool,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QMouseEvent,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from core.session_manager import (
    DEFAULT_CATEGORY_COLOR,
    DEFAULT_TRASH_COLOR,
    TimelineEntry,
    is_video_file,
)
from ui.filmstrip import _ThumbPreloadTask, _ThumbSignalBridge
from ui.i18n import tr
from ui.icons import draw_icon, render_icon
from ui.theme import MONO_FONT_FAMILY, PALETTE, UI_FONT_FAMILY


def _alpha_color(hex_color: str, alpha: int) -> QColor:
    c = QColor(hex_color)
    if not c.isValid():
        c = QColor("#F59E0B")
    c.setAlpha(max(0, min(255, int(alpha))))
    return c


class _GalleryResizeGrip(QWidget):
    """5px left-edge interactive drag handle for smooth live width resizing."""

    drag_delta = pyqtSignal(int)
    drag_finished = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(5)
        self.setCursor(Qt.CursorShape.SizeHorCursor)
        self.setMouseTracking(True)
        self._dragging = False
        self._hovered = False
        self._last_global_x = 0

    def enterEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hovered = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_global_x = int(round(event.globalPosition().x()))
            self.update()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging:
            gx = int(round(event.globalPosition().x()))
            dx = gx - self._last_global_x
            if dx != 0:
                self._last_global_x = gx
                self.drag_delta.emit(dx)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        if self._dragging and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.update()
            self.drag_finished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if not (self._hovered or self._dragging):
            return
        painter = QPainter(self)
        c = QColor(PALETTE["accent"] if self._dragging else PALETTE["border_strong"])
        c.setAlpha(210 if self._dragging else 170)
        painter.fillRect(QRectF(1.0, 6.0, 2.0, max(12.0, float(self.height()) - 12.0)), c)
        painter.end()


class _GalleryGridCanvas(QWidget):
    """Viewport-aware virtualized thumbnail grid supporting 1,000+ photos at 60 FPS."""

    item_activated = pyqtSignal(int, object)  # (timeline_index, KeyboardModifiers)
    context_requested = pyqtSignal(int, QPoint)

    MIN_CARD_W = 104
    CARD_GAP = 8
    GRID_PAD = 10
    THUMB_CACHE_CAP = 400

    def __init__(self, scroll_area: QScrollArea, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scroll_area = scroll_area
        self.setMouseTracking(True)

        self._timeline: List[TimelineEntry] = []
        self._visible_indices: List[int] = []
        self._visible_pos_map: Dict[int, int] = {}
        self._root_folder: str = ""
        self._active_index: int = 0
        self._selected_indices: Set[int] = set()
        self._category_colors: Dict[str, str] = {}
        self._trash_name: str = "_Buang"
        self._trash_color: str = DEFAULT_TRASH_COLOR
        self._skipped_files: Set[str] = set()

        self._cols: int = 2
        self._card_w: int = 116
        self._card_h: int = 87
        self._hovered_idx: Optional[int] = None

        self._generation: int = 0
        self._thumb_cache: OrderedDict[str, QPixmap] = OrderedDict()
        self._pending_paths: Set[str] = set()
        self._signal_bridge = _ThumbSignalBridge(self)
        self._signal_bridge.loaded.connect(self._on_thumb_ready)
        self._thread_pool = QThreadPool.globalInstance()

    def set_state(
        self,
        timeline: Sequence[TimelineEntry],
        visible_indices: Sequence[int],
        active_index: int,
        selected_indices: Set[int],
        root_folder: str,
        category_colors: Dict[str, str],
        trash_name: str,
        trash_color: str,
        skipped_files: Set[str],
    ) -> None:
        self._timeline = list(timeline)
        self._visible_indices = list(visible_indices)
        self._visible_pos_map = {tidx: pos for pos, tidx in enumerate(self._visible_indices)}
        self._active_index = active_index
        self._selected_indices = set(selected_indices)
        self._root_folder = root_folder
        self._category_colors = dict(category_colors)
        self._trash_name = trash_name or "_Buang"
        self._trash_color = trash_color or DEFAULT_TRASH_COLOR
        self._skipped_files = set(skipped_files)
        self._recompute_metrics()
        self.request_visible_thumbs()
        self.update()

    def update_selection_only(self, active_index: int, selected_indices: Set[int]) -> None:
        self._active_index = active_index
        self._selected_indices = set(selected_indices)
        self.update()

    def update_entry_inplace(
        self,
        timeline_index: int,
        entry: Optional[TimelineEntry],
        active_index: int,
        selected_indices: Set[int],
        skipped_files: Set[str],
    ) -> None:
        if entry is not None and 0 <= timeline_index < len(self._timeline):
            self._timeline[timeline_index] = entry
        self._active_index = active_index
        self._selected_indices = set(selected_indices)
        self._skipped_files = set(skipped_files)
        self.update()

    def clear_cache(self) -> None:
        self._generation += 1
        self._thumb_cache.clear()
        self._pending_paths.clear()

    def column_count(self) -> int:
        return self._cols

    def _resolve_entry_path(self, entry: TimelineEntry) -> str:
        if not self._root_folder:
            return ""
        cand = os.path.join(self._root_folder, entry.rel_path)
        if os.path.exists(cand):
            return cand
        if entry.origin_rel_path:
            alt = os.path.join(self._root_folder, entry.origin_rel_path)
            if os.path.exists(alt):
                return alt
        return cand

    def _recompute_metrics(self) -> None:
        vp_w = max(140, self._scroll_area.viewport().width())
        usable_w = max(self.MIN_CARD_W, vp_w - 2 * self.GRID_PAD)
        cols = max(1, (usable_w + self.CARD_GAP) // (self.MIN_CARD_W + self.CARD_GAP))
        card_w = max(84, (usable_w - (cols - 1) * self.CARD_GAP) // cols)
        card_h = max(64, int(round(card_w * 0.75)))

        self._cols = cols
        self._card_w = card_w
        self._card_h = card_h

        total = len(self._visible_indices)
        rows = (total + cols - 1) // cols if total > 0 else 0
        total_h = (
            max(120, 2 * self.GRID_PAD + rows * card_h + max(0, rows - 1) * self.CARD_GAP)
            if rows > 0
            else max(140, self._scroll_area.viewport().height() - 4)
        )
        self.setFixedWidth(vp_w)
        self.setMinimumHeight(total_h)
        self.resize(vp_w, total_h)

    def card_rect_for_pos(self, pos: int) -> QRect:
        row = pos // self._cols
        col = pos % self._cols
        x = self.GRID_PAD + col * (self._card_w + self.CARD_GAP)
        y = self.GRID_PAD + row * (self._card_h + self.CARD_GAP)
        return QRect(x, y, self._card_w, self._card_h)

    def card_rect_for_timeline_index(self, timeline_index: int) -> Optional[QRect]:
        pos = self._visible_pos_map.get(timeline_index)
        if pos is None:
            return None
        return self.card_rect_for_pos(pos)

    def index_at_pos(self, pt: QPoint) -> Optional[int]:
        x = pt.x() - self.GRID_PAD
        y = pt.y() - self.GRID_PAD
        if x < 0 or y < 0:
            return None
        step_x = self._card_w + self.CARD_GAP
        step_y = self._card_h + self.CARD_GAP
        col = x // step_x
        row = y // step_y
        if col >= self._cols:
            return None
        if (x % step_x) > self._card_w or (y % step_y) > self._card_h:
            return None
        pos = row * self._cols + col
        if 0 <= pos < len(self._visible_indices):
            return self._visible_indices[pos]
        return None

    def _visible_row_bounds(self, buffer_rows: int = 2) -> Tuple[int, int]:
        total = len(self._visible_indices)
        if total == 0:
            return 0, -1
        scroll_y = self._scroll_area.verticalScrollBar().value()
        vp_h = max(100, self._scroll_area.viewport().height())
        step_y = self._card_h + self.CARD_GAP
        first_row = max(0, (scroll_y - self.GRID_PAD) // step_y - buffer_rows)
        last_row = min(
            (total - 1) // self._cols,
            (scroll_y + vp_h - self.GRID_PAD) // step_y + buffer_rows,
        )
        return first_row, last_row

    def request_visible_thumbs(self) -> None:
        if not self._visible_indices or not self._root_folder:
            return
        first_row, last_row = self._visible_row_bounds(buffer_rows=2)
        if last_row < first_row:
            return
        start_pos = first_row * self._cols
        end_pos = min(len(self._visible_indices), (last_row + 1) * self._cols)
        target_w = max(84, self._card_w)
        target_h = max(64, self._card_h)

        for pos in range(start_pos, end_pos):
            tidx = self._visible_indices[pos]
            if not (0 <= tidx < len(self._timeline)):
                continue
            entry = self._timeline[tidx]
            media_path = self._resolve_entry_path(entry)
            if not media_path or media_path in self._thumb_cache or media_path in self._pending_paths:
                continue
            if os.path.exists(media_path):
                self._pending_paths.add(media_path)
                task = _ThumbPreloadTask(
                    media_path,
                    self._generation,
                    target_w,
                    target_h,
                    self._signal_bridge,
                )
                self._thread_pool.start(task)

    def _on_thumb_ready(self, path_str: str, gen: int, img: QImage) -> None:
        self._pending_paths.discard(path_str)
        if gen != self._generation or img.isNull():
            return
        pm = QPixmap.fromImage(img)
        if pm.isNull():
            return
        self._thumb_cache[path_str] = pm
        self._thumb_cache.move_to_end(path_str)
        while len(self._thumb_cache) > self.THUMB_CACHE_CAP:
            self._thumb_cache.popitem(last=False)
        self.update()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        hit = self.index_at_pos(event.pos())
        if hit != self._hovered_idx:
            self._hovered_idx = hit
            self.setCursor(
                Qt.CursorShape.PointingHandCursor if hit is not None else Qt.CursorShape.ArrowCursor
            )
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        if self._hovered_idx is not None:
            self._hovered_idx = None
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # type: ignore[override]
        hit = self.index_at_pos(event.pos())
        if event.button() == Qt.MouseButton.LeftButton and hit is not None:
            self.item_activated.emit(hit, event.modifiers())
            event.accept()
            return
        if event.button() == Qt.MouseButton.RightButton and hit is not None:
            self.context_requested.emit(hit, self.mapToGlobal(event.pos()))
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        if not self._visible_indices:
            painter.setFont(QFont(UI_FONT_FAMILY, 11))
            painter.setPen(QColor(PALETTE["text_muted"]))
            painter.drawText(
                QRectF(14.0, 24.0, max(40.0, float(self.width()) - 28.0), 80.0),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                tr("gallery.empty_filtered"),
            )
            painter.end()
            return

        paint_rect = event.rect()
        first_row, last_row = self._visible_row_bounds(buffer_rows=0)
        start_pos = max(0, first_row * self._cols)
        end_pos = min(len(self._visible_indices), (last_row + 1) * self._cols)

        for pos in range(start_pos, end_pos):
            crect = self.card_rect_for_pos(pos)
            if not crect.intersects(paint_rect):
                continue
            tidx = self._visible_indices[pos]
            if not (0 <= tidx < len(self._timeline)):
                continue
            self._paint_card(painter, QRectF(crect), tidx, self._timeline[tidx])

        painter.end()

    def _paint_card(
        self,
        painter: QPainter,
        rect: QRectF,
        tidx: int,
        entry: TimelineEntry,
    ) -> None:
        radius = 7.0
        inner = rect.adjusted(1.0, 1.0, -1.0, -1.0)
        clip = QPainterPath()
        clip.addRoundedRect(inner, radius, radius)

        is_active = tidx == self._active_index
        is_selected = tidx in self._selected_indices
        is_hovered = tidx == self._hovered_idx
        is_video = is_video_file(entry.name)

        media_path = self._resolve_entry_path(entry)
        pix = self._thumb_cache.get(media_path) if media_path else None

        painter.save()
        painter.setClipPath(clip)
        if pix is not None and not pix.isNull():
            pw, ph = pix.width(), pix.height()
            if pw > 0 and ph > 0:
                rw, rh = inner.width(), inner.height()
                scale = max(rw / pw, rh / ph)
                sw, sh = pw * scale, ph * scale
                dx = inner.left() + (rw - sw) / 2.0
                dy = inner.top() + (rh - sh) / 2.0
                painter.drawPixmap(QRectF(dx, dy, sw, sh), pix, QRectF(0, 0, pw, ph))
        else:
            painter.fillRect(inner, QColor(PALETTE["bg_elevated"]))
            draw_icon(
                painter,
                "video" if is_video else "image",
                QRectF(inner.center().x() - 10.0, inner.center().y() - 10.0, 20.0, 20.0),
                PALETTE["text_muted"],
                stroke_width=1.4,
            )

        if is_selected:
            painter.fillRect(inner, _alpha_color(PALETTE["accent"], 44))
        elif is_hovered:
            painter.fillRect(inner, QColor(255, 255, 255, 18))

        # Top-left frame #index pill
        num_str = f"#{tidx + 1}"
        nf = QFont(MONO_FONT_FAMILY, 8, QFont.Weight.DemiBold)
        painter.setFont(nf)
        nw = float(QFontMetrics(nf).horizontalAdvance(num_str) + 8)
        nrect = QRectF(inner.left() + 5.0, inner.top() + 5.0, nw, 15.0)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(8, 10, 14, 195))
        painter.drawRoundedRect(nrect, 4.0, 4.0)
        painter.setPen(QColor("#F4F4F5"))
        painter.drawText(nrect, Qt.AlignmentFlag.AlignCenter, num_str)

        # Top-right [✓] badge when selected, else ▶ VID pill on video
        if is_selected:
            chk = QRectF(inner.right() - 22.0, inner.top() + 5.0, 17.0, 17.0)
            painter.setBrush(QColor(PALETTE["accent"]))
            painter.drawEllipse(chk)
            draw_icon(
                painter,
                "check",
                chk.adjusted(3.2, 3.2, -3.2, -3.2),
                PALETTE["accent_text_on_fill"],
                stroke_width=2.0,
            )
        elif is_video:
            vf = QFont(MONO_FONT_FAMILY, 7, QFont.Weight.Bold)
            painter.setFont(vf)
            vrect = QRectF(inner.right() - 33.0, inner.top() + 5.0, 28.0, 14.0)
            painter.setBrush(QColor(8, 10, 14, 195))
            painter.drawRoundedRect(vrect, 4.0, 4.0)
            painter.setPen(QColor(PALETTE["accent"]))
            painter.drawText(vrect, Qt.AlignmentFlag.AlignCenter, "▶ VID")

        # Bottom sorted category tag or skipped indicator
        if entry.status == "sorted" and entry.category:
            is_trash = entry.category.lower() == self._trash_name.lower()
            cat_hex = (
                self._trash_color
                if is_trash
                else self._category_colors.get(entry.category, DEFAULT_CATEGORY_COLOR)
            )
            strip_h = 18.0
            strip = QRectF(inner.left(), inner.bottom() - strip_h, inner.width(), strip_h)
            painter.fillRect(strip, QColor(10, 12, 16, 205))
            dot = QRectF(strip.left() + 6.0, strip.center().y() - 3.5, 7.0, 7.0)
            painter.setBrush(QColor(cat_hex))
            painter.drawEllipse(dot)
            cf = QFont(UI_FONT_FAMILY, 8, QFont.Weight.DemiBold)
            painter.setFont(cf)
            painter.setPen(QColor("#F4F4F5"))
            avail_w = max(20.0, strip.width() - 20.0)
            elided = QFontMetrics(cf).elidedText(
                entry.category, Qt.TextElideMode.ElideRight, int(avail_w)
            )
            painter.drawText(
                QRectF(strip.left() + 16.0, strip.top(), avail_w, strip_h),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                elided,
            )
        elif entry.name in self._skipped_files:
            sf = QFont(UI_FONT_FAMILY, 8, QFont.Weight.Medium)
            painter.setFont(sf)
            s_lbl = tr("filmstrip.skipped_tag")
            sw = float(min(inner.width() - 10.0, QFontMetrics(sf).horizontalAdvance(s_lbl) + 10.0))
            srect = QRectF(inner.left() + 5.0, inner.bottom() - 18.0, sw, 14.0)
            painter.setBrush(QColor(14, 16, 22, 195))
            painter.drawRoundedRect(srect, 4.0, 4.0)
            painter.setPen(QColor("#D4D4D8"))
            painter.drawText(srect, Qt.AlignmentFlag.AlignCenter, s_lbl)

        painter.restore()

        # Outer selection / active outline
        if is_selected:
            painter.setPen(QPen(QColor(PALETTE["accent"]), 2.5))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(inner, radius, radius)
        elif is_active:
            painter.setPen(QPen(QColor(PALETTE["accent"]), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(inner, radius, radius)
        else:
            b_color = QColor(PALETTE["border_strong"] if is_hovered else PALETTE["border"])
            painter.setPen(QPen(b_color, 1.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(inner, radius, radius)


class SessionGallerySidebar(QFrame):
    """Collapsible, drag-resizable Right Gallery Drawer with range multi-selection."""

    MIN_WIDTH = 180
    DEFAULT_WIDTH = 276
    MAX_WIDTH = 640
    MIN_CENTER_WIDTH = 460

    item_clicked = pyqtSignal(int)
    preview_requested = pyqtSignal(int)
    selection_changed = pyqtSignal(list)
    bulk_move_requested = pyqtSignal(list, str)
    collapse_requested = pyqtSignal()
    width_changed = pyqtSignal(int)
    width_committed = pyqtSignal(int)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("GallerySidebar")

        self._preferred_width: int = self.DEFAULT_WIDTH
        self._max_allowed_width: int = self.MAX_WIDTH
        self.filter_mode: str = "all"

        self._timeline: List[TimelineEntry] = []
        self._active_index: int = 0
        self._root_folder: str = ""
        self._categories: List[str] = []
        self._category_colors: Dict[str, str] = {}
        self._trash_name: str = "_Buang"
        self._trash_color: str = DEFAULT_TRASH_COLOR
        self._skipped_files: Set[str] = set()

        self._selected_indices: Set[int] = set()
        self._anchor_index: Optional[int] = None
        self._cursor_index: Optional[int] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(6, 10, 4, 0)
        root.setSpacing(8)

        # Header row
        header = QHBoxLayout()
        header.setContentsMargins(6, 0, 6, 0)
        header.setSpacing(6)

        self.title_lbl = QLabel(tr("gallery.title"), self)
        self.title_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; letter-spacing: 1.0px; color: {PALETTE['text_secondary']};"
        )
        header.addWidget(self.title_lbl)

        self.count_badge_lbl = QLabel("0", self)
        self.count_badge_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 10px; font-weight: 600; "
            f"color: {PALETTE['text_primary']}; background-color: {PALETTE['bg_input']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 5px; padding: 1px 6px;"
        )
        header.addWidget(self.count_badge_lbl)
        header.addStretch(1)

        self.collapse_btn = QPushButton(self)
        self.collapse_btn.setObjectName("GhostIconButton")
        self.collapse_btn.setFixedSize(26, 26)
        self.collapse_btn.setIcon(render_icon("close", 12, PALETTE["text_secondary"]))
        self.collapse_btn.setToolTip(tr("gallery.toggle_tip"))
        self.collapse_btn.clicked.connect(self.collapse_requested.emit)
        header.addWidget(self.collapse_btn)
        root.addLayout(header)

        # Quick Filter Tabs: All / Unsorted / Sorted
        tabs_row = QHBoxLayout()
        tabs_row.setContentsMargins(6, 0, 6, 0)
        tabs_row.setSpacing(5)
        self.all_btn = QPushButton("", self)
        self.unsorted_btn = QPushButton("", self)
        self.sorted_btn = QPushButton("", self)
        for btn, mode in (
            (self.all_btn, "all"),
            (self.unsorted_btn, "unsorted"),
            (self.sorted_btn, "sorted"),
        ):
            btn.setObjectName("GalleryFilterBtn")
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            btn.clicked.connect(lambda _=False, m=mode: self.set_filter(m))
            tabs_row.addWidget(btn)
        root.addLayout(tabs_row)

        # Scrollable Responsive Grid
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.grid_canvas = _GalleryGridCanvas(self.scroll_area, parent=self.scroll_area)
        self.grid_canvas.item_activated.connect(self._on_canvas_item_activated)
        self.grid_canvas.context_requested.connect(self._on_canvas_context_requested)
        self.scroll_area.setWidget(self.grid_canvas)
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            lambda _v: self.grid_canvas.request_visible_thumbs()
        )
        root.addWidget(self.scroll_area, 1)

        # Floating Bottom Bulk Action Bar (visible only when len(selected_indices) >= 2)
        self.bulk_bar = QFrame(self)
        self.bulk_bar.setObjectName("GalleryBulkBar")
        brow = QHBoxLayout(self.bulk_bar)
        brow.setContentsMargins(10, 8, 10, 8)
        brow.setSpacing(8)

        bulk_col = QVBoxLayout()
        bulk_col.setSpacing(1)
        self.bulk_label = QLabel("", self.bulk_bar)
        self.bulk_label.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {PALETTE['accent']};"
        )
        bulk_col.addWidget(self.bulk_label)
        self.bulk_hint_lbl = QLabel(tr("gallery.choose_cat"), self.bulk_bar)
        self.bulk_hint_lbl.setStyleSheet(
            f"font-size: 10px; color: {PALETTE['text_muted']};"
        )
        bulk_col.addWidget(self.bulk_hint_lbl)
        brow.addLayout(bulk_col, 1)

        self.clear_sel_btn = QPushButton(tr("gallery.clear"), self.bulk_bar)
        self.clear_sel_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.clear_sel_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.clear_sel_btn.clicked.connect(lambda: self.clear_selection(sync_to_active=True))
        brow.addWidget(self.clear_sel_btn)
        self.bulk_bar.hide()
        root.addWidget(self.bulk_bar)

        # Left-edge drag handle overlays 0..5px on left edge
        self.resize_grip = _GalleryResizeGrip(self)
        self.resize_grip.drag_delta.connect(self._on_resize_delta)
        self.resize_grip.drag_finished.connect(self._on_resize_finished)
        self._sync_filter_tabs()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self.resize_grip.setGeometry(0, 0, 5, self.height())
        self.resize_grip.raise_()
        self.grid_canvas._recompute_metrics()
        self.grid_canvas.request_visible_thumbs()

    def showEvent(self, event) -> None:  # type: ignore[override]
        super().showEvent(event)
        self.grid_canvas._recompute_metrics()
        self.grid_canvas.request_visible_thumbs()

    def set_max_allowed_width(self, max_w: int) -> None:
        self._max_allowed_width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(max_w)))

    def clamp_width(self, width: int) -> int:
        return max(self.MIN_WIDTH, min(self._max_allowed_width, int(width)))

    def set_preferred_width(self, width: int) -> int:
        self._preferred_width = max(self.MIN_WIDTH, min(self.MAX_WIDTH, int(width)))
        return self.clamp_width(self._preferred_width)

    def preferred_width(self) -> int:
        return self._preferred_width

    def _on_resize_delta(self, dx: int) -> None:
        # Dragging left edge leftward (dx < 0) widens the right sidebar
        new_w = self.clamp_width(self.width() - dx)
        self._preferred_width = new_w
        self.setFixedWidth(new_w)
        self.grid_canvas._recompute_metrics()
        self.grid_canvas.request_visible_thumbs()
        self.width_changed.emit(new_w)

    def _on_resize_finished(self) -> None:
        self.width_committed.emit(self.clamp_width(self.width()))

    def column_count(self) -> int:
        return self.grid_canvas.column_count()

    def visible_indices(self) -> List[int]:
        if self.filter_mode == "unsorted":
            return [e.index for e in self._timeline if e.status != "sorted"]
        if self.filter_mode == "sorted":
            return [e.index for e in self._timeline if e.status == "sorted"]
        return [e.index for e in self._timeline]

    def selected_indices_sorted(self) -> List[int]:
        return sorted(self._selected_indices)

    def has_multi_selection(self) -> bool:
        return self.isVisible() and len(self._selected_indices) >= 2

    def target_indices_for_action(self) -> List[int]:
        if self.has_multi_selection():
            return sorted(self._selected_indices)
        return []

    def clear_cache(self) -> None:
        self.grid_canvas.clear_cache()

    def set_session_state(
        self,
        timeline: Sequence[TimelineEntry],
        active_index: int,
        root_folder: str,
        categories: Sequence[str],
        category_colors: Dict[str, str],
        trash_name: str = "_Buang",
        trash_color: str = DEFAULT_TRASH_COLOR,
        skipped_files: Optional[Iterable[str]] = None,
    ) -> None:
        folder_changed = root_folder != self._root_folder
        self._timeline = list(timeline)
        self._active_index = max(0, min(len(self._timeline) - 1, active_index)) if self._timeline else 0
        self._root_folder = root_folder
        self._categories = list(categories)
        self._category_colors = dict(category_colors)
        self._trash_name = trash_name or "_Buang"
        self._trash_color = trash_color or DEFAULT_TRASH_COLOR
        self._skipped_files = set(skipped_files or ())

        if folder_changed:
            self.clear_cache()
            self._selected_indices = (
                {self._active_index} if self._timeline else set()
            )
            self._anchor_index = self._active_index if self._timeline else None
            self._cursor_index = self._active_index if self._timeline else None

        visible = self.visible_indices()
        vis_set = set(visible)
        self._selected_indices = {i for i in self._selected_indices if i in vis_set}
        if not self._selected_indices and self._timeline and self._active_index in vis_set:
            self._selected_indices = {self._active_index}
            self._anchor_index = self._active_index
            self._cursor_index = self._active_index

        self._sync_filter_tabs()
        self.grid_canvas.set_state(
            self._timeline,
            visible,
            self._active_index,
            self._selected_indices,
            self._root_folder,
            self._category_colors,
            self._trash_name,
            self._trash_color,
            self._skipped_files,
        )
        self._sync_bulk_bar()

    def update_single_item(
        self,
        timeline_index: int,
        entry: Optional[TimelineEntry],
        active_index: int,
        skipped_files: Optional[Iterable[str]] = None,
    ) -> None:
        """O(1) fast path for single-photo moves so sorting speed remains instant."""
        if entry is not None and 0 <= timeline_index < len(self._timeline):
            self._timeline[timeline_index] = entry
        self._active_index = max(0, min(len(self._timeline) - 1, active_index)) if self._timeline else 0
        if skipped_files is not None:
            self._skipped_files = set(skipped_files)

        self._sync_filter_tabs()
        if self.filter_mode != "all":
            visible = self.visible_indices()
            vis_set = set(visible)
            self._selected_indices = (
                {self._active_index} if self._active_index in vis_set else set()
            )
            self._anchor_index = self._active_index if self._active_index in vis_set else None
            self._cursor_index = self._anchor_index
            self.grid_canvas.set_state(
                self._timeline,
                visible,
                self._active_index,
                self._selected_indices,
                self._root_folder,
                self._category_colors,
                self._trash_name,
                self._trash_color,
                self._skipped_files,
            )
        else:
            self._selected_indices = {self._active_index} if self._timeline else set()
            self._anchor_index = self._active_index if self._timeline else None
            self._cursor_index = self._anchor_index
            self.grid_canvas.update_entry_inplace(
                timeline_index,
                entry,
                self._active_index,
                self._selected_indices,
                self._skipped_files,
            )
        self._sync_bulk_bar()
        self.ensure_visible(self._active_index)
        self.grid_canvas.request_visible_thumbs()

    def clear_selection(self, sync_to_active: bool = True) -> None:
        visible_set = set(self.visible_indices())
        if sync_to_active and self._timeline and self._active_index in visible_set:
            self._selected_indices = {self._active_index}
            self._anchor_index = self._active_index
            self._cursor_index = self._active_index
        else:
            self._selected_indices.clear()
            self._anchor_index = None
            self._cursor_index = None
        self.grid_canvas.update_selection_only(self._active_index, self._selected_indices)
        self._sync_bulk_bar()
        self.selection_changed.emit(self.selected_indices_sorted())

    def select_single(self, timeline_index: int) -> None:
        if not (0 <= timeline_index < len(self._timeline)):
            return
        self._active_index = timeline_index
        self._selected_indices = {timeline_index}
        self._anchor_index = timeline_index
        self._cursor_index = timeline_index
        self.grid_canvas.update_selection_only(self._active_index, self._selected_indices)
        self._sync_bulk_bar()
        self.ensure_visible(timeline_index)
        self.selection_changed.emit(self.selected_indices_sorted())

    def prune_moved_indices(self, moved_indices: Sequence[int]) -> None:
        moved_set = set(moved_indices)
        self._selected_indices = {i for i in self._selected_indices if i not in moved_set}
        self.grid_canvas.update_selection_only(self._active_index, self._selected_indices)
        self._sync_bulk_bar()
        self.selection_changed.emit(self.selected_indices_sorted())

    def set_filter(self, mode: str) -> None:
        norm = mode if mode in ("all", "unsorted", "sorted") else "all"
        if self.filter_mode == norm:
            return
        self.filter_mode = norm
        visible = self.visible_indices()
        vis_set = set(visible)
        self._selected_indices = {i for i in self._selected_indices if i in vis_set}
        if len(self._selected_indices) < 2:
            if self._active_index in vis_set:
                self._selected_indices = {self._active_index}
                self._anchor_index = self._active_index
                self._cursor_index = self._active_index
            elif visible:
                self._selected_indices = {visible[0]}
                self._anchor_index = visible[0]
                self._cursor_index = visible[0]
            else:
                self._selected_indices.clear()
                self._anchor_index = None
                self._cursor_index = None
        self._sync_filter_tabs()
        self.grid_canvas.set_state(
            self._timeline,
            visible,
            self._active_index,
            self._selected_indices,
            self._root_folder,
            self._category_colors,
            self._trash_name,
            self._trash_color,
            self._skipped_files,
        )
        self._sync_bulk_bar()
        self.selection_changed.emit(self.selected_indices_sorted())

    def step_range_selection(self, delta: int) -> Optional[int]:
        """Extend or shrink range selection within visible_indices ONLY (Shift+Left/Right)."""
        visible = self.visible_indices()
        if not visible:
            return None
        vis_pos = {tidx: pos for pos, tidx in enumerate(visible)}

        anchor = self._anchor_index
        if anchor is None or anchor not in vis_pos:
            anchor = self._active_index if self._active_index in vis_pos else visible[0]
        cursor = self._cursor_index
        if cursor is None or cursor not in vis_pos:
            cursor = anchor

        anchor_pos = vis_pos[anchor]
        cur_pos = vis_pos[cursor]
        step = -1 if delta < 0 else 1
        new_pos = max(0, min(len(visible) - 1, cur_pos + step))
        new_cursor = visible[new_pos]

        lo = min(anchor_pos, new_pos)
        hi = max(anchor_pos, new_pos)
        self._selected_indices = set(visible[lo : hi + 1])
        self._anchor_index = anchor
        self._cursor_index = new_cursor
        self._active_index = new_cursor

        self.grid_canvas.update_selection_only(self._active_index, self._selected_indices)
        self._sync_bulk_bar()
        self.ensure_visible(new_cursor)
        self.selection_changed.emit(self.selected_indices_sorted())
        return new_cursor

    def ensure_visible(self, timeline_index: int) -> None:
        rect = self.grid_canvas.card_rect_for_timeline_index(timeline_index)
        if rect is None:
            return
        self.scroll_area.ensureVisible(
            rect.center().x(), rect.center().y(), 16, 20
        )

    def _on_canvas_item_activated(self, tidx: int, modifiers: object) -> None:
        mods = Qt.KeyboardModifier(modifiers)
        visible = self.visible_indices()
        vis_pos = {i: pos for pos, i in enumerate(visible)}
        if tidx not in vis_pos:
            return

        if mods & Qt.KeyboardModifier.ShiftModifier:
            anchor = self._anchor_index
            if anchor is None or anchor not in vis_pos:
                anchor = self._active_index if self._active_index in vis_pos else tidx
            p_a = vis_pos[anchor]
            p_b = vis_pos[tidx]
            lo, hi = min(p_a, p_b), max(p_a, p_b)
            self._selected_indices = set(visible[lo : hi + 1])
            self._anchor_index = anchor
            self._cursor_index = tidx
            self._active_index = tidx
            self.grid_canvas.update_selection_only(self._active_index, self._selected_indices)
            self._sync_bulk_bar()
            self.selection_changed.emit(self.selected_indices_sorted())
            self.preview_requested.emit(tidx)
        elif mods & Qt.KeyboardModifier.ControlModifier:
            if tidx in self._selected_indices and len(self._selected_indices) > 1:
                self._selected_indices.remove(tidx)
            else:
                self._selected_indices.add(tidx)
            self._anchor_index = tidx
            self._cursor_index = tidx
            self._active_index = tidx
            self.grid_canvas.update_selection_only(self._active_index, self._selected_indices)
            self._sync_bulk_bar()
            self.selection_changed.emit(self.selected_indices_sorted())
            self.preview_requested.emit(tidx)
        else:
            self.select_single(tidx)
            self.item_clicked.emit(tidx)

    def _on_canvas_context_requested(self, tidx: int, global_pos: QPoint) -> None:
        if tidx not in self._selected_indices:
            self.select_single(tidx)
            self.preview_requested.emit(tidx)
        targets = sorted(self._selected_indices) if self._selected_indices else [tidx]
        count = len(targets)

        menu = QMenu(self)
        heading = (
            tr("gallery.menu.move_multi", count=count)
            if count > 1
            else tr("gallery.menu.move_single")
        )
        hdr_act = menu.addAction(heading)
        hdr_act.setEnabled(False)
        menu.addSeparator()

        act_map: Dict[object, str] = {}
        trash_act = menu.addAction(
            render_icon("trash", 13, self._trash_color), self._trash_name
        )
        act_map[trash_act] = self._trash_name
        if self._categories:
            menu.addSeparator()
        for cat in self._categories:
            color = self._category_colors.get(cat, DEFAULT_CATEGORY_COLOR)
            act = menu.addAction(render_icon("tag", 13, color), cat)
            act_map[act] = cat

        chosen = menu.exec(global_pos)
        if chosen in act_map:
            self.bulk_move_requested.emit(targets, act_map[chosen])

    def _sync_filter_tabs(self) -> None:
        total = len(self._timeline)
        sorted_cnt = sum(1 for e in self._timeline if e.status == "sorted")
        unsorted_cnt = max(0, total - sorted_cnt)
        self.count_badge_lbl.setText(str(total))
        self.all_btn.setText(tr("gallery.tab.all", count=total))
        self.unsorted_btn.setText(tr("gallery.tab.unsorted", count=unsorted_cnt))
        self.sorted_btn.setText(tr("gallery.tab.sorted", count=sorted_cnt))

        for btn, mode in (
            (self.all_btn, "all"),
            (self.unsorted_btn, "unsorted"),
            (self.sorted_btn, "sorted"),
        ):
            btn.setProperty("active", "true" if self.filter_mode == mode else "false")
            btn.style().unpolish(btn)
            btn.style().polish(btn)

    def _sync_bulk_bar(self) -> None:
        cnt = len(self._selected_indices)
        show_bulk = cnt >= 2
        self.bulk_bar.setVisible(show_bulk)
        if show_bulk:
            self.bulk_label.setText(tr("gallery.selected_count", count=cnt))
            self.bulk_hint_lbl.setText(tr("gallery.choose_cat"))

    def refresh_texts(self) -> None:
        self.title_lbl.setText(tr("gallery.title"))
        self.collapse_btn.setToolTip(tr("gallery.toggle_tip"))
        self.clear_sel_btn.setText(tr("gallery.clear"))
        self._sync_filter_tabs()
        self._sync_bulk_bar()
        self.grid_canvas.update()

    def refresh_theme(self) -> None:
        self.title_lbl.setStyleSheet(
            f"font-size: 11px; font-weight: 700; letter-spacing: 1.0px; color: {PALETTE['text_secondary']};"
        )
        self.count_badge_lbl.setStyleSheet(
            f"font-family: '{MONO_FONT_FAMILY}', monospace; font-size: 10px; font-weight: 600; "
            f"color: {PALETTE['text_primary']}; background-color: {PALETTE['bg_input']}; "
            f"border: 1px solid {PALETTE['border']}; border-radius: 5px; padding: 1px 6px;"
        )
        self.collapse_btn.setIcon(render_icon("close", 12, PALETTE["text_secondary"]))
        self.bulk_label.setStyleSheet(
            f"font-size: 12px; font-weight: 700; color: {PALETTE['accent']};"
        )
        self.bulk_hint_lbl.setStyleSheet(
            f"font-size: 10px; color: {PALETTE['text_muted']};"
        )
        self.grid_canvas.update()
