"""Cubic-bezier motion system, reduced-motion detection, and interruptible ValueAnimator."""

from __future__ import annotations

import ctypes
import os
from typing import Callable, Optional, Sequence

from PyQt6.QtCore import (
    QEasingCurve,
    QObject,
    QPointF,
    QTimer,
    Qt,
    QVariantAnimation,
)
from PyQt6.QtGui import QColor, QPainter, QPixmap
from PyQt6.QtWidgets import QGraphicsOpacityEffect, QStackedWidget, QWidget

_SPI_GETCLIENTAREAANIMATION = 0x1042


def _bezier_curve(x1: float, y1: float, x2: float, y2: float) -> QEasingCurve:
    curve = QEasingCurve(QEasingCurve.Type.BezierSpline)
    curve.addCubicBezierSegment(
        QPointF(x1, y1), QPointF(x2, y2), QPointF(1.0, 1.0)
    )
    return curve


def ease_out() -> QEasingCurve:
    """Strong UI ease-out: cubic-bezier(0.23, 1.0, 0.32, 1.0)."""
    return _bezier_curve(0.23, 1.0, 0.32, 1.0)


def ease_in_out() -> QEasingCurve:
    """Strong on-screen move ease-in-out: cubic-bezier(0.77, 0.0, 0.175, 1.0)."""
    return _bezier_curve(0.77, 0.0, 0.175, 1.0)


def ease_drawer() -> QEasingCurve:
    """Drawer slide curve: cubic-bezier(0.32, 0.72, 0.0, 1.0)."""
    return _bezier_curve(0.32, 0.72, 0.0, 1.0)


def ease_overshoot() -> QEasingCurve:
    """Subtle crisp spring-overshoot curve: cubic-bezier(0.34, 1.48, 0.64, 1.0)."""
    return _bezier_curve(0.34, 1.48, 0.64, 1.0)


def lerp_float(a: float, b: float, t: float) -> float:
    """Linearly interpolate a -> b clamped cleanly to [0.0, 1.0]."""
    clamped = max(0.0, min(1.0, float(t)))
    return float(a) + (float(b) - float(a)) * clamped


def lerp_color(c1: QColor | str, c2: QColor | str, t: float) -> QColor:
    """Interpolate RGBA components smoothly between two colors."""
    qc1 = QColor(c1) if isinstance(c1, str) else QColor(c1)
    qc2 = QColor(c2) if isinstance(c2, str) else QColor(c2)
    clamped = max(0.0, min(1.0, float(t)))
    return QColor(
        int(round(qc1.red() + (qc2.red() - qc1.red()) * clamped)),
        int(round(qc1.green() + (qc2.green() - qc1.green()) * clamped)),
        int(round(qc1.blue() + (qc2.blue() - qc1.blue()) * clamped)),
        int(round(qc1.alpha() + (qc2.alpha() - qc1.alpha()) * clamped)),
    )


# Compatibility aliases
ease_out_quint = ease_out
ease_in_out_quart = ease_in_out
ease_spring_drawer = ease_drawer


def prefers_reduced_motion() -> bool:
    """Query Windows SPI_GETCLIENTAREAANIMATION; returns False if unavailable."""
    if os.name != "nt":
        return False
    try:
        enabled = ctypes.c_int(1)
        ok = ctypes.windll.user32.SystemParametersInfoW(
            _SPI_GETCLIENTAREAANIMATION,
            0,
            ctypes.byref(enabled),
            0,
        )
        if ok:
            return not bool(enabled.value)
    except Exception:
        return False
    return False


class ValueAnimator(QVariantAnimation):
    """Interruptible single-float animator that retargets smoothly from live value."""

    def __init__(
        self,
        initial: float = 0.0,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self._value: float = float(initial)
        self._generation: int = 0
        self._on_value: Optional[Callable[[float], None]] = None
        self._on_done: Optional[Callable[[], None]] = None
        self.valueChanged.connect(self._handle_value_changed)
        self.finished.connect(self._handle_finished)

    @property
    def current_float(self) -> float:
        return self._value

    def set_immediate(
        self,
        value: float,
        on_value: Optional[Callable[[float], None]] = None,
        notify: bool = True,
    ) -> None:
        """Stop any active animation cleanly and optionally emit value."""
        self._generation += 1
        self._on_done = None
        if self.state() == QVariantAnimation.State.Running:
            self.stop()
        self._value = float(value)
        if on_value is not None:
            self._on_value = on_value
        if notify and self._on_value is not None:
            self._on_value(self._value)

    def stop_silent(self, value: Optional[float] = None) -> None:
        """Stop active animation without firing _on_value or _on_done callbacks."""
        self.set_immediate(
            self._value if value is None else float(value),
            notify=False,
        )

    def animate_to(
        self,
        target: float,
        duration_ms: int,
        curve: Optional[QEasingCurve] = None,
        start_from: Optional[float] = None,
        on_value: Optional[Callable[[float], None]] = None,
        on_done: Optional[Callable[[], None]] = None,
        non_vestibular: bool = False,
    ) -> None:
        """Animate float toward target, retargeting from live value if already running."""
        self._generation += 1
        active_gen = self._generation

        if self.state() == QVariantAnimation.State.Running:
            live = self.currentValue()
            if isinstance(live, (int, float)):
                self._value = float(live)
            self.stop()

        start_val = float(start_from) if start_from is not None else self._value
        end_val = float(target)

        if on_value is not None:
            self._on_value = on_value
        self._on_done = on_done

        reduced = prefers_reduced_motion()
        effective_ms = 0 if (reduced and not non_vestibular) else max(0, int(duration_ms))

        if effective_ms == 0 or abs(end_val - start_val) < 1e-6:
            self._value = end_val
            if self._on_value is not None:
                self._on_value(end_val)
            cb = self._on_done
            self._on_done = None
            if cb is not None and active_gen == self._generation:
                cb()
            return

        self._value = start_val
        self.setStartValue(start_val)
        self.setEndValue(end_val)
        self.setDuration(effective_ms)
        self.setEasingCurve(curve if curve is not None else ease_out())
        if self._on_value is not None:
            self._on_value(start_val)
        self.start()

    def _handle_value_changed(self, value: object) -> None:
        if isinstance(value, (int, float)):
            self._value = float(value)
            if self._on_value is not None:
                self._on_value(self._value)

    def _handle_finished(self) -> None:
        cb = self._on_done
        self._on_done = None
        if cb is not None:
            cb()


def pulse_keycap(widget: QWidget, duration_ms: int = 140) -> None:
    """Trigger a tactile shortcut/press pulse on a keycap button if supported."""
    trigger = getattr(widget, "trigger_pulse", None)
    if callable(trigger):
        trigger(duration_ms=duration_ms)


def animate_sidebar_width(
    animator: ValueAnimator,
    sidebar: QWidget,
    expand: bool,
    target_width: int = 260,
    duration_ms: int = 180,
    on_finished: Optional[Callable[[], None]] = None,
) -> None:
    """Slide sidebar width smoothly between 0 and target_width using ease_drawer()."""
    if expand:
        sidebar.show()
        start = float(sidebar.width() if sidebar.isVisible() else 0)
        end = float(target_width)
    else:
        start = float(sidebar.width() if sidebar.isVisible() else 0)
        end = 0.0

    def _apply(val: float) -> None:
        w = max(0, int(round(val)))
        sidebar.setFixedWidth(w)

    def _done() -> None:
        if not expand:
            sidebar.setFixedWidth(0)
            sidebar.hide()
        else:
            sidebar.setFixedWidth(target_width)
        if on_finished is not None:
            on_finished()

    animator.animate_to(
        target=end,
        duration_ms=duration_ms,
        curve=ease_drawer(),
        start_from=start,
        on_value=_apply,
        on_done=_done,
    )


class WidgetSlideFadeTransition(QWidget):
    """Mouse-passthrough outgoing-snapshot crossfade + subtle directional slide for QStackedWidget."""

    def __init__(self, stack: QStackedWidget, duration_ms: int = 145) -> None:
        super().__init__(stack)
        self._stack = stack
        self._duration_ms = max(0, int(duration_ms))
        self._snapshot: Optional[QPixmap] = None
        self._direction: int = 1
        self._t: float = 0.0
        self._animator = ValueAnimator(0.0, parent=self)

        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.hide()

    def transition_to(
        self,
        switch_fn: Callable[[], None],
        outgoing: Optional[QWidget],
        incoming: Optional[QWidget],
        direction: int = 1,
    ) -> None:
        """Capture outgoing snapshot, swap view synchronously (0ms block), and crossfade overlay out."""
        if (
            outgoing is None
            or incoming is None
            or outgoing is incoming
            or not self._stack.isVisible()
            or self._stack.width() <= 0
            or self._stack.height() <= 0
            or prefers_reduced_motion()
        ):
            self.cancel()
            switch_fn()
            return

        # Hide stale transition overlay before grabbing outgoing so no ghost snapshot bleeds in
        self._animator.stop_silent(0.0)
        self.hide()
        snap = outgoing.grab()
        switch_fn()

        if snap.isNull():
            self.cancel()
            return

        self._snapshot = snap
        self._direction = 1 if direction >= 0 else -1
        self._t = 0.0
        self.setGeometry(self._stack.rect())
        self.show()
        self.raise_()
        self._animator.animate_to(
            target=1.0,
            duration_ms=self._duration_ms,
            curve=ease_out(),
            start_from=0.0,
            on_value=self._on_tick,
            on_done=self._on_done,
            non_vestibular=True,
        )

    def cancel(self) -> None:
        self._animator.stop_silent(0.0)
        self._snapshot = None
        self._t = 0.0
        self.hide()

    def _on_tick(self, val: float) -> None:
        self._t = max(0.0, min(1.0, float(val)))
        self.update()

    def _on_done(self) -> None:
        self._snapshot = None
        self.hide()

    def hideEvent(self, event) -> None:  # type: ignore[override]
        self._snapshot = None
        super().hideEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        if self._snapshot is None or self._snapshot.isNull():
            return
        alpha = max(0.0, 1.0 - self._t)
        if alpha <= 0.01:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setOpacity(alpha)
        dx = -self._direction * 6.0 * self._t
        painter.drawPixmap(QPointF(dx, 0.0), self._snapshot)
        painter.end()


def stagger_entrance(
    widgets: Sequence[QWidget],
    step_ms: int = 40,
    duration_ms: int = 220,
    owner: Optional[QObject] = None,
) -> None:
    """Staggered opacity reveal for SessionCompleteView summary rows."""
    if not widgets:
        return

    reduced = prefers_reduced_motion()
    if reduced:
        for widget in widgets:
            widget.setGraphicsEffect(None)
        return

    for idx, widget in enumerate(widgets):
        effect = QGraphicsOpacityEffect(widget)
        effect.setOpacity(0.0)
        widget.setGraphicsEffect(effect)

        anim = ValueAnimator(0.0, parent=widget)

        def _make_updater(eff: QGraphicsOpacityEffect) -> Callable[[float], None]:
            def _update(val: float) -> None:
                try:
                    eff.setOpacity(val)
                except RuntimeError:
                    pass
            return _update

        def _make_cleanup(w: QWidget) -> Callable[[], None]:
            def _cleanup() -> None:
                try:
                    w.setGraphicsEffect(None)
                except RuntimeError:
                    pass
            return _cleanup

        def _start(
            a: ValueAnimator = anim,
            e: QGraphicsOpacityEffect = effect,
            w: QWidget = widget,
        ) -> None:
            try:
                if not w.isVisible() and w.parentWidget() is None:
                    return
                a.animate_to(
                    target=1.0,
                    duration_ms=duration_ms,
                    curve=ease_out(),
                    start_from=0.0,
                    on_value=_make_updater(e),
                    on_done=_make_cleanup(w),
                    non_vestibular=True,
                )
            except RuntimeError:
                pass

        delay = idx * max(0, int(step_ms))
        if delay <= 0:
            _start()
        else:
            QTimer.singleShot(delay, _start)
