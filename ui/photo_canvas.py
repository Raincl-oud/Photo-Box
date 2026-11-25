"""Fast C++ QImageReader photo & QtMultimedia video canvas with EXIF auto-transform, smooth motion, and Action HUD."""

from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from typing import Iterable, Optional, Tuple, Union

from PyQt6.QtCore import (
    QBuffer,
    QByteArray,
    QCoreApplication,
    QEventLoop,
    QFile,
    QIODevice,
    QObject,
    QPointF,
    QRectF,
    QRunnable,
    QSize,
    QSizeF,
    Qt,
    QThreadPool,
    QTimer,
    QUrl,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QFont,
    QFontMetrics,
    QImage,
    QImageIOHandler,
    QImageReader,
    QPainter,
    QPen,
    QPixmap,
    QTransform,
    QWheelEvent,
)
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QGraphicsVideoItem
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsOpacityEffect,
    QGraphicsPixmapItem,
    QGraphicsRectItem,
    QGraphicsScene,
    QGraphicsTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyle,
    QStyleOptionSlider,
    QWidget,
)

from core.session_manager import is_video_file
from ui.i18n import tr
from ui.icons import draw_icon, render_icon
from ui.motion import (
    ValueAnimator,
    ease_out,
    ease_overshoot,
    lerp_float,
    prefers_reduced_motion,
)
from ui.theme import MONO_FONT_FAMILY, PALETTE, UI_FONT_FAMILY

_MAX_IN_MEMORY_BYTES = 32 * 1024 * 1024  # 32 MB


def file_cache_key(path: Path) -> Tuple[str, int]:
    """Build cache key combining path string and nanosecond mtime."""
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return (str(path), mtime_ns)


def _format_duration_ms(ms: int) -> str:
    total_sec = max(0, int(ms // 1000))
    mins, secs = divmod(total_sec, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def _decode_with_reader(
    reader: QImageReader,
    max_dim: Union[int, Tuple[int, int]] = 2560,
) -> Tuple[Optional[QImage], int, int]:
    """Decode QImage via C++ QImageReader respecting EXIF pre-transform scaling."""
    reader.setDecideFormatFromContent(True)
    reader.setAutoTransform(True)

    raw_size = reader.size()
    tx = reader.transformation()
    swapped = bool(tx & QImageIOHandler.Transformation.TransformationRotate90)

    disp_w, disp_h = 0, 0
    if raw_size.isValid() and raw_size.width() > 0 and raw_size.height() > 0:
        raw_w, raw_h = raw_size.width(), raw_size.height()
        disp_w, disp_h = (raw_h, raw_w) if swapped else (raw_w, raw_h)

        if isinstance(max_dim, tuple):
            max_w, max_h = int(max_dim[0]), int(max_dim[1])
        else:
            max_w = max_h = int(max_dim)

        if max_w > 0 and max_h > 0 and (disp_w > max_w or disp_h > max_h):
            scale = min(max_w / disp_w, max_h / disp_h)
            scaled_raw_w = max(1, int(round(raw_w * scale)))
            scaled_raw_h = max(1, int(round(raw_h * scale)))
            reader.setScaledSize(QSize(scaled_raw_w, scaled_raw_h))

    qimg = reader.read()
    if qimg.isNull():
        return None, disp_w, disp_h

    if disp_w <= 0 or disp_h <= 0:
        disp_w, disp_h = qimg.width(), qimg.height()
    return qimg, disp_w, disp_h


def load_scaled_qimage(
    path: Path,
    max_dim: Union[int, Tuple[int, int]] = 2560,
) -> Tuple[Optional[QImage], int, int]:
    """Decode an image via C++ QImageReader with EXIF transform and DCT scaling.

    Returns (QImage | None, original_display_width, original_display_height).
    Closes OS file handles immediately so subsequent file moves never hit WinError 32.
    """
    try:
        file_size = path.stat().st_size
    except OSError:
        return None, 0, 0

    if file_size <= _MAX_IN_MEMORY_BYTES:
        try:
            raw_bytes = path.read_bytes()
        except OSError:
            return None, 0, 0
        qbytes = QByteArray(raw_bytes)
        buf = QBuffer(qbytes)
        if not buf.open(QIODevice.OpenModeFlag.ReadOnly):
            return None, 0, 0
        try:
            reader = QImageReader(buf)
            return _decode_with_reader(reader, max_dim=max_dim)
        except Exception:
            return None, 0, 0
        finally:
            buf.close()

    qfile = QFile(str(path))
    if not qfile.open(QIODevice.OpenModeFlag.ReadOnly):
        return None, 0, 0
    try:
        reader = QImageReader(qfile)
        return _decode_with_reader(reader, max_dim=max_dim)
    except Exception:
        return None, 0, 0
    finally:
        qfile.close()


def load_exif_qimage(
    path: Path, max_size: Optional[Tuple[int, int]] = None
) -> Optional[QImage]:
    """Decode an EXIF-oriented QImage bounded by max_size (or 2560px)."""
    qimg, _w, _h = load_scaled_qimage(
        path, max_dim=max_size if max_size is not None else 2560
    )
    return qimg


class _PreloadSignals(QObject):
    loaded = pyqtSignal(str, object, object, int, int)


class _ImagePreloadTask(QRunnable):
    def __init__(
        self, path: Path, mtime_ns: int, max_dim: int, signals: _PreloadSignals
    ) -> None:
        super().__init__()
        self._path = path
        self._mtime_ns = mtime_ns
        self._max_dim = max_dim
        self._signals = signals

    def run(self) -> None:
        qimg, orig_w, orig_h = load_scaled_qimage(self._path, max_dim=self._max_dim)
        try:
            self._signals.loaded.emit(
                str(self._path), self._mtime_ns, qimg, orig_w, orig_h
            )
        except RuntimeError:
            pass


class _ActionHudPill(QWidget):
    """Floating transparent-to-mouse pill confirming category move, undo, seek, or skip."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self._label = ""
        self._icon_name = "check"
        self._tone = "accent"
        self._progress = 0.0
        self._opacity = 0.0
        self._pop_t = 1.0
        self._bottom_clearance = 18

        self._anim = ValueAnimator(0.0, parent=self)
        self._pop_anim = ValueAnimator(1.0, parent=self)
        self._width_anim = ValueAnimator(96.0, parent=self)
        self._hold_timer = QTimer(self)
        self._hold_timer.setSingleShot(True)
        self._hold_timer.timeout.connect(self._fade_out)
        self.hide()

    def set_bottom_clearance(self, px: int) -> None:
        self._bottom_clearance = max(14, int(px))
        if self.isVisible():
            self._reposition()

    def show_message(
        self, label: str, icon_name: str = "check", tone: str = "accent"
    ) -> None:
        self._label = label
        self._icon_name = icon_name
        self._tone = tone
        self._hold_timer.stop()

        font = QFont(UI_FONT_FAMILY, 10)
        font.setWeight(QFont.Weight.DemiBold)
        fm = QFontMetrics(font)
        text_w = fm.horizontalAdvance(self._label)
        pill_w = max(96, 14 + 16 + 8 + text_w + 18)
        pill_h = 34
        was_visible = self.isVisible()
        self.setFixedHeight(pill_h)
        if was_visible and not prefers_reduced_motion() and self.width() > 0:
            self._width_anim.animate_to(
                float(pill_w),
                duration_ms=120,
                curve=ease_out(),
                start_from=float(self.width()),
                on_value=self._on_width_tick,
                non_vestibular=True,
            )
        else:
            self._width_anim.stop_silent(float(pill_w))
            self.setFixedWidth(pill_w)
            self._reposition()
        self.show()
        self.raise_()

        self._pop_anim.animate_to(
            target=1.0,
            duration_ms=165,
            curve=ease_overshoot(),
            start_from=0.0,
            on_value=self._on_pop_tick,
            non_vestibular=False,
        )

        self._anim.animate_to(
            target=1.0,
            duration_ms=140,
            curve=ease_out(),
            start_from=0.0 if not was_visible else max(0.0, self._progress),
            on_value=self._on_anim_tick,
            on_done=self._start_hold,
            non_vestibular=True,
        )

    def _on_width_tick(self, w: float) -> None:
        self.setFixedWidth(max(96, int(round(w))))
        self._reposition()

    def _on_pop_tick(self, value: float) -> None:
        self._pop_t = float(value)
        self.update()

    def _start_hold(self) -> None:
        self._hold_timer.start(950)

    def _fade_out(self) -> None:
        self._anim.animate_to(
            target=0.0,
            duration_ms=140,
            curve=ease_out(),
            on_value=self._on_anim_tick,
            on_done=self.hide,
            non_vestibular=True,
        )

    def _on_anim_tick(self, value: float) -> None:
        self._progress = value
        self._opacity = max(0.0, min(1.0, value))
        self.update()

    def _reposition(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        x = (parent.width() - self.width()) // 2
        y = max(12, parent.height() - self.height() - self._bottom_clearance)
        self.move(x, y)

    def paintEvent(self, event) -> None:  # type: ignore[override]
        if self._opacity <= 0.01:
            return

        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setOpacity(self._opacity)

        reduced = prefers_reduced_motion()
        scale = 1.0 if reduced else (0.96 + 0.04 * self._progress)
        dy = 0.0 if reduced else (8.0 * (1.0 - self._progress))

        cx = self.width() * 0.5
        cy = self.height() * 0.5
        painter.translate(cx, cy + dy)
        painter.scale(scale, scale)
        painter.translate(-cx, -cy)

        rect = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        if self._tone == "danger":
            bg_col = QColor(PALETTE["hud_trash_bg"])
            bg_col.setAlpha(240)
            border_col = QColor(PALETTE["border_trash"])
            icon_col = QColor(PALETTE["danger"])
            text_col = QColor(PALETTE["text_primary"])
        elif self._tone == "accent":
            bg_col = QColor(PALETTE["hud_bg"])
            bg_col.setAlpha(240)
            border_col = QColor(PALETTE["accent"])
            icon_col = QColor(PALETTE["accent"])
            text_col = QColor(PALETTE["text_primary"])
        else:
            bg_col = QColor(PALETTE["hud_bg"])
            bg_col.setAlpha(240)
            border_col = QColor(PALETTE["border_keycap"])
            icon_col = QColor(PALETTE["text_secondary"])
            text_col = QColor(PALETTE["text_primary"])

        painter.setBrush(QBrush(bg_col))
        painter.setPen(QPen(border_col, 1.0))
        painter.drawRoundedRect(rect, 17.0, 17.0)

        icon_rect = QRectF(14.0, (self.height() - 15.0) * 0.5, 15.0, 15.0)
        icon_scale = 1.0 if reduced else lerp_float(0.82, 1.0, self._pop_t)
        painter.save()
        painter.translate(icon_rect.center())
        painter.scale(icon_scale, icon_scale)
        painter.translate(-icon_rect.center())
        draw_icon(painter, self._icon_name, icon_rect, color=icon_col, stroke_width=1.6)
        painter.restore()

        font = QFont(UI_FONT_FAMILY, 10)
        font.setWeight(QFont.Weight.DemiBold)
        painter.setFont(font)
        painter.setPen(text_col)
        text_rect = QRectF(36.0, 0.0, self.width() - 48.0, float(self.height()))
        painter.drawText(
            text_rect,
            int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft),
            self._label,
        )
        painter.end()


class _ClickableSeekSlider(QSlider):
    """Horizontal seek slider supporting instant click-anywhere seek plus smooth drag."""

    clicked_value = pyqtSignal(int)

    def _value_for_x(self, x: float) -> int:
        opt = QStyleOptionSlider()
        self.initStyleOption(opt)
        groove = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            opt,
            QStyle.SubControl.SC_SliderGroove,
            self,
        )
        handle = self.style().subControlRect(
            QStyle.ComplexControl.CC_Slider,
            opt,
            QStyle.SubControl.SC_SliderHandle,
            self,
        )
        slider_len = max(1, handle.width())
        slider_min = groove.x()
        slider_max = max(slider_min + 1, groove.right() - slider_len + 1)
        pos = max(slider_min, min(slider_max, int(round(x - slider_len * 0.5))))
        return QStyle.sliderValueFromPosition(
            self.minimum(),
            self.maximum(),
            pos - slider_min,
            max(1, slider_max - slider_min),
            opt.upsideDown,
        )

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if (
            event.button() == Qt.MouseButton.LeftButton
            and self.maximum() > self.minimum()
        ):
            val = self._value_for_x(event.position().x())
            self.setValue(val)
            self.clicked_value.emit(val)
            self.sliderPressed.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if (
            (event.buttons() & Qt.MouseButton.LeftButton)
            and self.maximum() > self.minimum()
        ):
            val = self._value_for_x(event.position().x())
            self.setValue(val)
            self.sliderMoved.emit(val)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.sliderReleased.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class _VideoControlBar(QFrame):
    """Floating darkroom video transport bar: Play/Pause, Seek, Duration, Mute, Autoplay."""

    play_pause_clicked = pyqtSignal()
    seek_requested = pyqtSignal(int)
    mute_clicked = pyqtSignal()
    autoplay_toggled = pyqtSignal(bool)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("VideoControlBar")
        self.setFixedHeight(44)
        self._is_playing = False
        self._is_muted = False
        self._is_seeking = False
        self._duration_ms = 0
        self._appear_t = 1.0
        self._appear_anim = ValueAnimator(1.0, parent=self)
        self._target_x = 0
        self._target_y = 0
        self._opacity_eff: Optional[QGraphicsOpacityEffect] = None

        row = QHBoxLayout(self)
        row.setContentsMargins(10, 6, 10, 6)
        row.setSpacing(8)

        self.play_btn = QPushButton("", self)
        self.play_btn.setObjectName("VideoIconBtn")
        self.play_btn.setFixedSize(30, 30)
        self.play_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.play_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.play_btn.setToolTip(tr("video.play_tip"))
        self.play_btn.clicked.connect(self.play_pause_clicked.emit)
        row.addWidget(self.play_btn)

        self.seek_slider = _ClickableSeekSlider(Qt.Orientation.Horizontal, self)
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.seek_slider.setCursor(Qt.CursorShape.PointingHandCursor)
        self.seek_slider.clicked_value.connect(self._on_slider_moved)
        self.seek_slider.sliderPressed.connect(self._on_slider_pressed)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        self.seek_slider.sliderMoved.connect(self._on_slider_moved)
        self.seek_slider.actionTriggered.connect(self._on_slider_action)
        row.addWidget(self.seek_slider, 1)

        self.time_lbl = QLabel("00:00 / 00:00", self)
        self.time_lbl.setObjectName("VideoTimeLabel")
        self.time_lbl.setMinimumWidth(92)
        self.time_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        row.addWidget(self.time_lbl)

        self.mute_btn = QPushButton("", self)
        self.mute_btn.setObjectName("VideoIconBtn")
        self.mute_btn.setFixedSize(30, 30)
        self.mute_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.mute_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.mute_btn.setToolTip(tr("video.mute_tip"))
        self.mute_btn.clicked.connect(self.mute_clicked.emit)
        row.addWidget(self.mute_btn)

        self.autoplay_btn = QPushButton(tr("video.autoplay"), self)
        self.autoplay_btn.setObjectName("AutoplayToggleBtn")
        self.autoplay_btn.setCheckable(True)
        self.autoplay_btn.setChecked(True)
        self.autoplay_btn.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.autoplay_btn.setCursor(Qt.CursorShape.PointingHandCursor)
        self.autoplay_btn.setToolTip(tr("video.autoplay_tip"))
        self.autoplay_btn.toggled.connect(self.autoplay_toggled.emit)
        row.addWidget(self.autoplay_btn)

        self.refresh_theme()
        self.hide()

    def refresh_texts(self) -> None:
        self.play_btn.setToolTip(tr("video.play_tip"))
        self.mute_btn.setToolTip(tr("video.mute_tip"))
        self.autoplay_btn.setText(tr("video.autoplay"))
        self.autoplay_btn.setToolTip(tr("video.autoplay_tip"))

    def _on_slider_pressed(self) -> None:
        self._is_seeking = True

    def _on_slider_released(self) -> None:
        self._is_seeking = False
        self.seek_requested.emit(self.seek_slider.value())

    def _on_slider_moved(self, pos: int) -> None:
        self.time_lbl.setText(
            f"{_format_duration_ms(pos)} / {_format_duration_ms(self._duration_ms)}"
        )
        self.seek_requested.emit(pos)

    def _on_slider_action(self, _action: int) -> None:
        val = self.seek_slider.sliderPosition()
        self.time_lbl.setText(
            f"{_format_duration_ms(val)} / {_format_duration_ms(self._duration_ms)}"
        )
        self.seek_requested.emit(val)

    def set_playing(self, playing: bool) -> None:
        self._is_playing = playing
        icon_name = "pause" if playing else "play"
        self.play_btn.setIcon(render_icon(icon_name, 15, PALETTE["text_primary"]))
        self.play_btn.setIconSize(QSize(15, 15))

    def set_muted(self, muted: bool) -> None:
        self._is_muted = muted
        icon_name = "mute" if muted else "volume"
        self.mute_btn.setIcon(render_icon(icon_name, 15, PALETTE["text_secondary"]))
        self.mute_btn.setIconSize(QSize(15, 15))

    def set_autoplay(self, enabled: bool) -> None:
        self.autoplay_btn.blockSignals(True)
        self.autoplay_btn.setChecked(bool(enabled))
        self.autoplay_btn.blockSignals(False)

    def set_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self.seek_slider.setRange(0, self._duration_ms)
        self.time_lbl.setText(
            f"{_format_duration_ms(self.seek_slider.value())} / "
            f"{_format_duration_ms(self._duration_ms)}"
        )

    def set_position(self, pos_ms: int) -> None:
        pos = max(0, int(pos_ms))
        if not self._is_seeking:
            self.seek_slider.blockSignals(True)
            self.seek_slider.setValue(pos)
            self.seek_slider.blockSignals(False)
        self.time_lbl.setText(
            f"{_format_duration_ms(pos)} / {_format_duration_ms(self._duration_ms)}"
        )

    def refresh_theme(self) -> None:
        self.set_playing(self._is_playing)
        self.set_muted(self._is_muted)

    def show_animated(self, target_x: int, target_y: int) -> None:
        """Show transport bar with crisp 160ms upward slide-fade entrance."""
        self._target_x = int(target_x)
        self._target_y = int(target_y)
        was_visible = self.isVisible()
        self.show()
        self.raise_()

        if was_visible or prefers_reduced_motion():
            self._appear_anim.stop_silent(1.0)
            self._appear_t = 1.0
            self.setGraphicsEffect(None)
            self._opacity_eff = None
            self.move(self._target_x, self._target_y)
            return

        eff = QGraphicsOpacityEffect(self)
        eff.setOpacity(0.0)
        self.setGraphicsEffect(eff)
        self._opacity_eff = eff
        self._appear_anim.animate_to(
            target=1.0,
            duration_ms=160,
            curve=ease_out(),
            start_from=0.0,
            on_value=self._on_appear_tick,
            on_done=self._on_appear_done,
            non_vestibular=True,
        )

    def _on_appear_tick(self, val: float) -> None:
        self._appear_t = max(0.0, min(1.0, float(val)))
        if self._opacity_eff is not None:
            try:
                self._opacity_eff.setOpacity(self._appear_t)
            except RuntimeError:
                pass
        dy = int(round(8.0 * (1.0 - self._appear_t)))
        self.move(self._target_x, self._target_y + dy)

    def _on_appear_done(self) -> None:
        self._appear_t = 1.0
        self.move(self._target_x, self._target_y)
        self._opacity_eff = None
        self.setGraphicsEffect(None)

    def cancel_entrance(self) -> None:
        self._appear_anim.stop_silent(1.0)
        self._opacity_eff = None
        self.setGraphicsEffect(None)


class PhotoCanvas(QGraphicsView):
    """Darkroom Technical media viewport with fast QImageReader, QtMultimedia video, zoom/pan, and motion."""

    autoplay_toggled = pyqtSignal(bool)
    video_metadata_ready = pyqtSignal(int, int, int)

    MIN_ZOOM = 0.2
    MAX_ZOOM = 10.0
    ZOOM_STEP = 1.15
    CACHE_CAPACITY = 32
    MAX_DECODE_DIM = 2560

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self.setScene(self._scene)

        self.setBackgroundBrush(QBrush(QColor(PALETTE["bg_canvas"])))
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setRenderHints(
            QPainter.RenderHint.Antialiasing
            | QPainter.RenderHint.SmoothPixmapTransform
        )
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFocusPolicy(Qt.FocusPolicy.ClickFocus)

        self._pixmap_cache: OrderedDict[Tuple[str, int], Tuple[QPixmap, int, int]] = (
            OrderedDict()
        )
        self._pending_keys: set[Tuple[str, int]] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(4)
        self._preload_signals = _PreloadSignals(self)
        self._preload_signals.loaded.connect(self._on_preload_finished)

        self._current_path: Optional[Path] = None
        self._content_rect: Optional[QRectF] = None
        self._active_item: Optional[object] = None
        self._fit_scale: float = 1.0
        self._zoom_ratio: float = 1.0
        self._target_zoom: float = 1.0
        self._is_fitted: bool = True

        # Custom background support (for standalone canvas or transparent window overlay)
        self._transparent_backdrop = False
        self._custom_bg_pixmap: Optional[QPixmap] = None
        self._custom_bg_dim: float = 0.68

        # Video playback engine
        self._is_video_mode = False
        self._video_autoplay = True
        self._video_seek_seconds = 3
        self._media_player = QMediaPlayer(self)
        self._media_player.setLoops(QMediaPlayer.Loops.Once)
        self._audio_output = QAudioOutput(self)
        self._audio_output.setVolume(0.85)
        self._media_player.setAudioOutput(self._audio_output)
        self._video_item: Optional[QGraphicsVideoItem] = None
        self._video_frame_item: Optional[QGraphicsRectItem] = None
        self._video_shadow_outer: Optional[QGraphicsRectItem] = None
        self._video_shadow_inner: Optional[QGraphicsRectItem] = None

        self._video_control_bar = _VideoControlBar(self)
        self._video_control_bar.play_pause_clicked.connect(self.toggle_video_playback)
        self._video_control_bar.seek_requested.connect(self._on_seek_slider)
        self._video_control_bar.mute_clicked.connect(self._toggle_mute)
        self._video_control_bar.autoplay_toggled.connect(
            self._on_control_autoplay_toggled
        )

        self._media_player.playbackStateChanged.connect(self._on_playback_state_changed)
        self._media_player.positionChanged.connect(self._on_position_changed)
        self._media_player.durationChanged.connect(self._on_duration_changed)
        self._media_player.errorOccurred.connect(self._on_media_error)

        # Interruptible motion animators
        self._transition_anim = ValueAnimator(1.0, parent=self)
        self._transition_dir: int = 0
        self._zoom_anim = ValueAnimator(1.0, parent=self)
        self._zoom_anchor_view = QPointF()
        self._zoom_anchor_scene = QPointF()
        self._reset_anim = ValueAnimator(1.0, parent=self)
        self._reset_start_zoom: float = 1.0
        self._reset_start_center = QPointF()

        self._hud = _ActionHudPill(self)

    def set_transparent_backdrop(self, enabled: bool) -> None:
        """Make viewport transparent so MainContainer paints a seamless window background."""
        self._transparent_backdrop = bool(enabled)
        if self._transparent_backdrop:
            self.setBackgroundBrush(QBrush(Qt.BrushStyle.NoBrush))
            self.viewport().setAutoFillBackground(False)
            self.setStyleSheet("QGraphicsView { background: transparent; border: none; }")
        else:
            self.setBackgroundBrush(QBrush(QColor(PALETTE["bg_canvas"])))
            self.viewport().setAutoFillBackground(True)
            self.setStyleSheet("")
        self.viewport().update()

    def set_custom_background(
        self, path: Optional[str], dim_opacity: float | int = 0.68
    ) -> None:
        """Load custom background image + scrim opacity (0.20..0.90 or 20..90)."""
        raw_dim = float(dim_opacity)
        if raw_dim > 1.0:
            raw_dim = raw_dim / 100.0
        self._custom_bg_dim = max(0.20, min(0.90, raw_dim))
        if path and Path(path).is_file():
            pix = QPixmap(str(path))
            self._custom_bg_pixmap = pix if not pix.isNull() else None
        else:
            self._custom_bg_pixmap = None
        self.viewport().update()

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # type: ignore[override]
        if self._transparent_backdrop:
            return
        if self._custom_bg_pixmap is None or self._custom_bg_pixmap.isNull():
            super().drawBackground(painter, rect)
            return

        painter.save()
        painter.resetTransform()
        vp_rect = self.viewport().rect()
        painter.fillRect(vp_rect, QColor(PALETTE["bg_canvas"]))

        scaled = self._custom_bg_pixmap.scaled(
            vp_rect.size(),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = max(0, (scaled.width() - vp_rect.width()) // 2)
        sy = max(0, (scaled.height() - vp_rect.height()) // 2)
        painter.drawPixmap(
            vp_rect, scaled, vp_rect.translated(sx, sy)
        )
        rgb = [int(part.strip()) for part in PALETTE["scrim_rgb"].split(",")]
        scrim = QColor(rgb[0], rgb[1], rgb[2], int(round(255 * self._custom_bg_dim)))
        painter.fillRect(vp_rect, scrim)
        painter.restore()

    def refresh_theme(self) -> None:
        if not self._transparent_backdrop:
            self.setBackgroundBrush(QBrush(QColor(PALETTE["bg_canvas"])))
        self._video_control_bar.refresh_theme()
        self._hud.update()
        self.viewport().update()

    def refresh_texts(self) -> None:
        self._video_control_bar.refresh_texts()

    def clear_cache(self) -> None:
        self._pixmap_cache.clear()
        self._pending_keys.clear()

    def rekey_cache(self, old_path: Path, new_path: Path) -> None:
        """Carry a cached QPixmap across a file move so it never re-decodes."""
        old_key_prefix = str(old_path)
        for key in list(self._pixmap_cache.keys()):
            if key[0] == old_key_prefix:
                val = self._pixmap_cache.pop(key)
                new_key = file_cache_key(new_path)
                self._pixmap_cache[new_key] = val
                self._pixmap_cache.move_to_end(new_key)
                break

    def _put_cache(
        self, key: Tuple[str, int], pixmap: QPixmap, orig_w: int, orig_h: int
    ) -> None:
        if key in self._pixmap_cache:
            self._pixmap_cache.move_to_end(key)
        self._pixmap_cache[key] = (pixmap, orig_w, orig_h)
        while len(self._pixmap_cache) > self.CACHE_CAPACITY:
            self._pixmap_cache.popitem(last=False)

    def _get_cached_entry(
        self, path: Path
    ) -> Optional[Tuple[QPixmap, int, int]]:
        key = file_cache_key(path)
        entry = self._pixmap_cache.get(key)
        if entry is not None:
            self._pixmap_cache.move_to_end(key)
        return entry

    def _on_preload_finished(
        self,
        path_str: str,
        mtime_ns: object,
        qimg: object,
        orig_w: int,
        orig_h: int,
    ) -> None:
        key = (path_str, int(mtime_ns))
        self._pending_keys.discard(key)
        if isinstance(qimg, QImage) and not qimg.isNull():
            self._put_cache(key, QPixmap.fromImage(qimg), orig_w, orig_h)

    def preload_paths(self, paths: Iterable[Path]) -> None:
        """Schedule asynchronous QThreadPool preload of neighbor images into LRU cache."""
        for path in paths:
            if not path.is_file() or is_video_file(path):
                continue
            key = file_cache_key(path)
            if key in self._pixmap_cache or key in self._pending_keys:
                continue
            self._pending_keys.add(key)
            task = _ImagePreloadTask(
                path, key[1], self.MAX_DECODE_DIM, self._preload_signals
            )
            self._pool.start(task)

    def show_action_hud(
        self, label: str, icon_name: str = "check", tone: str = "accent"
    ) -> None:
        """Show non-blocking floating Action HUD pill at bottom-center of canvas."""
        self._hud.set_bottom_clearance(72 if self._is_video_mode else 18)
        self._hud.show_message(label=label, icon_name=icon_name, tone=tone)

    # --- Video lifecycle & playback controls ---

    def is_video_active(self) -> bool:
        """Return True whenever a video item is currently displayed on canvas."""
        return self._is_video_mode

    def is_playing_video(self) -> bool:
        """Return True when video mode is active and playback state is Playing."""
        return (
            self._is_video_mode
            and self._media_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )

    def is_video_finished(self) -> bool:
        """Return True when video has finished playing through once."""
        if not self._is_video_mode:
            return False
        status = self._media_player.mediaStatus()
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            return True
        dur = self._media_player.duration()
        pos = self._media_player.position()
        state = self._media_player.playbackState()
        return (
            dur > 0
            and state == QMediaPlayer.PlaybackState.StoppedState
            and pos >= max(0, dur - 250)
        )

    def set_video_seek_seconds(self, seconds: int) -> None:
        self._video_seek_seconds = max(1, min(60, int(seconds)))

    def set_video_autoplay(self, enabled: bool) -> None:
        self._video_autoplay = bool(enabled)
        self._video_control_bar.set_autoplay(self._video_autoplay)

    def _on_control_autoplay_toggled(self, enabled: bool) -> None:
        self._video_autoplay = bool(enabled)
        self.autoplay_toggled.emit(self._video_autoplay)

    def toggle_video_playback(self) -> None:
        if not self._is_video_mode:
            return
        if (
            self._media_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        ):
            self._media_player.pause()
            self.show_action_hud(tr("hud.pause"), icon_name="pause", tone="neutral")
        else:
            if (
                self._media_player.mediaStatus()
                == QMediaPlayer.MediaStatus.EndOfMedia
            ):
                self._media_player.setPosition(0)
            self._media_player.play()
            self.show_action_hud(tr("hud.play"), icon_name="play", tone="accent")

    def seek_video_relative(self, delta_ms: Optional[int] = None) -> None:
        if not self._is_video_mode:
            return
        step_ms = int(delta_ms) if delta_ms is not None else self._video_seek_seconds * 1000
        dur = self._media_player.duration()
        pos = self._media_player.position()
        target = max(0, pos + step_ms)
        if dur > 0:
            target = min(dur, target)
        self._media_player.setPosition(target)
        self._video_control_bar.set_position(target)
        sec = max(1, abs(step_ms) // 1000)
        prefix = "+" if step_ms >= 0 else "-"
        self.show_action_hud(
            f"{prefix}{sec}s",
            icon_name="chevron_right" if step_ms >= 0 else "chevron_left",
            tone="neutral",
        )

    def _on_seek_slider(self, pos_ms: int) -> None:
        if self._is_video_mode:
            self._media_player.setPosition(max(0, int(pos_ms)))

    def _toggle_mute(self) -> None:
        muted = not self._audio_output.isMuted()
        self._audio_output.setMuted(muted)
        self._video_control_bar.set_muted(muted)

    def _on_playback_state_changed(self, state: QMediaPlayer.PlaybackState) -> None:
        self._video_control_bar.set_playing(
            state == QMediaPlayer.PlaybackState.PlayingState
        )

    def _on_position_changed(self, pos_ms: int) -> None:
        if self._is_video_mode:
            self._video_control_bar.set_position(pos_ms)

    def _on_duration_changed(self, dur_ms: int) -> None:
        if self._is_video_mode:
            self._video_control_bar.set_duration(dur_ms)
            if self._video_item is not None:
                sz = self._video_item.nativeSize()
                self.video_metadata_ready.emit(
                    int(round(sz.width())) if sz.width() > 0 else 0,
                    int(round(sz.height())) if sz.height() > 0 else 0,
                    int(dur_ms),
                )

    def _on_video_native_size_changed(self, size: QSizeF) -> None:
        if not self._is_video_mode or self._video_item is None:
            return
        w = float(size.width())
        h = float(size.height())
        if w <= 0 or h <= 0:
            return
        rect = QRectF(0.0, 0.0, w, h)
        self._video_item.setSize(QSizeF(w, h))
        if self._video_shadow_outer is not None:
            self._video_shadow_outer.setRect(rect.adjusted(-4.0, -2.0, 4.0, 8.0))
        if self._video_shadow_inner is not None:
            self._video_shadow_inner.setRect(rect.adjusted(-2.0, -1.0, 2.0, 4.0))
        if self._video_frame_item is not None:
            self._video_frame_item.setRect(rect)
        self._content_rect = rect
        pad = max(160.0, max(w, h) * 0.25)
        self._scene.setSceneRect(rect.adjusted(-pad, -pad, pad, pad))
        self.reset_view(animate=False)
        self.video_metadata_ready.emit(
            int(round(w)), int(round(h)), int(self._media_player.duration())
        )

    def _on_media_error(self, _error, _error_string: str) -> None:
        if not self._is_video_mode or self._current_path is None:
            return
        # Show graceful fallback card inside scene while keeping video mode flag coherent
        self._video_control_bar.set_playing(False)

    def release_media(self) -> None:
        """Stop playback and clear source immediately so Windows file handles release before moves."""
        was_video = self._is_video_mode
        released_path = self._current_path
        self._is_video_mode = False
        self._video_control_bar.cancel_entrance()
        self._video_control_bar.hide()
        self._hud.set_bottom_clearance(18)
        self._video_item = None
        self._video_frame_item = None
        self._video_shadow_outer = None
        self._video_shadow_inner = None
        self._media_player.stop()
        self._media_player.setSource(QUrl())
        self._media_player.setVideoOutput(None)
        if was_video:
            QCoreApplication.processEvents(
                QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 50
            )
            if released_path is not None and released_path.is_file():
                import os
                import time

                for _ in range(25):
                    try:
                        os.rename(str(released_path), str(released_path))
                        break
                    except PermissionError:
                        QCoreApplication.processEvents(
                            QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents, 20
                        )
                        time.sleep(0.015)
                    except OSError:
                        break

    def set_photo(
        self,
        path: Optional[Path],
        preload_paths: Iterable[Path] = (),
        direction: int = 0,
    ) -> None:
        """Display photo or video with darkroom framing, 95ms settle motion, and async preloading."""
        self._zoom_anim.stop_silent(1.0)
        self._reset_anim.stop_silent(1.0)
        self._transition_anim.stop_silent(1.0)

        self.release_media()
        self._current_path = path
        self._active_item = None
        self._scene.clear()
        self._content_rect = None
        self._zoom_ratio = 1.0
        self._target_zoom = 1.0
        self._is_fitted = True

        if path is None:
            return

        if is_video_file(path):
            self._load_video_item(path, preload_paths=preload_paths)
            return

        cached = self._get_cached_entry(path)
        pixmap: Optional[QPixmap] = cached[0] if cached is not None else None
        if pixmap is None:
            qimg, orig_w, orig_h = load_scaled_qimage(
                path, max_dim=self.MAX_DECODE_DIM
            )
            if qimg is not None and not qimg.isNull():
                pixmap = QPixmap.fromImage(qimg)
                self._put_cache(file_cache_key(path), pixmap, orig_w, orig_h)

        if pixmap is None or pixmap.isNull():
            self._render_error_placeholder(path)
        else:
            self._render_pixmap(pixmap)

        self.reset_view(animate=False)

        if self._active_item is not None:
            self._transition_dir = int(direction)
            self._transition_anim.animate_to(
                target=1.0,
                duration_ms=95,
                curve=ease_out(),
                start_from=0.0,
                on_value=self._apply_transition_tick,
                non_vestibular=True,
            )

        if preload_paths:
            self.preload_paths(preload_paths)

    set_media = set_photo

    def _load_video_item(
        self, path: Path, preload_paths: Iterable[Path] = ()
    ) -> None:
        self._is_video_mode = True
        self._hud.set_bottom_clearance(72)
        w, h = 1280.0, 720.0
        rect = QRectF(0.0, 0.0, w, h)

        shadow_outer = QGraphicsRectItem(rect.adjusted(-4.0, -2.0, 4.0, 8.0))
        shadow_outer.setBrush(QBrush(QColor(0, 0, 0, 72)))
        shadow_outer.setPen(QPen(Qt.PenStyle.NoPen))
        shadow_outer.setZValue(-2)
        self._scene.addItem(shadow_outer)
        self._video_shadow_outer = shadow_outer

        shadow_inner = QGraphicsRectItem(rect.adjusted(-2.0, -1.0, 2.0, 4.0))
        shadow_inner.setBrush(QBrush(QColor(0, 0, 0, 120)))
        shadow_inner.setPen(QPen(Qt.PenStyle.NoPen))
        shadow_inner.setZValue(-1)
        self._scene.addItem(shadow_inner)
        self._video_shadow_inner = shadow_inner

        video_item = QGraphicsVideoItem()
        video_item.setSize(QSizeF(w, h))
        video_item.setZValue(1)
        video_item.nativeSizeChanged.connect(self._on_video_native_size_changed)
        self._scene.addItem(video_item)
        self._video_item = video_item

        frame_pen = QPen(QColor(PALETTE["border_photo"]), 1.0)
        frame_pen.setCosmetic(True)
        frame_item = QGraphicsRectItem(rect, parent=video_item)
        frame_item.setPen(frame_pen)
        frame_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        frame_item.setZValue(2)
        self._video_frame_item = frame_item

        self._active_item = video_item
        self._content_rect = rect
        pad = max(160.0, max(w, h) * 0.25)
        self._scene.setSceneRect(rect.adjusted(-pad, -pad, pad, pad))
        self.reset_view(animate=False)

        self._video_control_bar.set_duration(0)
        self._video_control_bar.set_position(0)
        self._video_control_bar.set_autoplay(self._video_autoplay)
        self._video_control_bar.set_muted(self._audio_output.isMuted())
        tx, ty = self._compute_video_bar_pos()
        self._video_control_bar.show_animated(tx, ty)

        if not prefers_reduced_motion():
            self._transition_dir = 0
            self._transition_anim.animate_to(
                target=1.0,
                duration_ms=95,
                curve=ease_out(),
                start_from=0.0,
                on_value=self._apply_transition_tick,
                non_vestibular=True,
            )

        self._media_player.setVideoOutput(video_item)
        self._media_player.setSource(QUrl.fromLocalFile(str(path)))
        if self._video_autoplay:
            self._media_player.play()
        else:
            self._media_player.pause()

        if preload_paths:
            self.preload_paths(preload_paths)

    def _compute_video_bar_pos(self) -> Tuple[int, int]:
        bar_w = max(280, min(560, self.width() - 32))
        bar_h = self._video_control_bar.height()
        self._video_control_bar.setFixedWidth(bar_w)
        x = (self.width() - bar_w) // 2
        y = max(12, self.height() - bar_h - 14)
        return x, y

    def _reposition_video_bar(self) -> None:
        x, y = self._compute_video_bar_pos()
        self._video_control_bar._target_x = x
        self._video_control_bar._target_y = y
        dy = int(round(8.0 * (1.0 - self._video_control_bar._appear_t)))
        self._video_control_bar.move(x, y + dy)

    def _apply_transition_tick(self, progress: float) -> None:
        item = self._active_item
        t = max(0.0, min(1.0, progress))
        if isinstance(item, QGraphicsVideoItem):
            op = 0.45 + 0.55 * t
            item.setOpacity(op)
            if self._video_frame_item is not None:
                self._video_frame_item.setOpacity(op)
            return
        if not isinstance(item, QGraphicsPixmapItem):
            return
        if prefers_reduced_motion():
            item.setOpacity(0.75 + 0.25 * t)
            item.setScale(1.0)
            item.setPos(0.0, 0.0)
            return

        item.setOpacity(0.45 + 0.55 * t)
        item.setScale(0.985 + 0.015 * t)
        dx_px = self._transition_dir * 14.0 * (1.0 - t)
        scene_dx = dx_px / max(0.05, self._fit_scale)
        item.setPos(scene_dx, 0.0)

    def _render_pixmap(self, pixmap: QPixmap) -> None:
        w = float(pixmap.width())
        h = float(pixmap.height())
        rect = QRectF(0.0, 0.0, w, h)

        shadow_outer = QGraphicsRectItem(rect.adjusted(-4.0, -2.0, 4.0, 8.0))
        shadow_outer.setBrush(QBrush(QColor(0, 0, 0, 72)))
        shadow_outer.setPen(QPen(Qt.PenStyle.NoPen))
        shadow_outer.setZValue(-2)
        self._scene.addItem(shadow_outer)

        shadow_inner = QGraphicsRectItem(rect.adjusted(-2.0, -1.0, 2.0, 4.0))
        shadow_inner.setBrush(QBrush(QColor(0, 0, 0, 120)))
        shadow_inner.setPen(QPen(Qt.PenStyle.NoPen))
        shadow_inner.setZValue(-1)
        self._scene.addItem(shadow_inner)

        pixmap_item = QGraphicsPixmapItem(pixmap)
        pixmap_item.setTransformationMode(
            Qt.TransformationMode.SmoothTransformation
        )
        pixmap_item.setTransformOriginPoint(QPointF(w * 0.5, h * 0.5))
        pixmap_item.setZValue(1)
        self._scene.addItem(pixmap_item)

        frame_pen = QPen(QColor(PALETTE["border_photo"]), 1.0)
        frame_pen.setCosmetic(True)
        frame_item = QGraphicsRectItem(rect, parent=pixmap_item)
        frame_item.setPen(frame_pen)
        frame_item.setBrush(QBrush(Qt.BrushStyle.NoBrush))
        frame_item.setZValue(2)

        self._active_item = pixmap_item
        self._content_rect = rect
        pad = max(160.0, max(w, h) * 0.25)
        self._scene.setSceneRect(rect.adjusted(-pad, -pad, pad, pad))

    def _render_error_placeholder(self, path: Path) -> None:
        rect = QRectF(0, 0, 520, 260)
        box = QGraphicsRectItem(rect)
        box.setBrush(QBrush(QColor(PALETTE["bg_panel"])))
        box.setPen(QPen(QColor(PALETTE["border"]), 1.0))
        self._scene.addItem(box)

        text_item = QGraphicsTextItem(
            f"{path.name}\n\nMedia could not be loaded.\n"
            "File may be corrupted or unsupported."
        )
        text_item.setDefaultTextColor(QColor(PALETTE["text_secondary"]))
        text_item.setFont(QFont(UI_FONT_FAMILY, 11))
        text_item.setTextWidth(460)
        text_rect = text_item.boundingRect()
        text_item.setPos(
            (rect.width() - text_rect.width()) / 2.0,
            (rect.height() - text_rect.height()) / 2.0,
        )
        self._scene.addItem(text_item)

        self._content_rect = rect
        self._scene.setSceneRect(rect.adjusted(-20, -20, 20, 20))

    def reset_view(self, animate: bool = False) -> None:
        """Fit current content cleanly inside viewport (optionally animated over 180ms)."""
        if self._content_rect is None:
            self.resetTransform()
            self._fit_scale = 1.0
            self._zoom_ratio = 1.0
            self._target_zoom = 1.0
            self._is_fitted = True
            return

        if animate and not self._is_fitted and not prefers_reduced_motion():
            self._zoom_anim.set_immediate(self._zoom_ratio)
            self._reset_start_zoom = self._zoom_ratio
            self._reset_start_center = self.mapToScene(
                self.viewport().rect().center()
            )
            self._target_zoom = 1.0
            self._reset_anim.animate_to(
                target=1.0,
                duration_ms=180,
                curve=ease_out(),
                start_from=0.0,
                on_value=self._apply_reset_tick,
                on_done=lambda: self.reset_view(animate=False),
            )
            return

        self.resetTransform()
        bottom_pad = 64.0 if self._is_video_mode else 16.0
        padded = self._content_rect.adjusted(-16, -16, 16, bottom_pad)
        self.fitInView(padded, Qt.AspectRatioMode.KeepAspectRatio)
        self._fit_scale = max(0.01, self.transform().m11())
        self._zoom_ratio = 1.0
        self._target_zoom = 1.0
        self._is_fitted = True

    def _apply_reset_tick(self, progress: float) -> None:
        if self._content_rect is None:
            return
        t = max(0.0, min(1.0, progress))
        z = self._reset_start_zoom + (1.0 - self._reset_start_zoom) * t
        target_center = self._content_rect.center()
        cx = self._reset_start_center.x() + (
            target_center.x() - self._reset_start_center.x()
        ) * t
        cy = self._reset_start_center.y() + (
            target_center.y() - self._reset_start_center.y()
        ) * t
        s = self._fit_scale * z
        self.setTransform(QTransform.fromScale(s, s))
        self.centerOn(cx, cy)
        self._zoom_ratio = z

    def _apply_zoom_tick(self, zoom: float) -> None:
        if self._content_rect is None:
            return
        self._zoom_ratio = zoom
        s = self._fit_scale * zoom
        self.setTransform(QTransform.fromScale(s, s))
        if zoom <= 1.0:
            self.centerOn(self._content_rect.center())
        else:
            vw = float(self.viewport().width())
            vh = float(self.viewport().height())
            cx = (
                self._zoom_anchor_scene.x()
                - (self._zoom_anchor_view.x() - vw * 0.5) / s
            )
            cy = (
                self._zoom_anchor_scene.y()
                - (self._zoom_anchor_view.y() - vh * 0.5) / s
            )
            self.centerOn(cx, cy)

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._content_rect is None:
            super().wheelEvent(event)
            return
        delta = event.angleDelta().y()
        if delta == 0:
            return

        factor = self.ZOOM_STEP if delta > 0 else (1.0 / self.ZOOM_STEP)
        new_target = max(
            self.MIN_ZOOM, min(self.MAX_ZOOM, self._target_zoom * factor)
        )
        if abs(new_target - self._target_zoom) < 1e-4:
            event.accept()
            return

        self._reset_anim.set_immediate(1.0)
        self._target_zoom = new_target
        self._is_fitted = False
        self._zoom_anchor_view = QPointF(event.position())
        self._zoom_anchor_scene = self.mapToScene(event.position().toPoint())

        self._zoom_anim.animate_to(
            target=new_target,
            duration_ms=110,
            curve=ease_out(),
            start_from=self._zoom_ratio,
            on_value=self._apply_zoom_tick,
        )
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_view(animate=True)
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._reposition_video_bar()
        self._hud._reposition()
        if self._is_fitted and self._content_rect is not None:
            self.reset_view(animate=False)

    def shutdown(self) -> None:
        """Stop video player and wait briefly for active preload workers on close."""
        self.release_media()
        self._pool.clear()
        self._pool.waitForDone(500)
