"""Multi-session photo & video sorting state, pure navigation, direct reassignment, undo, and detailed metadata."""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from PIL import ExifTags, Image
from PyQt6.QtGui import QImageIOHandler, QImageReader

APP_NAME = "PhotoBox"
APP_VERSION = "0.6.0"
TRASH_FOLDER = "_Buang"

SUPPORTED_IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
SUPPORTED_VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v"}
SUPPORTED_EXTS = SUPPORTED_IMAGE_EXTS | SUPPORTED_VIDEO_EXTS
SUPPORTED_LANGUAGES = ("en", "id")

DEFAULT_KEYBIND_SLOTS = ["1", "2", "3", "4", "5", "6", "7", "8", "9"]
DEFAULT_KEYBINDS = DEFAULT_KEYBIND_SLOTS
SHORTCUT_KEYS = [
    "1", "2", "3", "4", "5", "6", "7", "8", "9", "0",
    "Q", "W", "E", "R", "T", "Y", "U", "I", "O", "P",
]
RESERVED_SYSTEM_KEYS = {
    "TAB",
    "SPACE",
    "BACKSPACE",
    "DELETE",
    "DEL",
    "LEFT",
    "RIGHT",
    "UP",
    "DOWN",
    "RETURN",
    "ENTER",
    "ESCAPE",
    "ESC",
    "CTRL+Z",
}
_TRASH_RESERVED_KEYS = RESERVED_SYSTEM_KEYS - {"DEL", "DELETE"}
DEFAULT_CATEGORY_COLOR = "#F59E0B"
DEFAULT_TRASH_COLOR = "#EF4444"
THEME_MODES = ("black", "white")
KEYBIND_PROFILES = ("default", "custom")
VIDEO_ARROW_MODES = ("seek", "skip")

METADATA_FIELD_KEYS: Tuple[str, ...] = (
    "dimensions",
    "size",
    "format",
    "date_taken",
    "camera",
    "lens",
    "focal_length",
    "shutter_speed",
    "aperture",
    "iso",
    "exposure",
    "color_space",
    "gps",
    "video_codec",
    "fps",
    "duration",
)
DEFAULT_VISIBLE_METADATA_FIELDS: List[str] = ["date_taken", "camera", "exposure"]

SORT_MODES = ("name_asc", "name_desc", "date_asc", "date_desc", "size_desc", "size_asc")
SORT_MODE_LABELS = {
    "name_asc": "Filename (A-Z)",
    "name_desc": "Filename (Z-A)",
    "date_asc": "Date Modified (Oldest)",
    "date_desc": "Date Modified (Newest)",
    "size_desc": "File Size (Largest)",
    "size_asc": "File Size (Smallest)",
}
TIMELINE_STATUSES = ("pending", "skipped", "sorted")

_ILLEGAL_WIN_CHARS_RE = re.compile(r'[\\/:*?"<>|]')
_HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
_FN_KEY_RE = re.compile(r"^F([1-9]|1[0-2])$")
_WIN_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
_EXIF_ORIENTATION_TAG = next(
    (tag for tag, name in ExifTags.TAGS.items() if name == "Orientation"),
    274,
)
_KEY_ALIASES = {
    "SPACEBAR": "SPACE",
    "ARROWLEFT": "LEFT",
    "ARROWRIGHT": "RIGHT",
    "ARROWUP": "UP",
    "ARROWDOWN": "DOWN",
    "DELETE": "DEL",
}

_DETAILED_META_CACHE: OrderedDict[Tuple[str, int], Dict[str, object]] = OrderedDict()
_DETAILED_META_CACHE_CAP = 512


def default_state_file() -> Path:
    """Resolve persistent session JSON path consistently for dev and frozen .exe."""
    appdata = os.environ.get("APPDATA")
    if appdata:
        return Path(appdata) / APP_NAME / "sessions.json"
    return Path.home() / ".photobox_sessions.json"


def normalize_folder_path(folder_path: str | os.PathLike[str]) -> str:
    """Return canonical normalized path key for anti-duplicate comparison."""
    return os.path.normcase(os.path.normpath(os.path.abspath(str(folder_path))))


def natural_sort_key(text: str) -> Tuple[Tuple[int, object], ...]:
    """Return type-safe natural sort key splitting digits and text."""
    parts = re.split(r"(\d+)", text.lower())
    key: List[Tuple[int, object]] = []
    for part in parts:
        if not part:
            continue
        if part.isdigit():
            key.append((0, int(part)))
        else:
            key.append((1, part))
    return tuple(key)


def is_video_file(path: str | os.PathLike[str]) -> bool:
    """Return True if path has a supported video extension."""
    return Path(path).suffix.lower() in SUPPORTED_VIDEO_EXTS


def is_image_file(path: str | os.PathLike[str]) -> bool:
    """Return True if path has a supported image extension."""
    return Path(path).suffix.lower() in SUPPORTED_IMAGE_EXTS


def normalize_color_hex(color: str, fallback: str = DEFAULT_CATEGORY_COLOR) -> str:
    """Normalize a 6-digit #RRGGBB hex color string."""
    cleaned = (color or "").strip()
    if _HEX_COLOR_RE.fullmatch(cleaned):
        return cleaned.upper()
    return fallback.upper()


def sanitize_category_name(name: str, allow_trash: bool = False) -> str:
    """Strip illegal Windows filename characters and reject reserved or empty names."""
    cleaned = _ILLEGAL_WIN_CHARS_RE.sub("", name or "").strip().rstrip(". ")
    if not cleaned or cleaned in {".", ".."}:
        raise ValueError("Nama kategori tidak boleh kosong.")
    if not allow_trash and cleaned.lower() == TRASH_FOLDER.lower():
        raise ValueError(f"Nama '{TRASH_FOLDER}' dicadangkan untuk folder buang.")
    if cleaned.startswith("."):
        raise ValueError("Nama kategori tidak boleh diawali titik.")
    stem = cleaned.split(".", 1)[0].strip().upper()
    if stem in _WIN_RESERVED_NAMES:
        raise ValueError(f"Nama '{cleaned}' dicadangkan oleh sistem Windows.")
    return cleaned


def sanitize_trash_category_name(name: str) -> str:
    """Sanitize trash folder name while allowing '_Buang'."""
    return sanitize_category_name(name, allow_trash=True)


def validate_keybind(
    key_str: str,
    existing_map: Optional[Dict[str, str]] = None,
    ignore_category: Optional[str] = None,
    allow_del: bool = False,
) -> str:
    """Normalize a category shortcut key and reject reserved system keys or conflicts."""
    if key_str is None:
        raise ValueError("Tombol shortcut tidak boleh kosong.")
    raw_input = str(key_str)
    if raw_input == " ":
        raise ValueError("Tombol 'SPACE' dicadangkan oleh sistem dan tidak bisa dipakai.")
    if raw_input == "\t":
        raise ValueError("Tombol 'TAB' dicadangkan oleh sistem dan tidak bisa dipakai.")

    cleaned = raw_input.strip()
    if not cleaned:
        raise ValueError("Tombol shortcut tidak boleh kosong.")

    norm = re.sub(r"\s*\+\s*", "+", cleaned.upper())
    norm = _KEY_ALIASES.get(norm, norm)

    if any(ord(ch) < 32 or ord(ch) == 127 or not ch.isprintable() for ch in norm):
        raise ValueError("Tombol shortcut tidak valid atau merupakan karakter kontrol.")

    reserved_set = _TRASH_RESERVED_KEYS if allow_del else RESERVED_SYSTEM_KEYS
    if norm in reserved_set:
        raise ValueError(
            f"Tombol '{norm}' dicadangkan oleh sistem dan tidak bisa dipakai."
        )

    is_del_ok = allow_del and norm == "DEL"
    if not (
        is_del_ok
        or (len(norm) == 1 and norm.isprintable() and not norm.isspace())
        or _FN_KEY_RE.fullmatch(norm)
    ):
        raise ValueError(f"Tombol '{norm}' tidak valid sebagai shortcut kategori.")

    if existing_map:
        ignore_lower = ignore_category.lower() if ignore_category else None
        for cat, used_key in existing_map.items():
            if ignore_lower is not None and cat.lower() == ignore_lower:
                continue
            if used_key and _KEY_ALIASES.get(str(used_key).strip().upper(), str(used_key).strip().upper()) == norm:
                raise ValueError(
                    f"Tombol '{norm}' sudah dipakai oleh kategori '{cat}'."
                )

    return norm


def next_available_keybind(
    used_keys: Iterable[str],
    profile_keys: Optional[Sequence[str]] = None,
) -> Optional[str]:
    """Return the first non-reserved shortcut key not already in used_keys."""
    used_upper = {str(k).strip().upper() for k in used_keys if str(k).strip()}
    candidates: List[str] = []
    for k in (profile_keys or DEFAULT_KEYBIND_SLOTS):
        ku = str(k).strip().upper()
        if ku and ku not in candidates:
            candidates.append(ku)
    for k in SHORTCUT_KEYS:
        ku = k.upper()
        if ku not in candidates:
            candidates.append(ku)

    for candidate in candidates:
        if candidate in RESERVED_SYSTEM_KEYS or candidate in used_upper:
            continue
        return candidate
    return None


def detect_existing_categories(
    folder_path: str | os.PathLike[str], trash_folder: str = TRASH_FOLDER
) -> List[str]:
    """List top-level subdirectories excluding trash and dot-prefixed folders."""
    root = Path(folder_path)
    if not root.is_dir():
        return []
    trash_lower = {TRASH_FOLDER.lower(), (trash_folder or TRASH_FOLDER).lower()}
    dirs = [
        entry.name
        for entry in root.iterdir()
        if entry.is_dir()
        and not entry.name.startswith(".")
        and entry.name.lower() not in trash_lower
    ]
    dirs.sort(key=natural_sort_key)
    return dirs


def has_subfolders(
    folder_path: str | os.PathLike[str], trash_folder: str = TRASH_FOLDER
) -> bool:
    """Return True when folder_path contains at least one non-hidden, non-trash subfolder."""
    root = Path(folder_path)
    if not root.is_dir():
        return False
    trash_lower = {TRASH_FOLDER.lower(), (trash_folder or TRASH_FOLDER).lower()}
    try:
        return any(
            entry.is_dir()
            and not entry.name.startswith(".")
            and entry.name.lower() not in trash_lower
            for entry in root.iterdir()
        )
    except OSError:
        return False


def count_subfolders(
    folder_path: str | os.PathLike[str], trash_folder: str = TRASH_FOLDER
) -> int:
    """Return number of non-hidden, non-trash top-level subdirectories."""
    return len(detect_existing_categories(folder_path, trash_folder=trash_folder))


def unique_dest_path(dest_dir: Path, filename: str) -> Path:
    """Return collision-safe path using 'stem (1).ext', 'stem (2).ext', ..."""
    candidate = dest_dir / filename
    if not candidate.exists():
        return candidate
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    counter = 1
    while True:
        candidate = dest_dir / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _move_file_safe(
    src: Path, dst: Path, retries: int = 15, delay_s: float = 0.025
) -> None:
    """Move file with brief retry on transient Windows sharing violations (WinError 32)."""
    for attempt in range(retries):
        try:
            shutil.move(str(src), str(dst))
            return
        except PermissionError:
            if src.exists() and dst.exists():
                try:
                    dst.unlink()
                except OSError:
                    pass
            if attempt >= retries - 1:
                raise
            time.sleep(delay_s)


def _probe_video_stream(path: Path) -> Dict[str, object]:
    """Read width, height, duration_ms, video_codec, fps, creation_time via ffprobe."""
    result: Dict[str, object] = {
        "width": 0,
        "height": 0,
        "duration_ms": 0,
        "video_codec": "",
        "fps": "",
        "date_taken": "",
    }
    ffprobe = shutil.which("ffprobe")
    if not ffprobe or not path.is_file():
        return result
    kwargs: Dict[str, object] = {
        "capture_output": True,
        "text": True,
        "timeout": 1.5,
    }
    if os.name == "nt":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        proc = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,codec_name,avg_frame_rate,r_frame_rate",
                "-show_entries",
                "format=duration,tags",
                "-of",
                "json",
                str(path),
            ],
            **kwargs,  # type: ignore[arg-type]
        )
        if proc.returncode != 0 or not proc.stdout:
            return result
        info = json.loads(proc.stdout)
        streams = info.get("streams") or []
        if streams:
            st = streams[0]
            result["width"] = max(0, int(st.get("width") or 0))
            result["height"] = max(0, int(st.get("height") or 0))
            codec = str(st.get("codec_name") or "").strip().upper()
            result["video_codec"] = codec
            fps_raw = str(st.get("avg_frame_rate") or st.get("r_frame_rate") or "")
            if "/" in fps_raw:
                num_s, den_s = fps_raw.split("/", 1)
                num, den = float(num_s), float(den_s)
                if den > 0 and num > 0:
                    fps_val = num / den
                    result["fps"] = (
                        f"{int(round(fps_val))} fps"
                        if abs(fps_val - round(fps_val)) < 0.05
                        else f"{fps_val:.2f} fps"
                    )
        fmt = info.get("format") or {}
        dur_raw = fmt.get("duration")
        if dur_raw:
            result["duration_ms"] = max(0, int(round(float(dur_raw) * 1000.0)))
        tags = fmt.get("tags") or {}
        ctime = str(tags.get("creation_time") or "").strip()
        if ctime:
            result["date_taken"] = ctime[:16].replace("T", " ")
    except Exception:
        pass
    return result


def _probe_video_metadata(path: Path) -> Tuple[int, int, int]:
    """Return (width, height, duration_ms) via ffprobe if available, else (0, 0, 0)."""
    info = _probe_video_stream(path)
    return int(info["width"]), int(info["height"]), int(info["duration_ms"])


def get_fast_media_metadata(path: str | os.PathLike[str]) -> Dict[str, object]:
    """Read image or video filename, resolution, size, and duration cleanly."""
    p = Path(path)
    size_bytes = 0
    try:
        size_bytes = p.stat().st_size
    except OSError:
        pass
    size_mb = size_bytes / (1024.0 * 1024.0)

    if is_video_file(p):
        width, height, duration_ms = (
            _probe_video_metadata(p) if size_bytes > 0 else (0, 0, 0)
        )
        return {
            "name": p.name,
            "width": width,
            "height": height,
            "size_bytes": size_bytes,
            "size_mb": size_mb,
            "is_video": True,
            "duration_ms": duration_ms,
        }

    width, height = 0, 0
    reader: Optional[QImageReader] = None
    try:
        reader = QImageReader(str(p))
        reader.setDecideFormatFromContent(True)
        reader.setAutoTransform(True)
        raw_size = reader.size()
        if raw_size.isValid() and raw_size.width() > 0 and raw_size.height() > 0:
            width, height = raw_size.width(), raw_size.height()
            tx = reader.transformation()
            if bool(tx & QImageIOHandler.Transformation.TransformationRotate90):
                width, height = height, width
    except Exception:
        width, height = 0, 0
    finally:
        if reader is not None:
            dev = reader.device()
            if dev is not None and dev.isOpen():
                dev.close()

    if width <= 0 or height <= 0:
        try:
            with Image.open(p) as img:
                width, height = img.size
                exif = img.getexif()
                if exif and exif.get(_EXIF_ORIENTATION_TAG) in (5, 6, 7, 8):
                    width, height = height, width
        except Exception:
            width, height = 0, 0

    return {
        "name": p.name,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "is_video": False,
        "duration_ms": 0,
    }


def get_fast_photo_metadata(path: str | os.PathLike[str]) -> Dict[str, object]:
    """Read media metadata (EXIF-adjusted images and video files)."""
    return get_fast_media_metadata(path)


def get_photo_metadata(path: str | os.PathLike[str]) -> Dict[str, object]:
    """Alias for get_fast_media_metadata."""
    return get_fast_media_metadata(path)


def _to_float_rational(val: object) -> Optional[float]:
    try:
        if isinstance(val, tuple) and len(val) == 2:
            num, den = float(val[0]), float(val[1])
            return num / den if den != 0 else None
        return float(val)  # type: ignore[arg-type]
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def _format_gps_dms(dms: object, ref: object) -> Optional[float]:
    if not isinstance(dms, (tuple, list)) or len(dms) < 3:
        return None
    d = _to_float_rational(dms[0])
    m = _to_float_rational(dms[1])
    s = _to_float_rational(dms[2])
    if d is None or m is None or s is None:
        return None
    decimal = d + m / 60.0 + s / 3600.0
    ref_str = str(ref or "").strip().upper()
    if ref_str in ("S", "W"):
        decimal = -abs(decimal)
    return decimal


def _format_duration_str(ms: int) -> str:
    if ms <= 0:
        return ""
    total_sec = max(0, int(ms // 1000))
    mins, secs = divmod(total_sec, 60)
    hours, mins = divmod(mins, 60)
    if hours > 0:
        return f"{hours}:{mins:02d}:{secs:02d}"
    return f"{mins:02d}:{secs:02d}"


def get_detailed_media_metadata(path: str | os.PathLike[str]) -> Dict[str, object]:
    """Return LRU-cached detailed EXIF & stream metadata dictionary for header & popup."""
    p = Path(path)
    try:
        stat = p.stat()
        mtime_ns = stat.st_mtime_ns
        size_bytes = stat.st_size
    except OSError:
        mtime_ns = 0
        size_bytes = 0

    cache_key = (str(p), mtime_ns)
    cached = _DETAILED_META_CACHE.get(cache_key)
    if cached is not None:
        _DETAILED_META_CACHE.move_to_end(cache_key)
        return dict(cached)

    fast = get_fast_media_metadata(p)
    width = int(fast["width"])
    height = int(fast["height"])
    size_mb = float(fast["size_mb"])
    is_vid = bool(fast["is_video"])
    duration_ms = int(fast["duration_ms"])

    size_str = (
        f"{size_mb:.2f} MB"
        if size_bytes >= 100 * 1024
        else f"{max(1, int(round(size_bytes / 1024.0)))} KB"
        if size_bytes > 0
        else "0 KB"
    )
    dim_str = f"{width} × {height} px" if width > 0 and height > 0 else ""

    meta: Dict[str, object] = {
        "name": p.name,
        "width": width,
        "height": height,
        "size_bytes": size_bytes,
        "size_mb": size_mb,
        "is_video": is_vid,
        "duration_ms": duration_ms,
        "dimensions": dim_str,
        "size": size_str,
        "format": p.suffix.lstrip(".").upper(),
        "date_taken": "",
        "camera": "",
        "lens": "",
        "focal_length": "",
        "shutter_speed": "",
        "aperture": "",
        "iso": "",
        "exposure": "",
        "color_space": "",
        "gps": "",
        "video_codec": "",
        "fps": "",
        "duration": _format_duration_str(duration_ms),
    }

    if is_vid:
        if size_bytes > 0:
            vinfo = _probe_video_stream(p)
            if vinfo["width"] and vinfo["height"]:
                meta["width"] = vinfo["width"]
                meta["height"] = vinfo["height"]
                meta["dimensions"] = f"{vinfo['width']} × {vinfo['height']} px"
            if vinfo["duration_ms"]:
                meta["duration_ms"] = vinfo["duration_ms"]
                meta["duration"] = _format_duration_str(int(vinfo["duration_ms"]))
            meta["video_codec"] = vinfo["video_codec"]
            meta["fps"] = vinfo["fps"]
            meta["date_taken"] = vinfo["date_taken"]
    elif size_bytes > 0:
        try:
            with Image.open(p) as img:
                if img.format:
                    meta["format"] = str(img.format).upper()
                if width <= 0 or height <= 0:
                    w, h = img.size
                    meta["width"], meta["height"] = w, h
                    if w > 0 and h > 0:
                        meta["dimensions"] = f"{w} × {h} px"
                ifd0 = img.getexif()
                if ifd0:
                    make = str(ifd0.get(271) or "").strip()
                    model = str(ifd0.get(272) or "").strip()
                    if make and model:
                        camera = (
                            model
                            if model.lower().startswith(make.lower())
                            else f"{make} {model}"
                        )
                    else:
                        camera = model or make
                    meta["camera"] = camera

                    dt_ifd0 = str(ifd0.get(306) or "").strip()

                    exif_ifd: Dict[int, object] = {}
                    try:
                        for k, v in ifd0.items():
                            exif_ifd[int(k)] = v
                    except Exception:
                        pass
                    try:
                        exif_sub = ifd0.get_ifd(ExifTags.IFD.Exif)
                        for k, v in exif_sub.items():
                            exif_ifd[int(k)] = v
                    except Exception:
                        pass

                    dt_orig = str(
                        exif_ifd.get(36867) or exif_ifd.get(36868) or dt_ifd0 or ""
                    ).strip()
                    if dt_orig:
                        if len(dt_orig) >= 16 and dt_orig[4] == ":" and dt_orig[7] == ":":
                            dt_orig = (
                                dt_orig[:10].replace(":", "-") + " " + dt_orig[11:16]
                            )
                        meta["date_taken"] = dt_orig

                    lens = str(exif_ifd.get(42036) or "").strip()
                    meta["lens"] = lens

                    focal_val = _to_float_rational(exif_ifd.get(37386))
                    if focal_val and focal_val > 0:
                        meta["focal_length"] = (
                            f"{int(round(focal_val))}mm"
                            if abs(focal_val - round(focal_val)) < 0.05
                            else f"{focal_val:.1f}mm"
                        )

                    exp_val = _to_float_rational(exif_ifd.get(33434))
                    if exp_val and exp_val > 0:
                        if exp_val < 0.5:
                            denom = max(1, int(round(1.0 / exp_val)))
                            meta["shutter_speed"] = f"1/{denom}s"
                        elif abs(exp_val - round(exp_val)) < 0.05:
                            meta["shutter_speed"] = f"{int(round(exp_val))}s"
                        else:
                            meta["shutter_speed"] = f"{exp_val:.1f}s"

                    fnum_val = _to_float_rational(exif_ifd.get(33437))
                    if fnum_val and fnum_val > 0:
                        meta["aperture"] = (
                            f"f/{int(round(fnum_val))}"
                            if abs(fnum_val - round(fnum_val)) < 0.05
                            else f"f/{fnum_val:.1f}"
                        )

                    iso_raw = exif_ifd.get(34855) or exif_ifd.get(34867)
                    if isinstance(iso_raw, (tuple, list)) and iso_raw:
                        iso_raw = iso_raw[0]
                    if iso_raw:
                        try:
                            meta["iso"] = f"ISO {int(iso_raw)}"
                        except (TypeError, ValueError):
                            pass

                    exp_parts = [
                        str(meta["focal_length"]),
                        str(meta["aperture"]),
                        str(meta["shutter_speed"]),
                        str(meta["iso"]),
                    ]
                    meta["exposure"] = " · ".join(part for part in exp_parts if part)

                    cs_val = exif_ifd.get(40961)
                    if cs_val == 1:
                        meta["color_space"] = "sRGB"
                    elif cs_val == 65535:
                        meta["color_space"] = "Uncalibrated"
                    elif img.info.get("icc_profile"):
                        meta["color_space"] = "Embedded ICC"

                    try:
                        gps_ifd = dict(ifd0.get_ifd(ExifTags.IFD.GPSInfo))
                    except Exception:
                        gps_ifd = {}
                    if gps_ifd:
                        lat = _format_gps_dms(gps_ifd.get(2), gps_ifd.get(1))
                        lon = _format_gps_dms(gps_ifd.get(4), gps_ifd.get(3))
                        if lat is not None and lon is not None:
                            meta["gps"] = f"{lat:.5f}, {lon:.5f}"
        except Exception:
            pass

    _DETAILED_META_CACHE[cache_key] = dict(meta)
    while len(_DETAILED_META_CACHE) > _DETAILED_META_CACHE_CAP:
        _DETAILED_META_CACHE.popitem(last=False)
    return meta


@dataclass
class DefaultCategorySpec:
    """Application-wide default category preset loaded into new session setups."""

    name: str
    key: str
    color: str = DEFAULT_CATEGORY_COLOR

    def to_dict(self) -> Dict[str, str]:
        return {
            "name": self.name,
            "key": self.key,
            "color": normalize_color_hex(self.color),
        }

    @classmethod
    def from_dict(
        cls, data: Dict[str, object], is_trash: bool = False
    ) -> "DefaultCategorySpec":
        name = sanitize_category_name(
            str(data.get("name", TRASH_FOLDER if is_trash else "")),
            allow_trash=is_trash,
        )
        key = validate_keybind(
            str(data.get("key", "DEL" if is_trash else "1")),
            allow_del=is_trash,
        )
        color = normalize_color_hex(
            str(
                data.get(
                    "color", DEFAULT_TRASH_COLOR if is_trash else DEFAULT_CATEGORY_COLOR
                )
            )
        )
        return cls(name=name, key=key, color=color)


def _default_categories_factory() -> List[DefaultCategorySpec]:
    return [
        DefaultCategorySpec(name="Favorites", key="1", color="#F59E0B"),
        DefaultCategorySpec(name="Selects", key="2", color="#10B981"),
    ]


def _default_trash_category_factory() -> DefaultCategorySpec:
    return DefaultCategorySpec(
        name=TRASH_FOLDER, key="DEL", color=DEFAULT_TRASH_COLOR
    )


def _clamp_dim(raw_val: object) -> int:
    try:
        val = float(raw_val)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 68
    if 0.0 < val <= 1.0:
        val *= 100.0
    return max(20, min(90, int(round(val))))


def _clamp_seek_seconds(raw_val: object) -> int:
    try:
        val = int(round(float(raw_val)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 3
    return max(1, min(60, val))


def _clamp_gallery_width(raw_val: object) -> int:
    try:
        val = int(round(float(raw_val)))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 276
    return max(180, min(640, val))


def _normalize_slots(raw_slots: object) -> List[str]:
    raw_list = list(raw_slots) if isinstance(raw_slots, (list, tuple)) else []
    slots: List[str] = []
    used: set[str] = set()
    for idx in range(9):
        raw = raw_list[idx] if idx < len(raw_list) else DEFAULT_KEYBIND_SLOTS[idx]
        chosen: Optional[str] = None
        try:
            cand = validate_keybind(str(raw))
            if cand not in used:
                chosen = cand
        except ValueError:
            chosen = None
        if chosen is None:
            chosen = next_available_keybind(used) or DEFAULT_KEYBIND_SLOTS[idx]
        used.add(chosen)
        slots.append(chosen)
    return slots


@dataclass
class AppSettings:
    """Global PhotoBox preferences persisted in sessions.json."""

    language: str = "en"
    theme_mode: str = "black"
    custom_bg_path: str = ""
    custom_bg_dim: int = 68
    video_autoplay: bool = True
    video_seek_seconds: int = 3
    video_arrow_mode: str = "seek"
    keybind_profile: str = "default"
    custom_keybinds: List[str] = field(
        default_factory=lambda: list(DEFAULT_KEYBIND_SLOTS)
    )
    custom_keybind_profiles: Dict[str, List[str]] = field(default_factory=dict)
    trash_category: DefaultCategorySpec = field(
        default_factory=_default_trash_category_factory
    )
    default_categories: List[DefaultCategorySpec] = field(
        default_factory=_default_categories_factory
    )
    visible_metadata_fields: List[str] = field(
        default_factory=lambda: list(DEFAULT_VISIBLE_METADATA_FIELDS)
    )
    gallery_open: bool = True
    gallery_width: int = 276

    def normalized(self) -> "AppSettings":
        lang = "id" if str(self.language).strip().lower() == "id" else "en"
        mode = self.theme_mode if self.theme_mode in THEME_MODES else "black"
        dim = _clamp_dim(self.custom_bg_dim)
        seek_sec = _clamp_seek_seconds(self.video_seek_seconds)
        arrow_mode = (
            self.video_arrow_mode
            if self.video_arrow_mode in VIDEO_ARROW_MODES
            else "seek"
        )

        # Normalize trash category (pinned left-most default)
        try:
            trash_name = sanitize_trash_category_name(self.trash_category.name)
        except ValueError:
            trash_name = TRASH_FOLDER
        try:
            trash_key = validate_keybind(self.trash_category.key, allow_del=True)
        except ValueError:
            trash_key = "DEL"
        trash_spec = DefaultCategorySpec(
            name=trash_name,
            key=trash_key,
            color=normalize_color_hex(self.trash_category.color, DEFAULT_TRASH_COLOR),
        )

        # Normalize named custom keybind profiles
        profiles: Dict[str, List[str]] = {}
        for pname, pslots in (self.custom_keybind_profiles or {}).items():
            clean_pname = str(pname or "").strip()
            if not clean_pname or clean_pname.lower() == "default":
                continue
            profiles[clean_pname] = _normalize_slots(pslots)

        slots = _normalize_slots(self.custom_keybinds)
        raw_profile = str(self.keybind_profile or "default").strip()
        if raw_profile.lower() == "custom":
            # Migrate legacy v0.3.0 "custom" profile cleanly
            if not profiles:
                profiles["Custom"] = list(slots)
                profile_name = "Custom"
            elif "Custom" in profiles:
                profile_name = "Custom"
            else:
                profile_name = next(iter(profiles.keys()))
        elif raw_profile in profiles:
            profile_name = raw_profile
            slots = list(profiles[raw_profile])
        else:
            profile_name = "default"

        cats: List[DefaultCategorySpec] = []
        seen_names: set[str] = {trash_spec.name.lower(), TRASH_FOLDER.lower()}
        used_cat_keys: set[str] = {trash_spec.key}
        for item in self.default_categories:
            spec = (
                item
                if isinstance(item, DefaultCategorySpec)
                else DefaultCategorySpec.from_dict(item)
            )
            try:
                clean_name = sanitize_category_name(spec.name)
            except ValueError:
                continue
            if clean_name.lower() in seen_names:
                continue
            seen_names.add(clean_name.lower())
            try:
                clean_key = validate_keybind(spec.key)
            except ValueError:
                clean_key = ""
            if not clean_key or clean_key in used_cat_keys:
                clean_key = next_available_keybind(used_cat_keys, slots) or "1"
            used_cat_keys.add(clean_key)
            cats.append(
                DefaultCategorySpec(
                    name=clean_name,
                    key=clean_key,
                    color=normalize_color_hex(spec.color),
                )
            )

        meta_fields = [
            f for f in (self.visible_metadata_fields or []) if f in METADATA_FIELD_KEYS
        ]
        if self.visible_metadata_fields is None:
            meta_fields = list(DEFAULT_VISIBLE_METADATA_FIELDS)

        return AppSettings(
            language=lang,
            theme_mode=mode,
            custom_bg_path=str(self.custom_bg_path or "").strip(),
            custom_bg_dim=dim,
            video_autoplay=bool(self.video_autoplay),
            video_seek_seconds=seek_sec,
            video_arrow_mode=arrow_mode,
            keybind_profile=profile_name,
            custom_keybinds=slots,
            custom_keybind_profiles=profiles,
            trash_category=trash_spec,
            default_categories=cats,
            visible_metadata_fields=meta_fields,
            gallery_open=bool(self.gallery_open),
            gallery_width=_clamp_gallery_width(self.gallery_width),
        )

    def active_keybind_slots(self) -> List[str]:
        """Return the 9 primary shortcut keys for the currently selected profile."""
        prof = str(self.keybind_profile or "default").strip()
        if prof.lower() != "default":
            if prof in self.custom_keybind_profiles:
                return list(self.custom_keybind_profiles[prof][:9])
            if self.custom_keybinds:
                return [str(k).upper() for k in self.custom_keybinds[:9]]
        return list(DEFAULT_KEYBIND_SLOTS)

    def to_dict(self) -> Dict[str, object]:
        norm = self.normalized()
        return {
            "language": norm.language,
            "theme_mode": norm.theme_mode,
            "custom_bg_path": norm.custom_bg_path,
            "custom_bg_dim": norm.custom_bg_dim,
            "video_autoplay": norm.video_autoplay,
            "video_seek_seconds": norm.video_seek_seconds,
            "video_arrow_mode": norm.video_arrow_mode,
            "keybind_profile": norm.keybind_profile,
            "custom_keybinds": list(norm.custom_keybinds),
            "custom_keybind_profiles": {
                k: list(v) for k, v in norm.custom_keybind_profiles.items()
            },
            "trash_category": norm.trash_category.to_dict(),
            "default_categories": [c.to_dict() for c in norm.default_categories],
            "visible_metadata_fields": list(norm.visible_metadata_fields),
            "gallery_open": bool(norm.gallery_open),
            "gallery_width": int(norm.gallery_width),
        }

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, object]]) -> "AppSettings":
        if not data:
            return cls().normalized()
        raw_cats = data.get("default_categories")
        if raw_cats is None:
            cats = _default_categories_factory()
        else:
            cats = []
            for item in raw_cats if isinstance(raw_cats, list) else []:
                if isinstance(item, dict):
                    try:
                        cats.append(DefaultCategorySpec.from_dict(item))
                    except ValueError:
                        continue
        raw_trash = data.get("trash_category")
        if isinstance(raw_trash, dict):
            try:
                trash_spec = DefaultCategorySpec.from_dict(raw_trash, is_trash=True)
            except ValueError:
                trash_spec = _default_trash_category_factory()
        else:
            trash_spec = _default_trash_category_factory()

        raw_keys = data.get("custom_keybinds")
        slots = (
            [str(k) for k in raw_keys]
            if isinstance(raw_keys, list)
            else list(DEFAULT_KEYBIND_SLOTS)
        )
        raw_profiles = data.get("custom_keybind_profiles")
        profiles: Dict[str, List[str]] = {}
        if isinstance(raw_profiles, dict):
            for k, v in raw_profiles.items():
                if isinstance(v, list):
                    profiles[str(k)] = [str(s) for s in v]

        raw_meta_fields = data.get("visible_metadata_fields")
        meta_fields = (
            [str(f) for f in raw_meta_fields]
            if isinstance(raw_meta_fields, list)
            else list(DEFAULT_VISIBLE_METADATA_FIELDS)
        )

        return cls(
            language=str(data.get("language", "en")),
            theme_mode=str(data.get("theme_mode", "black")),
            custom_bg_path=str(data.get("custom_bg_path", "") or ""),
            custom_bg_dim=_clamp_dim(data.get("custom_bg_dim", 68)),
            video_autoplay=bool(data.get("video_autoplay", True)),
            video_seek_seconds=_clamp_seek_seconds(data.get("video_seek_seconds", 3)),
            video_arrow_mode=str(data.get("video_arrow_mode", "seek")),
            keybind_profile=str(data.get("keybind_profile", "default")),
            custom_keybinds=slots,
            custom_keybind_profiles=profiles,
            trash_category=trash_spec,
            default_categories=cats,
            visible_metadata_fields=meta_fields,
            gallery_open=bool(data.get("gallery_open", True)),
            gallery_width=_clamp_gallery_width(data.get("gallery_width", 276)),
        ).normalized()


@dataclass
class TimelineEntry:
    """Single fixed-numbered media slot within a sorting session timeline."""

    index: int
    name: str
    rel_path: str
    status: str = "pending"
    category: Optional[str] = None
    origin_rel_path: Optional[str] = None

    @property
    def photo_name(self) -> str:
        """Compatibility alias for callers expecting .photo_name."""
        return self.name

    def to_dict(self) -> Dict[str, object]:
        data: Dict[str, object] = {
            "index": self.index,
            "name": self.name,
            "rel_path": self.rel_path,
            "status": self.status,
            "category": self.category,
        }
        if self.origin_rel_path is not None:
            data["origin_rel_path"] = self.origin_rel_path
        return data

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "TimelineEntry":
        status = str(data.get("status", "pending"))
        if status not in TIMELINE_STATUSES:
            status = "pending"
        category = data.get("category")
        name = str(data["name"])
        rel_path = str(data.get("rel_path") or name)
        origin = data.get("origin_rel_path")
        return cls(
            index=max(0, int(data.get("index", 0))),
            name=name,
            rel_path=rel_path,
            status=status,
            category=str(category) if category else None,
            origin_rel_path=str(origin) if origin else None,
        )


@dataclass
class MoveAction:
    """Single reversible file move (initial sort or direct category reassignment)."""

    photo_name: str
    src_path: str
    dest_path: str
    category: str
    prev_index: int
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    prev_status: str = "pending"
    prev_category: Optional[str] = None
    prev_rel_path: Optional[str] = None
    batch_id: Optional[str] = None

    def to_dict(self) -> Dict[str, object]:
        return {
            "photo_name": self.photo_name,
            "src_path": self.src_path,
            "dest_path": self.dest_path,
            "category": self.category,
            "prev_index": self.prev_index,
            "timestamp": self.timestamp,
            "prev_status": self.prev_status,
            "prev_category": self.prev_category,
            "prev_rel_path": self.prev_rel_path,
            "batch_id": self.batch_id,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "MoveAction":
        prev_cat = data.get("prev_category")
        prev_rel = data.get("prev_rel_path")
        batch_id = data.get("batch_id")
        return cls(
            photo_name=str(data["photo_name"]),
            src_path=str(data["src_path"]),
            dest_path=str(data["dest_path"]),
            category=str(data["category"]),
            prev_index=int(data.get("prev_index", 0)),
            timestamp=str(data.get("timestamp", "")),
            prev_status=str(data.get("prev_status", "pending")),
            prev_category=str(prev_cat) if prev_cat else None,
            prev_rel_path=str(prev_rel) if prev_rel else None,
            batch_id=str(batch_id) if batch_id else None,
        )


@dataclass
class SessionState:
    """Persistent state for one media sorting workspace folder."""

    id: str
    name: str
    folder_path: str
    categories: List[str] = field(default_factory=list)
    current_index: int = 0
    current_photo_name: Optional[str] = None
    current_photo_rel_path: Optional[str] = None
    sort_mode: str = "name_asc"
    skipped_files: List[str] = field(default_factory=list)
    undo_stack: List[MoveAction] = field(default_factory=list)
    timeline: List[TimelineEntry] = field(default_factory=list)
    deep_search: bool = False
    category_meta: Dict[str, Dict[str, str]] = field(default_factory=dict)

    @property
    def history(self) -> List[MoveAction]:
        """Alias for undo_stack."""
        return self.undo_stack

    def to_dict(self) -> Dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "folder_path": self.folder_path,
            "categories": list(self.categories),
            "current_index": self.current_index,
            "current_photo_name": self.current_photo_name,
            "current_photo_rel_path": self.current_photo_rel_path,
            "sort_mode": self.sort_mode,
            "skipped_files": list(self.skipped_files),
            "undo_stack": [action.to_dict() for action in self.undo_stack],
            "timeline": [entry.to_dict() for entry in self.timeline],
            "deep_search": self.deep_search,
            "category_meta": {
                k: dict(v) for k, v in self.category_meta.items()
            },
        }

    @classmethod
    def from_dict(cls, data: Dict[str, object]) -> "SessionState":
        raw_actions = data.get("undo_stack") or data.get("history") or []
        raw_timeline = data.get("timeline") or []
        sort_mode = str(data.get("sort_mode", "name_asc"))
        if sort_mode not in SORT_MODES:
            sort_mode = "name_asc"
        raw_meta = data.get("category_meta") or {}
        parsed_meta: Dict[str, Dict[str, str]] = {}
        if isinstance(raw_meta, dict):
            for k, v in raw_meta.items():
                if isinstance(v, dict):
                    parsed_meta[str(k)] = {
                        str(mk): str(mv) for mk, mv in v.items() if mv is not None
                    }
        return cls(
            id=str(data["id"]),
            name=str(data["name"]),
            folder_path=str(data["folder_path"]),
            categories=[str(c) for c in (data.get("categories") or [])],
            current_index=max(0, int(data.get("current_index", 0))),
            current_photo_name=(
                str(data["current_photo_name"])
                if data.get("current_photo_name")
                else None
            ),
            current_photo_rel_path=(
                str(data["current_photo_rel_path"])
                if data.get("current_photo_rel_path")
                else None
            ),
            sort_mode=sort_mode,
            skipped_files=[str(s) for s in (data.get("skipped_files") or [])],
            undo_stack=[MoveAction.from_dict(item) for item in raw_actions],
            timeline=[TimelineEntry.from_dict(item) for item in raw_timeline],
            deep_search=bool(data.get("deep_search", False)),
            category_meta=parsed_meta,
        )


class SessionManager:
    """Coordinates multi-session state, pure navigation, direct reassignment, undo, and settings."""

    def __init__(self, state_file: Optional[str | os.PathLike[str]] = None) -> None:
        self.state_file = (
            Path(state_file) if state_file is not None else default_state_file()
        )
        self.sessions: List[SessionState] = []
        self.active_session_id: Optional[str] = None
        self.settings: AppSettings = AppSettings().normalized()
        self._queues: Dict[str, List[Path]] = {}
        self._cached_counts: Dict[str, Dict[str, int]] = {}
        self.load()

    def _trash_folder_name(self) -> str:
        return self.settings.trash_category.name or TRASH_FOLDER

    def _all_trash_aliases(self) -> List[str]:
        names = [self._trash_folder_name()]
        if TRASH_FOLDER.lower() != names[0].lower():
            names.append(TRASH_FOLDER)
        return names

    def load(self) -> None:
        """Load persisted sessions and application settings from JSON if available."""
        self._queues.clear()
        self._cached_counts.clear()
        if not self.state_file.exists():
            self.sessions = []
            self.active_session_id = None
            self.settings = AppSettings().normalized()
            return
        try:
            payload = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            self.sessions = []
            self.active_session_id = None
            self.settings = AppSettings().normalized()
            return

        self.settings = AppSettings.from_dict(payload.get("settings"))
        self.sessions = [
            SessionState.from_dict(item) for item in (payload.get("sessions") or [])
        ]
        active_id = payload.get("active_session_id")
        if active_id and any(s.id == active_id for s in self.sessions):
            self.active_session_id = str(active_id)
        elif self.sessions:
            self.active_session_id = self.sessions[0].id
        else:
            self.active_session_id = None

    def save(self) -> None:
        """Persist all sessions and app settings atomically to state JSON."""
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "app_name": APP_NAME,
            "app_version": APP_VERSION,
            "active_session_id": self.active_session_id,
            "settings": self.settings.to_dict(),
            "sessions": [session.to_dict() for session in self.sessions],
        }
        tmp_path = self.state_file.with_suffix(self.state_file.suffix + ".tmp")
        tmp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        os.replace(tmp_path, self.state_file)

    def get_active_keybind_slots(self) -> List[str]:
        """Return ordered shortcut keys matching the active keybind profile."""
        prof = self.settings.keybind_profile
        if prof != "default":
            if prof in self.settings.custom_keybind_profiles:
                custom = list(self.settings.custom_keybind_profiles[prof][:9])
            else:
                custom = [k.upper() for k in self.settings.custom_keybinds[:9]]
            tail = [k for k in SHORTCUT_KEYS[9:] if k not in custom]
            return custom + tail
        return list(SHORTCUT_KEYS)

    def _sync_session_category_meta(self, session: SessionState) -> None:
        """Ensure every session category has valid non-conflicting key & color metadata."""
        profile_slots = self.get_active_keybind_slots()
        used_keys: set[str] = {self.settings.trash_category.key.upper()}

        for cat in session.categories:
            meta = session.category_meta.setdefault(cat, {})
            meta["color"] = normalize_color_hex(
                meta.get("color", DEFAULT_CATEGORY_COLOR)
            )
            if meta.get("key_source") == "custom" and meta.get("key"):
                try:
                    valid_k = validate_keybind(meta["key"])
                    if valid_k not in used_keys:
                        meta["key"] = valid_k
                        used_keys.add(valid_k)
                        continue
                except ValueError:
                    pass
                meta.pop("key", None)
                meta["key_source"] = "profile"

        for cat in session.categories:
            meta = session.category_meta.setdefault(cat, {})
            if meta.get("key_source") == "custom" and meta.get("key") in used_keys:
                continue
            existing_k = meta.get("key", "").strip().upper()
            if (
                meta.get("key_source") == "preset"
                and existing_k
                and existing_k not in RESERVED_SYSTEM_KEYS
                and existing_k not in used_keys
            ):
                meta["key"] = existing_k
                used_keys.add(existing_k)
                continue
            assigned = next_available_keybind(used_keys, profile_slots)
            if assigned:
                meta["key"] = assigned
                meta["key_source"] = "profile"
                used_keys.add(assigned)
            else:
                meta.pop("key", None)
                meta["key_source"] = "profile"

        active_set = set(session.categories)
        for cat in list(session.category_meta.keys()):
            if cat not in active_set:
                session.category_meta.pop(cat, None)

    def save_custom_keybind_profile(
        self, name: str, slots: Sequence[str], activate: bool = True
    ) -> str:
        """Validate and store a named 9-slot custom keybind profile."""
        clean_name = str(name or "").strip()
        if not clean_name or clean_name.lower() == "default":
            raise ValueError("Nama profil custom tidak boleh kosong atau 'default'.")
        validated: List[str] = []
        used_map: Dict[str, str] = {}
        for idx in range(9):
            raw_k = slots[idx] if idx < len(slots) else DEFAULT_KEYBIND_SLOTS[idx]
            k = validate_keybind(str(raw_k), existing_map=used_map)
            used_map[f"Slot {idx + 1}"] = k
            validated.append(k)
        self.settings.custom_keybind_profiles[clean_name] = validated
        if activate:
            self.settings.keybind_profile = clean_name
            self.settings.custom_keybinds = list(validated)
        self.settings = self.settings.normalized()
        for session in self.sessions:
            self._sync_session_category_meta(session)
        self.save()
        return clean_name

    def delete_custom_keybind_profile(self, name: str) -> bool:
        """Delete a named custom keybind profile and revert to 'default' if active."""
        if name not in self.settings.custom_keybind_profiles:
            return False
        self.settings.custom_keybind_profiles.pop(name, None)
        if self.settings.keybind_profile == name:
            self.settings.keybind_profile = "default"
        self.settings = self.settings.normalized()
        for session in self.sessions:
            self._sync_session_category_meta(session)
        self.save()
        return True

    def update_settings(
        self,
        *,
        language: Optional[str] = None,
        theme_mode: Optional[str] = None,
        custom_bg_path: Optional[str] = None,
        custom_bg_dim: Optional[int] = None,
        video_autoplay: Optional[bool] = None,
        video_seek_seconds: Optional[int] = None,
        video_arrow_mode: Optional[str] = None,
        keybind_profile: Optional[str] = None,
        custom_keybinds: Optional[List[str]] = None,
        custom_keybind_profiles: Optional[Dict[str, List[str]]] = None,
        trash_category: Optional[DefaultCategorySpec | Dict[str, str]] = None,
        default_categories: Optional[List[DefaultCategorySpec | Dict[str, str]]] = None,
        visible_metadata_fields: Optional[List[str]] = None,
        gallery_open: Optional[bool] = None,
        gallery_width: Optional[int] = None,
    ) -> AppSettings:
        """Update global AppSettings, refresh profile-bound session keys, and save."""
        resolved_trash = self.settings.trash_category
        if trash_category is not None:
            resolved_trash = (
                trash_category
                if isinstance(trash_category, DefaultCategorySpec)
                else DefaultCategorySpec.from_dict(trash_category, is_trash=True)
            )
        updated = AppSettings(
            language=language if language is not None else self.settings.language,
            theme_mode=(
                theme_mode if theme_mode is not None else self.settings.theme_mode
            ),
            custom_bg_path=(
                custom_bg_path
                if custom_bg_path is not None
                else self.settings.custom_bg_path
            ),
            custom_bg_dim=(
                _clamp_dim(custom_bg_dim)
                if custom_bg_dim is not None
                else self.settings.custom_bg_dim
            ),
            video_autoplay=(
                bool(video_autoplay)
                if video_autoplay is not None
                else self.settings.video_autoplay
            ),
            video_seek_seconds=(
                _clamp_seek_seconds(video_seek_seconds)
                if video_seek_seconds is not None
                else self.settings.video_seek_seconds
            ),
            video_arrow_mode=(
                video_arrow_mode
                if video_arrow_mode is not None
                else self.settings.video_arrow_mode
            ),
            keybind_profile=(
                keybind_profile
                if keybind_profile is not None
                else self.settings.keybind_profile
            ),
            custom_keybinds=(
                list(custom_keybinds)
                if custom_keybinds is not None
                else list(self.settings.custom_keybinds)
            ),
            custom_keybind_profiles=(
                {k: list(v) for k, v in custom_keybind_profiles.items()}
                if custom_keybind_profiles is not None
                else {k: list(v) for k, v in self.settings.custom_keybind_profiles.items()}
            ),
            trash_category=resolved_trash,
            default_categories=(
                [
                    c
                    if isinstance(c, DefaultCategorySpec)
                    else DefaultCategorySpec.from_dict(c)
                    for c in default_categories
                ]
                if default_categories is not None
                else list(self.settings.default_categories)
            ),
            visible_metadata_fields=(
                list(visible_metadata_fields)
                if visible_metadata_fields is not None
                else list(self.settings.visible_metadata_fields)
            ),
            gallery_open=(
                bool(gallery_open)
                if gallery_open is not None
                else self.settings.gallery_open
            ),
            gallery_width=(
                _clamp_gallery_width(gallery_width)
                if gallery_width is not None
                else self.settings.gallery_width
            ),
        ).normalized()
        self.settings = updated
        for session in self.sessions:
            self._sync_session_category_meta(session)
        self.save()
        return self.settings

    def get_effective_keybinds(self, session_id: str) -> Dict[str, str]:
        """Return mapping {category: key_str} for all categories in session."""
        session = self.get_session(session_id)
        self._sync_session_category_meta(session)
        result: Dict[str, str] = {}
        for cat in session.categories:
            key = session.category_meta.get(cat, {}).get("key")
            if key:
                result[cat] = key
        return result

    def get_effective_category_colors(self, session_id: str) -> Dict[str, str]:
        """Return mapping {category: '#RRGGBB'} for all categories in session."""
        session = self.get_session(session_id)
        return {
            cat: normalize_color_hex(
                session.category_meta.get(cat, {}).get("color", DEFAULT_CATEGORY_COLOR)
            )
            for cat in session.categories
        }

    def set_category_keybind(
        self, session_id: str, category: str, new_key: str
    ) -> str:
        """Assign a validated non-conflicting shortcut key to a session category."""
        session = self.get_session(session_id)
        actual = next(
            (c for c in session.categories if c.lower() == category.lower()),
            None,
        )
        if actual is None:
            raise KeyError(f"Kategori tidak ditemukan: {category}")
        current_map = self.get_effective_keybinds(session_id)
        current_map[self._trash_folder_name()] = self.settings.trash_category.key
        validated = validate_keybind(
            new_key, existing_map=current_map, ignore_category=actual
        )
        meta = session.category_meta.setdefault(actual, {})
        meta["key"] = validated
        meta["key_source"] = "custom"
        meta.setdefault("color", DEFAULT_CATEGORY_COLOR)
        self.save()
        return validated

    def set_category_color(
        self, session_id: str, category: str, color_hex: str
    ) -> str:
        """Assign a hex accent color to a session category."""
        session = self.get_session(session_id)
        actual = next(
            (c for c in session.categories if c.lower() == category.lower()),
            None,
        )
        if actual is None:
            raise KeyError(f"Kategori tidak ditemukan: {category}")
        if not _HEX_COLOR_RE.fullmatch((color_hex or "").strip()):
            raise ValueError(f"Kode warna tidak valid: {color_hex}")
        normalized = normalize_color_hex(color_hex)
        meta = session.category_meta.setdefault(actual, {})
        meta["color"] = normalized
        self.save()
        return normalized

    def get_session(self, session_id: str) -> SessionState:
        """Return session by id or raise KeyError."""
        for session in self.sessions:
            if session.id == session_id:
                return session
        raise KeyError(f"Session not found: {session_id}")

    def get_active_session(self) -> Optional[SessionState]:
        """Return currently active session if any."""
        if not self.active_session_id:
            return None
        for session in self.sessions:
            if session.id == self.active_session_id:
                return session
        return None

    def find_session_by_folder(
        self, folder_path: str | os.PathLike[str]
    ) -> Optional[SessionState]:
        """Return an existing SessionState matching normalized folder_path, if any."""
        target_norm = normalize_folder_path(os.path.abspath(str(folder_path)))
        for session in self.sessions:
            if normalize_folder_path(session.folder_path) == target_norm:
                return session
        return None

    def set_active_session(self, session_id: str) -> SessionState:
        """Activate an existing session, resyncing its timeline."""
        session = self.get_session(session_id)
        self.active_session_id = session.id
        self.sync_session(session.id)
        self.save()
        return session

    def get_timeline(self, session_id: str) -> List[TimelineEntry]:
        """Return fixed timeline entries, syncing from disk if not yet loaded."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        return session.timeline

    def get_queue(self, session_id: str) -> List[Path]:
        """Return cached list of unsorted media Paths, syncing if not yet loaded."""
        if session_id not in self._queues:
            return self.sync_session(session_id)
        return self._queues[session_id]

    def is_session_complete(self, session_id: str) -> bool:
        """Return True when zero unsorted items remain in the session."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        return all(entry.status == "sorted" for entry in session.timeline)

    def get_current_photo(self, session_id: str) -> Optional[Path]:
        """Return active timeline item's Path (supports viewing already-sorted items) or None if complete."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        if not session.timeline:
            return None
        if all(entry.status == "sorted" for entry in session.timeline):
            return None
        idx = max(0, min(session.current_index, len(session.timeline) - 1))
        entry = session.timeline[idx]
        return Path(session.folder_path) / entry.rel_path

    def detect_existing_categories(self, folder_path: str | os.PathLike[str]) -> List[str]:
        return detect_existing_categories(folder_path, trash_folder=self._trash_folder_name())

    def has_subfolders(self, folder_path: str | os.PathLike[str]) -> bool:
        return has_subfolders(folder_path, trash_folder=self._trash_folder_name())

    @staticmethod
    def sanitize_category_name(name: str) -> str:
        return sanitize_category_name(name)

    def _merge_categories(
        self, existing: List[str], additions: Optional[List[str]]
    ) -> List[str]:
        seen = {cat.lower() for cat in existing}
        merged = list(existing)
        for raw in additions or []:
            cleaned = sanitize_category_name(raw)
            if cleaned.lower() not in seen:
                seen.add(cleaned.lower())
                merged.append(cleaned)
        return merged

    def _apply_incoming_category_meta(
        self,
        session: SessionState,
        incoming_meta: Optional[Dict[str, Dict[str, str]]],
    ) -> None:
        if incoming_meta:
            for raw_cat, meta in incoming_meta.items():
                matched = next(
                    (c for c in session.categories if c.lower() == raw_cat.lower()),
                    None,
                )
                if matched is None or not isinstance(meta, dict):
                    continue
                target = session.category_meta.setdefault(matched, {})
                if meta.get("color"):
                    target["color"] = normalize_color_hex(str(meta["color"]))
                if meta.get("key"):
                    try:
                        target["key"] = validate_keybind(str(meta["key"]))
                        target["key_source"] = str(
                            meta.get("key_source") or "custom"
                        )
                    except ValueError:
                        pass
        self._sync_session_category_meta(session)

    def create_or_get_session(
        self,
        folder_path: str | os.PathLike[str],
        name: Optional[str] = None,
        categories: Optional[List[str]] = None,
        deep_search: bool = False,
        category_meta: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Tuple[SessionState, bool]:
        """Create a new session or focus existing one matching the normalized folder."""
        abs_folder = os.path.abspath(str(folder_path))
        if not Path(abs_folder).is_dir():
            raise FileNotFoundError(f"Folder tidak ditemukan: {abs_folder}")

        target_norm = normalize_folder_path(abs_folder)
        for existing in self.sessions:
            if normalize_folder_path(existing.folder_path) == target_norm:
                if categories:
                    existing.categories = self._merge_categories(
                        existing.categories, categories
                    )
                if deep_search and not existing.deep_search:
                    existing.deep_search = True
                self._apply_incoming_category_meta(existing, category_meta)
                self.active_session_id = existing.id
                self.sync_session(existing.id)
                self.save()
                return existing, True

        resolved_name = (name or "").strip() or Path(abs_folder).name or abs_folder
        merged_cats = self._merge_categories([], categories)
        session = SessionState(
            id=uuid.uuid4().hex[:12],
            name=resolved_name,
            folder_path=abs_folder,
            categories=merged_cats,
            deep_search=bool(deep_search),
        )
        self._apply_incoming_category_meta(session, category_meta)
        self.sessions.append(session)
        self.active_session_id = session.id
        self.sync_session(session.id)
        self.save()
        return session, False

    def create_or_focus_session(
        self,
        folder_path: str | os.PathLike[str],
        name: Optional[str] = None,
        categories: Optional[List[str]] = None,
        deep_search: bool = False,
        category_meta: Optional[Dict[str, Dict[str, str]]] = None,
    ) -> Tuple[SessionState, bool]:
        """Alias for create_or_get_session."""
        return self.create_or_get_session(
            folder_path,
            name=name,
            categories=categories,
            deep_search=deep_search,
            category_meta=category_meta,
        )

    @staticmethod
    def _safe_mtime(path: Path) -> float:
        try:
            return path.stat().st_mtime
        except OSError:
            return 0.0

    @staticmethod
    def _safe_size(path: Path) -> int:
        try:
            return int(path.stat().st_size)
        except OSError:
            return 0

    def _scan_session_files(self, root: Path, deep_search: bool) -> List[Path]:
        if not root.is_dir():
            return []
        if not deep_search:
            return [
                entry
                for entry in root.iterdir()
                if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTS
            ]
        trash_lower = {t.lower() for t in self._all_trash_aliases()}
        found: List[Path] = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [
                d
                for d in dirnames
                if not d.startswith(".") and d.lower() not in trash_lower
            ]
            base = Path(dirpath)
            for fname in filenames:
                p = base / fname
                if p.suffix.lower() in SUPPORTED_EXTS:
                    found.append(p)
        return found

    def _sort_paths(self, paths: List[Path], sort_mode: str) -> List[Path]:
        mode = sort_mode if sort_mode in SORT_MODES else "name_asc"
        ordered = list(paths)
        if mode == "name_asc":
            ordered.sort(key=lambda p: (natural_sort_key(p.name), natural_sort_key(str(p))))
        elif mode == "name_desc":
            ordered.sort(
                key=lambda p: (natural_sort_key(p.name), natural_sort_key(str(p))),
                reverse=True,
            )
        elif mode == "date_asc":
            ordered.sort(
                key=lambda p: (self._safe_mtime(p), natural_sort_key(p.name), natural_sort_key(str(p)))
            )
        elif mode == "date_desc":
            ordered.sort(
                key=lambda p: (-self._safe_mtime(p), natural_sort_key(p.name), natural_sort_key(str(p)))
            )
        elif mode == "size_desc":
            ordered.sort(
                key=lambda p: (-self._safe_size(p), natural_sort_key(p.name), natural_sort_key(str(p)))
            )
        elif mode == "size_asc":
            ordered.sort(
                key=lambda p: (self._safe_size(p), natural_sort_key(p.name), natural_sort_key(str(p)))
            )
        return ordered

    def _sort_timeline_entries(
        self, root: Path, entries: List[TimelineEntry], sort_mode: str
    ) -> List[TimelineEntry]:
        mode = sort_mode if sort_mode in SORT_MODES else "name_asc"
        ordered = list(entries)
        if mode == "name_asc":
            ordered.sort(key=lambda e: (natural_sort_key(e.name), natural_sort_key(e.rel_path)))
        elif mode == "name_desc":
            ordered.sort(
                key=lambda e: (natural_sort_key(e.name), natural_sort_key(e.rel_path)),
                reverse=True,
            )
        elif mode == "date_asc":
            ordered.sort(
                key=lambda e: (
                    self._safe_mtime(root / e.rel_path),
                    natural_sort_key(e.name),
                    natural_sort_key(e.rel_path),
                )
            )
        elif mode == "date_desc":
            ordered.sort(
                key=lambda e: (
                    -self._safe_mtime(root / e.rel_path),
                    natural_sort_key(e.name),
                    natural_sort_key(e.rel_path),
                )
            )
        elif mode == "size_desc":
            ordered.sort(
                key=lambda e: (
                    -self._safe_size(root / e.rel_path),
                    natural_sort_key(e.name),
                    natural_sort_key(e.rel_path),
                )
            )
        elif mode == "size_asc":
            ordered.sort(
                key=lambda e: (
                    self._safe_size(root / e.rel_path),
                    natural_sort_key(e.name),
                    natural_sort_key(e.rel_path),
                )
            )
        for idx, entry in enumerate(ordered):
            entry.index = idx
        return ordered

    @staticmethod
    def _find_next_unsorted_index(
        timeline: List[TimelineEntry], start_after: int
    ) -> Optional[int]:
        """Find next index after start_after with status != 'sorted', wrapping around."""
        n = len(timeline)
        if n == 0:
            return None
        for offset in range(1, n + 1):
            idx = (start_after + offset) % n
            if timeline[idx].status != "sorted":
                return idx
        return None

    def _rebuild_in_memory_queue(self, session: SessionState) -> List[Path]:
        root = Path(session.folder_path)
        queue = [
            root / entry.rel_path
            for entry in session.timeline
            if entry.status != "sorted"
        ]
        self._queues[session.id] = queue
        return queue

    def _ensure_cached_counts(self, session_id: str) -> Dict[str, int]:
        if session_id not in self._cached_counts:
            return self.get_category_counts(session_id)
        return self._cached_counts[session_id]

    def _pop_matching_undo(
        self, session: SessionState, entry: TimelineEntry, root: Path
    ) -> Optional[MoveAction]:
        target_rel = entry.rel_path.replace("\\", "/")
        for i in range(len(session.undo_stack) - 1, -1, -1):
            act = session.undo_stack[i]
            dest_p = Path(act.dest_path)
            try:
                act_rel = dest_p.relative_to(root).as_posix()
            except ValueError:
                act_rel = f"{act.category}/{dest_p.name}"
            if act_rel == target_rel or (
                dest_p.name == entry.name and act.category == entry.category
            ):
                return session.undo_stack.pop(i)
        return None

    def _restore_origin_destination(
        self, root: Path, entry: TimelineEntry, orig_name: str
    ) -> Tuple[Path, str]:
        """Compute collision-safe destination path honoring origin_rel_path when set."""
        if entry.origin_rel_path:
            origin_parent_rel = Path(entry.origin_rel_path).parent
            dest_dir = (
                root / origin_parent_rel
                if str(origin_parent_rel) not in ("", ".")
                else root
            )
        else:
            dest_dir = root
        dest_dir.mkdir(parents=True, exist_ok=True)
        restored_path = unique_dest_path(dest_dir, orig_name)
        rel_str = restored_path.relative_to(root).as_posix()
        return restored_path, rel_str

    def sync_session(self, session_id: str) -> List[Path]:
        """Build or reconcile fixed timeline against disk, compute cached counts, and maintain focus."""
        session = self.get_session(session_id)
        root = Path(session.folder_path)
        disk_files = self._scan_session_files(root, session.deep_search)

        if not session.timeline:
            ordered_disk = self._sort_paths(disk_files, session.sort_mode)
            skipped_set = set(session.skipped_files)
            timeline: List[TimelineEntry] = []

            migrated_sorted: List[TimelineEntry] = []
            migrated_norms: set[str] = set()
            for action in session.undo_stack:
                dest_p = Path(action.dest_path)
                if dest_p.is_file():
                    migrated_norms.add(normalize_folder_path(dest_p))
                    try:
                        rel_str = dest_p.relative_to(root).as_posix()
                    except ValueError:
                        rel_str = f"{action.category}/{dest_p.name}"
                    migrated_sorted.append(
                        TimelineEntry(
                            index=0,
                            name=dest_p.name,
                            rel_path=rel_str,
                            status="sorted",
                            category=action.category,
                        )
                    )

            for p in ordered_disk:
                if normalize_folder_path(p) in migrated_norms:
                    continue
                rel_str = p.relative_to(root).as_posix()
                status = "skipped" if p.name in skipped_set else "pending"
                timeline.append(
                    TimelineEntry(
                        index=0,
                        name=p.name,
                        rel_path=rel_str,
                        status=status,
                        category=None,
                        origin_rel_path=rel_str,
                    )
                )

            if migrated_sorted:
                timeline = self._sort_timeline_entries(
                    root, [*migrated_sorted, *timeline], session.sort_mode
                )
            else:
                for idx, entry in enumerate(timeline):
                    entry.index = idx
            session.timeline = timeline
        else:
            claimed_norms: set[str] = set()
            reconciled: List[TimelineEntry] = []

            for entry in session.timeline:
                if entry.status == "sorted":
                    rel_p = root / entry.rel_path
                    if rel_p.is_file():
                        claimed_norms.add(normalize_folder_path(rel_p))

            for entry in session.timeline:
                rel_p = root / entry.rel_path
                origin_candidate = root / (entry.origin_rel_path or entry.name)

                if entry.status == "sorted":
                    if rel_p.is_file():
                        claimed_norms.add(normalize_folder_path(rel_p))
                        reconciled.append(entry)
                    elif origin_candidate.is_file() and (
                        session.deep_search or origin_candidate.parent == root
                    ):
                        entry.rel_path = origin_candidate.relative_to(root).as_posix()
                        entry.name = origin_candidate.name
                        entry.status = "pending"
                        entry.category = None
                        claimed_norms.add(normalize_folder_path(origin_candidate))
                        reconciled.append(entry)
                    else:
                        reconciled.append(entry)
                else:
                    if rel_p.is_file() and (
                        session.deep_search or rel_p.parent == root
                    ):
                        claimed_norms.add(normalize_folder_path(rel_p))
                        reconciled.append(entry)
                    elif origin_candidate.is_file() and (
                        session.deep_search or origin_candidate.parent == root
                    ):
                        entry.rel_path = origin_candidate.relative_to(root).as_posix()
                        entry.name = origin_candidate.name
                        claimed_norms.add(normalize_folder_path(origin_candidate))
                        reconciled.append(entry)
                    else:
                        found_cat: Optional[Tuple[str, Path]] = None
                        for cat in [*session.categories, *self._all_trash_aliases()]:
                            cat_p = root / cat / entry.name
                            if (
                                cat_p.is_file()
                                and normalize_folder_path(cat_p) not in claimed_norms
                            ):
                                found_cat = (cat, cat_p)
                                break
                        if found_cat is not None:
                            cat_name, cat_p = found_cat
                            entry.status = "sorted"
                            entry.category = cat_name
                            entry.rel_path = f"{cat_name}/{cat_p.name}"
                            claimed_norms.add(normalize_folder_path(cat_p))
                            reconciled.append(entry)

            new_disk_files = [
                p
                for p in disk_files
                if normalize_folder_path(p) not in claimed_norms
            ]
            if new_disk_files:
                for p in self._sort_paths(new_disk_files, session.sort_mode):
                    rel_str = p.relative_to(root).as_posix()
                    reconciled.append(
                        TimelineEntry(
                            index=0,
                            name=p.name,
                            rel_path=rel_str,
                            status="pending",
                            category=None,
                            origin_rel_path=rel_str,
                        )
                    )

            for idx, entry in enumerate(reconciled):
                entry.index = idx
            session.timeline = reconciled

        skipped_names = {
            e.name for e in session.timeline if e.status == "skipped"
        }
        session.skipped_files = [s for s in session.skipped_files if s in skipped_names]

        unsorted_entries = [e for e in session.timeline if e.status != "sorted"]
        if not session.timeline:
            session.current_index = 0
            session.current_photo_name = None
            session.current_photo_rel_path = None
        elif not unsorted_entries:
            session.current_index = max(
                0, min(session.current_index, len(session.timeline) - 1)
            )
            cur_entry = session.timeline[session.current_index]
            session.current_photo_name = cur_entry.name
            session.current_photo_rel_path = cur_entry.rel_path
        else:
            cur_idx = max(0, min(session.current_index, len(session.timeline) - 1))
            cur_entry = session.timeline[cur_idx]
            matches_current = (
                cur_entry.rel_path == session.current_photo_rel_path
                if session.current_photo_rel_path
                else (
                    not session.current_photo_name
                    or cur_entry.name == session.current_photo_name
                )
            )
            if matches_current and (root / cur_entry.rel_path).is_file():
                session.current_index = cur_idx
                session.current_photo_name = cur_entry.name
                session.current_photo_rel_path = cur_entry.rel_path
            elif session.current_photo_rel_path or session.current_photo_name:
                matched_idx = None
                if session.current_photo_rel_path:
                    matched_idx = next(
                        (
                            e.index
                            for e in session.timeline
                            if e.rel_path == session.current_photo_rel_path
                            and (root / e.rel_path).is_file()
                        ),
                        None,
                    )
                if matched_idx is None and session.current_photo_name and not session.current_photo_rel_path:
                    matched_idx = next(
                        (
                            e.index
                            for e in unsorted_entries
                            if e.name == session.current_photo_name
                        ),
                        None,
                    )
                if matched_idx is not None:
                    session.current_index = matched_idx
                    session.current_photo_name = session.timeline[matched_idx].name
                    session.current_photo_rel_path = session.timeline[matched_idx].rel_path
                else:
                    next_idx = (
                        cur_idx
                        if cur_entry.status != "sorted"
                        else self._find_next_unsorted_index(session.timeline, cur_idx)
                    )
                    session.current_index = next_idx if next_idx is not None else 0
                    chosen_entry = session.timeline[session.current_index]
                    session.current_photo_name = chosen_entry.name
                    session.current_photo_rel_path = chosen_entry.rel_path
            else:
                next_idx = (
                    cur_idx
                    if cur_entry.status != "sorted"
                    else self._find_next_unsorted_index(session.timeline, cur_idx)
                )
                session.current_index = next_idx if next_idx is not None else 0
                chosen_entry = session.timeline[session.current_index]
                session.current_photo_name = chosen_entry.name
                session.current_photo_rel_path = chosen_entry.rel_path

        self._sync_session_category_meta(session)
        self.get_category_counts(session_id)
        return self._rebuild_in_memory_queue(session)

    def set_sort_mode(self, session_id: str, sort_mode: str) -> List[Path]:
        """Re-sort timeline, reassign indices, preserve focused item, and save."""
        if sort_mode not in SORT_MODES:
            raise ValueError(f"Mode urutan tidak valid: {sort_mode}")
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)

        focused_rel = (
            session.timeline[session.current_index].rel_path
            if session.timeline
            and 0 <= session.current_index < len(session.timeline)
            else None
        )
        focused_name = session.current_photo_name
        if (
            not focused_name
            and session.timeline
            and 0 <= session.current_index < len(session.timeline)
        ):
            focused_name = session.timeline[session.current_index].name

        session.sort_mode = sort_mode
        root = Path(session.folder_path)
        session.timeline = self._sort_timeline_entries(
            root, session.timeline, sort_mode
        )

        if session.timeline:
            match = None
            if focused_rel:
                match = next(
                    (e.index for e in session.timeline if e.rel_path == focused_rel),
                    None,
                )
            if match is None and focused_name:
                match = next(
                    (e.index for e in session.timeline if e.name == focused_name),
                    None,
                )
            if match is not None:
                session.current_index = match
                session.current_photo_name = session.timeline[match].name
                session.current_photo_rel_path = session.timeline[match].rel_path
            else:
                session.current_index = 0
                session.current_photo_name = session.timeline[0].name
                session.current_photo_rel_path = session.timeline[0].rel_path
        else:
            session.current_index = 0
            session.current_photo_name = None
            session.current_photo_rel_path = None

        queue = self._rebuild_in_memory_queue(session)
        self.save()
        return queue

    def skip_photo(self, session_id: str) -> Optional[TimelineEntry]:
        """Pure forward navigation: leave sorted items untouched, mark pending skipped, step +1."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        if not session.timeline:
            return None
        if all(e.status == "sorted" for e in session.timeline):
            return None

        cur_idx = max(0, min(session.current_index, len(session.timeline) - 1))
        cur_entry = session.timeline[cur_idx]
        if cur_entry.status == "pending":
            cur_entry.status = "skipped"
            if cur_entry.name not in session.skipped_files:
                session.skipped_files.append(cur_entry.name)

        if cur_idx + 1 < len(session.timeline):
            next_idx: Optional[int] = cur_idx + 1
        else:
            next_idx = self._find_next_unsorted_index(session.timeline, cur_idx)
        if next_idx is None:
            return None
        session.current_index = next_idx
        session.current_photo_name = session.timeline[next_idx].name
        session.current_photo_rel_path = session.timeline[next_idx].rel_path
        self.save()
        return session.timeline[next_idx]

    def next_photo(self, session_id: str) -> Optional[TimelineEntry]:
        """Alias for pure forward navigation (skip_photo)."""
        return self.skip_photo(session_id)

    def prev_photo(self, session_id: str) -> Optional[TimelineEntry]:
        """Pure backward navigation: move 1 slot back (clamped at 0) without undoing moves."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        if not session.timeline or session.current_index <= 0:
            return None

        target_idx = min(session.current_index - 1, len(session.timeline) - 1)
        entry = session.timeline[target_idx]
        session.current_index = target_idx
        session.current_photo_name = entry.name
        session.current_photo_rel_path = entry.rel_path
        self.save()
        return entry

    def step_back(self, session_id: str) -> Optional[TimelineEntry]:
        """Alias for pure prev_photo navigation."""
        return self.prev_photo(session_id)

    def _restore_single_action(
        self,
        session: SessionState,
        action: MoveAction,
        root: Path,
        counts: Dict[str, int],
    ) -> Tuple[Optional[TimelineEntry], bool]:
        """Restore a single popped MoveAction. Returns (entry, was_missing_on_disk)."""
        dest_p = Path(action.dest_path)
        try:
            action_rel = dest_p.relative_to(root).as_posix()
        except ValueError:
            action_rel = f"{action.category}/{dest_p.name}"

        entry: Optional[TimelineEntry] = None
        if 0 <= action.prev_index < len(session.timeline):
            cand = session.timeline[action.prev_index]
            if cand.rel_path == action_rel or cand.name == dest_p.name:
                entry = cand
        if entry is None:
            entry = next(
                (e for e in session.timeline if e.rel_path == action_rel),
                None,
            )
        if entry is None:
            entry = next(
                (
                    e
                    for e in session.timeline
                    if e.name == dest_p.name and e.category == action.category
                ),
                None,
            )

        moved_file = dest_p
        if not moved_file.exists() and entry is not None:
            moved_file = root / entry.rel_path

        if not moved_file.exists():
            if action.category in counts:
                counts[action.category] = max(0, counts[action.category] - 1)
            if entry is not None and entry in session.timeline:
                removed_idx = entry.index
                session.timeline.remove(entry)
                for idx, item in enumerate(session.timeline):
                    item.index = idx
                if session.timeline:
                    new_idx = max(0, min(removed_idx, len(session.timeline) - 1))
                    session.current_index = new_idx
                    session.current_photo_name = session.timeline[new_idx].name
                    session.current_photo_rel_path = session.timeline[new_idx].rel_path
                else:
                    session.current_index = 0
                    session.current_photo_name = None
                    session.current_photo_rel_path = None
            return None, True

        if entry is None:
            entry = TimelineEntry(
                index=max(0, min(action.prev_index, len(session.timeline))),
                name=dest_p.name,
                rel_path=action_rel,
                status="sorted",
                category=action.category,
            )
            session.timeline.insert(entry.index, entry)
            for idx, item in enumerate(session.timeline):
                item.index = idx

        if action.prev_status == "sorted" and action.prev_category:
            prev_cat = action.prev_category
            prev_dir = root / prev_cat
            prev_dir.mkdir(parents=True, exist_ok=True)
            restore_name = (
                Path(action.prev_rel_path).name
                if action.prev_rel_path
                else action.photo_name
            )
            restored_path = unique_dest_path(prev_dir, restore_name)
            _move_file_safe(moved_file, restored_path)
            if action.category in counts:
                counts[action.category] = max(0, counts[action.category] - 1)
            counts[prev_cat] = counts.get(prev_cat, 0) + 1
            entry.name = restored_path.name
            entry.rel_path = f"{prev_cat}/{restored_path.name}"
            entry.status = "sorted"
            entry.category = prev_cat
        else:
            restored_path, rel_str = self._restore_origin_destination(
                root, entry, action.photo_name or entry.name
            )
            _move_file_safe(moved_file, restored_path)
            if action.category in counts:
                counts[action.category] = max(0, counts[action.category] - 1)
            entry.name = restored_path.name
            entry.rel_path = rel_str
            entry.status = "pending"
            entry.category = None

        return entry, False

    def undo_last_batch(self, session_id: str) -> List[TimelineEntry]:
        """Pop latest MoveAction or contiguous batch_id actions in reverse order."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        if not session.undo_stack:
            return []

        root = Path(session.folder_path)
        counts = self._ensure_cached_counts(session_id)

        top = session.undo_stack.pop()
        batch: List[MoveAction] = [top]
        if top.batch_id:
            bid = top.batch_id
            while session.undo_stack and session.undo_stack[-1].batch_id == bid:
                batch.append(session.undo_stack.pop())

        restored: List[TimelineEntry] = []
        first_missing: Optional[FileNotFoundError] = None
        for act in batch:
            entry, missing = self._restore_single_action(session, act, root, counts)
            if missing and first_missing is None:
                first_missing = FileNotFoundError(
                    f"Entri undo dihapus karena file tidak lagi ditemukan di disk: {act.dest_path}"
                )
            elif entry is not None:
                restored.append(entry)

        if restored:
            restored.sort(key=lambda e: e.index)
            earliest = restored[0]
            session.current_index = earliest.index
            session.current_photo_name = earliest.name
            session.current_photo_rel_path = earliest.rel_path

        self._rebuild_in_memory_queue(session)
        self.save()
        if first_missing is not None:
            raise first_missing
        return restored

    def undo_last_action(self, session_id: str) -> Optional[TimelineEntry]:
        """Undo the latest MoveAction or atomic batch, returning earliest restored entry."""
        restored = self.undo_last_batch(session_id)
        return restored[0] if restored else None

    def navigate(self, session_id: str, delta: int) -> Optional[TimelineEntry]:
        """Step forward (skip_photo) or backward (prev_photo) along fixed timeline."""
        if delta > 0:
            return self.skip_photo(session_id)
        if delta < 0:
            return self.prev_photo(session_id)
        session = self.get_session(session_id)
        if not session.timeline:
            return None
        idx = max(0, min(session.current_index, len(session.timeline) - 1))
        return session.timeline[idx]

    def jump_to(self, session_id: str, index: int) -> Optional[Path]:
        """Pure focus jump to timeline index without reverting sorted files."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        if not session.timeline:
            return None

        idx = max(0, min(index, len(session.timeline) - 1))
        entry = session.timeline[idx]
        root = Path(session.folder_path)

        session.current_index = idx
        session.current_photo_name = entry.name
        session.current_photo_rel_path = entry.rel_path
        self.save()
        return root / entry.rel_path

    def _resolve_target_category(self, session: SessionState, target_category: str) -> str:
        trash_name = self._trash_folder_name()
        if target_category.lower() in (TRASH_FOLDER.lower(), trash_name.lower()):
            return trash_name
        folder_name = sanitize_category_name(target_category)
        if not any(c.lower() == folder_name.lower() for c in session.categories):
            session.categories.append(folder_name)
            self._sync_session_category_meta(session)
            return folder_name
        return next(
            c for c in session.categories if c.lower() == folder_name.lower()
        )

    def move_current_photo(
        self, session_id: str, target_category: str
    ) -> MoveAction:
        """O(1) instant media move or direct category reassignment without disk scan."""
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        if not session.timeline:
            raise ValueError("Tidak ada media tersisa untuk dipindahkan.")

        folder_name = self._resolve_target_category(session, target_category)

        old_index = max(0, min(session.current_index, len(session.timeline) - 1))
        entry = session.timeline[old_index]
        if entry.origin_rel_path is None:
            entry.origin_rel_path = entry.rel_path
        root = Path(session.folder_path)
        was_sorted = entry.status == "sorted"
        old_cat = entry.category
        old_rel = entry.rel_path
        old_status = entry.status

        # Same-category click on already-sorted item advances cleanly without duplicating file
        if was_sorted and old_cat and old_cat.lower() == folder_name.lower():
            next_idx = (
                old_index + 1
                if old_index + 1 < len(session.timeline)
                else self._find_next_unsorted_index(session.timeline, old_index)
            )
            if next_idx is not None:
                session.current_index = next_idx
                session.current_photo_name = session.timeline[next_idx].name
                session.current_photo_rel_path = session.timeline[next_idx].rel_path
                self.save()
            return MoveAction(
                photo_name=entry.name,
                src_path=str(root / old_rel),
                dest_path=str(root / old_rel),
                category=folder_name,
                prev_index=old_index,
                prev_status=old_status,
                prev_category=old_cat,
                prev_rel_path=old_rel,
            )

        src_path = root / old_rel
        dest_dir = root / folder_name
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest_path = unique_dest_path(dest_dir, src_path.name)

        counts = self._ensure_cached_counts(session_id)
        _move_file_safe(src_path, dest_path)

        if was_sorted and old_cat:
            counts[old_cat] = max(0, counts.get(old_cat, 0) - 1)
        counts[folder_name] = counts.get(folder_name, 0) + 1

        orig_name = entry.name
        entry.name = dest_path.name
        entry.rel_path = f"{folder_name}/{dest_path.name}"
        entry.status = "sorted"
        entry.category = folder_name

        action = MoveAction(
            photo_name=orig_name,
            src_path=str(src_path),
            dest_path=str(dest_path),
            category=folder_name,
            prev_index=old_index,
            prev_status=old_status,
            prev_category=old_cat,
            prev_rel_path=old_rel,
        )
        session.undo_stack.append(action)
        if orig_name in session.skipped_files:
            session.skipped_files.remove(orig_name)

        next_idx: Optional[int] = None
        if not all(e.status == "sorted" for e in session.timeline):
            if was_sorted:
                if old_index + 1 < len(session.timeline):
                    next_idx = old_index + 1
                else:
                    next_idx = self._find_next_unsorted_index(session.timeline, old_index)
            else:
                next_idx = self._find_next_unsorted_index(session.timeline, old_index)

        if next_idx is not None:
            session.current_index = next_idx
            session.current_photo_name = session.timeline[next_idx].name
            session.current_photo_rel_path = session.timeline[next_idx].rel_path
        else:
            session.current_index = old_index
            session.current_photo_name = entry.name
            session.current_photo_rel_path = entry.rel_path

        self._rebuild_in_memory_queue(session)
        self.save()
        return action

    def bulk_move_photos(
        self,
        session_id: str,
        indices: Sequence[int],
        target_category: str,
    ) -> Tuple[List[MoveAction], Optional[Exception]]:
        """Move selected timeline items into target_category as a single atomic undo batch.

        Commits every completed move (1..k-1) on mid-batch failure and returns
        ``(moved_actions, error)`` so callers can preserve failed items for retry.
        """
        session = self.get_session(session_id)
        if session_id not in self._queues:
            self.sync_session(session_id)
        if not session.timeline:
            raise ValueError("Tidak ada media tersisa untuk dipindahkan.")

        ordered_indices = sorted(
            {int(i) for i in indices if 0 <= int(i) < len(session.timeline)}
        )
        if not ordered_indices:
            return [], None

        folder_name = self._resolve_target_category(session, target_category)
        root = Path(session.folder_path)
        counts = self._ensure_cached_counts(session_id)
        dest_dir = root / folder_name
        dest_dir.mkdir(parents=True, exist_ok=True)

        batch_id = uuid.uuid4().hex[:12]
        moved_actions: List[MoveAction] = []
        last_moved_idx: Optional[int] = None
        failed_idx: Optional[int] = None
        error: Optional[Exception] = None

        for idx in ordered_indices:
            entry = session.timeline[idx]
            was_sorted = entry.status == "sorted"
            old_cat = entry.category
            if was_sorted and old_cat and old_cat.lower() == folder_name.lower():
                continue

            if entry.origin_rel_path is None:
                entry.origin_rel_path = entry.rel_path
            old_rel = entry.rel_path
            old_status = entry.status
            src_path = root / old_rel
            dest_path = unique_dest_path(dest_dir, src_path.name)

            try:
                _move_file_safe(src_path, dest_path)
            except Exception as exc:
                error = exc
                failed_idx = idx
                break

            if was_sorted and old_cat:
                counts[old_cat] = max(0, counts.get(old_cat, 0) - 1)
            counts[folder_name] = counts.get(folder_name, 0) + 1

            orig_name = entry.name
            entry.name = dest_path.name
            entry.rel_path = f"{folder_name}/{dest_path.name}"
            entry.status = "sorted"
            entry.category = folder_name

            action = MoveAction(
                photo_name=orig_name,
                src_path=str(src_path),
                dest_path=str(dest_path),
                category=folder_name,
                prev_index=idx,
                prev_status=old_status,
                prev_category=old_cat,
                prev_rel_path=old_rel,
                batch_id=batch_id,
            )
            session.undo_stack.append(action)
            moved_actions.append(action)
            last_moved_idx = idx
            if orig_name in session.skipped_files:
                session.skipped_files.remove(orig_name)

        max_anchor = max(ordered_indices)
        next_idx: Optional[int] = None
        if error is not None and failed_idx is not None:
            next_idx = failed_idx
        elif not all(e.status == "sorted" for e in session.timeline):
            next_idx = self._find_next_unsorted_index(session.timeline, max_anchor)
        elif last_moved_idx is not None:
            next_idx = last_moved_idx
        else:
            next_idx = max_anchor

        if next_idx is not None and 0 <= next_idx < len(session.timeline):
            session.current_index = next_idx
            session.current_photo_name = session.timeline[next_idx].name
            session.current_photo_rel_path = session.timeline[next_idx].rel_path

        self._rebuild_in_memory_queue(session)
        self.save()
        return moved_actions, error

    def add_category(
        self,
        session_id: str,
        name: str,
        key: Optional[str] = None,
        color: Optional[str] = None,
    ) -> bool:
        """Validate and append a new category to the session."""
        session = self.get_session(session_id)
        cleaned = sanitize_category_name(name)
        if any(c.lower() == cleaned.lower() for c in session.categories):
            return False
        session.categories.append(cleaned)
        meta = session.category_meta.setdefault(cleaned, {})
        if color:
            meta["color"] = normalize_color_hex(color)
        if key:
            existing_map = self.get_effective_keybinds(session_id)
            existing_map[self._trash_folder_name()] = self.settings.trash_category.key
            validated = validate_keybind(key, existing_map=existing_map)
            meta["key"] = validated
            meta["key_source"] = "custom"
        self._sync_session_category_meta(session)
        if session_id in self._cached_counts:
            self._cached_counts[session_id].setdefault(cleaned, 0)
        self.save()
        return True

    def rename_category(
        self, session_id: str, old_name: str, new_name: str
    ) -> bool:
        """Rename a category, disk subfolder, metadata, undo_stack, and timeline entries."""
        session = self.get_session(session_id)
        try:
            idx = next(
                i
                for i, cat in enumerate(session.categories)
                if cat.lower() == old_name.lower()
            )
        except StopIteration as exc:
            raise KeyError(f"Kategori tidak ditemukan: {old_name}") from exc

        actual_old = session.categories[idx]
        cleaned_new = sanitize_category_name(new_name)
        if cleaned_new == actual_old:
            return True

        conflict = any(
            i != idx and cat.lower() == cleaned_new.lower()
            for i, cat in enumerate(session.categories)
        )
        if conflict:
            raise ValueError(f"Kategori '{cleaned_new}' sudah ada.")

        root = Path(session.folder_path)
        old_dir = root / actual_old
        new_dir = root / cleaned_new
        remapped_paths: Dict[str, str] = {}

        if old_dir.exists() and old_dir.is_dir():
            if actual_old.lower() == cleaned_new.lower():
                temp_dir = root / f".__rename_{uuid.uuid4().hex[:8]}"
                old_dir.rename(temp_dir)
                temp_dir.rename(new_dir)
            elif new_dir.exists():
                for item in list(old_dir.iterdir()):
                    if item.is_file():
                        dest = unique_dest_path(new_dir, item.name)
                        _move_file_safe(item, dest)
                        remapped_paths[str(item)] = str(dest)
                if not any(old_dir.iterdir()):
                    old_dir.rmdir()
            else:
                old_dir.rename(new_dir)

        session.categories[idx] = cleaned_new
        if actual_old in session.category_meta:
            session.category_meta[cleaned_new] = session.category_meta.pop(actual_old)

        for action in session.undo_stack:
            if action.category == actual_old:
                action.category = cleaned_new
                if action.dest_path in remapped_paths:
                    action.dest_path = remapped_paths[action.dest_path]
                else:
                    action.dest_path = str(new_dir / Path(action.dest_path).name)
            if action.prev_category == actual_old:
                action.prev_category = cleaned_new

        for entry in session.timeline:
            if entry.status == "sorted" and entry.category == actual_old:
                old_abs = str(root / entry.rel_path)
                entry.category = cleaned_new
                if old_abs in remapped_paths:
                    new_p = Path(remapped_paths[old_abs])
                    entry.name = new_p.name
                    entry.rel_path = f"{cleaned_new}/{new_p.name}"
                else:
                    entry.rel_path = f"{cleaned_new}/{Path(entry.rel_path).name}"

        if 0 <= session.current_index < len(session.timeline):
            cur_e = session.timeline[session.current_index]
            session.current_photo_name = cur_e.name
            session.current_photo_rel_path = cur_e.rel_path

        if session_id in self._cached_counts:
            counts = self._cached_counts[session_id]
            old_count = counts.pop(actual_old, 0)
            counts[cleaned_new] = counts.get(cleaned_new, 0) + old_count

        self.save()
        return True

    def revert_and_remove_category(
        self, session_id: str, category_name: str
    ) -> int:
        """Move all files from category subfolder back to origin, sync timeline, remove category."""
        session = self.get_session(session_id)
        matched = next(
            (cat for cat in session.categories if cat.lower() == category_name.lower()),
            None,
        )
        if matched is None:
            raise KeyError(f"Kategori tidak ditemukan: {category_name}")

        root = Path(session.folder_path)
        cat_dir = root / matched
        reverted_count = 0

        entry_by_rel = {
            e.rel_path: e
            for e in session.timeline
            if e.status == "sorted" and e.category == matched
        }

        if cat_dir.exists() and cat_dir.is_dir():
            for item in sorted(cat_dir.iterdir(), key=lambda p: natural_sort_key(p.name)):
                if item.is_file():
                    rel_key = f"{matched}/{item.name}"
                    matched_entry = entry_by_rel.get(rel_key)
                    if matched_entry is not None:
                        dest, rel_str = self._restore_origin_destination(
                            root, matched_entry, item.name
                        )
                        _move_file_safe(item, dest)
                        matched_entry.name = dest.name
                        matched_entry.rel_path = rel_str
                        matched_entry.status = "pending"
                        matched_entry.category = None
                    else:
                        dest = unique_dest_path(root, item.name)
                        _move_file_safe(item, dest)
                    reverted_count += 1
            if not any(cat_dir.iterdir()):
                cat_dir.rmdir()

        for entry in session.timeline:
            if entry.status == "sorted" and entry.category == matched:
                entry.status = "pending"
                entry.category = None

        session.categories = [c for c in session.categories if c != matched]
        session.category_meta.pop(matched, None)
        session.undo_stack = [
            action for action in session.undo_stack if action.category != matched
        ]
        self._sync_session_category_meta(session)
        self._cached_counts.pop(session_id, None)
        self.sync_session(session_id)
        self.save()
        return reverted_count

    def revert_category(self, session_id: str, category_name: str) -> int:
        """Alias for revert_and_remove_category."""
        return self.revert_and_remove_category(session_id, category_name)

    def delete_session(self, session_id: str) -> bool:
        """Remove a session from app state only; disk files stay untouched."""
        before = len(self.sessions)
        self.sessions = [s for s in self.sessions if s.id != session_id]
        if len(self.sessions) == before:
            return False
        self._queues.pop(session_id, None)
        self._cached_counts.pop(session_id, None)
        if self.active_session_id == session_id:
            self.active_session_id = self.sessions[0].id if self.sessions else None
        self.save()
        return True

    def get_category_counts(self, session_id: str) -> Dict[str, int]:
        """Return media counts on disk for each category and trash folder."""
        session = self.get_session(session_id)
        root = Path(session.folder_path)
        counts: Dict[str, int] = {}
        trash_name = self._trash_folder_name()
        for name in [*session.categories, trash_name]:
            target_dir = root / name
            if target_dir.is_dir():
                counts[name] = sum(
                    1
                    for entry in target_dir.iterdir()
                    if entry.is_file() and entry.suffix.lower() in SUPPORTED_EXTS
                )
            else:
                counts[name] = 0
        self._cached_counts[session_id] = counts
        return counts

    def get_cached_counts(self, session_id: str) -> Dict[str, int]:
        """Return in-memory category counts without disk rescan when already cached."""
        return dict(self._ensure_cached_counts(session_id))

    def get_session_progress(
        self, session_id: str, use_cached: bool = False
    ) -> Tuple[int, int]:
        """Return (remaining_count, sorted_count) for a session."""
        if (
            use_cached
            and session_id in self._queues
            and session_id in self._cached_counts
        ):
            return (
                len(self._queues[session_id]),
                sum(self._cached_counts[session_id].values()),
            )
        queue = self.get_queue(session_id)
        counts = (
            self._ensure_cached_counts(session_id)
            if use_cached
            else self.get_category_counts(session_id)
        )
        return len(queue), sum(counts.values())
