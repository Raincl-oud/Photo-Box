"""Headless Qt UI tests for PhotoBox v0.6.0 Multi-Surface & Gallery Edition."""

from __future__ import annotations

import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from PyQt6.QtCore import QEasingCurve, QEvent, QPoint, Qt
from PyQt6.QtGui import QColor, QFontDatabase, QKeyEvent, QPixmap
from PyQt6.QtMultimedia import QMediaPlayer
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import (
    QApplication,
    QGraphicsOpacityEffect,
    QLabel,
    QLineEdit,
    QPushButton,
    QStackedWidget,
    QWidget,
)

from core.session_manager import (
    APP_VERSION,
    SHORTCUT_KEYS,
    TRASH_FOLDER,
    DefaultCategorySpec,
    SessionManager,
    SessionState,
    get_detailed_media_metadata,
    get_fast_photo_metadata,
)
from main import _SHORTCUT_INDEX_MAP, MainWindow
from ui.filmstrip import FilmstripBar, _FilmstripSlotButton
from ui.flow_layout import CenteredFlowLayout
from ui.gallery_sidebar import SessionGallerySidebar
from ui.i18n import get_language, set_language
from ui.icons import ICON_NAMES, draw_brand_logo, render_brand_logo, render_icon, render_pixmap
from ui.motion import (
    ValueAnimator,
    WidgetSlideFadeTransition,
    ease_drawer,
    ease_in_out,
    ease_out,
    ease_overshoot,
    lerp_color,
    lerp_float,
)
from ui.photo_canvas import PhotoCanvas, file_cache_key, load_scaled_qimage
from ui.theme import (
    PALETTE,
    get_stylesheet,
    load_bundled_fonts,
    resolve_asset_path,
    set_active_theme,
)
from ui.views import (
    CategoryKeycapButton,
    FullMetadataOverlay,
    KeyCaptureDialog,
    KeyCaptureOverlay,
    SettingsDialog,
    SettingsOverlay,
    SplashScreen,
    TrashKeycapButton,
    _AutoExpandingCategoryBox,
    _DashedActionButton,
    _InWindowOverlayBase,
    _MetadataHoverLayer,
    _SessionRowWidget,
    _SettingGroupCard,
    _SettingRow,
    _SetupCategoryChip,
    _ToggleSwitch,
)


def _get_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    load_bundled_fonts(app)
    app.setStyleSheet(get_stylesheet())
    return app


class PhotoBoxUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.app = _get_app()

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state_file = self.root / "sessions.json"
        self.photos_dir = self.root / "Album"
        self.photos_dir.mkdir()

    def tearDown(self) -> None:
        set_language("en")
        set_active_theme("black", False, 68)
        self.app.setStyleSheet(get_stylesheet())
        self._tmp.cleanup()

    def test_shortcut_map_covers_all_20_keys(self) -> None:
        self.assertEqual(len(_SHORTCUT_INDEX_MAP), len(SHORTCUT_KEYS))
        self.assertEqual(len(_SHORTCUT_INDEX_MAP), 20)

    def test_centered_flow_layout_centers_wrapped_rows(self) -> None:
        host = QWidget()
        layout = CenteredFlowLayout(
            host, margin=10, h_spacing=10, v_spacing=8, centered=True
        )
        buttons = []
        for idx in range(4):
            btn = QPushButton(f"Button {idx}", host)
            btn.setFixedSize(100, 32)
            layout.addWidget(btn)
            buttons.append(btn)

        host.resize(260, 120)
        layout.activate()
        self.app.processEvents()

        self.assertEqual(buttons[0].x(), 25)
        self.assertEqual(buttons[1].x(), 135)
        self.assertEqual(buttons[2].x(), 25)
        self.assertGreater(buttons[2].y(), buttons[0].y())
        host.deleteLater()

    def test_vector_icons_and_motion_curves(self) -> None:
        for name in ICON_NAMES:
            pix_1x = render_pixmap(name, size=16, device_pixel_ratio=1.0)
            self.assertFalse(pix_1x.isNull(), f"Icon {name} returned null pixmap")
            self.assertEqual((pix_1x.width(), pix_1x.height()), (16, 16))

            pix_2x = render_pixmap(name, size=16, device_pixel_ratio=2.0)
            self.assertEqual((pix_2x.width(), pix_2x.height()), (32, 32))
            self.assertEqual(pix_2x.devicePixelRatio(), 2.0)

            icon = render_icon(name, size=16)
            self.assertFalse(icon.isNull())

        for curve in (ease_out(), ease_in_out(), ease_drawer()):
            self.assertEqual(curve.type(), QEasingCurve.Type.BezierSpline)
            self.assertAlmostEqual(curve.valueForProgress(0.0), 0.0, places=3)
            self.assertAlmostEqual(curve.valueForProgress(1.0), 1.0, places=3)
            self.assertGreater(curve.valueForProgress(0.5), 0.45)

        seen: list[float] = []
        done_calls: list[int] = []
        anim = ValueAnimator(0.0)
        anim.animate_to(1.0, duration_ms=0, on_value=seen.append)
        self.assertEqual(seen[-1], 1.0)

        with patch("ui.motion.prefers_reduced_motion", return_value=False):
            seen.clear()
            anim.animate_to(
                0.0,
                duration_ms=120,
                start_from=1.0,
                on_value=seen.append,
                on_done=lambda: done_calls.append(1),
            )
            before_stop = len(seen)
            anim.stop_silent(1.0)
            self.assertEqual(len(seen), before_stop)
            self.assertEqual(done_calls, [])
            self.assertEqual(anim.current_float, 1.0)

        with patch("ui.motion.prefers_reduced_motion", return_value=True):
            done_calls.clear()
            anim.animate_to(
                0.0,
                duration_ms=120,
                start_from=1.0,
                on_done=lambda: done_calls.append(1),
            )
            self.assertEqual(done_calls, [1])
            self.assertEqual(anim.current_float, 0.0)

    def test_exif_rotated_jpeg_scaled_decode_and_metadata(self) -> None:
        # 600x300 raw with EXIF orientation 6 (90 deg CW -> display 300x600)
        rot6 = self.photos_dir / "rot6.jpg"
        img6 = Image.new("RGB", (600, 300), (20, 90, 180))
        ex6 = img6.getexif()
        ex6[274] = 6
        img6.save(rot6, exif=ex6)

        meta6 = get_fast_photo_metadata(rot6)
        self.assertEqual((meta6["width"], meta6["height"]), (300, 600))

        qimg_canvas, orig_w, orig_h = load_scaled_qimage(rot6, max_dim=300)
        self.assertIsNotNone(qimg_canvas)
        self.assertEqual((orig_w, orig_h), (300, 600))
        self.assertEqual((qimg_canvas.width(), qimg_canvas.height()), (150, 300))

        qimg_thumb, tw, th = load_scaled_qimage(rot6, max_dim=(184, 120))
        self.assertIsNotNone(qimg_thumb)
        self.assertEqual((tw, th), (300, 600))
        self.assertEqual((qimg_thumb.width(), qimg_thumb.height()), (60, 120))

        # 400x200 raw with EXIF orientation 8 (270 deg CCW -> display 200x400)
        rot8 = self.photos_dir / "rot8.jpg"
        img8 = Image.new("RGB", (400, 200), (180, 60, 40))
        ex8 = img8.getexif()
        ex8[274] = 8
        img8.save(rot8, exif=ex8)

        meta8 = get_fast_photo_metadata(rot8)
        self.assertEqual((meta8["width"], meta8["height"]), (200, 400))
        qimg8, _, _ = load_scaled_qimage(rot8, max_dim=200)
        self.assertEqual((qimg8.width(), qimg8.height()), (100, 200))

    def test_photo_canvas_handles_corrupted_file_gracefully(self) -> None:
        bad_img = self.photos_dir / "broken.jpg"
        bad_img.write_bytes(b"not a valid jpeg")

        canvas = PhotoCanvas()
        canvas.resize(640, 480)
        canvas.set_photo(bad_img)
        self.app.processEvents()

        self.assertGreater(len(canvas.scene().items()), 0)
        canvas.shutdown()
        canvas.deleteLater()

    def test_splash_screen_branding_and_steps(self) -> None:
        splash = SplashScreen()
        for pct, msg in SplashScreen.BOOT_STEPS:
            splash.set_step(pct, msg)
        self.assertEqual(splash.progress.value(), 100)
        self.assertEqual(splash.status_lbl.text(), "Ready.")
        splash.close()
        splash.deleteLater()

    def test_fixed_timeline_header_filmstrip_badges_and_inplace_buttons(self) -> None:
        for i in range(1, 26):
            Image.new("RGB", (80, 60), color=(i * 9, 80, 140)).save(
                self.photos_dir / f"img_{i:02d}.jpg"
            )

        mgr = SessionManager(state_file=self.state_file)
        mgr.create_or_get_session(
            self.photos_dir, name="Trip", categories=["Bagus", "Keluarga"]
        )

        win = MainWindow(manager=mgr)
        win.show()
        self.app.processEvents()
        win.sorting_view.canvas.setFocus()
        self.app.processEvents()

        sv = win.sorting_view
        self.assertFalse(hasattr(sv, "progress_lbl"))
        self.assertEqual(sv.counter_pill.pill_btn.text(), "1 / 25")

        # Trash Keycap MUST be pinned far-left at index 0 of Console Deck flow!
        self.assertIs(sv.category_flow.itemAt(0).widget(), sv._trash_button)

        # Verify custom CategoryKeycapButton & TrashKeycapButton instances reused in-place
        bagus_btn = sv._category_buttons["Bagus"]
        keluarga_btn = sv._category_buttons["Keluarga"]
        trash_btn = sv._trash_button
        self.assertIsInstance(bagus_btn, CategoryKeycapButton)
        self.assertIsInstance(trash_btn, TrashKeycapButton)
        bagus_id = id(bagus_btn)
        trash_id = id(trash_btn)

        # Move photo #1 to Bagus and photo #2 to Keluarga
        win._on_move_to_category("Bagus")
        win._on_move_to_category("Keluarga")
        self.app.processEvents()

        self.assertIs(sv._category_buttons["Bagus"], bagus_btn)
        self.assertEqual(id(sv._category_buttons["Bagus"]), bagus_id)
        self.assertEqual(id(sv._trash_button), trash_id)
        self.assertEqual(bagus_btn.count, 1)
        self.assertEqual(keluarga_btn.count, 1)

        # Current photo is #3 (3 / 25); filmstrip keeps showing sorted photos #1 and #2!
        self.assertEqual(sv.counter_pill.pill_btn.text(), "3 / 25")
        slots = sv.filmstrip.slots
        self.assertEqual(slots[0].entry.status, "sorted")
        self.assertEqual(slots[0].entry.category, "Bagus")
        self.assertEqual(slots[1].entry.status, "sorted")
        self.assertEqual(slots[1].entry.category, "Keluarga")

        # Advance to index 19 -> displays 20 / 25
        for _ in range(17):
            win._on_skip_photo()
        self.app.processEvents()
        self.assertEqual(sv.counter_pill.pill_btn.text(), "20 / 25")

        # Pure navigation via jump_to(1): stays sorted in Keluarga + active LED!
        win._on_jump_to(1)
        self.app.processEvents()
        self.assertEqual(sv.counter_pill.pill_btn.text(), "2 / 25")
        self.assertTrue((self.photos_dir / "Keluarga" / "img_02.jpg").exists())
        self.assertEqual(keluarga_btn.count, 1)
        self.assertTrue(keluarga_btn._is_active)

        # Direct reassignment from Keluarga -> Bagus while viewing sorted img_02
        win._on_move_to_category("Bagus")
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "Bagus" / "img_02.jpg").exists())
        self.assertEqual(bagus_btn.count, 2)
        self.assertEqual(keluarga_btn.count, 0)

        # Ctrl+Z undo restores img_02 back from Bagus -> Keluarga
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "Keluarga" / "img_02.jpg").exists())
        self.assertEqual(bagus_btn.count, 1)
        self.assertEqual(keluarga_btn.count, 1)

        # Second & third Ctrl+Z restore img_02 and img_01 back to root
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier),
        )
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "img_02.jpg").exists())
        self.assertTrue((self.photos_dir / "img_01.jpg").exists())
        self.assertEqual(bagus_btn.count, 0)
        self.assertEqual(keluarga_btn.count, 0)

        # At index 0, Prev (Key_Left) and Ctrl+Z never wrap to index 24
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertEqual(sv.counter_pill.pill_btn.text(), "1 / 25")

        win.close()
        self.app.processEvents()

    def test_main_window_shortcuts_and_sidebar_toggle(self) -> None:
        for i in range(6):
            Image.new("RGB", (80, 60), color=(20 * i, 60, 120)).save(
                self.photos_dir / f"p{i}.jpg"
            )

        mgr = SessionManager(state_file=self.state_file)
        mgr.create_or_get_session(
            self.photos_dir, name="Trip", categories=["Bagus", "Keluarga"]
        )

        win = MainWindow(manager=mgr)
        win.show()
        self.app.processEvents()
        win.sorting_view.canvas.setFocus()
        self.app.processEvents()

        sid = mgr.sessions[0].id
        self.assertEqual(mgr.get_current_photo(sid).name, "p0.jpg")

        # Space skips forward cleanly on photo -> p1.jpg
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        )
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(sid).name, "p1.jpg")

        # Left returns to p0.jpg (pure navigation)
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(sid).name, "p0.jpg")

        # Key '1' moves p0.jpg to "Bagus"
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1"),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "Bagus" / "p0.jpg").exists())
        self.assertEqual(mgr.get_current_photo(sid).name, "p1.jpg")

        # Left returns to p0.jpg inside Bagus (stays sorted in Bagus!)
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "Bagus" / "p0.jpg").exists())
        self.assertEqual(mgr.get_current_photo(sid), self.photos_dir / "Bagus" / "p0.jpg")

        # Right moves focus back to p1.jpg cleanly
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(sid).name, "p1.jpg")

        # Key 'Delete' moves p1.jpg to _Buang
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Delete, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / TRASH_FOLDER / "p1.jpg").exists())

        # Ctrl+Z undoes _Buang move (restores p1.jpg back to root)
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "p1.jpg").exists())
        self.assertEqual(mgr.get_current_photo(sid).name, "p1.jpg")

        # Tab toggles sidebar collapse with 180ms drawer animation
        self.assertTrue(win.sidebar.isVisible())
        QApplication.sendEvent(
            win.sorting_view.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Tab, Qt.KeyboardModifier.NoModifier),
        )
        QTest.qWait(240)
        self.app.processEvents()
        self.assertFalse(win.sidebar.isVisible())
        self.assertTrue(win.sorting_view.sidebar_expand_btn.isVisible())

        # Shortcuts blocked when QLineEdit has focus
        edit = QLineEdit(win.sorting_view)
        edit.show()
        edit.setFocus()
        self.app.processEvents()
        QApplication.sendEvent(
            edit,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1"),
        )
        self.app.processEvents()
        self.assertEqual(len(mgr.get_queue(sid)), 5)

        win.close()
        self.app.processEvents()

    def test_move_and_update_speed_benchmark(self) -> None:
        for i in range(15):
            Image.new("RGB", (1280, 960), color=(i * 15, 90, 150)).save(
                self.photos_dir / f"bench_{i:02d}.jpg", quality=85
            )

        mgr = SessionManager(state_file=self.state_file)
        mgr.create_or_get_session(
            self.photos_dir, name="Bench", categories=["Keep", "Best"]
        )
        win = MainWindow(manager=mgr)
        win.show()
        QTest.qWait(250)  # allow preloader to warm next neighbors
        self.app.processEvents()

        # Verify QThreadPool preloader populated exact 64-bit file_cache_key entries
        self.assertIsNotNone(
            win.sorting_view.canvas._get_cached_entry(self.photos_dir / "bench_01.jpg")
        )

        timings_ms = []
        for idx in range(8):
            t0 = time.perf_counter()
            win._on_move_to_category("Keep" if idx % 2 == 0 else "Best")
            self.app.processEvents()
            timings_ms.append((time.perf_counter() - t0) * 1000.0)

        avg_ms = sum(timings_ms) / len(timings_ms)
        self.assertLess(avg_ms, 50.0, f"Move+update average {avg_ms:.2f}ms >= 50ms")

        win.close()
        self.app.processEvents()

    def test_poppins_font_bundled_and_loaded(self) -> None:
        for fname in (
            "Poppins-Regular.ttf",
            "Poppins-Medium.ttf",
            "Poppins-SemiBold.ttf",
            "Poppins-Bold.ttf",
        ):
            self.assertTrue(
                resolve_asset_path(f"assets/fonts/{fname}").is_file(),
                f"Missing bundled font {fname}",
            )
        family = load_bundled_fonts(self.app)
        self.assertEqual(family, "Poppins")
        self.assertEqual(self.app.font().family(), "Poppins")
        self.assertIn("Poppins", QFontDatabase.families())

    def test_video_playback_controls_shortcuts_and_move_release(self) -> None:
        vid_a = self.photos_dir / "a_clip.mp4"
        vid_b = self.photos_dir / "b_clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=green:s=160x120:d=5",
                "-pix_fmt", "yuv420p", str(vid_a),
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=blue:s=160x120:d=5",
                "-pix_fmt", "yuv420p", str(vid_b),
            ],
            check=True,
            capture_output=True,
        )
        Image.new("RGB", (80, 60), color=(40, 90, 160)).save(
            self.photos_dir / "c_photo.jpg"
        )

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, name="VideoTrip", categories=["Videos", "Keep"]
        )
        mgr.set_category_keybind(s.id, "Videos", "V")

        win = MainWindow(manager=mgr)
        win.show()
        QTest.qWait(250)
        self.app.processEvents()

        sv = win.sorting_view
        sv.canvas.setFocus()
        self.app.processEvents()

        # Non-looping single pass playback!
        self.assertEqual(sv.canvas._media_player.loops(), QMediaPlayer.Loops.Once)
        self.assertTrue(sv.canvas.is_video_active())
        self.assertTrue(sv.canvas._video_control_bar.isVisible())
        self.assertTrue(sv.filmstrip.slots[0].is_video)

        # Wait briefly for ffmpeg filmstrip thumbnail worker
        for _ in range(20):
            self.app.processEvents()
            if sv.filmstrip.slots[0].pixmap is not None:
                break
            QTest.qWait(40)
        self.assertIsNotNone(sv.filmstrip.slots[0].pixmap)

        # Toggle autoplay button inside control bar
        sv.canvas._video_control_bar.autoplay_btn.click()
        self.app.processEvents()
        self.assertFalse(mgr.settings.video_autoplay)
        sv.canvas._video_control_bar.autoplay_btn.click()
        self.app.processEvents()
        self.assertTrue(mgr.settings.video_autoplay)

        # Space alone on video toggles Play/Pause (without skipping!)
        was_playing = sv.canvas.is_playing_video()
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        )
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(s.id).name, "a_clip.mp4")
        self.assertNotEqual(sv.canvas.is_playing_video(), was_playing)

        # Right arrow seeks forward & Left seeks backward
        pos_before = sv.canvas._media_player.position()
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertGreaterEqual(sv.canvas._media_player.position(), pos_before + 2500)

        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertLess(sv.canvas._media_player.position(), 1500)

        # Click-anywhere along seek slider groove jumps directly to clicked ratio
        slider = sv.canvas._video_control_bar.seek_slider
        QTest.mouseClick(
            slider,
            Qt.MouseButton.LeftButton,
            Qt.KeyboardModifier.NoModifier,
            slider.rect().center(),
        )
        self.app.processEvents()
        self.assertGreaterEqual(sv.canvas._media_player.position(), 1800)

        # Continuous Space + Right -> Right navigates consecutive videos without releasing Space!
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        )
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(s.id).name, "b_clip.mp4")
        self.assertTrue(sv.canvas.is_video_active())

        # Press Right a SECOND time while Space is STILL held!
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(s.id).name, "c_photo.jpg")
        self.assertFalse(sv.canvas.is_video_active())

        # Back to b_clip.mp4 with Space still held + Left, then release Space
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.NoModifier),
        )
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyRelease, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(s.id).name, "b_clip.mp4")

        # When finished playing (is_video_finished == True), standalone Right Arrow skips to next item!
        orig_finished = sv.canvas.is_video_finished
        sv.canvas.is_video_finished = lambda: True  # type: ignore[method-assign]
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.NoModifier),
        )
        sv.canvas.is_video_finished = orig_finished  # type: ignore[method-assign]
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(s.id).name, "c_photo.jpg")

        # Jump back to a_clip.mp4 and move playing video using custom keybind 'V' -> release_media frees lock cleanly
        win._on_jump_to(0)
        self.app.processEvents()
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_V, Qt.KeyboardModifier.NoModifier, "v"),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "Videos" / "a_clip.mp4").exists())
        self.assertEqual(mgr.get_current_photo(s.id).name, "b_clip.mp4")

        win.close()
        self.app.processEvents()

    def test_setup_view_dynamic_boxes_deep_search_and_keybind_conflicts(self) -> None:
        (self.photos_dir / "SubA").mkdir()
        Image.new("RGB", (40, 40), color=(20, 40, 60)).save(
            self.photos_dir / "SubA" / "nested.jpg"
        )

        mgr = SessionManager(state_file=self.state_file)
        win = MainWindow(manager=mgr)
        win.show()
        self.app.processEvents()

        setup = win.setup_view
        # Trash chip pinned at FAR LEFT (index 0) + auto-populated default categories
        self.assertGreaterEqual(len(setup._categories), 1)
        self.assertTrue(setup.trash_chip.isVisible())
        self.assertIs(setup.chips_layout.itemAt(0).widget(), setup.trash_chip)
        self.assertNotIn(TRASH_FOLDER, setup._categories)

        # Auto-expanding inline category box grows dynamically with text
        initial_w = setup.inline_box.name_input.width()
        self.assertGreaterEqual(initial_w, 116)
        setup.inline_box.name_input.setText("Prewedding Outdoor Sunset Extended Label")
        self.app.processEvents()
        self.assertGreater(setup.inline_box.name_input.width(), initial_w + 40)

        # Commit inline box -> resets back to initial width and adds chip
        setup.inline_box.add_btn.click()
        self.app.processEvents()
        self.assertIn("Prewedding Outdoor Sunset Extended Label", setup._categories)
        self.assertEqual(setup.inline_box.name_input.width(), initial_w)

        # KeyCaptureOverlay renders in-window & rejects reserved keys, Ctrl+A control chars, and conflicts
        cap_ov = KeyCaptureOverlay(
            target_label="Favorit",
            existing_map={"Favorit": "1", "Simpan": "2"},
            ignore_category="Favorit",
            parent=win.container,
        )
        win._open_overlay(cap_ov)
        self.app.processEvents()
        # Ctrl+A must be ignored without overwriting captured key with '\x01'
        QApplication.sendEvent(
            cap_ov,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_A, Qt.KeyboardModifier.ControlModifier, "\x01"),
        )
        self.assertIsNone(cap_ov.captured_key())
        # Ctrl+Z maps to CTRL+Z and is rejected as reserved system key
        QApplication.sendEvent(
            cap_ov,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier, "\x1a"),
        )
        self.assertTrue(cap_ov.error_lbl.isVisible())
        self.assertIn("CTRL+Z", cap_ov.error_lbl.text())

        self.assertFalse(cap_ov.accept_token("SPACE"))
        self.assertTrue(cap_ov.error_lbl.isVisible())
        self.assertFalse(cap_ov.accept_token("2"))
        self.assertIn("Simpan", cap_ov.error_lbl.text())
        self.assertTrue(cap_ov.accept_token("F"))
        self.assertEqual(cap_ov.captured_key(), "F")

        # Conditional Deep Search shown when subfolders exist, hides load_subfolders_btn
        # Also respects custom Trash folder name & rejects custom Trash key shortcut conflicts!
        mgr.update_settings(
            trash_category=DefaultCategorySpec("_Recycled", "X", "#EF4444")
        )
        setup.apply_settings(mgr.settings)
        (self.photos_dir / "_Recycled").mkdir()
        self.assertIn("_Recycled", setup._setup_existing_key_map())
        self.assertEqual(setup._setup_existing_key_map()["_Recycled"], "X")

        # Verify clean Setup chips have integrated accent color without separate square color_btn
        from ui.views import _SetupCategoryChip
        chips = setup.findChildren(_SetupCategoryChip)
        self.assertGreaterEqual(len(chips), 1)
        self.assertFalse(any(hasattr(c, "color_btn") for c in chips))

        setup.folder_edit.setText(str(self.photos_dir))
        self.app.processEvents()
        self.assertTrue(setup.deep_search_cb.isVisible())
        self.assertTrue(setup.load_subfolders_btn.isVisible())
        setup.deep_search_cb.setChecked(True)
        self.app.processEvents()
        self.assertFalse(setup.load_subfolders_btn.isVisible())
        setup.start_btn.click()
        self.app.processEvents()

        self.assertEqual(mgr.sessions[0].deep_search, True)
        self.assertEqual(mgr.get_current_photo(mgr.sessions[0].id).name, "nested.jpg")

        win.close()
        self.app.processEvents()

    def test_sorting_header_metadata_capsule_settings_overlay_and_i18n(self) -> None:
        for i in range(1, 6):
            im = Image.new("RGB", (120 + i, 90), color=(i * 40, 80, 120))
            exif = im.getexif()
            exif[0x0110] = "Sony A7IV"
            exif[0x8827] = 200
            im.save(self.photos_dir / f"pic_{i}&test.jpg", exif=exif)

        bg_img = self.root / "bg.png"
        Image.new("RGB", (120, 80), color=(30, 50, 90)).save(bg_img)

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, name="Studio & Co", categories=["Q&A", "Portrait", "Landscape"]
        )
        win = MainWindow(manager=mgr)
        win.show()
        self.app.processEvents()

        sv = win.sorting_view
        self.assertEqual(sv.sort_by_lbl.text(), "Sort by :")
        self.assertTrue(sv.settings_btn.isVisible())
        self.assertEqual(sv.counter_pill.pill_btn.text(), "1 / 5")
        self.assertNotIn("dashed", get_stylesheet().lower())

        # Header shows photo properties (dimensions · size) right before Metadata Capsule!
        self.assertTrue(sv.props_lbl.isVisible())
        self.assertIn("121 × 90", sv.props_lbl.text())
        self.assertIn("Sony A7IV", sv.meta_capsule.text())
        sv.meta_capsule.hover_entered.emit()
        self.app.processEvents()
        self.assertTrue(sv.meta_hover_card.isVisible())
        sv.meta_capsule.hover_left.emit()
        self.app.processEvents()
        self.assertFalse(sv.meta_hover_card.isVisible())

        sv.meta_capsule.click()
        self.app.processEvents()
        meta_ov = win.active_overlay()
        self.assertIsInstance(meta_ov, FullMetadataOverlay)
        # Shortcuts blocked while overlay is visible!
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " "),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_current_photo(s.id).name, "pic_1&test.jpg")
        meta_ov.close_overlay()
        self.app.processEvents()

        # Clickable CounterPill jump to #4
        sv.counter_pill._enter_edit_mode()
        self.assertTrue(sv.counter_pill.jump_input.isVisible())
        sv.counter_pill.jump_input.setText("4")
        QApplication.sendEvent(
            sv.counter_pill.jump_input,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Return, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertEqual(sv.counter_pill.pill_btn.text(), "4 / 5")
        self.assertEqual(mgr.get_current_photo(s.id).name, "pic_4&test.jpg")

        # Customize keybind & color -> Keycap updates in-place & shortcut moves file
        win._apply_category_keybind(s.id, "Portrait", "P")
        win._apply_category_color(s.id, "Portrait", "#EC4899")
        self.app.processEvents()
        self.assertEqual(sv._category_buttons["Portrait"].key_label, "P")
        self.assertEqual(sv._category_buttons["Portrait"].accent_color, "#EC4899")

        sv.canvas.setFocus()
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_P, Qt.KeyboardModifier.NoModifier, "p"),
        )
        self.app.processEvents()
        self.assertTrue((self.photos_dir / "Portrait" / "pic_4&test.jpg").exists())

        # In-window 7-menu SettingsOverlay: Theme, Conditional BG, Named Keybind Profiles, Bilingual i18n
        ov = win.open_settings()
        self.app.processEvents()
        self.assertIsInstance(ov, SettingsOverlay)
        self.assertEqual(ov.pages.count(), 7)

        # Page 5 (Theme & BG): Custom BG path/dim/reset are hidden until uploaded!
        ov.select_page(5)
        self.assertFalse(ov.bg_path_lbl.isVisible())
        self.assertFalse(ov.dim_host.isVisible())
        self.assertFalse(ov.reset_bg_btn.isVisible())

        ov.theme_white_btn.click()
        ov.set_custom_bg_path(str(bg_img))
        ov.dim_slider.setValue(45)
        self.assertTrue(ov.bg_path_lbl.isVisible())
        self.assertTrue(ov.dim_host.isVisible())
        self.assertTrue(ov.reset_bg_btn.isVisible())

        # Page 6 (About): GitHub link uses active theme accent color dynamically
        ov.select_page(6)
        self.assertIn(PALETTE["accent"], ov.about_link_lbl.text())

        # Page 2 (Keybind Profiles): hidden under Default (1-9), expands cleanly on clicking Custom!
        ov.select_page(2)
        self.assertFalse(ov.editor_card.isVisible())
        ov.kb_custom_btn.click()
        self.app.processEvents()
        self.assertTrue(ov.editor_card.isVisible())
        ov._on_slot_captured(0, "Q")
        ov.profile_name_edit.setText("StudioPro")
        ov.save_profile_btn.click()
        self.app.processEvents()

        # Page 0 (Language): switch EN -> ID and save settings
        ov.select_page(0)
        ov.lang_id_btn.click()
        self.app.processEvents()
        self.assertEqual(get_language(), "id")

        ov._on_save()
        self.app.processEvents()

        self.assertEqual(PALETTE["mode"], "white")
        self.assertTrue(win.container.has_active_background)
        self.assertEqual(mgr.settings.custom_bg_dim, 45)
        self.assertEqual(mgr.settings.language, "id")
        self.assertEqual(mgr.settings.keybind_profile, "StudioPro")
        self.assertEqual(sv.sort_by_lbl.text(), "Urutkan :")

        win.close()
        self.app.processEvents()

    def test_motion_v050_curves_transitions_and_choreography(self) -> None:
        for i in range(3):
            Image.new("RGB", (120, 90), color=(40 + i * 50, 80, 140)).save(
                self.photos_dir / f"pic_{i}.jpg"
            )

        # 1. Curves & interpolation helpers
        c_over = ease_overshoot()
        self.assertIsInstance(c_over, QEasingCurve)
        self.assertEqual(c_over.valueForProgress(0.0), 0.0)
        self.assertGreater(c_over.valueForProgress(0.6), 1.0)
        self.assertAlmostEqual(c_over.valueForProgress(1.0), 1.0, places=5)

        self.assertAlmostEqual(lerp_float(10.0, 20.0, 0.5), 15.0)
        mid_c = lerp_color(QColor("#000000"), QColor("#FFFFFF"), 0.5)
        self.assertGreaterEqual(mid_c.red(), 127)
        self.assertLessEqual(mid_c.red(), 128)

        # 2. WidgetSlideFadeTransition across stacked pages & MainWindow view swap
        with patch("ui.motion.prefers_reduced_motion", return_value=False):
            stack = QStackedWidget()
            stack.resize(320, 200)
            p0 = QWidget()
            p1 = QWidget()
            stack.addWidget(p0)
            stack.addWidget(p1)
            stack.show()
            trans = WidgetSlideFadeTransition(stack, duration_ms=110)
            trans.transition_to(lambda: stack.setCurrentWidget(p1), p0, p1, direction=1)
            self.assertEqual(stack.currentWidget(), p1)
            self.assertTrue(trans.isVisible())
            self.assertTrue(trans.testAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents))
            QTest.qWait(150)
            self.app.processEvents()
            self.assertFalse(trans.isVisible())
            stack.close()

        # 3. CategoryKeycapButton & TrashKeycapButton animators + count bump
        with (
            patch("ui.motion.prefers_reduced_motion", return_value=False),
            patch("ui.views.prefers_reduced_motion", return_value=False),
        ):
            btn = CategoryKeycapButton("Portrait", "1", count=0)
            btn.show()
            btn.set_active(True)
            btn.set_count(3)
            QTest.qWait(30)
            self.app.processEvents()
            self.assertGreater(btn._active_t, 0.0)
            btn.trigger_pulse(duration_ms=90)
            QTest.qWait(190)
            self.app.processEvents()
            self.assertAlmostEqual(btn._active_t, 1.0, delta=0.05)
            btn.repaint()
            btn.close()

            tbtn = TrashKeycapButton("_Buang", "DEL", count=0)
            tbtn.show()
            tbtn.set_active(True)
            tbtn.set_count(2)
            QTest.qWait(190)
            self.app.processEvents()
            self.assertAlmostEqual(tbtn._active_t, 1.0, delta=0.05)
            tbtn.repaint()
            tbtn.close()

            d_btn = _DashedActionButton()
            d_btn.show()
            QApplication.sendEvent(d_btn, QEvent(QEvent.Type.Enter))
            QTest.qWait(145)
            self.app.processEvents()
            self.assertGreater(d_btn._hover_t, 0.5)
            d_btn.repaint()
            d_btn.close()

        # 4. Filmstrip slot animators & reel shift
        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, name="MotionVerify", categories=["Portrait"]
        )
        with (
            patch("ui.motion.prefers_reduced_motion", return_value=False),
            patch("ui.filmstrip.prefers_reduced_motion", return_value=False),
        ):
            strip = FilmstripBar()
            strip.resize(640, 96)
            strip.show()
            strip.update_items(
                s.timeline,
                focus_index=0,
                root_folder=str(self.photos_dir),
                category_colors={"Portrait": "#38BDF8"},
            )
            strip.update_items(
                s.timeline,
                focus_index=1,
                root_folder=str(self.photos_dir),
                category_colors={"Portrait": "#38BDF8"},
            )
            slot = strip.slots[1]
            slot.pixmap = None
            pm = QPixmap(60, 40)
            pm.fill(QColor("#224466"))
            slot.set_loaded_pixmap(pm)
            QTest.qWait(190)
            self.app.processEvents()
            self.assertGreater(slot._thumb_alpha, 0.9)
            self.assertGreater(slot._active_t, 0.8)
            slot.repaint()
            strip.close()

        # 5. PhotoCanvas directional slide + HUD pop + VideoControlBar entrance + no QGraphicsOpacityEffect on PhotoCanvas
        with (
            patch("ui.motion.prefers_reduced_motion", return_value=False),
            patch("ui.photo_canvas.prefers_reduced_motion", return_value=False),
        ):
            canvas = PhotoCanvas()
            canvas.resize(640, 420)
            canvas.show()
            self.assertIsNone(canvas.graphicsEffect())
            canvas.set_photo(self.photos_dir / "pic_0.jpg", direction=1)
            self.assertIsNone(canvas.graphicsEffect())
            canvas.set_photo(self.photos_dir / "pic_1.jpg", direction=-1)
            canvas.show_action_hud("Skipped", icon_name="skip", tone="accent")
            self.assertTrue(canvas._hud.isVisible())
            canvas._video_control_bar.show_animated(120, 320)
            self.assertTrue(canvas._video_control_bar.isVisible())
            QTest.qWait(210)
            self.app.processEvents()
            self.assertIsNone(canvas._video_control_bar.graphicsEffect())
            canvas.release_media()
            canvas.close()

        # 6. Metadata hover card, in-window overlay scrim, SettingsOverlay nav pill & page transitions, SessionRow, InlineBox
        with (
            patch("ui.motion.prefers_reduced_motion", return_value=False),
            patch("ui.views.prefers_reduced_motion", return_value=False),
        ):
            win = MainWindow(manager=mgr)
            win.resize(1160, 760)
            win.show()
            self.app.processEvents()

            self.assertIsNotNone(win._stack_transition)
            self.assertIsNone(win.sorting_view.canvas.graphicsEffect())

            # Session row animators + custom paint
            rows = win.sidebar.findChildren(_SessionRowWidget)
            self.assertGreater(len(rows), 0)
            rows[0].repaint()

            # Metadata hover animated reveal + synchronous hide invariant
            sv = win.sorting_view
            sv._show_metadata_hover()
            self.assertTrue(sv.meta_hover_card.isVisible())
            sv._hide_metadata_hover()
            self.assertFalse(sv.meta_hover_card.isVisible())

            # Inline auto-expanding box focus animator + chip entrance on setup view
            win._on_add_session_clicked()
            self.app.processEvents()
            setup_v = win.setup_view
            box = setup_v.inline_box
            self.assertIsInstance(box, _AutoExpandingCategoryBox)
            box.name_input.setText("LongCategoryLabelForWidthCheck")
            self.app.processEvents()
            self.assertGreater(box.name_input.width(), 140)
            box.add_btn.click()
            self.app.processEvents()
            self.assertIn("LongCategoryLabelForWidthCheck", setup_v._categories)

            # SettingsOverlay: never attach QGraphicsOpacityEffect to card, smooth nav pill + page transition
            ov = win.open_settings()
            self.app.processEvents()
            self.assertIsNone(ov.card.graphicsEffect())
            self.assertGreater(ov._open_t, -0.01)
            ov.select_page(3)
            QTest.qWait(210)
            self.app.processEvents()
            self.assertGreater(ov._open_t, 0.85)
            self.assertEqual(ov.pages.currentIndex(), 3)
            self.assertEqual(ov.nav_panel._pill_y, float(ov._nav_buttons[3].y()))
            ov.close_overlay()
            self.assertFalse(ov.isVisible())

            win.close()
            self.app.processEvents()

    def test_v060_logo_refined_settings_gallery_bulk_move_and_shift_arrow(self) -> None:
        # 1. Brand mark assets & icon presence
        self.assertIn("gallery", ICON_NAMES)
        ico_path = resolve_asset_path("assets/icons/photobox.ico")
        png_path = resolve_asset_path("assets/icons/photobox.png")
        self.assertTrue(ico_path.exists())
        self.assertTrue(png_path.exists())
        with Image.open(ico_path) as ico_im:
            self.assertEqual(
                sorted(ico_im.ico.sizes()),
                [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)],
            )
        logo_pm = render_brand_logo(24)
        self.assertFalse(logo_pm.isNull())

        splash = SplashScreen()
        self.assertEqual(len(splash.BOOT_STEPS), 4)
        splash.close()

        # Media setup (5 photos + 1 ffmpeg clip)
        for i in range(5):
            Image.new("RGB", (140 + i * 20, 100), color=(40 + i * 35, 90, 150)).save(
                self.photos_dir / f"shot_{i}.jpg"
            )
        vid_path = self.photos_dir / "z_clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=160x120:d=2",
                "-c:v", "libx264", "-pix_fmt", "yuv420p", str(vid_path),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True,
        )

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, name="GallerySuite", categories=["Selects", "Print"]
        )
        win = MainWindow(manager=mgr)
        win.resize(1280, 820)
        win.show()
        self.app.processEvents()

        # Window icon & sidebar logo
        self.assertFalse(win.windowIcon().isNull())
        self.assertIsNotNone(win.sidebar.brand_logo_lbl.pixmap())
        self.assertFalse(win.sidebar.brand_logo_lbl.pixmap().isNull())

        sv = win.sorting_view
        gs = sv.gallery_sidebar
        self.assertIsInstance(gs, SessionGallerySidebar)
        self.assertTrue(sv.is_gallery_open())
        self.assertEqual(gs.width(), 276)
        self.assertGreaterEqual(gs.column_count(), 2)

        # Sort combo shows cleaned A-Z label + size modes
        combo_items = [sv.sort_combo.itemText(i) for i in range(sv.sort_combo.count())]
        self.assertIn("Filename (A-Z)", combo_items)
        self.assertNotIn("Natural", combo_items[0])
        self.assertIn("File Size (Largest)", combo_items)
        self.assertIn("File Size (Smallest)", combo_items)

        # Resize drag widens gallery -> live column count increases cleanly
        gs._on_resize_delta(-190)  # widen from 276 -> 466
        self.app.processEvents()
        self.assertGreater(gs.width(), 400)
        self.assertGreaterEqual(gs.column_count(), 3)
        gs._on_resize_finished()
        self.app.processEvents()
        self.assertEqual(mgr.settings.gallery_width, gs.width())

        # Refined SettingsOverlay checks (close_btn, _SettingGroupCard, _SettingRow, _ToggleSwitch, About logo)
        ov = win.open_settings()
        self.app.processEvents()
        self.assertTrue(ov.close_btn.isVisible())
        self.assertGreaterEqual(len(ov.findChildren(_SettingGroupCard)), 3)
        self.assertGreaterEqual(len(ov.findChildren(_SettingRow)), 3)
        ov.select_page(3)
        self.app.processEvents()
        self.assertIsInstance(ov.autoplay_cb, _ToggleSwitch)
        was_checked = ov.autoplay_cb.isChecked()
        ov.autoplay_cb.click()
        self.app.processEvents()
        self.assertEqual(ov.autoplay_cb.isChecked(), not was_checked)
        ov.select_page(6)
        self.app.processEvents()
        self.assertIsNotNone(ov.about_logo_lbl.pixmap())
        self.assertFalse(ov.about_logo_lbl.pixmap().isNull())
        ov.close_overlay()
        self.app.processEvents()

        # Shift+Click range selection -> bulk bar visible -> Esc clears selection
        gs._on_canvas_item_activated(0, Qt.KeyboardModifier.NoModifier)
        gs._on_canvas_item_activated(2, Qt.KeyboardModifier.ShiftModifier)
        self.app.processEvents()
        self.assertEqual(gs.selected_indices_sorted(), [0, 1, 2])
        self.assertTrue(gs.has_multi_selection())
        self.assertTrue(gs.bulk_bar.isVisible())

        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Escape, Qt.KeyboardModifier.NoModifier),
        )
        self.app.processEvents()
        self.assertFalse(gs.has_multi_selection())
        self.assertFalse(gs.bulk_bar.isVisible())

        # Shift+Right on gallery open extends visible range, then Key_1 bulk moves all selected!
        win._on_jump_to(0)
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier),
        )
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier),
        )
        self.app.processEvents()
        self.assertEqual(gs.selected_indices_sorted(), [0, 1, 2])

        # Also test Shift+Left precedence over video seek on video frame (index 5 = z_clip.mp4)
        win._on_jump_to(5)
        self.app.processEvents()
        self.assertTrue(sv.canvas.is_video_active())
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Left, Qt.KeyboardModifier.ShiftModifier),
        )
        self.app.processEvents()
        self.assertEqual(gs.selected_indices_sorted(), [4, 5])
        sv.canvas.release_media()

        # Clear selection, re-select [0, 1, 2], press Key_1 to bulk-move
        gs._on_canvas_item_activated(0, Qt.KeyboardModifier.NoModifier)
        gs._on_canvas_item_activated(2, Qt.KeyboardModifier.ShiftModifier)
        self.app.processEvents()
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_1, Qt.KeyboardModifier.NoModifier, "1"),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_category_counts(s.id)["Selects"], 3)
        for idx in range(3):
            self.assertTrue((self.photos_dir / "Selects" / f"shot_{idx}.jpg").exists())
        self.assertFalse(gs.has_multi_selection())

        # Filter tabs: Sorted vs Unsorted count consistency
        gs.set_filter("sorted")
        self.assertEqual(gs.visible_indices(), [0, 1, 2])
        gs.set_filter("unsorted")
        self.assertEqual(gs.visible_indices(), [3, 4, 5])
        gs.set_filter("all")

        # Ctrl+Z executes atomic Batch Undo across all 3 items
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier),
        )
        self.app.processEvents()
        self.assertEqual(mgr.get_category_counts(s.id)["Selects"], 0)
        for idx in range(3):
            self.assertTrue((self.photos_dir / f"shot_{idx}.jpg").exists())

        # Ctrl+G collapses gallery (updates settings) & Shift+Right falls back to plain navigation
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_G, Qt.KeyboardModifier.ControlModifier),
        )
        self.app.processEvents()
        self.assertFalse(sv.is_gallery_open())
        self.assertFalse(mgr.settings.gallery_open)
        cur_before = s.current_index
        QApplication.sendEvent(
            sv.canvas,
            QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Right, Qt.KeyboardModifier.ShiftModifier),
        )
        self.app.processEvents()
        self.assertEqual(s.current_index, cur_before + 1)

        win.close()
        self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
