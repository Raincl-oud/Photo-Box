"""PhotoBox v0.6.0 entrypoint and main workspace controller."""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import QEvent, QObject, Qt, QTimer, QUrl
from PyQt6.QtGui import QColor, QDesktopServices, QIcon, QKeyEvent, QPainter, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLineEdit,
    QMainWindow,
    QPlainTextEdit,
    QSpinBox,
    QStackedWidget,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from core.session_manager import (
    APP_VERSION,
    DEFAULT_CATEGORY_COLOR,
    SHORTCUT_KEYS,
    SessionManager,
    SessionState,
    TimelineEntry,
    get_detailed_media_metadata,
)
from ui.i18n import set_language, tr
from ui.motion import ValueAnimator, WidgetSlideFadeTransition, animate_sidebar_width
from ui.theme import (
    CATEGORY_COLOR_PRESETS,
    PALETTE,
    get_stylesheet,
    load_bundled_fonts,
    resolve_asset_path,
    set_active_theme,
)
from ui.views import (
    FullMetadataOverlay,
    KeyCaptureOverlay,
    SessionCompleteView,
    SessionSetupView,
    SessionSidebar,
    SettingsOverlay,
    SortingView,
    SplashScreen,
    _ColorPickerOverlay,
    _ConfirmOverlay,
    _InWindowOverlayBase,
    _TextPromptOverlay,
)


_SHORTCUT_INDEX_MAP: Dict[str, int] = {k: i for i, k in enumerate(SHORTCUT_KEYS)}


def _key_event_to_token(event: QKeyEvent) -> Optional[str]:
    key = event.key()
    mods = event.modifiers()
    if mods & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier):
        return None
    special = {
        Qt.Key.Key_Delete: "DEL",
        Qt.Key.Key_Insert: "INS",
        Qt.Key.Key_Home: "HOME",
        Qt.Key.Key_End: "END",
        Qt.Key.Key_PageUp: "PGUP",
        Qt.Key.Key_PageDown: "PGDN",
    }
    if key in special:
        return special[key]
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


class _MainContainer(QWidget):
    """Root container painting full-window custom background image + dimming scrim."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setObjectName("MainContainer")
        self._bg_pixmap: Optional[QPixmap] = None
        self._bg_path: str = ""
        self._dim_percent: int = 68
        self._theme_mode: str = "black"

    def set_background(self, bg_path: str, dim_percent: int, theme_mode: str) -> None:
        clean = str(bg_path or "").strip()
        self._dim_percent = max(20, min(90, int(dim_percent)))
        self._theme_mode = "white" if theme_mode == "white" else "black"
        if clean != self._bg_path:
            self._bg_path = clean
            if clean and os.path.isfile(clean):
                pix = QPixmap(clean)
                self._bg_pixmap = pix if not pix.isNull() else None
            else:
                self._bg_pixmap = None
        self.update()

    @property
    def has_active_background(self) -> bool:
        return self._bg_pixmap is not None and not self._bg_pixmap.isNull()

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(PALETTE["bg_app"]))
        if self._bg_pixmap is not None and not self._bg_pixmap.isNull():
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
            pw, ph = self._bg_pixmap.width(), self._bg_pixmap.height()
            rw, rh = max(1, self.width()), max(1, self.height())
            if pw > 0 and ph > 0:
                scale = max(rw / pw, rh / ph)
                sw, sh = int(round(pw * scale)), int(round(ph * scale))
                dx = (rw - sw) // 2
                dy = (rh - sh) // 2
                painter.drawPixmap(dx, dy, sw, sh, self._bg_pixmap)
            rgb = (244, 242, 237) if self._theme_mode == "white" else (8, 8, 10)
            alpha = int(round(255 * (self._dim_percent / 100.0)))
            painter.fillRect(self.rect(), QColor(rgb[0], rgb[1], rgb[2], alpha))
        painter.end()


class MainWindow(QMainWindow):
    """Main PhotoBox workspace window with multi-session sidebar, Floating Console Deck, & overlays."""

    def __init__(
        self,
        state_file: Optional[Path | str] = None,
        storage_path: Optional[Path | str] = None,
        manager: Optional[SessionManager] = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f"PhotoBox v{APP_VERSION}")
        self.resize(1260, 820)
        self.setMinimumSize(860, 600)

        icon_path = resolve_asset_path("assets/icons/photobox.ico")
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))

        resolved_state = state_file if state_file is not None else storage_path
        self.manager = (
            manager
            if manager is not None
            else SessionManager(state_file=resolved_state)
        )
        set_language(self.manager.settings.language)

        self._sidebar_collapsed = False
        self._sidebar_width = float(SessionSidebar.TARGET_WIDTH)
        self._sidebar_anim = ValueAnimator(self._sidebar_width, parent=self)
        self._space_held = False
        self._space_combo_used = False
        self._overlay_stack: List[_InWindowOverlayBase] = []
        self._last_loaded_path: Optional[str] = None
        self._last_timeline_index: Optional[int] = None
        self._pending_nav_dir: int = 0
        self._current_meta: Dict[str, object] = {}

        self.container = _MainContainer(self)
        self.setCentralWidget(self.container)

        root = QHBoxLayout(self.container)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        self.sidebar = SessionSidebar(self.container)
        self.sidebar.add_session_requested.connect(self._on_add_session_clicked)
        self.sidebar.session_selected.connect(self._on_session_selected)
        self.sidebar.session_delete_requested.connect(self._on_session_delete_requested)
        self.sidebar.collapse_requested.connect(self.toggle_sidebar)
        root.addWidget(self.sidebar)

        self.stacked = QStackedWidget(self.container)
        self._stack_transition = WidgetSlideFadeTransition(self.stacked, duration_ms=145)
        root.addWidget(self.stacked, 1)

        # Views
        self.setup_view = SessionSetupView(
            settings_provider=lambda: self.manager.settings,
            duplicate_checker=self.manager.find_session_by_folder,
            parent=self.stacked,
        )
        self.setup_view.sidebar_expand_requested.connect(self.toggle_sidebar)
        self.setup_view.settings_requested.connect(self.open_settings)
        self.setup_view.overlay_requested.connect(self._open_overlay)
        self.setup_view.session_started.connect(self._on_session_started)
        self.setup_view.resume_existing_requested.connect(self._on_session_selected)
        self.stacked.addWidget(self.setup_view)

        self.sorting_view = SortingView(self.stacked)
        self.sorting_view.sidebar_expand_requested.connect(self.toggle_sidebar)
        self.sorting_view.settings_requested.connect(self.open_settings)
        self.sorting_view.undo_requested.connect(self._on_undo)
        self.sorting_view.prev_requested.connect(self._on_prev_photo)
        self.sorting_view.skip_requested.connect(self._on_skip_photo)
        self.sorting_view.jump_requested.connect(self._on_jump_to)
        self.sorting_view.sort_changed.connect(self._on_sort_changed)
        self.sorting_view.move_requested.connect(self._on_move_to_category)
        self.sorting_view.trash_requested.connect(self._on_trash)
        self.sorting_view.new_category_requested.connect(self._on_new_category_clicked)
        self.sorting_view.category_rename_requested.connect(self._on_category_rename_requested)
        self.sorting_view.category_keybind_requested.connect(self._on_category_keybind_requested)
        self.sorting_view.category_color_requested.connect(self._on_category_color_requested)
        self.sorting_view.category_revert_requested.connect(self._on_category_revert_requested)
        self.sorting_view.metadata_clicked.connect(self._on_metadata_clicked)
        self.sorting_view.gallery_toggle_requested.connect(self.toggle_gallery)
        self.sorting_view.gallery_item_clicked.connect(self._on_jump_to)
        self.sorting_view.gallery_preview_requested.connect(self._on_gallery_preview)
        self.sorting_view.gallery_bulk_move_requested.connect(self._on_bulk_move_to_category)
        self.sorting_view.gallery_width_committed.connect(self._on_gallery_width_committed)
        self.sorting_view.filmstrip.jump_requested.connect(self._on_jump_to)
        self.sorting_view.canvas.autoplay_toggled.connect(self._on_autoplay_toggled)
        self.sorting_view.canvas.video_metadata_ready.connect(self._on_video_metadata_ready)
        self.stacked.addWidget(self.sorting_view)

        init_settings = self.manager.settings.normalized()
        self.sorting_view.apply_gallery_preferences(
            init_settings.gallery_open, init_settings.gallery_width, animate=False
        )

        self.complete_view = SessionCompleteView(self.stacked)
        self.complete_view.sidebar_expand_requested.connect(self.toggle_sidebar)
        self.complete_view.settings_requested.connect(self.open_settings)
        self.complete_view.open_folder_requested.connect(self._on_open_folder)
        self.complete_view.undo_last_requested.connect(self._on_undo)
        self.complete_view.delete_session_requested.connect(
            lambda: self._on_session_delete_requested(self.manager.active_session_id or "")
        )
        self.stacked.addWidget(self.complete_view)

        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)

        self.apply_theme_settings()
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)

    # --- Theme, Language, Overlays ---

    def apply_theme_settings(self) -> None:
        settings = self.manager.settings.normalized()
        set_language(settings.language)
        has_bg = bool(settings.custom_bg_path and os.path.isfile(settings.custom_bg_path))
        set_active_theme(
            mode=settings.theme_mode,
            has_custom_bg=has_bg,
            bg_dim=settings.custom_bg_dim,
        )
        self.container.set_background(
            bg_path=settings.custom_bg_path,
            dim_percent=settings.custom_bg_dim,
            theme_mode=settings.theme_mode,
        )
        app = QApplication.instance()
        if app is not None:
            app.setStyleSheet(get_stylesheet())
        self.setStyleSheet(get_stylesheet())

        self.sorting_view.canvas.set_transparent_backdrop(has_bg)
        self.sorting_view.canvas.set_video_autoplay(settings.video_autoplay)
        self.sorting_view.canvas.set_video_seek_seconds(settings.video_seek_seconds)

        self.sidebar.refresh_theme()
        self.setup_view.refresh_theme()
        self.sorting_view.refresh_theme()
        self.complete_view.refresh_theme()
        for ov in self._overlay_stack:
            if hasattr(ov, "refresh_theme"):
                ov.refresh_theme()

    def refresh_texts(self) -> None:
        self.sidebar.refresh_texts()
        self.setup_view.refresh_texts()
        self.sorting_view.refresh_texts()
        self.complete_view.refresh_texts()
        self._render_active_view(force_reload=False)

    def _open_overlay(self, overlay: _InWindowOverlayBase) -> None:
        self._reset_space_state()
        if overlay.parentWidget() is not self.container:
            overlay.setParent(self.container)
        self._overlay_stack.append(overlay)

        def _cleanup() -> None:
            if overlay in self._overlay_stack:
                self._overlay_stack.remove(overlay)
            overlay.deleteLater()

        overlay.closed.connect(_cleanup)
        overlay.open_overlay()

    def active_overlay(self) -> Optional[_InWindowOverlayBase]:
        return self._overlay_stack[-1] if self._overlay_stack else None

    def open_settings(self) -> SettingsOverlay:
        ov = SettingsOverlay(self.manager.settings, parent=self.container)
        ov.language_preview_requested.connect(lambda _lang: self.refresh_texts())
        ov.settings_saved.connect(self._on_settings_saved)
        self._open_overlay(ov)
        return ov

    def _on_settings_saved(self, updated: object) -> None:
        from core.session_manager import AppSettings

        if not isinstance(updated, AppSettings):
            return
        old_trash = self.manager.settings.trash_category.name
        self.manager.update_settings(
            language=updated.language,
            theme_mode=updated.theme_mode,
            custom_bg_path=updated.custom_bg_path,
            custom_bg_dim=updated.custom_bg_dim,
            video_autoplay=updated.video_autoplay,
            video_seek_seconds=updated.video_seek_seconds,
            video_arrow_mode=updated.video_arrow_mode,
            keybind_profile=updated.keybind_profile,
            custom_keybinds=updated.custom_keybinds,
            custom_keybind_profiles=updated.custom_keybind_profiles,
            trash_category=updated.trash_category,
            default_categories=updated.default_categories,
            visible_metadata_fields=updated.visible_metadata_fields,
        )
        # Re-sync active session metadata cleanly if trash folder name changed
        sess = self.manager.get_active_session()
        if sess is not None and updated.trash_category.name != old_trash:
            self.manager.sync_session(sess.id)
        self.apply_theme_settings()
        self.setup_view.apply_settings(self.manager.settings)
        self.refresh_texts()
        self._refresh_sidebar()

    def _on_autoplay_toggled(self, enabled: bool) -> None:
        self.manager.update_settings(video_autoplay=bool(enabled))

    # --- Sidebar & Session Selection ---

    def toggle_sidebar(self) -> None:
        self._sidebar_collapsed = not self._sidebar_collapsed
        animate_sidebar_width(
            self._sidebar_anim,
            self.sidebar,
            expand=not self._sidebar_collapsed,
            target_width=SessionSidebar.TARGET_WIDTH,
            duration_ms=180,
        )
        self.setup_view.set_sidebar_collapsed(self._sidebar_collapsed)
        self.sorting_view.set_sidebar_collapsed(self._sidebar_collapsed)
        self.complete_view.set_sidebar_collapsed(self._sidebar_collapsed)

    def toggle_gallery(self) -> None:
        new_open = not self.sorting_view.is_gallery_open()
        self.manager.update_settings(gallery_open=new_open)
        self.sorting_view.apply_gallery_preferences(
            new_open, self.manager.settings.gallery_width, animate=True
        )

    def _on_gallery_width_committed(self, width: int) -> None:
        self.manager.update_settings(gallery_width=int(width))

    def _refresh_sidebar(self) -> None:
        self.sidebar.set_sessions(
            self.manager.sessions, self.manager.active_session_id
        )

    def _switch_stacked_view(self, target_widget: QWidget, direction: int = 1) -> None:
        current = self.stacked.currentWidget()
        if current is target_widget:
            return
        self._stack_transition.transition_to(
            lambda: self.stacked.setCurrentWidget(target_widget),
            current,
            target_widget,
            direction=direction,
        )

    def _on_add_session_clicked(self) -> None:
        self.sorting_view.canvas.release_media()
        self.sorting_view.gallery_sidebar.clear_selection(sync_to_active=False)
        self._last_loaded_path = None
        self._last_timeline_index = None
        self._pending_nav_dir = 0
        self.manager.active_session_id = None
        self.manager.save()
        self.setup_view.reset_defaults()
        self._refresh_sidebar()
        self._switch_stacked_view(self.setup_view, direction=-1)

    def _on_session_selected(self, session_id: str) -> None:
        if not session_id:
            return
        self.sorting_view.canvas.release_media()
        self.sorting_view.gallery_sidebar.clear_selection(sync_to_active=False)
        self._last_loaded_path = None
        self._last_timeline_index = None
        self._pending_nav_dir = 0
        self.manager.set_active_session(session_id)
        self.manager.sync_session(session_id)
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)

    def _on_session_started(
        self,
        name: str,
        folder_path: str,
        categories: list,
        deep_search: bool,
        category_meta: dict,
    ) -> None:
        try:
            sess, _existed = self.manager.create_or_get_session(
                folder_path=folder_path,
                name=name,
                categories=categories,
                deep_search=deep_search,
                category_meta=category_meta,
            )
        except Exception as exc:
            self.setup_view.error_lbl.setText(str(exc))
            self.setup_view.error_lbl.show()
            return
        self._last_loaded_path = None
        self._last_timeline_index = None
        self._pending_nav_dir = 0
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)
        assert self.manager.active_session_id == sess.id

    def _on_session_delete_requested(self, session_id: str) -> None:
        sess = self.manager.get_session(session_id)
        if sess is None:
            return
        ov = _ConfirmOverlay(
            tr("sidebar.delete_title"),
            tr("sidebar.delete_body", name=sess.name),
            danger=True,
            parent=self.container,
        )
        ov.confirmed.connect(lambda sid=session_id: self._confirm_delete_session(sid))
        self._open_overlay(ov)

    def _confirm_delete_session(self, session_id: str) -> None:
        if self.manager.active_session_id == session_id:
            self.sorting_view.canvas.release_media()
            self._last_loaded_path = None
        self.manager.delete_session(session_id)
        if self.manager.active_session_id is None:
            self.setup_view.reset_defaults()
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)

    # --- Active View Rendering ---

    def _current_entry_and_path(
        self, sess: SessionState
    ) -> tuple[Optional[TimelineEntry], Optional[Path]]:
        if not sess.timeline or self.manager.is_session_complete(sess.id):
            return None, None
        idx = max(0, min(sess.current_index, len(sess.timeline) - 1))
        entry = sess.timeline[idx]
        return entry, Path(sess.folder_path) / entry.rel_path

    def _render_active_view(
        self,
        force_reload: bool = False,
        gallery_moved_index: Optional[int] = None,
        categories_changed: bool = False,
    ) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            self.sorting_view.canvas.release_media()
            self.sorting_view.gallery_sidebar.clear_selection(sync_to_active=False)
            self._last_loaded_path = None
            self._last_timeline_index = None
            self._pending_nav_dir = 0
            self._switch_stacked_view(self.setup_view, direction=-1)
            return

        settings = self.manager.settings.normalized()
        trash_name = settings.trash_category.name
        counts = self.manager.get_category_counts(sess.id)

        if self.manager.is_session_complete(sess.id):
            self.sorting_view.canvas.release_media()
            self.sorting_view.gallery_sidebar.clear_selection(sync_to_active=False)
            self._last_loaded_path = None
            self._pending_nav_dir = 0
            self.complete_view.set_summary(sess, counts, trash_name=trash_name)
            self._switch_stacked_view(self.complete_view, direction=1)
            return

        self._switch_stacked_view(
            self.sorting_view, direction=-1 if self._pending_nav_dir < 0 else 1
        )
        entry, media_path = self._current_entry_and_path(sess)

        total = len(sess.timeline)
        human_idx = (sess.current_index + 1) if total > 0 else 0
        self.sorting_view.counter_pill.set_progress(human_idx, total)
        self.sorting_view.set_sort_mode(sess.sort_mode)

        keybinds = self.manager.get_effective_keybinds(sess.id)
        colors = self.manager.get_effective_category_colors(sess.id)
        active_cat = entry.category if (entry and entry.status == "sorted") else None
        self.sorting_view.update_categories(
            sess.categories,
            counts,
            keybinds,
            colors,
            trash_name=trash_name,
            trash_key=settings.trash_category.key,
            trash_color=settings.trash_category.color,
            active_category=active_cat,
        )

        self.sorting_view.set_navigation_state(
            can_prev=(sess.current_index > 0),
            can_skip=(sess.current_index < total - 1),
            can_undo=bool(sess.undo_stack),
        )

        self.sorting_view.filmstrip.update_items(
            sess.timeline,
            sess.current_index,
            root_folder=sess.folder_path,
            trash_name=trash_name,
            category_colors=colors,
            skipped_files=sess.skipped_files,
        )

        gallery = self.sorting_view.gallery_sidebar
        if (
            gallery_moved_index is not None
            and not categories_changed
            and gallery._root_folder == sess.folder_path
            and 0 <= gallery_moved_index < len(sess.timeline)
            and len(gallery._timeline) == total
        ):
            gallery.update_single_item(
                gallery_moved_index,
                sess.timeline[gallery_moved_index],
                sess.current_index,
                skipped_files=sess.skipped_files,
            )
        else:
            gallery.set_session_state(
                sess.timeline,
                sess.current_index,
                sess.folder_path,
                sess.categories,
                colors,
                trash_name=trash_name,
                trash_color=settings.trash_category.color,
                skipped_files=sess.skipped_files,
            )
            gallery.ensure_visible(sess.current_index)

        path_str = str(media_path) if media_path else None
        if entry is None or media_path is None:
            self.sorting_view.set_filename("")
            self._current_meta = {}
            self.sorting_view.set_metadata({}, settings.visible_metadata_fields)
            self.sorting_view.canvas.set_photo(None)
            self._last_loaded_path = None
            return

        self.sorting_view.set_filename(entry.name)
        self._current_meta = get_detailed_media_metadata(media_path)
        self.sorting_view.set_metadata(
            self._current_meta, settings.visible_metadata_fields
        )

        if force_reload or path_str != self._last_loaded_path:
            direction = self._pending_nav_dir
            if (
                direction == 0
                and self._last_timeline_index is not None
                and sess.current_index != self._last_timeline_index
            ):
                direction = 1 if sess.current_index > self._last_timeline_index else -1
            self._pending_nav_dir = 0
            self._last_timeline_index = sess.current_index
            preloads = self._neighbor_paths(sess)
            self.sorting_view.canvas.set_photo(
                media_path, preload_paths=preloads, direction=direction
            )
            self._last_loaded_path = path_str
        else:
            self._pending_nav_dir = 0
            self._last_timeline_index = sess.current_index

    def _neighbor_paths(self, sess: SessionState) -> List[Path]:
        out: List[Path] = []
        total = len(sess.timeline)
        for delta in (1, 2, -1):
            idx = sess.current_index + delta
            if 0 <= idx < total:
                e = sess.timeline[idx]
                p = Path(sess.folder_path) / e.rel_path
                if p.exists():
                    out.append(p)
        return out

    def _on_video_metadata_ready(self, w: int, h: int, dur_ms: int) -> None:
        if not self._current_meta:
            return
        changed = False
        if w > 0 and h > 0 and not self._current_meta.get("dimensions"):
            self._current_meta["dimensions"] = f"{w} × {h} px"
            changed = True
        if dur_ms > 0 and not self._current_meta.get("duration"):
            secs = max(0, dur_ms // 1000)
            self._current_meta["duration"] = f"{secs // 60:02d}:{secs % 60:02d}"
            changed = True
        if changed:
            self.sorting_view.set_metadata(
                self._current_meta, self.manager.settings.visible_metadata_fields
            )

    def _on_metadata_clicked(self) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        entry, media_path = self._current_entry_and_path(sess)
        if media_path is None:
            return
        meta = get_detailed_media_metadata(media_path)
        if not meta.get("dimensions") and self._current_meta.get("dimensions"):
            meta["dimensions"] = self._current_meta["dimensions"]
        if not meta.get("duration") and self._current_meta.get("duration"):
            meta["duration"] = self._current_meta["duration"]
        ov = FullMetadataOverlay(meta, parent=self.container)
        self._open_overlay(ov)

    # --- Sorting, Pure Navigation & Undo ---

    def _on_prev_photo(self) -> None:
        sess = self.manager.get_active_session()
        if sess is None or self.manager.is_session_complete(sess.id) or sess.current_index <= 0:
            return
        self._pending_nav_dir = -1
        self.sorting_view.canvas.release_media()
        self.manager.prev_photo(sess.id)
        self.sorting_view.gallery_sidebar.select_single(sess.current_index)
        self.sorting_view.canvas.show_action_hud(
            tr("hud.prev"), icon_name="chevron_left", tone="neutral"
        )
        self._render_active_view(force_reload=True)

    def _on_skip_photo(self) -> None:
        sess = self.manager.get_active_session()
        if (
            sess is None
            or self.manager.is_session_complete(sess.id)
            or sess.current_index >= len(sess.timeline) - 1
        ):
            return
        self._pending_nav_dir = 1
        self.sorting_view.canvas.release_media()
        self.manager.skip_photo(sess.id)
        self.sorting_view.gallery_sidebar.select_single(sess.current_index)
        self.sorting_view.canvas.show_action_hud(
            tr("hud.skipped"), icon_name="chevron_right", tone="neutral"
        )
        self._render_active_view(force_reload=True)

    def _on_jump_to(self, index: int) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        if index > sess.current_index:
            self._pending_nav_dir = 1
        elif index < sess.current_index:
            self._pending_nav_dir = -1
        else:
            self._pending_nav_dir = 0
        self.sorting_view.canvas.release_media()
        self.manager.jump_to(sess.id, index)
        self.sorting_view.gallery_sidebar.select_single(sess.current_index)
        self._render_active_view(force_reload=True)

    def _on_gallery_preview(self, index: int) -> None:
        sess = self.manager.get_active_session()
        if sess is None or self.manager.is_session_complete(sess.id):
            return
        if index > sess.current_index:
            self._pending_nav_dir = 1
        elif index < sess.current_index:
            self._pending_nav_dir = -1
        else:
            self._pending_nav_dir = 0
        self.sorting_view.canvas.release_media()
        self.manager.jump_to(sess.id, index)
        self._render_active_view(force_reload=True)

    def _on_sort_changed(self, mode: str) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        self._pending_nav_dir = 0
        self.sorting_view.canvas.release_media()
        self.manager.set_sort_mode(sess.id, mode)
        self.sorting_view.gallery_sidebar.select_single(sess.current_index)
        self._render_active_view(force_reload=True)

    def _on_move_to_category(self, category: str) -> None:
        sess = self.manager.get_active_session()
        if sess is None or self.manager.is_session_complete(sess.id):
            return
        if (
            self.sorting_view.is_gallery_open()
            and self.sorting_view.gallery_sidebar.has_multi_selection()
        ):
            targets = self.sorting_view.gallery_sidebar.target_indices_for_action()
            self._on_bulk_move_to_category(targets, category)
            return

        entry, media_path = self._current_entry_and_path(sess)
        if entry is None:
            return
        moved_idx = sess.current_index
        old_cat_count = len(sess.categories)
        self._pending_nav_dir = 1
        btn = self.sorting_view.keycap_for(
            category, trash_name=self.manager.settings.trash_category.name
        )
        if hasattr(btn, "trigger_pulse"):
            btn.trigger_pulse()  # type: ignore[union-attr]
        self.sorting_view.canvas.release_media()
        try:
            action = self.manager.move_current_photo(sess.id, category)
        except Exception as exc:
            self._pending_nav_dir = 0
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.move_failed"), str(exc), danger=True, parent=self.container
                )
            )
            return
        if action is not None:
            if media_path is not None:
                self.sorting_view.canvas.rekey_cache(media_path, Path(action.dest_path))
            is_trash = category == self.manager.settings.trash_category.name
            self.sorting_view.canvas.show_action_hud(
                category,
                icon_name="trash" if is_trash else "check",
                tone="danger" if is_trash else "accent",
            )
        self._refresh_sidebar()
        self._render_active_view(
            force_reload=True,
            gallery_moved_index=moved_idx,
            categories_changed=(len(sess.categories) != old_cat_count),
        )

    def _on_bulk_move_to_category(self, indices: List[int], category: str) -> None:
        sess = self.manager.get_active_session()
        if sess is None or not indices:
            return
        self._pending_nav_dir = 1
        btn = self.sorting_view.keycap_for(
            category, trash_name=self.manager.settings.trash_category.name
        )
        if hasattr(btn, "trigger_pulse"):
            btn.trigger_pulse()  # type: ignore[union-attr]
        self.sorting_view.canvas.release_media()
        moved_actions, err = self.manager.bulk_move_photos(sess.id, indices, category)
        for act in moved_actions:
            self.sorting_view.canvas.rekey_cache(Path(act.src_path), Path(act.dest_path))

        if err is not None:
            self._pending_nav_dir = 0
            moved_prev = [a.prev_index for a in moved_actions]
            self.sorting_view.gallery_sidebar.prune_moved_indices(moved_prev)
            self._refresh_sidebar()
            self._render_active_view(force_reload=True)
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.move_failed"), str(err), danger=True, parent=self.container
                )
            )
            return

        self.sorting_view.gallery_sidebar.clear_selection(sync_to_active=True)
        if moved_actions:
            is_trash = category == self.manager.settings.trash_category.name
            label = (
                tr("hud.moved_batch", count=len(moved_actions), category=category)
                if len(moved_actions) > 1
                else category
            )
            self.sorting_view.canvas.show_action_hud(
                label,
                icon_name="trash" if is_trash else "check",
                tone="danger" if is_trash else "accent",
            )
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)

    def _on_trash(self) -> None:
        self._on_move_to_category(self.manager.settings.trash_category.name)

    def _on_undo(self) -> None:
        sess = self.manager.get_active_session()
        if sess is None or not sess.undo_stack:
            return
        self._pending_nav_dir = -1
        self.sorting_view.canvas.release_media()
        try:
            restored = self.manager.undo_last_batch(sess.id)
        except Exception as exc:
            self._pending_nav_dir = 0
            self.sorting_view.gallery_sidebar.clear_selection(sync_to_active=True)
            self._refresh_sidebar()
            self._render_active_view(force_reload=True)
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.undo_failed"), str(exc), danger=True, parent=self.container
                )
            )
            return
        self.sorting_view.gallery_sidebar.select_single(sess.current_index)
        if restored:
            label = (
                tr("hud.undone_batch", count=len(restored))
                if len(restored) > 1
                else tr("hud.undone")
            )
            self.sorting_view.canvas.show_action_hud(
                label, icon_name="undo", tone="neutral"
            )
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)

    # --- Dynamic Categories Management (In-Window Overlays) ---

    def _on_new_category_clicked(self) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        ov = _TextPromptOverlay(
            tr("sorting.dialog.new_cat_title"),
            tr("sorting.dialog.new_cat_label"),
            placeholder=tr("sorting.dialog.new_cat_placeholder"),
            parent=self.container,
        )
        ov.submitted.connect(lambda name, sid=sess.id: self._apply_new_category(sid, name))
        self._open_overlay(ov)

    def _apply_new_category(self, session_id: str, raw_name: str) -> None:
        try:
            self.manager.add_category(session_id, raw_name)
        except Exception as exc:
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.invalid_category"), str(exc), danger=True, parent=self.container
                )
            )
            return
        self._render_active_view(force_reload=False)

    def _on_category_rename_requested(self, old_name: str) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        ov = _TextPromptOverlay(
            tr("sorting.dialog.rename_title"),
            tr("sorting.dialog.rename_label", name=old_name),
            initial_text=old_name,
            parent=self.container,
        )
        ov.submitted.connect(
            lambda new_name, sid=sess.id, o=old_name: self._apply_rename_category(
                sid, o, new_name
            )
        )
        self._open_overlay(ov)

    def _apply_rename_category(self, session_id: str, old_name: str, new_name: str) -> None:
        self.sorting_view.canvas.release_media()
        try:
            self.manager.rename_category(session_id, old_name, new_name)
        except Exception as exc:
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.rename_failed"), str(exc), danger=True, parent=self.container
                )
            )
            return
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)

    def _on_category_keybind_requested(self, category: str) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        keybinds = self.manager.get_effective_keybinds(sess.id)
        keybinds_with_trash = dict(keybinds)
        keybinds_with_trash[self.manager.settings.trash_category.name] = (
            self.manager.settings.trash_category.key
        )
        ov = KeyCaptureOverlay(
            category,
            existing_map=keybinds_with_trash,
            ignore_category=category,
            allow_del=False,
            parent=self.container,
        )
        ov.key_captured.connect(
            lambda tok, sid=sess.id, c=category: self._apply_category_keybind(sid, c, tok)
        )
        self._open_overlay(ov)

    def _apply_category_keybind(self, session_id: str, category: str, token: str) -> None:
        try:
            self.manager.set_category_keybind(session_id, category, token)
        except Exception as exc:
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.keybind_invalid"), str(exc), danger=True, parent=self.container
                )
            )
            return
        self._render_active_view(force_reload=False)

    def _on_category_color_requested(self, category: str) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        colors = self.manager.get_effective_category_colors(sess.id)
        cur = colors.get(category, DEFAULT_CATEGORY_COLOR)
        ov = _ColorPickerOverlay(
            tr("sorting.dialog.color_title", name=category),
            cur,
            parent=self.container,
        )
        ov.color_selected.connect(
            lambda hx, sid=sess.id, c=category: self._apply_category_color(sid, c, hx)
        )
        self._open_overlay(ov)

    def _apply_category_color(self, session_id: str, category: str, hex_c: str) -> None:
        try:
            self.manager.set_category_color(session_id, category, hex_c)
        except Exception as exc:
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.color_invalid"), str(exc), danger=True, parent=self.container
                )
            )
            return
        self._render_active_view(force_reload=False)

    def _on_category_revert_requested(self, category: str) -> None:
        sess = self.manager.get_active_session()
        if sess is None:
            return
        cnt = self.manager.get_category_counts(sess.id).get(category, 0)
        ov = _ConfirmOverlay(
            tr("sorting.dialog.revert_title"),
            tr("sorting.dialog.revert_body", count=cnt, name=category),
            danger=True,
            parent=self.container,
        )
        ov.confirmed.connect(lambda sid=sess.id, c=category: self._apply_revert_category(sid, c))
        self._open_overlay(ov)

    def _apply_revert_category(self, session_id: str, category: str) -> None:
        self.sorting_view.canvas.release_media()
        try:
            self.manager.revert_and_remove_category(session_id, category)
        except Exception as exc:
            self._open_overlay(
                _ConfirmOverlay(
                    tr("err.revert_failed"), str(exc), danger=True, parent=self.container
                )
            )
            return
        self._refresh_sidebar()
        self._render_active_view(force_reload=True)

    def _on_open_folder(self) -> None:
        sess = self.manager.get_active_session()
        if sess is not None and os.path.isdir(sess.folder_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(sess.folder_path))

    # --- Keyboard Shortcuts & Continuous Space+Arrow Video Navigation ---

    def _reset_space_state(self) -> None:
        self._space_held = False
        self._space_combo_used = False

    def _is_shortcut_blocked(self) -> bool:
        if not self.isVisible():
            return True
        if any(ov.isVisible() for ov in self._overlay_stack):
            return True
        if QApplication.activePopupWidget() is not None:
            return True
        if self.sorting_view.counter_pill.is_editing():
            return True
        fw = QApplication.focusWidget()
        if isinstance(fw, (QLineEdit, QSpinBox, QTextEdit, QPlainTextEdit)):
            return True
        return False

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        for ov in self._overlay_stack:
            ov.sync_bounds()

    def closeEvent(self, event) -> None:  # type: ignore[override]
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        self.sorting_view.canvas.shutdown()
        super().closeEvent(event)

    def eventFilter(self, watched: QObject, event: QEvent) -> bool:  # type: ignore[override]
        if event.type() == QEvent.Type.WindowDeactivate:
            self._reset_space_state()
            return super().eventFilter(watched, event)

        if self._is_shortcut_blocked():
            return super().eventFilter(watched, event)

        if event.type() == QEvent.Type.KeyPress and isinstance(event, QKeyEvent):
            key = event.key()
            mods = event.modifiers()

            if key == Qt.Key.Key_Tab and not (mods & Qt.KeyboardModifier.ControlModifier):
                if not event.isAutoRepeat():
                    self.toggle_sidebar()
                return True

            if (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_Z:
                if not event.isAutoRepeat():
                    self._on_undo()
                return True

            if self.stacked.currentWidget() is not self.sorting_view:
                return super().eventFilter(watched, event)

            if (mods & Qt.KeyboardModifier.ControlModifier) and key == Qt.Key.Key_G:
                if not event.isAutoRepeat():
                    self.toggle_gallery()
                return True

            if key == Qt.Key.Key_Escape:
                if (
                    not event.isAutoRepeat()
                    and self.sorting_view.is_gallery_open()
                    and self.sorting_view.gallery_sidebar.has_multi_selection()
                ):
                    self.sorting_view.gallery_sidebar.clear_selection(sync_to_active=True)
                    return True
                return super().eventFilter(watched, event)

            sess = self.manager.get_active_session()
            if sess is None or self.manager.is_session_complete(sess.id):
                return super().eventFilter(watched, event)

            is_video = self.sorting_view.canvas.is_video_active()
            settings = self.manager.settings.normalized()

            # Space: smart video Play/Pause vs Space+Arrow continuous jump vs Photo skip
            if key == Qt.Key.Key_Space:
                if event.isAutoRepeat():
                    return True
                self._space_held = True
                if not is_video:
                    self._space_combo_used = True
                    self._on_skip_photo()
                else:
                    self._space_combo_used = False
                return True

            # Left / Right Arrow keys (Shift+Arrow range select takes priority over video seek)
            if key in (Qt.Key.Key_Left, Qt.Key.Key_Right):
                if mods & Qt.KeyboardModifier.ShiftModifier:
                    if self.sorting_view.is_gallery_open():
                        delta = -1 if key == Qt.Key.Key_Left else 1
                        target_idx = self.sorting_view.gallery_sidebar.step_range_selection(delta)
                        if target_idx is not None:
                            self._on_gallery_preview(target_idx)
                        return True
                    if key == Qt.Key.Key_Right:
                        self._on_skip_photo()
                    else:
                        self._on_prev_photo()
                    return True

                if self._space_held:
                    self._space_combo_used = True
                    if key == Qt.Key.Key_Right:
                        self._on_skip_photo()
                    else:
                        self._on_prev_photo()
                    return True

                if is_video:
                    if settings.video_arrow_mode == "skip":
                        if key == Qt.Key.Key_Right:
                            self._on_skip_photo()
                        else:
                            self._on_prev_photo()
                        return True
                    # Seek mode: if video has ended, Right Arrow immediately advances Next
                    if key == Qt.Key.Key_Right and self.sorting_view.canvas.is_video_finished():
                        self._on_skip_photo()
                        return True
                    step_ms = settings.video_seek_seconds * 1000
                    self.sorting_view.canvas.seek_video_relative(
                        step_ms if key == Qt.Key.Key_Right else -step_ms
                    )
                    return True
                else:
                    if key == Qt.Key.Key_Right:
                        self._on_skip_photo()
                    else:
                        self._on_prev_photo()
                    return True

            if key == Qt.Key.Key_Backspace:
                if not event.isAutoRepeat():
                    self._on_prev_photo()
                return True

            # Category & Trash single-key shortcut dispatch
            if event.isAutoRepeat():
                return super().eventFilter(watched, event)

            tok = _key_event_to_token(event)
            if tok is not None:
                trash_key = (settings.trash_category.key or "DEL").upper()
                if tok == trash_key or (key == Qt.Key.Key_Delete and trash_key == "DEL"):
                    self._on_trash()
                    return True
                keybinds = self.manager.get_effective_keybinds(sess.id)
                for cat, bound in keybinds.items():
                    if bound.upper() == tok:
                        self._on_move_to_category(cat)
                        return True

        elif event.type() == QEvent.Type.KeyRelease and isinstance(event, QKeyEvent):
            if event.key() == Qt.Key.Key_Space and not event.isAutoRepeat():
                if self._space_held and not self._space_combo_used:
                    if (
                        self.stacked.currentWidget() is self.sorting_view
                        and self.sorting_view.canvas.is_video_active()
                    ):
                        self.sorting_view.canvas.toggle_video_playback()
                self._reset_space_state()
                return True

        return super().eventFilter(watched, event)


def run_self_check() -> int:
    """Headless self-check validating PhotoBox v0.6.0 startup & workflows."""
    app = QApplication.instance() or QApplication(sys.argv)
    load_bundled_fonts(app)

    splash = SplashScreen()
    for pct, msg in SplashScreen.BOOT_STEPS:
        splash.set_step(pct, msg)
    assert splash.status_lbl.text() == "Ready."
    splash.close()

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        photos = root / "album"
        photos.mkdir(parents=True)
        for i in range(3):
            p = photos / f"shot_{i+1}.jpg"
            pix = QPixmap(64, 48)
            pix.fill(QColor("#F59E0B"))
            pix.save(str(p), "JPG")

        win = MainWindow(storage_path=root / "sessions.json")
        win.show()
        QApplication.processEvents()

        # Verify Settings button & Setup defaults
        assert win.setup_view.settings_btn.isVisible()
        assert win.setup_view.trash_chip is not None

        # Create session & verify pure navigation vs Ctrl+Z undo
        sess, _ = win.manager.create_or_get_session(
            str(photos), name="SelfCheck", categories=["Selects"], deep_search=False
        )
        win._on_session_selected(sess.id)
        QApplication.processEvents()

        assert win.stacked.currentWidget() is win.sorting_view
        assert win.sorting_view._trash_button is not None
        assert win.sorting_view.sort_by_lbl.text() == "Sort by :"

        # Move item 0 -> Selects
        win._on_move_to_category("Selects")
        assert (photos / "Selects" / "shot_1.jpg").is_file()
        assert win.manager.get_active_session().current_index == 1

        # Prev back to 0 -> stays sorted in Selects (pure navigation)
        win._on_prev_photo()
        assert win.manager.get_active_session().current_index == 0
        assert (photos / "Selects" / "shot_1.jpg").is_file()

        # Undo via undo_last_action restores to root
        win._on_undo()
        assert (photos / "shot_1.jpg").is_file()

        # Open & close in-window SettingsOverlay
        ov = win.open_settings()
        assert ov.isVisible()
        assert ov.pages.count() == 7
        ov.close_overlay()

        win.close()
    print(f"PhotoBox v{APP_VERSION} self-check OK")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=f"PhotoBox v{APP_VERSION}")
    parser.add_argument("--self-check", action="store_true", help="Run headless verification")
    args = parser.parse_args()

    if args.self_check:
        return run_self_check()

    app = QApplication(sys.argv)
    load_bundled_fonts(app)
    app_icon = resolve_asset_path("assets/icons/photobox.ico")
    if app_icon.exists():
        app.setWindowIcon(QIcon(str(app_icon)))

    splash = SplashScreen()
    splash.show()
    app.processEvents()
    for pct, msg in SplashScreen.BOOT_STEPS[:-1]:
        splash.set_step(pct, msg)
        app.processEvents()

    win = MainWindow()
    splash.set_step(SplashScreen.BOOT_STEPS[-1][0], SplashScreen.BOOT_STEPS[-1][1])
    app.processEvents()
    win.show()
    splash.finish_timer = QTimer()
    splash.close()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
