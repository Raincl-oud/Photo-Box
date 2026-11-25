"""Comprehensive unit tests for PhotoBox v0.2.0 SessionManager, fixed timeline, and step_back."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from PIL import Image

from core.session_manager import (
    APP_NAME,
    APP_VERSION,
    RESERVED_SYSTEM_KEYS,
    SHORTCUT_KEYS,
    SORT_MODES,
    SUPPORTED_EXTS,
    SUPPORTED_IMAGE_EXTS,
    SUPPORTED_VIDEO_EXTS,
    TIMELINE_STATUSES,
    TRASH_FOLDER,
    AppSettings,
    DefaultCategorySpec,
    SessionManager,
    detect_existing_categories,
    get_fast_media_metadata,
    get_fast_photo_metadata,
    get_photo_metadata,
    has_subfolders,
    is_video_file,
    next_available_keybind,
    sanitize_category_name,
    validate_keybind,
)


def _create_image(
    path: Path,
    size=(60, 40),
    color=(40, 100, 180),
    exif_orientation: int | None = None,
) -> Path:
    img = Image.new("RGB", size, color=color)
    if exif_orientation is not None:
        exif = img.getexif()
        exif[274] = exif_orientation
        img.save(path, exif=exif)
    else:
        img.save(path)
    return path


class SessionManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.state_file = self.root / "state" / "sessions.json"
        self.photos_dir = self.root / "Photos_Session"
        self.photos_dir.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_constants(self) -> None:
        self.assertEqual(APP_NAME, "PhotoBox")
        self.assertEqual(APP_VERSION, "0.6.0")
        self.assertEqual(TRASH_FOLDER, "_Buang")
        self.assertEqual(TIMELINE_STATUSES, ("pending", "skipped", "sorted"))
        self.assertEqual(
            SHORTCUT_KEYS,
            [
                "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
                "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
            ],
        )
        self.assertEqual(
            SORT_MODES,
            ("name_asc", "name_desc", "date_asc", "date_desc", "size_desc", "size_asc"),
        )
        self.assertIn(".jpg", SUPPORTED_IMAGE_EXTS)
        self.assertIn(".mp4", SUPPORTED_VIDEO_EXTS)
        self.assertTrue(SUPPORTED_IMAGE_EXTS.issubset(SUPPORTED_EXTS))
        self.assertTrue(SUPPORTED_VIDEO_EXTS.issubset(SUPPORTED_EXTS))
        self.assertTrue(is_video_file("clip.MP4"))
        self.assertFalse(is_video_file("photo.jpg"))
        for rk in ("TAB", "SPACE", "LEFT", "RIGHT", "BACKSPACE", "DEL", "CTRL+Z"):
            self.assertIn(rk, RESERVED_SYSTEM_KEYS)

    def test_sanitize_category_name(self) -> None:
        self.assertEqual(
            sanitize_category_name('  Liburan: Bali/2026?*  '), "Liburan Bali2026"
        )
        self.assertEqual(sanitize_category_name('A<B>C|"D\\'), "ABCD")
        for bad in [
            "", "   ", "...", ".", "..", "_Buang", "_buang", "_BUANG", ".hidden",
            "CON", "con", "PRN", "AUX", "nul", "COM1", "com9", "LPT1", "lpt9",
            "con.txt", "NUL.jpg",
        ]:
            with self.assertRaises(ValueError):
                sanitize_category_name(bad)
        self.assertEqual(sanitize_category_name("Console"), "Console")

    def test_detect_existing_categories_filters_trash_and_hidden(self) -> None:
        (self.photos_dir / "Keluarga").mkdir()
        (self.photos_dir / "Produk 10").mkdir()
        (self.photos_dir / "Produk 2").mkdir()
        (self.photos_dir / "_Buang").mkdir()
        (self.photos_dir / ".git").mkdir()
        _create_image(self.photos_dir / "a.jpg")

        detected = detect_existing_categories(self.photos_dir)
        self.assertEqual(detected, ["Keluarga", "Produk 2", "Produk 10"])

    def test_create_or_get_session_anti_duplicate_and_name(self) -> None:
        _create_image(self.photos_dir / "p1.jpg")
        mgr = SessionManager(state_file=self.state_file)

        s1, is_existing = mgr.create_or_get_session(
            self.photos_dir, categories=["Keluarga"]
        )
        self.assertFalse(is_existing)
        self.assertEqual(s1.name, "Photos_Session")
        self.assertEqual(s1.categories, ["Keluarga"])

        variant = Path(str(self.photos_dir).lower()) / "."
        s2, is_existing2 = mgr.create_or_get_session(
            variant, name="Ignored", categories=["Keluarga", "Teman"]
        )
        self.assertTrue(is_existing2)
        self.assertEqual(s2.id, s1.id)
        self.assertEqual(len(mgr.sessions), 1)
        self.assertEqual(s2.categories, ["Keluarga", "Teman"])

    def test_natural_and_date_sort_modes_preserve_active_photo(self) -> None:
        p1 = _create_image(self.photos_dir / "img1.jpg")
        p2 = _create_image(self.photos_dir / "img2.jpg")
        p10 = _create_image(self.photos_dir / "img10.jpg")
        (self.photos_dir / "notes.txt").write_text("ignore", encoding="utf-8")
        sub = self.photos_dir / "Sub"
        sub.mkdir()
        _create_image(sub / "nested.jpg")

        os.utime(p1, (1000, 1000))
        os.utime(p10, (2000, 2000))
        os.utime(p2, (3000, 3000))

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(self.photos_dir)
        queue = mgr.get_queue(s.id)
        self.assertEqual(
            [p.name for p in queue], ["img1.jpg", "img2.jpg", "img10.jpg"]
        )

        # Focus on img2.jpg then switch sort mode
        mgr.skip_photo(s.id)
        self.assertEqual(mgr.get_current_photo(s.id).name, "img2.jpg")

        mgr.set_sort_mode(s.id, "name_desc")
        self.assertEqual(
            [e.name for e in s.timeline],
            ["img10.jpg", "img2.jpg", "img1.jpg"],
        )
        self.assertEqual(mgr.get_current_photo(s.id).name, "img2.jpg")
        self.assertEqual(s.current_index, 1)

        mgr.set_sort_mode(s.id, "date_asc")
        self.assertEqual(
            [e.name for e in s.timeline],
            ["img1.jpg", "img10.jpg", "img2.jpg"],
        )
        self.assertEqual(mgr.get_current_photo(s.id).name, "img2.jpg")
        self.assertEqual(s.current_index, 2)

        mgr.set_sort_mode(s.id, "date_desc")
        self.assertEqual(
            [e.name for e in s.timeline],
            ["img2.jpg", "img10.jpg", "img1.jpg"],
        )
        self.assertEqual(mgr.get_current_photo(s.id).name, "img2.jpg")
        self.assertEqual(s.current_index, 0)

    def test_fixed_timeline_numbering_remains_immutable_after_moves(self) -> None:
        for i in range(1, 26):
            _create_image(self.photos_dir / f"img_{i:02d}.jpg")

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, categories=["Favorit"]
        )
        self.assertEqual(len(s.timeline), 25)

        # Sort first 5 photos -> index stays anchored in 1..25 timeline
        for _ in range(5):
            mgr.move_current_photo(s.id, "Favorit")

        # Skip ahead to img_20.jpg (index 19 -> display 20 / 25)
        while s.current_index < 19:
            mgr.skip_photo(s.id)

        self.assertEqual(s.current_index, 19)
        self.assertEqual(mgr.get_current_photo(s.id).name, "img_20.jpg")
        self.assertEqual(len(s.timeline), 25)
        self.assertEqual(len(mgr.get_queue(s.id)), 20)
        self.assertEqual(s.timeline[0].status, "sorted")
        self.assertEqual(s.timeline[0].rel_path, "Favorit/img_01.jpg")
        self.assertEqual(s.timeline[19].index, 19)

        # Reload from JSON and verify timeline 20 / 25 persists
        mgr2 = SessionManager(state_file=self.state_file)
        s2 = mgr2.get_session(s.id)
        mgr2.sync_session(s2.id)
        self.assertEqual(len(s2.timeline), 25)
        self.assertEqual(s2.current_index, 19)
        self.assertEqual(mgr2.get_current_photo(s2.id).name, "img_20.jpg")

    def test_unified_step_back_never_wraps_from_zero_and_restores_sorted(self) -> None:
        _create_image(self.photos_dir / "a.jpg")
        _create_image(self.photos_dir / "b.jpg")
        _create_image(self.photos_dir / "c.jpg")

        cat_dir = self.photos_dir / "Favorit"
        cat_dir.mkdir()
        _create_image(cat_dir / "b.jpg")

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, categories=["Favorit"]
        )

        # At index 0: step_back / prev_photo / undo_last_action NEVER wrap to last photo
        self.assertEqual(s.current_index, 0)
        self.assertIsNone(mgr.step_back(s.id))
        self.assertIsNone(mgr.prev_photo(s.id))
        self.assertIsNone(mgr.undo_last_action(s.id))
        self.assertEqual(s.current_index, 0)

        # Skip a.jpg (0 -> 1), then move b.jpg with collision -> b (1).jpg (1 -> 2)
        mgr.skip_photo(s.id)
        self.assertEqual(s.timeline[0].status, "skipped")
        action = mgr.move_current_photo(s.id, "Favorit")
        self.assertEqual(Path(action.dest_path).name, "b (1).jpg")
        self.assertEqual(s.current_index, 2)
        self.assertEqual(mgr.get_current_photo(s.id).name, "c.jpg")
        self.assertEqual(mgr.get_cached_counts(s.id)["Favorit"], 2)

        # Persistence reload check
        mgr2 = SessionManager(state_file=self.state_file)
        s2 = mgr2.get_session(s.id)
        self.assertEqual(mgr2.get_current_photo(s2.id).name, "c.jpg")

        # v0.4.0: step_back / prev_photo from index 2 is PURE navigation (does NOT revert b (1).jpg!)
        navigated = mgr2.step_back(s2.id)
        self.assertIsNotNone(navigated)
        self.assertEqual(s2.current_index, 1)
        self.assertTrue((cat_dir / "b (1).jpg").exists())
        self.assertEqual(s2.timeline[1].status, "sorted")
        self.assertEqual(s2.timeline[1].category, "Favorit")
        self.assertEqual(mgr2.get_current_photo(s2.id).name, "b (1).jpg")
        self.assertEqual(mgr2.get_cached_counts(s2.id)["Favorit"], 2)

        # v0.4.0: undo_last_action restores b (1).jpg back to b.jpg on root and updates count
        undone = mgr2.undo_last_action(s2.id)
        self.assertIsNotNone(undone)
        self.assertEqual(s2.current_index, 1)
        self.assertFalse((cat_dir / "b (1).jpg").exists())
        self.assertTrue((self.photos_dir / "b.jpg").exists())
        self.assertEqual(s2.timeline[1].status, "pending")
        self.assertIsNone(s2.timeline[1].category)
        self.assertEqual(mgr2.get_cached_counts(s2.id)["Favorit"], 1)

        # prev_photo from index 1 -> lands on index 0 cleanly without changing status
        nav0 = mgr2.prev_photo(s2.id)
        self.assertIsNotNone(nav0)
        self.assertEqual(s2.current_index, 0)

        # step_back at index 0 is NO-OP
        self.assertIsNone(mgr2.step_back(s2.id))

    def test_step_back_from_completed_session_restores_last_sorted_photo(self) -> None:
        _create_image(self.photos_dir / "x.jpg")
        _create_image(self.photos_dir / "y.jpg")
        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(self.photos_dir, categories=["Keep"])

        mgr.move_current_photo(s.id, TRASH_FOLDER)
        self.assertEqual(len(mgr.get_queue(s.id)), 1)
        mgr.move_current_photo(s.id, "Keep")
        self.assertEqual(len(mgr.get_queue(s.id)), 0)
        self.assertTrue(mgr.is_session_complete(s.id))
        self.assertIsNone(mgr.get_current_photo(s.id))

        # Undo from completed state restores y.jpg at index 1
        restored = mgr.undo_last_action(s.id)
        self.assertIsNotNone(restored)
        self.assertFalse(mgr.is_session_complete(s.id))
        self.assertEqual(s.current_index, 1)
        self.assertTrue((self.photos_dir / "y.jpg").exists())
        self.assertFalse((self.photos_dir / "Keep" / "y.jpg").exists())
        self.assertEqual(mgr.get_cached_counts(s.id)["Keep"], 0)

    def test_jump_to_sorted_entry_restores_file_to_root(self) -> None:
        _create_image(self.photos_dir / "p1.jpg")
        _create_image(self.photos_dir / "p2.jpg")
        _create_image(self.photos_dir / "p3.jpg")
        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, categories=["Keep", "Other"]
        )

        mgr.move_current_photo(s.id, "Keep")  # p1 -> Keep
        self.assertEqual(s.current_index, 1)
        self.assertEqual(mgr.get_cached_counts(s.id)["Keep"], 1)

        # Jump to index 0 (p1) -> pure navigation, stays sorted in Keep
        jumped = mgr.jump_to(s.id, 0)
        self.assertEqual(jumped, self.photos_dir / "Keep" / "p1.jpg")
        self.assertTrue((self.photos_dir / "Keep" / "p1.jpg").exists())
        self.assertEqual(s.timeline[0].status, "sorted")
        self.assertEqual(mgr.get_cached_counts(s.id)["Keep"], 1)

        # Direct reassignment from Keep -> Other while focused on sorted item 0
        mgr.move_current_photo(s.id, "Other")
        self.assertTrue((self.photos_dir / "Other" / "p1.jpg").exists())
        self.assertFalse((self.photos_dir / "Keep" / "p1.jpg").exists())
        self.assertEqual(mgr.get_cached_counts(s.id)["Keep"], 0)
        self.assertEqual(mgr.get_cached_counts(s.id)["Other"], 1)

        # Ctrl+Z undo restores p1 back from Other -> Keep
        mgr.undo_last_action(s.id)
        self.assertTrue((self.photos_dir / "Keep" / "p1.jpg").exists())
        self.assertEqual(mgr.get_cached_counts(s.id)["Keep"], 1)
        self.assertEqual(mgr.get_cached_counts(s.id)["Other"], 0)

    def test_move_is_o1_and_subsequent_sync_appends_external_files(self) -> None:
        _create_image(self.photos_dir / "a.jpg")
        _create_image(self.photos_dir / "b.jpg")
        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(self.photos_dir, categories=["Keep"])

        # Drop external file after initial sync
        _create_image(self.photos_dir / "external_new.jpg")

        # O(1) move does NOT scan directory, so timeline stays length 2
        mgr.move_current_photo(s.id, "Keep")
        self.assertEqual(len(s.timeline), 2)
        self.assertEqual(s.timeline[0].status, "sorted")

        # Explicit sync appends external file at the end without removing sorted entry 0
        mgr.sync_session(s.id)
        self.assertEqual([e.name for e in s.timeline], ["a.jpg", "b.jpg", "external_new.jpg"])
        self.assertEqual(s.timeline[0].status, "sorted")
        self.assertEqual(s.timeline[0].rel_path, "Keep/a.jpg")

    def test_v010_json_backward_compatibility_without_timeline(self) -> None:
        _create_image(self.photos_dir / "a.jpg")
        _create_image(self.photos_dir / "b.jpg")
        keep_dir = self.photos_dir / "Keep"
        keep_dir.mkdir()
        _create_image(keep_dir / "sorted_01.jpg")

        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        legacy_payload = {
            "app_name": "PhotoBox",
            "app_version": "0.1.0",
            "active_session_id": "legacy1",
            "sessions": [
                {
                    "id": "legacy1",
                    "name": "Legacy",
                    "folder_path": str(self.photos_dir),
                    "categories": ["Keep"],
                    "current_index": 0,
                    "current_photo_name": "b.jpg",
                    "sort_mode": "name_asc",
                    "skipped_files": ["a.jpg"],
                    "undo_stack": [
                        {
                            "photo_name": "sorted_01.jpg",
                            "src_path": str(self.photos_dir / "sorted_01.jpg"),
                            "dest_path": str(keep_dir / "sorted_01.jpg"),
                            "category": "Keep",
                            "prev_index": 0,
                            "timestamp": "2026-11-16T00:00:00Z",
                        }
                    ],
                }
            ],
        }
        self.state_file.write_text(json.dumps(legacy_payload), encoding="utf-8")

        mgr = SessionManager(state_file=self.state_file)
        s = mgr.get_session("legacy1")
        self.assertEqual(s.timeline, [])
        mgr.sync_session(s.id)
        self.assertEqual(len(s.timeline), 3)
        self.assertEqual(mgr.get_current_photo(s.id).name, "b.jpg")
        skipped_entry = next(e for e in s.timeline if e.name == "a.jpg")
        self.assertEqual(skipped_entry.status, "skipped")

    def test_rename_and_revert_category_keep_timeline_in_sync(self) -> None:
        _create_image(self.photos_dir / "p1.jpg")
        _create_image(self.photos_dir / "p2.jpg")
        _create_image(self.photos_dir / "p3.jpg")

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, categories=["lama", "CatB"]
        )

        mgr.move_current_photo(s.id, "lama")  # p1 -> lama
        mgr.move_current_photo(s.id, "CatB")  # p2 -> CatB
        self.assertEqual(s.timeline[0].rel_path, "lama/p1.jpg")

        mgr.rename_category(s.id, "lama", "Baru")
        self.assertEqual(s.categories, ["Baru", "CatB"])
        self.assertEqual(s.timeline[0].category, "Baru")
        self.assertEqual(s.timeline[0].rel_path, "Baru/p1.jpg")
        self.assertTrue((self.photos_dir / "Baru" / "p1.jpg").exists())

        # Case-only rename on Windows
        mgr.rename_category(s.id, "Baru", "baru")
        self.assertEqual(s.timeline[0].category, "baru")
        self.assertEqual(s.timeline[0].rel_path, "baru/p1.jpg")

        # Revert category with collision in root
        _create_image(self.photos_dir / "p1.jpg")
        reverted = mgr.revert_and_remove_category(s.id, "baru")
        self.assertEqual(reverted, 1)
        self.assertFalse((self.photos_dir / "baru").exists())
        self.assertTrue((self.photos_dir / "p1 (1).jpg").exists())
        self.assertEqual(s.timeline[0].status, "pending")
        self.assertIsNone(s.timeline[0].category)
        self.assertEqual(s.timeline[0].rel_path, "p1 (1).jpg")
        self.assertEqual(s.categories, ["CatB"])

    def test_delete_session_keeps_disk_files_untouched(self) -> None:
        _create_image(self.photos_dir / "keep_me.jpg")
        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(self.photos_dir, categories=["Done"])
        mgr.move_current_photo(s.id, "Done")

        deleted = mgr.delete_session(s.id)
        self.assertTrue(deleted)
        self.assertEqual(mgr.sessions, [])
        self.assertTrue((self.photos_dir / "Done" / "keep_me.jpg").exists())

    def test_exif_metadata_and_exif_image_loading(self) -> None:
        from ui.photo_canvas import load_exif_qimage

        rotated_path = _create_image(
            self.photos_dir / "rotated.jpg", size=(100, 60), exif_orientation=6
        )
        meta = get_fast_photo_metadata(rotated_path)
        self.assertEqual((meta["width"], meta["height"]), (60, 100))
        self.assertEqual(get_photo_metadata(rotated_path), meta)

        qimg = load_exif_qimage(rotated_path)
        self.assertIsNotNone(qimg)
        self.assertEqual((qimg.width(), qimg.height()), (60, 100))

    def test_stale_undo_entry_popped_allows_subsequent_undo(self) -> None:
        _create_image(self.photos_dir / "a.jpg")
        _create_image(self.photos_dir / "b.jpg")
        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(self.photos_dir, categories=["Keep"])

        mgr.move_current_photo(s.id, "Keep")  # a.jpg
        mgr.move_current_photo(s.id, "Keep")  # b.jpg
        self.assertEqual(len(s.undo_stack), 2)

        # Simulate external deletion of b.jpg
        (self.photos_dir / "Keep" / "b.jpg").unlink()
        with self.assertRaises(FileNotFoundError):
            mgr.undo_last_action(s.id)

        self.assertEqual(len(s.undo_stack), 1)
        mgr_reloaded = SessionManager(state_file=self.state_file)
        self.assertEqual(len(mgr_reloaded.get_session(s.id).undo_stack), 1)

        restored = mgr_reloaded.undo_last_action(s.id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.photo_name, "a.jpg")
        self.assertTrue((self.photos_dir / "a.jpg").exists())

    def test_cached_session_progress_avoids_rescanning_inactive_sessions(self) -> None:
        other_dir = self.root / "OtherAlbum"
        other_dir.mkdir()
        _create_image(self.photos_dir / "a.jpg")
        _create_image(other_dir / "o1.jpg")

        mgr = SessionManager(state_file=self.state_file)
        s_inactive, _ = mgr.create_or_get_session(other_dir, categories=["Done"])
        s_active, _ = mgr.create_or_get_session(self.photos_dir, categories=["Done"])

        self.assertEqual(mgr.get_session_progress(s_inactive.id, use_cached=False), (1, 0))
        _create_image(other_dir / "o2.jpg")
        self.assertEqual(mgr.get_session_progress(s_inactive.id, use_cached=True), (1, 0))
        mgr.sync_session(s_inactive.id)
        self.assertEqual(mgr.get_session_progress(s_inactive.id, use_cached=True), (2, 0))
        self.assertEqual(mgr.active_session_id, s_active.id)

    def test_move_file_safe_retries_transient_permission_error(self) -> None:
        _create_image(self.photos_dir / "locked.jpg")
        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(self.photos_dir, categories=["Keep"])

        real_move = shutil.move
        attempts = {"count": 0}

        def flaky_move(src: str, dst: str) -> str:
            attempts["count"] += 1
            if attempts["count"] <= 2:
                raise PermissionError("Simulated WinError 32 lock")
            return real_move(src, dst)

        with (
            mock.patch("core.session_manager.shutil.move", side_effect=flaky_move),
            mock.patch("core.session_manager.time.sleep") as sleep_mock,
        ):
            action = mgr.move_current_photo(s.id, "Keep")

        self.assertEqual(attempts["count"], 3)
        self.assertEqual(sleep_mock.call_count, 2)
        self.assertTrue(Path(action.dest_path).exists())
        self.assertEqual(s.timeline[0].status, "sorted")

    def test_validate_keybind_reserved_and_conflicts(self) -> None:
        self.assertEqual(validate_keybind("q"), "Q")
        self.assertEqual(validate_keybind("f3"), "F3")

        for reserved in (
            "Tab", "Space", " ", "\t", "Backspace", "Delete", "Del",
            "Left", "Right", "Up", "Down", "Enter", "Return", "Esc", "Escape",
            "Ctrl+Z", "ctrl + z", "\x01", "\x1b", "\x7f", "\n",
        ):
            with self.assertRaises(ValueError):
                validate_keybind(reserved)

        existing = {"Bagus": "1", "Keluarga": "Q"}
        with self.assertRaises(ValueError) as ctx:
            validate_keybind("1", existing_map=existing)
        self.assertIn("Tombol '1' sudah dipakai oleh kategori 'Bagus'", str(ctx.exception))

        # Updating same category with its own existing key is allowed
        self.assertEqual(
            validate_keybind("1", existing_map=existing, ignore_category="Bagus"),
            "1",
        )
        self.assertEqual(next_available_keybind(["1", "2"]), "3")
        self.assertEqual(next_available_keybind(["Q"], ["Q", "W", "E"]), "W")

    def test_app_settings_persistence_and_updates(self) -> None:
        mgr = SessionManager(state_file=self.state_file)
        self.assertEqual(mgr.settings.theme_mode, "black")
        self.assertEqual(mgr.settings.custom_bg_dim, 68)
        self.assertTrue(mgr.settings.video_autoplay)
        self.assertEqual(mgr.settings.keybind_profile, "default")
        self.assertEqual(
            mgr.settings.custom_keybinds,
            ["1", "2", "3", "4", "5", "6", "7", "8", "9"],
        )

        updated = mgr.update_settings(
            theme_mode="white",
            custom_bg_path="C:/wallpapers/studio.png",
            custom_bg_dim=40,
            video_autoplay=False,
            keybind_profile="custom",
            custom_keybinds=["Q", "W", "E", "R", "T", "Y", "U", "I", "O"],
            default_categories=[
                DefaultCategorySpec(name="Portrait", key="P", color="#EC4899")
            ],
        )
        self.assertEqual(updated.theme_mode, "white")
        self.assertEqual(updated.custom_bg_dim, 40)
        self.assertFalse(updated.video_autoplay)

        raw = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertIn("settings", raw)
        self.assertEqual(raw["settings"]["theme_mode"], "white")
        self.assertEqual(raw["settings"]["custom_bg_dim"], 40)

        mgr2 = SessionManager(state_file=self.state_file)
        self.assertEqual(mgr2.settings.theme_mode, "white")
        # Legacy 'custom' profile migrates cleanly to named 'Custom' profile
        self.assertEqual(mgr2.settings.keybind_profile, "Custom")
        self.assertIn("Custom", mgr2.settings.custom_keybind_profiles)
        self.assertEqual(mgr2.settings.custom_keybinds[0], "Q")
        self.assertEqual(mgr2.settings.default_categories[0].name, "Portrait")
        self.assertEqual(mgr2.settings.default_categories[0].key, "P")
        self.assertEqual(mgr2.settings.default_categories[0].color, "#EC4899")

    def test_deep_search_recursive_scan_and_origin_restore(self) -> None:
        self.assertFalse(has_subfolders(self.photos_dir))
        (self.photos_dir / "_Buang").mkdir()
        (self.photos_dir / ".hidden").mkdir()
        self.assertFalse(has_subfolders(self.photos_dir))

        sub1 = self.photos_dir / "Day1"
        sub2 = sub1 / "Nested"
        sub2.mkdir(parents=True)
        self.assertTrue(has_subfolders(self.photos_dir))

        _create_image(self.photos_dir / "root_c.jpg")
        _create_image(sub1 / "a.jpg")
        _create_image(sub2 / "b.jpg")
        _create_image(self.photos_dir / "_Buang" / "ignored_trash.jpg")
        _create_image(self.photos_dir / ".hidden" / "ignored_hidden.jpg")

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir,
            categories=["Keep"],
            deep_search=True,
        )
        self.assertEqual(
            [e.rel_path for e in s.timeline],
            ["Day1/a.jpg", "Day1/Nested/b.jpg", "root_c.jpg"],
        )
        self.assertEqual(s.timeline[1].origin_rel_path, "Day1/Nested/b.jpg")

        # Move Day1/a.jpg to Keep -> sync_session must NOT re-add Keep/a.jpg
        mgr.move_current_photo(s.id, "Keep")
        self.assertTrue((self.photos_dir / "Keep" / "a.jpg").exists())
        mgr.sync_session(s.id)
        self.assertEqual(len(s.timeline), 3)
        self.assertEqual(s.timeline[0].status, "sorted")

        # Move Day1/Nested/b.jpg to Keep, jump_to stays sorted, undo_last_action restores to Day1/Nested/b.jpg
        mgr.move_current_photo(s.id, "Keep")
        self.assertTrue((self.photos_dir / "Keep" / "b.jpg").exists())
        viewed_jump = mgr.jump_to(s.id, 1)
        self.assertEqual(viewed_jump, self.photos_dir / "Keep" / "b.jpg")
        mgr.undo_last_action(s.id)
        self.assertTrue((sub2 / "b.jpg").exists())

        # Second undo pops Keep/a.jpg and restores back to Day1/a.jpg
        restored_back = mgr.undo_last_action(s.id)
        self.assertIsNotNone(restored_back)
        self.assertTrue((sub1 / "a.jpg").exists())
        self.assertFalse((self.photos_dir / "Keep" / "a.jpg").exists())

    def test_category_keybind_color_and_video_metadata(self) -> None:
        _create_image(self.photos_dir / "p1.jpg")
        vid = self.photos_dir / "clip.mp4"
        subprocess.run(
            [
                "ffmpeg", "-y", "-f", "lavfi",
                "-i", "color=c=blue:s=160x120:d=2",
                "-pix_fmt", "yuv420p", str(vid),
            ],
            check=True,
            capture_output=True,
        )
        meta = get_fast_media_metadata(vid)
        self.assertTrue(meta["is_video"])
        self.assertEqual((meta["width"], meta["height"]), (160, 120))
        self.assertGreaterEqual(int(meta["duration_ms"]), 1500)

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, categories=["Bagus", "Keluarga"]
        )
        self.assertEqual(mgr.get_effective_keybinds(s.id), {"Bagus": "1", "Keluarga": "2"})

        mgr.set_category_keybind(s.id, "Bagus", "b")
        mgr.set_category_color(s.id, "Bagus", "#10b981")
        with self.assertRaises(ValueError):
            mgr.set_category_keybind(s.id, "Keluarga", "B")
        with self.assertRaises(ValueError):
            mgr.set_category_keybind(s.id, "Keluarga", "Space")

        mgr.rename_category(s.id, "Bagus", "Best")
        self.assertEqual(mgr.get_effective_keybinds(s.id)["Best"], "B")
        self.assertEqual(mgr.get_effective_category_colors(s.id)["Best"], "#10B981")

        mgr.revert_and_remove_category(s.id, "Best")
        self.assertNotIn("Best", s.category_meta)

    def test_deep_search_duplicate_filenames_preserve_rel_path_focus(self) -> None:
        sub_a = self.photos_dir / "SubA"
        sub_b = self.photos_dir / "SubB"
        sub_a.mkdir()
        sub_b.mkdir()
        _create_image(sub_a / "img.jpg")
        _create_image(sub_b / "img.jpg")

        mgr = SessionManager(state_file=self.state_file)
        s, _ = mgr.create_or_get_session(
            self.photos_dir, categories=["Keep"], deep_search=True
        )
        self.assertEqual(
            [e.rel_path for e in s.timeline],
            ["SubA/img.jpg", "SubB/img.jpg"],
        )

        # Skip SubA/img.jpg so focus lands on SubB/img.jpg
        mgr.skip_photo(s.id)
        self.assertEqual(s.current_index, 1)
        self.assertEqual(s.current_photo_rel_path, "SubB/img.jpg")
        self.assertEqual(mgr.get_current_photo(s.id), sub_b / "img.jpg")

        # Simulate stale current_index=0 and reload from JSON; sync_session must resume on SubB/img.jpg!
        s.current_index = 0
        mgr.save()

        mgr2 = SessionManager(state_file=self.state_file)
        s2 = mgr2.get_session(s.id)
        mgr2.sync_session(s2.id)
        self.assertEqual(s2.current_index, 1)
        self.assertEqual(mgr2.get_current_photo(s2.id), sub_b / "img.jpg")

        # Move SubB/img.jpg -> Keep/img.jpg, jump_to stays focused on Keep/img.jpg, undo restores to SubB/img.jpg
        mgr2.move_current_photo(s2.id, "Keep")
        self.assertTrue((self.photos_dir / "Keep" / "img.jpg").exists())
        self.assertFalse((sub_b / "img.jpg").exists())
        mgr2.jump_to(s2.id, 1)
        self.assertEqual(s2.current_index, 1)
        mgr2.undo_last_action(s2.id)
        self.assertTrue((sub_b / "img.jpg").exists())

        # Sort both SubB/img.jpg (idx 1) and SubA/img.jpg (idx 0) -> undo from complete restores SubA/img.jpg
        mgr2.move_current_photo(s2.id, "Keep")  # idx 1 -> idx 0
        mgr2.move_current_photo(s2.id, "Keep")  # idx 0 -> complete
        self.assertTrue(mgr2.is_session_complete(s2.id))
        mgr2.undo_last_action(s2.id)
        self.assertFalse(mgr2.is_session_complete(s2.id))
        self.assertTrue((sub_a / "img.jpg").exists())

    def test_v040_named_profiles_trash_settings_and_detailed_exif(self) -> None:
        from PIL import ExifTags
        from core.session_manager import get_detailed_media_metadata

        mgr = SessionManager(state_file=self.state_file)
        # Default language is English & trash category editable
        self.assertEqual(mgr.settings.language, "en")
        self.assertEqual(mgr.settings.trash_category.name, "_Buang")
        self.assertEqual(mgr.settings.trash_category.key, "DEL")

        mgr.save_custom_keybind_profile(
            "LeftHand", ["Q", "W", "E", "R", "A", "S", "D", "F", "G"], activate=True
        )
        self.assertEqual(mgr.settings.keybind_profile, "LeftHand")
        self.assertEqual(mgr.get_active_keybind_slots()[:4], ["Q", "W", "E", "R"])

        mgr.update_settings(
            language="id",
            video_seek_seconds=12,
            video_arrow_mode="skip",
            trash_category=DefaultCategorySpec("_Recycled", "X", "#EF4444"),
            visible_metadata_fields=["camera", "aperture", "dimensions"],
        )
        mgr2 = SessionManager(state_file=self.state_file)
        self.assertEqual(mgr2.settings.language, "id")
        self.assertEqual(mgr2.settings.video_seek_seconds, 12)
        self.assertEqual(mgr2.settings.video_arrow_mode, "skip")
        self.assertEqual(mgr2.settings.trash_category.name, "_Recycled")
        self.assertEqual(mgr2.settings.trash_category.key, "X")
        self.assertTrue(mgr2.delete_custom_keybind_profile("LeftHand"))
        self.assertEqual(mgr2.settings.keybind_profile, "default")

        # Write sample image with EXIF tags
        img_path = self.photos_dir / "exif_shot.jpg"
        im = Image.new("RGB", (320, 240), (30, 90, 140))
        exif = Image.Exif()
        exif[0x010F] = "Canon"
        exif[0x0110] = "Canon EOS R6"
        exif[0x8827] = 400  # ISO
        im.save(str(img_path), "JPEG", exif=exif)
        meta = get_detailed_media_metadata(img_path)
        self.assertEqual(meta["dimensions"], "320 × 240 px")
        self.assertEqual(meta["camera"], "Canon EOS R6")
        self.assertEqual(meta["iso"], "ISO 400")

    def test_v060_sort_by_size_gallery_prefs_bulk_move_and_batch_undo(self) -> None:
        from unittest.mock import patch
        from core.session_manager import SORT_MODE_LABELS, _move_file_safe

        self.assertEqual(SORT_MODE_LABELS["name_asc"], "Filename (A-Z)")
        self.assertNotIn("Natural", SORT_MODE_LABELS["name_asc"])

        # Create 5 files with strictly distinct sizes
        p_small = _create_image(self.photos_dir / "a_small.jpg", size=(40, 40), color=(10, 20, 30))
        p_mid = _create_image(self.photos_dir / "b_mid.jpg", size=(420, 420), color=(90, 140, 200))
        # Ensure big file is genuinely largest bytes via varied pixel noise
        big_im = Image.frombytes(
            "RGB",
            (600, 600),
            bytes(((i * 73) % 256) for i in range(600 * 600 * 3)),
        )
        p_big = self.photos_dir / "c_big.jpg"
        big_im.save(p_big, format="JPEG", quality=95)
        p_d = _create_image(self.photos_dir / "d_extra.jpg", size=(120, 120), color=(200, 100, 40))
        p_e = _create_image(self.photos_dir / "e_extra.jpg", size=(180, 180), color=(50, 200, 80))

        sizes = {p.name: p.stat().st_size for p in (p_small, p_mid, p_big, p_d, p_e)}
        self.assertLess(sizes["a_small.jpg"], sizes["c_big.jpg"])

        mgr = SessionManager(state_file=self.state_file)
        self.assertTrue(mgr.settings.gallery_open)
        self.assertEqual(mgr.settings.gallery_width, 276)

        mgr.update_settings(gallery_open=False, gallery_width=999)
        mgr_reloaded = SessionManager(state_file=self.state_file)
        self.assertFalse(mgr_reloaded.settings.gallery_open)
        self.assertEqual(mgr_reloaded.settings.gallery_width, 640)  # clamped max 640

        s, _ = mgr.create_or_get_session(
            self.photos_dir, name="BatchTest", categories=["Selects", "Rejects"]
        )
        mgr.jump_to(s.id, 1)  # b_mid.jpg
        mgr.set_sort_mode(s.id, "size_desc")
        self.assertEqual(s.timeline[0].name, "c_big.jpg")
        self.assertEqual(s.timeline[-1].name, "a_small.jpg")
        self.assertEqual(mgr.get_current_photo(s.id).name, "b_mid.jpg")

        mgr.set_sort_mode(s.id, "size_asc")
        self.assertEqual(s.timeline[0].name, "a_small.jpg")
        self.assertEqual(s.timeline[-1].name, "c_big.jpg")
        self.assertEqual(mgr.get_current_photo(s.id).name, "b_mid.jpg")

        # Reset back to name_asc for predictable index tests [0..4]
        mgr.set_sort_mode(s.id, "name_asc")
        names = [e.name for e in s.timeline]
        self.assertEqual(names, ["a_small.jpg", "b_mid.jpg", "c_big.jpg", "d_extra.jpg", "e_extra.jpg"])

        # Bulk move [1, 2, 3] -> Selects (shared batch_id, next unsorted becomes index 4)
        moved, err = mgr.bulk_move_photos(s.id, [1, 2, 3], "Selects")
        self.assertIsNone(err)
        self.assertEqual(len(moved), 3)
        self.assertTrue(moved[0].batch_id)
        self.assertEqual({a.batch_id for a in moved}, {moved[0].batch_id})
        self.assertEqual(mgr.get_category_counts(s.id)["Selects"], 3)
        self.assertEqual(s.current_index, 4)
        for nm in ("b_mid.jpg", "c_big.jpg", "d_extra.jpg"):
            self.assertTrue((self.photos_dir / "Selects" / nm).is_file())

        # Atomic batch undo via undo_last_action restores ALL 3 back to root & focuses earliest (1)
        earliest = mgr.undo_last_action(s.id)
        self.assertIsNotNone(earliest)
        self.assertEqual(earliest.index, 1)
        self.assertEqual(s.current_index, 1)
        self.assertEqual(mgr.get_category_counts(s.id)["Selects"], 0)
        for nm in ("b_mid.jpg", "c_big.jpg", "d_extra.jpg"):
            self.assertTrue((self.photos_dir / nm).is_file())

        # Partial failure resilience: 2nd item fails -> 1st commits, 2nd & 3rd stay pending
        call_count = 0
        def _flaky_move(src: Path, dest: Path) -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise PermissionError("Simulated lock")
            _move_file_safe(src, dest)

        with patch("core.session_manager._move_file_safe", side_effect=_flaky_move):
            part_moved, part_err = mgr.bulk_move_photos(s.id, [0, 1, 2], "Rejects")
        self.assertIsInstance(part_err, PermissionError)
        self.assertEqual(len(part_moved), 1)
        self.assertEqual(mgr.get_category_counts(s.id)["Rejects"], 1)
        self.assertTrue((self.photos_dir / "Rejects" / "a_small.jpg").is_file())
        self.assertTrue((self.photos_dir / "b_mid.jpg").is_file())
        mgr.undo_last_batch(s.id)
        self.assertTrue((self.photos_dir / "a_small.jpg").is_file())

        # Missing-file resilience in batch undo: delete middle file -> others still restore!
        moved2, _ = mgr.bulk_move_photos(s.id, [0, 1, 2], "Selects")
        self.assertEqual(len(moved2), 3)
        (self.photos_dir / "Selects" / "b_mid.jpg").unlink()
        with self.assertRaises(FileNotFoundError):
            mgr.undo_last_batch(s.id)
        self.assertTrue((self.photos_dir / "a_small.jpg").is_file())
        self.assertTrue((self.photos_dir / "c_big.jpg").is_file())
        self.assertEqual(mgr.get_category_counts(s.id)["Selects"], 0)
        self.assertEqual(len(s.timeline), 4)


if __name__ == "__main__":
    unittest.main()
