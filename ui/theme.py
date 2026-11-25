"""Optical Instrument theme system, fonts, palettes, and QSS generation for PhotoBox v0.4.0."""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Dict

from PyQt6.QtGui import QColor, QFont, QFontDatabase
from PyQt6.QtWidgets import QApplication

FONT_FAMILY = "Poppins"
UI_FONT_FAMILY = FONT_FAMILY
FONT_MONO = "JetBrains Mono, Consolas, Courier New, monospace"
MONO_FONT_FAMILY = "JetBrains Mono"

CATEGORY_COLOR_PRESETS = [
    ("Amber", "#F59E0B"),
    ("Emerald", "#10B981"),
    ("Sky", "#38BDF8"),
    ("Rose", "#F43F5E"),
    ("Violet", "#A78BFA"),
    ("Teal", "#14B8A6"),
    ("Orange", "#FB923C"),
    ("Indigo", "#818CF8"),
]

PALETTE_BLACK: Dict[str, str] = {
    "mode": "black",
    "bg_app": "#08080A",
    "bg_sidebar": "#0C0D10",
    "bg_surface": "#121316",
    "bg_elevated": "#181A1F",
    "bg_card": "#141519",
    "bg_hover": "#1F2229",
    "bg_input": "#0E0F12",
    "bg_keycap": "#17191E",
    "bg_keycap_hover": "#20232B",
    "bg_keycap_lip": "#0B0C0F",
    "bg_canvas": "#08080A",
    "bg_panel": "#121316",
    "bg_active_row": "rgba(245, 158, 11, 0.14)",
    "bg_trash": "rgba(239, 68, 68, 0.12)",
    "bg_trash_hover": "rgba(239, 68, 68, 0.22)",
    "bg_trash_pulse": "rgba(239, 68, 68, 0.34)",
    "bg_trash_well": "#180E10",
    "border": "#272A32",
    "border_subtle": "#1C1E24",
    "border_strong": "#3A3F4B",
    "border_focus": "#F59E0B",
    "border_photo": "#272A32",
    "border_keycap": "#2E323D",
    "border_trash": "rgba(239, 68, 68, 0.50)",
    "text_primary": "#F4F4F5",
    "text_secondary": "#A1A1AA",
    "text_muted": "#71717A",
    "accent": "#F59E0B",
    "accent_hover": "#FBBF24",
    "accent_pressed": "#D97706",
    "accent_text_on_fill": "#09090B",
    "accent_soft": "rgba(245, 158, 11, 0.14)",
    "accent_glow": "rgba(245, 158, 11, 0.36)",
    "danger": "#EF4444",
    "danger_hover": "#F87171",
    "danger_soft": "rgba(239, 68, 68, 0.16)",
    "trash_accent": "#EF4444",
    "success": "#10B981",
    "success_soft": "rgba(16, 185, 129, 0.16)",
    "warning": "#F59E0B",
    "hud_bg": "rgba(18, 19, 22, 0.92)",
    "hud_trash_bg": "rgba(28, 12, 14, 0.94)",
    "hud_border": "rgba(255, 255, 255, 0.10)",
    "overlay_scrim": "rgba(5, 6, 8, 0.72)",
    "scrim_rgb": "8, 8, 10",
    "specular": "rgba(255, 255, 255, 0.08)",
}

PALETTE_WHITE: Dict[str, str] = {
    "mode": "white",
    "bg_app": "#ECEAE5",
    "bg_sidebar": "#E3E0D9",
    "bg_surface": "#FAF9F6",
    "bg_elevated": "#FFFFFF",
    "bg_card": "#F5F4F0",
    "bg_hover": "#E6E3DC",
    "bg_input": "#FFFFFF",
    "bg_keycap": "#F8F7F3",
    "bg_keycap_hover": "#EFECE4",
    "bg_keycap_lip": "#C9C4BA",
    "bg_canvas": "#ECEAE5",
    "bg_panel": "#FAF9F6",
    "bg_active_row": "rgba(217, 119, 6, 0.12)",
    "bg_trash": "rgba(220, 38, 38, 0.08)",
    "bg_trash_hover": "rgba(220, 38, 38, 0.15)",
    "bg_trash_pulse": "rgba(220, 38, 38, 0.25)",
    "bg_trash_well": "#FCE8E8",
    "border": "#D5D1C8",
    "border_subtle": "#E4E0D8",
    "border_strong": "#B6B0A4",
    "border_focus": "#D97706",
    "border_photo": "#D5D1C8",
    "border_keycap": "#D5D1C8",
    "border_trash": "rgba(220, 38, 38, 0.45)",
    "text_primary": "#18181B",
    "text_secondary": "#52525B",
    "text_muted": "#71717A",
    "accent": "#D97706",
    "accent_hover": "#B45309",
    "accent_pressed": "#92400E",
    "accent_text_on_fill": "#FFFFFF",
    "accent_soft": "rgba(217, 119, 6, 0.12)",
    "accent_glow": "rgba(217, 119, 6, 0.28)",
    "danger": "#DC2626",
    "danger_hover": "#B91C1C",
    "danger_soft": "rgba(220, 38, 38, 0.11)",
    "trash_accent": "#DC2626",
    "success": "#059669",
    "success_soft": "rgba(5, 150, 105, 0.12)",
    "warning": "#D97706",
    "hud_bg": "rgba(250, 249, 246, 0.94)",
    "hud_trash_bg": "rgba(254, 242, 242, 0.95)",
    "hud_border": "rgba(24, 24, 27, 0.10)",
    "overlay_scrim": "rgba(24, 24, 27, 0.45)",
    "scrim_rgb": "236, 234, 229",
    "specular": "rgba(255, 255, 255, 0.72)",
}

# Mutated in-place by set_active_theme so all modules reading PALETTE stay live.
PALETTE: Dict[str, str] = dict(PALETTE_BLACK)

_THEME_STATE: Dict[str, object] = {
    "mode": "black",
    "has_custom_bg": False,
    "bg_dim": 68,
}

_FONTS_LOADED = False


def resolve_asset_path(rel_path: str) -> Path:
    """Resolve an asset path in PyInstaller (_MEIPASS) or local development."""
    rel = Path(rel_path)
    base_dir = getattr(sys, "_MEIPASS", None)
    if base_dir:
        candidate = Path(base_dir) / rel
        if candidate.exists():
            return candidate
    repo_root = Path(__file__).resolve().parent.parent
    candidate = repo_root / rel
    if candidate.exists():
        return candidate
    return Path(os.getcwd()) / rel


def load_bundled_fonts(app: QApplication | None = None) -> str:
    """Load bundled Poppins fonts and set as application font."""
    global _FONTS_LOADED
    font_files = [
        "assets/fonts/Poppins-Regular.ttf",
        "assets/fonts/Poppins-Medium.ttf",
        "assets/fonts/Poppins-SemiBold.ttf",
        "assets/fonts/Poppins-Bold.ttf",
    ]
    if QApplication.instance() is not None and not _FONTS_LOADED:
        for rel in font_files:
            path = resolve_asset_path(rel)
            if path.exists():
                QFontDatabase.addApplicationFont(str(path))
        _FONTS_LOADED = True

    target_app = app or QApplication.instance()
    if isinstance(target_app, QApplication):
        base_font = QFont(FONT_FAMILY, 10)
        base_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
        target_app.setFont(base_font)
    return FONT_FAMILY


def _hex_to_rgba(hex_str: str, alpha: float) -> str:
    c = QColor(hex_str)
    if not c.isValid():
        return hex_str
    a = max(0, min(255, int(round(alpha * 255))))
    return f"rgba({c.red()}, {c.green()}, {c.blue()}, {a / 255:.3f})"


def get_theme_mode() -> str:
    return str(_THEME_STATE.get("mode", "black"))


def is_light_theme() -> bool:
    return get_theme_mode() == "white"


def is_custom_bg_active() -> bool:
    return bool(_THEME_STATE.get("has_custom_bg", False))


def get_custom_bg_dim() -> int:
    return int(_THEME_STATE.get("bg_dim", 68))


def set_active_theme(
    mode: str = "black",
    has_custom_bg: bool = False,
    bg_dim: int = 68,
) -> Dict[str, str]:
    """Update active palette in-place for black/white theme and custom background scrim."""
    norm_mode = "white" if str(mode).strip().lower() == "white" else "black"
    clamped_dim = max(20, min(90, int(bg_dim)))
    _THEME_STATE["mode"] = norm_mode
    _THEME_STATE["has_custom_bg"] = bool(has_custom_bg)
    _THEME_STATE["bg_dim"] = clamped_dim

    source = PALETTE_WHITE if norm_mode == "white" else PALETTE_BLACK
    PALETTE.clear()
    PALETTE.update(source)
    return PALETTE


def _panel_bg(hex_key: str, default_alpha_bg: float = 0.88) -> str:
    """Return translucent rgba when custom background is active, else opaque hex."""
    base = PALETTE[hex_key]
    if not is_custom_bg_active():
        return base
    dim = get_custom_bg_dim()
    # Keep surfaces readable at all dim levels while allowing custom artwork to show.
    alpha = max(0.56, min(0.92, (dim + 14) / 100.0))
    if hex_key == "bg_sidebar":
        alpha = max(0.62, min(0.94, alpha + 0.04))
    elif hex_key == "bg_card":
        alpha = max(0.64, min(0.94, default_alpha_bg))
    return _hex_to_rgba(base, alpha)


def get_stylesheet(*_args: object) -> str:
    """Generate clean, modern Optical Instrument QSS without dashed CSS border artifacts."""
    p = PALETTE
    custom_bg = is_custom_bg_active()
    main_bg = "transparent" if custom_bg else p["bg_app"]
    sidebar_bg = _panel_bg("bg_sidebar")
    surface_bg = _panel_bg("bg_surface")
    elevated_bg = _panel_bg("bg_elevated")
    card_bg = _panel_bg("bg_card")
    canvas_bg = "transparent" if custom_bg else p["bg_canvas"]
    hud_bar_bg = _hex_to_rgba(p["bg_surface"], 0.86 if custom_bg else 0.96)
    tray_bg = _hex_to_rgba(p["bg_surface"], 0.82 if custom_bg else 0.94)
    deck_bg = _hex_to_rgba(p["bg_surface"], 0.86 if custom_bg else 0.97)

    return f"""
QWidget {{
    font-family: "{FONT_FAMILY}", "Segoe UI", sans-serif;
    color: {p["text_primary"]};
    selection-background-color: {p["accent"]};
    selection-color: {p["accent_text_on_fill"]};
}}

QMainWindow, QWidget#MainContainer {{
    background-color: {p["bg_app"]};
}}

QStackedWidget, QWidget#SortingRoot, QWidget#SetupRoot, QWidget#CompleteRoot {{
    background-color: {main_bg};
}}

QFrame#SessionSidebar {{
    background-color: {sidebar_bg};
    border-right: 1px solid {p["border"]};
}}

QFrame#GallerySidebar {{
    background-color: {sidebar_bg};
    border-left: 1px solid {p["border"]};
}}

QFrame#GalleryBulkBar {{
    background-color: {p["bg_elevated"]};
    border-top: 1px solid {p["border"]};
}}

QFrame#SettingGroupCard {{
    background-color: {p["bg_elevated"]};
    border: 1px solid {p["border"]};
    border-radius: 10px;
}}

QFrame#HairlineDivider {{
    background-color: {p["border_subtle"]};
    border: none;
    max-height: 1px;
    min-height: 1px;
}}

QFrame#SortingHeader {{
    background-color: {hud_bar_bg};
    border: 1px solid {p["border"]};
    border-radius: 10px;
}}

QFrame#FilmstripBar {{
    background-color: transparent;
    border: none;
}}

QFrame#FilmstripTray {{
    background-color: {tray_bg};
    border: 1px solid {p["border"]};
    border-radius: 10px;
}}

QFrame#CategoryBarContainer {{
    background-color: {deck_bg};
    border: 1px solid {p["border"]};
    border-radius: 12px;
}}

QFrame#PhotoCanvasFrame, QGraphicsView#PhotoGraphicsView {{
    background-color: {canvas_bg};
    border: none;
}}

QFrame#ElevatedCard {{
    background-color: {card_bg};
    border: 1px solid {p["border"]};
    border-radius: 12px;
}}

QFrame#MetadataHoverCard {{
    background-color: {p["bg_elevated"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 10px;
}}

QFrame#InWindowOverlayBackdrop {{
    background-color: transparent;
}}

QFrame#InWindowOverlayCard {{
    background-color: {p["bg_surface"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 14px;
}}

QFrame#SettingsNavPanel {{
    background-color: {p["bg_sidebar"]};
    border-right: 1px solid {p["border"]};
    border-top-left-radius: 14px;
    border-bottom-left-radius: 14px;
}}

QFrame#VideoControlBar {{
    background-color: {p["hud_bg"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 10px;
}}

QPushButton#VideoIconBtn {{
    background-color: {p["bg_keycap"]};
    border: 1px solid {p["border"]};
    border-radius: 7px;
    padding: 0px;
}}

QPushButton#VideoIconBtn:hover {{
    background-color: {p["bg_hover"]};
    border: 1px solid {p["accent"]};
}}

QPushButton#AutoplayToggleBtn {{
    background-color: {p["bg_keycap"]};
    border: 1px solid {p["border"]};
    border-radius: 7px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
    color: {p["text_secondary"]};
}}

QPushButton#AutoplayToggleBtn:checked {{
    background-color: {p["accent_soft"]};
    border: 1px solid {p["accent"]};
    color: {p["accent"]};
}}

QLabel#VideoTimeLabel {{
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
    color: {p["text_secondary"]};
    background: transparent;
}}

QFrame#SplashCard {{
    background-color: {p["bg_surface"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 14px;
}}

QLabel#BrandTitle {{
    font-size: 20px;
    font-weight: 700;
    letter-spacing: 2px;
    color: {p["text_primary"]};
    background: transparent;
}}

QLabel#SectionHeading {{
    font-size: 16px;
    font-weight: 600;
    color: {p["text_primary"]};
    background: transparent;
}}

QLabel#FieldLabel {{
    font-size: 12px;
    font-weight: 600;
    color: {p["text_secondary"]};
    background: transparent;
}}

QLabel#MutedLabel {{
    font-size: 12px;
    font-weight: 400;
    color: {p["text_muted"]};
    background: transparent;
}}

QLabel#MonoLabel {{
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
    color: {p["text_secondary"]};
    background: transparent;
}}

QLineEdit, QSpinBox {{
    background-color: {p["bg_input"]};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 6px 10px;
    font-size: 13px;
    color: {p["text_primary"]};
}}

QLineEdit:focus, QSpinBox:focus {{
    border: 1px solid {p["border_focus"]};
}}

QLineEdit#InlineCategoryInput {{
    background-color: transparent;
    border: none;
    padding: 2px 4px;
    font-size: 12px;
    font-weight: 500;
    color: {p["text_primary"]};
}}

QComboBox {{
    background-color: {p["bg_input"]};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 4px 26px 4px 10px;
    font-size: 12px;
    font-weight: 500;
    color: {p["text_primary"]};
    min-height: 26px;
}}

QComboBox:hover {{
    border: 1px solid {p["border_strong"]};
}}

QComboBox:focus {{
    border: 1px solid {p["border_focus"]};
}}

QComboBox::drop-down {{
    border: none;
    width: 22px;
}}

QComboBox QAbstractItemView {{
    background-color: {p["bg_elevated"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 8px;
    padding: 4px;
    color: {p["text_primary"]};
    selection-background-color: {p["accent_soft"]};
    selection-color: {p["accent"]};
    outline: none;
}}

QPushButton {{
    background-color: {elevated_bg};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 6px 12px;
    font-size: 12px;
    font-weight: 500;
    color: {p["text_primary"]};
}}

QPushButton:hover {{
    background-color: {p["bg_hover"]};
    border: 1px solid {p["border_strong"]};
}}

QPushButton:pressed {{
    background-color: {p["bg_keycap_lip"]};
}}

QPushButton:disabled {{
    color: {p["text_muted"]};
    border-color: {p["border_subtle"]};
}}

QPushButton#PrimaryButton {{
    background-color: {p["accent"]};
    border: 1px solid {p["accent"]};
    color: {p["accent_text_on_fill"]};
    font-weight: 600;
}}

QPushButton#PrimaryButton:hover {{
    background-color: {p["accent_hover"]};
    border: 1px solid {p["accent_hover"]};
}}

QPushButton#PrimaryButton:pressed {{
    background-color: {p["accent_pressed"]};
    border: 1px solid {p["accent_pressed"]};
}}

QPushButton#DangerButton {{
    background-color: {p["danger_soft"]};
    border: 1px solid {p["danger"]};
    color: {p["danger"]};
    font-weight: 600;
}}

QPushButton#DangerButton:hover {{
    background-color: {p["danger"]};
    color: #FFFFFF;
}}

QPushButton#GhostIconButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 5px;
}}

QPushButton#GhostIconButton:hover {{
    background-color: {p["bg_hover"]};
    border: 1px solid {p["border"]};
}}

QPushButton#MetadataCapsuleBtn {{
    background-color: {p["bg_keycap"]};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 4px 10px;
    font-size: 11px;
    font-weight: 500;
    color: {p["text_secondary"]};
}}

QPushButton#MetadataCapsuleBtn:hover {{
    background-color: {p["bg_hover"]};
    border: 1px solid {p["border_focus"]};
    color: {p["text_primary"]};
}}

QPushButton#CounterPillBtn {{
    background-color: {p["bg_keycap"]};
    border: 1px solid {p["border"]};
    border-radius: 8px;
    padding: 3px 10px;
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 12px;
    font-weight: 600;
    color: {p["text_primary"]};
}}

QPushButton#CounterPillBtn:hover {{
    border: 1px solid {p["accent"]};
    color: {p["accent"]};
}}

QPushButton#KeyBadgeButton {{
    background-color: {p["bg_input"]};
    border: 1px solid {p["border_strong"]};
    border-bottom: 2px solid {p["bg_keycap_lip"]};
    border-radius: 5px;
    padding: 2px 7px;
    font-family: "JetBrains Mono", "Consolas", monospace;
    font-size: 11px;
    font-weight: 700;
    color: {p["text_primary"]};
    min-width: 24px;
}}

QPushButton#KeyBadgeButton:hover {{
    border-color: {p["accent"]};
    color: {p["accent"]};
}}

QPushButton#SettingsNavButton {{
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 8px;
    padding: 8px 12px;
    text-align: left;
    font-size: 12px;
    font-weight: 500;
    color: {p["text_secondary"]};
}}

QPushButton#SettingsNavButton:hover {{
    background-color: {p["bg_hover"]};
    border: 1px solid transparent;
    color: {p["text_primary"]};
}}

QPushButton#SettingsNavButton:pressed {{
    background-color: transparent;
    border: 1px solid transparent;
}}

QPushButton#SettingsNavButton[active="true"],
QPushButton#SettingsNavButton[active="true"]:hover {{
    background-color: transparent;
    border: 1px solid transparent;
    color: {p["accent"]};
    font-weight: 600;
}}

QPushButton#GhostIconButton[active="true"] {{
    background-color: {p["accent_soft"]};
    border: 1px solid {p["accent"]};
    color: {p["accent"]};
}}

QPushButton#GalleryFilterBtn {{
    background-color: {p["bg_input"]};
    border: 1px solid {p["border"]};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
    font-weight: 500;
    color: {p["text_secondary"]};
}}

QPushButton#GalleryFilterBtn:hover {{
    background-color: {p["bg_hover"]};
    border: 1px solid {p["border_strong"]};
    color: {p["text_primary"]};
}}

QPushButton#GalleryFilterBtn[active="true"] {{
    background-color: {p["accent_soft"]};
    border: 1px solid {p["accent"]};
    color: {p["accent"]};
    font-weight: 600;
}}

QPushButton#SegmentedButton {{
    background-color: {p["bg_input"]};
    border: 1px solid {p["border"]};
    border-radius: 7px;
    padding: 6px 14px;
    font-size: 12px;
    font-weight: 500;
    color: {p["text_secondary"]};
}}

QPushButton#SegmentedButton[active="true"] {{
    background-color: {p["accent"]};
    border: 1px solid {p["accent"]};
    color: {p["accent_text_on_fill"]};
    font-weight: 600;
}}

QCheckBox {{
    spacing: 8px;
    font-size: 12px;
    color: {p["text_primary"]};
    background: transparent;
}}

QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border-radius: 4px;
    border: 1px solid {p["border_strong"]};
    background-color: {p["bg_input"]};
}}

QCheckBox::indicator:checked {{
    border: 1px solid {p["accent"]};
    background-color: {p["accent"]};
}}

QSlider::groove:horizontal {{
    height: 5px;
    background: {p["border"]};
    border-radius: 2px;
}}

QSlider::sub-page:horizontal {{
    background: {p["accent"]};
    border-radius: 2px;
}}

QSlider::handle:horizontal {{
    width: 13px;
    height: 13px;
    margin: -4px 0;
    border-radius: 6px;
    background: {p["text_primary"]};
    border: 2px solid {p["accent"]};
}}

QScrollArea {{
    background: transparent;
    border: none;
}}

QScrollArea > QWidget > QWidget {{
    background: transparent;
}}

QScrollBar:vertical {{
    background: transparent;
    width: 7px;
    margin: 2px;
}}

QScrollBar::handle:vertical {{
    background: {p["border_strong"]};
    border-radius: 3px;
    min-height: 24px;
}}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0px;
}}

QScrollBar:horizontal {{
    background: transparent;
    height: 7px;
    margin: 2px;
}}

QScrollBar::handle:horizontal {{
    background: {p["border_strong"]};
    border-radius: 3px;
    min-width: 24px;
}}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0px;
}}

QMenu {{
    background-color: {p["bg_elevated"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 8px;
    padding: 5px;
    color: {p["text_primary"]};
}}

QMenu::item {{
    padding: 6px 14px;
    border-radius: 5px;
    font-size: 12px;
}}

QMenu::item:selected {{
    background-color: {p["accent_soft"]};
    color: {p["accent"]};
}}

QToolTip {{
    background-color: {p["bg_elevated"]};
    color: {p["text_primary"]};
    border: 1px solid {p["border_strong"]};
    border-radius: 6px;
    padding: 4px 8px;
    font-size: 11px;
}}
"""
