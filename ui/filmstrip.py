"""Compact 5-slot optical filmstrip tray with async thumbnails and smooth motion for PhotoBox v0.5.0."""
from __future__ import annotations

import os
import subprocess
import tempfile
import zlib
from pathlib import Path
from typing import Dict, Iterable, List, Optional

from PyQt6.QtCore import QObject, QRectF, QRunnable, QSize, Qt, QThreadPool, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QImage, QImageReader, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QSizePolicy, QToolButton, QWidget

from core.session_manager import TimelineEntry, is_video_file
from ui.i18n import tr
from ui.icons import draw_icon
from ui.motion import ValueAnimator, ease_out, lerp_color, lerp_float, prefers_reduced_motion
from ui.theme import FONT_FAMILY, PALETTE


class _ThumbSignalBridge(QObject):
    loaded = pyqtSignal(str, int, QImage)


class _ThumbPreloadTask(QRunnable):
    """Background thumbnail loader supporting high-speed QImageReader and ffmpeg frame extraction."""

    def __init__(
        self,
        path: str,
        generation: int,
        target_w: int,
        target_h: int,
        bridge: _ThumbSignalBridge,
    ) -> None:
        super().__init__()
        self.path = path
        self.generation = generation
        self.target_w = target_w
        self.target_h = target_h
        self.bridge = bridge

    def run(self) -> None:
        img = QImage()
        if is_video_file(self.path):
            img = self._extract_video_frame(self.path, self.target_w * 2, self.target_h * 2)
        else:
            reader = QImageReader(self.path)
            reader.setAutoTransform(True)
            size = reader.size()
            if size.isValid() and size.width() > 0 and size.height() > 0:
                scaled = size.scaled(
                    QSize(self.target_w * 2, self.target_h * 2),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                )
                reader.setScaledSize(scaled)
            img = reader.read()
        try:
            self.bridge.loaded.emit(self.path, self.generation, img)
        except RuntimeError:
            pass

    @staticmethod
    def _extract_video_frame(path: str, width: int, height: int) -> QImage:
        tmp_path = ""
        try:
            with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
                tmp_path = tf.name
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            cmd = [
                "ffmpeg",
                "-y",
                "-ss",
                "0.3",
                "-i",
                path,
                "-vframes",
                "1",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}",
                "-q:v",
                "4",
                tmp_path,
            ]
            res = subprocess.run(
                cmd,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=3.5,
                creationflags=flags,
                check=False,
            )
            if res.returncode == 0 and os.path.exists(tmp_path) and os.path.getsize(tmp_path) > 0:
                img = QImage(tmp_path)
                if not img.isNull():
                    return img
        except Exception:
            pass
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
        return QImage()


class _FilmstripSlotButton(QToolButton):
    """Single optical frame slot with thumbnail, frame index pill, status & ▶ VID badge."""

    def __init__(self, slot_pos: int, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.slot_pos = slot_pos
        self.timeline_index: int = -1
        self.entry: Optional[TimelineEntry] = None
        self.pixmap: Optional[QPixmap] = None
        self.is_active: bool = False
        self.is_video: bool = False
        self.is_skipped: bool = False
        self.trash_name: str = "_Buang"
        self.accent_color: Optional[str] = None

        self._hover_t: float = 0.0
        self._press_t: float = 0.0
        self._active_t: float = 0.0
        self._thumb_alpha: float = 1.0

        self._hover_anim = ValueAnimator(0.0, parent=self)
        self._press_anim = ValueAnimator(0.0, parent=self)
        self._active_anim = ValueAnimator(0.0, parent=self)
        self._thumb_fade_anim = ValueAnimator(1.0, parent=self)

        self.setFixedSize(72, 48)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.setAutoRaise(False)
        self.setStyleSheet("QToolButton { background: transparent; border: none; padding: 0px; }")

    def _on_hover_tick(self, val: float) -> None:
        self._hover_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _on_press_tick(self, val: float) -> None:
        self._press_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _on_active_tick(self, val: float) -> None:
        self._active_t = max(0.0, min(1.0, float(val)))
        self.update()

    def _on_thumb_tick(self, val: float) -> None:
        self._thumb_alpha = max(0.0, min(1.0, float(val)))
        self.update()

    def enterEvent(self, event) -> None:  # type: ignore[override]
        if self.isEnabled():
            self._hover_anim.animate_to(
                1.0, duration_ms=135, curve=ease_out(), on_value=self._on_hover_tick
            )
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:  # type: ignore[override]
        self._hover_anim.animate_to(
            0.0, duration_ms=135, curve=ease_out(), on_value=self._on_hover_tick
        )
        self._press_anim.animate_to(
            0.0, duration_ms=140, curve=ease_out(), on_value=self._on_press_tick
        )
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if self.isEnabled() and event.button() == Qt.MouseButton.LeftButton:
            self._press_anim.animate_to(
                1.0, duration_ms=90, curve=ease_out(), on_value=self._on_press_tick
            )
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        self._press_anim.animate_to(
            0.0, duration_ms=140, curve=ease_out(), on_value=self._on_press_tick
        )
        super().mouseReleaseEvent(event)

    def set_loaded_pixmap(self, pixmap: QPixmap) -> None:
        """Apply freshly decoded thumbnail with smooth 140ms fade-in."""
        had_pix = self.pixmap is not None and not self.pixmap.isNull()
        self.pixmap = pixmap
        if not had_pix and not pixmap.isNull():
            self._thumb_fade_anim.animate_to(
                1.0,
                duration_ms=140,
                curve=ease_out(),
                start_from=0.0,
                on_value=self._on_thumb_tick,
                non_vestibular=True,
            )
        else:
            self._thumb_alpha = 1.0
            self.update()

    def set_slot_data(
        self,
        timeline_index: int,
        entry: Optional[TimelineEntry],
        pixmap: Optional[QPixmap],
        is_active: bool,
        is_skipped: bool = False,
        trash_name: str = "_Buang",
        accent_color: Optional[str] = None,
    ) -> None:
        prev_had_pix = self.pixmap is not None and not self.pixmap.isNull()
        prev_rel = self.entry.rel_path if self.entry is not None else None
        new_rel = entry.rel_path if entry is not None else None

        self.timeline_index = timeline_index
        self.entry = entry
        self.pixmap = pixmap
        self.is_active = bool(is_active)
        self.is_skipped = bool(is_skipped)
        self.is_video = bool(entry and is_video_file(entry.name))
        self.trash_name = trash_name
        self.accent_color = accent_color
        self.setEnabled(entry is not None)
        self.refresh_tooltip()

        if entry is None:
            self._hover_anim.stop_silent(0.0)
            self._press_anim.stop_silent(0.0)
            self._active_anim.stop_silent(0.0)
            self._thumb_fade_anim.stop_silent(1.0)
            self._hover_t = 0.0
            self._press_t = 0.0
            self._active_t = 0.0
            self._thumb_alpha = 1.0
            self.update()
            return

        target_active = 1.0 if self.is_active else 0.0
        if self.isVisible():
            self._active_anim.animate_to(
                target_active,
                duration_ms=160,
                curve=ease_out(),
                on_value=self._on_active_tick,
            )
        else:
            self._active_anim.stop_silent(target_active)
            self._active_t = target_active

        now_has_pix = pixmap is not None and not pixmap.isNull()
        if now_has_pix and (not prev_had_pix and prev_rel == new_rel):
            self._thumb_fade_anim.animate_to(
                1.0,
                duration_ms=140,
                curve=ease_out(),
                start_from=0.0,
                on_value=self._on_thumb_tick,
                non_vestibular=True,
            )
        else:
            self._thumb_fade_anim.stop_silent(1.0)
            self._thumb_alpha = 1.0
        self.update()

    def refresh_tooltip(self) -> None:
        if not self.entry:
            self.setToolTip("")
            return
        name = os.path.basename(self.entry.name)
        parts = [f"#{self.timeline_index + 1} · {name}"]
        if self.is_video:
            parts.append(f"[{tr('filmstrip.video_tag')}]")
        if self.entry.status == "sorted" and self.entry.category:
            parts.append(f"→ {self.entry.category}")
        elif self.is_skipped:
            parts.append(f"({tr('filmstrip.skipped_tag')})")
        self.setToolTip(" ".join(parts))

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        rect = QRectF(self.rect()).adjusted(1.5, 1.5, -1.5, -1.5)
        radius = 7.0

        clip = QPainterPath()
        clip.addRoundedRect(rect, radius, radius)

        if self.entry is None:
            painter.setPen(QPen(QColor(PALETTE["border_subtle"]), 1.0))
            painter.setBrush(QColor(PALETTE["bg_input"]))
            painter.drawPath(clip)
            painter.end()
            return

        # Inner thumbnail fill with smooth hover zoom, press sink, and load fade
        painter.save()
        painter.setClipPath(clip)
        reduced = prefers_reduced_motion()
        thumb_scale = (
            1.0
            if reduced
            else (1.0 + 0.055 * self._hover_t) * (1.0 - 0.035 * self._press_t)
        )
        if self.pixmap and not self.pixmap.isNull():
            pw = self.pixmap.width()
            ph = self.pixmap.height()
            if pw > 0 and ph > 0:
                rw, rh = rect.width(), rect.height()
                scale = max(rw / pw, rh / ph) * thumb_scale
                sw, sh = pw * scale, ph * scale
                dx = rect.left() + (rw - sw) / 2.0
                dy = rect.top() + (rh - sh) / 2.0
                painter.save()
                painter.setOpacity(self._thumb_alpha)
                painter.drawPixmap(QRectF(dx, dy, sw, sh), self.pixmap, QRectF(0, 0, pw, ph))
                painter.restore()
        else:
            painter.fillRect(rect, QColor(PALETTE["bg_elevated"]))
            icon_rect = QRectF(rect.center().x() - 9, rect.center().y() - 9, 18, 18)
            draw_icon(
                painter,
                "video" if self.is_video else "image",
                icon_rect,
                PALETTE["text_muted"],
                stroke_width=1.4,
            )

        # Smooth non-active optical dimming relieved on focus or hover
        dim_max = 54 if PALETTE["mode"] == "black" else 22
        dim_alpha = int(
            round(dim_max * (1.0 - max(self._active_t, 0.75 * self._hover_t)))
        )
        if dim_alpha > 0:
            painter.fillRect(rect, QColor(0, 0, 0, dim_alpha))

        # Top-left compact frame index badge (#01..)
        idx_text = f"{self.timeline_index + 1:02d}"
        idx_font = QFont(FONT_FAMILY, 7, QFont.Weight.DemiBold)
        painter.setFont(idx_font)
        badge_bg = QColor(PALETTE["accent"]) if self.is_active else QColor(8, 8, 10, 178)
        badge_fg = QColor(PALETTE["accent_text_on_fill"]) if self.is_active else QColor("#F4F4F5")
        idx_w = max(20, painter.fontMetrics().horizontalAdvance(idx_text) + 7)
        idx_rect = QRectF(rect.left() + 3, rect.top() + 3, idx_w, 13)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(badge_bg)
        painter.drawRoundedRect(idx_rect, 3.5, 3.5)
        painter.setPen(badge_fg)
        painter.drawText(idx_rect, Qt.AlignmentFlag.AlignCenter, idx_text)

        # Top-right video badge
        if self.is_video:
            vid_text = "▶ VID"
            vid_font = QFont(FONT_FAMILY, 6, QFont.Weight.Bold)
            painter.setFont(vid_font)
            vw = painter.fontMetrics().horizontalAdvance(vid_text) + 6
            vrect = QRectF(rect.right() - vw - 3, rect.top() + 3, vw, 12)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(8, 8, 10, 195))
            painter.drawRoundedRect(vrect, 3.0, 3.0)
            painter.setPen(QColor("#F4F4F5"))
            painter.drawText(vrect, Qt.AlignmentFlag.AlignCenter, vid_text)

        # Bottom status pill if sorted or skipped
        if self.entry.status == "sorted" and self.entry.category:
            is_trash = self.entry.category == self.trash_name
            cat_hex = PALETTE["danger"] if is_trash else (self.accent_color or PALETTE["success"])
            cat_color = QColor(cat_hex)
            strip_rect = QRectF(rect.left(), rect.bottom() - 14, rect.width(), 14)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(10, 10, 14, 210))
            painter.drawRect(strip_rect)
            # Subtle bottom status indicator dot + category text
            painter.setBrush(cat_color)
            painter.drawEllipse(QRectF(rect.left() + 4, rect.bottom() - 10, 5.5, 5.5))
            tag_font = QFont(FONT_FAMILY, 7, QFont.Weight.DemiBold)
            painter.setFont(tag_font)
            elided = painter.fontMetrics().elidedText(
                self.entry.category, Qt.TextElideMode.ElideRight, int(rect.width() - 14)
            )
            painter.setPen(QColor("#F4F4F5"))
            painter.drawText(
                QRectF(rect.left() + 12, rect.bottom() - 14, rect.width() - 15, 14),
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                elided,
            )
        elif self.is_skipped and self.entry.status == "pending":
            skip_label = tr("filmstrip.skipped_tag")
            skip_font = QFont(FONT_FAMILY, 6, QFont.Weight.DemiBold)
            painter.setFont(skip_font)
            sw = min(rect.width() - 6, painter.fontMetrics().horizontalAdvance(skip_label) + 8)
            srect = QRectF(rect.left() + 3, rect.bottom() - 14, sw, 11)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(12, 13, 16, 195))
            painter.drawRoundedRect(srect, 3.0, 3.0)
            painter.setPen(QColor(PALETTE["text_secondary"]))
            painter.drawText(srect, Qt.AlignmentFlag.AlignCenter, skip_label)

        painter.restore()

        # Animated active top optical bar + smoothly interpolated border glow
        if self._active_t > 0.02:
            bar_w = 18.0 * self._active_t
            bar_col = QColor(PALETTE["accent"])
            bar_col.setAlpha(int(round(255 * self._active_t)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(bar_col)
            painter.drawRoundedRect(
                QRectF(rect.center().x() - bar_w / 2.0, rect.top() + 1.0, bar_w, 2.0),
                1.0,
                1.0,
            )

        base_border = lerp_color(PALETTE["border"], PALETTE["border_strong"], self._hover_t)
        border_color = lerp_color(base_border, PALETTE["accent"], self._active_t)
        pen_w = lerp_float(lerp_float(1.0, 1.4, self._hover_t), 2.0, self._active_t)
        painter.setPen(QPen(border_color, pen_w))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(rect, radius, radius)
        painter.end()


class FilmstripBar(QFrame):
    """Compact centered 5-slot filmstrip tray floating cleanly above the keycap deck."""

    jump_requested = pyqtSignal(int)

    SLOT_COUNT = 5

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("FilmstripBar")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setFixedHeight(64)

        self._thumb_cache: Dict[str, QPixmap] = {}
        self._pending_paths: set[str] = set()
        self._generation: int = 0
        self._pool = QThreadPool.globalInstance()
        self._bridge = _ThumbSignalBridge(self)
        self._bridge.loaded.connect(self._on_thumb_loaded)

        self._timeline: List[TimelineEntry] = []
        self._focus_index: int = 0
        self._prev_focus_index: Optional[int] = None
        self._reel_shift: float = 0.0
        self._reel_anim = ValueAnimator(0.0, parent=self)
        self._root_folder: str = ""
        self._trash_name: str = "_Buang"
        self._category_colors: Dict[str, str] = {}

        outer = QHBoxLayout(self)
        outer.setContentsMargins(0, 2, 0, 2)
        outer.setSpacing(0)
        self._outer_layout = outer
        outer.addStretch(1)

        self.tray = QFrame(self)
        self.tray.setObjectName("FilmstripTray")
        tray_layout = QHBoxLayout(self.tray)
        tray_layout.setContentsMargins(8, 6, 8, 6)
        tray_layout.setSpacing(10)

        self.slots: List[_FilmstripSlotButton] = []
        for pos in range(self.SLOT_COUNT):
            btn = _FilmstripSlotButton(pos, self.tray)
            btn.clicked.connect(lambda _checked=False, b=btn: self._on_slot_clicked(b))
            self.slots.append(btn)
            tray_layout.addWidget(btn)

        outer.addWidget(self.tray)
        outer.addStretch(1)

    def _on_slot_clicked(self, btn: _FilmstripSlotButton) -> None:
        if btn.timeline_index >= 0 and btn.entry is not None:
            self.jump_requested.emit(btn.timeline_index)

    def _resolve_entry_path(self, entry: TimelineEntry) -> str:
        if not self._root_folder:
            return entry.name
        if entry.rel_path:
            p = os.path.join(self._root_folder, entry.rel_path)
            if os.path.exists(p):
                return p
        if entry.status == "sorted" and entry.category:
            p = os.path.join(self._root_folder, entry.category, entry.name)
            if os.path.exists(p):
                return p
        if getattr(entry, "origin_rel_path", None):
            p = os.path.join(self._root_folder, str(entry.origin_rel_path))
            if os.path.exists(p):
                return p
        return os.path.join(self._root_folder, entry.name)

    def _on_reel_tick(self, val: float) -> None:
        self._reel_shift = float(val)
        left_m = max(0, int(round(self._reel_shift * 2.0)))
        right_m = max(0, int(round(-self._reel_shift * 2.0)))
        self._outer_layout.setContentsMargins(left_m, 2, right_m, 2)

    def clear_cache(self) -> None:
        self._generation += 1
        self._thumb_cache.clear()
        self._pending_paths.clear()

    def update_items(
        self,
        timeline: List[TimelineEntry],
        focus_index: int,
        root_folder: str = "",
        trash_name: str = "_Buang",
        category_colors: Optional[Dict[str, str]] = None,
        skipped_files: Optional[Iterable[str]] = None,
    ) -> None:
        """Render the 5 visible slots centered around focus_index."""
        self._timeline = list(timeline)
        self._focus_index = max(0, focus_index)
        self._root_folder = root_folder
        self._trash_name = trash_name or "_Buang"
        self._category_colors = dict(category_colors or {})
        skipped_set = set(skipped_files or ())

        if (
            self._prev_focus_index is not None
            and abs(self._focus_index - self._prev_focus_index) == 1
            and self.isVisible()
            and self.isEnabled()
            and not prefers_reduced_motion()
        ):
            step_dir = 1 if self._focus_index > self._prev_focus_index else -1
            self._reel_anim.animate_to(
                0.0,
                duration_ms=135,
                curve=ease_out(),
                start_from=float(step_dir * 8.0),
                on_value=self._on_reel_tick,
            )
        else:
            self._reel_anim.stop_silent(0.0)
            self._on_reel_tick(0.0)
        self._prev_focus_index = self._focus_index

        total = len(self._timeline)
        if total == 0:
            for slot in self.slots:
                slot.set_slot_data(-1, None, None, False, trash_name=self._trash_name)
            return

        half = self.SLOT_COUNT // 2
        if total <= self.SLOT_COUNT:
            start = 0
        else:
            start = max(0, min(total - self.SLOT_COUNT, self._focus_index - half))

        for pos, slot in enumerate(self.slots):
            idx = start + pos
            if 0 <= idx < total:
                entry = self._timeline[idx]
                media_path = self._resolve_entry_path(entry)
                pix = self._thumb_cache.get(media_path)
                if pix is None and media_path not in self._pending_paths and os.path.exists(media_path):
                    self._pending_paths.add(media_path)
                    task = _ThumbPreloadTask(
                        media_path,
                        self._generation,
                        slot.width(),
                        slot.height(),
                        self._bridge,
                    )
                    self._pool.start(task)
                accent_color = (
                    self._category_colors.get(entry.category)
                    if entry.category
                    else None
                )
                is_skip = entry.rel_path in skipped_set or entry.name in skipped_set
                slot.set_slot_data(
                    idx,
                    entry,
                    pix,
                    is_active=(idx == self._focus_index),
                    is_skipped=is_skip,
                    trash_name=self._trash_name,
                    accent_color=accent_color,
                )
            else:
                slot.set_slot_data(-1, None, None, False, trash_name=self._trash_name)

        # Preload +/- 3 neighbors seamlessly for rapid skipping
        for neighbor in range(max(0, start - 3), min(total, start + self.SLOT_COUNT + 3)):
            npath = self._resolve_entry_path(self._timeline[neighbor])
            if npath not in self._thumb_cache and npath not in self._pending_paths and os.path.exists(npath):
                self._pending_paths.add(npath)
                self._pool.start(
                    _ThumbPreloadTask(npath, self._generation, 72, 48, self._bridge)
                )

    def _on_thumb_loaded(self, path: str, generation: int, img: QImage) -> None:
        self._pending_paths.discard(path)
        if generation != self._generation or img.isNull():
            return
        pix = QPixmap.fromImage(img)
        if len(self._thumb_cache) > 400:
            self._thumb_cache.clear()
        self._thumb_cache[path] = pix
        for slot in self.slots:
            if slot.entry is not None and self._resolve_entry_path(slot.entry) == path:
                slot.set_loaded_pixmap(pix)

    def refresh_theme(self) -> None:
        for slot in self.slots:
            slot.update()
        self.tray.style().unpolish(self.tray)
        self.tray.style().polish(self.tray)
        self.update()

    def refresh_texts(self) -> None:
        for slot in self.slots:
            slot.refresh_tooltip()
            slot.update()


def cache_key_hint(path: str) -> str:
    return hex(zlib.crc32(path.encode("utf-8", errors="ignore")))
