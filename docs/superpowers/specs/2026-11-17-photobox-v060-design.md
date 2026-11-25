# PhotoBox v0.6.0 — Design Specification

**Date:** 2026-11-17  
**Workspace:** `c:\Users\Muhamad Rafif I\Documents\antigravity\Photo Sorting`  
**Target Version:** `0.6.0` (from `0.5.0`)  
**Scope:** Multi-surface UX & Visual Craft Upgrade (Refined Settings UI, PhotoBox Brand Logo + Window/`.exe` Icon, Resizable Right Sidebar Gallery with `Shift+Click` / `Shift+Arrow` Range Selection & Batch Categorize/Undo, plus Sort-by-Size).

---

## 1. Intent & Jobs to Be Done
- **Refine Settings UI:** Elevate the 7-menu `SettingsOverlay` from ungrouped raw widgets into tactile Linear/macOS-style Grouped Setting Cards with clean visual hierarchy, crisp alignment, hairline dividers, pill toggle switches, and zero double-border noise on the left nav pill.
- **Brand Identity & Logo:** Give **PhotoBox** a memorable, optical-precision brand mark (Rounded Square Dark Badge + Geometric Box + Amber `#F59E0B` Optical Lens Ring) rendered consistently across the Left Session Sidebar header, Splash Screen, About page, Desktop Window titlebar/taskbar, and bundled `dist\PhotoBox.exe`.
- **High-Speed Gallery Overview & Bulk Triage:** Add a collapsible, draggable-width Right Sidebar Gallery showing all items in the active session as a scrollable responsive multi-column grid (1, 2, 3, 4+ columns depending on dragged width), supporting `Shift+Click`, `Ctrl+Click`, and `Shift + Left/Right Arrow` multi-selection to bulk-move photos/videos to any category or trash with single-step Batch Undo (`Ctrl+Z`).
- **Sort Improvements:** Add symmetric File Size sorting (`Largest` & `Smallest`) and remove `"Natural"` from the `Filename (A-Z)` sort label.

---

## 2. Approaches Considered & Selected Direction
- **Approach A (Selected):** Custom viewport-aware scrollable thumbnail grid (`SessionGallerySidebar` reusing `QThreadPool` + `QImageReader`/FFmpeg thumbnail cache pattern) inside a resizable horizontal splitter/drawer paired with Grouped Setting Cards (`_SettingGroupCard`, `_SettingRow`, `_ToggleSwitch`), vector logo rendering in `ui/icons.py` + generated multi-resolution `assets/icons/photobox.ico` / `photobox.png`, and native `batch_id` batch moves/undo in `core/session_manager.py`.
  - *Why others don't fit:* Standard `QListWidget` (`IconMode`) struggles with live dynamic column reflow on drag-resize, custom painted category dots/keycap feedback, video badges, and flicker-free in-place updates on bulk categorize. Approach A reuses PhotoBox's existing C++ `QImageReader` + `ValueAnimator` primitives with zero heavy dependencies.

---

## 3. Detailed Architecture & Component Design

### 3.1. PhotoBox Logo & Application Icon
- **Visual Motif:** Rounded square Matte Carbon badge (`#141519` fill, subtle `#2C303A` bevel hairline) framing a geometric Optical Aperture Ring in Optical Amber (`#F59E0B`) nested inside an isometric/framed viewfinder box. High contrast and legible from `16×16` taskbar icons up to `128×128` splash/About emblems.
- **Runtime & Static Assets:**
  - Add `draw_brand_logo(painter, rect, dark_mode=...)` and `render_brand_logo(size, device_pixel_ratio=1.0) -> QPixmap` in `ui/icons.py`.
  - Generate multi-size `assets/icons/photobox.ico` (`16, 24, 32, 48, 64, 128, 256 px`) and `assets/icons/photobox.png` (`256×256`) using Qt `QPainter` + Pillow so `main.py:152` (`resolve_asset_path("assets/icons/photobox.ico")`) loads cleanly in both dev and frozen `.exe`.
  - Update `build_exe.ps1` PyInstaller command to include `--icon "assets\icons\photobox.ico"` and `--add-data "assets\icons;assets\icons"`.
- **UI Placement:**
  1. `SessionSidebar` header (`ui/views.py`): `[Logo 22×22]` beside `PHOTOBOX` wordmark (replacing text-only header).
  2. `SplashScreen` (`ui/views.py`): `[Logo 34×34]` next to `PHOTOBOX` + version badge (replacing generic camera icon).
  3. `SettingsOverlay` About page (`ui/views.py`): Framed Brand Hero Card featuring `[Logo 44×44]`, `PHOTOBOX v0.6.0`, subtitle, `Brought to you by Raincl-oud`, and dynamic accent GitHub link.

### 3.2. Refined 7-Menu `SettingsOverlay` Look
- **Modal Frame & Header (`ui/views.py`, `ui/theme.py`):**
  - Clean `×` close button aligned at top-right of the content pane.
  - Hairline `1px` (`PALETTE["border_subtle"]`) divider above the right-pane footer (`Cancel` / `Save Settings`).
  - Fix `_SettingsNavPanel` double-border artifact: set `QPushButton#SettingsNavButton[active="true"]` background/border in QSS to `transparent` so `_SettingsNavPanel.paintEvent` alone renders the sliding amber pill (`rgba(245,158,11,0.11)` + 3px left bar + crisp `PALETTE["accent"]` border).
  - Add subtle `1px` inset dividers / generous breathing room (`20–24px` padding) across pages.
- **Reusable Settings Layout Primitives (`ui/views.py`):**
  - `_SettingGroupCard(QFrame)`: Rounded card (`bg_elevated`/`bg_card`, `1px solid border`, `radius: 10px`) stacking `_SettingRow` entries separated by `1px` `border_subtle` hairlines.
  - `_SettingRow(QWidget)`: Structured horizontal setting row with Primary Title (`13px DemiBold`) + optional Muted Description (`11px`) on the left, and the interactive control aligned cleanly on the right.
  - `_ToggleSwitch(QCheckBox)`: Subclass of `QCheckBox` preserving full `.isChecked()` / `.setChecked()` / `.toggled` API compatibility while custom-painting a smooth animated macOS/Linear pill switch (`38×22px`, knob gliding smoothly over `120ms ease_out()`).
- **Refined Page Organization (preserving all existing attributes & controls):**
  - **General:** Language switcher (`English` / `Bahasa Indonesia`) inside a `_SettingGroupCard`.
  - **Default Categories:** `_Buang` Trash card (locked badge, custom key/color/name) + ordered category cards with round color pill buttons instead of flat squares.
  - **Keybind Profiles:** Grouped Card 1 (Active Profile toggle `[Default (1–9)]` / `[Custom]` + Saved Custom Profiles selector/delete row) and Card 2 (Custom 9-Slot Editor card with Profile Name, 3×3 keycap slots, Reset Slots 1–9).
  - **Video & Playback:** Autoplay (`_ToggleSwitch` row), Seek Step (`1–60s` slider + spinbox row), Left/Right Arrow Mode (`Seek` / `Skip` row) inside clean `_SettingGroupCard`s.
  - **Metadata Display:** Toggle chip/switch 2-column matrix inside a single `_SettingGroupCard`.
  - **Theme & Background:** Theme Mode (`Black` / `White` segmented row), Custom Background upload/reset row, and conditional Scrim Dimming (`20–90%` slider + mono readout) inside clean `_SettingGroupCard`s.
  - **About:** Brand Identity Card featuring the PhotoBox Logo, version badge, credit, and clickable GitHub link.

### 3.3. Sort by Size + Label Cleanup
- Update `SORT_MODES` in `core/session_manager.py`:
  `("name_asc", "name_desc", "date_asc", "date_desc", "size_desc", "size_asc")`
- Labels in `SORT_MODE_LABELS` & `ui/i18n.py`:
  - `"sort.name_asc"`: `"Filename (A-Z)"` (EN) / `"Nama File (A-Z)"` (ID) — removes `"Natural"` while retaining natural alphanumeric comparator in backend (`natural_sort_key`).
  - `"sort.size_desc"`: `"File Size (Largest)"` (EN) / `"Ukuran File (Terbesar)"` (ID).
  - `"sort.size_asc"`: `"File Size (Smallest)"` (EN) / `"Ukuran File (Terkecil)"` (ID).
- Implement size key via `_safe_size(path)` (`stat().st_size`, tie-breaking by `natural_sort_key(name)` and `rel_path`) across both `_sort_paths` and `_sort_timeline_entries`, preserving current active photo index tracking (`set_sort_mode`).

### 3.4. Right Sidebar Gallery (`SessionGallerySidebar`) & `Shift+Click` Bulk Categorize
- **Placement, Open/Close & Drag-Resize (`ui/gallery_sidebar.py`, `ui/views.py`, `main.py`):**
  - Layout inside `SortingView`: `SortingHeader` spans **full width at the top** (putting the `[Gallery]` toggle button at the far top-right next to Settings). Below `SortingHeader`, `SortingView` splits horizontally into `[Center Stage: PhotoCanvas + FilmstripBar + CategoryBarContainer]` on the left and `SessionGallerySidebar` extending from below `SortingHeader` down to the bottom edge on the right.
  - **Open by default (`gallery_open = True`)** with default width `276px` (`minWidth: 180px`, dynamic max width clamped to `min(640px, container_w - 460px)` so `FilmstripBar`'s `416px` minimum and `PhotoCanvas` never clip).
  - **Toggle Control:** Top-right header button in `SortingHeader` + **`Ctrl+G` keyboard shortcut** (won't conflict with single-key category shortcuts). Animates smoothly (`180ms ease_drawer()`) between collapsed (`0px`) and saved open width; collapsing the gallery clears any active multi-selection back to single focus so hidden items cannot be bulk-moved.
  - **Drag Resize Handle:** Interactive `5px` left-edge resize grip on `SessionGallerySidebar` (`Qt.CursorShape.SizeHorCursor`, amber hover highlight line) allowing live column reflow; width and open/closed state persist in `AppSettings` (`gallery_open`, `gallery_width`).
- **Scrollable Responsive Multi-Column Grid (`_GalleryGridCanvas`):**
  - Clean header: `"SESSION GALLERY"` + item count badge + collapse icon button.
  - 3 Quick Filter Pills: `[All]` · `[Unsorted]` · `[Sorted]`.
  - Dynamic column computation:
    `cols = max(1, (viewport_w - 2 * padding + gap) // (MIN_CARD_W + gap))` (`MIN_CARD_W = 104px`, `gap = 8px`), where each card scales cleanly to fill available row width with `4:3` optical frame proportions (`card_w` stretches evenly across `cols`, zero horizontal scrollbar).
  - **Performance for 1,000+ Photos:** Viewport-gated lazy thumbnail decoding: only items within visible scroll viewport ±2 buffer rows request decode from the `QThreadPool` cache, keeping scroll & resize smooth.
  - **Card Visual States:**
    - Thumbnail centered-cropped inside `7px` rounded frame (`#01` top-left badge; `▶ VID` top-right badge; sorted color dot + bottom strip showing category or trash name).
    - **Active Current Item:** `2px` accent border (`PALETTE["accent"]`).
    - **Multi-Selected Items:** Luminous selection border (`2.5px`), subtle translucent tint overlay, and crisp top-right check badge `[✓]`.
- **Selection & Bulk Categorize Semantics (Visible-Only & Precedence):**
  1. **Single Click:** Selects *only* that clicked item (clearing any multi-selection), updates anchor index, jumps main canvas & filmstrip to that item (`jump_to`).
  2. **`Shift + Click`:** Computes inclusive visual range in the **currently visible filtered list** (`[All]`, `[Unsorted]`, or `[Sorted]`) from `anchor_index` to `clicked_index`, blocks only those visible items, previews the newly clicked item on the main canvas (keeping selection intact), and reveals the bottom **Bulk Action Bar**.
  3. **`Ctrl + Click`:** Toggles the clicked item inside the selected set cleanly (updating anchor).
  4. **Keyboard `Shift + Left Arrow` / `Shift + Right Arrow`:**
     - **When Right Gallery is OPEN:** Queries `SessionGallerySidebar`'s **currently visible filtered index list**, moves `cursor_index` by `±1` within visible items, extends/shrinks the inclusive visible range from `anchor_index`, releases/updates active canvas preview cleanly (taking precedence over `video_arrow_mode == "seek"` so Shift+Arrow works identically across photos and videos), and preserves the multi-selection.
     - **When Right Gallery is CLOSED:** Performs ordinary single-item navigation (`_on_prev_photo()` / `_on_skip_photo()`) with zero invisible multi-selection.
  5. **Clearing Multi-Selection:** Any plain navigation (`Left`, `Right`, `Space`, `Prev`, `Next`, plain 1x gallery click), pressing `Esc` (when no modal overlay is open), clicking `"Clear"` on the Bulk Action Bar, closing the gallery, switching filter tabs cleanly (pruning/resetting to single active item if current view changes), or switching sessions resets multi-selection back to single item.
- **Bulk Action Execution, Partial-Failure Safety & Batch Undo (`core/session_manager.py`, `main.py`):**
  - When `len(selected_indices) >= 2`: Floating **Bulk Action Bar** appears at bottom of `SessionGallerySidebar`: `"N selected · Choose category"` + `[Clear]`. Pressing any category shortcut (`1–9`, custom keybind, `DEL`), clicking any Console Deck button, or right-clicking inside the gallery (`Move N items to > [Categories / Trash]`) invokes `SessionManager.bulk_move_photos(session_id, selected_indices, target_category)`.
  - **`SessionManager.bulk_move_photos(session_id, indices, target_category)`:**
    - Assigns a shared `batch_id = uuid4().hex[:12]` to every `MoveAction` pushed in that batch.
    - Moves items sequentially with `_move_file_safe`; if item *k* raises an error mid-batch, commits already-moved `1..k-1` items cleanly under `batch_id` (preserving disk/state parity), updates counts/timeline, keeps remaining unmoved items highlighted so the user can retry, and surfaces the error dialog.
    - **Next Focus After Batch Move:** Advances `current_index` to the next unsorted (`status != 'sorted'`) item after `max(selected_indices)` (wrapping if earlier unsorted items remain), or transitions to `SessionCompleteView` if all items are now sorted.
  - **`SessionManager.undo_last_action(session_id)` (Batch Undo):**
    - Pops all contiguous tail `MoveAction`s sharing the same `batch_id` and restores them in reverse order in one atomic `Ctrl+Z` / `Undo` press. If any single file in the batch was externally deleted from disk, removes that missing entry and **continues restoring the remaining valid files in that `batch_id`** before reporting missing files, keeping the rest of the batch consistent and focusing the earliest restored item.

---

## 4. Constraints, Non-Goals & Builder Boundaries
- **Invariants to preserve:**
  - Keep `APP_VERSION` synced between `VERSION.md` and `core/session_manager.py` via `bump-version.ps1` (`0.5.0 -> 0.6.0` minor bump).
  - Preserve **0 ms keyboard sorting latency** (`test_move_and_update_speed_benchmark` `< 50 ms` average across synchronous moves). Lazy gallery updates must not slow down single-photo sorting (`O(1)` card update for changed index, not tearing down the entire grid on every single `1-9` keypress!).
  - Maintain bilingual `"en"` (default) and `"id"` parity across all new strings in `ui/i18n.py`.
  - Respect all existing synchronous test contracts (`_AutoExpandingCategoryBox._adjust_input_width`, `SortingView._hide_metadata_hover`, `_InWindowOverlayBase.close_overlay()`, zero `QGraphicsOpacityEffect` on `PhotoCanvas` / `SettingsOverlay.card`).
  - Keep left-to-right `Shift+Click` sorting isolated to visible filtered items so hidden items in another filter tab are never accidentally categorized.
- **Explicit Non-Goals:**
  - No background cloud sync or external icon/UI dependencies (`PyQt6`, `Pillow`, `FFmpeg` remain the full stack).
  - Do not remove the bottom 5-slot `FilmstripBar` (stays centered above Keycap Console Deck as immediate `±2` viewfinder strip).
