# Changelog

All notable changes to **PhotoBox** are documented in this file. See [VERSION.md](VERSION.md) for the compact monotonic version ledger.

## [0.6.0] — 2026-11-17
### Added
- **PhotoBox Brand Identity & Icons:** Added vector Optical Aperture Box emblem (`draw_brand_logo` / `render_brand_logo`), bundled multi-resolution `assets/icons/photobox.ico` (`16–256px`) and `assets/icons/photobox.png`, and wired them into the Left Sidebar, Splash Screen, About page, Window icon, and `dist/PhotoBox.exe`.
- **Resizable Right Session Gallery (`SessionGallerySidebar`):** Viewport-aware scrollable media grid on the right side of the Sorting view with draggable left resize edge (`180px` up to dynamic window clamp), responsive column reflow (`1–4+` columns), and `[All]` / `[Unsorted]` / `[Sorted]` filter pills.
- **Bulk Selection & Batch Undo:** Support for `Shift + Click`, `Ctrl + Click`, and keyboard `Shift + Left/Right Arrow` (visible-only range selection), context-menu move actions, `bulk_move_photos` with partial-failure resilience, and single-press Batch Undo via `MoveAction.batch_id` (`Ctrl+Z`).
- **Sort by File Size:** Added `File Size (Largest)` (`size_desc`) and `File Size (Smallest)` (`size_asc`) modes.
- **Gallery Toggle Shortcut & Persistence:** Added top-right header toggle + `Ctrl+G` shortcut, persisted `gallery_open` and `gallery_width` settings.

### Changed
- **Refined 7-Menu Settings UI:** Redesigned Settings using Linear/macOS-inspired `_SettingGroupCard`, `_SettingRow`, and animated `_ToggleSwitch` controls, added top-right close button (`×`) and footer divider, and fixed double-border rendering on `_SettingsNavPanel`.
- **Sort Label Cleanup:** Renamed `Filename (A-Z Natural)` to `Filename (A-Z)` (`Nama File (A-Z)` in Indonesian).

## [0.5.0] — 2026-11-17
### Added
- Cohesive motion choreography (`WidgetSlideFadeTransition`, `ease_overshoot`, `lerp_color`) with `0ms` keyboard input blocking and reduced-motion support across stacked views, filmstrip hover/press/fade-in, directional photo transitions, `_VideoControlBar`, 3D keycaps, overlays, and Settings nav pills.

## [0.4.0] — 2026-11-16
### Added
- **Optical Instrument Redesign:** Floating Top HUD Bar, Compact 5-Slot Filmstrip Tray, and Floating 3D Keycap Console Deck.
- **Pure Navigation vs Undo:** `Prev`/`Next` move cleanly without un-sorting files; assigning another category moves directly; `Ctrl+Z` restores actions non-destructively.
- **Viewfinder Metadata Lookup:** Inline header EXIF capsule, quick hover readout layer, and full centered metadata popup with `Copy All`.
- **Bilingual Localization:** Full English (default) and Bahasa Indonesia (`id`) UI switching live in Settings.
- **In-Window 7-Menu Settings & Custom Keybind Profiles:** Named 9-slot keybind profiles, custom Trash category name/key/color, video seek seconds (`1–60s`), and arrow seek vs skip toggle.

## [0.3.0] — 2026-11-16
### Added
- Bundled `Poppins` font family, video playback & scrub transport bar, dynamic auto-expanding Setup category boxes, conditional `Deep Search`, Black/White themes with custom dimmable background, and clickable jump counter (`CounterPill`).

## [0.2.0] — 2026-11-16
### Added
- C++ `QImageReader` `< 15ms` EXIF-oriented decode + background preloader, vector `QPainter` icons, cubic-bezier motion, and Fixed Session Timeline `1..N`.

## [0.1.0] — 2026-11-16
### Added
- Initial multi-session desktop photo sorter release.
