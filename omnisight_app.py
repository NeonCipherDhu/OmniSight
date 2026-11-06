"""
OmniSight — Minimalist NVR / VMS Desktop Client for Windows.
Unofficial client compatible with V380 cameras.

Design System:
- Inspired by Ubiquiti UniFi Protect & Frigate NVR (Zinc/Obsidian dark palette, zero emojis)
- Built-in Lucide 24x24 vector SVG icon renderer (HiDPI-aware via PyQt6.QtSvg)
- Video-first edge-to-edge camera grid with collapsible PTZ & device inspector drawer
- Persistent camera configuration & custom name registry (cameras.json + camera_names.json)
"""

from __future__ import annotations

import json
import logging
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("OmniSight")

from PyQt6.QtCore import (
    QByteArray,
    QDate,
    QEvent,
    QMimeData,
    QPoint,
    QRect,
    QRectF,
    QSettings,
    QSize,
    Qt,
    QThread,
    QTime,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QBrush,
    QColor,
    QDrag,
    QDragEnterEvent,
    QDragLeaveEvent,
    QDragMoveEvent,
    QDropEvent,
    QFont,
    QIcon,
    QImage,
    QKeyEvent,
    QMouseEvent,
    QPainter,
    QPen,
    QPixmap,
    QResizeEvent,
    QTransform,
    QWheelEvent,
)
from PyQt6.QtSvg import QSvgRenderer
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDateEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QStatusBar,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from stream_worker import (
    CameraConfig,
    CameraStreamWorker,
    DetectionChimePlayer,
    LiveAudioOutput,
    SDCardBulkBackupWorker,
    SDCardExportWorker,
    SDCardListWorker,
    SDCardPlaybackWorker,
    find_segment_backup_file,
    get_gpu_capabilities,
    get_segment_backup_path,
    load_local_backup_segments,
    save_segment_backup_meta,
)
from omnisight_protocol import (
    DiscoveredCamera,
    LanCameraClient,
    LanCameraDiscovery,
    RecordingSegment,
    get_local_ipv4_addresses,
)

BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
APP_VERSION = "1.0.1"
APP_DIR = (
    Path(sys.executable).resolve().parent
    if getattr(sys, "frozen", False)
    else Path(__file__).resolve().parent
)
USER_DATA_DIR = APP_DIR
CONFIG_FILE = APP_DIR / "cameras.json"
USER_CONFIG_FILE = CONFIG_FILE
USER_NAMES_FILE = APP_DIR / "camera_names.json"
SNAPSHOT_DIR = APP_DIR / "captures" / "snapshots"
RECORDING_DIR = APP_DIR / "captures" / "recordings"
BACKUP_DIR = RECORDING_DIR / "backups"
PRESET_THUMB_DIR = APP_DIR / "captures" / "presets"


# ---------------------------------------------------------------------------
# Lucide 24x24 Vector SVG Icon Engine (Zero Emojis, HiDPI Crisp)
# ---------------------------------------------------------------------------
LUCIDE_PATHS: Dict[str, str] = {
    "cctv": (
        '<path d="M16.75 12h3.632a1 1 0 0 1 .894 1.447l-2.034 4.069a1 1 0 0 1-1.708.134l-2.124-2.97"/>'
        '<path d="M17.106 9.053a1 1 0 0 1 .447 1.341l-3.106 6.211a1 1 0 0 1-1.342.447L3.61 12.3a2.92 2.92 0 0 1-1.3-3.91L3.69 5.6a2.92 2.92 0 0 1 3.92-1.3z"/>'
        '<path d="M2 19h3.76a2 2 0 0 0 1.8-1.1L9 15"/>'
        '<path d="M2 21v-4"/>'
        '<path d="M7 9h.01"/>'
    ),
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "radar": (
        '<path d="M19.07 4.93A10 10 0 0 0 6.99 3.34"/>'
        '<path d="M4 6h.01"/>'
        '<path d="M2.29 9.62A10 10 0 1 0 21.31 8.35"/>'
        '<path d="M16.24 7.76A6 6 0 1 0 8.23 16.67"/>'
        '<path d="M12 18h.01"/>'
        '<path d="M17.99 11.66A6 6 0 0 1 15.77 16.67"/>'
        '<circle cx="12" cy="12" r="2"/>'
        '<path d="m13.41 10.59 5.66-5.66"/>'
    ),
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "square": '<rect width="16" height="16" x="4" y="4" rx="2"/>',
    "volume_2": (
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>'
        '<path d="M15.54 8.46a5 5 0 0 1 0 7.07"/>'
        '<path d="M19.07 4.93a10 10 0 0 1 0 14.14"/>'
    ),
    "volume_x": (
        '<polygon points="11 5 6 9 2 9 2 15 6 15 11 19 11 5"/>'
        '<line x1="22" x2="16" y1="9" y2="15"/>'
        '<line x1="16" x2="22" y1="9" y2="15"/>'
    ),
    "camera": (
        '<path d="M14.5 4h-5L7 7H4a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2V9a2 2 0 0 0-2-2h-3l-2.5-3z"/>'
        '<circle cx="12" cy="13" r="3"/>'
    ),
    "disc": '<circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/>',
    "maximize_2": (
        '<polyline points="15 3 21 3 21 9"/>'
        '<polyline points="9 21 3 21 3 15"/>'
        '<line x1="21" x2="14" y1="3" y2="10"/>'
        '<line x1="3" x2="10" y1="21" y2="14"/>'
    ),
    "minimize_2": (
        '<polyline points="4 14 10 14 10 20"/>'
        '<polyline points="20 10 14 10 14 4"/>'
        '<line x1="14" x2="21" y1="10" y2="3"/>'
        '<line x1="3" x2="10" y1="21" y2="14"/>'
    ),
    "sliders": (
        '<line x1="21" x2="14" y1="4" y2="4"/>'
        '<line x1="10" x2="3" y1="4" y2="4"/>'
        '<line x1="21" x2="12" y1="12" y2="12"/>'
        '<line x1="8" x2="3" y1="12" y2="12"/>'
        '<line x1="21" x2="16" y1="20" y2="20"/>'
        '<line x1="12" x2="3" y1="20" y2="20"/>'
        '<line x1="14" x2="14" y1="2" y2="6"/>'
        '<line x1="8" x2="8" y1="10" y2="14"/>'
        '<line x1="16" x2="16" y1="18" y2="22"/>'
    ),
    "panel_right": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M15 3v18"/>'
    ),
    "pencil": (
        '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>'
        '<path d="m15 5 4 4"/>'
    ),
    "settings": (
        '<path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/>'
        '<path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
        '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>'
    ),
    "chevron_up": '<path d="m18 15-6-6-6 6"/>',
    "chevron_down": '<path d="m6 9 6 6 6-6"/>',
    "chevron_left": '<path d="m15 18-6-6 6-6"/>',
    "chevron_right": '<path d="m9 18 6-6-6-6"/>',
    "crosshair": (
        '<circle cx="12" cy="12" r="10"/>'
        '<line x1="22" x2="18" y1="12" y2="12"/>'
        '<line x1="6" x2="2" y1="12" y2="12"/>'
        '<line x1="12" x2="12" y1="6" y2="2"/>'
        '<line x1="12" x2="12" y1="22" y2="18"/>'
    ),
    "sun": (
        '<circle cx="12" cy="12" r="4"/>'
        '<path d="M12 2v2"/><path d="M12 20v2"/>'
        '<path d="m4.93 4.93 1.41 1.41"/><path d="m17.66 17.66 1.41 1.41"/>'
        '<path d="M2 12h2"/><path d="M20 12h2"/>'
        '<path d="m6.34 17.66-1.41 1.41"/><path d="m19.07 4.93-1.41 1.41"/>'
    ),
    "moon": '<path d="M12 3a6 6 0 0 0 9 9 9 9 0 1 1-9-9Z"/>',
    "zap": '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
    "eye": (
        '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>'
        '<circle cx="12" cy="12" r="3"/>'
    ),
    "flip_vertical": (
        '<path d="M21 8V5a2 2 0 0 0-2-2H5a2 2 0 0 0-2 2v3"/>'
        '<path d="M21 16v3a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-3"/>'
        '<path d="M4 12H2"/><path d="M10 12H8"/><path d="M16 12h-2"/><path d="M22 12h-2"/>'
    ),
    "folder": (
        '<path d="M20 20a2 2 0 0 0 2-2V8a2 2 0 0 0-2-2h-7.9a2 2 0 0 1-1.69-.9L9.6 3.9A2 2 0 0 0 7.93 3H4a2 2 0 0 0-2 2v13a2 2 0 0 0 2 2Z"/>'
    ),
    "film": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M7 3v18"/><path d="M3 7.5h4"/><path d="M3 12h18"/><path d="M3 16.5h4"/>'
        '<path d="M17 3v18"/><path d="M17 7.5h4"/><path d="M17 16.5h4"/>'
    ),
    "refresh": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/>'
        '<path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/>'
        '<path d="M8 16H3v5"/>'
    ),
    "grid_1x1": '<rect width="18" height="18" x="3" y="3" rx="2"/>',
    "grid_2x2": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M3 12h18"/><path d="M12 3v18"/>'
    ),
    "grid_3x3": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/>'
        '<path d="M3 9h18"/><path d="M3 15h18"/>'
        '<path d="M9 3v18"/><path d="M15 3v18"/>'
    ),
    "monitor": (
        '<rect width="20" height="14" x="2" y="3" rx="2"/>'
        '<line x1="8" x2="16" y1="21" y2="21"/>'
        '<line x1="12" x2="12" y1="17" y2="21"/>'
    ),
    "bookmark": '<path d="m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z"/>',
    "navigation": '<polygon points="3 11 22 2 13 21 11 13 3 11"/>',
    "move_horizontal": (
        '<polyline points="18 8 22 12 18 16"/>'
        '<polyline points="6 8 2 12 6 16"/>'
        '<line x1="2" x2="22" y1="12" y2="12"/>'
    ),
    "pan": (
        '<polyline points="18 8 22 12 18 16"/>'
        '<polyline points="6 8 2 12 6 16"/>'
        '<line x1="2" x2="22" y1="12" y2="12"/>'
    ),
    "save": (
        '<path d="M19 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h11l5 5v11a2 2 0 0 1-2 2z"/>'
        '<polyline points="17 21 17 13 7 13 7 21"/>'
        '<polyline points="7 3 7 8 15 8"/>'
    ),
    "x": '<path d="M18 6 6 18"/><path d="m6 6 12 12"/>',
    "check": '<polyline points="20 6 9 17 4 12"/>',
    "alert_triangle": (
        '<path d="m21.73 18-8-14a2 2 0 0 0-3.48 0l-8 14A2 2 0 0 0 4 21h16a2 2 0 0 0 1.73-3Z"/>'
        '<line x1="12" x2="12" y1="9" y2="13"/>'
        '<line x1="12" x2="12.01" y1="17" y2="17"/>'
    ),
    "grip_vertical": (
        '<circle cx="9" cy="12" r="1"/>'
        '<circle cx="9" cy="5" r="1"/>'
        '<circle cx="9" cy="19" r="1"/>'
        '<circle cx="15" cy="12" r="1"/>'
        '<circle cx="15" cy="5" r="1"/>'
        '<circle cx="15" cy="19" r="1"/>'
    ),
    "pause": '<rect width="4" height="16" x="6" y="4"/><rect width="4" height="16" x="14" y="4"/>',
    "skip_back": '<polygon points="19 20 9 12 19 4 19 20"/><line x1="5" x2="5" y1="19" y2="5"/>',
    "skip_forward": '<polygon points="5 4 15 12 5 20 5 4"/><line x1="19" x2="19" y1="5" y2="19"/>',
    "download": (
        '<path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/>'
        '<polyline points="7 10 12 15 17 10"/>'
        '<line x1="12" x2="12" y1="15" y2="3"/>'
    ),
    "calendar": (
        '<rect width="18" height="18" x="3" y="4" rx="2" ry="2"/>'
        '<line x1="16" x2="16" y1="2" y2="6"/>'
        '<line x1="8" x2="8" y1="2" y2="6"/>'
        '<line x1="3" x2="21" y1="10" y2="10"/>'
    ),
    "hard_drive": (
        '<line x1="22" x2="2" y1="12" y2="12"/>'
        '<path d="M5.45 5.11 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.45-6.89A2 2 0 0 0 16.76 4H7.24a2 2 0 0 0-1.79 1.11z"/>'
        '<line x1="6" x2="6.01" y1="16" y2="16"/>'
        '<line x1="10" x2="10.01" y1="16" y2="16"/>'
    ),
    "clock": '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
    "wifi": (
        '<path d="M12 20h.01"/>'
        '<path d="M2 8.82a15 15 0 0 1 20 0"/>'
        '<path d="M5 12.859a10 10 0 0 1 14 0"/>'
        '<path d="M8.5 16.429a5 5 0 0 1 7 0"/>'
    ),
    "wifi_off": (
        '<line x1="2" x2="22" y1="2" y2="22"/>'
        '<path d="M8.5 16.429a5 5 0 0 1 7 0"/>'
        '<path d="M5 12.859a10 10 0 0 1 5.17-2.69"/>'
        '<path d="M19 12.859a10 10 0 0 0-2.007-1.523"/>'
        '<path d="M2 8.82a15 15 0 0 1 4.177-2.643"/>'
        '<path d="M22 8.82a15 15 0 0 0-11.288-3.764"/>'
        '<path d="M12 20h.01"/>'
    ),
    "shield_alert": (
        '<path fill="#ef4444" fill-opacity="0.22" d="M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z"/>'
        '<path d="M12 8v4"/>'
        '<path d="M12 16h.01"/>'
    ),
    "bell": (
        '<path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/>'
        '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'
    ),
    "bell_off": (
        '<path d="M8.7 3A6 6 0 0 1 18 8a21.3 21.3 0 0 0 .6 5"/>'
        '<path d="M17 17H3s3-2 3-9a4.67 4.67 0 0 1 .3-1.7"/>'
        '<path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/>'
        '<path d="m2 2 20 20"/>'
    ),
    "scan": (
        '<path d="M3 7V5a2 2 0 0 1 2-2h2"/>'
        '<path d="M17 3h2a2 2 0 0 1 2 2v2"/>'
        '<path d="M21 17v2a2 2 0 0 1-2 2h-2"/>'
        '<path d="M7 21H5a2 2 0 0 1-2-2v-2"/>'
        '<rect width="10" height="8" x="7" y="8" rx="1"/>'
    ),
    "coffee": (
        '<path d="M10 2v2"/>'
        '<path d="M14 2v2"/>'
        '<path d="M16 8a1 1 0 0 1 1 1v8a4 4 0 0 1-4 4H7a4 4 0 0 1-4-4V9a1 1 0 0 1 1-1h14a4 4 0 1 1 0 8h-1"/>'
        '<path d="M6 2v2"/>'
    ),
}

_ICON_CACHE: Dict[str, QIcon] = {}


def lucide_pixmap(name: str, color: str = "#e4e4e7", size: int = 16, stroke_width: float = 1.85) -> QPixmap:
    body = LUCIDE_PATHS.get(name, LUCIDE_PATHS["cctv"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    dpr = 1.0
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        dpr = max(1.0, float(app.primaryScreen().devicePixelRatio()))
    px_size = max(1, int(round(size * dpr)))
    pixmap = QPixmap(px_size, px_size)
    pixmap.fill(Qt.GlobalColor.transparent)
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    renderer.render(painter, QRectF(0, 0, px_size, px_size))
    painter.end()
    pixmap.setDevicePixelRatio(dpr)
    return pixmap


def lucide_icon(name: str, color: str = "#e4e4e7", size: int = 16, stroke_width: float = 1.85) -> QIcon:
    key = f"{name}:{color}:{size}:{stroke_width}"
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached
    ico = QIcon(lucide_pixmap(name, color=color, size=size, stroke_width=stroke_width))
    _ICON_CACHE[key] = ico
    return ico


def clip_row_icon(event_icon_name: str, event_color: str, is_backed_up: bool = False) -> QIcon:
    """
    Renders a crisp 26x26 rounded badge QIcon for recording clip rows:
    - Rounded badge container tinted with `event_color` + 16x16 centered event glyph
    - Optional bottom-right green checkmark badge (`#10b981`) when backed up locally on disk
    """
    key = f"cliprow26:{event_icon_name}:{event_color}:{int(is_backed_up)}"
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    dpr = 1.0
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        dpr = max(1.0, float(app.primaryScreen().devicePixelRatio()))

    w, h = 26, 26
    pixmap = QPixmap(max(1, int(round(w * dpr))), max(1, int(round(h * dpr))))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    base_col = QColor(event_color)
    fill_col = QColor(base_col.red(), base_col.green(), base_col.blue(), 42)
    border_col = QColor(base_col.red(), base_col.green(), base_col.blue(), 155)
    painter.setPen(QPen(border_col, 1.2))
    painter.setBrush(fill_col)
    painter.drawRoundedRect(QRectF(1.0, 1.0, 24.0, 24.0), 6.0, 6.0)

    ev_px = lucide_pixmap(event_icon_name, color=event_color, size=16, stroke_width=2.1)
    painter.drawPixmap(5, 5, ev_px)

    if is_backed_up:
        painter.setPen(QPen(QColor("#10b981"), 1.2))
        painter.setBrush(QColor("#064e3b"))
        painter.drawEllipse(QRectF(14.0, 14.0, 11.5, 11.5))
        chk_px = lucide_pixmap("check", color="#34d399", size=8, stroke_width=3.0)
        painter.drawPixmap(16, 16, chk_px)

    painter.end()
    ico = QIcon(pixmap)
    _ICON_CACHE[key] = ico
    return ico


class StatusDot(QWidget):
    """Crisp vector status indicator dot (Live / Connecting / Error / Idle)."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedSize(10, 10)
        self._color = QColor("#52525b")

    def set_state(self, state: str) -> None:
        palette = {
            "live": "#10b981",       # Emerald
            "connecting": "#f59e0b", # Amber
            "error": "#ef4444",      # Red
            "idle": "#52525b",       # Zinc
        }
        self._color = QColor(palette.get(state, "#52525b"))
        self.update()

    def paintEvent(self, event) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(self._color)
        painter.drawEllipse(1, 1, 8, 8)
        painter.end()


class _VerticalScrollArea(QScrollArea):
    """Scroll area that strictly locks its inner content widget width to the viewport width so horizontal clipping never occurs."""

    def _sync_content_width(self) -> None:
        w = self.widget()
        if w is not None:
            vw = self.viewport().width()
            if vw > 0 and w.width() != vw:
                w.setFixedWidth(vw)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._sync_content_width()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._sync_content_width()


def _short_gpu_label(gpu_dev: str) -> str:
    """Return a concise GPU badge label that never overflows narrow sidebar buttons."""
    raw = (gpu_dev or "GPU").strip()
    for token in ("(R)", "(TM)", "Corporation", "Graphics", "Adapter", "Series", "Laptop GPU", "Processor", "with Max-Q Design"):
        raw = raw.replace(token, "")
    raw = " ".join(raw.split()).strip(" -_/")
    if not raw:
        return "GPU"
    if len(raw) > 15:
        low = raw.lower()
        for brand in ("RTX ", "GTX ", "Arc ", "Iris ", "UHD ", "HD ", "Radeon ", "Vega ", "Adreno "):
            idx = low.find(brand.lower())
            if idx >= 0:
                sub = raw[idx : idx + 14].rstrip(" -_/")
                if sub.endswith(" GPU"):
                    sub = sub[:-4].strip()
                if sub:
                    return sub
        if "adreno" in low or "qualcomm" in low:
            return "Adreno GPU"
        if "intel" in low:
            return "Intel GPU"
        if "nvidia" in low or "geforce" in low:
            return "NVIDIA GPU"
        if "amd" in low or "radeon" in low:
            return "AMD GPU"
        return raw[:14].strip()
    return raw


# ---------------------------------------------------------------------------
# UniFi Protect / Frigate NVR Zinc Dark Stylesheet
# ---------------------------------------------------------------------------
NVR_STYLESHEET = """
QMainWindow, QDialog {
    background-color: #09090b;
    color: #f4f4f5;
    font-family: "Segoe UI", -apple-system, sans-serif;
    font-size: 12px;
}
QWidget {
    color: #f4f4f5;
    font-family: "Segoe UI", -apple-system, sans-serif;
}
QFrame#topHeaderBar {
    background-color: #0e0e11;
    border-bottom: 1px solid #1f1f24;
}
QFrame#inspectorDrawer {
    background-color: #0e0e11;
    border-left: 1px solid #1f1f24;
}
QScrollArea#drawerScrollArea {
    background-color: transparent;
    border: none;
}
QWidget#drawerContent {
    background-color: #0e0e11;
}
QScrollBar:vertical {
    background-color: #0e0e11;
    width: 7px;
    margin: 2px 1px 2px 1px;
    border: none;
}
QScrollBar::handle:vertical {
    background-color: #3f3f46;
    min-height: 28px;
    border-radius: 3px;
}
QScrollBar::handle:vertical:hover {
    background-color: #52525b;
}
QScrollBar::handle:vertical:pressed {
    background-color: #71717a;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: none;
}
QFrame#drawerSection {
    background-color: #141418;
    border: 1px solid #222228;
    border-radius: 6px;
}
QLabel#sectionTitle {
    color: #71717a;
    font-size: 10px;
    font-weight: 700;
    letter-spacing: 0.8px;
    padding: 0px;
    margin: 0px;
}
QPushButton {
    background-color: #18181c;
    border: 1px solid #27272d;
    border-radius: 5px;
    padding: 3px 8px;
    min-width: 0px;
    qproperty-iconSize: 14px 14px;
    color: #e4e4e7;
    font-size: 12px;
    font-weight: 500;
}
QPushButton:hover {
    background-color: #222228;
    border-color: #3f3f46;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #27272a;
}
QPushButton:checked {
    background-color: #27272a;
    border-color: #52525b;
    color: #ffffff;
}
QPushButton#primaryBtn {
    background-color: #2563eb;
    border: 1px solid #3b82f6;
    min-width: 0px;
    color: #ffffff;
    font-weight: 600;
}
QPushButton#primaryBtn:hover {
    background-color: #1d4ed8;
    border-color: #60a5fa;
}
QPushButton#ghostBtn {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px 5px;
    min-width: 0px;
    color: #a1a1aa;
}
QPushButton#ghostBtn:hover {
    background-color: #1f1f24;
    color: #f4f4f5;
}
QPushButton#ghostBtn:checked {
    background-color: #1f1f24;
    border-color: #3f3f46;
    color: #ffffff;
}
QPushButton#dangerGhostBtn {
    background-color: transparent;
    border: 1px solid transparent;
    border-radius: 4px;
    padding: 3px 5px;
    min-width: 0px;
    color: #a1a1aa;
}
QPushButton#dangerGhostBtn:hover {
    background-color: #3f1518;
    border-color: #7f1d1d;
    color: #fca5a5;
}
QPushButton#segBtn {
    background-color: #141418;
    border: 1px solid #24242b;
    border-radius: 4px;
    padding: 3px 7px;
    min-width: 0px;
    color: #a1a1aa;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#segBtn:hover {
    background-color: #1e1e24;
    color: #e4e4e7;
}
QPushButton#segBtn:checked {
    background-color: #1e3a8a;
    border-color: #3b82f6;
    color: #ffffff;
}
QPushButton#segBtn:disabled {
    background-color: #121217;
    border: 1px solid #23232c;
    color: #71717a;
}
QPushButton#optBtn {
    background-color: #18181c;
    border: 1px solid #27272d;
    border-radius: 5px;
    padding: 4px 8px;
    min-width: 0px;
    qproperty-iconSize: 13px 13px;
    color: #a1a1aa;
    font-size: 11px;
    font-weight: 600;
}
QPushButton#optBtn:hover {
    background-color: #222228;
    border-color: #3f3f46;
    color: #f4f4f5;
}
QPushButton#optBtn:checked {
    background-color: #1f2430;
    border: 1px solid #3b82f6;
    color: #ffffff;
}
QLineEdit, QSpinBox, QComboBox {
    background-color: #121216;
    border: 1px solid #27272d;
    border-radius: 5px;
    padding: 5px 8px;
    min-width: 0px;
    color: #f4f4f5;
}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus {
    border-color: #3b82f6;
}
QListWidget, QTableWidget {
    background-color: #111115;
    border: 1px solid #222228;
    border-radius: 5px;
    outline: none;
}
QListWidget::item {
    padding: 3px 6px;
    border-bottom: 1px solid #18181d;
    border-radius: 3px;
    color: #d4d4d8;
}
QListWidget::item:hover {
    background-color: #18181e;
}
QListWidget::item:selected {
    background-color: #1f2430;
    border: 1px solid #3b82f6;
    color: #ffffff;
}
QHeaderView::section {
    background-color: #141418;
    color: #a1a1aa;
    border: none;
    border-bottom: 1px solid #27272d;
    padding: 6px 8px;
    font-size: 11px;
    font-weight: 600;
}
QStatusBar {
    background-color: #09090b;
    border-top: 1px solid #18181d;
    color: #71717a;
    font-size: 11px;
}
QProgressBar {
    background-color: #18181d;
    border: none;
    border-radius: 2px;
}
QProgressBar::chunk {
    background-color: #10b981;
    border-radius: 2px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: #27272d;
    border-radius: 2px;
}
QSlider::sub-page:horizontal {
    background: #3b82f6;
    border-radius: 2px;
}
QSlider::handle:horizontal {
    background: #f4f4f5;
    width: 12px;
    height: 12px;
    margin: -4px 0;
    border-radius: 6px;
}
QMenu {
    background-color: #141418;
    border: 1px solid #27272d;
    border-radius: 6px;
    padding: 4px;
}
QMenu::item {
    padding: 6px 20px 6px 12px;
    border-radius: 4px;
    color: #e4e4e7;
}
QMenu::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}
QMenu::separator {
    height: 1px;
    background: #27272d;
    margin: 4px 0;
}
"""


# ---------------------------------------------------------------------------
# LAN Discovery Worker & Dialog
# ---------------------------------------------------------------------------
class DiscoveryWorker(QThread):
    """Background thread for LAN UDP + TCP 8800 camera discovery."""

    progress = pyqtSignal(str)
    finished_results = pyqtSignal(list)

    def __init__(self, scan_tcp: bool = True, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.scan_tcp = scan_tcp

    def run(self) -> None:
        cams = LanCameraDiscovery.discover(
            timeout_sec=2.0,
            scan_tcp_8800=self.scan_tcp,
            progress_cb=lambda msg: self.progress.emit(msg),
        )
        self.finished_results.emit(cams)


class DiscoveryDialog(QDialog):
    """Modal dialog to scan the local network for compatible cameras on UDP 10008 and TCP 8800."""

    def __init__(
        self,
        parent: Optional[QWidget] = None,
        existing_cameras: Optional[List[CameraConfig]] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Network Discovery — Port 8800 & UDP 10008 (Compatible with V380 Cameras)")
        scr = (parent.screen() if parent else None) or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        self.resize(
            min(740, avail.width() - 40) if avail else 740,
            min(420, avail.height() - 40) if avail else 420,
        )
        self.selected_camera: Optional[DiscoveredCamera] = None
        self._all_discovered: List[DiscoveredCamera] = []
        self._visible_discovered: List[DiscoveredCamera] = []
        self._worker: Optional[DiscoveryWorker] = None

        if existing_cameras is not None:
            self._existing_cameras: List[CameraConfig] = list(existing_cameras)
        else:
            cams = getattr(parent, "cameras", None)
            if cams is None and parent is not None:
                cams = getattr(parent.parent(), "cameras", None)
            self._existing_cameras = list(cams) if cams else []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        local_ips = ", ".join(get_local_ipv4_addresses()) or "Unknown"
        info_lbl = QLabel(
            f"Scanning local & neighbor subnets (<b>{local_ips}</b>, <code>192.168.0.x</code>, "
            "<code>192.168.1.x</code>) via UDP broadcast/unicast <code>NVDEVSEARCH^100</code> and TCP port 8800."
        )
        info_lbl.setStyleSheet("color: #a1a1aa;")
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        top_row = QHBoxLayout()
        self.chk_tcp = QCheckBox("Probe subnet for open TCP port 8800")
        self.chk_tcp.setChecked(True)
        top_row.addWidget(self.chk_tcp)

        self.chk_hide_added = QCheckBox("Hide already-added cameras")
        self.chk_hide_added.setChecked(False)
        self.chk_hide_added.toggled.connect(self._rebuild_table)
        top_row.addWidget(self.chk_hide_added)
        top_row.addStretch()

        self.btn_rescan = QPushButton("Rescan")
        self.btn_rescan.setIcon(lucide_icon("refresh", "#e4e4e7", 14))
        self.btn_rescan.clicked.connect(self.start_scan)
        top_row.addWidget(self.btn_rescan)
        layout.addLayout(top_row)

        self.status_lbl = QLabel("Initializing network scan...")
        self.status_lbl.setStyleSheet("color: #a1a1aa; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px;")
        layout.addWidget(self.status_lbl)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(["Status", "IP Address", "Device ID", "MAC Address", "Discovery Method"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.itemDoubleClicked.connect(self._accept_selected)
        layout.addWidget(self.table, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch()
        self.btn_add = QPushButton("Select Camera")
        self.btn_add.setObjectName("primaryBtn")
        self.btn_add.setEnabled(False)
        self.btn_add.clicked.connect(self._accept_selected)
        btn_row.addWidget(self.btn_add)

        btn_close = QPushButton("Cancel")
        btn_close.clicked.connect(self.reject)
        btn_row.addWidget(btn_close)
        layout.addLayout(btn_row)

        self.table.itemSelectionChanged.connect(
            lambda: self.btn_add.setEnabled(len(self.table.selectedItems()) > 0)
        )

        QTimer.singleShot(150, self.start_scan)

    def _match_existing(self, cam: DiscoveredCamera) -> Optional[CameraConfig]:
        for cfg in self._existing_cameras:
            if cam.dev_id and cfg.device_id and cam.dev_id.strip() == cfg.device_id.strip():
                return cfg
            if cam.ip and cfg.ip and cam.ip.strip() == cfg.ip.strip():
                return cfg
        return None

    def start_scan(self) -> None:
        if self._worker is not None and self._worker.isRunning():
            return
        self.table.setRowCount(0)
        self._all_discovered.clear()
        self._visible_discovered.clear()
        self.btn_rescan.setEnabled(False)
        self.btn_add.setEnabled(False)
        self.status_lbl.setText("Probing local network...")

        self._worker = DiscoveryWorker(scan_tcp=self.chk_tcp.isChecked(), parent=self)
        self._worker.progress.connect(self.status_lbl.setText)
        self._worker.finished_results.connect(self._on_results)
        self._worker.start()

    def _on_results(self, cams: List[DiscoveredCamera]) -> None:
        self.btn_rescan.setEnabled(True)
        # Sort so NEW (not yet added) cameras always appear at the top of the table
        self._all_discovered = sorted(
            cams,
            key=lambda c: (0 if self._match_existing(c) is None else 1, c.ip),
        )
        self._rebuild_table()

    def _rebuild_table(self) -> None:
        hide_added = self.chk_hide_added.isChecked()
        new_count = sum(1 for c in self._all_discovered if self._match_existing(c) is None)
        added_count = len(self._all_discovered) - new_count

        if hide_added:
            self._visible_discovered = [c for c in self._all_discovered if self._match_existing(c) is None]
        else:
            self._visible_discovered = list(self._all_discovered)

        self.table.setRowCount(len(self._visible_discovered))
        for row, cam in enumerate(self._visible_discovered):
            existing = self._match_existing(cam)
            if existing is None:
                status_item = QTableWidgetItem("NEW — Ready to Add")
                status_item.setForeground(QColor("#10b981"))
            else:
                status_item = QTableWidgetItem(f"Already Added ({existing.name})")
                status_item.setForeground(QColor("#71717a"))

            ip_item = QTableWidgetItem(cam.ip)
            dev_item = QTableWidgetItem(cam.dev_id or "Manual ID required")
            mac_item = QTableWidgetItem(cam.mac or "-")
            method_item = QTableWidgetItem(cam.discovery_method)

            if existing is not None:
                for it in (ip_item, dev_item, mac_item, method_item):
                    it.setForeground(QColor("#a1a1aa"))

            self.table.setItem(row, 0, status_item)
            self.table.setItem(row, 1, ip_item)
            self.table.setItem(row, 2, dev_item)
            self.table.setItem(row, 3, mac_item)
            self.table.setItem(row, 4, method_item)

        total = len(self._all_discovered)
        if total == 0:
            self.status_lbl.setText("No compatible cameras responded on the network. Enter IP and Device ID manually.")
        elif new_count > 0:
            self.status_lbl.setText(
                f"Discovered {total} camera(s): {new_count} new camera(s) ready to add, {added_count} already added."
            )
            if self._visible_discovered:
                self.table.selectRow(0)
        else:
            if hide_added:
                self.status_lbl.setText(
                    f"Discovered {total} camera(s), all {added_count} already added (uncheck 'Hide already-added' to view)."
                )
            else:
                self.status_lbl.setText(
                    f"Discovered {total} camera(s) on network — all {added_count} are already added to OmniSight."
                )
                self.table.selectRow(0)

    def _accept_selected(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self._visible_discovered):
            self.selected_camera = self._visible_discovered[row]
            self.accept()


# ---------------------------------------------------------------------------
# Add / Edit Camera Configuration Dialog
# ---------------------------------------------------------------------------
class CameraConfigDialog(QDialog):
    """Modal dialog for adding or editing a camera configuration."""

    def __init__(self, config: Optional[CameraConfig] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Add Camera" if config is None else f"Camera Settings — {config.name}")
        scr = (parent.screen() if parent else None) or QApplication.primaryScreen()
        avail = scr.availableGeometry() if scr else None
        self.resize(
            min(460, avail.width() - 40) if avail else 460,
            min(440, avail.height() - 40) if avail else 440,
        )
        self._initial = config

        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)

        form = QFormLayout()
        form.setSpacing(10)

        self.edt_name = QLineEdit(config.name if config else "Camera 01")
        form.addRow("Camera Name", self.edt_name)

        self.cmb_proto = QComboBox()
        self.cmb_proto.addItem("LAN Protocol TCP 8800 (Compatible with V380 cameras)", "lan8800")
        self.cmb_proto.addItem("RTSP / ONVIF Stream (TCP 554)", "rtsp")
        self.cmb_proto.addItem("Synthetic Test Pattern (Simulator)", "demo")
        if config:
            norm_proto = "lan8800" if config.protocol in ("lan8800", "v380") else config.protocol
            idx = self.cmb_proto.findData(norm_proto)
            if idx >= 0:
                self.cmb_proto.setCurrentIndex(idx)
        self.cmb_proto.currentIndexChanged.connect(self._update_field_states)
        form.addRow("Protocol", self.cmb_proto)

        ip_row = QHBoxLayout()
        self.edt_ip = QLineEdit(config.ip if config else "192.168.1.100")
        self.edt_ip.setPlaceholderText("192.168.1.100")
        ip_row.addWidget(self.edt_ip, 1)

        self.btn_scan = QPushButton("Discover")
        self.btn_scan.setIcon(lucide_icon("radar", "#e4e4e7", 14))
        self.btn_scan.setAutoDefault(False)
        self.btn_scan.setDefault(False)
        self.btn_scan.clicked.connect(self._open_scan)
        ip_row.addWidget(self.btn_scan)
        form.addRow("IP Address", ip_row)

        self.spn_port = QSpinBox()
        self.spn_port.setRange(1, 65535)
        self.spn_port.setValue(config.port if config else 8800)
        form.addRow("TCP Port", self.spn_port)

        self.edt_dev_id = QLineEdit(config.device_id if config else "")
        self.edt_dev_id.setPlaceholderText("8-digit Device ID")
        form.addRow("Device ID", self.edt_dev_id)

        self.edt_user = QLineEdit(config.username if config else "admin")
        form.addRow("Username", self.edt_user)

        self.edt_pass = QLineEdit(config.password if config else "")
        self.edt_pass.setEchoMode(QLineEdit.EchoMode.PasswordEchoOnEdit)
        self.edt_pass.setPlaceholderText("Camera password")
        form.addRow("Password", self.edt_pass)

        self.cmb_quality = QComboBox()
        self.cmb_quality.addItem("HD (Main Stream)", 1)
        self.cmb_quality.addItem("SD (Sub Stream)", 0)
        if config:
            q_idx = self.cmb_quality.findData(config.quality)
            if q_idx >= 0:
                self.cmb_quality.setCurrentIndex(q_idx)
        form.addRow("Stream Profile", self.cmb_quality)

        self.edt_rtsp = QLineEdit(config.rtsp_url if config else "")
        self.edt_rtsp.setPlaceholderText("rtsp://user:pass@192.168.1.100:554/live/ch00_1")
        form.addRow("RTSP URL", self.edt_rtsp)

        self.cmb_sensitivity = QComboBox()
        self.cmb_sensitivity.addItem("Ultra (Far Range — Detects Distant People)", "ultra")
        self.cmb_sensitivity.addItem("High (Extended Range)", "high")
        self.cmb_sensitivity.addItem("Standard (Close / Mid Range)", "standard")
        if config:
            s_idx = self.cmb_sensitivity.findData(getattr(config, "detection_sensitivity", "ultra"))
            if s_idx >= 0:
                self.cmb_sensitivity.setCurrentIndex(s_idx)
        form.addRow("Detection Sensitivity", self.cmb_sensitivity)

        chime_row = QHBoxLayout()
        self.chk_det_sound = QCheckBox("Play gentle chime on person detection")
        self.chk_det_sound.setChecked(bool(getattr(config, "detection_sound", True)) if config else True)
        chime_row.addWidget(self.chk_det_sound, 1)

        self.cmb_det_cooldown = QComboBox()
        self.cmb_det_cooldown.addItem("5s Cooldown", 5)
        self.cmb_det_cooldown.addItem("10s Cooldown", 10)
        self.cmb_det_cooldown.addItem("20s Cooldown", 20)
        init_cd = int(getattr(config, "detection_sound_cooldown", 10) or 10) if config else 10
        cd_idx = self.cmb_det_cooldown.findData(init_cd if init_cd in (5, 10, 20) else 10)
        if cd_idx >= 0:
            self.cmb_det_cooldown.setCurrentIndex(cd_idx)
        chime_row.addWidget(self.cmb_det_cooldown)
        form.addRow("Detection Chime", chime_row)

        gpu_caps = get_gpu_capabilities()
        gpu_summary = gpu_caps.get("summary", "OpenCL + D3D11VA")
        self.chk_gpu = QCheckBox(f"Use GPU Acceleration ({gpu_summary})")
        self.chk_gpu.setChecked(bool(getattr(config, "gpu_acceleration", True)) if config else True)
        form.addRow("", self.chk_gpu)

        self.chk_audio = QCheckBox("Enable live audio stream (AAC / G.711 A-Law / ADPCM)")
        self.chk_audio.setChecked(config.audio_enabled if config else True)
        form.addRow("", self.chk_audio)

        layout.addLayout(form)
        layout.addStretch()

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setObjectName("primaryBtn")
            save_btn.setDefault(True)
            save_btn.setAutoDefault(True)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._update_field_states()

    def _update_field_states(self) -> None:
        proto = self.cmb_proto.currentData()
        is_demo = proto == "demo"
        is_rtsp = proto == "rtsp"

        self.edt_ip.setEnabled(not is_demo)
        self.btn_scan.setEnabled(not is_demo)
        self.spn_port.setEnabled(proto in ("lan8800", "v380"))
        self.edt_dev_id.setEnabled(proto in ("lan8800", "v380"))
        self.edt_user.setEnabled(not is_demo)
        self.edt_pass.setEnabled(not is_demo)
        self.edt_rtsp.setEnabled(is_rtsp)

    def _open_scan(self) -> None:
        dlg = DiscoveryDialog(self)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_camera:
            cam = dlg.selected_camera
            self.edt_ip.setText(cam.ip)
            self.spn_port.setValue(cam.port)
            if cam.dev_id:
                self.edt_dev_id.setText(cam.dev_id)
                if not self.edt_user.text().strip() or self.edt_user.text().strip() == "admin":
                    self.edt_user.setText(cam.dev_id)
            if self._initial is None and (not self.edt_name.text().strip() or self.edt_name.text() == "Camera 01"):
                self.edt_name.setText(cam.name or f"Camera ({cam.ip})")

    def get_config(self) -> CameraConfig:
        cam_id = self._initial.cam_id if self._initial else uuid.uuid4().hex[:8]
        dev_id = self.edt_dev_id.text().strip()
        user = self.edt_user.text().strip() or dev_id or "admin"
        return CameraConfig(
            cam_id=cam_id,
            name=self.edt_name.text().strip() or "Camera",
            protocol=self.cmb_proto.currentData(),
            ip=self.edt_ip.text().strip() or "192.168.1.100",
            port=self.spn_port.value(),
            device_id=dev_id,
            username=user,
            password=self.edt_pass.text(),
            quality=int(self.cmb_quality.currentData()),
            rtsp_url=self.edt_rtsp.text().strip(),
            audio_enabled=self.chk_audio.isChecked(),
            light_mode=self._initial.light_mode if self._initial else "auto",
            image_mode=self._initial.image_mode if self._initial else "auto",
            flipped=self._initial.flipped if self._initial else False,
            person_detection=bool(getattr(self._initial, "person_detection", True)) if self._initial else True,
            detection_sensitivity=str(self.cmb_sensitivity.currentData() or "ultra"),
            detection_sound=self.chk_det_sound.isChecked(),
            detection_sound_cooldown=int(self.cmb_det_cooldown.currentData() or 10),
            chime_zone=CameraConfig.normalize_chime_zone(getattr(self._initial, "chime_zone", None)) if self._initial else None,
            gpu_acceleration=self.chk_gpu.isChecked(),
            ptz_presets=list(self._initial.ptz_presets) if (self._initial and self._initial.ptz_presets) else [],
            ignored_objects=list(getattr(self._initial, "ignored_objects", None) or []) if self._initial else [],
        )


# ---------------------------------------------------------------------------
# Video Viewport Canvas & NVR Tile
# ---------------------------------------------------------------------------
class CanvasToast(QFrame):
    """Floating HUD notification pill over camera video canvas."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setObjectName("canvasToast")
        self.setStyleSheet("""
            QFrame#canvasToast {
                background-color: rgba(14, 14, 18, 0.94);
                border: 1px solid #27272a;
                border-radius: 6px;
            }
        """)
        self._layout = QHBoxLayout(self)
        self._layout.setContentsMargins(10, 5, 10, 5)
        self._layout.setSpacing(8)

        self.icon_lbl = QLabel()
        self._layout.addWidget(self.icon_lbl)

        self.msg_lbl = QLabel()
        self.msg_lbl.setStyleSheet("color: #f4f4f5; font-size: 11px; font-weight: 600;")
        self._layout.addWidget(self.msg_lbl)

        self.btn_open = QPushButton("Open")
        self.btn_open.setFixedHeight(22)
        self.btn_open.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_open.setStyleSheet("""
            QPushButton {
                background-color: #1e293b;
                color: #38bdf8;
                border: 1px solid #334155;
                border-radius: 4px;
                padding: 1px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #38bdf8;
                color: #09090b;
            }
        """)
        self._layout.addWidget(self.btn_open)

        self.btn_folder = QPushButton("Folder")
        self.btn_folder.setFixedHeight(22)
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.setStyleSheet("""
            QPushButton {
                background-color: #27272a;
                color: #a1a1aa;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 1px 10px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #3f3f46;
                color: #f4f4f5;
            }
        """)
        self._layout.addWidget(self.btn_folder)

        self._hide_timer = QTimer(self)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(self.hide)

        self._current_path: str = ""
        self.btn_open.clicked.connect(self._on_open_clicked)
        self.btn_folder.clicked.connect(self._on_folder_clicked)

    def _on_open_clicked(self) -> None:
        if self._current_path and os.path.exists(self._current_path):
            try:
                os.startfile(self._current_path)
            except Exception:
                pass
        self.hide()

    def _on_folder_clicked(self) -> None:
        folder = Path(self._current_path).parent if self._current_path else SNAPSHOT_DIR
        try:
            folder.mkdir(parents=True, exist_ok=True)
            os.startfile(str(folder))
        except Exception:
            pass
        self.hide()

    def show_success(self, filepath: str, timeout_ms: int = 4500) -> None:
        self._current_path = filepath
        fname = Path(filepath).name
        self.icon_lbl.setPixmap(lucide_pixmap("check", "#10b981", 14))
        self.msg_lbl.setText(f"Photo Saved: {fname}")
        self.btn_open.setVisible(True)
        self.btn_folder.setVisible(True)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(timeout_ms)

    def show_warning(self, msg: str, timeout_ms: int = 3500) -> None:
        self._current_path = ""
        self.icon_lbl.setPixmap(lucide_pixmap("alert_triangle", "#f59e0b", 14))
        self.msg_lbl.setText(msg)
        self.btn_open.setVisible(False)
        self.btn_folder.setVisible(False)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(timeout_ms)

    def show_info(self, msg: str, timeout_ms: int = 3200) -> None:
        self._current_path = ""
        self.icon_lbl.setPixmap(lucide_pixmap("check", "#10b981", 14))
        self.msg_lbl.setText(msg)
        self.btn_open.setVisible(False)
        self.btn_folder.setVisible(False)
        self.adjustSize()
        self._reposition()
        self.show()
        self.raise_()
        self._hide_timer.start(timeout_ms)

    def _reposition(self) -> None:
        if self.parentWidget():
            p_rect = self.parentWidget().rect()
            x = (p_rect.width() - self.width()) // 2
            y = 10
            self.move(max(8, x), max(8, y))


class VideoCanvas(QWidget):
    """
    Hardware-smooth video rendering surface with:
    - Aspect-ratio preserving fit on deep obsidian background (#050507)
    - Digital zoom (mouse wheel 1.0x .. 5.0x) and click-drag pan
    - Double-click to maximize/restore tile
    - Camera shutter flash visual feedback & toast banner
    """

    clicked = pyqtSignal()
    double_clicked = pyqtSignal()
    drag_requested = pyqtSignal()
    reconnect_requested = pyqtSignal()
    dismiss_detection_requested = pyqtSignal(tuple)
    chime_zone_changed = pyqtSignal(object)
    chime_zone_edit_toggled = pyqtSignal(bool)
    ptz_pressed = pyqtSignal(str)
    ptz_released = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(140, 90)
        self.setMouseTracking(True)
        self._qimage: Optional[QImage] = None
        self._last_frame: Optional[QImage] = None
        self._blurred_last_frame: Optional[QImage] = None
        self._show_reconnect_overlay: bool = False
        self._has_disconnected: bool = False
        self._status_text: str = "Standby"
        self._is_connected: bool = False
        self._download_progress: Optional[int] = None
        self._zoom: float = 1.0
        self._pan_offset = QPoint(0, 0)
        self._drag_last: Optional[QPoint] = None
        self._mouse_down_pos: Optional[QPoint] = None
        self._hover_pos: Optional[QPoint] = None
        self._detections: List[tuple] = []
        self._live_detections: List[tuple] = []
        self._pinned_detections: List[tuple] = []
        self._last_detection_ts: float = 0.0
        self._dismiss_hit_rects: List[Tuple[QRect, tuple]] = []
        self._box_hit_rects: List[Tuple[QRect, tuple]] = []
        self._chime_zone: Optional[List[float]] = None
        self._editing_chime_zone: bool = False
        self._chime_drag_start: Optional[Tuple[float, float]] = None
        self._chime_drag_curr: Optional[Tuple[float, float]] = None
        self._chime_done_rect: QRect = QRect()
        self._chime_reset_rect: QRect = QRect()
        self._flash_step: int = 0
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(95)
        self._flash_timer.timeout.connect(self._on_flash_tick)

        # Hold timer so detection boxes don't disappear while user moves mouse to mark 'Not a Person'
        self._det_hold_timer = QTimer(self)
        self._det_hold_timer.setSingleShot(True)
        self._det_hold_timer.timeout.connect(self._on_det_hold_expired)

        # Shutter flash animation (fade-out white flash overlay)
        self._shutter_alpha: float = 0.0
        self._shutter_timer = QTimer(self)
        self._shutter_timer.setInterval(16)
        self._shutter_timer.timeout.connect(self._on_shutter_tick)

        # Smooth animation timer for connecting radar waves and pulses
        self._anim_step: int = 0
        self._anim_timer = QTimer(self)
        self._anim_timer.setInterval(45)
        self._anim_timer.timeout.connect(self._on_anim_tick)
        self._anim_timer.start()

        self._cam_name: str = ""
        self._cam_endpoint: str = ""

        # Floating HUD toast notification pill
        self._toast = CanvasToast(self)
        self._toast.hide()

        # Reconnect button overlay for disconnected camera state
        self.btn_reconnect = QPushButton("  Reconnect", self)
        self.btn_reconnect.setIcon(lucide_icon("refresh", "#ffffff", 14))
        self.btn_reconnect.setIconSize(QSize(14, 14))
        self.btn_reconnect.setFixedSize(134, 34)
        self.btn_reconnect.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reconnect.setToolTip("Wake sleeping camera & reconnect live stream")
        self.btn_reconnect.setStyleSheet("""
            QPushButton {
                background-color: rgba(37, 99, 235, 0.92);
                border: 1px solid #60a5fa;
                border-radius: 17px;
                color: #ffffff;
                font-family: "Segoe UI", sans-serif;
                font-size: 12px;
                font-weight: 600;
                padding: 0 14px;
            }
            QPushButton:hover {
                background-color: #3b82f6;
                border-color: #93c5fd;
                color: #ffffff;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
            }
        """)
        self.btn_reconnect.hide()
        self.btn_reconnect.clicked.connect(self.reconnect_requested.emit)

        # Direct On-Video PTZ Hover Edge Buttons (Up / Down / Left / Right)
        self._ptz_overlay_enabled: bool = False
        self._ptz_edge_btns: Dict[str, QPushButton] = {}
        ptz_specs = [
            ("up", "chevron_up", "Tilt Camera Up (Hold)"),
            ("down", "chevron_down", "Tilt Camera Down (Hold)"),
            ("left", "chevron_left", "Pan Camera Left (Hold)"),
            ("right", "chevron_right", "Pan Camera Right (Hold)"),
        ]
        ptz_btn_style = """
            QPushButton {
                background-color: rgba(14, 14, 20, 0.72);
                border: 1px solid rgba(82, 82, 91, 0.75);
                border-radius: 13px;
            }
            QPushButton:hover {
                background-color: rgba(30, 58, 138, 0.90);
                border-color: #38bdf8;
            }
            QPushButton:pressed {
                background-color: #2563eb;
                border-color: #93c5fd;
            }
        """
        for d_key, ico_name, tip in ptz_specs:
            b = QPushButton(self)
            b.setIcon(lucide_icon(ico_name, "#f4f4f5", 14))
            b.setIconSize(QSize(14, 14))
            b.setFixedSize(26, 26)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip)
            b.setStyleSheet(ptz_btn_style)
            b.hide()
            b.pressed.connect(lambda d=d_key: self.ptz_pressed.emit(d))
            b.released.connect(self.ptz_released.emit)
            self._ptz_edge_btns[d_key] = b

    def set_ptz_overlay_enabled(self, enabled: bool) -> None:
        self._ptz_overlay_enabled = bool(enabled)
        self._sync_ptz_edge_buttons()

    def _reposition_ptz_edge_buttons(self) -> None:
        if not getattr(self, "_ptz_edge_btns", None):
            return
        r = self.rect()
        cx, cy = r.center().x(), r.center().y()
        pad = 7
        if "up" in self._ptz_edge_btns:
            self._ptz_edge_btns["up"].move(max(4, cx - 13), pad)
        if "down" in self._ptz_edge_btns:
            self._ptz_edge_btns["down"].move(max(4, cx - 13), max(4, r.height() - 26 - pad))
        if "left" in self._ptz_edge_btns:
            self._ptz_edge_btns["left"].move(pad, max(4, cy - 13))
        if "right" in self._ptz_edge_btns:
            self._ptz_edge_btns["right"].move(max(4, r.width() - 26 - pad), max(4, cy - 13))

    def _sync_ptz_edge_buttons(self) -> None:
        if not getattr(self, "_ptz_edge_btns", None):
            return
        show = (
            self._ptz_overlay_enabled
            and self._is_connected
            and not getattr(self, "_editing_chime_zone", False)
            and (self._hover_pos is not None or self.underMouse())
            and self.width() >= 180
            and self.height() >= 110
        )
        if show:
            self._reposition_ptz_edge_buttons()
            for b in self._ptz_edge_btns.values():
                if not b.isVisible():
                    b.show()
                    b.raise_()
        else:
            for b in self._ptz_edge_btns.values():
                if b.isVisible():
                    b.hide()

    def set_reconnect_overlay_enabled(self, enabled: bool) -> None:
        self._show_reconnect_overlay = bool(enabled)
        self._sync_reconnect_button()

    def set_last_frame(self, image: Optional[QImage]) -> None:
        if image is not None and not image.isNull():
            self._last_frame = image.copy()
            self._blurred_last_frame = None
            self.update()

    def _get_blurred_last_frame(self) -> Optional[QImage]:
        if self._last_frame is None or self._last_frame.isNull():
            return None
        if self._blurred_last_frame is not None and not self._blurred_last_frame.isNull():
            return self._blurred_last_frame
        try:
            import cv2
            import numpy as np

            small = self._last_frame.scaled(
                320,
                180,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ).convertToFormat(QImage.Format.Format_RGB888)
            w, h = small.width(), small.height()
            bpl = small.bytesPerLine()
            ptr = small.bits()
            ptr.setsize(h * bpl)
            arr = np.frombuffer(ptr, dtype=np.uint8).reshape((h, bpl))[:, : w * 3].reshape((h, w, 3)).copy()
            blurred = cv2.GaussianBlur(arr, (35, 35), 11.0)
            blurred = cv2.GaussianBlur(blurred, (25, 25), 7.0)
            blurred = (blurred.astype(np.float32) * 0.62).astype(np.uint8)
            self._blurred_last_frame = QImage(
                blurred.data, w, h, w * 3, QImage.Format.Format_RGB888
            ).copy()
            return self._blurred_last_frame
        except Exception:
            tiny = self._last_frame.scaled(
                48,
                27,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._blurred_last_frame = tiny.scaled(
                320,
                180,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            return self._blurred_last_frame

    def _reposition_reconnect_button(self) -> None:
        if hasattr(self, "btn_reconnect"):
            r = self.rect()
            bx = (r.width() - self.btn_reconnect.width()) // 2
            by = r.center().y() + 8
            self.btn_reconnect.move(max(4, bx), max(4, by))

    def _is_disconnected_state(self) -> bool:
        if self._is_connected or (self._qimage is not None and not self._qimage.isNull()):
            return False
        st = (self._status_text or "").lower()
        is_downloading = (
            self._download_progress is not None
            or any(w in st for w in ("downloading", "backing up", "exporting"))
        )
        if is_downloading:
            return False
        is_disc_word = any(
            w in st
            for w in (
                "stopped",
                "standby",
                "offline",
                "idle",
                "disconnected",
                "error",
                "failed",
                "invalid",
                "lost",
                "cannot connect",
                "retrying",
                "reconnecting",
            )
        )
        return is_disc_word or self._has_disconnected

    def _sync_reconnect_button(self) -> None:
        if not hasattr(self, "btn_reconnect"):
            return
        should_show = (
            self._show_reconnect_overlay
            and self._is_disconnected_state()
            and not getattr(self, "_editing_chime_zone", False)
        )
        if should_show:
            self._reposition_reconnect_button()
            if not self.btn_reconnect.isVisible():
                self.btn_reconnect.show()
                self.btn_reconnect.raise_()
        else:
            if self.btn_reconnect.isVisible():
                self.btn_reconnect.hide()

    def set_chime_zone(self, zone: Optional[List[float]]) -> None:
        self._chime_zone = CameraConfig.normalize_chime_zone(zone)
        self.update()

    def set_chime_zone_edit_mode(self, enabled: bool) -> None:
        new_mode = bool(enabled)
        if self._editing_chime_zone == new_mode and not new_mode:
            return
        self._editing_chime_zone = new_mode
        self._chime_drag_start = None
        self._chime_drag_curr = None
        if self._editing_chime_zone:
            self._zoom = 1.0
            self._pan_offset = QPoint(0, 0)
            self.setCursor(Qt.CursorShape.CrossCursor)
        else:
            self.unsetCursor()
        self._sync_reconnect_button()
        self._sync_ptz_edge_buttons()
        self.chime_zone_edit_toggled.emit(self._editing_chime_zone)
        self.update()

    def _get_video_target_rect(self) -> QRect:
        rect = self.rect()
        img = self._qimage if (self._qimage is not None and not self._qimage.isNull()) else self._last_frame
        if img is not None and not img.isNull() and img.width() > 0 and img.height() > 0:
            img_w = img.width()
            img_h = img.height()
            scale = min(rect.width() / img_w, rect.height() / img_h) * (
                1.0 if self._editing_chime_zone else self._zoom
            )
            draw_w = max(1, int(img_w * scale))
            draw_h = max(1, int(img_h * scale))
            cx = rect.center().x() + (0 if self._editing_chime_zone else self._pan_offset.x())
            cy = rect.center().y() + (0 if self._editing_chime_zone else self._pan_offset.y())
            return QRect(cx - draw_w // 2, cy - draw_h // 2, draw_w, draw_h)
        # Fallback 16:9 rect when no frame has ever been received yet
        avail = rect.adjusted(10, 10, -10, -10)
        if avail.width() <= 0 or avail.height() <= 0:
            return rect
        scale = min(avail.width() / 16.0, avail.height() / 9.0)
        draw_w = max(1, int(16.0 * scale))
        draw_h = max(1, int(9.0 * scale))
        return QRect(rect.center().x() - draw_w // 2, rect.center().y() - draw_h // 2, draw_w, draw_h)

    def _pos_to_norm(self, pos: QPoint) -> Tuple[float, float]:
        target = self._get_video_target_rect()
        tw = max(1, target.width())
        th = max(1, target.height())
        xn = max(0.0, min(1.0, (pos.x() - target.left()) / float(tw)))
        yn = max(0.0, min(1.0, (pos.y() - target.top()) / float(th)))
        return (xn, yn)

    def notify_manual_connect_started(self) -> None:
        self._has_disconnected = False
        self._sync_reconnect_button()
        self._sync_ptz_edge_buttons()
        self.update()

    def set_camera_info(self, name: str, endpoint: str) -> None:
        self._cam_name = name
        self._cam_endpoint = endpoint
        self.update()

    def _on_anim_tick(self) -> None:
        self._anim_step = (self._anim_step + 1) % 120
        self.update()

    def _on_flash_tick(self) -> None:
        self._flash_step = (self._flash_step + 1) % 20
        self.update()

    def set_detections(self, boxes: list, force_clear: bool = False) -> None:
        import time as _time

        now_t = _time.monotonic()
        if force_clear:
            self._live_detections = []
            self._pinned_detections = []
            self._detections = []
            self._det_hold_timer.stop()
            if self._flash_timer.isActive():
                self._flash_timer.stop()
            self.update()
            return

        if boxes:
            self._live_detections = list(boxes)
            self._pinned_detections = list(boxes)
            self._last_detection_ts = now_t
            self._detections = list(boxes)
            self._det_hold_timer.stop()
            if not self._flash_timer.isActive():
                self._flash_step = 0
                self._flash_timer.start()
            self.update()
            return

        # Live boxes became empty: if hovering on the camera view or within the 2.2s grace hold, keep boxes visible!
        self._live_detections = []
        if self._hover_pos is not None and self._pinned_detections:
            self._detections = list(self._pinned_detections)
            self.update()
            return

        elapsed = now_t - self._last_detection_ts
        if self._pinned_detections and elapsed < 2.2:
            remaining_ms = max(200, int((2.2 - elapsed) * 1000))
            if not self._det_hold_timer.isActive():
                self._det_hold_timer.start(remaining_ms)
            return

        self._detections = []
        if self._flash_timer.isActive():
            self._flash_timer.stop()
        self.update()

    def _on_det_hold_expired(self) -> None:
        if self._live_detections:
            return
        if self._hover_pos is not None and self._pinned_detections:
            # User is currently hovering over the camera view — keep boxes pinned!
            return
        self._detections = []
        if self._flash_timer.isActive():
            self._flash_timer.stop()
        self.update()

    def remove_pinned_box(self, dismissed_box: Tuple[float, float, float, float]) -> None:
        from stream_worker import ClassicalPersonDetector

        self._pinned_detections = [
            b
            for b in self._pinned_detections
            if len(b) < 4
            or ClassicalPersonDetector._box_iou(
                dismissed_box, (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            )
            < 0.30
        ]
        self._live_detections = [
            b
            for b in self._live_detections
            if len(b) < 4
            or ClassicalPersonDetector._box_iou(
                dismissed_box, (float(b[0]), float(b[1]), float(b[2]), float(b[3]))
            )
            < 0.30
        ]
        self._detections = list(self._live_detections or self._pinned_detections)
        if not self._detections and self._flash_timer.isActive():
            self._flash_timer.stop()
        self.update()

    def set_frame(self, image: QImage) -> None:
        self._qimage = image
        self._is_connected = True
        self._has_disconnected = False
        if image is not None and not image.isNull():
            self._last_frame = image
            self._blurred_last_frame = None
        if self._anim_timer.isActive():
            self._anim_timer.stop()
        self._sync_reconnect_button()
        self._sync_ptz_edge_buttons()
        self.update()

    def set_status(self, text: str, connected: bool) -> None:
        self._status_text = text
        self._is_connected = connected
        low = (text or "").lower()
        if not any(w in low for w in ("downloading", "backing up", "exporting")):
            self._download_progress = None
        if not connected:
            self._qimage = None
            self.set_detections([], force_clear=True)
            if any(
                w in low
                for w in (
                    "stopped",
                    "standby",
                    "offline",
                    "idle",
                    "disconnected",
                    "error",
                    "failed",
                    "invalid",
                    "lost",
                    "cannot connect",
                    "retrying",
                    "reconnecting",
                )
            ):
                self._has_disconnected = True
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        else:
            self._has_disconnected = False
            if self._anim_timer.isActive():
                self._anim_timer.stop()
        self._sync_reconnect_button()
        self._sync_ptz_edge_buttons()
        self.update()

    def set_download_progress(self, pct: Optional[int]) -> None:
        self._download_progress = max(0, min(100, int(pct))) if pct is not None else None
        if pct is not None and not self._anim_timer.isActive():
            self._anim_timer.start()
        self.update()

    def reset_zoom(self) -> None:
        self._zoom = 1.0
        self._pan_offset = QPoint(0, 0)
        self.update()

    def trigger_shutter_flash(self) -> None:
        self._shutter_alpha = 1.0
        self._shutter_timer.start()
        self.update()

    def _on_shutter_tick(self) -> None:
        self._shutter_alpha -= 0.14
        if self._shutter_alpha <= 0.0:
            self._shutter_alpha = 0.0
            self._shutter_timer.stop()
        self.update()

    def show_snapshot_toast(self, filepath: str) -> None:
        self._toast.show_success(filepath)

    def show_error_toast(self, msg: str) -> None:
        self._toast.show_warning(msg)

    def show_info_toast(self, msg: str) -> None:
        self._toast.show_info(msg)

    def detection_at_pos(self, pos: QPoint) -> Optional[tuple]:
        for pill_rect, det_box in self._dismiss_hit_rects:
            if pill_rect.contains(pos):
                return det_box
        for box_rect, det_box in self._box_hit_rects:
            if box_rect.adjusted(-8, -8, 8, 8).contains(pos):
                return det_box
        return None

    def leaveEvent(self, event) -> None:
        self._hover_pos = None
        if not self._editing_chime_zone:
            self.unsetCursor()
        if not self._live_detections and self._detections:
            # Give a 1.5s grace period after cursor leaves (e.g. when opening right-click menu) before hiding
            self._det_hold_timer.start(1500)
        self._sync_ptz_edge_buttons()
        self.update()
        super().leaveEvent(event)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_toast") and self._toast.isVisible():
            self._toast._reposition()
        self._reposition_reconnect_button()
        self._reposition_ptz_edge_buttons()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._editing_chime_zone:
            event.accept()
            return
        delta = event.angleDelta().y()
        if delta > 0:
            self._zoom = min(5.0, self._zoom * 1.15)
        elif delta < 0:
            self._zoom = max(1.0, self._zoom / 1.15)
            if self._zoom <= 1.02:
                self._zoom = 1.0
                self._pan_offset = QPoint(0, 0)
        self.update()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        self.clicked.emit()
        if self._editing_chime_zone:
            if event.button() == Qt.MouseButton.LeftButton:
                if self._chime_done_rect.contains(event.pos()):
                    self.set_chime_zone_edit_mode(False)
                    event.accept()
                    return
                if self._chime_reset_rect.contains(event.pos()):
                    self._chime_zone = None
                    self._chime_drag_start = None
                    self._chime_drag_curr = None
                    self.chime_zone_changed.emit(None)
                    self.update()
                    event.accept()
                    return
                self._chime_drag_start = self._pos_to_norm(event.pos())
                self._chime_drag_curr = self._chime_drag_start
                self.update()
                event.accept()
                return
            elif event.button() == Qt.MouseButton.RightButton:
                self.set_chime_zone_edit_mode(False)
                event.accept()
                return
        if event.button() == Qt.MouseButton.LeftButton:
            for pill_rect, det_box in self._dismiss_hit_rects:
                if pill_rect.contains(event.pos()):
                    self.dismiss_detection_requested.emit(det_box)
                    event.accept()
                    return
            if self._zoom > 1.0:
                self._drag_last = event.pos()
            else:
                self._mouse_down_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        import time as _time

        self._hover_pos = event.pos()
        self._det_hold_timer.stop()
        self._sync_ptz_edge_buttons()

        if self._editing_chime_zone:
            if self._chime_drag_start is not None and (event.buttons() & Qt.MouseButton.LeftButton):
                self._chime_drag_curr = self._pos_to_norm(event.pos())
                self.setCursor(Qt.CursorShape.CrossCursor)
                self.update()
                event.accept()
                return
            if self._chime_done_rect.contains(event.pos()) or self._chime_reset_rect.contains(event.pos()):
                self.setCursor(Qt.CursorShape.PointingHandCursor)
            else:
                self.setCursor(Qt.CursorShape.CrossCursor)
            self.update()
            event.accept()
            return

        # If a detection happened recently (within 8s) and flickered off right before the user hovered, restore it!
        if (
            not self._detections
            and self._pinned_detections
            and (_time.monotonic() - self._last_detection_ts) <= 8.0
        ):
            self._detections = list(self._pinned_detections)
            if not self._flash_timer.isActive():
                self._flash_step = 0
                self._flash_timer.start()
        over_pill = any(pr.contains(event.pos()) for pr, _ in self._dismiss_hit_rects)
        if over_pill:
            self.setCursor(Qt.CursorShape.PointingHandCursor)
        else:
            self.unsetCursor()
        if self._detections or self._chime_zone is not None:
            self.update()
        if self._drag_last is not None and self._zoom > 1.0:
            diff = event.pos() - self._drag_last
            self._pan_offset += diff
            self._drag_last = event.pos()
            self.update()
        elif (
            (event.buttons() & Qt.MouseButton.LeftButton)
            and self._zoom <= 1.0
            and getattr(self, "_mouse_down_pos", None) is not None
        ):
            if (event.pos() - self._mouse_down_pos).manhattanLength() >= QApplication.startDragDistance():
                self._mouse_down_pos = None
                self.drag_requested.emit()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if (
            self._editing_chime_zone
            and event.button() == Qt.MouseButton.LeftButton
            and self._chime_drag_start is not None
        ):
            end_pt = self._pos_to_norm(event.pos())
            x1 = min(self._chime_drag_start[0], end_pt[0])
            y1 = min(self._chime_drag_start[1], end_pt[1])
            x2 = max(self._chime_drag_start[0], end_pt[0])
            y2 = max(self._chime_drag_start[1], end_pt[1])
            self._chime_drag_start = None
            self._chime_drag_curr = None
            if (x2 - x1) >= 0.03 and (y2 - y1) >= 0.03:
                norm = CameraConfig.normalize_chime_zone([x1, y1, x2, y2])
                self._chime_zone = norm
                self.chime_zone_changed.emit(self._chime_zone)
            self.update()
            event.accept()
            return
        self._drag_last = None
        self._mouse_down_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
        if self._editing_chime_zone:
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self.double_clicked.emit()
        super().mouseDoubleClickEvent(event)

    def paintEvent(self, event) -> None:
        _ = event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        painter.fillRect(rect, QColor("#050507"))

        self._dismiss_hit_rects.clear()
        self._box_hit_rects.clear()

        if self._qimage is not None and not self._qimage.isNull():
            img_w = self._qimage.width()
            img_h = self._qimage.height()
            if img_w > 0 and img_h > 0:
                scale = min(rect.width() / img_w, rect.height() / img_h) * self._zoom
                draw_w = int(img_w * scale)
                draw_h = int(img_h * scale)
                cx = rect.center().x() + self._pan_offset.x()
                cy = rect.center().y() + self._pan_offset.y()
                target = QRect(cx - draw_w // 2, cy - draw_h // 2, draw_w, draw_h)
                painter.drawImage(target, self._qimage)

                # --- Person Detection Subject Corner Brackets (4 Corners Only, No Sides, No Label) ---
                if self._detections:
                    for det in self._detections:
                        if len(det) < 4:
                            continue
                        x1n, y1n, x2n, y2n = float(det[0]), float(det[1]), float(det[2]), float(det[3])
                        det_box = (x1n, y1n, x2n, y2n)

                        bx = int(target.left() + x1n * target.width())
                        by = int(target.top() + y1n * target.height())
                        bw = max(16, int((x2n - x1n) * target.width()))
                        bh = max(24, int((y2n - y1n) * target.height()))
                        box_rect = QRect(bx, by, bw, bh)
                        self._box_hit_rects.append((box_rect, det_box))

                        # 4 corner L-brackets around the subject (no rectangle sides, no fill, no text label)
                        arm = max(8, min(20, min(bw, bh) // 4))
                        pen_box = QPen(QColor("#ff3b3b"), 2.6)
                        pen_box.setCapStyle(Qt.PenCapStyle.RoundCap)
                        painter.setPen(pen_box)
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        x_l, y_t, x_r, y_b = box_rect.left(), box_rect.top(), box_rect.right(), box_rect.bottom()
                        # Top-Left corner
                        painter.drawLine(x_l, y_t, x_l + arm, y_t)
                        painter.drawLine(x_l, y_t, x_l, y_t + arm)
                        # Top-Right corner
                        painter.drawLine(x_r - arm, y_t, x_r, y_t)
                        painter.drawLine(x_r, y_t, x_r, y_t + arm)
                        # Bottom-Left corner
                        painter.drawLine(x_l, y_b - arm, x_l, y_b)
                        painter.drawLine(x_l, y_b, x_l + arm, y_b)
                        # Bottom-Right corner
                        painter.drawLine(x_r - arm, y_b, x_r, y_b)
                        painter.drawLine(x_r, y_b - arm, x_r, y_b)

                        # Interactive '× Not a Person' quick-dismiss pill when mouse hovers over the viewport
                        if self._hover_pos is not None:
                            pill_w, pill_h = 96, 20
                            px = max(rect.left() + 6, min(rect.right() - pill_w - 6, x_l))
                            py = y_t - pill_h - 4 if (y_t - pill_h - 4) >= (rect.top() + 6) else (y_t + 4)
                            pill_rect = QRect(px, py, pill_w, pill_h)
                            self._dismiss_hit_rects.append((pill_rect, det_box))
                            is_hovered = pill_rect.contains(self._hover_pos)
                            painter.setPen(
                                QPen(QColor("#f87171" if is_hovered else "#ef4444"), 1.1)
                            )
                            painter.setBrush(
                                QColor(185, 28, 28, 240) if is_hovered else QColor(15, 15, 20, 225)
                            )
                            painter.drawRoundedRect(pill_rect, 4, 4)
                            painter.setPen(QColor("#ffffff" if is_hovered else "#fca5a5"))
                            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                            painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, "× Not a Person")

                    # --- Flashing Red Corner & Border Outline on Footage ---
                    import math as _math

                    pulse = 0.5 + 0.5 * _math.sin(self._flash_step * (_math.pi / 5.0))
                    corner_alpha = int(95 + 160 * pulse)
                    border_alpha = int(50 + 135 * pulse)
                    glow_alpha = int(20 + 55 * pulse)

                    vis_rect = target.intersected(rect).adjusted(1, 1, -2, -2)
                    if vis_rect.width() > 20 and vis_rect.height() > 20:
                        # Soft inner red perimeter glow
                        painter.setBrush(Qt.BrushStyle.NoBrush)
                        painter.setPen(QPen(QColor(239, 68, 68, glow_alpha), 6.0))
                        painter.drawRect(vis_rect.adjusted(2, 2, -2, -2))

                        # Pulsing thin red frame outline
                        painter.setPen(QPen(QColor(248, 113, 113, border_alpha), 2.0))
                        painter.drawRect(vis_rect)

                        # Bold flashing red L-brackets at all 4 corners of the footage
                        c_arm = max(18, min(34, min(vis_rect.width(), vis_rect.height()) // 6))
                        pen_corner = QPen(QColor(255, 42, 42, corner_alpha), 4.0)
                        pen_corner.setCapStyle(Qt.PenCapStyle.RoundCap)
                        painter.setPen(pen_corner)
                        vl, vt, vr, vb = vis_rect.left(), vis_rect.top(), vis_rect.right(), vis_rect.bottom()
                        # Top-Left corner
                        painter.drawLine(vl, vt, vl + c_arm, vt)
                        painter.drawLine(vl, vt, vl, vt + c_arm)
                        # Top-Right corner
                        painter.drawLine(vr - c_arm, vt, vr, vt)
                        painter.drawLine(vr, vt, vr, vt + c_arm)
                        # Bottom-Left corner
                        painter.drawLine(vl, vb - c_arm, vl, vb)
                        painter.drawLine(vl, vb, vl + c_arm, vb)
                        # Bottom-Right corner
                        painter.drawLine(vr - c_arm, vb, vr, vb)
                        painter.drawLine(vr, vb - c_arm, vr, vb)

                if self._zoom > 1.01:
                    badge_rect = QRect(rect.right() - 58, 10, 48, 22)
                    painter.setPen(QPen(QColor("#3f3f46"), 1))
                    painter.setBrush(QColor(15, 15, 18, 210))
                    painter.drawRoundedRect(badge_rect, 4, 4)
                    painter.setPen(QColor("#f4f4f5"))
                    painter.setFont(QFont("Cascadia Mono", 9, QFont.Weight.DemiBold))
                    painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, f"{self._zoom:.1f}x")
        elif self._editing_chime_zone and self._last_frame is not None and not self._last_frame.isNull():
            # When editing the chime trigger area while stream is offline, show crisp cached last frame
            target = self._get_video_target_rect()
            painter.drawImage(target, self._last_frame)
        else:
            # 0. Draw blurred last frame of footage if available
            blurred_img = self._get_blurred_last_frame()
            if blurred_img is not None and not blurred_img.isNull():
                bw = blurred_img.width()
                bh = blurred_img.height()
                if bw > 0 and bh > 0:
                    b_scale = min(rect.width() / bw, rect.height() / bh)
                    b_draw_w = int(bw * b_scale)
                    b_draw_h = int(bh * b_scale)
                    b_target = QRect(
                        rect.center().x() - b_draw_w // 2,
                        rect.center().y() - b_draw_h // 2,
                        b_draw_w,
                        b_draw_h,
                    )
                    painter.drawImage(b_target, blurred_img)
                    painter.fillRect(b_target, QColor(5, 5, 8, 110))

            # 1. Subtle camera viewport framing reticles (Corner L-brackets)
            p_pen = QPen(QColor("#1e1e24"), 1.5)
            painter.setPen(p_pen)
            c_arm = 12
            pad = 14
            vl, vt, vr, vb = rect.left() + pad, rect.top() + pad, rect.right() - pad, rect.bottom() - pad
            if vr - vl > 60 and vb - vt > 60:
                painter.drawLine(vl, vt, vl + c_arm, vt)
                painter.drawLine(vl, vt, vl, vt + c_arm)
                painter.drawLine(vr - c_arm, vt, vr, vt)
                painter.drawLine(vr, vt, vr, vt + c_arm)
                painter.drawLine(vl, vb - c_arm, vl, vb)
                painter.drawLine(vl, vb, vl + c_arm, vb)
                painter.drawLine(vr - c_arm, vb, vr, vb)
                painter.drawLine(vr, vb - c_arm, vr, vb)

            cx = rect.center().x()
            cy = rect.center().y() - 14

            st = (self._status_text or "").lower()
            is_error = any(
                w in st
                for w in ("error", "failed", "invalid", "lost", "cannot connect", "retrying", "reconnecting")
            )
            is_downloading = (
                self._download_progress is not None
                or any(w in st for w in ("downloading", "backing up", "exporting"))
            ) and not is_error
            is_review_idle = (
                any(
                    w in st
                    for w in (
                        "loaded ",
                        "select a ",
                        "click any clip",
                        "no recordings",
                        "reading microsd",
                        "syncing",
                        "complete",
                        "finished",
                    )
                )
                and not is_error
                and not is_downloading
            )
            is_stopped = (
                any(w in st for w in ("stopped", "standby", "offline", "idle", "disconnected"))
                and not is_error
            )
            is_disconnected_tile = self._show_reconnect_overlay and self._is_disconnected_state()
            is_connecting = (
                not is_stopped
                and not is_error
                and not is_downloading
                and not is_review_idle
                and not is_disconnected_tile
            )

            if is_downloading:
                import math

                pulse = 0.5 + 0.5 * math.sin(self._anim_step * 0.18)
                ring_r = 28
                ring_rect = QRectF(cx - ring_r, cy - ring_r, ring_r * 2, ring_r * 2)

                # Soft outer emerald pulse halo
                halo_r = int(ring_r + 4 + 6 * pulse)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(16, 185, 129, int(85 * (1.0 - pulse * 0.5))), 1.5))
                painter.drawEllipse(QPoint(cx, cy), halo_r, halo_r)

                # Background circle track
                painter.setPen(QPen(QColor("#13382c"), 3.5))
                painter.setBrush(QColor(6, 46, 34, 170))
                painter.drawEllipse(ring_rect)

                # Progress arc (or sweeping indeterminate arc while starting)
                arc_pen = QPen(QColor("#10b981"), 3.5)
                arc_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(arc_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                pct_val = self._download_progress or 0
                if pct_val > 0:
                    start_angle = 90 * 16
                    span_angle = -int((pct_val / 100.0) * 360 * 16)
                    painter.drawArc(ring_rect, start_angle, span_angle)
                else:
                    start_angle = int((90 - (self._anim_step * 7) % 360) * 16)
                    painter.drawArc(ring_rect, start_angle, -110 * 16)

                # Animated downward arrow + tray icon inside the ring
                bob = int(3.0 * math.sin(self._anim_step * 0.24))
                arrow_pen = QPen(QColor("#6ee7b7"), 2.4)
                arrow_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                arrow_pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(arrow_pen)
                # Arrow shaft & head
                painter.drawLine(cx, cy - 10 + bob, cx, cy + 4 + bob)
                painter.drawLine(cx - 6, cy - 2 + bob, cx, cy + 4 + bob)
                painter.drawLine(cx + 6, cy - 2 + bob, cx, cy + 4 + bob)
                # Tray at bottom of circle
                tray_pen = QPen(QColor("#34d399"), 2.2)
                tray_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(tray_pen)
                painter.drawLine(cx - 9, cy + 11, cx + 9, cy + 11)
                painter.drawLine(cx - 9, cy + 7, cx - 9, cy + 11)
                painter.drawLine(cx + 9, cy + 7, cx + 9, cy + 11)

                # Emerald DOWNLOADING pill badge
                dots = "." * ((self._anim_step // 8) % 4 + 1)
                badge_text = f"DOWNLOADING • {pct_val}%" if pct_val > 0 else f"DOWNLOADING {dots}"
                pill_w, pill_h = 186, 26
                pill_rect = QRect(cx - pill_w // 2, cy + 40, pill_w, pill_h)
                painter.setPen(QPen(QColor(16, 185, 129, 175), 1.0))
                painter.setBrush(QColor(6, 78, 59, 230))
                painter.drawRoundedRect(pill_rect, 13, 13)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(52, 211, 153, int(155 + 100 * pulse)))
                painter.drawEllipse(QPoint(cx - pill_w // 2 + 14, cy + 40 + pill_h // 2), 4, 4)

                painter.setPen(QColor("#6ee7b7"))
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(
                    QRect(cx - pill_w // 2 + 20, cy + 40, pill_w - 24, pill_h),
                    Qt.AlignmentFlag.AlignCenter,
                    badge_text,
                )

                sub_text = (self._status_text or "Downloading footage from microSD card...").strip()
                painter.setPen(QColor("#a7f3d0"))
                painter.setFont(QFont("Cascadia Mono", 8))
                painter.drawText(
                    QRect(rect.left() + 20, cy + 72, rect.width() - 40, 24),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    sub_text,
                )

            elif is_review_idle:
                import math

                is_reading_sd = ("reading microsd" in st or "syncing" in st)
                is_empty_sd = ("no recordings" in st)

                if is_reading_sd:
                    pulse = 0.5 + 0.5 * math.sin(self._anim_step * 0.16)
                    painter.setBrush(QColor(15, 23, 42, 180))
                    painter.setPen(QPen(QColor(59, 130, 246, int(120 + 90 * pulse)), 1.8))
                    painter.drawEllipse(QPoint(cx, cy), 24, 24)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(96, 165, 250, 220))
                    painter.drawEllipse(QPoint(cx, cy), 6, 6)
                    dots = "." * ((self._anim_step // 10) % 4 + 1)
                    badge_text = f"READING SD CARD {dots}"
                    pill_border = QColor(59, 130, 246, 140)
                    pill_bg = QColor(15, 23, 42, 225)
                    pill_fg = QColor("#93c5fd")
                elif is_empty_sd:
                    painter.setBrush(QColor(24, 24, 30, 190))
                    painter.setPen(QPen(QColor(82, 82, 91, 140), 1.5))
                    painter.drawEllipse(QPoint(cx, cy), 24, 24)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor(113, 113, 122, 200))
                    painter.drawEllipse(QPoint(cx, cy), 6, 6)
                    badge_text = "NO RECORDINGS"
                    pill_border = QColor(82, 82, 91, 140)
                    pill_bg = QColor(24, 24, 27, 225)
                    pill_fg = QColor("#a1a1aa")
                else:
                    # Ready / Select a clip to play
                    painter.setBrush(QColor(23, 37, 84, 150))
                    painter.setPen(QPen(QColor(59, 130, 246, 150), 1.6))
                    painter.drawEllipse(QPoint(cx, cy), 24, 24)
                    # Crisp play triangle icon inside circle
                    from PyQt6.QtGui import QPolygonF
                    from PyQt6.QtCore import QPointF

                    tri = QPolygonF(
                        [
                            QPointF(cx - 5, cy - 8),
                            QPointF(cx - 5, cy + 8),
                            QPointF(cx + 9, cy),
                        ]
                    )
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QColor("#93c5fd"))
                    painter.drawPolygon(tri)
                    badge_text = "SELECT A CLIP"
                    pill_border = QColor(59, 130, 246, 135)
                    pill_bg = QColor(15, 23, 42, 220)
                    pill_fg = QColor("#93c5fd")

                pill_w, pill_h = 168, 25
                pill_rect = QRect(cx - pill_w // 2, cy + 36, pill_w, pill_h)
                painter.setPen(QPen(pill_border, 1.0))
                painter.setBrush(pill_bg)
                painter.drawRoundedRect(pill_rect, 12, 12)

                painter.setPen(pill_fg)
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, badge_text)

                sub_text = (self._status_text or "Select a clip on the left or click the timeline to play").strip()
                painter.setPen(QColor("#94a3b8"))
                painter.setFont(QFont("Cascadia Mono", 8))
                painter.drawText(
                    QRect(rect.left() + 20, cy + 68, rect.width() - 40, 24),
                    Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    sub_text,
                )

            elif is_connecting:
                import math
                # Concentric smooth radar ripple waves
                painter.setBrush(Qt.BrushStyle.NoBrush)
                for i in range(3):
                    wave_phase = ((self._anim_step + i * 28) % 84) / 84.0
                    r = int(14 + wave_phase * 34)
                    alpha = int(190 * (1.0 - wave_phase) * (1.0 - wave_phase))
                    painter.setPen(QPen(QColor(56, 189, 248, max(0, min(255, alpha))), 1.5))
                    painter.drawEllipse(QPoint(cx, cy), r, r)

                # Rotating sweep beacon needle
                angle = (self._anim_step * 6) % 360
                rad = math.radians(angle)
                sweep_len = 28
                sx = cx + int(sweep_len * math.cos(rad))
                sy = cy + int(sweep_len * math.sin(rad))
                painter.setPen(QPen(QColor(14, 165, 233, 160), 1.8))
                painter.drawLine(cx, cy, sx, sy)

                # Center pulsing core
                pulse = 0.5 + 0.5 * math.sin(self._anim_step * 0.15)
                core_r = int(7 + 2 * pulse)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(14, 165, 233, 230))
                painter.drawEllipse(QPoint(cx, cy), core_r, core_r)
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(QPoint(cx, cy), 3, 3)

                # Connecting pill badge with animated dots
                dots = "." * ((self._anim_step // 10) % 4 + 1)
                badge_text = f"CONNECTING {dots}"
                pill_w, pill_h = 168, 26
                pill_rect = QRect(cx - pill_w // 2, cy + 36, pill_w, pill_h)
                painter.setPen(QPen(QColor(56, 189, 248, 140), 1.0))
                painter.setBrush(QColor(15, 23, 42, 230))
                painter.drawRoundedRect(pill_rect, 13, 13)

                # Pulsing cyan indicator dot inside badge
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(56, 189, 248, int(150 + 105 * pulse)))
                painter.drawEllipse(QPoint(cx - pill_w // 2 + 14, cy + 36 + pill_h // 2), 4, 4)

                painter.setPen(QColor("#38bdf8"))
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(QRect(cx - pill_w // 2 + 20, cy + 36, pill_w - 24, pill_h), Qt.AlignmentFlag.AlignCenter, badge_text)

                # Subtitle (detail text - clean and elegant, zero IP addresses shown)
                raw_st = (self._status_text or "").strip()
                import re
                cleaned = re.sub(r'Connecting to\s+\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\.{0,3}', 'Establishing live video stream...', raw_st, flags=re.I)
                cleaned = re.sub(r'Connecting to[^\.]+\.{0,3}', 'Establishing live video stream...', cleaned, flags=re.I)
                cleaned = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b', '', cleaned)
                cleaned = re.sub(r'\s+\b(for|to|at)\b\s*$', '', cleaned, flags=re.I)
                cleaned = cleaned.replace("()", "").strip(" (•-.,)")
                if not cleaned or cleaned.lower() in ("standby", "stopped", "connecting", ""):
                    sub_text = f"{self._cam_name or 'Camera'}  •  Live Feed"
                else:
                    sub_text = cleaned
                painter.setPen(QColor("#94a3b8"))
                painter.setFont(QFont("Cascadia Mono", 8))
                painter.drawText(QRect(rect.left() + 20, cy + 68, rect.width() - 40, 20), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, sub_text)

            elif is_disconnected_tile:
                # Disconnected camera state: blurred last frame background + wifi_off badge + Reconnect button overlay
                self._sync_reconnect_button()
                icon_cy = rect.center().y() - 34
                painter.setPen(QPen(QColor(82, 82, 91, 165), 1.2))
                painter.setBrush(QColor(18, 18, 24, 215))
                painter.drawEllipse(QPoint(cx, icon_cy), 20, 20)
                wifi_off_px = lucide_pixmap("wifi_off", color="#a1a1aa", size=18, stroke_width=2.0)
                painter.drawPixmap(cx - 9, icon_cy - 9, wifi_off_px)

                import re
                raw_err = (self._status_text or "").strip()
                cleaned_err = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b', '', raw_err)
                cleaned_err = re.sub(r'\s*—\s*retrying.*$', '', cleaned_err, flags=re.I)
                cleaned_err = re.sub(r'\s+\b(for|to|at)\b\s*$', '', cleaned_err, flags=re.I).strip(" (•-.,)")
                if not cleaned_err or cleaned_err.lower() in ("stopped", "standby", "offline", "idle", "disconnected"):
                    sub_lbl = "Camera Disconnected"
                elif any(k in cleaned_err.lower() for k in ("invalid", "password", "username", "error", "rejected", "refused")):
                    sub_lbl = cleaned_err
                else:
                    sub_lbl = "Camera Disconnected"
                is_auth_err = sub_lbl != "Camera Disconnected"
                painter.setPen(QColor("#fca5a5" if is_auth_err else "#d4d4d8"))
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
                painter.drawText(
                    QRect(rect.left() + 20, rect.center().y() + 48, rect.width() - 40, 20),
                    Qt.AlignmentFlag.AlignCenter,
                    sub_lbl,
                )

            elif is_error:
                # Error state: amber / red alert beacon
                import math
                pulse = 0.5 + 0.5 * math.sin(self._anim_step * 0.2)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(239, 68, 68, int(90 + 90 * pulse)), 1.5))
                painter.drawEllipse(QPoint(cx, cy), 24, 24)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(239, 68, 68, 220))
                painter.drawEllipse(QPoint(cx, cy), 9, 9)
                painter.setBrush(QColor("#ffffff"))
                painter.drawEllipse(QPoint(cx, cy), 3, 3)

                pill_w, pill_h = 160, 26
                pill_rect = QRect(cx - pill_w // 2, cy + 36, pill_w, pill_h)
                painter.setPen(QPen(QColor(239, 68, 68, 140), 1.0))
                painter.setBrush(QColor(30, 10, 10, 230))
                painter.drawRoundedRect(pill_rect, 13, 13)

                painter.setPen(QColor("#fca5a5"))
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, "RECONNECTING...")

                import re
                err_text = re.sub(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}(:\d+)?\b', '', self._status_text or "Connection Error")
                err_text = re.sub(r'\s+\b(for|to|at)\b\s*$', '', err_text, flags=re.I).strip(" (•-.,)")
                painter.setPen(QColor("#f87171"))
                painter.setFont(QFont("Cascadia Mono", 8))
                painter.drawText(QRect(rect.left() + 20, cy + 68, rect.width() - 40, 36), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap, err_text)

            else:
                # Stopped / Standby state: calm muted slate aesthetic
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.setPen(QPen(QColor(75, 85, 99, 100), 1.0))
                painter.drawEllipse(QPoint(cx, cy), 22, 22)

                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor(55, 65, 81, 200))
                painter.drawEllipse(QPoint(cx, cy), 8, 8)

                pill_w, pill_h = 120, 24
                pill_rect = QRect(cx - pill_w // 2, cy + 34, pill_w, pill_h)
                painter.setPen(QPen(QColor(75, 85, 99, 120), 1.0))
                painter.setBrush(QColor(17, 24, 39, 210))
                painter.drawRoundedRect(pill_rect, 12, 12)

                painter.setPen(QColor("#9ca3af"))
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.Bold))
                painter.drawText(pill_rect, Qt.AlignmentFlag.AlignCenter, "STANDBY")

                painter.setPen(QColor("#6b7280"))
                painter.setFont(QFont("Segoe UI", 9))
                painter.drawText(QRect(rect.left() + 20, cy + 66, rect.width() - 40, 20), Qt.AlignmentFlag.AlignCenter, "Stream offline")

        # Chime Trigger Zone overlay (interactive editor or subtle active-zone indicator)
        self._paint_chime_zone_overlay(painter, self._get_video_target_rect())

        # Camera shutter flash effect
        if self._shutter_alpha > 0.01:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(self._shutter_alpha * 220)))
            painter.drawRect(rect)

        painter.end()

    def _paint_chime_zone_overlay(self, painter: QPainter, target: QRect) -> None:
        self._chime_done_rect = QRect()
        self._chime_reset_rect = QRect()
        rect = self.rect()
        if target.width() < 24 or target.height() < 24:
            return

        if self._editing_chime_zone:
            # Determine active box being dragged or currently configured
            active_box: Optional[List[float]] = None
            if self._chime_drag_start is not None and self._chime_drag_curr is not None:
                x1 = min(self._chime_drag_start[0], self._chime_drag_curr[0])
                y1 = min(self._chime_drag_start[1], self._chime_drag_curr[1])
                x2 = max(self._chime_drag_start[0], self._chime_drag_curr[0])
                y2 = max(self._chime_drag_start[1], self._chime_drag_curr[1])
                if (x2 - x1) >= 0.015 and (y2 - y1) >= 0.015:
                    active_box = [x1, y1, x2, y2]
            if active_box is None and self._chime_zone is not None:
                active_box = list(self._chime_zone)

            if active_box is not None and len(active_box) >= 4:
                zx = int(round(target.left() + active_box[0] * target.width()))
                zy = int(round(target.top() + active_box[1] * target.height()))
                zw = max(10, int(round((active_box[2] - active_box[0]) * target.width())))
                zh = max(10, int(round((active_box[3] - active_box[1]) * target.height())))
                z_rect = QRect(zx, zy, zw, zh).intersected(target)

                # Dim areas outside the Chime Trigger Area
                dim_col = QColor(5, 5, 10, 148)
                if z_rect.top() > target.top():
                    painter.fillRect(
                        QRect(target.left(), target.top(), target.width(), z_rect.top() - target.top()),
                        dim_col,
                    )
                if z_rect.bottom() < target.bottom():
                    painter.fillRect(
                        QRect(
                            target.left(),
                            z_rect.bottom() + 1,
                            target.width(),
                            target.bottom() - z_rect.bottom(),
                        ),
                        dim_col,
                    )
                if z_rect.left() > target.left():
                    painter.fillRect(
                        QRect(
                            target.left(),
                            z_rect.top(),
                            z_rect.left() - target.left(),
                            z_rect.height(),
                        ),
                        dim_col,
                    )
                if z_rect.right() < target.right():
                    painter.fillRect(
                        QRect(
                            z_rect.right() + 1,
                            z_rect.top(),
                            target.right() - z_rect.right(),
                            z_rect.height(),
                        ),
                        dim_col,
                    )

                # Subtle warm amber highlight inside trigger zone
                painter.fillRect(z_rect, QColor(245, 158, 11, 28))
                dash_pen = QPen(QColor("#f59e0b"), 2.0, Qt.PenStyle.DashLine)
                painter.setPen(dash_pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                painter.drawRect(z_rect)

                # Bold corner L-handles on the zone rectangle
                arm = max(8, min(18, min(z_rect.width(), z_rect.height()) // 4))
                c_pen = QPen(QColor("#fbbf24"), 3.2)
                c_pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(c_pen)
                zl, zt, zr, zb = z_rect.left(), z_rect.top(), z_rect.right(), z_rect.bottom()
                painter.drawLine(zl, zt, zl + arm, zt)
                painter.drawLine(zl, zt, zl, zt + arm)
                painter.drawLine(zr - arm, zt, zr, zt)
                painter.drawLine(zr, zt, zr, zt + arm)
                painter.drawLine(zl, zb - arm, zl, zb)
                painter.drawLine(zl, zb, zl + arm, zb)
                painter.drawLine(zr - arm, zb, zr, zb)
                painter.drawLine(zr, zb - arm, zr, zb)

                # Zone tag badge
                tag_w, tag_h = min(146, max(96, z_rect.width())), 20
                tag_x = max(target.left() + 4, min(target.right() - tag_w - 4, zl))
                tag_y = zt - tag_h - 4 if (zt - tag_h - 4) >= (target.top() + 38) else (zt + 5)
                tag_rect = QRect(tag_x, tag_y, tag_w, tag_h)
                painter.setPen(QPen(QColor("#f59e0b"), 1.0))
                painter.setBrush(QColor(24, 18, 10, 230))
                painter.drawRoundedRect(tag_rect, 4, 4)
                painter.setPen(QColor("#fde68a"))
                painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
                painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, "CHIME TRIGGER AREA")
            else:
                # Whole Frame currently active in edit mode
                vis_t = target.adjusted(2, 2, -2, -2)
                painter.setPen(QPen(QColor(245, 158, 11, 185), 2.0, Qt.PenStyle.DashLine))
                painter.setBrush(QColor(245, 158, 11, 14))
                painter.drawRect(vis_t)

                hint_w = min(rect.width() - 24, 340)
                hint_h = 28
                hint_rect = QRect(rect.center().x() - hint_w // 2, rect.center().y() - hint_h // 2, hint_w, hint_h)
                painter.setPen(QPen(QColor(245, 158, 11, 160), 1.0))
                painter.setBrush(QColor(14, 14, 18, 225))
                painter.drawRoundedRect(hint_rect, 6, 6)
                painter.setPen(QColor("#fde68a"))
                painter.setFont(QFont("Segoe UI", 9, QFont.Weight.DemiBold))
                painter.drawText(
                    hint_rect,
                    Qt.AlignmentFlag.AlignCenter,
                    "Whole Frame Active — Drag on footage to set Gate / Chime Area",
                )

            # Top Interactive HUD Control Bar for Chime Area Editor
            bar_w = min(rect.width() - 12, 410)
            bar_h = 30
            bar_x = max(6, rect.center().x() - bar_w // 2)
            bar_y = 8
            bar_rect = QRect(bar_x, bar_y, bar_w, bar_h)
            painter.setPen(QPen(QColor("#f59e0b"), 1.2))
            painter.setBrush(QColor(14, 14, 18, 238))
            painter.drawRoundedRect(bar_rect, 6, 6)

            done_w, done_h = 54, 22
            reset_w, reset_h = 80, 22
            done_x = bar_rect.right() - done_w - 6
            done_y = bar_y + (bar_h - done_h) // 2
            reset_x = done_x - reset_w - 6
            reset_y = done_y

            self._chime_done_rect = QRect(done_x, done_y, done_w, done_h)
            self._chime_reset_rect = QRect(reset_x, reset_y, reset_w, reset_h)

            lbl_rect = QRect(bar_x + 10, bar_y, max(60, reset_x - bar_x - 14), bar_h)
            painter.setPen(QColor("#fef3c7"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            status_lbl = (
                "DRAG TO SET CHIME AREA (CUSTOM)"
                if self._chime_zone is not None
                else "DRAG TO SET CHIME AREA (FULL FRAME)"
            )
            painter.drawText(
                lbl_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                status_lbl,
            )

            # 'Full Frame' reset pill
            hov_reset = self._hover_pos is not None and self._chime_reset_rect.contains(self._hover_pos)
            is_custom = self._chime_zone is not None
            painter.setPen(
                QPen(
                    QColor("#fbbf24" if hov_reset else ("#52525b" if is_custom else "#27272a")),
                    1.0,
                )
            )
            painter.setBrush(
                QColor(63, 63, 70, 240) if hov_reset else QColor(24, 24, 30, 220)
            )
            painter.drawRoundedRect(self._chime_reset_rect, 4, 4)
            painter.setPen(QColor("#ffffff" if hov_reset else ("#e4e4e7" if is_custom else "#71717a")))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.DemiBold))
            painter.drawText(self._chime_reset_rect, Qt.AlignmentFlag.AlignCenter, "Full Frame")

            # 'Done' primary pill
            hov_done = self._hover_pos is not None and self._chime_done_rect.contains(self._hover_pos)
            painter.setPen(QPen(QColor("#fde68a" if hov_done else "#f59e0b"), 1.0))
            painter.setBrush(QColor(217, 119, 6, 245) if hov_done else QColor(180, 83, 9, 235))
            painter.drawRoundedRect(self._chime_done_rect, 4, 4)
            painter.setPen(QColor("#ffffff"))
            painter.setFont(QFont("Segoe UI", 8, QFont.Weight.Bold))
            painter.drawText(self._chime_done_rect, Qt.AlignmentFlag.AlignCenter, "Done")
            return

        # Passive indicator when a custom Chime Zone is configured and user hovers or person is detected
        if self._chime_zone is not None and len(self._chime_zone) >= 4:
            if not self._is_connected and (self._qimage is None or self._qimage.isNull()):
                return
            triggered = any(
                len(det) >= 4
                and CameraConfig.box_in_chime_zone(
                    (float(det[0]), float(det[1]), float(det[2]), float(det[3])),
                    self._chime_zone,
                )
                for det in self._detections
            )
            if self._hover_pos is None and not self._detections:
                return

            zx = int(round(target.left() + self._chime_zone[0] * target.width()))
            zy = int(round(target.top() + self._chime_zone[1] * target.height()))
            zw = max(10, int(round((self._chime_zone[2] - self._chime_zone[0]) * target.width())))
            zh = max(10, int(round((self._chime_zone[3] - self._chime_zone[1]) * target.height())))
            z_rect = QRect(zx, zy, zw, zh).intersected(target)
            if z_rect.width() < 8 or z_rect.height() < 8:
                return

            border_col = QColor(251, 191, 36, 225) if triggered else QColor(245, 158, 11, 145)
            if triggered:
                painter.fillRect(z_rect, QColor(245, 158, 11, 22))
            painter.setPen(QPen(border_col, 1.8 if triggered else 1.4, Qt.PenStyle.DashLine))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(z_rect)

            tag_text = "CHIME AREA • TRIGGERED" if triggered else "CHIME AREA"
            tag_w = 132 if triggered else 76
            tag_h = 17
            tag_x = max(target.left() + 4, min(target.right() - tag_w - 4, z_rect.left()))
            tag_y = (
                z_rect.top() - tag_h - 3
                if (z_rect.top() - tag_h - 3) >= (target.top() + 4)
                else (z_rect.top() + 3)
            )
            tag_rect = QRect(tag_x, tag_y, tag_w, tag_h)
            painter.setPen(QPen(border_col, 1.0))
            painter.setBrush(QColor(20, 16, 10, 210))
            painter.drawRoundedRect(tag_rect, 3, 3)
            painter.setPen(QColor("#fef3c7" if triggered else "#fcd34d"))
            painter.setFont(QFont("Segoe UI", 7, QFont.Weight.Bold))
            painter.drawText(tag_rect, Qt.AlignmentFlag.AlignCenter, tag_text)


class CameraTileWidget(QFrame):
    """UniFi Protect / Frigate NVR style camera viewport card."""

    selected = pyqtSignal(str)
    maximize_requested = pyqtSignal(str)
    monitor_mode_requested = pyqtSignal()
    reorder_requested = pyqtSignal(str, str)
    rename_requested = pyqtSignal(str)
    edit_requested = pyqtSignal(str)
    delete_requested = pyqtSignal(str)
    controls_requested = pyqtSignal(str)
    review_sd_requested = pyqtSignal(str)
    config_changed = pyqtSignal()
    snapshot_saved = pyqtSignal(str)
    recording_toggled = pyqtSignal(str, bool, str)

    def __init__(self, config: CameraConfig, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.config = config
        self.worker: Optional[CameraStreamWorker] = None
        self._retiring_workers: List[QThread] = []
        self.audio_out = LiveAudioOutput(sample_rate=8000, parent=self)
        self._is_selected = False
        self._is_hovered = False
        self._is_maximized = False
        self._monitor_mode = False
        self._person_detected = False
        self._last_chime_ts: float = 0.0
        self._last_saved_snapshot: Optional[str] = None
        self._last_thumb_save_ts: float = 0.0
        self._rec_start_mono: float = 0.0
        self._rec_blink_state: bool = True
        self._rec_ui_timer = QTimer(self)
        self._rec_ui_timer.setInterval(500)
        self._rec_ui_timer.timeout.connect(self._update_rec_timer_label)
        self._tile_held_ptz_dir: Optional[str] = None
        self._tile_ptz_timer = QTimer(self)
        self._tile_ptz_timer.setInterval(180)
        self._tile_ptz_timer.timeout.connect(self._on_tile_ptz_tick)
        self._is_being_dragged = False
        self._is_drop_target = False
        self._is_landing_pulse = False
        self._header_drag_start: Optional[QPoint] = None
        self._audio_meter_level: float = 0.0
        self._audio_decay_timer = QTimer(self)
        self._audio_decay_timer.setInterval(55)
        self._audio_decay_timer.timeout.connect(self._decay_audio_meter)
        self.setAcceptDrops(True)

        self.setFrameShape(QFrame.Shape.NoFrame)
        self._update_border()

        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(1, 1, 1, 1)
        self._root_layout.setSpacing(0)

        # --- Top Telemetry Bar (32px) ---
        self.header_bar = QWidget()
        self.header_bar.setFixedHeight(32)
        self.header_bar.setStyleSheet("background-color: #0e0e12; border-bottom: 1px solid #1a1a20;")
        self.header_bar.setCursor(Qt.CursorShape.OpenHandCursor)
        h_layout = QHBoxLayout(self.header_bar)
        h_layout.setContentsMargins(8, 0, 8, 0)
        h_layout.setSpacing(6)

        self.grip_icon = QLabel()
        self.grip_icon.setPixmap(lucide_pixmap("grip_vertical", "#71717a", 14))
        self.grip_icon.setFixedSize(14, 20)
        self.grip_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.grip_icon.setToolTip("Drag to reposition camera in grid (Trello card style)")
        self.grip_icon.setCursor(Qt.CursorShape.OpenHandCursor)
        h_layout.addWidget(self.grip_icon)

        self.status_dot = StatusDot(self.header_bar)
        h_layout.addWidget(self.status_dot)

        self.title_lbl = QLabel(self.config.name)
        self.title_lbl.setMinimumWidth(24)
        self.title_lbl.setStyleSheet("font-weight: 600; font-size: 12px; color: #f4f4f5;")
        self.title_lbl.setToolTip("Double-click to rename camera")
        self.title_lbl.mouseDoubleClickEvent = lambda e: self.rename_requested.emit(self.config.cam_id)
        h_layout.addWidget(self.title_lbl)

        self.btn_rename = QPushButton()
        self.btn_rename.setObjectName("ghostBtn")
        self.btn_rename.setIcon(lucide_icon("pencil", "#71717a", 12))
        self.btn_rename.setIconSize(QSize(12, 12))
        self.btn_rename.setFixedSize(22, 22)
        self.btn_rename.setToolTip("Rename Camera (F2)")
        self.btn_rename.clicked.connect(lambda: self.rename_requested.emit(self.config.cam_id))
        h_layout.addWidget(self.btn_rename)

        self.badge_lbl = QLabel(self._protocol_badge())
        self.badge_lbl.setStyleSheet(
            "background-color: #16161c; color: #71717a; border: 1px solid #22222a; "
            "border-radius: 3px; padding: 1px 6px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px;"
        )
        h_layout.addWidget(self.badge_lbl)

        h_layout.addStretch()

        self.person_lbl = QLabel()
        self.person_lbl.setPixmap(lucide_pixmap("shield_alert", "#ef4444", 16, stroke_width=2.0))
        self.person_lbl.setFixedSize(20, 20)
        self.person_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.person_lbl.setToolTip("Person Detected")
        self.person_lbl.setVisible(False)
        h_layout.addWidget(self.person_lbl)

        self.rec_lbl = QLabel("REC")
        self.rec_lbl.setStyleSheet(
            "background-color: #450a0a; color: #f87171; border: 1px solid #7f1d1d; "
            "border-radius: 3px; padding: 1px 5px; font-family: 'Cascadia Mono', 'Consolas', monospace; "
            "font-weight: 700; font-size: 10px;"
        )
        self.rec_lbl.setVisible(False)
        h_layout.addWidget(self.rec_lbl)

        self.stats_lbl = QLabel("0.0 FPS  0 kbps")
        self.stats_lbl.setMinimumWidth(0)
        self.stats_lbl.setStyleSheet(
            "color: #71717a; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px;"
        )
        h_layout.addWidget(self.stats_lbl)

        self.btn_settings = QPushButton()
        self.btn_settings.setObjectName("ghostBtn")
        self.btn_settings.setIcon(lucide_icon("settings", "#71717a", 13))
        self.btn_settings.setIconSize(QSize(13, 13))
        self.btn_settings.setFixedSize(24, 24)
        self.btn_settings.setToolTip("Camera Connection Settings")
        self.btn_settings.clicked.connect(lambda: self.edit_requested.emit(self.config.cam_id))
        h_layout.addWidget(self.btn_settings)

        self.header_bar.installEventFilter(self)
        self.grip_icon.installEventFilter(self)
        self.title_lbl.installEventFilter(self)

        self._root_layout.addWidget(self.header_bar)

        # --- Video Canvas ---
        self.canvas = VideoCanvas(self)
        self.canvas.set_reconnect_overlay_enabled(True)
        self.canvas.set_ptz_overlay_enabled(True)
        self.canvas.set_chime_zone(getattr(self.config, "chime_zone", None))
        self.canvas.clicked.connect(lambda: self.selected.emit(self.config.cam_id))
        self.canvas.double_clicked.connect(lambda: self.maximize_requested.emit(self.config.cam_id))
        self.canvas.drag_requested.connect(self._start_trello_drag)
        self.canvas.reconnect_requested.connect(self.reconnect_stream)
        self.canvas.dismiss_detection_requested.connect(self.ignore_detection_box)
        self.canvas.chime_zone_changed.connect(self._on_canvas_chime_zone_changed)
        self.canvas.chime_zone_edit_toggled.connect(self._on_canvas_chime_zone_edit_toggled)
        self.canvas.ptz_pressed.connect(self._start_tile_ptz_hold)
        self.canvas.ptz_released.connect(self._stop_tile_ptz_hold)
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)
        self.canvas.set_camera_info(self.config.name, "Live Feed")
        self._load_cached_last_frame()
        self._root_layout.addWidget(self.canvas, 1)

        # --- Bottom Control Bar (34px) ---
        self.ctrl_bar = QWidget()
        self.ctrl_bar.setFixedHeight(34)
        self.ctrl_bar.setStyleSheet("background-color: #0e0e12; border-top: 1px solid #1a1a20;")
        c_layout = QHBoxLayout(self.ctrl_bar)
        c_layout.setContentsMargins(8, 0, 8, 0)
        c_layout.setSpacing(4)

        self.btn_power = QPushButton()
        self.btn_power.setObjectName("ghostBtn")
        self.btn_power.setIcon(lucide_icon("wifi_off", "#71717a", 14))
        self.btn_power.setIconSize(QSize(14, 14))
        self.btn_power.setFixedSize(26, 24)
        self.btn_power.setToolTip("Disconnected — Click to Connect Stream")
        self.btn_power.clicked.connect(self.toggle_stream)
        c_layout.addWidget(self.btn_power)

        self.btn_mute = QPushButton()
        self.btn_mute.setObjectName("ghostBtn")
        self.btn_mute.setCheckable(True)
        self.btn_mute.setIcon(lucide_icon("volume_x", "#71717a", 14))
        self.btn_mute.setIconSize(QSize(14, 14))
        self.btn_mute.setFixedSize(26, 24)
        self.btn_mute.setToolTip("Toggle Live Audio")
        self.btn_mute.clicked.connect(self._on_mute_toggled)
        c_layout.addWidget(self.btn_mute)

        self.audio_bar = QProgressBar()
        self.audio_bar.setRange(0, 100)
        self.audio_bar.setValue(0)
        self.audio_bar.setTextVisible(False)
        self.audio_bar.setFixedSize(40, 4)
        self.audio_bar.setToolTip("Live Audio Level")
        c_layout.addWidget(self.audio_bar)

        self.btn_chime = QPushButton()
        self.btn_chime.setObjectName("ghostBtn")
        self.btn_chime.setCheckable(True)
        self.btn_chime.setIconSize(QSize(14, 14))
        self.btn_chime.setFixedSize(26, 24)
        self.btn_chime.clicked.connect(self._toggle_detection_sound)
        c_layout.addWidget(self.btn_chime)

        self.btn_chime_zone = QPushButton()
        self.btn_chime_zone.setObjectName("ghostBtn")
        self.btn_chime_zone.setCheckable(True)
        self.btn_chime_zone.setIconSize(QSize(14, 14))
        self.btn_chime_zone.setFixedSize(26, 24)
        self.btn_chime_zone.clicked.connect(self.toggle_chime_zone_editor)
        self._update_chime_btn()
        c_layout.addWidget(self.btn_chime_zone)

        c_layout.addStretch()

        self.btn_quality = QPushButton("HD" if self.config.quality == 1 else "SD")
        self.btn_quality.setObjectName("segBtn")
        self.btn_quality.setFixedSize(34, 22)
        self.btn_quality.setToolTip("Switch HD / SD Stream Profile")
        self.btn_quality.clicked.connect(self._toggle_quality)
        self._update_quality_btn()
        c_layout.addWidget(self.btn_quality)

        self.btn_snap = QPushButton()
        self.btn_snap.setObjectName("ghostBtn")
        self.btn_snap.setIcon(lucide_icon("camera", "#a1a1aa", 14))
        self.btn_snap.setIconSize(QSize(14, 14))
        self.btn_snap.setFixedSize(26, 24)
        self.btn_snap.setToolTip("Take Photo / Save Snapshot (JPG)")
        self.btn_snap.clicked.connect(self._on_snap_clicked)
        c_layout.addWidget(self.btn_snap)

        self.btn_rec = QPushButton()
        self.btn_rec.setObjectName("ghostBtn")
        self.btn_rec.setCheckable(True)
        self.btn_rec.setIcon(lucide_icon("disc", "#a1a1aa", 14))
        self.btn_rec.setIconSize(QSize(14, 14))
        self.btn_rec.setFixedSize(26, 24)
        self.btn_rec.setToolTip("Record Stream (MP4)")
        self.btn_rec.clicked.connect(self.toggle_recording)
        c_layout.addWidget(self.btn_rec)

        self.btn_sd_review = QPushButton()
        self.btn_sd_review.setObjectName("ghostBtn")
        self.btn_sd_review.setIcon(lucide_icon("hard_drive", "#38bdf8", 14))
        self.btn_sd_review.setIconSize(QSize(14, 14))
        self.btn_sd_review.setFixedSize(26, 24)
        self.btn_sd_review.setToolTip("Review SD Card Footage & Anomalies")
        self.btn_sd_review.clicked.connect(lambda: self.review_sd_requested.emit(self.config.cam_id))
        c_layout.addWidget(self.btn_sd_review)

        self.btn_max = QPushButton()
        self.btn_max.setObjectName("ghostBtn")
        self.btn_max.setIcon(lucide_icon("maximize_2", "#a1a1aa", 14))
        self.btn_max.setIconSize(QSize(14, 14))
        self.btn_max.setFixedSize(26, 24)
        self.btn_max.setToolTip("Maximize / Restore Camera (Double-Click Video)")
        self.btn_max.clicked.connect(lambda: self.maximize_requested.emit(self.config.cam_id))
        c_layout.addWidget(self.btn_max)

        self._root_layout.addWidget(self.ctrl_bar)

    def enterEvent(self, event) -> None:
        self._is_hovered = True
        self._update_border()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._is_hovered = False
        self._update_border()
        super().leaveEvent(event)

    def _start_tile_ptz_hold(self, direction: str) -> None:
        self.selected.emit(self.config.cam_id)
        self._tile_held_ptz_dir = direction
        if self.worker is not None:
            self.worker.ptz_move(direction)
        if not self._tile_ptz_timer.isActive():
            self._tile_ptz_timer.start()

    def _on_tile_ptz_tick(self) -> None:
        if self._tile_held_ptz_dir and self.worker is not None:
            self.worker.ptz_move(self._tile_held_ptz_dir)
        else:
            self._tile_ptz_timer.stop()

    def _stop_tile_ptz_hold(self) -> None:
        was_holding = self._tile_held_ptz_dir is not None
        self._tile_held_ptz_dir = None
        if self._tile_ptz_timer.isActive():
            self._tile_ptz_timer.stop()
        if was_holding and self.worker is not None:
            self.worker.ptz_move("stop")

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        w = self.width()
        if hasattr(self, "badge_lbl"):
            self.badge_lbl.setVisible(w >= 250)
        if hasattr(self, "audio_bar"):
            self.audio_bar.setVisible(w >= 310)
        if hasattr(self, "ctrl_bar") and self.ctrl_bar.layout() is not None:
            cl = self.ctrl_bar.layout()
            if w < 290:
                cl.setContentsMargins(4, 0, 4, 0)
                cl.setSpacing(2)
            else:
                cl.setContentsMargins(8, 0, 8, 0)
                cl.setSpacing(4)

    def _show_context_menu(self, pos: QPoint) -> None:
        self.selected.emit(self.config.cam_id)
        menu = QMenu(self)

        hit_det = self.canvas.detection_at_pos(pos)
        active_dets = list(self.canvas._detections or self.canvas._pinned_detections)
        act_ignore_det = None
        if hit_det is not None or active_dets:
            act_ignore_det = menu.addAction('Mark Detected Object as "Not a Person" (Ignore False Trigger)')
            menu.addSeparator()

        ignored_count = len(getattr(self.config, "ignored_objects", None) or [])
        act_clear_ignored = None
        if ignored_count > 0:
            act_clear_ignored = menu.addAction(
                f'Reset Ignored "Not a Person" Objects ({ignored_count} saved)'
            )
            menu.addSeparator()

        act_sd = menu.addAction("Review SD Card Footage & Anomalies...")
        menu.addSeparator()
        act_mon = menu.addAction(
            "Exit Monitor Mode (Esc / F11)" if self._monitor_mode else "Monitor Mode — Pure Footage Only (F11)"
        )
        act_max = menu.addAction("Solo / Restore This Camera (Double-Click)")
        act_ptz = menu.addAction("PTZ & Camera Controls")
        chime_on = bool(getattr(self.config, "detection_sound", True))
        cd_sec = int(getattr(self.config, "detection_sound_cooldown", 10) or 10)
        act_chime = menu.addAction(
            f"Mute Person Detection Chime ({cd_sec}s cooldown)"
            if chime_on
            else f"Enable Person Detection Chime ({cd_sec}s cooldown)"
        )
        is_editing_zone = bool(getattr(self.canvas, "_editing_chime_zone", False))
        has_custom_zone = CameraConfig.normalize_chime_zone(getattr(self.config, "chime_zone", None)) is not None
        act_chime_zone = menu.addAction(
            "Finish Editing Chime Trigger Area"
            if is_editing_zone
            else "Set Chime Trigger Area on Footage (Drag Box)..."
        )
        act_reset_chime_zone = (
            menu.addAction("Reset Chime Trigger Area to Whole Frame") if has_custom_zone else None
        )
        menu.addSeparator()
        act_snap = menu.addAction("Take Photo / Snapshot (Space / S)")
        act_folder = menu.addAction("Open Snapshots Folder")
        menu.addSeparator()
        act_rename = menu.addAction("Rename Camera (F2)")
        act_edit = menu.addAction("Connection Settings...")
        menu.addSeparator()
        act_del = menu.addAction("Remove Camera")
        chosen = menu.exec(self.canvas.mapToGlobal(pos))
        if act_ignore_det is not None and chosen == act_ignore_det:
            self.ignore_detection_box(hit_det)
        elif act_clear_ignored is not None and chosen == act_clear_ignored:
            self.clear_ignored_objects()
        elif chosen == act_sd:
            self.review_sd_requested.emit(self.config.cam_id)
        elif chosen == act_mon:
            self.monitor_mode_requested.emit()
        elif chosen == act_max:
            self.maximize_requested.emit(self.config.cam_id)
        elif chosen == act_ptz:
            self.controls_requested.emit(self.config.cam_id)
        elif chosen == act_chime:
            self._toggle_detection_sound()
        elif chosen == act_chime_zone:
            self.toggle_chime_zone_editor()
        elif act_reset_chime_zone is not None and chosen == act_reset_chime_zone:
            self.reset_chime_zone()
        elif chosen == act_snap:
            self.take_snapshot()
        elif chosen == act_folder:
            CameraTileWidget._open_folder(SNAPSHOT_DIR)
        elif chosen == act_rename:
            self.rename_requested.emit(self.config.cam_id)
        elif chosen == act_edit:
            self.edit_requested.emit(self.config.cam_id)
        elif chosen == act_del:
            self.delete_requested.emit(self.config.cam_id)

    def _protocol_badge(self) -> str:
        p = (self.config.protocol or "lan8800").lower()
        if p == "demo":
            return "SIM"
        if p == "rtsp":
            return "RTSP"
        return "LAN"

    def set_selected(self, selected: bool) -> None:
        self._is_selected = selected
        self._update_border()

    def set_maximized_state(self, maximized: bool) -> None:
        self._is_maximized = maximized
        self.btn_max.setIcon(lucide_icon("minimize_2" if maximized else "maximize_2", "#a1a1aa", 14))

    def set_monitor_mode(self, enabled: bool) -> None:
        self._monitor_mode = enabled
        self.header_bar.setVisible(not enabled)
        self.ctrl_bar.setVisible(not enabled)
        if enabled:
            self._root_layout.setContentsMargins(0, 0, 0, 0)
        else:
            self._root_layout.setContentsMargins(1, 1, 1, 1)
        self._update_border()

    def _update_border(self) -> None:
        if self._monitor_mode:
            self.setStyleSheet("CameraTileWidget { border: none; border-radius: 0px; background-color: #000000; }")
        elif getattr(self, "_is_being_dragged", False):
            self.setStyleSheet(
                "CameraTileWidget { border: 2px dashed #3b82f6; border-radius: 6px; background-color: rgba(30, 58, 138, 0.22); }"
            )
        elif getattr(self, "_is_drop_target", False):
            self.setStyleSheet(
                "CameraTileWidget { border: 2px solid #38bdf8; border-radius: 6px; background-color: rgba(56, 189, 248, 0.16); }"
            )
        elif getattr(self, "_is_landing_pulse", False):
            self.setStyleSheet(
                "CameraTileWidget { border: 2px solid #10b981; border-radius: 6px; background-color: rgba(16, 185, 129, 0.14); }"
            )
        elif self._person_detected:
            self.setStyleSheet(
                "CameraTileWidget { border: 1px solid #ef4444; border-radius: 5px; background-color: #050507; }"
            )
        elif getattr(self, "_is_hovered", False):
            self.setStyleSheet(
                "CameraTileWidget { border: 1px solid #facc15; border-radius: 5px; background-color: #050507; }"
            )
        elif self._is_selected:
            self.setStyleSheet(
                "CameraTileWidget { border: 1px solid #3b82f6; border-radius: 5px; background-color: #050507; }"
            )
        else:
            self.setStyleSheet(
                "CameraTileWidget { border: 1px solid #1f1f24; border-radius: 5px; background-color: #050507; }"
            )

    def eventFilter(self, watched, event) -> bool:
        if watched in (self.header_bar, getattr(self, "grip_icon", None), getattr(self, "title_lbl", None)):
            if event.type() == QEvent.Type.MouseButtonPress and event.button() == Qt.MouseButton.LeftButton:
                self._header_drag_start = event.globalPosition().toPoint()
                self.selected.emit(self.config.cam_id)
            elif event.type() == QEvent.Type.MouseMove and (event.buttons() & Qt.MouseButton.LeftButton):
                if hasattr(self, "_header_drag_start") and self._header_drag_start is not None:
                    if (event.globalPosition().toPoint() - self._header_drag_start).manhattanLength() >= QApplication.startDragDistance():
                        self._header_drag_start = None
                        self._start_trello_drag()
                        return True
            elif event.type() == QEvent.Type.MouseButtonRelease:
                self._header_drag_start = None
        return super().eventFilter(watched, event)

    def _start_trello_drag(self) -> None:
        if self._is_maximized or self._monitor_mode:
            return

        # Snapshot the current tile to construct elevated Trello card
        pix = self.grab()
        if pix.isNull():
            return

        # Scale to a compact card size (~260px wide)
        target_w = min(280, max(200, pix.width()))
        scaled = pix.scaledToWidth(target_w, Qt.TransformationMode.SmoothTransformation)

        # Apply Trello-style 3.5 degree card tilt
        transform = QTransform()
        transform.rotate(3.5)
        tilted = scaled.transformed(transform, Qt.TransformationMode.SmoothTransformation)

        # Elevate with glow and subtle transparency
        ghost_img = QImage(tilted.width() + 16, tilted.height() + 16, QImage.Format.Format_ARGB32)
        ghost_img.fill(QColor(0, 0, 0, 0))
        p = QPainter(ghost_img)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.setOpacity(0.90)
        p.drawPixmap(8, 8, tilted)
        p.setOpacity(1.0)
        p.setPen(QPen(QColor("#38bdf8"), 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRoundedRect(QRectF(8, 8, tilted.width() - 1, tilted.height() - 1), 6, 6)
        p.end()

        drag_pix = QPixmap.fromImage(ghost_img)

        mime = QMimeData()
        mime.setData("application/x-omnisight-camera-id", self.config.cam_id.encode("utf-8"))

        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.setPixmap(drag_pix)
        drag.setHotSpot(QPoint(drag_pix.width() // 3, 24))

        self._is_being_dragged = True
        self._update_border()

        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._is_being_dragged = False
            self._update_border()

    def set_drop_target_state(self, is_target: bool) -> None:
        if self._is_drop_target != is_target:
            self._is_drop_target = is_target
            self._update_border()

    def flash_landing_glow(self) -> None:
        self._is_landing_pulse = True
        self._update_border()
        QTimer.singleShot(420, self._clear_landing_glow)

    def _clear_landing_glow(self) -> None:
        self._is_landing_pulse = False
        self._update_border()

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        if self._is_maximized or self._monitor_mode:
            event.ignore()
            return
        if event.mimeData().hasFormat("application/x-omnisight-camera-id"):
            src_id = bytes(event.mimeData().data("application/x-omnisight-camera-id")).decode("utf-8")
            if src_id != self.config.cam_id:
                self.set_drop_target_state(True)
                event.acceptProposedAction()
                return
        event.ignore()

    def dragMoveEvent(self, event: QDragMoveEvent) -> None:
        if event.mimeData().hasFormat("application/x-omnisight-camera-id"):
            src_id = bytes(event.mimeData().data("application/x-omnisight-camera-id")).decode("utf-8")
            if src_id != self.config.cam_id:
                event.acceptProposedAction()
                return
        event.ignore()

    def dragLeaveEvent(self, event: QDragLeaveEvent) -> None:
        self.set_drop_target_state(False)
        event.accept()

    def dropEvent(self, event: QDropEvent) -> None:
        self.set_drop_target_state(False)
        if event.mimeData().hasFormat("application/x-omnisight-camera-id"):
            src_id = bytes(event.mimeData().data("application/x-omnisight-camera-id")).decode("utf-8")
            if src_id != self.config.cam_id:
                event.acceptProposedAction()
                self.reorder_requested.emit(src_id, self.config.cam_id)
                return
        event.ignore()

    def update_config(self, config: CameraConfig) -> None:
        stream_changed = (
            self.config.protocol != config.protocol
            or self.config.ip != config.ip
            or self.config.port != config.port
            or self.config.device_id != config.device_id
            or self.config.username != config.username
            or self.config.password != config.password
            or self.config.quality != config.quality
            or self.config.rtsp_url != config.rtsp_url
            or self.config.audio_enabled != config.audio_enabled
        )
        was_running = self.is_streaming()
        if was_running and stream_changed:
            self.stop_stream()
        self.config = config
        self.title_lbl.setText(config.name)
        self.badge_lbl.setText(self._protocol_badge())
        self.canvas.set_camera_info(config.name, "Live Feed")
        self.canvas.set_chime_zone(getattr(config, "chime_zone", None))
        self._update_quality_btn()
        self._update_chime_btn()
        if self.worker is not None and not stream_changed:
            self.worker.config.name = config.name
            self.worker.set_detection_options(
                enabled=bool(getattr(config, "person_detection", True)),
                sensitivity=getattr(config, "detection_sensitivity", "ultra"),
                gpu_acceleration=bool(getattr(config, "gpu_acceleration", True)),
            )
        if was_running and stream_changed:
            self.start_stream()

    def _last_frame_cache_path(self) -> Path:
        return PRESET_THUMB_DIR / f"_last_frame_{self.config.cam_id}.jpg"

    def _load_cached_last_frame(self) -> None:
        try:
            fp = self._last_frame_cache_path()
            if fp.is_file():
                qimg = QImage(str(fp))
                if not qimg.isNull():
                    self.canvas.set_last_frame(qimg)
        except Exception:
            pass

    def _save_cached_last_frame(self, image: Optional[QImage]) -> None:
        if image is None or image.isNull():
            return
        try:
            PRESET_THUMB_DIR.mkdir(parents=True, exist_ok=True)
            fp = self._last_frame_cache_path()
            image.scaled(
                480,
                270,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ).save(str(fp), "JPG", 82)
        except Exception:
            pass

    def _update_wifi_btn(self, connected: bool) -> None:
        self.btn_power.setText("")
        self.btn_power.setFixedSize(26, 24)
        self.btn_power.setIconSize(QSize(14, 14))
        if connected:
            self.btn_power.setIcon(lucide_icon("wifi", "#3b82f6", 14))
            self.btn_power.setToolTip("Connected — Click to Disconnect Stream")
        else:
            self.btn_power.setIcon(lucide_icon("wifi_off", "#71717a", 14))
            self.btn_power.setToolTip("Disconnected — Click to Connect Stream")

    def is_streaming(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def toggle_stream(self) -> None:
        self.selected.emit(self.config.cam_id)
        if self.is_streaming():
            if self.canvas._is_disconnected_state():
                self.reconnect_stream()
            else:
                self.stop_stream()
        else:
            self.start_stream()

    def reconnect_stream(self) -> None:
        self.selected.emit(self.config.cam_id)
        if self.is_streaming():
            self.stop_stream()
        if (self.config.protocol or "lan8800").lower() not in ("demo", "rtsp") and self.config.ip:
            def _wake_bg(ip: str = self.config.ip, port: int = self.config.port, dev_id: str = self.config.device_id) -> None:
                try:
                    c = LanCameraClient(ip=ip, port=port, device_id=dev_id)
                    c._wake_camera_port_8800(include_cloud=True)
                except Exception:
                    pass
            threading.Thread(target=_wake_bg, daemon=True).start()
        self.start_stream()

    def start_stream(self) -> None:
        if self.is_streaming():
            return
        self.canvas.notify_manual_connect_started()
        self.canvas.set_status("Connecting to camera stream...", False)
        self.status_dot.set_state("connecting")
        self.worker = CameraStreamWorker(self.config, self)
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.detections_ready.connect(self._on_detections)
        self.worker.audio_ready.connect(self._on_audio)
        self.worker.status_changed.connect(self._on_status)
        self.worker.recording_changed.connect(self._on_recording_state)
        self.worker.start()

    def _retire_worker(self, worker: Optional[CameraStreamWorker]) -> None:
        if worker is None:
            return
        try:
            worker.stop_stream()
        except Exception:
            pass
        if worker.isRunning():
            if worker not in self._retiring_workers:
                self._retiring_workers.append(worker)

            def _cleanup(w: QThread = worker) -> None:
                if w in self._retiring_workers:
                    self._retiring_workers.remove(w)
                w.deleteLater()

            worker.finished.connect(_cleanup)
        else:
            worker.deleteLater()

    def stop_stream(self) -> None:
        if self.canvas._last_frame is not None and not self.canvas._last_frame.isNull():
            self._save_cached_last_frame(self.canvas._last_frame)
        self.audio_out.close()
        if self.worker is not None:
            worker = self.worker
            self.worker = None
            try:
                worker.frame_ready.disconnect()
                worker.detections_ready.disconnect()
                worker.audio_ready.disconnect()
                worker.status_changed.disconnect()
                worker.recording_changed.disconnect()
            except Exception:
                pass
            self._retire_worker(worker)
        self._audio_decay_timer.stop()
        self._audio_meter_level = 0.0
        self.audio_bar.setValue(0)
        self._update_wifi_btn(False)
        self._stop_tile_ptz_hold()
        self._rec_ui_timer.stop()
        self.btn_rec.setChecked(False)
        self.btn_rec.setIcon(lucide_icon("disc", "#a1a1aa", 14))
        self.rec_lbl.setVisible(False)
        self.person_lbl.setVisible(False)
        self._person_detected = False
        self._update_border()
        self.canvas.set_status("Disconnected", False)
        self.status_dot.set_state("idle")
        self.stats_lbl.setText("0.0 FPS  0 kbps")

    def _on_frame(self, cam_id: str, image: QImage, fps: float, kbps: float) -> None:
        _ = cam_id
        if not self.canvas._is_connected:
            self._update_wifi_btn(True)
            self.status_dot.set_state("live")
        self.canvas.set_frame(image)
        self.stats_lbl.setText(f"{fps:.1f} FPS  {kbps:.0f} kbps")
        import time as _time
        now_t = _time.monotonic()
        if now_t - self._last_thumb_save_ts >= 20.0:
            self._last_thumb_save_ts = now_t
            self._save_cached_last_frame(image)

    def _any_detection_in_chime_zone(self, boxes: list) -> bool:
        if not boxes:
            return False
        zone = CameraConfig.normalize_chime_zone(getattr(self.config, "chime_zone", None))
        if zone is None:
            return True
        for det in boxes:
            if len(det) >= 4 and CameraConfig.box_in_chime_zone(
                (float(det[0]), float(det[1]), float(det[2]), float(det[3])),
                zone,
            ):
                return True
        return False

    def _on_detections(self, cam_id: str, boxes: list) -> None:
        _ = cam_id
        has_person = bool(boxes)
        self.canvas.set_detections(boxes)
        if has_person != self._person_detected:
            self._person_detected = has_person
            self.person_lbl.setVisible(has_person)
            self._update_border()
        if has_person and bool(getattr(self.config, "person_detection", True)):
            import time as _time

            now_t = _time.monotonic()
            if bool(getattr(self.config, "detection_sound", True)) and self._any_detection_in_chime_zone(boxes):
                cd_sec = float(getattr(self.config, "detection_sound_cooldown", 10) or 10)
                if (now_t - self._last_chime_ts) >= cd_sec:
                    self._last_chime_ts = now_t
                    DetectionChimePlayer.play_chime()

    def _on_audio(self, cam_id: str, pcm_bytes: bytes, level: float) -> None:
        _ = cam_id
        # Instant attack for faint transients, smooth release via ballistics
        if level >= self._audio_meter_level:
            self._audio_meter_level = min(1.0, level)
        else:
            self._audio_meter_level = self._audio_meter_level * 0.72 + level * 0.28
        pct = int(round(min(100.0, self._audio_meter_level * 100.0)))
        if level > 0.02 and pct < 12:
            pct = 12
        self.audio_bar.setValue(pct)
        if pct > 0 and not self._audio_decay_timer.isActive():
            self._audio_decay_timer.start()
        self.audio_out.write_pcm(pcm_bytes)

    def _decay_audio_meter(self) -> None:
        if self._audio_meter_level <= 0.015:
            self._audio_meter_level = 0.0
            self.audio_bar.setValue(0)
            self._audio_decay_timer.stop()
            return
        self._audio_meter_level *= 0.80
        self.audio_bar.setValue(int(round(min(100.0, self._audio_meter_level * 100.0))))

    def _on_status(self, cam_id: str, status_text: str, is_connected: bool) -> None:
        _ = cam_id
        self._update_wifi_btn(is_connected)
        self.canvas.set_status(status_text, is_connected)
        low = (status_text or "").lower()
        if is_connected:
            self.status_dot.set_state("live")
        elif any(w in low for w in ("error", "failed", "invalid", "lost", "cannot connect", "retrying", "disconnected")):
            self.status_dot.set_state("error")
        else:
            self.status_dot.set_state("connecting")

    def _on_mute_toggled(self, checked: bool) -> None:
        self.selected.emit(self.config.cam_id)
        self.audio_out.set_muted(not checked)
        self.btn_mute.setIcon(
            lucide_icon("volume_2", "#10b981", 14) if checked else lucide_icon("volume_x", "#71717a", 14)
        )

    def _update_chime_btn(self) -> None:
        chime_on = bool(getattr(self.config, "detection_sound", True))
        cd_sec = int(getattr(self.config, "detection_sound_cooldown", 10) or 10)
        zone = CameraConfig.normalize_chime_zone(getattr(self.config, "chime_zone", None))
        zone_desc = "Custom Area" if zone is not None else "Whole Frame"
        self.btn_chime.blockSignals(True)
        self.btn_chime.setChecked(chime_on)
        self.btn_chime.blockSignals(False)
        if chime_on:
            self.btn_chime.setIcon(lucide_icon("bell", "#f59e0b", 14))
            self.btn_chime.setToolTip(
                f"Person Detection Chime: ON ({cd_sec}s cooldown • {zone_desc}) — Click to Mute Chime"
            )
        else:
            self.btn_chime.setIcon(lucide_icon("bell_off", "#71717a", 14))
            self.btn_chime.setToolTip(
                f"Person Detection Chime: OFF ({zone_desc}) — Click to Enable Chime"
            )
        if hasattr(self, "btn_chime_zone"):
            editing = bool(getattr(self.canvas, "_editing_chime_zone", False))
            self.btn_chime_zone.blockSignals(True)
            self.btn_chime_zone.setChecked(editing or zone is not None)
            self.btn_chime_zone.blockSignals(False)
            if editing:
                self.btn_chime_zone.setIcon(lucide_icon("scan", "#fbbf24", 14))
                self.btn_chime_zone.setToolTip(
                    "Editing Chime Trigger Area — Drag a box on the footage or click to finish"
                )
            elif zone is not None:
                self.btn_chime_zone.setIcon(lucide_icon("scan", "#f59e0b", 14))
                self.btn_chime_zone.setToolTip(
                    "Chime Trigger Area: Custom Area Set — Click to edit area on footage"
                )
            else:
                self.btn_chime_zone.setIcon(lucide_icon("scan", "#71717a", 14))
                self.btn_chime_zone.setToolTip(
                    "Chime Trigger Area: Whole Frame (Default) — Click to draw a custom trigger area (e.g. Gate)"
                )

    def toggle_chime_zone_editor(self) -> None:
        self.selected.emit(self.config.cam_id)
        new_mode = not bool(getattr(self.canvas, "_editing_chime_zone", False))
        self.canvas.set_chime_zone_edit_mode(new_mode)

    def reset_chime_zone(self) -> None:
        self.selected.emit(self.config.cam_id)
        self.config.chime_zone = None
        self.canvas.set_chime_zone(None)
        self.canvas.set_chime_zone_edit_mode(False)
        self._update_chime_btn()
        self.canvas.show_info_toast(f"Chime trigger area reset to Whole Frame on {self.config.name}")
        self.config_changed.emit()

    def _on_canvas_chime_zone_changed(self, zone: object) -> None:
        norm = CameraConfig.normalize_chime_zone(zone)
        self.config.chime_zone = norm
        self.canvas.set_chime_zone(norm)
        self._update_chime_btn()
        if norm is not None:
            self.canvas.show_info_toast(f"Saved custom Chime Trigger Area on {self.config.name}")
        else:
            self.canvas.show_info_toast(f"Chime Trigger Area set to Whole Frame on {self.config.name}")
        self.config_changed.emit()

    def _on_canvas_chime_zone_edit_toggled(self, editing: bool) -> None:
        _ = editing
        self._update_chime_btn()
        self.config_changed.emit()

    def _toggle_detection_sound(self) -> None:
        self.selected.emit(self.config.cam_id)
        new_state = not bool(getattr(self.config, "detection_sound", True))
        self.config.detection_sound = new_state
        self._update_chime_btn()
        if new_state:
            DetectionChimePlayer.play_chime(force=True)
        self.config_changed.emit()

    def ignore_detection_box(self, box: Optional[tuple] = None) -> bool:
        """Mark the given detection box (or all currently active/pinned detection boxes on this tile) as 'Not a Person'."""
        self.selected.emit(self.config.cam_id)
        targets: List[Tuple[float, float, float, float]] = []
        if box is not None and len(box) >= 4:
            targets.append((float(box[0]), float(box[1]), float(box[2]), float(box[3])))
        else:
            for det in list(self.canvas._detections or self.canvas._pinned_detections):
                if len(det) >= 4:
                    targets.append((float(det[0]), float(det[1]), float(det[2]), float(det[3])))

        if not targets:
            self.canvas.show_error_toast("No recent person detection on this camera to mark")
            return False

        for tbox in targets:
            if self.worker is not None:
                self.worker.ignore_detection_box(tbox)
            else:
                if not hasattr(self.config, "ignored_objects") or self.config.ignored_objects is None:
                    self.config.ignored_objects = []
                self.config.ignored_objects.append(
                    {
                        "box": [round(tbox[0], 4), round(tbox[1], 4), round(tbox[2], 4), round(tbox[3], 4)],
                        "fingerprint": [],
                        "created_at": datetime.now().strftime("%H:%M"),
                    }
                )
            self.canvas.remove_pinned_box(tbox)

        remaining = list(self.canvas._detections)
        self._person_detected = bool(remaining)
        self.person_lbl.setVisible(bool(remaining))
        self._update_border()
        total_ig = len(getattr(self.config, "ignored_objects", None) or [])
        self.canvas.show_info_toast(f'Marked as "Not a Person" ({total_ig} ignored on {self.config.name})')
        self.config_changed.emit()
        return True

    def clear_ignored_objects(self) -> None:
        self.selected.emit(self.config.cam_id)
        self.config.ignored_objects = []
        if self.worker is not None:
            self.worker.clear_ignored_objects()
        self.canvas.show_info_toast(f'Cleared ignored objects on {self.config.name}')
        self.config_changed.emit()

    def _update_quality_btn(self) -> None:
        is_hd = self.config.quality == 1
        self.btn_quality.setText("HD" if is_hd else "SD")
        if is_hd:
            self.btn_quality.setStyleSheet(
                "QPushButton { background-color: #172554; border: 1px solid #2563eb; border-radius: 4px; "
                "color: #93c5fd; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700; padding: 0; }"
                "QPushButton:hover { background-color: #1e3a8a; border-color: #3b82f6; color: #ffffff; }"
            )
        else:
            self.btn_quality.setStyleSheet(
                "QPushButton { background-color: #18181c; border: 1px solid #2e2e36; border-radius: 4px; "
                "color: #a1a1aa; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700; padding: 0; }"
                "QPushButton:hover { background-color: #24242b; border-color: #52525b; color: #f4f4f5; }"
            )

    def set_quality_profile(self, quality: int, emit_change: bool = True) -> None:
        new_q = 1 if int(quality) else 0
        if self.config.quality == new_q:
            self._update_quality_btn()
            return
        self.config.quality = new_q
        self._update_quality_btn()
        if self.worker is not None:
            self.worker.set_quality(new_q)
        if emit_change:
            self.config_changed.emit()

    def _toggle_quality(self) -> None:
        self.selected.emit(self.config.cam_id)
        new_q = 0 if self.config.quality == 1 else 1
        self.set_quality_profile(new_q, emit_change=True)

    def _on_snap_clicked(self) -> None:
        self.take_snapshot()

    def take_snapshot(self) -> None:
        self.selected.emit(self.config.cam_id)
        if not self.is_streaming() or self.worker is None:
            self.canvas.show_error_toast("Cannot take photo: Camera stream is offline")
            self._flash_snapshot_button_err()
            return

        saved = self.worker.take_snapshot(SNAPSHOT_DIR)
        # Fallback to canvas displayed QImage if worker cache had no frame
        if not saved and self.canvas._qimage is not None and not self.canvas._qimage.isNull():
            try:
                SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
                safe_name = "".join(
                    c if c.isalnum() or c in ("-", "_") else "_" for c in self.config.name
                ).strip("_") or "Camera"
                ts = datetime.now().strftime("%Y%m%d_%H%M%S")
                fp = SNAPSHOT_DIR / f"{safe_name}_{ts}.jpg"
                if self.canvas._qimage.save(str(fp), "JPG", 95):
                    saved = str(fp)
            except Exception as e:
                logger.warning("Canvas fallback snapshot failed: %s", e)

        if saved:
            self.canvas.trigger_shutter_flash()
            self._flash_snapshot_button_ok(saved)
            self.canvas.show_snapshot_toast(saved)
            try:
                import winsound
                winsound.MessageBeep(winsound.MB_OK)
            except Exception:
                pass
            self.snapshot_saved.emit(saved)
        else:
            self.canvas.show_error_toast("Could not capture frame. Waiting for stream...")
            self._flash_snapshot_button_err()

    def _flash_snapshot_button_ok(self, filepath: str) -> None:
        self._last_saved_snapshot = filepath
        self.btn_snap.setIcon(lucide_icon("check", "#10b981", 14))
        self.btn_snap.setToolTip("Snapshot Saved! Click to open photo")
        self.btn_snap.setStyleSheet("QPushButton#ghostBtn { background-color: #064e3b; border: 1px solid #059669; }")
        QTimer.singleShot(2500, self._restore_snapshot_button)

    def _flash_snapshot_button_err(self) -> None:
        self.btn_snap.setIcon(lucide_icon("alert_triangle", "#ef4444", 14))
        self.btn_snap.setToolTip("Could not take snapshot — stream offline")
        self.btn_snap.setStyleSheet("QPushButton#ghostBtn { background-color: #450a0a; border: 1px solid #dc2626; }")
        QTimer.singleShot(2200, self._restore_snapshot_button)

    def _restore_snapshot_button(self) -> None:
        self._last_saved_snapshot = None
        self.btn_snap.setIcon(lucide_icon("camera", "#a1a1aa", 14))
        self.btn_snap.setToolTip("Take Photo / Save Snapshot (JPG)")
        self.btn_snap.setStyleSheet("")

    @staticmethod
    def _open_folder(folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            try:
                os.startfile(str(folder))
            except Exception:
                pass

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_S):
            self.take_snapshot()
            event.accept()
            return
        super().keyPressEvent(event)

    def toggle_recording(self) -> None:
        self.selected.emit(self.config.cam_id)
        if self.worker is None or not self.is_streaming():
            self.btn_rec.setChecked(False)
            return
        if self.worker.is_recording():
            self.worker.stop_recording()
        else:
            self.worker.start_recording(RECORDING_DIR)

    def _on_recording_state(self, cam_id: str, is_rec: bool, path: str) -> None:
        import time as _time

        self.btn_rec.setChecked(is_rec)
        self.btn_rec.setIcon(lucide_icon("disc", "#ef4444" if is_rec else "#a1a1aa", 14))
        if is_rec:
            self._rec_start_mono = _time.monotonic()
            self._rec_blink_state = True
            self.rec_lbl.setText("● REC 00:00")
            self.rec_lbl.setVisible(True)
            self._rec_ui_timer.start()
        else:
            self._rec_ui_timer.stop()
            self.rec_lbl.setVisible(False)
        self.recording_toggled.emit(cam_id, is_rec, path)

    def _update_rec_timer_label(self) -> None:
        import time as _time

        if not self.rec_lbl.isVisible():
            self._rec_ui_timer.stop()
            return
        elapsed = max(0, int(_time.monotonic() - self._rec_start_mono))
        self._rec_blink_state = not self._rec_blink_state
        dot = "●" if self._rec_blink_state else "○"
        mm, ss = divmod(elapsed, 60)
        if mm >= 60:
            hh, mm = divmod(mm, 60)
            self.rec_lbl.setText(f"{dot} REC {hh:02d}:{mm:02d}:{ss:02d}")
        else:
            self.rec_lbl.setText(f"{dot} REC {mm:02d}:{ss:02d}")


# ---------------------------------------------------------------------------
# PTZ Hardware Presets Pop-Out Flyout (6 Hardware PTZX Slots)
# ---------------------------------------------------------------------------
class PTZPresetPopup(QDialog):
    """Pop-out flyout anchored to the center PTZ Gimbal button for saving & recalling 6 PTZ preset positions."""

    MAX_SLOTS = 6

    def __init__(
        self,
        tile: CameraTileWidget,
        on_save_config,
        on_status_msg,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent, Qt.WindowType.Popup | Qt.WindowType.FramelessWindowHint)
        self.tile = tile
        self.config = tile.config
        self.on_save_config = on_save_config
        self.on_status_msg = on_status_msg
        self.setFixedWidth(368)
        self.setStyleSheet(
            NVR_STYLESHEET
            + """
            QDialog {
                background-color: #101014;
                border: 1px solid #2e2e38;
                border-radius: 8px;
            }
            QFrame#presetCardSaved {
                background-color: #16161d;
                border: 1px solid #272732;
                border-radius: 6px;
            }
            QFrame#presetCardSaved:hover {
                border: 1px solid #3b82f6;
                background-color: #191922;
            }
            QFrame#presetCardEmpty {
                background-color: #121217;
                border: 1px dashed #24242e;
                border-radius: 6px;
            }
            QLineEdit#presetNameEdit {
                background-color: transparent;
                border: 1px solid transparent;
                border-radius: 3px;
                color: #f4f4f5;
                font-size: 12px;
                font-weight: 600;
                padding: 1px 4px;
            }
            QLineEdit#presetNameEdit:hover, QLineEdit#presetNameEdit:focus {
                background-color: #09090b;
                border: 1px solid #3b82f6;
            }
            """
        )
        self._root_layout = QVBoxLayout(self)
        self._root_layout.setContentsMargins(12, 12, 12, 12)
        self._root_layout.setSpacing(8)
        self._rebuild_content()

    def _preset_map(self) -> Dict[int, dict]:
        res: Dict[int, dict] = {}
        for item in self.config.ptz_presets or []:
            if isinstance(item, dict) and "slot" in item:
                try:
                    res[int(item["slot"])] = item
                except Exception:
                    pass
        return res

    def _save_preset_map(self, pmap: Dict[int, dict]) -> None:
        self.config.ptz_presets = [pmap[k] for k in sorted(pmap.keys())]
        self.on_save_config()

    def _clear_layout(self, layout) -> None:
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()
            elif child.layout():
                self._clear_layout(child.layout())

    def _rebuild_content(self) -> None:
        self._clear_layout(self._root_layout)

        # Header Row
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(6)
        ic = QLabel()
        ic.setPixmap(lucide_pixmap("crosshair", "#3b82f6", 15))
        hdr_row.addWidget(ic)

        title = QLabel("PTZ PRESET POSITIONS")
        title.setStyleSheet(
            "color: #f4f4f5; font-size: 11px; font-weight: 700; letter-spacing: 0.7px;"
        )
        hdr_row.addWidget(title)

        cam_badge = QLabel(self.config.name)
        cam_badge.setStyleSheet(
            "background-color: #18181f; color: #93c5fd; border: 1px solid #1e3a8a; "
            "border-radius: 3px; padding: 1px 6px; font-size: 10px; font-weight: 600;"
        )
        hdr_row.addWidget(cam_badge)
        hdr_row.addStretch()

        btn_close = QPushButton()
        btn_close.setObjectName("ghostBtn")
        btn_close.setIcon(lucide_icon("x", "#a1a1aa", 13))
        btn_close.setFixedSize(22, 22)
        btn_close.setToolTip("Close Presets")
        btn_close.clicked.connect(self.close)
        hdr_row.addWidget(btn_close)
        self._root_layout.addLayout(hdr_row)

        sub = QLabel("Click Go to auto-pan camera to a saved preset, or Save Current to store angle.")
        sub.setStyleSheet("color: #71717a; font-size: 11px;")
        sub.setWordWrap(True)
        self._root_layout.addWidget(sub)

        pmap = self._preset_map()

        for slot_idx in range(self.MAX_SLOTS):
            slot_num = slot_idx + 1
            entry = pmap.get(slot_idx)
            is_saved = entry is not None

            card = QFrame()
            card.setObjectName("presetCardSaved" if is_saved else "presetCardEmpty")
            card.setFixedHeight(52)
            row = QHBoxLayout(card)
            row.setContentsMargins(8, 6, 8, 6)
            row.setSpacing(8)

            # Thumbnail or Slot Badge
            thumb_lbl = QLabel()
            thumb_lbl.setFixedSize(60, 36)
            thumb_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            thumb_path = entry.get("thumb", "") if entry else ""
            has_thumb = False
            if thumb_path and os.path.isfile(thumb_path):
                pix = QPixmap(thumb_path)
                if not pix.isNull():
                    thumb_lbl.setPixmap(
                        pix.scaled(
                            60,
                            36,
                            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                            Qt.TransformationMode.SmoothTransformation,
                        )
                    )
                    thumb_lbl.setStyleSheet("border: 1px solid #3f3f46; border-radius: 3px; background-color: #09090b;")
                    has_thumb = True
            if not has_thumb:
                thumb_lbl.setText(f"#{slot_num}")
                thumb_lbl.setStyleSheet(
                    "background-color: #18181f; color: #3b82f6; border: 1px solid #272732; "
                    "border-radius: 4px; font-family: 'Cascadia Mono', 'Consolas', monospace; "
                    "font-size: 11px; font-weight: 700;"
                    if is_saved
                    else "background-color: #0d0d11; color: #52525b; border: 1px solid #1f1f26; "
                    "border-radius: 4px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px;"
                )
            row.addWidget(thumb_lbl)

            # Preset Name & Subtitle
            meta_col = QVBoxLayout()
            meta_col.setContentsMargins(0, 0, 0, 0)
            meta_col.setSpacing(1)

            name_edit = QLineEdit(entry.get("name", f"Preset {slot_num}") if is_saved else f"Preset {slot_num}")
            name_edit.setObjectName("presetNameEdit")
            name_edit.setPlaceholderText(f"Preset {slot_num} Name...")
            name_edit.setToolTip("Click to rename this preset position")
            if is_saved:
                name_edit.editingFinished.connect(
                    lambda s=slot_idx, ed=name_edit: self._rename_preset(s, ed.text())
                )
            meta_col.addWidget(name_edit)

            status_txt = (
                f"Slot #{slot_num} • Saved ({entry.get('updated_at', 'Ready')})"
                if is_saved
                else f"Slot #{slot_num} • Empty slot"
            )
            sub_lbl = QLabel(status_txt)
            sub_lbl.setStyleSheet("color: #71717a; font-size: 10px; padding-left: 4px;")
            meta_col.addWidget(sub_lbl)
            row.addLayout(meta_col, 1)

            # Action Buttons
            if is_saved:
                btn_go = QPushButton("Go")
                btn_go.setObjectName("primaryBtn")
                btn_go.setIcon(lucide_icon("pan", "#ffffff", 13))
                btn_go.setFixedSize(56, 28)
                btn_go.setToolTip(f"Pan camera to {entry.get('name', f'Preset {slot_num}')}")
                btn_go.clicked.connect(lambda _, s=slot_idx: self._goto_preset(s))
                row.addWidget(btn_go)

                btn_upd = QPushButton()
                btn_upd.setIcon(lucide_icon("save", "#a1a1aa", 13))
                btn_upd.setFixedSize(28, 28)
                btn_upd.setToolTip("Overwrite this preset with current camera angle")
                btn_upd.clicked.connect(lambda _, s=slot_idx, ed=name_edit: self._save_preset(s, ed.text()))
                row.addWidget(btn_upd)

                btn_del = QPushButton()
                btn_del.setObjectName("dangerGhostBtn")
                btn_del.setIcon(lucide_icon("trash", "#a1a1aa", 13))
                btn_del.setFixedSize(26, 28)
                btn_del.setToolTip("Clear this preset slot")
                btn_del.clicked.connect(lambda _, s=slot_idx: self._clear_preset(s))
                row.addWidget(btn_del)
            else:
                btn_save = QPushButton("Save Current")
                btn_save.setIcon(lucide_icon("plus", "#93c5fd", 12))
                btn_save.setFixedHeight(28)
                btn_save.setStyleSheet(
                    "QPushButton { background-color: #172554; border: 1px solid #1e3a8a; border-radius: 4px; "
                    "color: #93c5fd; font-size: 11px; font-weight: 600; padding: 0 10px; }"
                    "QPushButton:hover { background-color: #1e3a8a; border-color: #3b82f6; color: #ffffff; }"
                )
                btn_save.setToolTip(f"Save camera's current PTZ angle into Slot #{slot_num}")
                btn_save.clicked.connect(lambda _, s=slot_idx, ed=name_edit: self._save_preset(s, ed.text()))
                row.addWidget(btn_save)

            self._root_layout.addWidget(card)

        # Bottom Quick Reset Bar
        bot_row = QHBoxLayout()
        bot_row.setSpacing(6)
        btn_stop_zoom = QPushButton("Stop Gimbal && Reset Digital Zoom")
        btn_stop_zoom.setIcon(lucide_icon("refresh", "#a1a1aa", 13))
        btn_stop_zoom.setFixedHeight(28)
        btn_stop_zoom.clicked.connect(self._stop_and_reset)
        bot_row.addWidget(btn_stop_zoom)
        self._root_layout.addLayout(bot_row)

    def _save_preset(self, slot_idx: int, raw_name: str) -> None:
        from datetime import datetime

        slot_num = slot_idx + 1
        clean_name = (raw_name or "").strip() or f"Preset {slot_num}"
        demo_coords = (0.0, 0.0)
        thumb_path = None
        if self.tile.worker is not None:
            demo_coords = self.tile.worker.ptz_preset_set(slot_idx)
            thumb_path = self.tile.worker.capture_preset_thumbnail(PRESET_THUMB_DIR, slot_idx)

        pmap = self._preset_map()
        prev = pmap.get(slot_idx, {})
        pmap[slot_idx] = {
            "slot": slot_idx,
            "name": clean_name,
            "thumb": thumb_path or prev.get("thumb", ""),
            "updated_at": datetime.now().strftime("%H:%M"),
            "demo_pan": demo_coords[0],
            "demo_tilt": demo_coords[1],
        }
        self._save_preset_map(pmap)
        self.on_status_msg(f"Saved PTZ Preset #{slot_num} ('{clean_name}') on {self.config.name}")
        self._rebuild_content()

    def _rename_preset(self, slot_idx: int, new_name: str) -> None:
        pmap = self._preset_map()
        if slot_idx not in pmap:
            return
        clean = (new_name or "").strip() or f"Preset {slot_idx + 1}"
        if pmap[slot_idx].get("name") != clean:
            pmap[slot_idx]["name"] = clean
            self._save_preset_map(pmap)
            self.on_status_msg(f"Renamed PTZ Preset #{slot_idx + 1} to '{clean}'")

    def _goto_preset(self, slot_idx: int) -> None:
        pmap = self._preset_map()
        entry = pmap.get(slot_idx, {})
        name = entry.get("name", f"Preset {slot_idx + 1}")
        if self.tile.worker is not None:
            coords = (float(entry.get("demo_pan", 0.0)), float(entry.get("demo_tilt", 0.0)))
            self.tile.worker.ptz_preset_goto(
                slot_idx,
                demo_coords=coords,
                thumb_path=entry.get("thumb", ""),
            )
            self.on_status_msg(f"Auto-panning {self.config.name} to Preset #{slot_idx + 1}: {name}")
        else:
            self.on_status_msg(f"Connect {self.config.name} stream first to pan to '{name}'")
        self.close()

    def _clear_preset(self, slot_idx: int) -> None:
        pmap = self._preset_map()
        entry = pmap.pop(slot_idx, None)
        if self.tile.worker is not None:
            self.tile.worker.ptz_preset_clear(slot_idx)
        if entry and entry.get("thumb") and os.path.isfile(entry["thumb"]):
            try:
                os.remove(entry["thumb"])
            except Exception:
                pass
        self._save_preset_map(pmap)
        self.on_status_msg(f"Cleared PTZ Preset #{slot_idx + 1} on {self.config.name}")
        self._rebuild_content()

    def _stop_and_reset(self) -> None:
        self.tile.canvas.reset_zoom()
        if self.tile.worker is not None:
            self.tile.worker.ptz_move("stop")
        self.on_status_msg(f"Stopped PTZ & reset zoom on {self.config.name}")
        self.close()


# ---------------------------------------------------------------------------
# 24-Hour Interactive SD Card Event Timeline & Footage Review Workspace
# ---------------------------------------------------------------------------
class DayTimelineWidget(QWidget):
    """
    24-hour interactive, zoomable timeline strip (00:00 to 24:00) for SD card recording segments.
    - Color-codes Continuous (`#3b82f6`), Motion (`#f59e0b`), and Anomaly/Alarm (`#ef4444`) clips
    - Highlights locally backed-up clips with a green accent bar (`#10b981`)
    - Supports smooth mouse-wheel zoom (5m up to 24h) centered at cursor, preset zoom buttons,
      right-click / middle-click / Shift+drag panning, and an interactive 24h mini-overview scrollbar
    - Renders adaptive 12-hour AM/PM tick marks down to 1-minute / 10-second intervals when zoomed in
    """

    seek_requested = pyqtSignal(int)  # target wall-clock timestamp (UTC epoch seconds)
    zoom_changed = pyqtSignal(float, int, int)  # (view_span_sec, win_start_ts, win_end_ts)

    MIN_SPAN_SEC = 300.0    # 5 minutes max zoom-in (288x)
    MAX_SPAN_SEC = 86400.0  # 24 hours full day (1x)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(54)
        self.setMouseTracking(True)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self._day_start_ts: int = 0
        self._segments: List[RecordingSegment] = []
        self._backed_up_ids: set = set()
        self._playhead_ts: int = 0
        self._hover_ts: Optional[int] = None

        # Zoom & Pan state (offset and span in seconds within [0, 86400])
        self._view_start_sec: float = 0.0
        self._view_span_sec: float = 86400.0
        self._panning: bool = False
        self._pan_overview_drag: bool = False
        self._pan_start_x: float = 0.0
        self._pan_orig_view_start: float = 0.0

    def set_day_and_segments(
        self,
        day_start_ts: int,
        segments: List[RecordingSegment],
        backed_up_ids: Optional[set] = None,
    ) -> None:
        day_changed = (int(day_start_ts) != self._day_start_ts)
        self._day_start_ts = int(day_start_ts)
        self._segments = list(segments)
        self._backed_up_ids = set(backed_up_ids) if backed_up_ids else set()
        if day_changed:
            self._view_start_sec = 0.0
            self._view_span_sec = self.MAX_SPAN_SEC
        self._emit_zoom_changed()
        self.update()

    def mark_segment_backed_up(self, seg_id: int) -> None:
        self._backed_up_ids.add(int(seg_id))
        self.update()

    def set_playhead(self, ts_sec: int, ensure_visible: bool = True) -> None:
        self._playhead_ts = int(ts_sec)
        if ensure_visible and self._day_start_ts > 0 and self._view_span_sec < self.MAX_SPAN_SEC:
            rel = float(self._playhead_ts - self._day_start_ts)
            if 0.0 <= rel <= self.MAX_SPAN_SEC:
                margin = self._view_span_sec * 0.06
                if rel < self._view_start_sec + margin or rel > self._view_start_sec + self._view_span_sec - margin:
                    self._view_start_sec = max(
                        0.0,
                        min(self.MAX_SPAN_SEC - self._view_span_sec, rel - self._view_span_sec * 0.35),
                    )
                    self._emit_zoom_changed()
        self.update()

    def set_view_span(self, span_sec: float, anchor_ts: Optional[int] = None) -> None:
        new_span = max(self.MIN_SPAN_SEC, min(self.MAX_SPAN_SEC, float(span_sec)))
        if anchor_ts is not None and self._day_start_ts > 0:
            anchor_rel = max(0.0, min(self.MAX_SPAN_SEC, float(anchor_ts - self._day_start_ts)))
            ratio = 0.5
        elif (
            self._day_start_ts > 0
            and self._playhead_ts >= self._day_start_ts
            and self._view_start_sec <= (self._playhead_ts - self._day_start_ts) <= (self._view_start_sec + self._view_span_sec)
        ):
            anchor_rel = float(self._playhead_ts - self._day_start_ts)
            ratio = (anchor_rel - self._view_start_sec) / max(1.0, self._view_span_sec)
        else:
            anchor_rel = self._view_start_sec + self._view_span_sec * 0.5
            ratio = 0.5

        self._view_span_sec = new_span
        self._view_start_sec = max(
            0.0,
            min(self.MAX_SPAN_SEC - new_span, anchor_rel - ratio * new_span),
        )
        self._emit_zoom_changed()
        self.update()

    def zoom_in(self) -> None:
        self.set_view_span(self._view_span_sec * 0.5)

    def zoom_out(self) -> None:
        self.set_view_span(self._view_span_sec * 2.0)

    def reset_zoom(self) -> None:
        self.set_view_span(self.MAX_SPAN_SEC)

    def pan_by_fraction(self, fraction: float) -> None:
        if self._view_span_sec >= self.MAX_SPAN_SEC:
            return
        delta = self._view_span_sec * float(fraction)
        self._view_start_sec = max(
            0.0,
            min(self.MAX_SPAN_SEC - self._view_span_sec, self._view_start_sec + delta),
        )
        self._emit_zoom_changed()
        self.update()

    def _emit_zoom_changed(self) -> None:
        if self._day_start_ts > 0:
            s_ts = self._day_start_ts + int(self._view_start_sec)
            e_ts = self._day_start_ts + int(min(86399.0, self._view_start_sec + self._view_span_sec))
        else:
            s_ts, e_ts = 0, 0
        self.zoom_changed.emit(self._view_span_sec, s_ts, e_ts)

    def _x_to_ts(self, x: float) -> int:
        w = max(1, self.width() - 16)
        ratio = max(0.0, min(1.0, (x - 8.0) / float(w)))
        rel_sec = self._view_start_sec + ratio * self._view_span_sec
        rel_sec = max(0.0, min(86399.0, rel_sec))
        return self._day_start_ts + int(round(rel_sec))

    def _ts_to_x(self, ts_sec: int) -> float:
        w = max(1, self.width() - 16)
        rel = float(ts_sec - self._day_start_ts) - self._view_start_sec
        return 8.0 + (rel / max(1.0, self._view_span_sec)) * float(w)

    def _in_overview_bar(self, y: float) -> bool:
        return self._view_span_sec < self.MAX_SPAN_SEC and y >= (self.height() - 9)

    def _pan_overview_to_x(self, x: float) -> None:
        w = max(1.0, float(self.width() - 16))
        ratio = max(0.0, min(1.0, (x - 8.0) / w))
        center_sec = ratio * self.MAX_SPAN_SEC
        self._view_start_sec = max(
            0.0,
            min(self.MAX_SPAN_SEC - self._view_span_sec, center_sec - self._view_span_sec * 0.5),
        )
        self._emit_zoom_changed()
        self.update()

    def wheelEvent(self, event: QWheelEvent) -> None:
        if self._day_start_ts <= 0:
            super().wheelEvent(event)
            return
        dy = event.angleDelta().y()
        dx = event.angleDelta().x()
        if dy != 0 and not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
            w = max(1.0, float(self.width() - 16))
            mx = event.position().x()
            ratio = max(0.0, min(1.0, (mx - 8.0) / w))
            anchor_rel = self._view_start_sec + ratio * self._view_span_sec
            factor = 0.72 if dy > 0 else (1.0 / 0.72)
            new_span = max(self.MIN_SPAN_SEC, min(self.MAX_SPAN_SEC, self._view_span_sec * factor))
            if abs(new_span - self.MAX_SPAN_SEC) < 5.0:
                new_span = self.MAX_SPAN_SEC
            self._view_span_sec = new_span
            self._view_start_sec = max(
                0.0,
                min(self.MAX_SPAN_SEC - new_span, anchor_rel - ratio * new_span),
            )
            self._hover_ts = self._x_to_ts(mx)
            self._emit_zoom_changed()
            self.update()
            event.accept()
            return
        pan_delta = dx if dx != 0 else dy
        if pan_delta != 0 and self._view_span_sec < self.MAX_SPAN_SEC:
            self.pan_by_fraction(-0.18 if pan_delta > 0 else 0.18)
            event.accept()
            return
        super().wheelEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if self._day_start_ts <= 0:
            super().mousePressEvent(event)
            return
        px = event.position().x()
        py = event.position().y()

        # Clicking the bottom 24h mini-overview bar pans the zoomed viewport
        if event.button() == Qt.MouseButton.LeftButton and self._in_overview_bar(py):
            self._pan_overview_drag = True
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            self._pan_overview_to_x(px)
            event.accept()
            return

        # Right-click, Middle-click, or Shift+Left-click pans the zoomed timeline
        if (
            event.button() in (Qt.MouseButton.RightButton, Qt.MouseButton.MiddleButton)
            or (
                event.button() == Qt.MouseButton.LeftButton
                and (event.modifiers() & Qt.KeyboardModifier.ShiftModifier)
            )
        ) and self._view_span_sec < self.MAX_SPAN_SEC:
            self._panning = True
            self._pan_start_x = px
            self._pan_orig_view_start = self._view_start_sec
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        if event.button() == Qt.MouseButton.LeftButton:
            ts = self._x_to_ts(px)
            self._playhead_ts = ts
            self.seek_requested.emit(ts)
            self.update()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if self._day_start_ts > 0:
            px = event.position().x()
            if self._pan_overview_drag:
                self._pan_overview_to_x(px)
                event.accept()
                return
            if self._panning:
                w = max(1.0, float(self.width() - 16))
                dx = px - self._pan_start_x
                dt_sec = -(dx / w) * self._view_span_sec
                self._view_start_sec = max(
                    0.0,
                    min(self.MAX_SPAN_SEC - self._view_span_sec, self._pan_orig_view_start + dt_sec),
                )
                self._emit_zoom_changed()
                self.update()
                event.accept()
                return

            ts = self._x_to_ts(px)
            self._hover_ts = ts
            if event.buttons() & Qt.MouseButton.LeftButton:
                self._playhead_ts = ts
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if self._pan_overview_drag:
            self._pan_overview_drag = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            event.accept()
            return
        if self._panning:
            self._panning = False
            self.setCursor(Qt.CursorShape.PointingHandCursor)
            event.accept()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._day_start_ts > 0:
            ts = self._x_to_ts(event.position().x())
            self.seek_requested.emit(ts)
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover_ts = None
        self.update()
        super().leaveEvent(event)

    def _tick_intervals(self) -> Tuple[int, int]:
        """Return (major_step_sec, minor_step_sec) adapted to current zoom span."""
        span = self._view_span_sec
        if span > 43200:      # > 12h: 3h major, 1h minor
            return 10800, 3600
        elif span > 18000:    # > 5h: 1h major, 15m minor
            return 3600, 900
        elif span > 7200:     # > 2h: 30m major, 5m minor
            return 1800, 300
        elif span > 2700:     # > 45m: 10m major, 2m minor
            return 600, 120
        elif span > 900:      # > 15m: 5m major, 1m minor
            return 300, 60
        else:                 # <= 15m: 1m major, 10s minor
            return 60, 10

    @staticmethod
    def _format_rel_tick_12h(sec_of_day: int, show_minutes: bool) -> str:
        sec_of_day = max(0, min(86400, int(sec_of_day)))
        if sec_of_day >= 86400:
            return "12 AM"
        total_min = sec_of_day // 60
        hr24 = (total_min // 60) % 24
        mn = total_min % 60
        hr12 = 12 if (hr24 % 12 == 0) else (hr24 % 12)
        ampm = "AM" if hr24 < 12 else "PM"
        if show_minutes or mn != 0:
            return f"{hr12}:{mn:02d} {ampm}"
        return f"{hr12} {ampm}"

    def paintEvent(self, event) -> None:
        _ = event
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect()
        p.fillRect(rect, QColor("#0d0d11"))

        w = max(1, rect.width() - 16)
        bar_top = 8
        bar_h = 20
        left_x = 8.0
        right_x = float(8 + w)

        # Track background
        track_rect = QRectF(8, bar_top, w, bar_h)
        p.setPen(QPen(QColor("#23232b"), 1))
        p.setBrush(QColor("#15151b"))
        p.drawRoundedRect(track_rect, 4, 4)

        # Clip painting of segments to the timeline track width
        p.save()
        p.setClipRect(QRectF(8, bar_top - 2, w, bar_h + 4))

        win_start_ts = self._day_start_ts + int(self._view_start_sec)
        win_end_ts = self._day_start_ts + int(self._view_start_sec + self._view_span_sec) + 1
        show_seg_borders = self._view_span_sec <= 14400.0  # <= 4h zoom shows individual clip borders

        # Draw segments (normal first, then anomalies on top so anomalies are always prominent)
        if self._day_start_ts > 0 and self._segments:
            for pass_anomaly in (False, True):
                for seg in self._segments:
                    if seg.is_anomaly != pass_anomaly:
                        continue
                    if seg.end_ts < win_start_ts or seg.start_ts > win_end_ts:
                        continue
                    x1 = max(left_x, self._ts_to_x(seg.start_ts))
                    x2 = min(right_x, self._ts_to_x(seg.end_ts))
                    seg_w = max(3.0 if seg.is_anomaly else 1.5, x2 - x1)
                    if seg.raw_type == 3:
                        col = QColor(239, 68, 68, 235)  # Red for Anomaly / Alarm
                        y_off, h_adj = bar_top + 1, bar_h - 2
                    elif seg.raw_type == 2:
                        col = QColor(245, 158, 11, 225)  # Amber for Motion
                        y_off, h_adj = bar_top + 2, bar_h - 4
                    else:
                        col = QColor(59, 130, 246, 185)  # Blue for Continuous
                        y_off, h_adj = bar_top + 3, bar_h - 6

                    if show_seg_borders and seg_w >= 5.0:
                        p.setPen(QPen(QColor("#0d0d11"), 1.0))
                    else:
                        p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(col)
                    p.drawRoundedRect(QRectF(x1, y_off, seg_w, h_adj), 1.5, 1.5)

                    # Draw emerald local-backup indicator strip along the bottom edge of backed-up segments
                    if seg.seg_id in self._backed_up_ids:
                        p.setPen(Qt.PenStyle.NoPen)
                        p.setBrush(QColor("#10b981"))
                        p.drawRect(QRectF(x1, bar_top + bar_h - 2.5, max(2.5, seg_w), 2.5))
        p.restore()

        # Adaptive ticks & 12-hour AM/PM labels
        major_step, minor_step = self._tick_intervals()
        show_minutes = major_step < 3600 or self._view_span_sec < 43200
        font = QFont("Cascadia Mono", 8)
        p.setFont(font)

        first_tick = int(self._view_start_sec // minor_step) * minor_step
        last_tick = int(min(86400.0, self._view_start_sec + self._view_span_sec)) + minor_step
        last_label_right = -999.0

        for t_sec in range(first_tick, last_tick + 1, minor_step):
            if t_sec < 0 or t_sec > 86400:
                continue
            rel = float(t_sec) - self._view_start_sec
            if rel < -0.5 or rel > self._view_span_sec + 0.5:
                continue
            hx = 8.0 + (rel / max(1.0, self._view_span_sec)) * float(w)
            is_major = (t_sec % major_step == 0)
            p.setPen(QPen(QColor("#3f3f46" if is_major else "#27272a"), 1))
            p.drawLine(int(hx), bar_top + bar_h, int(hx), bar_top + bar_h + (5 if is_major else 3))

            if is_major and t_sec < 86400:
                lbl = self._format_rel_tick_12h(t_sec, show_minutes)
                lbl_w = max(44.0, float(len(lbl) * 6 + 8))
                if hx < 8.0 + lbl_w * 0.45:
                    lx = 8.0
                    align = Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                elif hx > right_x - lbl_w * 0.45:
                    lx = right_x - lbl_w
                    align = Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
                else:
                    lx = hx - lbl_w * 0.5
                    align = Qt.AlignmentFlag.AlignCenter
                if lx >= last_label_right + 6.0:
                    p.setPen(QColor("#a1a1aa" if self._view_span_sec < 86400 else "#71717a"))
                    p.drawText(QRectF(lx, bar_top + bar_h + 4, lbl_w, 14), align, lbl)
                    last_label_right = lx + lbl_w

        # Mini 24-hour overview scrollbar at the very bottom when zoomed in
        if self._view_span_sec < self.MAX_SPAN_SEC:
            ov_y = rect.height() - 5
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor("#18181f"))
            p.drawRoundedRect(QRectF(8.0, ov_y, w, 4.0), 2.0, 2.0)
            thumb_x = 8.0 + (self._view_start_sec / self.MAX_SPAN_SEC) * float(w)
            thumb_w = max(14.0, (self._view_span_sec / self.MAX_SPAN_SEC) * float(w))
            thumb_x = min(8.0 + float(w) - thumb_w, thumb_x)
            p.setBrush(QColor("#38bdf8"))
            p.drawRoundedRect(QRectF(thumb_x, ov_y, thumb_w, 4.0), 2.0, 2.0)

        # Playhead cursor
        if self._day_start_ts > 0 and self._playhead_ts >= self._day_start_ts:
            px = self._ts_to_x(self._playhead_ts)
            if left_x <= px <= right_x:
                p.setPen(QPen(QColor("#ffffff"), 2.0))
                p.drawLine(int(px), bar_top - 3, int(px), bar_top + bar_h + 3)
                p.setBrush(QColor("#ef4444"))
                p.setPen(QPen(QColor("#ffffff"), 1))
                p.drawEllipse(QPoint(int(px), bar_top - 2), 4, 4)

        # Hover time & clip tooltip
        if self._hover_ts is not None and self._day_start_ts > 0:
            hx = self._ts_to_x(self._hover_ts)
            if left_x <= hx <= right_x:
                p.setPen(QPen(QColor(255, 255, 255, 130), 1, Qt.PenStyle.DashLine))
                p.drawLine(int(hx), bar_top, int(hx), bar_top + bar_h)

                t_str = datetime.fromtimestamp(self._hover_ts, timezone.utc).strftime("%I:%M:%S %p").lstrip("0")
                hover_seg = None
                for s in self._segments:
                    if s.start_ts <= self._hover_ts <= s.end_ts:
                        hover_seg = s
                        if s.is_anomaly:
                            break
                if hover_seg is not None:
                    t_str = f"{t_str} • {hover_seg.kind_label} ({hover_seg.start_time_str}–{hover_seg.end_time_str})"
                tw = max(78, len(t_str) * 6 + 14)
                tx = max(6.0, min(float(rect.width() - tw - 6), hx - tw / 2.0))
                tip_rect = QRectF(tx, 1, tw, 16)
                p.setPen(QPen(QColor("#3f3f46"), 1))
                p.setBrush(QColor(15, 15, 20, 240))
                p.drawRoundedRect(tip_rect, 3, 3)
                p.setPen(QColor("#f4f4f5"))
                p.drawText(tip_rect, Qt.AlignmentFlag.AlignCenter, t_str)

        p.end()


class BackupRangeDialog(QDialog):
    """
    Date & Time Range Finder / Downloader dialog for backing up SD card footage to the PC.
    Automatically organizes backed-up clips into `captures/recordings/backups/<Camera>/<YYYY-MM-DD>/`.
    """

    def __init__(
        self,
        cameras: List[CameraConfig],
        active_cam_id: Optional[str],
        initial_date: QDate,
        initial_filter: str = "all",
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Backup SD Card Footage — Date & Time Range Finder")
        self.setMinimumWidth(470)
        self.cameras = list(cameras)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(18, 16, 18, 16)
        vbox.setSpacing(12)

        title = QLabel("Backup SD Card Footage to Local PC")
        title.setStyleSheet("color: #f4f4f5; font-size: 14px; font-weight: 700;")
        vbox.addWidget(title)

        sub = QLabel(
            "Select a date and time range below. Backed-up clips are saved locally and automatically "
            "used for instant offline playback when reviewing footage."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        vbox.addWidget(sub)

        picker_style = (
            "QDateEdit, QTimeEdit, QComboBox { background-color: #141418; border: 1px solid #2e2e36; "
            "border-radius: 5px; color: #f4f4f5; padding: 4px 8px; "
            "font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; }"
        )

        form = QFormLayout()
        form.setSpacing(10)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)

        # Camera Selector
        self.cmb_cam = QComboBox()
        self.cmb_cam.setFixedHeight(30)
        self.cmb_cam.setStyleSheet(picker_style)
        sel_idx = 0
        for idx, c in enumerate(self.cameras):
            self.cmb_cam.addItem(f"{c.name}  ({c.ip or c.protocol.upper()})", c.cam_id)
            if active_cam_id and c.cam_id == active_cam_id:
                sel_idx = idx
        if self.cameras:
            self.cmb_cam.setCurrentIndex(sel_idx)
        form.addRow("Camera:", self.cmb_cam)

        # Start Date & Time (12-hr AM/PM + full month name)
        start_row = QHBoxLayout()
        start_row.setSpacing(6)
        self.start_date = QDateEdit(initial_date)
        self.start_date.setCalendarPopup(True)
        self.start_date.setDisplayFormat("MMMM d, yyyy")
        self.start_date.setFixedHeight(30)
        self.start_date.setStyleSheet(picker_style)
        self.start_time = QTimeEdit(QTime(0, 0, 0))
        self.start_time.setDisplayFormat("hh:mm:ss AP")
        self.start_time.setFixedHeight(30)
        self.start_time.setStyleSheet(picker_style)
        start_row.addWidget(self.start_date, 3)
        start_row.addWidget(self.start_time, 2)
        form.addRow("From Date & Time:", start_row)

        # End Date & Time (12-hr AM/PM + full month name)
        end_row = QHBoxLayout()
        end_row.setSpacing(6)
        self.end_date = QDateEdit(initial_date)
        self.end_date.setCalendarPopup(True)
        self.end_date.setDisplayFormat("MMMM d, yyyy")
        self.end_date.setFixedHeight(30)
        self.end_date.setStyleSheet(picker_style)
        self.end_time = QTimeEdit(QTime(23, 59, 59))
        self.end_time.setDisplayFormat("hh:mm:ss AP")
        self.end_time.setFixedHeight(30)
        self.end_time.setStyleSheet(picker_style)
        end_row.addWidget(self.end_date, 3)
        end_row.addWidget(self.end_time, 2)
        form.addRow("To Date & Time:", end_row)

        # Quick Time Range Presets
        preset_row = QHBoxLayout()
        preset_row.setSpacing(4)
        for lbl, t1, t2 in (
            ("Full Day", QTime(0, 0, 0), QTime(23, 59, 59)),
            ("Morning (6A–12P)", QTime(6, 0, 0), QTime(11, 59, 59)),
            ("Afternoon (12P–6P)", QTime(12, 0, 0), QTime(17, 59, 59)),
            ("Night (6P–12A)", QTime(18, 0, 0), QTime(23, 59, 59)),
        ):
            b = QPushButton(lbl)
            b.setFixedHeight(24)
            b.setStyleSheet("font-size: 10px; padding: 2px 6px;")
            b.clicked.connect(lambda _, a=t1, z=t2: self._apply_time_preset(a, z))
            preset_row.addWidget(b)
        form.addRow("Quick Time:", preset_row)

        # Clip Type Filter
        self.cmb_filter = QComboBox()
        self.cmb_filter.setFixedHeight(30)
        self.cmb_filter.setStyleSheet(picker_style)
        self.cmb_filter.addItem("All Clips (Anomalies + Normal)", "all")
        self.cmb_filter.addItem("Anomalies & Motion Clips Only", "anomaly")
        self.cmb_filter.addItem("Normal / Continuous Clips Only", "normal")
        if initial_filter == "anomaly":
            self.cmb_filter.setCurrentIndex(1)
        elif initial_filter == "normal":
            self.cmb_filter.setCurrentIndex(2)
        form.addRow("Clip Types:", self.cmb_filter)

        vbox.addLayout(form)

        # Skip Existing Checkbox
        self.chk_skip = QCheckBox("Skip clips that already have a local backup on disk")
        self.chk_skip.setChecked(True)
        self.chk_skip.setStyleSheet("color: #d4d4d8; font-size: 11px;")
        vbox.addWidget(self.chk_skip)

        # Destination Info Box
        dest_lbl = QLabel(f"Backup Folder:  {BACKUP_DIR}\\<Camera>\\<YYYY-MM-DD>\\")
        dest_lbl.setWordWrap(True)
        dest_lbl.setStyleSheet(
            "background-color: #121217; color: #10b981; border: 1px solid #23232c; "
            "border-radius: 5px; padding: 7px 9px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px;"
        )
        vbox.addWidget(dest_lbl)

        # Buttons Row
        btn_row = QHBoxLayout()
        btn_cancel = QPushButton("Cancel")
        btn_cancel.setFixedHeight(32)
        btn_cancel.clicked.connect(self.reject)

        btn_start = QPushButton("Start Backup Download")
        btn_start.setObjectName("primaryBtn")
        btn_start.setIcon(lucide_icon("download", "#ffffff", 14))
        btn_start.setFixedHeight(32)
        btn_start.clicked.connect(self._on_accept)

        btn_row.addStretch()
        btn_row.addWidget(btn_cancel)
        btn_row.addWidget(btn_start)
        vbox.addLayout(btn_row)

    def _apply_time_preset(self, t1: QTime, t2: QTime) -> None:
        self.end_date.setDate(self.start_date.date())
        self.start_time.setTime(t1)
        self.end_time.setTime(t2)

    def _on_accept(self) -> None:
        s_ts, e_ts = self.selected_timestamps()
        if e_ts <= s_ts:
            QMessageBox.warning(
                self,
                "Invalid Date/Time Range",
                "The 'To Date & Time' must be later than 'From Date & Time'.",
            )
            return
        self.accept()

    def selected_camera(self) -> Optional[CameraConfig]:
        cid = self.cmb_cam.currentData()
        for c in self.cameras:
            if c.cam_id == cid:
                return c
        return self.cameras[0] if self.cameras else None

    def selected_timestamps(self) -> Tuple[int, int]:
        sd = self.start_date.date()
        st = self.start_time.time()
        ed = self.end_date.date()
        et = self.end_time.time()
        dt_start = datetime(
            sd.year(), sd.month(), sd.day(), st.hour(), st.minute(), st.second(), tzinfo=timezone.utc
        )
        dt_end = datetime(
            ed.year(), ed.month(), ed.day(), et.hour(), et.minute(), et.second(), tzinfo=timezone.utc
        )
        return int(dt_start.timestamp()), int(dt_end.timestamp())

    def selected_filter(self) -> str:
        return str(self.cmb_filter.currentData() or "all")

    def skip_existing(self) -> bool:
        return bool(self.chk_skip.isChecked())


class _ClipListWidget(QListWidget):
    """
    QListWidget that only emits `itemClicked` on Left-Click, so Right-Clicking a clip to open
    the context menu ("Backup This Clip to PC") never triggers an unintended SD card playback stream.
    """

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            it = self.itemAt(event.pos())
            if it is not None:
                self.blockSignals(True)
                self.setCurrentItem(it)
                self.blockSignals(False)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.RightButton:
            event.accept()
            return
        super().mouseReleaseEvent(event)


class FootageReviewWorkspace(QWidget):
    """
    Dedicated Footage Review Workspace for browsing, filtering, playing back, and backing up:
    - On-Camera microSD Card recordings (auto-recorded Anomalies/Alarms, Motion, and Continuous clips)
    - Local PC Backups (`captures/recordings/backups/<Camera>/<YYYY-MM-DD>/*.mp4`) — automatically played
      instead of reading from the SD card whenever a clip has a local backup!
    - Local PC Captures (`captures/recordings/*.mp4` and `captures/snapshots/*.jpg`)
    """

    def __init__(self, main_win: "OmniSightMainWindow", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.main_win = main_win
        self.cameras: List[CameraConfig] = []
        self.active_cam_id: Optional[str] = None
        self.source_mode: str = "sdcard"  # "sdcard" | "local"
        self.event_filter: str = "all"    # "all" | "anomaly" | "normal"
        self.sort_newest_first: bool = True  # True = New -> Old, False = Old -> New

        self.all_segments: List[RecordingSegment] = []
        self.filtered_segments: List[RecordingSegment] = []
        self.local_files: List[Path] = []
        self.current_segment: Optional[RecordingSegment] = None
        self.current_local_file: Optional[Path] = None

        self.list_worker: Optional[SDCardListWorker] = None
        self.play_worker: Optional[SDCardPlaybackWorker] = None
        self.export_worker: Optional[SDCardExportWorker] = None
        self.bulk_worker: Optional[SDCardBulkBackupWorker] = None
        self._retiring_workers: List[QThread] = []
        self._pending_sd_play: Optional[Tuple[CameraConfig, RecordingSegment, int]] = None
        self._sd_play_timer = QTimer(self)
        self._sd_play_timer.setSingleShot(True)
        self._sd_play_timer.timeout.connect(self._start_pending_sd_play)
        self._single_backup_seg_id: Optional[int] = None
        self.audio_out = LiveAudioOutput(sample_rate=8000, parent=self)
        self.playback_speed: float = 1.0
        self.current_position_ts: int = 0

        self._build_ui()

    def _retire_thread(self, w: Optional[QThread]) -> None:
        """
        Safely stop and retire a QThread without ever calling deleteLater() or dropping
        its Python reference while isRunning() is True (which triggers a fatal C++ abort in Qt6).
        """
        if w is None:
            return
        if hasattr(w, "request_stop"):
            try:
                w.request_stop()
            except Exception:
                pass
        if w.isRunning():
            if w not in self._retiring_workers:
                self._retiring_workers.append(w)

            def _cleanup_finished(worker: QThread = w) -> None:
                if worker in self._retiring_workers:
                    self._retiring_workers.remove(worker)
                worker.deleteLater()

            w.finished.connect(_cleanup_finished)
        else:
            w.deleteLater()

    def _build_ui(self) -> None:
        root_hbox = QHBoxLayout(self)
        root_hbox.setContentsMargins(0, 0, 0, 0)
        root_hbox.setSpacing(0)

        # --- Left Sidebar: Camera / Source / Date / Event Filter / Clip List (368px) ---
        self.sidebar = QFrame()
        self.sidebar.setObjectName("inspectorDrawer")
        self.sidebar.setFixedWidth(368)
        sb_vbox = QVBoxLayout(self.sidebar)
        sb_vbox.setContentsMargins(12, 12, 12, 12)
        sb_vbox.setSpacing(10)

        # 1. Camera & Source Selector Section
        src_sec = QFrame()
        src_sec.setObjectName("drawerSection")
        src_vbox = QVBoxLayout(src_sec)
        src_vbox.setContentsMargins(10, 10, 10, 10)
        src_vbox.setSpacing(8)

        hdr_cam = QLabel("CAMERA & FOOTAGE SOURCE")
        hdr_cam.setObjectName("sectionTitle")
        src_vbox.addWidget(hdr_cam)

        self.cmb_camera = QComboBox()
        self.cmb_camera.setFixedHeight(32)
        self.cmb_camera.currentIndexChanged.connect(self._on_camera_combo_changed)
        src_vbox.addWidget(self.cmb_camera)

        src_row = QHBoxLayout()
        src_row.setSpacing(6)
        self.btn_src_sd = QPushButton("SD Card Footage")
        self.btn_src_sd.setObjectName("optBtn")
        self.btn_src_sd.setCheckable(True)
        self.btn_src_sd.setChecked(True)
        self.btn_src_sd.setFixedHeight(32)
        self.btn_src_sd.setIcon(lucide_icon("hard_drive", "#38bdf8", 14))
        self.btn_src_sd.setIconSize(QSize(14, 14))
        self.btn_src_sd.clicked.connect(lambda: self._set_source_mode("sdcard"))

        self.btn_src_local = QPushButton("Local PC Files")
        self.btn_src_local.setObjectName("optBtn")
        self.btn_src_local.setCheckable(True)
        self.btn_src_local.setFixedHeight(32)
        self.btn_src_local.setIcon(lucide_icon("folder", "#a1a1aa", 14))
        self.btn_src_local.setIconSize(QSize(14, 14))
        self.btn_src_local.clicked.connect(lambda: self._set_source_mode("local"))

        src_row.addWidget(self.btn_src_sd, 1)
        src_row.addWidget(self.btn_src_local, 1)
        src_vbox.addLayout(src_row)
        sb_vbox.addWidget(src_sec)

        # 2. Date Navigator, Event Filter & Date/Time Range Backup Section
        flt_sec = QFrame()
        flt_sec.setObjectName("drawerSection")
        flt_vbox = QVBoxLayout(flt_sec)
        flt_vbox.setContentsMargins(10, 10, 10, 10)
        flt_vbox.setSpacing(8)

        date_hdr_row = QHBoxLayout()
        date_hdr_row.setSpacing(6)
        hdr_date = QLabel("RECORDING DATE & FILTER")
        hdr_date.setObjectName("sectionTitle")
        date_hdr_row.addWidget(hdr_date)
        date_hdr_row.addStretch()

        self.btn_today = QPushButton("Today")
        self.btn_today.setFixedHeight(24)
        self.btn_today.setStyleSheet(
            "QPushButton { background-color: #18181f; color: #e4e4e7; border: 1px solid #2b2b36; "
            "border-radius: 4px; font-size: 11px; font-weight: 600; padding: 2px 10px; }"
            "QPushButton:hover { background-color: #24242f; border-color: #3b82f6; color: #ffffff; }"
        )
        self.btn_today.setToolTip("Jump to Today")
        self.btn_today.clicked.connect(self._jump_today)
        date_hdr_row.addWidget(self.btn_today)

        self.btn_refresh_sd = QPushButton("Refresh")
        self.btn_refresh_sd.setIcon(lucide_icon("refresh", "#38bdf8", 12))
        self.btn_refresh_sd.setIconSize(QSize(12, 12))
        self.btn_refresh_sd.setFixedHeight(24)
        self.btn_refresh_sd.setStyleSheet(
            "QPushButton { background-color: #18181f; color: #e4e4e7; border: 1px solid #2b2b36; "
            "border-radius: 4px; font-size: 11px; font-weight: 600; padding: 2px 10px; }"
            "QPushButton:hover { background-color: #24242f; border-color: #38bdf8; color: #ffffff; }"
        )
        self.btn_refresh_sd.setToolTip("Refresh Recordings List")
        self.btn_refresh_sd.clicked.connect(self.refresh_recordings)
        date_hdr_row.addWidget(self.btn_refresh_sd)
        flt_vbox.addLayout(date_hdr_row)

        date_row = QHBoxLayout()
        date_row.setSpacing(6)
        self.btn_prev_day = QPushButton()
        self.btn_prev_day.setIcon(lucide_icon("chevron_left", "#e4e4e7", 15))
        self.btn_prev_day.setIconSize(QSize(15, 15))
        self.btn_prev_day.setFixedSize(32, 30)
        self.btn_prev_day.setStyleSheet("padding: 0px;")
        self.btn_prev_day.setToolTip("Previous Day")
        self.btn_prev_day.clicked.connect(lambda: self._shift_day(-1))
        date_row.addWidget(self.btn_prev_day)

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("MMM d, yyyy")
        self.date_edit.setFixedHeight(30)
        self.date_edit.setStyleSheet(
            "QDateEdit { background-color: #141418; border: 1px solid #2e2e36; border-radius: 5px; "
            "color: #f4f4f5; padding: 2px 10px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; font-weight: 600; }"
        )
        self.date_edit.dateChanged.connect(lambda _: self.refresh_recordings())
        date_row.addWidget(self.date_edit, 1)

        self.btn_next_day = QPushButton()
        self.btn_next_day.setIcon(lucide_icon("chevron_right", "#e4e4e7", 15))
        self.btn_next_day.setIconSize(QSize(15, 15))
        self.btn_next_day.setFixedSize(32, 30)
        self.btn_next_day.setStyleSheet("padding: 0px;")
        self.btn_next_day.setToolTip("Next Day")
        self.btn_next_day.clicked.connect(lambda: self._shift_day(1))
        date_row.addWidget(self.btn_next_day)
        flt_vbox.addLayout(date_row)

        # Event Type Filter Pills
        ev_row = QHBoxLayout()
        ev_row.setSpacing(6)
        self.btn_flt_all = QPushButton("All (0)")
        self.btn_flt_all.setObjectName("optBtn")
        self.btn_flt_all.setCheckable(True)
        self.btn_flt_all.setChecked(True)
        self.btn_flt_all.setFixedHeight(30)
        self.btn_flt_all.clicked.connect(lambda: self._set_event_filter("all"))

        self.btn_flt_anom = QPushButton("Anomalies (0)")
        self.btn_flt_anom.setObjectName("optBtn")
        self.btn_flt_anom.setCheckable(True)
        self.btn_flt_anom.setFixedHeight(30)
        self.btn_flt_anom.setIcon(lucide_icon("alert_triangle", "#ef4444", 13))
        self.btn_flt_anom.setIconSize(QSize(13, 13))
        self.btn_flt_anom.setToolTip("Show Only Anomaly / Alarm & Motion Triggered Clips")
        self.btn_flt_anom.clicked.connect(lambda: self._set_event_filter("anomaly"))

        self.btn_flt_norm = QPushButton("Normal (0)")
        self.btn_flt_norm.setObjectName("optBtn")
        self.btn_flt_norm.setCheckable(True)
        self.btn_flt_norm.setFixedHeight(30)
        self.btn_flt_norm.clicked.connect(lambda: self._set_event_filter("normal"))

        ev_row.addWidget(self.btn_flt_all, 1)
        ev_row.addWidget(self.btn_flt_anom, 1)
        ev_row.addWidget(self.btn_flt_norm, 1)
        flt_vbox.addLayout(ev_row)

        # Date & Time Range Backup Button
        self.btn_backup_range = QPushButton("Backup Footage (Date / Time Range)...")
        self.btn_backup_range.setIcon(lucide_icon("download", "#10b981", 14))
        self.btn_backup_range.setIconSize(QSize(14, 14))
        self.btn_backup_range.setFixedHeight(32)
        self.btn_backup_range.setToolTip(
            "Download & back up SD card clips for a chosen date and time range so they play directly from your PC"
        )
        self.btn_backup_range.setStyleSheet(
            "QPushButton { background-color: #064e3b; color: #ecfdf5; border: 1px solid #10b981; "
            "border-radius: 5px; font-size: 12px; font-weight: 600; padding: 4px 10px; } "
            "QPushButton:hover { background-color: #047857; } "
            "QPushButton:disabled { background-color: #18181b; color: #52525b; border-color: #27272a; }"
        )
        self.btn_backup_range.clicked.connect(self.open_backup_range_dialog)
        flt_vbox.addWidget(self.btn_backup_range)

        # Live Bulk Backup Progress Box (hidden when idle)
        self.backup_box = QFrame()
        self.backup_box.setStyleSheet(
            "background-color: #0c1915; border: 1px solid #10b981; border-radius: 5px;"
        )
        bk_vbox = QVBoxLayout(self.backup_box)
        bk_vbox.setContentsMargins(8, 6, 8, 6)
        bk_vbox.setSpacing(4)
        self.lbl_backup_status = QLabel("Backing up footage...")
        self.lbl_backup_status.setWordWrap(True)
        self.lbl_backup_status.setStyleSheet(
            "color: #a7f3d0; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; border: none;"
        )
        bk_vbox.addWidget(self.lbl_backup_status)
        bk_bar_row = QHBoxLayout()
        bk_bar_row.setSpacing(6)
        self.backup_progress_bar = QProgressBar()
        self.backup_progress_bar.setRange(0, 100)
        self.backup_progress_bar.setValue(0)
        self.backup_progress_bar.setFixedHeight(14)
        bk_bar_row.addWidget(self.backup_progress_bar, 1)
        self.btn_cancel_backup = QPushButton("Stop")
        self.btn_cancel_backup.setFixedSize(46, 20)
        self.btn_cancel_backup.setStyleSheet("font-size: 10px; padding: 1px 4px;")
        self.btn_cancel_backup.clicked.connect(self._cancel_bulk_backup)
        bk_bar_row.addWidget(self.btn_cancel_backup)
        bk_vbox.addLayout(bk_bar_row)
        self.backup_box.setVisible(False)
        flt_vbox.addWidget(self.backup_box)

        sb_vbox.addWidget(flt_sec)

        # 3. Clips List Section
        list_sec = QFrame()
        list_sec.setObjectName("drawerSection")
        list_vbox = QVBoxLayout(list_sec)
        list_vbox.setContentsMargins(10, 10, 10, 10)
        list_vbox.setSpacing(6)

        list_hdr_row = QHBoxLayout()
        list_hdr_row.setSpacing(6)
        self.lbl_clips_header = QLabel("SD CARD RECORDINGS")
        self.lbl_clips_header.setObjectName("sectionTitle")
        list_hdr_row.addWidget(self.lbl_clips_header)
        list_hdr_row.addStretch()

        self.btn_sort_order = QPushButton("↓ New → Old")
        self.btn_sort_order.setObjectName("segBtn")
        self.btn_sort_order.setFixedHeight(24)
        self.btn_sort_order.setToolTip("Switch footage list sort order between Newest First (New → Old) and Oldest First (Old → New)")
        self.btn_sort_order.setStyleSheet(
            "QPushButton { background-color: #16161d; color: #d4d4d8; border: 1px solid #2b2b36; "
            "border-radius: 4px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 600; padding: 2px 8px; }"
            "QPushButton:hover { background-color: #1f1f29; color: #ffffff; border-color: #3b82f6; }"
        )
        self.btn_sort_order.clicked.connect(self._toggle_sort_order)
        list_hdr_row.addWidget(self.btn_sort_order)
        list_vbox.addLayout(list_hdr_row)

        self.lbl_list_status = QLabel("Idle")
        self.lbl_list_status.setStyleSheet(
            "color: #a1a1aa; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; padding-bottom: 2px;"
        )
        list_vbox.addWidget(self.lbl_list_status)

        self.clip_list = _ClipListWidget()
        self.clip_list.setIconSize(QSize(26, 26))
        self.clip_list.setSpacing(2)
        self.clip_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.clip_list.customContextMenuRequested.connect(self._show_clip_context_menu)
        self.clip_list.setStyleSheet(
            "QListWidget { background-color: #0e0e12; border: 1px solid #23232b; border-radius: 5px; outline: none; qproperty-iconSize: 26px 26px; }"
            "QListWidget::item { padding: 7px 10px; border-bottom: 1px solid #18181f; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; }"
            "QListWidget::item:selected { background-color: #1e3a8a; color: #ffffff; border-radius: 4px; }"
            "QListWidget::item:hover:!selected { background-color: #181820; }"
        )
        self.clip_list.itemClicked.connect(self._on_clip_item_clicked)
        list_vbox.addWidget(self.clip_list, 1)

        sb_vbox.addWidget(list_sec, 1)
        root_hbox.addWidget(self.sidebar)

        # --- Center Main Player Area: Video Viewport + 24h Timeline + Transport & Export Bar ---
        player_container = QWidget()
        player_container.setStyleSheet("background-color: #09090b;")
        p_vbox = QVBoxLayout(player_container)
        p_vbox.setContentsMargins(8, 8, 8, 8)
        p_vbox.setSpacing(6)

        # Top Info Bar inside Player
        top_info = QFrame()
        top_info.setFixedHeight(36)
        top_info.setStyleSheet(
            "background-color: #121216; border: 1px solid #22222a; border-radius: 5px;"
        )
        ti_hbox = QHBoxLayout(top_info)
        ti_hbox.setContentsMargins(12, 0, 12, 0)
        ti_hbox.setSpacing(8)

        self.lbl_player_title = QLabel("Select a recording clip or click the 24-hour timeline below")
        self.lbl_player_title.setMinimumWidth(40)
        self.lbl_player_title.setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: 600;")
        ti_hbox.addWidget(self.lbl_player_title, 1)

        self.lbl_event_badge = QLabel("ANOMALY")
        self.lbl_event_badge.setStyleSheet(
            "background-color: #7f1d1d; color: #ffffff; border: 1px solid #ef4444; "
            "border-radius: 3px; padding: 2px 8px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700;"
        )
        self.lbl_event_badge.setVisible(False)
        ti_hbox.addWidget(self.lbl_event_badge)

        self.lbl_backup_badge = QLabel("✓ LOCAL BACKUP")
        self.lbl_backup_badge.setToolTip("Playing directly from local PC backup instead of reading from camera SD card")
        self.lbl_backup_badge.setStyleSheet(
            "background-color: #064e3b; color: #6ee7b7; border: 1px solid #10b981; "
            "border-radius: 3px; padding: 2px 8px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700;"
        )
        self.lbl_backup_badge.setVisible(False)
        ti_hbox.addWidget(self.lbl_backup_badge)

        ti_hbox.addStretch()

        self.lbl_player_fps = QLabel("0.0 FPS")
        self.lbl_player_fps.setStyleSheet(
            "color: #71717a; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px;"
        )
        ti_hbox.addWidget(self.lbl_player_fps)

        btn_reset_zoom = QPushButton("Reset Zoom")
        btn_reset_zoom.setFixedHeight(26)
        btn_reset_zoom.setStyleSheet("padding: 2px 10px; font-size: 11px;")
        btn_reset_zoom.setToolTip("Reset Digital Zoom (or Double-Click Video)")
        btn_reset_zoom.clicked.connect(lambda: self.canvas.reset_zoom())
        ti_hbox.addWidget(btn_reset_zoom)

        p_vbox.addWidget(top_info)

        # Video Viewport
        self.canvas = VideoCanvas(self)
        self.canvas.set_camera_info("Footage Review", "SD Card & Local Captures")
        self.canvas.set_status("Select a camera clip on the left or click the 24h timeline", False)
        self.canvas.double_clicked.connect(lambda: self.canvas.reset_zoom())
        p_vbox.addWidget(self.canvas, 1)

        # 24-Hour Interactive Event Timeline Strip
        timeline_frame = QFrame()
        timeline_frame.setStyleSheet(
            "background-color: #121216; border: 1px solid #22222a; border-radius: 5px;"
        )
        tl_vbox = QVBoxLayout(timeline_frame)
        tl_vbox.setContentsMargins(10, 6, 10, 6)
        tl_vbox.setSpacing(6)

        tl_legend = QHBoxLayout()
        tl_legend.setSpacing(5)
        lbl_tl = QLabel("SD TIMELINE")
        lbl_tl.setObjectName("sectionTitle")
        lbl_tl.setToolTip(
            "Left-click/drag to scrub • Mouse wheel to zoom at cursor • Right-drag or bottom bar to pan"
        )
        tl_legend.addWidget(lbl_tl)

        tl_legend.addSpacing(6)

        tl_nav_btn_style = (
            "QPushButton { background-color: #16161d; color: #e4e4e7; border: 1px solid #2b2b36; "
            "border-radius: 4px; padding: 0px; font-size: 12px; font-weight: 700; }"
            "QPushButton:hover { background-color: #24242f; border-color: #3b82f6; color: #ffffff; }"
            "QPushButton:disabled { background-color: #111116; color: #52525b; border-color: #1f1f27; }"
        )
        tl_span_btn_style = (
            "QPushButton { background-color: #16161d; color: #d4d4d8; border: 1px solid #272732; "
            "border-radius: 4px; padding: 0px 6px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 600; }"
            "QPushButton:hover { background-color: #22222c; color: #ffffff; border-color: #3f3f4e; }"
            "QPushButton:checked { background-color: #1e3a8a; color: #ffffff; border-color: #3b82f6; }"
        )

        self.btn_tl_pan_left = QPushButton("◀")
        self.btn_tl_pan_left.setFixedSize(28, 24)
        self.btn_tl_pan_left.setStyleSheet(tl_nav_btn_style)
        self.btn_tl_pan_left.setToolTip("Pan Timeline Earlier (or Right-Click Drag on Timeline)")
        self.btn_tl_pan_left.clicked.connect(lambda: self.timeline.pan_by_fraction(-0.35))
        tl_legend.addWidget(self.btn_tl_pan_left)

        self.btn_tl_zoom_out = QPushButton("−")
        self.btn_tl_zoom_out.setFixedSize(28, 24)
        self.btn_tl_zoom_out.setStyleSheet(tl_nav_btn_style)
        self.btn_tl_zoom_out.setToolTip("Zoom Out Timeline (Mouse Wheel Down)")
        self.btn_tl_zoom_out.clicked.connect(lambda: self.timeline.zoom_out())
        tl_legend.addWidget(self.btn_tl_zoom_out)

        self.tl_zoom_btns: Dict[int, QPushButton] = {}
        for span_sec, lbl_z in (
            (86400, "24h"),
            (21600, "6h"),
            (7200, "2h"),
            (3600, "1h"),
            (900, "15m"),
            (300, "5m"),
        ):
            bz = QPushButton(lbl_z)
            bz.setCheckable(True)
            bz.setChecked(span_sec == 86400)
            bz.setFixedSize(40, 24)
            bz.setStyleSheet(tl_span_btn_style)
            bz.setToolTip(f"Zoom timeline to {lbl_z} window (or use Mouse Wheel over timeline)")
            bz.clicked.connect(lambda checked, s=span_sec: self.timeline.set_view_span(float(s)))
            tl_legend.addWidget(bz)
            self.tl_zoom_btns[span_sec] = bz

        self.btn_tl_zoom_in = QPushButton("+")
        self.btn_tl_zoom_in.setFixedSize(28, 24)
        self.btn_tl_zoom_in.setStyleSheet(tl_nav_btn_style)
        self.btn_tl_zoom_in.setToolTip("Zoom In Timeline for Precise Time Selection (Mouse Wheel Up)")
        self.btn_tl_zoom_in.clicked.connect(lambda: self.timeline.zoom_in())
        tl_legend.addWidget(self.btn_tl_zoom_in)

        self.btn_tl_pan_right = QPushButton("▶")
        self.btn_tl_pan_right.setFixedSize(28, 24)
        self.btn_tl_pan_right.setStyleSheet(tl_nav_btn_style)
        self.btn_tl_pan_right.setToolTip("Pan Timeline Later (or Right-Click Drag on Timeline)")
        self.btn_tl_pan_right.clicked.connect(lambda: self.timeline.pan_by_fraction(0.35))
        tl_legend.addWidget(self.btn_tl_pan_right)

        self.lbl_tl_zoom_range = QLabel("Full Day • Wheel to Zoom")
        self.lbl_tl_zoom_range.setStyleSheet(
            "color: #38bdf8; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 600; padding-left: 6px;"
        )
        tl_legend.addWidget(self.lbl_tl_zoom_range)

        tl_legend.addStretch()

        self._tl_legend_dots: List[Tuple[QLabel, str, str]] = []
        for dot_col, full_txt, short_txt in (
            ("#ef4444", "Anomaly / Alarm", "Alarm"),
            ("#f59e0b", "Motion", "Motion"),
            ("#3b82f6", "Continuous", "Cont."),
            ("#10b981", "Local Backup", "Backup"),
        ):
            dot = QLabel(f"■ {full_txt}")
            dot.setStyleSheet(
                f"color: {dot_col}; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 600; padding-left: 4px;"
            )
            tl_legend.addWidget(dot)
            self._tl_legend_dots.append((dot, full_txt, short_txt))
        tl_vbox.addLayout(tl_legend)

        self.timeline = DayTimelineWidget(self)
        self.timeline.seek_requested.connect(self._on_timeline_seek)
        self.timeline.zoom_changed.connect(self._on_timeline_zoom_changed)
        tl_vbox.addWidget(self.timeline)
        self._on_timeline_zoom_changed(86400.0, 0, 0)
        p_vbox.addWidget(timeline_frame)

        # Transport & MP4 Export Control Bar
        ctrl_frame = QFrame()
        ctrl_frame.setFixedHeight(52)
        ctrl_frame.setStyleSheet(
            "background-color: #121216; border: 1px solid #22222a; border-radius: 5px;"
        )
        c_hbox = QHBoxLayout(ctrl_frame)
        c_hbox.setContentsMargins(12, 0, 12, 0)
        c_hbox.setSpacing(8)

        self.btn_back10 = QPushButton("-10s")
        self.btn_back10.setIcon(lucide_icon("skip_back", "#e4e4e7", 14))
        self.btn_back10.setIconSize(QSize(14, 14))
        self.btn_back10.setMinimumWidth(68)
        self.btn_back10.setFixedHeight(32)
        self.btn_back10.setStyleSheet("padding: 4px 10px; font-weight: 600;")
        self.btn_back10.setToolTip("Rewind 10 Seconds")
        self.btn_back10.clicked.connect(lambda: self.skip_seconds(-10))
        c_hbox.addWidget(self.btn_back10)

        self.btn_play_pause = QPushButton("Play")
        self.btn_play_pause.setObjectName("primaryBtn")
        self.btn_play_pause.setIcon(lucide_icon("play", "#ffffff", 14))
        self.btn_play_pause.setIconSize(QSize(14, 14))
        self.btn_play_pause.setMinimumWidth(88)
        self.btn_play_pause.setFixedHeight(32)
        self.btn_play_pause.setStyleSheet("padding: 4px 12px; font-weight: 600;")
        self.btn_play_pause.setToolTip("Play / Pause Playback")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        c_hbox.addWidget(self.btn_play_pause)

        self.btn_fwd10 = QPushButton("+10s")
        self.btn_fwd10.setIcon(lucide_icon("skip_forward", "#e4e4e7", 14))
        self.btn_fwd10.setIconSize(QSize(14, 14))
        self.btn_fwd10.setMinimumWidth(68)
        self.btn_fwd10.setFixedHeight(32)
        self.btn_fwd10.setStyleSheet("padding: 4px 10px; font-weight: 600;")
        self.btn_fwd10.setToolTip("Forward 10 Seconds")
        self.btn_fwd10.clicked.connect(lambda: self.skip_seconds(10))
        c_hbox.addWidget(self.btn_fwd10)

        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(lucide_icon("square", "#ef4444", 14))
        self.btn_stop.setIconSize(QSize(14, 14))
        self.btn_stop.setFixedSize(34, 32)
        self.btn_stop.setStyleSheet("padding: 0px;")
        self.btn_stop.setToolTip("Stop Playback")
        self.btn_stop.clicked.connect(self.stop_playback)
        c_hbox.addWidget(self.btn_stop)

        c_hbox.addSpacing(4)

        self.lbl_clock_pos = QLabel("--:--:--")
        self.lbl_clock_pos.setStyleSheet(
            "background-color: #09090b; color: #38bdf8; border: 1px solid #27272d; border-radius: 4px; "
            "padding: 5px 10px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 12px; font-weight: 700;"
        )
        c_hbox.addWidget(self.lbl_clock_pos)

        c_hbox.addSpacing(4)

        # Speed Selector Pills (1x always available; 2x, 4x, 8x, 16x unlocked only when footage is downloaded/local)
        speed_btn_style = (
            "QPushButton { background-color: #16161d; color: #e4e4e7; border: 1px solid #2b2b36; "
            "border-radius: 4px; padding: 0px 4px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; }"
            "QPushButton:hover { background-color: #24242f; color: #ffffff; border-color: #3f3f50; }"
            "QPushButton:checked { background-color: #1e3a8a; color: #ffffff; border-color: #3b82f6; }"
            "QPushButton:disabled { background-color: #121217; color: #71717a; border: 1px solid #22222b; }"
        )
        self.speed_btns: Dict[float, QPushButton] = {}
        for spd in (1.0, 2.0, 4.0, 8.0, 16.0):
            b = QPushButton(f"{int(spd)}x")
            b.setCheckable(True)
            b.setChecked(spd == 1.0)
            b.setFixedSize(42, 28)
            b.setStyleSheet(speed_btn_style)
            b.clicked.connect(lambda checked, s=spd: self.set_playback_speed(s))
            c_hbox.addWidget(b)
            self.speed_btns[spd] = b
        self._update_speed_controls_lock(unlocked=False)

        c_hbox.addStretch()

        # Audio Mute Toggle
        self.btn_mute = QPushButton()
        self.btn_mute.setCheckable(True)
        self.btn_mute.setChecked(True)
        self.btn_mute.setIcon(lucide_icon("volume_2", "#10b981", 15))
        self.btn_mute.setIconSize(QSize(15, 15))
        self.btn_mute.setFixedSize(34, 32)
        self.btn_mute.setStyleSheet("padding: 0px;")
        self.btn_mute.setToolTip("Toggle Recorded Audio Output (1x speed)")
        self.btn_mute.clicked.connect(self._on_mute_toggled)
        self.audio_out.set_muted(False)
        c_hbox.addWidget(self.btn_mute)

        self._review_audio_level: float = 0.0
        self.audio_bar = QProgressBar()
        self.audio_bar.setRange(0, 100)
        self.audio_bar.setValue(0)
        self.audio_bar.setTextVisible(False)
        self.audio_bar.setFixedSize(36, 4)
        self.audio_bar.setToolTip("Playback Audio Level")
        c_hbox.addWidget(self.audio_bar)

        self._review_audio_decay_timer = QTimer(self)
        self._review_audio_decay_timer.setInterval(55)
        self._review_audio_decay_timer.timeout.connect(self._decay_review_audio_meter)

        # Snapshot Button
        self.btn_snap = QPushButton("Snapshot")
        self.btn_snap.setIcon(lucide_icon("camera", "#e4e4e7", 14))
        self.btn_snap.setIconSize(QSize(14, 14))
        self.btn_snap.setFixedHeight(32)
        self.btn_snap.setStyleSheet("padding: 4px 12px; font-weight: 600;")
        self.btn_snap.setToolTip("Save Current Playback Frame as JPG Snapshot")
        self.btn_snap.clicked.connect(self.save_snapshot)
        c_hbox.addWidget(self.btn_snap)

        # Export Progress Bar (visible during SD Card MP4 export)
        self.export_bar = QProgressBar()
        self.export_bar.setRange(0, 100)
        self.export_bar.setValue(0)
        self.export_bar.setFixedSize(96, 18)
        self.export_bar.setVisible(False)
        c_hbox.addWidget(self.export_bar)

        # Backup / Export Menu Button
        self.btn_export = QPushButton("Backup / Export .MP4")
        self.btn_export.setObjectName("primaryBtn")
        self.btn_export.setIcon(lucide_icon("download", "#ffffff", 14))
        self.btn_export.setIconSize(QSize(14, 14))
        self.btn_export.setFixedHeight(32)
        self.btn_export.setStyleSheet("padding: 4px 14px; font-weight: 600;")
        self.btn_export.setToolTip("Back up current clip, back up a date/time range, or export a sub-clip as .MP4")
        self.btn_export.clicked.connect(self._show_export_menu)
        c_hbox.addWidget(self.btn_export)

        p_vbox.addWidget(ctrl_frame)
        root_hbox.addWidget(player_container, 1)

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        w = self.width()
        if hasattr(self, "sidebar"):
            if w < 960:
                self.sidebar.setFixedWidth(308)
            elif w < 1220:
                self.sidebar.setFixedWidth(336)
            else:
                self.sidebar.setFixedWidth(368)
        if hasattr(self, "_tl_legend_dots"):
            compact_tl = w < 1260
            hide_tl_dots = w < 980
            for dot, full_txt, short_txt in self._tl_legend_dots:
                dot.setVisible(not hide_tl_dots)
                dot.setText(f"■ {short_txt if compact_tl else full_txt}")
        if hasattr(self, "lbl_tl_zoom_range"):
            self.lbl_tl_zoom_range.setVisible(w >= 1060)
        if hasattr(self, "audio_bar"):
            self.audio_bar.setVisible(w >= 1100)
        if hasattr(self, "btn_snap"):
            self.btn_snap.setText("" if w < 1140 else "Snapshot")
        if hasattr(self, "btn_export") and self.btn_export.isEnabled():
            self.btn_export.setText("Export .MP4" if w < 1200 else "Backup / Export .MP4")

    # --- Camera & Source Management ---
    def set_cameras(self, cameras: List[CameraConfig], selected_cam_id: Optional[str] = None) -> None:
        self.cameras = list(cameras)
        prev_id = selected_cam_id or self.active_cam_id
        self.cmb_camera.blockSignals(True)
        self.cmb_camera.clear()
        target_idx = 0
        for idx, cfg in enumerate(self.cameras):
            self.cmb_camera.addItem(f"{cfg.name}  ({cfg.ip or cfg.protocol.upper()})", cfg.cam_id)
            if prev_id and cfg.cam_id == prev_id:
                target_idx = idx
        if self.cameras:
            self.cmb_camera.setCurrentIndex(target_idx)
            self.active_cam_id = self.cameras[target_idx].cam_id
        else:
            self.active_cam_id = None
        self.cmb_camera.blockSignals(False)

    def select_camera_and_load(self, cam_id: Optional[str] = None) -> None:
        if cam_id:
            for i in range(self.cmb_camera.count()):
                if self.cmb_camera.itemData(i) == cam_id:
                    self.cmb_camera.blockSignals(True)
                    self.cmb_camera.setCurrentIndex(i)
                    self.cmb_camera.blockSignals(False)
                    self.active_cam_id = cam_id
                    break
        self.refresh_recordings()

    def _active_config(self) -> Optional[CameraConfig]:
        for c in self.cameras:
            if c.cam_id == self.active_cam_id:
                return c
        return self.cameras[0] if self.cameras else None

    def _on_camera_combo_changed(self, idx: int) -> None:
        if 0 <= idx < self.cmb_camera.count():
            self.active_cam_id = self.cmb_camera.itemData(idx)
            self.stop_playback()
            self.refresh_recordings()

    def _set_source_mode(self, mode: str) -> None:
        self.source_mode = mode
        is_sd = mode == "sdcard"
        self.btn_src_sd.setChecked(is_sd)
        self.btn_src_local.setChecked(not is_sd)
        self.date_edit.setEnabled(is_sd)
        self.btn_prev_day.setEnabled(is_sd)
        self.btn_next_day.setEnabled(is_sd)
        self.btn_today.setEnabled(is_sd)
        self.btn_flt_all.setEnabled(is_sd)
        self.btn_flt_anom.setEnabled(is_sd)
        self.btn_flt_norm.setEnabled(is_sd)
        self.btn_backup_range.setEnabled(is_sd)
        self.lbl_clips_header.setText("SD CARD RECORDINGS" if is_sd else "LOCAL PC CAPTURES")
        self.stop_playback()
        self.refresh_recordings()

    def _shift_day(self, delta_days: int) -> None:
        new_date = self.date_edit.date().addDays(delta_days)
        if new_date <= QDate.currentDate():
            self.date_edit.setDate(new_date)

    def _jump_today(self) -> None:
        if self.date_edit.date() != QDate.currentDate():
            self.date_edit.setDate(QDate.currentDate())
        else:
            self.refresh_recordings()

    def _set_event_filter(self, flt: str) -> None:
        self.event_filter = flt
        self.btn_flt_all.setChecked(flt == "all")
        self.btn_flt_anom.setChecked(flt == "anomaly")
        self.btn_flt_norm.setChecked(flt == "normal")
        self._populate_sd_clip_list()

    def _day_start_utc_ts(self) -> int:
        qd = self.date_edit.date()
        dt = datetime(qd.year(), qd.month(), qd.day(), 0, 0, 0, tzinfo=timezone.utc)
        return int(dt.timestamp())

    # --- Listing SD Card & Local Recordings ---
    def refresh_recordings(self) -> None:
        if self.source_mode == "local":
            self._load_local_captures()
            return

        cfg = self._active_config()
        if cfg is None:
            self.lbl_list_status.setText("No camera")
            return

        if self.list_worker is not None:
            old_lw = self.list_worker
            self.list_worker = None
            try:
                old_lw.finished_list.disconnect()
            except Exception:
                pass
            self._retire_thread(old_lw)

        qd = self.date_edit.date()
        self.canvas.set_camera_info(cfg.name, f"SD Card • {qd.toString('MMMM d, yyyy')}")

        # Immediately load any local backups for this camera & date so they appear in the
        # On-Camera SD Card list right away on every app restart (even before the camera responds)
        local_bk_segs = load_local_backup_segments(BACKUP_DIR, cfg, qd.year(), qd.month(), qd.day())
        if local_bk_segs:
            self.all_segments = list(local_bk_segs)
            backed_ids = {s.seg_id for s in local_bk_segs}
            total = len(self.all_segments)
            anom = sum(1 for s in self.all_segments if s.is_anomaly)
            norm = total - anom
            self.btn_flt_all.setText(f"All ({total})")
            self.btn_flt_anom.setText(f"Anomalies ({anom})")
            self.btn_flt_norm.setText(f"Normal ({norm})")
            self.timeline.set_day_and_segments(self._day_start_utc_ts(), self.all_segments, backed_ids)
            self._populate_sd_clip_list()
            self.lbl_list_status.setText(f"{len(backed_ids)} backed up • Syncing SD...")
            if self.play_worker is None or not self.play_worker.isRunning():
                self.canvas.set_status(
                    f"Loaded {len(backed_ids)} local backup clips — Syncing microSD card on {cfg.name}...",
                    False,
                )
        else:
            self.lbl_list_status.setText("Reading SD...")
            self.clip_list.clear()
            self.canvas.set_status(f"Reading microSD card on {cfg.name}...", False)

        self.list_worker = SDCardListWorker(cfg, qd.year(), qd.month(), qd.day(), self)
        self.list_worker.finished_list.connect(self._on_sd_list_finished)
        self.list_worker.start()

    def _compute_backed_up_ids(self, segments: List[RecordingSegment]) -> set:
        cfg = self._active_config()
        if cfg is None:
            return set()
        backed = set()
        for s in segments:
            if find_segment_backup_file(BACKUP_DIR, cfg, s) is not None:
                backed.add(s.seg_id)
        return backed

    def _on_sd_list_finished(self, ok: bool, summary: str, segments: list) -> None:
        cfg = self._active_config()
        qd = self.date_edit.date()
        local_bk_segs = (
            load_local_backup_segments(BACKUP_DIR, cfg, qd.year(), qd.month(), qd.day())
            if cfg is not None
            else []
        )

        # Merge live SD card segments with local backup segments so backed-up clips are ALWAYS
        # previewed in the On-Camera SD Card list (even if camera is offline or overwrote old clips)
        merged_by_id: Dict[int, RecordingSegment] = {s.seg_id: s for s in local_bk_segs}
        for s in segments:
            merged_by_id[s.seg_id] = s

        self.all_segments = sorted(merged_by_id.values(), key=lambda s: s.start_ts)
        backed_ids = self._compute_backed_up_ids(self.all_segments)

        # Persist exact SD card metadata (such as exact end_ts) for any backed-up segments
        if cfg is not None and backed_ids:
            backed_segs = [s for s in self.all_segments if s.seg_id in backed_ids]
            save_segment_backup_meta(BACKUP_DIR, cfg, backed_segs)

        total = len(self.all_segments)
        anom = sum(1 for s in self.all_segments if s.is_anomaly)
        norm = total - anom
        self.btn_flt_all.setText(f"All ({total})")
        self.btn_flt_anom.setText(f"Anomalies ({anom})")
        self.btn_flt_norm.setText(f"Normal ({norm})")

        if backed_ids:
            self.lbl_list_status.setText(f"{total} clips • {len(backed_ids)} backed up")
        else:
            self.lbl_list_status.setText(summary)

        self.timeline.set_day_and_segments(self._day_start_utc_ts(), self.all_segments, backed_ids)
        self._populate_sd_clip_list()

        if not ok and not self.all_segments:
            self.canvas.set_status(summary, False)
        elif not self.all_segments:
            self.canvas.set_status("No recordings on SD card for this date", False)
        elif self.play_worker is None or not self.play_worker.isRunning():
            self.canvas.set_status(
                f"Loaded {total} clips ({anom} anomalies, {len(backed_ids)} local backups) — Click any clip to play",
                False,
            )

    @staticmethod
    def _apply_segment_item_appearance(item: QListWidgetItem, seg: RecordingSegment, has_backup: bool) -> None:
        if seg.raw_type == 3:
            ev_ico, ev_col, fg = "alert_triangle", "#ef4444", "#fca5a5"
        elif seg.raw_type == 2:
            ev_ico, ev_col, fg = "zap", "#f59e0b", "#fcd34d"
        else:
            ev_ico, ev_col, fg = "film", "#60a5fa", "#d4d4d8"
        item.setForeground(QColor(fg))
        item.setIcon(clip_row_icon(ev_ico, ev_col, is_backed_up=has_backup))
        tip = f"{seg.kind_label}  •  {seg.date_str}  {seg.start_time_str} – {seg.end_time_str} ({seg.duration_str})"
        if has_backup:
            tip += "\n✓ Local backup available on PC (plays directly from local disk)"
        else:
            tip += "\nRight-click to back up this clip to your PC"
        item.setToolTip(tip)

    def _toggle_sort_order(self) -> None:
        self.sort_newest_first = not self.sort_newest_first
        self.btn_sort_order.setText("↓ New → Old" if self.sort_newest_first else "↑ Old → New")
        if self.source_mode == "local":
            self._load_local_captures()
        else:
            self._populate_sd_clip_list()

    def _populate_sd_clip_list(self) -> None:
        self.clip_list.clear()
        if self.event_filter == "anomaly":
            self.filtered_segments = [s for s in self.all_segments if s.is_anomaly]
        elif self.event_filter == "normal":
            self.filtered_segments = [s for s in self.all_segments if not s.is_anomaly]
        else:
            self.filtered_segments = list(self.all_segments)

        cfg = self._active_config()
        ordered_segs = reversed(self.filtered_segments) if self.sort_newest_first else self.filtered_segments
        selected_item: Optional[QListWidgetItem] = None
        for seg in ordered_segs:
            text = f"{seg.start_time_str} – {seg.end_time_str}  ({seg.duration_str})"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, seg)
            has_backup = bool(cfg and find_segment_backup_file(BACKUP_DIR, cfg, seg) is not None)
            self._apply_segment_item_appearance(item, seg, has_backup)
            self.clip_list.addItem(item)
            if self.current_segment is not None and seg.seg_id == self.current_segment.seg_id:
                selected_item = item

        if selected_item is not None:
            self.clip_list.blockSignals(True)
            self.clip_list.setCurrentItem(selected_item)
            self.clip_list.blockSignals(False)

    def _load_local_captures(self) -> None:
        self.clip_list.clear()
        self.local_files.clear()
        RECORDING_DIR.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

        # Only show manual recordings and snapshots made via the app (NOT SD card backups)
        files = sorted(
            list(RECORDING_DIR.glob("*.mp4")) + list(SNAPSHOT_DIR.glob("*.jpg")),
            key=lambda p: p.stat().st_mtime if p.exists() else 0,
            reverse=self.sort_newest_first,
        )
        self.local_files = files
        for fp in files:
            is_vid = fp.suffix.lower() == ".mp4"
            sz_kb = fp.stat().st_size / 1024.0 if fp.exists() else 0
            sz_str = f"{sz_kb / 1024.0:.1f} MB" if sz_kb >= 1024 else f"{sz_kb:.0f} KB"
            kind = "[MP4]" if is_vid else "[JPG]"
            item = QListWidgetItem(f"{kind}  {fp.name}  ({sz_str})")
            item.setData(Qt.ItemDataRole.UserRole, str(fp))
            item.setToolTip(str(fp))
            item.setIcon(
                clip_row_icon("film" if is_vid else "camera", "#38bdf8" if is_vid else "#10b981", is_backed_up=False)
            )
            self.clip_list.addItem(item)

        self.lbl_list_status.setText(f"{len(files)} local files")
        self.timeline.set_day_and_segments(0, [])

    # --- Playback Controls (Auto-Plays Local Backup When Available!) ---
    def _on_clip_item_clicked(self, item: QListWidgetItem) -> None:
        data = item.data(Qt.ItemDataRole.UserRole)
        if self.source_mode == "sdcard" and isinstance(data, RecordingSegment):
            self.play_sd_segment(data, start_ts=data.start_ts)
        elif self.source_mode == "local" and isinstance(data, str):
            self.play_local_capture(Path(data))

    def _select_clip_in_list(self, segment: RecordingSegment) -> None:
        """Select and scroll to `segment` in `self.clip_list` (switching filter to All if hidden)."""
        found_item: Optional[QListWidgetItem] = None
        for i in range(self.clip_list.count()):
            it = self.clip_list.item(i)
            if it is not None:
                data = it.data(Qt.ItemDataRole.UserRole)
                if isinstance(data, RecordingSegment) and data.seg_id == segment.seg_id:
                    found_item = it
                    break

        if found_item is None and self.event_filter != "all":
            self._set_event_filter("all")
            for i in range(self.clip_list.count()):
                it = self.clip_list.item(i)
                if it is not None:
                    data = it.data(Qt.ItemDataRole.UserRole)
                    if isinstance(data, RecordingSegment) and data.seg_id == segment.seg_id:
                        found_item = it
                        break

        if found_item is not None:
            self.clip_list.blockSignals(True)
            self.clip_list.setCurrentItem(found_item)
            self.clip_list.blockSignals(False)
            self.clip_list.scrollToItem(found_item)

    def play_sd_segment(
        self,
        segment: RecordingSegment,
        start_ts: Optional[int] = None,
        force_sd_stream: bool = False,
    ) -> None:
        cfg = self._active_config()
        if cfg is None:
            return
        self.stop_playback()
        self.current_segment = segment
        self.current_local_file = None
        target_ts = int(start_ts if start_ts is not None else segment.start_ts)
        self.current_position_ts = target_ts
        self.timeline.set_playhead(target_ts)
        self._select_clip_in_list(segment)

        # Check if this SD card clip already has a local backup on disk
        local_backup = None if force_sd_stream else find_segment_backup_file(BACKUP_DIR, cfg, segment)
        # Only 1x is available for footage still on the SD card; 2x-16x unlock when downloaded locally
        self._update_speed_controls_lock(unlocked=(local_backup is not None))

        self.lbl_player_title.setText(
            f"{cfg.name}  •  {segment.date_str}  {segment.start_time_str} – {segment.end_time_str} ({segment.duration_str})"
        )
        self.lbl_event_badge.setText(segment.kind_label)
        if segment.raw_type == 3:
            self.lbl_event_badge.setStyleSheet(
                "background-color: #7f1d1d; color: #ffffff; border: 1px solid #ef4444; "
                "border-radius: 3px; padding: 1px 7px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700;"
            )
        elif segment.raw_type == 2:
            self.lbl_event_badge.setStyleSheet(
                "background-color: #78350f; color: #fef3c7; border: 1px solid #f59e0b; "
                "border-radius: 3px; padding: 1px 7px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700;"
            )
        else:
            self.lbl_event_badge.setStyleSheet(
                "background-color: #172554; color: #93c5fd; border: 1px solid #2563eb; "
                "border-radius: 3px; padding: 1px 7px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700;"
            )
        self.lbl_event_badge.setVisible(True)

        if local_backup is not None:
            self.lbl_backup_badge.setVisible(True)
            self.lbl_backup_badge.setToolTip(f"Playing local backup file:\n{local_backup}")
            self.canvas.set_camera_info(cfg.name, f"Local Backup • {segment.kind_label}")
            self.play_worker = SDCardPlaybackWorker(
                config=cfg,
                segment=segment,
                start_ts=target_ts,
                speed=self.playback_speed,
                local_file=str(local_backup),
                parent=self,
            )
            self.play_worker.frame_ready.connect(self._on_playback_frame)
            self.play_worker.audio_ready.connect(self._on_playback_audio)
            self.play_worker.status_changed.connect(self._on_playback_status)
            self.play_worker.segment_finished.connect(self._on_segment_finished)
            self.play_worker.start()
            self._update_play_pause_btn(is_playing=True)
        else:
            self.lbl_backup_badge.setVisible(False)
            self.canvas.set_camera_info(cfg.name, f"SD Playback • {segment.kind_label}")
            self.canvas.set_status("Connecting to SD Card...", False)
            # Debounce rapid clicks across un-downloaded SD card clips by 120ms so only the
            # final selected clip opens a TCP 8800 session to the camera
            self._pending_sd_play = (cfg, segment, target_ts)
            self._sd_play_timer.start(120)
            self._update_play_pause_btn(is_playing=True)

    def _start_pending_sd_play(self) -> None:
        if self._pending_sd_play is None:
            return
        cfg, segment, target_ts = self._pending_sd_play
        self._pending_sd_play = None
        if self.play_worker is not None:
            w = self.play_worker
            self.play_worker = None
            try:
                w.frame_ready.disconnect()
                w.audio_ready.disconnect()
                w.status_changed.disconnect()
                w.segment_finished.disconnect()
            except Exception:
                pass
            self._retire_thread(w)
        self.play_worker = SDCardPlaybackWorker(
            config=cfg,
            segment=segment,
            start_ts=target_ts,
            speed=1.0,
            local_file=None,
            parent=self,
        )
        self.play_worker.frame_ready.connect(self._on_playback_frame)
        self.play_worker.audio_ready.connect(self._on_playback_audio)
        self.play_worker.status_changed.connect(self._on_playback_status)
        self.play_worker.segment_finished.connect(self._on_segment_finished)
        self.play_worker.start()

    def play_local_capture(self, path: Path) -> None:
        self.stop_playback()
        self.current_local_file = path
        self.current_segment = None
        self.lbl_event_badge.setVisible(False)
        self.lbl_backup_badge.setVisible(BACKUP_DIR in path.parents)
        self.lbl_player_title.setText(f"Local Capture  •  {path.name}")
        self.canvas.set_camera_info("Local Capture", path.name)

        if path.suffix.lower() in (".jpg", ".jpeg", ".png"):
            self._update_speed_controls_lock(unlocked=False)
            qimg = QImage(str(path))
            if not qimg.isNull():
                self.canvas.set_frame(qimg)
                self.lbl_clock_pos.setText("SNAPSHOT")
                self.lbl_player_fps.setText(f"{qimg.width()}x{qimg.height()}")
            else:
                self.canvas.set_status("Could not load snapshot image", False)
            return

        self._update_speed_controls_lock(unlocked=True)
        cfg = self._active_config() or CameraConfig(cam_id="local", name="Local")
        self.play_worker = SDCardPlaybackWorker(
            config=cfg,
            local_file=str(path),
            speed=self.playback_speed,
            parent=self,
        )
        self.play_worker.frame_ready.connect(self._on_playback_frame)
        self.play_worker.status_changed.connect(self._on_playback_status)
        self.play_worker.start()
        self._update_play_pause_btn(is_playing=True)

    def _on_playback_frame(self, qimg: QImage, ts_sec: int, fps: float) -> None:
        self.canvas.set_frame(qimg)
        self.lbl_player_fps.setText(f"{fps:.1f} FPS")
        if self.source_mode == "sdcard" and ts_sec > 0:
            self.current_position_ts = ts_sec
            self.timeline.set_playhead(
                ts_sec,
                ensure_visible=(not self.timeline._panning and not self.timeline._pan_overview_drag),
            )
            self.lbl_clock_pos.setText(
                datetime.fromtimestamp(ts_sec, timezone.utc).strftime("%I:%M:%S %p")
            )
        else:
            m, s = divmod(max(0, ts_sec), 60)
            self.lbl_clock_pos.setText(f"{m:02d}:{s:02d}")

    def _on_timeline_zoom_changed(self, span_sec: float, start_ts: int, end_ts: int) -> None:
        is_full_day = (span_sec >= 86390.0)
        self.btn_tl_pan_left.setEnabled(not is_full_day)
        self.btn_tl_pan_right.setEnabled(not is_full_day)
        self.btn_tl_zoom_out.setEnabled(not is_full_day)
        self.btn_tl_zoom_in.setEnabled(span_sec > DayTimelineWidget.MIN_SPAN_SEC + 1.0)
        for preset_sec, btn in self.tl_zoom_btns.items():
            btn.setChecked(abs(span_sec - float(preset_sec)) < max(30.0, preset_sec * 0.12))

        if is_full_day or start_ts <= 0 or end_ts <= 0:
            self.lbl_tl_zoom_range.setText("Full Day • Wheel to Zoom")
        else:
            s_str = datetime.fromtimestamp(start_ts, timezone.utc).strftime("%I:%M %p").lstrip("0")
            e_str = datetime.fromtimestamp(end_ts, timezone.utc).strftime("%I:%M %p").lstrip("0")
            mins = int(round(span_sec / 60.0))
            span_lbl = f"{mins // 60}h {mins % 60}m" if (mins >= 60 and mins % 60 != 0) else (f"{mins // 60}h" if mins >= 60 else f"{mins}m")
            self.lbl_tl_zoom_range.setText(f"{s_str} – {e_str} ({span_lbl})")

    def _on_playback_audio(self, pcm_bytes: bytes, level: float) -> None:
        if level >= self._review_audio_level:
            self._review_audio_level = min(1.0, level)
        else:
            self._review_audio_level = self._review_audio_level * 0.72 + level * 0.28
        pct = int(round(min(100.0, self._review_audio_level * 100.0)))
        if level > 0.02 and pct < 12:
            pct = 12
        self.audio_bar.setValue(pct)
        if pct > 0 and not self._review_audio_decay_timer.isActive():
            self._review_audio_decay_timer.start()
        self.audio_out.write_pcm(pcm_bytes)

    def _decay_review_audio_meter(self) -> None:
        if self._review_audio_level <= 0.015:
            self._review_audio_level = 0.0
            self.audio_bar.setValue(0)
            self._review_audio_decay_timer.stop()
            return
        self._review_audio_level *= 0.80
        self.audio_bar.setValue(int(round(min(100.0, self._review_audio_level * 100.0))))

    def _on_playback_status(self, msg: str, is_playing: bool) -> None:
        self.canvas.set_status(msg, is_playing)
        self.main_win.status_bar.showMessage(msg, 4000)

    def _on_segment_finished(self) -> None:
        # Auto-advance to the next chronological segment on the SD card
        if self.source_mode == "sdcard" and self.current_segment is not None:
            nxt = next(
                (s for s in self.all_segments if s.start_ts > self.current_segment.start_ts),
                None,
            )
            if nxt is not None:
                self.play_sd_segment(nxt, start_ts=nxt.start_ts)
                return
        self._update_play_pause_btn(is_playing=False)

    def _on_timeline_seek(self, target_ts: int, from_timeline_click: bool = True) -> None:
        if self.source_mode != "sdcard" or not self.all_segments:
            return
        # 1. Exact segment match if target_ts is inside an anomaly segment
        exact_anom = next(
            (s for s in self.all_segments if s.is_anomaly and s.start_ts <= target_ts < s.end_ts),
            None,
        )
        if exact_anom is not None:
            self.play_sd_segment(exact_anom, start_ts=target_ts)
            return

        # 2. If clicked on the timeline within the 3px visual width of an anomaly bar (scaled to zoom span)
        if from_timeline_click:
            tl_w = max(100.0, float(self.timeline.width() - 16))
            anom_tol = max(2, int(round((self.timeline._view_span_sec / tl_w) * 3.5)))
            near_anoms = [
                s for s in self.all_segments
                if s.is_anomaly and (s.start_ts - anom_tol) <= target_ts <= (s.end_ts + anom_tol)
            ]
            if near_anoms:
                seg = min(
                    near_anoms,
                    key=lambda s: min(abs(target_ts - s.start_ts), abs(target_ts - s.end_ts)),
                )
                self.play_sd_segment(seg, start_ts=seg.start_ts)
                return

        # 3. Exact segment match (continuous or any segment)
        seg = next(
            (s for s in self.all_segments if s.start_ts <= target_ts < s.end_ts),
            None,
        )
        # 4. Next chronological segment (or last segment of the day)
        if seg is None:
            seg = next((s for s in self.all_segments if s.start_ts >= target_ts), None)
            if seg is None and self.all_segments:
                seg = self.all_segments[-1]
            if seg is not None:
                target_ts = seg.start_ts
        if seg is not None:
            self.play_sd_segment(seg, start_ts=target_ts)

    def toggle_play_pause(self) -> None:
        if self.play_worker is not None and self.play_worker.isRunning():
            new_paused = not self.play_worker.is_paused()
            self.play_worker.set_paused(new_paused)
            self._update_play_pause_btn(is_playing=not new_paused)
            return

        if self.source_mode == "sdcard":
            if self.current_segment is not None:
                self.play_sd_segment(self.current_segment, start_ts=self.current_position_ts or self.current_segment.start_ts)
            elif self.filtered_segments:
                first_seg = self.filtered_segments[-1]
                self.play_sd_segment(first_seg, start_ts=first_seg.start_ts)
        elif self.source_mode == "local" and self.current_local_file is not None:
            self.play_local_capture(self.current_local_file)

    def skip_seconds(self, delta_sec: int) -> None:
        if self.source_mode == "sdcard" and self.current_position_ts > 0:
            self._on_timeline_seek(max(0, self.current_position_ts + int(delta_sec)), from_timeline_click=False)

    def _update_speed_controls_lock(self, unlocked: bool) -> None:
        """
        Lock 2x, 4x, 8x, 16x when footage is streaming directly from the camera's SD card (only 1x available).
        Unlock 2x, 4x, 8x, 16x when the footage is already downloaded locally on the PC.
        """
        if not unlocked:
            self.playback_speed = 1.0
        for spd, btn in self.speed_btns.items():
            is_1x = abs(spd - 1.0) < 0.05
            can_use = True if is_1x else bool(unlocked)
            btn.setEnabled(can_use)
            btn.setChecked(abs(spd - self.playback_speed) < 0.05)
            if is_1x:
                btn.setToolTip("Playback Speed 1x (Normal Real-Time)")
            elif unlocked:
                btn.setToolTip(f"Playback Speed {int(spd)}x (Unlocked — Local Backup Fast Playback)")
            else:
                btn.setToolTip(
                    f"{int(spd)}x Speed Locked — Back up / download this clip to PC to unlock {int(spd)}x fast playback"
                )

    def set_playback_speed(self, speed: float) -> None:
        btn_target = self.speed_btns.get(float(speed))
        if btn_target is not None and not btn_target.isEnabled():
            speed = 1.0
        self.playback_speed = float(speed)
        for spd, btn in self.speed_btns.items():
            btn.setChecked(abs(spd - speed) < 0.05)
        if self.play_worker is not None and self.play_worker.isRunning():
            # If the clip finished downloading while playing from SD, switch seamlessly to local backup playback
            if not self.play_worker.local_file and self.source_mode == "sdcard" and self.current_segment is not None:
                cfg = self._active_config()
                if cfg and find_segment_backup_file(BACKUP_DIR, cfg, self.current_segment) is not None:
                    self.play_sd_segment(
                        self.current_segment,
                        start_ts=self.current_position_ts or self.current_segment.start_ts,
                    )
                    return
            self.play_worker.set_speed(speed)

    def _update_play_pause_btn(self, is_playing: bool) -> None:
        if is_playing:
            self.btn_play_pause.setText("Pause")
            self.btn_play_pause.setIcon(lucide_icon("pause", "#ffffff", 14))
        else:
            self.btn_play_pause.setText("Play")
            self.btn_play_pause.setIcon(lucide_icon("play", "#ffffff", 14))

    def _on_mute_toggled(self, checked: bool) -> None:
        self.audio_out.set_muted(not checked)
        self.btn_mute.setIcon(
            lucide_icon("volume_2", "#10b981", 14) if checked else lucide_icon("volume_x", "#71717a", 14)
        )

    def stop_playback(self) -> None:
        self._sd_play_timer.stop()
        self._pending_sd_play = None
        if hasattr(self, "_review_audio_decay_timer"):
            self._review_audio_decay_timer.stop()
            self._review_audio_level = 0.0
            self.audio_bar.setValue(0)
        self.audio_out.flush()
        if self.play_worker is not None:
            w = self.play_worker
            self.play_worker = None
            try:
                w.frame_ready.disconnect()
                w.audio_ready.disconnect()
                w.status_changed.disconnect()
                w.segment_finished.disconnect()
            except Exception:
                pass
            self._retire_thread(w)
        self._update_play_pause_btn(is_playing=False)

    def stop_all(self) -> None:
        self.stop_playback()
        self.audio_out.close()
        if self.export_worker is not None:
            ew = self.export_worker
            self.export_worker = None
            self._retire_thread(ew)
        if self.bulk_worker is not None:
            bw = self.bulk_worker
            self.bulk_worker = None
            self._retire_thread(bw)

    # --- Local Backup & Date/Time Range Downloader ---
    def _mark_segment_backed_up_ui(self, seg_id: int) -> None:
        self.timeline.mark_segment_backed_up(seg_id)
        for i in range(self.clip_list.count()):
            it = self.clip_list.item(i)
            if it is None:
                continue
            seg = it.data(Qt.ItemDataRole.UserRole)
            if isinstance(seg, RecordingSegment) and seg.seg_id == seg_id:
                self._apply_segment_item_appearance(it, seg, has_backup=True)
        if self.current_segment is not None and self.current_segment.seg_id == seg_id:
            self.lbl_backup_badge.setVisible(True)
            self._update_speed_controls_lock(unlocked=True)

    def _show_clip_context_menu(self, pos: QPoint) -> None:
        # Cancel any pending debounced SD stream before opening the modal context menu
        self._sd_play_timer.stop()
        self._pending_sd_play = None
        item = self.clip_list.itemAt(pos)
        if item is None:
            return
        data = item.data(Qt.ItemDataRole.UserRole)
        menu = QMenu(self)

        if self.source_mode == "sdcard" and isinstance(data, RecordingSegment):
            cfg = self._active_config()
            existing_bk = find_segment_backup_file(BACKUP_DIR, cfg, data) if cfg else None
            act_play = menu.addAction(
                "Play Local Backup Copy" if existing_bk else "Stream Clip from Camera SD Card"
            )
            act_stream_sd = menu.addAction("Force Stream Directly from SD Card") if existing_bk else None
            menu.addSeparator()
            act_backup_clip = menu.addAction(
                "Re-Download Local Backup from SD Card" if existing_bk else "Backup This Clip to PC (.MP4)"
            )
            act_backup_range = menu.addAction("Backup Footage by Date & Time Range...")
            menu.addSeparator()
            act_open_bk = menu.addAction("Open Local Backups Folder")

            chosen = menu.exec(self.clip_list.viewport().mapToGlobal(pos))
            if chosen == act_play:
                self.play_sd_segment(data, start_ts=data.start_ts)
            elif act_stream_sd and chosen == act_stream_sd:
                self.play_sd_segment(data, start_ts=data.start_ts, force_sd_stream=True)
            elif chosen == act_backup_clip:
                self.backup_single_segment(data)
            elif chosen == act_backup_range:
                self.open_backup_range_dialog()
            elif chosen == act_open_bk:
                BACKUP_DIR.mkdir(parents=True, exist_ok=True)
                self.main_win._open_folder(existing_bk.parent if existing_bk else BACKUP_DIR)

        elif self.source_mode == "local" and isinstance(data, str):
            fp = Path(data)
            act_play = menu.addAction("Play / View File")
            act_open = menu.addAction("Open Containing Folder")
            chosen = menu.exec(self.clip_list.viewport().mapToGlobal(pos))
            if chosen == act_play:
                self.play_local_capture(fp)
            elif chosen == act_open:
                self.main_win._open_folder(fp.parent)

    def backup_single_segment(self, seg: RecordingSegment) -> None:
        cfg = self._active_config()
        if cfg is None:
            return
        if self.export_worker is not None and self.export_worker.isRunning():
            self.canvas.show_error_toast("A clip download is already in progress")
            return
        if self.export_worker is not None:
            old_ew = self.export_worker
            self.export_worker = None
            self._retire_thread(old_ew)

        # Pause active SD stream so the camera dedicates full bandwidth to the backup download
        self.stop_playback()
        self.current_segment = seg
        self._select_clip_in_list(seg)
        self.canvas.set_status(f"Downloading clip ({seg.start_time_str} – {seg.end_time_str}) from SD card...", False)

        out_file = get_segment_backup_path(BACKUP_DIR, cfg, seg)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        save_segment_backup_meta(BACKUP_DIR, cfg, [seg])
        self._single_backup_seg_id = seg.seg_id

        self.export_bar.setValue(0)
        self.export_bar.setVisible(True)
        self.canvas.set_download_progress(0)
        self.btn_export.setText("Backing up...")
        self.btn_export.setEnabled(False)
        self.main_win.status_bar.showMessage(
            f"Backing up SD clip ({seg.start_time_str} – {seg.end_time_str}) to {out_file.name}...", 8000
        )

        self.export_worker = SDCardExportWorker(
            config=cfg,
            segment=seg,
            start_ts=seg.start_ts,
            end_ts=seg.end_ts,
            output_path=str(out_file),
            parent=self,
        )
        self.export_worker.progress_changed.connect(self._on_export_progress)
        self.export_worker.export_finished.connect(self._on_export_finished)
        self.export_worker.start()

    def open_backup_range_dialog(self) -> None:
        if not self.cameras:
            self.canvas.show_error_toast("No cameras configured")
            return
        if self.bulk_worker is not None and self.bulk_worker.isRunning():
            self.canvas.show_error_toast("A bulk footage backup is already running")
            return
        if self.bulk_worker is not None:
            old_bw = self.bulk_worker
            self.bulk_worker = None
            self._retire_thread(old_bw)

        dlg = BackupRangeDialog(
            cameras=self.cameras,
            active_cam_id=self.active_cam_id,
            initial_date=self.date_edit.date(),
            initial_filter=self.event_filter,
            parent=self,
        )
        if dlg.exec() != QDialog.DialogCode.Accepted:
            return

        cfg = dlg.selected_camera()
        if cfg is None:
            return
        start_ts, end_ts = dlg.selected_timestamps()
        flt = dlg.selected_filter()
        skip_ex = dlg.skip_existing()

        # Pause playback if backing up from the same camera
        if self.active_cam_id == cfg.cam_id:
            self.stop_playback()

        self.backup_box.setVisible(True)
        self.backup_progress_bar.setValue(0)
        self.lbl_backup_status.setText(f"Scanning {cfg.name} SD card for range...")
        self.btn_backup_range.setEnabled(False)

        self.bulk_worker = SDCardBulkBackupWorker(
            config=cfg,
            backup_root=BACKUP_DIR,
            start_ts=start_ts,
            end_ts=end_ts,
            event_filter=flt,
            skip_existing=skip_ex,
            parent=self,
        )
        self.bulk_worker.status_updated.connect(self._on_bulk_backup_status)
        self.bulk_worker.segment_backed_up.connect(self._on_bulk_segment_backed_up)
        self.bulk_worker.backup_finished.connect(self._on_bulk_backup_finished)
        self.bulk_worker.start()

    def _cancel_bulk_backup(self) -> None:
        if self.bulk_worker is not None and self.bulk_worker.isRunning():
            self.lbl_backup_status.setText("Stopping backup after current packet...")
            self.bulk_worker.cancel()

    def _on_bulk_backup_status(self, msg: str, done_count: int, total_count: int, overall_pct: int) -> None:
        _ = (done_count, total_count)
        self.lbl_backup_status.setText(msg)
        self.backup_progress_bar.setValue(overall_pct)
        self.main_win.status_bar.showMessage(msg, 4000)

    def _on_bulk_segment_backed_up(self, seg_id: int, file_path: str) -> None:
        _ = file_path
        self._mark_segment_backed_up_ui(seg_id)

    def _on_bulk_backup_finished(self, ok: bool, summary: str, saved_count: int, dest_folder: str) -> None:
        if self.bulk_worker is not None:
            bw = self.bulk_worker
            self.bulk_worker = None
            self._retire_thread(bw)
        self.backup_box.setVisible(False)
        self.btn_backup_range.setEnabled(self.source_mode == "sdcard")
        if ok and saved_count > 0:
            self.canvas.show_snapshot_toast(dest_folder)
            self.main_win.status_bar.showMessage(f"✓ {summary}", 8000)
        elif ok:
            self.main_win.status_bar.showMessage(f"✓ {summary}", 7000)
        else:
            self.canvas.show_error_toast(summary)
            self.main_win.status_bar.showMessage(summary, 6000)
        # Refresh backup count badge in list header
        if self.source_mode == "sdcard" and self.all_segments:
            backed_ids = self._compute_backed_up_ids(self.all_segments)
            self.lbl_list_status.setText(f"{len(self.all_segments)} clips • {len(backed_ids)} backed up")

    # --- Snapshot & MP4 Clip Export ---
    def save_snapshot(self) -> None:
        qimg = self.canvas._qimage
        if qimg is None or qimg.isNull():
            self.canvas.show_error_toast("No video frame displayed to capture")
            return
        SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
        cfg = self._active_config()
        cam_name = cfg.name if cfg else "Review"
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in cam_name).strip("_") or "Camera"
        ts_str = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = SNAPSHOT_DIR / f"{safe_name}_Review_{ts_str}.jpg"
        if qimg.save(str(out_path), "JPG", 95):
            self.canvas.trigger_shutter_flash()
            self.canvas.show_snapshot_toast(str(out_path))
            self.main_win.status_bar.showMessage(f"✓ Saved review snapshot: {out_path.name}", 5000)

    def _show_export_menu(self) -> None:
        menu = QMenu(self)
        act_backup_clip = None
        act_full = None
        act_30s = None
        act_1m = None
        act_5m = None
        if self.source_mode == "sdcard" and self.current_segment is not None:
            act_backup_clip = menu.addAction(
                f"Backup Current Clip ({self.current_segment.duration_str}) to Local Backups"
            )
            menu.addSeparator()
            act_full = menu.addAction(f"Export Entire Clip ({self.current_segment.duration_str}) to captures/recordings")
            act_30s = menu.addAction("Export Next 30 Seconds from Playhead (.MP4)")
            act_1m = menu.addAction("Export Next 1 Minute from Playhead (.MP4)")
            act_5m = menu.addAction("Export Next 5 Minutes from Playhead (.MP4)")
            menu.addSeparator()

        act_range = menu.addAction("Backup Footage by Date & Time Range...")
        menu.addSeparator()
        act_open_bk = menu.addAction("Open Local Backups Folder (captures/recordings/backups)")
        act_open = menu.addAction("Open Recordings Folder (captures/recordings)")

        chosen = menu.exec(self.btn_export.mapToGlobal(QPoint(0, -menu.sizeHint().height())))
        if chosen is None:
            return
        if chosen == act_range:
            self.open_backup_range_dialog()
        elif chosen == act_open_bk:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            self.main_win._open_folder(BACKUP_DIR)
        elif chosen == act_open:
            self.main_win._open_folder(RECORDING_DIR)
        elif act_backup_clip and chosen == act_backup_clip and self.current_segment is not None:
            self.backup_single_segment(self.current_segment)
        elif act_full and chosen == act_full and self.current_segment is not None:
            self._start_sd_export(self.current_segment.start_ts, self.current_segment.end_ts)
        elif act_30s and chosen == act_30s and self.current_segment is not None:
            st = max(self.current_segment.start_ts, self.current_position_ts or self.current_segment.start_ts)
            self._start_sd_export(st, min(self.current_segment.end_ts, st + 30))
        elif act_1m and chosen == act_1m and self.current_segment is not None:
            st = max(self.current_segment.start_ts, self.current_position_ts or self.current_segment.start_ts)
            self._start_sd_export(st, min(self.current_segment.end_ts, st + 60))
        elif act_5m and chosen == act_5m and self.current_segment is not None:
            st = max(self.current_segment.start_ts, self.current_position_ts or self.current_segment.start_ts)
            self._start_sd_export(st, min(self.current_segment.end_ts, st + 300))

    def _start_sd_export(self, start_ts: int, end_ts: int) -> None:
        cfg = self._active_config()
        seg = self.current_segment
        if cfg is None or seg is None:
            return
        if self.export_worker is not None and self.export_worker.isRunning():
            self.canvas.show_error_toast("An MP4 export is already in progress")
            return
        if self.export_worker is not None:
            old_ew = self.export_worker
            self.export_worker = None
            self._retire_thread(old_ew)

        # Pause playback socket so the camera dedicates full bandwidth to the export stream
        self.stop_playback()
        self._single_backup_seg_id = None

        RECORDING_DIR.mkdir(parents=True, exist_ok=True)
        safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in cfg.name).strip("_") or "Camera"
        stamp = datetime.fromtimestamp(start_ts, timezone.utc).strftime("%Y%m%d_%H%M%S")
        kind_tag = seg.kind_label.replace(" ", "")
        out_file = RECORDING_DIR / f"{safe_name}_SD_{kind_tag}_{stamp}.mp4"

        self.export_bar.setValue(0)
        self.export_bar.setVisible(True)
        self.canvas.set_status(f"Exporting clip ({seg.start_time_str} – {seg.end_time_str}) from SD card...", False)
        self.canvas.set_download_progress(0)
        self.btn_export.setText("Exporting...")
        self.btn_export.setEnabled(False)
        self.main_win.status_bar.showMessage(f"Downloading SD card clip to {out_file.name}...", 8000)

        self.export_worker = SDCardExportWorker(
            config=cfg,
            segment=seg,
            start_ts=start_ts,
            end_ts=end_ts,
            output_path=str(out_file),
            parent=self,
        )
        self.export_worker.progress_changed.connect(self._on_export_progress)
        self.export_worker.export_finished.connect(self._on_export_finished)
        self.export_worker.start()

    def _on_export_progress(self, pct: int) -> None:
        self.export_bar.setValue(pct)
        self.canvas.set_download_progress(pct)

    def _on_export_finished(self, ok: bool, msg: str, output_path: str) -> None:
        if self.export_worker is not None:
            ew = self.export_worker
            self.export_worker = None
            self._retire_thread(ew)
        self.canvas.set_download_progress(None)
        self.export_bar.setVisible(False)
        self.btn_export.setText("Backup / Export .MP4")
        self.btn_export.setEnabled(True)
        backed_seg_id = self._single_backup_seg_id
        self._single_backup_seg_id = None
        if ok and output_path:
            if backed_seg_id is not None:
                self._mark_segment_backed_up_ui(backed_seg_id)
                backed_ids = self._compute_backed_up_ids(self.all_segments)
                self.lbl_list_status.setText(f"{len(self.all_segments)} clips • {len(backed_ids)} backed up")
                if self.current_segment is not None and self.current_segment.seg_id == backed_seg_id:
                    self.play_sd_segment(self.current_segment, start_ts=self.current_segment.start_ts)
            self.canvas.show_snapshot_toast(output_path)
            self.main_win.status_bar.showMessage(f"✓ {msg}", 7000)
        else:
            self.canvas.show_error_toast(msg)
            self.main_win.status_bar.showMessage(msg, 6000)


# ---------------------------------------------------------------------------
# Buy Me a Coffee / GCash QR Ph Support Dialog
# ---------------------------------------------------------------------------
_EMBEDDED_GCASH_QR_B64 = "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"


class SupportQRDialog(QDialog):
    """Modal popup displaying the developer's cropped GCash / InstaPay / QR Ph donation QR code."""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Buy Me a Coffee — Support Development")
        self.setFixedSize(370, 468)
        self.setModal(True)

        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(22, 20, 22, 18)
        vbox.setSpacing(12)

        # Header Row
        hdr_row = QHBoxLayout()
        hdr_row.setSpacing(10)
        ico_lbl = QLabel()
        ico_lbl.setPixmap(lucide_pixmap("coffee", "#fbbf24", 24, stroke_width=2.1))
        ico_lbl.setFixedSize(36, 36)
        ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ico_lbl.setStyleSheet(
            "background-color: #2e1a05; border: 1px solid #d97706; border-radius: 18px;"
        )
        hdr_row.addWidget(ico_lbl)

        title_col = QVBoxLayout()
        title_col.setSpacing(2)
        lbl_title = QLabel("Buy Me a Coffee")
        lbl_title.setStyleSheet("color: #f4f4f5; font-size: 16px; font-weight: 700;")
        lbl_sub = QLabel("Support independent development via GCash / QR Ph")
        lbl_sub.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        title_col.addWidget(lbl_title)
        title_col.addWidget(lbl_sub)
        hdr_row.addLayout(title_col, 1)
        vbox.addLayout(hdr_row)

        lbl_msg = QLabel(
            "Enjoying this app? Scan the QR code below using GCash, Maya, or any InstaPay / QR Ph app to send a coffee!"
        )
        lbl_msg.setWordWrap(True)
        lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl_msg.setStyleSheet("color: #d4d4d8; font-size: 12px; padding: 2px 4px;")
        vbox.addWidget(lbl_msg)

        # QR Code Frame (Cropped QR Only)
        qr_card = QFrame()
        qr_card.setFixedSize(264, 264)
        qr_card.setStyleSheet(
            "QFrame { background-color: #ffffff; border: 2px solid #3b82f6; border-radius: 12px; }"
        )
        qr_card_vbox = QVBoxLayout(qr_card)
        qr_card_vbox.setContentsMargins(8, 8, 8, 8)
        self.qr_lbl = QLabel()
        self.qr_lbl.setFixedSize(244, 244)
        self.qr_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.qr_lbl.setStyleSheet("border: none; background: transparent;")
        qr_card_vbox.addWidget(self.qr_lbl)

        qr_center_row = QHBoxLayout()
        qr_center_row.addStretch()
        qr_center_row.addWidget(qr_card)
        qr_center_row.addStretch()
        vbox.addLayout(qr_center_row)

        # Badges Row (GCash & InstaPay / QR Ph)
        badge_row = QHBoxLayout()
        badge_row.setSpacing(8)
        badge_row.addStretch()
        b_gcash = QLabel("GCash")
        b_gcash.setStyleSheet(
            "background-color: #1e3a8a; color: #93c5fd; border: 1px solid #3b82f6; "
            "border-radius: 4px; padding: 2px 10px; font-size: 11px; font-weight: 700;"
        )
        b_qrph = QLabel("InstaPay • QR Ph")
        b_qrph.setStyleSheet(
            "background-color: #18181f; color: #e4e4e7; border: 1px solid #2e2e38; "
            "border-radius: 4px; padding: 2px 10px; font-size: 11px; font-weight: 600;"
        )
        badge_row.addWidget(b_gcash)
        badge_row.addWidget(b_qrph)
        badge_row.addStretch()
        vbox.addLayout(badge_row)

        vbox.addStretch()

        # Bottom Action Row
        btn_row = QHBoxLayout()
        btn_row.addStretch()
        btn_close = QPushButton("Close")
        btn_close.setObjectName("primaryBtn")
        btn_close.setFixedSize(110, 32)
        btn_close.clicked.connect(self.accept)
        btn_row.addWidget(btn_close)
        btn_row.addStretch()
        vbox.addLayout(btn_row)

        self._load_qr_pixmap()

    def _load_qr_pixmap(self) -> None:
        import base64

        pix = QPixmap()
        if _EMBEDDED_GCASH_QR_B64 and not _EMBEDDED_GCASH_QR_B64.startswith("__"):
            try:
                raw = base64.b64decode(_EMBEDDED_GCASH_QR_B64)
                pix.loadFromData(raw)
            except Exception:
                pass
        if pix.isNull():
            app_dir = Path(__file__).resolve().parent
            for cand in (USER_DATA_DIR / "gcash_qr.png", app_dir / "gcash_qr.png"):
                if cand.is_file():
                    pix = QPixmap(str(cand))
                    if not pix.isNull():
                        break
        if not pix.isNull():
            self.qr_lbl.setPixmap(
                pix.scaled(
                    244,
                    244,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )


# ---------------------------------------------------------------------------
# Main Video-First NVR Window
# ---------------------------------------------------------------------------
class OmniSightMainWindow(QMainWindow):
    """UniFi Protect / Frigate NVR style video-first multi-camera client."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle(f"OmniSight v{APP_VERSION} — Unofficial Client Compatible with V380 Cameras")
        self.setMinimumSize(760, 480)
        self._apply_initial_screen_geometry()
        self.setStyleSheet(NVR_STYLESHEET)
        self.setWindowIcon(lucide_icon("cctv", "#3b82f6", 24))

        self.cameras: List[CameraConfig] = []
        self.tiles: Dict[str, CameraTileWidget] = {}
        self.selected_cam_id: Optional[str] = None
        self._ui_settings = QSettings("OmniSight", "OmniSightNVR")
        saved_grid = self._ui_settings.value("grid_mode", 2)
        try:
            self.grid_mode: int = int(saved_grid) if int(saved_grid) in (0, 1, 2, 3) else 2
        except Exception:
            self.grid_mode = 2
        self.maximized_cam_id: Optional[str] = None
        self.monitor_mode: bool = False
        saved_drawer = self._ui_settings.value("drawer_open", True)
        self._drawer_was_open: bool = str(saved_drawer).lower() not in ("false", "0")
        self._held_ptz_dir: Optional[str] = None
        self._ptz_repeat_timer = QTimer(self)
        self._ptz_repeat_timer.setInterval(180)
        self._ptz_repeat_timer.timeout.connect(self._on_ptz_repeat_tick)

        self._build_ui()
        self._restore_ui_preferences()
        self._load_cameras()
        self._apply_responsive_layout()

    def _apply_initial_screen_geometry(self) -> None:
        app = QApplication.instance()
        screen = app.primaryScreen() if app is not None else None
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(1440, max(760, int(avail.width() * 0.94)))
            target_h = min(860, max(480, int(avail.height() * 0.92)))
            self.resize(target_w, target_h)
            x = avail.left() + max(0, (avail.width() - target_w) // 2)
            y = avail.top() + max(0, (avail.height() - target_h) // 2)
            self.move(x, y)
        else:
            self.resize(1360, 800)

    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        main_vbox = QVBoxLayout(central)
        main_vbox.setContentsMargins(0, 0, 0, 0)
        main_vbox.setSpacing(0)

        # --- Slim 46px Top Header Bar ---
        self.header_bar = QFrame()
        self.header_bar.setObjectName("topHeaderBar")
        self.header_bar.setFixedHeight(46)
        hb = QHBoxLayout(self.header_bar)
        hb.setContentsMargins(12, 0, 12, 0)
        hb.setSpacing(6)

        self.cam_count_badge = QLabel("0 CAMERAS")
        self.cam_count_badge.setFixedHeight(28)
        self.cam_count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cam_count_badge.setStyleSheet(
            "background-color: #141418; color: #a1a1aa; border: 1px solid #27272d; "
            "border-radius: 5px; padding: 4px 8px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 600;"
        )
        hb.addWidget(self.cam_count_badge)

        self.header_ver_badge = QLabel(f"v{APP_VERSION}")
        self.header_ver_badge.setFixedHeight(28)
        self.header_ver_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.header_ver_badge.setToolTip(f"OmniSight Version {APP_VERSION}")
        self.header_ver_badge.setStyleSheet(
            "background-color: #111827; color: #60a5fa; border: 1px solid #1e3a8a; "
            "border-radius: 5px; padding: 4px 7px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 600;"
        )
        hb.addWidget(self.header_ver_badge)

        hb.addSpacing(4)

        # Workspace Switcher: Live Wall vs Footage Review (SD Card & Captures)
        self.btn_ws_live = QPushButton("Live Wall")
        self.btn_ws_live.setObjectName("segBtn")
        self.btn_ws_live.setCheckable(True)
        self.btn_ws_live.setChecked(True)
        self.btn_ws_live.setIcon(lucide_icon("cctv", "#38bdf8", 13))
        self.btn_ws_live.setFixedHeight(28)
        self.btn_ws_live.setToolTip("Live Multi-Camera Surveillance Wall")
        self.btn_ws_live.clicked.connect(lambda: self.set_workspace_mode("live"))
        hb.addWidget(self.btn_ws_live)

        self.btn_ws_review = QPushButton("Footage Review")
        self.btn_ws_review.setObjectName("segBtn")
        self.btn_ws_review.setCheckable(True)
        self.btn_ws_review.setChecked(False)
        self.btn_ws_review.setIcon(lucide_icon("hard_drive", "#f59e0b", 13))
        self.btn_ws_review.setFixedHeight(28)
        self.btn_ws_review.setToolTip("Footage Review Workspace — Browse On-Camera SD Card Anomalies & Local Captures")
        self.btn_ws_review.clicked.connect(lambda: self.set_workspace_mode("review"))
        hb.addWidget(self.btn_ws_review)

        hb.addSpacing(4)

        self.btn_add = QPushButton("Add Camera")
        self.btn_add.setObjectName("primaryBtn")
        self.btn_add.setIcon(lucide_icon("plus", "#ffffff", 14))
        self.btn_add.setFixedHeight(28)
        self.btn_add.setToolTip("Add a new camera")
        self.btn_add.clicked.connect(self.add_camera_dialog)
        hb.addWidget(self.btn_add)

        self.btn_discover = QPushButton("Discover")
        self.btn_discover.setIcon(lucide_icon("radar", "#e4e4e7", 14))
        self.btn_discover.setFixedHeight(28)
        self.btn_discover.setToolTip("Scan LAN for Compatible Cameras (UDP 10008 / TCP 8800)")
        self.btn_discover.clicked.connect(self.discover_cameras_dialog)
        hb.addWidget(self.btn_discover)

        self.btn_start_all = QPushButton("Connect All")
        self.btn_start_all.setIcon(lucide_icon("play", "#10b981", 13))
        self.btn_start_all.setFixedHeight(28)
        self.btn_start_all.setToolTip("Connect all cameras")
        self.btn_start_all.clicked.connect(self.start_all_streams)
        hb.addWidget(self.btn_start_all)

        self.btn_stop_all = QPushButton("Stop All")
        self.btn_stop_all.setIcon(lucide_icon("square", "#a1a1aa", 12))
        self.btn_stop_all.setFixedHeight(28)
        self.btn_stop_all.setToolTip("Stop all camera streams")
        self.btn_stop_all.clicked.connect(self.stop_all_streams)
        hb.addWidget(self.btn_stop_all)

        self.btn_all_quality = QPushButton("All: SD → HD")
        self.btn_all_quality.setFixedHeight(28)
        self.btn_all_quality.setToolTip("Toggle all cameras between SD (Sub Stream) and HD (Main Stream)")
        self.btn_all_quality.clicked.connect(self.toggle_all_streams_quality)
        hb.addWidget(self.btn_all_quality)

        gpu_caps = get_gpu_capabilities()
        self.btn_all_gpu = QPushButton("GPU: ON")
        self.btn_all_gpu.setIcon(lucide_icon("zap", "#10b981", 13))
        self.btn_all_gpu.setFixedHeight(28)
        self.btn_all_gpu.setToolTip(
            f"GPU Acceleration ({gpu_caps.get('summary', 'OpenCL + D3D11VA')}) — Click to toggle GPU power on all cameras"
        )
        self.btn_all_gpu.clicked.connect(self.toggle_all_gpu_acceleration)
        hb.addWidget(self.btn_all_gpu)

        hb.addStretch()

        # Segmented Layout Switcher
        self.btn_grid_auto = QPushButton("AUTO")
        self.btn_grid_auto.setObjectName("segBtn")
        self.btn_grid_auto.setCheckable(True)
        self.btn_grid_auto.setChecked(False)
        self.btn_grid_auto.setFixedHeight(26)
        self.btn_grid_auto.setToolTip("Automatic Smart Grid Layout")
        self.btn_grid_auto.clicked.connect(lambda: self.set_grid_mode(0))
        hb.addWidget(self.btn_grid_auto)

        self.btn_grid_1 = QPushButton("1x1")
        self.btn_grid_1.setObjectName("segBtn")
        self.btn_grid_1.setCheckable(True)
        self.btn_grid_1.setFixedHeight(26)
        self.btn_grid_1.clicked.connect(lambda: self.set_grid_mode(1))
        hb.addWidget(self.btn_grid_1)

        self.btn_grid_2 = QPushButton("2x2")
        self.btn_grid_2.setObjectName("segBtn")
        self.btn_grid_2.setCheckable(True)
        self.btn_grid_2.setChecked(True)
        self.btn_grid_2.setFixedHeight(26)
        self.btn_grid_2.clicked.connect(lambda: self.set_grid_mode(2))
        hb.addWidget(self.btn_grid_2)

        self.btn_grid_3 = QPushButton("3x3")
        self.btn_grid_3.setObjectName("segBtn")
        self.btn_grid_3.setCheckable(True)
        self.btn_grid_3.setFixedHeight(26)
        self.btn_grid_3.clicked.connect(lambda: self.set_grid_mode(3))
        hb.addWidget(self.btn_grid_3)

        hb.addSpacing(4)

        btn_snaps = QPushButton()
        btn_snaps.setObjectName("ghostBtn")
        btn_snaps.setIcon(lucide_icon("folder", "#a1a1aa", 15))
        btn_snaps.setFixedSize(28, 28)
        btn_snaps.setToolTip("Open Snapshots Folder")
        btn_snaps.clicked.connect(lambda: self._open_folder(SNAPSHOT_DIR))
        hb.addWidget(btn_snaps)

        btn_recs = QPushButton()
        btn_recs.setObjectName("ghostBtn")
        btn_recs.setIcon(lucide_icon("film", "#a1a1aa", 15))
        btn_recs.setFixedSize(28, 28)
        btn_recs.setToolTip("Open Recordings Folder")
        btn_recs.clicked.connect(lambda: self._open_folder(RECORDING_DIR))
        hb.addWidget(btn_recs)

        self.btn_drawer_toggle = QPushButton("Controls")
        self.btn_drawer_toggle.setCheckable(True)
        self.btn_drawer_toggle.setChecked(True)
        self.btn_drawer_toggle.setIcon(lucide_icon("panel_right", "#e4e4e7", 14))
        self.btn_drawer_toggle.setFixedHeight(28)
        self.btn_drawer_toggle.setToolTip("Toggle PTZ & Camera Inspector Drawer")
        self.btn_drawer_toggle.clicked.connect(self._toggle_drawer)
        hb.addWidget(self.btn_drawer_toggle)

        self.btn_monitor = QPushButton("Monitor Mode")
        self.btn_monitor.setIcon(lucide_icon("monitor", "#e4e4e7", 14))
        self.btn_monitor.setFixedHeight(28)
        self.btn_monitor.setToolTip("Fullscreen Security Monitor Wall — Pure Footage Only, No UI (F11 / Esc to exit)")
        self.btn_monitor.clicked.connect(self.toggle_monitor_mode)
        hb.addWidget(self.btn_monitor)

        main_vbox.addWidget(self.header_bar)

        # --- Workspace Body: Video-First Grid + Collapsible Right Inspector Drawer ---
        workspace = QWidget()
        ws_hbox = QHBoxLayout(workspace)
        ws_hbox.setContentsMargins(0, 0, 0, 0)
        ws_hbox.setSpacing(0)

        # Center Edge-to-Edge Video Grid
        self.grid_container = QWidget()
        self.grid_container.setStyleSheet("background-color: #09090b;")
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setContentsMargins(6, 6, 6, 6)
        self.grid_layout.setSpacing(6)
        ws_hbox.addWidget(self.grid_container, 1)

        # Right Collapsible PTZ & Device Inspector Drawer (Open by default, scrollable when window height is short)
        self.drawer = QFrame()
        self.drawer.setObjectName("inspectorDrawer")
        self.drawer.setFixedWidth(288)
        self.drawer.setVisible(True)
        drawer_outer_vbox = QVBoxLayout(self.drawer)
        drawer_outer_vbox.setContentsMargins(0, 0, 0, 0)
        drawer_outer_vbox.setSpacing(0)

        self.drawer_scroll = _VerticalScrollArea()
        self.drawer_scroll.setObjectName("drawerScrollArea")
        self.drawer_scroll.setWidgetResizable(True)
        self.drawer_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.drawer_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.drawer_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        drawer_content = QWidget()
        drawer_content.setObjectName("drawerContent")
        drawer_content.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        d_vbox = QVBoxLayout(drawer_content)
        d_vbox.setContentsMargins(8, 8, 8, 8)
        d_vbox.setSpacing(7)
        self._drawer_vbox = d_vbox

        # 1. Camera Switcher Section (Auto-sizes height to fit items cleanly without scrollbar)
        cam_sec = QFrame()
        cam_sec.setObjectName("drawerSection")
        cam_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        cs_vbox = QVBoxLayout(cam_sec)
        cs_vbox.setContentsMargins(8, 7, 8, 7)
        cs_vbox.setSpacing(6)

        cs_hdr = QLabel("DEVICES")
        cs_hdr.setObjectName("sectionTitle")
        cs_hdr.setFixedHeight(13)
        cs_vbox.addWidget(cs_hdr)

        self.cam_list = QListWidget()
        self.cam_list.setIconSize(QSize(14, 14))
        self.cam_list.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.cam_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cam_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.cam_list.currentRowChanged.connect(self._on_list_row_changed)
        self.cam_list.itemDoubleClicked.connect(lambda _: self.edit_selected_camera())
        self.cam_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.cam_list.customContextMenuRequested.connect(self._show_cam_list_context_menu)
        cs_vbox.addWidget(self.cam_list)

        dev_btns = QHBoxLayout()
        dev_btns.setSpacing(4)
        btn_rename = QPushButton("Rename")
        btn_rename.setIcon(lucide_icon("pencil", "#e4e4e7", 12))
        btn_rename.setMinimumWidth(0)
        btn_rename.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_rename.setToolTip("Rename Selected Camera (F2)")
        btn_rename.clicked.connect(lambda: self.rename_camera_dialog())
        dev_btns.addWidget(btn_rename, 1)

        btn_edit = QPushButton("Config")
        btn_edit.setIcon(lucide_icon("settings", "#e4e4e7", 12))
        btn_edit.setMinimumWidth(0)
        btn_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        btn_edit.setToolTip("Edit Connection Settings")
        btn_edit.clicked.connect(self.edit_selected_camera)
        dev_btns.addWidget(btn_edit, 1)

        btn_del = QPushButton()
        btn_del.setObjectName("dangerGhostBtn")
        btn_del.setIcon(lucide_icon("trash", "#a1a1aa", 13))
        btn_del.setFixedWidth(28)
        btn_del.setToolTip("Remove Camera")
        btn_del.clicked.connect(self.delete_selected_camera)
        dev_btns.addWidget(btn_del)
        cs_vbox.addLayout(dev_btns)
        d_vbox.addWidget(cam_sec)

        # 2. Active Camera Telemetry
        sel_sec = QFrame()
        sel_sec.setObjectName("drawerSection")
        sel_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        ss_vbox = QVBoxLayout(sel_sec)
        ss_vbox.setContentsMargins(8, 6, 8, 6)
        ss_vbox.setSpacing(3)

        ss_hdr = QLabel("ACTIVE CAMERA")
        ss_hdr.setObjectName("sectionTitle")
        ss_hdr.setFixedHeight(13)
        ss_vbox.addWidget(ss_hdr)

        self.lbl_selected_name = QLabel("None Selected")
        self.lbl_selected_name.setMinimumWidth(0)
        self.lbl_selected_name.setWordWrap(True)
        self.lbl_selected_name.setStyleSheet("font-size: 12px; font-weight: 600; color: #f4f4f5;")
        ss_vbox.addWidget(self.lbl_selected_name)

        self.lbl_selected_meta = QLabel("Select a viewport to control PTZ")
        self.lbl_selected_meta.setMinimumWidth(0)
        self.lbl_selected_meta.setStyleSheet(
            "color: #71717a; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px;"
        )
        self.lbl_selected_meta.setWordWrap(True)
        ss_vbox.addWidget(self.lbl_selected_meta)
        d_vbox.addWidget(sel_sec)

        # Segmented Inspector Mode Bar: PTZ & Optics | Alert Chimes
        dtab_row = QHBoxLayout()
        dtab_row.setContentsMargins(0, 0, 0, 0)
        dtab_row.setSpacing(4)
        self.btn_dtab_ptz = QPushButton("PTZ & Optics")
        self.btn_dtab_ptz.setObjectName("segBtn")
        self.btn_dtab_ptz.setCheckable(True)
        self.btn_dtab_ptz.setChecked(True)
        self.btn_dtab_ptz.setFixedHeight(26)
        self.btn_dtab_ptz.setMinimumWidth(0)
        self.btn_dtab_ptz.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_dtab_ptz.setToolTip("PTZ Gimbal, Presets, Night Vision & White Floodlight Controls")
        self.btn_dtab_ptz.clicked.connect(lambda: self._switch_drawer_tab(0))
        dtab_row.addWidget(self.btn_dtab_ptz, 1)

        self.btn_dtab_ai = QPushButton("Alert Chimes")
        self.btn_dtab_ai.setObjectName("segBtn")
        self.btn_dtab_ai.setCheckable(True)
        self.btn_dtab_ai.setFixedHeight(26)
        self.btn_dtab_ai.setMinimumWidth(0)
        self.btn_dtab_ai.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_dtab_ai.setToolTip("Alert Chimes, Person Detection, Custom Chime Area & GPU Acceleration")
        self.btn_dtab_ai.clicked.connect(lambda: self._switch_drawer_tab(1))
        dtab_row.addWidget(self.btn_dtab_ai, 1)
        d_vbox.addLayout(dtab_row)

        # 3. PTZ Control Section
        ptz_sec = QFrame()
        self.ptz_sec = ptz_sec
        ptz_sec.setObjectName("drawerSection")
        ptz_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        ptz_vbox = QVBoxLayout(ptz_sec)
        ptz_vbox.setContentsMargins(8, 7, 8, 8)
        ptz_vbox.setSpacing(5)

        ptz_hdr = QLabel("PTZ GIMBAL CONTROL")
        ptz_hdr.setObjectName("sectionTitle")
        ptz_hdr.setFixedHeight(13)
        ptz_vbox.addWidget(ptz_hdr)

        ptz_grid_wrap = QHBoxLayout()
        ptz_grid_wrap.setContentsMargins(0, 0, 0, 0)
        ptz_grid_wrap.addStretch(1)

        ptz_grid = QGridLayout()
        ptz_grid.setContentsMargins(0, 0, 0, 0)
        ptz_grid.setHorizontalSpacing(8)
        ptz_grid.setVerticalSpacing(5)

        self.btn_ptz_up = self._make_ptz_button("chevron_up", "up", "Tilt Up (Up Arrow)")
        self.btn_ptz_left = self._make_ptz_button("chevron_left", "left", "Pan Left (Left Arrow)")
        self.btn_ptz_stop = QPushButton()
        self.btn_ptz_stop.setIcon(lucide_icon("crosshair", "#93c5fd", 15))
        self.btn_ptz_stop.setFixedSize(44, 30)
        self.btn_ptz_stop.setStyleSheet(
            "QPushButton { background-color: #172554; border: 1px solid #2563eb; border-radius: 5px; }"
            "QPushButton:hover { background-color: #1e3a8a; border-color: #3b82f6; }"
            "QPushButton:pressed { background-color: #2563eb; }"
        )
        self.btn_ptz_stop.setToolTip("PTZ Saved Presets — Click to open preset positions & gimbal reset")
        self.btn_ptz_stop.clicked.connect(self._open_ptz_presets_popup)
        self.btn_ptz_right = self._make_ptz_button("chevron_right", "right", "Pan Right (Right Arrow)")
        self.btn_ptz_down = self._make_ptz_button("chevron_down", "down", "Tilt Down (Down Arrow)")

        ptz_grid.addWidget(self.btn_ptz_up, 0, 1, Qt.AlignmentFlag.AlignCenter)
        ptz_grid.addWidget(self.btn_ptz_left, 1, 0, Qt.AlignmentFlag.AlignCenter)
        ptz_grid.addWidget(self.btn_ptz_stop, 1, 1, Qt.AlignmentFlag.AlignCenter)
        ptz_grid.addWidget(self.btn_ptz_right, 1, 2, Qt.AlignmentFlag.AlignCenter)
        ptz_grid.addWidget(self.btn_ptz_down, 2, 1, Qt.AlignmentFlag.AlignCenter)

        ptz_grid_wrap.addLayout(ptz_grid)
        ptz_grid_wrap.addStretch(1)
        ptz_vbox.addLayout(ptz_grid_wrap)

        ptz_hint = QLabel("Click center crosshair for PTZ Presets")
        ptz_hint.setMinimumWidth(0)
        ptz_hint.setWordWrap(True)
        ptz_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ptz_hint.setStyleSheet("color: #71717a; font-size: 10px;")
        ptz_vbox.addWidget(ptz_hint)
        self.ptz_hint = ptz_hint
        self._ptz_vbox = ptz_vbox
        d_vbox.addWidget(ptz_sec)

        # 4. Illumination & Optics Section
        opt_sec = QFrame()
        self.opt_sec = opt_sec
        opt_sec.setObjectName("drawerSection")
        opt_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        opt_vbox = QVBoxLayout(opt_sec)
        opt_vbox.setContentsMargins(8, 7, 8, 8)
        opt_vbox.setSpacing(5)
        self._opt_vbox = opt_vbox

        opt_hdr = QLabel("ILLUMINATION & OPTICS")
        opt_hdr.setObjectName("sectionTitle")
        opt_hdr.setFixedHeight(13)
        opt_vbox.addWidget(opt_hdr)

        lbl_sensor = QLabel("Night Vision / Sensor")
        lbl_sensor.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 500; padding-top: 1px;")
        opt_vbox.addWidget(lbl_sensor)

        img_row = QHBoxLayout()
        img_row.setSpacing(4)
        self.btn_img_color = QPushButton("Color")
        self.btn_img_color.setObjectName("optBtn")
        self.btn_img_color.setCheckable(True)
        self.btn_img_color.setMinimumWidth(0)
        self.btn_img_color.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_img_color.setIcon(lucide_icon("sun", "#e4e4e7", 12))
        self.btn_img_color.setToolTip("Force Full-Color Day Mode")
        self.btn_img_color.clicked.connect(lambda: self._send_image_mode("color"))

        self.btn_img_bw = QPushButton("IR B&W")
        self.btn_img_bw.setObjectName("optBtn")
        self.btn_img_bw.setCheckable(True)
        self.btn_img_bw.setMinimumWidth(0)
        self.btn_img_bw.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_img_bw.setIcon(lucide_icon("eye", "#a1a1aa", 12))
        self.btn_img_bw.setToolTip("Force Infrared B&W Night Vision")
        self.btn_img_bw.clicked.connect(lambda: self._send_image_mode("bw"))

        self.btn_img_auto = QPushButton("Auto")
        self.btn_img_auto.setObjectName("optBtn")
        self.btn_img_auto.setCheckable(True)
        self.btn_img_auto.setMinimumWidth(0)
        self.btn_img_auto.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_img_auto.setIcon(lucide_icon("zap", "#f59e0b", 12))
        self.btn_img_auto.setToolTip("Automatic Day/Night Sensor Switching")
        self.btn_img_auto.clicked.connect(lambda: self._send_image_mode("auto"))

        img_row.addWidget(self.btn_img_color, 1)
        img_row.addWidget(self.btn_img_bw, 1)
        img_row.addWidget(self.btn_img_auto, 1)
        opt_vbox.addLayout(img_row)

        lbl_flood = QLabel("White Floodlight")
        lbl_flood.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 500; padding-top: 1px;")
        opt_vbox.addWidget(lbl_flood)

        light_row = QHBoxLayout()
        light_row.setSpacing(4)
        self.btn_l_on = QPushButton("On")
        self.btn_l_on.setObjectName("optBtn")
        self.btn_l_on.setCheckable(True)
        self.btn_l_on.setMinimumWidth(0)
        self.btn_l_on.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_l_on.setIcon(lucide_icon("sun", "#e4e4e7", 12))
        self.btn_l_on.setToolTip("Turn White Floodlight On")
        self.btn_l_on.clicked.connect(lambda: self._send_light("on"))

        self.btn_l_off = QPushButton("Off")
        self.btn_l_off.setObjectName("optBtn")
        self.btn_l_off.setCheckable(True)
        self.btn_l_off.setMinimumWidth(0)
        self.btn_l_off.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_l_off.setIcon(lucide_icon("moon", "#a1a1aa", 12))
        self.btn_l_off.setToolTip("Turn White Floodlight Off")
        self.btn_l_off.clicked.connect(lambda: self._send_light("off"))

        self.btn_l_auto = QPushButton("Auto")
        self.btn_l_auto.setObjectName("optBtn")
        self.btn_l_auto.setCheckable(True)
        self.btn_l_auto.setMinimumWidth(0)
        self.btn_l_auto.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_l_auto.setIcon(lucide_icon("zap", "#f59e0b", 12))
        self.btn_l_auto.setToolTip("Automatic Floodlight & Night Vision")
        self.btn_l_auto.clicked.connect(lambda: self._send_light("auto"))

        light_row.addWidget(self.btn_l_on, 1)
        light_row.addWidget(self.btn_l_off, 1)
        light_row.addWidget(self.btn_l_auto, 1)
        opt_vbox.addLayout(light_row)

        flip_row = QHBoxLayout()
        flip_row.setSpacing(4)
        self.btn_flip = QPushButton("Flip Orientation 180°")
        self.btn_flip.setObjectName("optBtn")
        self.btn_flip.setCheckable(True)
        self.btn_flip.setMinimumWidth(0)
        self.btn_flip.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_flip.setIcon(lucide_icon("flip_vertical", "#a1a1aa", 12))
        self.btn_flip.setToolTip("Invert Camera Image 180 Degrees")
        self.btn_flip.clicked.connect(self._send_flip)
        flip_row.addWidget(self.btn_flip, 1)
        opt_vbox.addLayout(flip_row)
        d_vbox.addWidget(opt_sec)

        # 4B. Alert Chimes, Person Detection & GPU Section (Tab 1)
        ai_sec = QFrame()
        self.ai_sec = ai_sec
        ai_sec.setObjectName("drawerSection")
        ai_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        ai_sec.setVisible(False)
        ai_vbox = QVBoxLayout(ai_sec)
        ai_vbox.setContentsMargins(8, 7, 8, 8)
        ai_vbox.setSpacing(6)

        lbl_det = QLabel("ALERT CHIMES & DETECTION")
        lbl_det.setObjectName("sectionTitle")
        lbl_det.setFixedHeight(13)
        ai_vbox.addWidget(lbl_det)

        det_row = QHBoxLayout()
        det_row.setSpacing(4)
        self.btn_person_det = QPushButton("Person Alert")
        self.btn_person_det.setObjectName("optBtn")
        self.btn_person_det.setCheckable(True)
        self.btn_person_det.setChecked(True)
        self.btn_person_det.setMinimumWidth(0)
        self.btn_person_det.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_person_det.setIcon(lucide_icon("crosshair", "#ef4444", 12))
        self.btn_person_det.setToolTip(
            "Long-Range Person Detection — Draws PERSON / PERSON (FAR) bounding box and flashes red corner outline"
        )
        self.btn_person_det.clicked.connect(self._toggle_person_detection)
        det_row.addWidget(self.btn_person_det, 1)

        self.btn_det_sound = QPushButton("Chime")
        self.btn_det_sound.setObjectName("optBtn")
        self.btn_det_sound.setCheckable(True)
        self.btn_det_sound.setChecked(True)
        self.btn_det_sound.setMinimumWidth(0)
        self.btn_det_sound.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_det_sound.setIcon(lucide_icon("bell", "#f59e0b", 12))
        self.btn_det_sound.setToolTip(
            "Person Detection Audio Chime — Plays a gentle two-note harmonic chime when a person is detected"
        )
        self.btn_det_sound.clicked.connect(self._toggle_detection_sound)
        det_row.addWidget(self.btn_det_sound, 1)
        ai_vbox.addLayout(det_row)

        cd_row = QHBoxLayout()
        cd_row.setSpacing(4)
        lbl_cd = QLabel("Chime Cooldown:")
        lbl_cd.setMinimumWidth(0)
        lbl_cd.setStyleSheet("color: #71717a; font-size: 10px; font-weight: 600;")
        cd_row.addWidget(lbl_cd)

        self.btn_cd_5 = QPushButton("5s")
        self.btn_cd_5.setObjectName("optBtn")
        self.btn_cd_5.setCheckable(True)
        self.btn_cd_5.setMinimumWidth(0)
        self.btn_cd_5.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_cd_5.setToolTip("5-second cooldown before the detection chime can play again on this camera")
        self.btn_cd_5.clicked.connect(lambda: self._set_detection_sound_cooldown(5))

        self.btn_cd_10 = QPushButton("10s")
        self.btn_cd_10.setObjectName("optBtn")
        self.btn_cd_10.setCheckable(True)
        self.btn_cd_10.setChecked(True)
        self.btn_cd_10.setMinimumWidth(0)
        self.btn_cd_10.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_cd_10.setToolTip("10-second cooldown before the detection chime can play again on this camera")
        self.btn_cd_10.clicked.connect(lambda: self._set_detection_sound_cooldown(10))

        self.btn_cd_20 = QPushButton("20s")
        self.btn_cd_20.setObjectName("optBtn")
        self.btn_cd_20.setCheckable(True)
        self.btn_cd_20.setMinimumWidth(0)
        self.btn_cd_20.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_cd_20.setToolTip("20-second cooldown before the detection chime can play again on this camera")
        self.btn_cd_20.clicked.connect(lambda: self._set_detection_sound_cooldown(20))

        cd_row.addWidget(self.btn_cd_5, 1)
        cd_row.addWidget(self.btn_cd_10, 1)
        cd_row.addWidget(self.btn_cd_20, 1)
        ai_vbox.addLayout(cd_row)

        chime_area_row = QHBoxLayout()
        chime_area_row.setSpacing(4)
        self.btn_chime_zone = QPushButton("Set Chime Area")
        self.btn_chime_zone.setObjectName("optBtn")
        self.btn_chime_zone.setCheckable(True)
        self.btn_chime_zone.setMinimumWidth(0)
        self.btn_chime_zone.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_chime_zone.setIcon(lucide_icon("scan", "#a1a1aa", 12))
        self.btn_chime_zone.setToolTip(
            "Draw a region on the camera footage (e.g. Gate) where Person Detection Chime is allowed to trigger.\n"
            "By default, the Whole Frame triggers the chime until a custom area is set."
        )
        self.btn_chime_zone.clicked.connect(self._toggle_chime_zone_editor)
        chime_area_row.addWidget(self.btn_chime_zone, 1)

        self.btn_reset_chime_zone = QPushButton("Full Frame")
        self.btn_reset_chime_zone.setObjectName("optBtn")
        self.btn_reset_chime_zone.setMinimumWidth(0)
        self.btn_reset_chime_zone.setIcon(lucide_icon("maximize_2", "#a1a1aa", 11))
        self.btn_reset_chime_zone.setEnabled(False)
        self.btn_reset_chime_zone.setToolTip(
            "Reset Chime Trigger Area to Whole Frame (Default — chime triggers anywhere in frame)"
        )
        self.btn_reset_chime_zone.clicked.connect(self._reset_chime_zone)
        chime_area_row.addWidget(self.btn_reset_chime_zone)
        ai_vbox.addLayout(chime_area_row)

        sens_row = QHBoxLayout()
        sens_row.setSpacing(4)
        self.btn_sens_std = QPushButton("Std")
        self.btn_sens_std.setObjectName("optBtn")
        self.btn_sens_std.setCheckable(True)
        self.btn_sens_std.setMinimumWidth(0)
        self.btn_sens_std.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_sens_std.setToolTip("Standard Sensitivity — Close & Mid-range person detection")
        self.btn_sens_std.clicked.connect(lambda: self._set_detection_sensitivity("standard"))

        self.btn_sens_high = QPushButton("High")
        self.btn_sens_high.setObjectName("optBtn")
        self.btn_sens_high.setCheckable(True)
        self.btn_sens_high.setMinimumWidth(0)
        self.btn_sens_high.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_sens_high.setToolTip("High Sensitivity — Extended range person detection")
        self.btn_sens_high.clicked.connect(lambda: self._set_detection_sensitivity("high"))

        self.btn_sens_ultra = QPushButton("Ultra (Far)")
        self.btn_sens_ultra.setObjectName("optBtn")
        self.btn_sens_ultra.setCheckable(True)
        self.btn_sens_ultra.setChecked(True)
        self.btn_sens_ultra.setMinimumWidth(0)
        self.btn_sens_ultra.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_sens_ultra.setToolTip(
            "Ultra Far-Range Sensitivity — Detects people even far from the camera (down to 4% frame height)"
        )
        self.btn_sens_ultra.clicked.connect(lambda: self._set_detection_sensitivity("ultra"))

        sens_row.addWidget(self.btn_sens_std, 1)
        sens_row.addWidget(self.btn_sens_high, 1)
        sens_row.addWidget(self.btn_sens_ultra, 1)
        ai_vbox.addLayout(sens_row)

        ignore_row = QHBoxLayout()
        ignore_row.setSpacing(4)
        self.btn_ignore_person = QPushButton("Not a Person")
        self.btn_ignore_person.setObjectName("optBtn")
        self.btn_ignore_person.setMinimumWidth(0)
        self.btn_ignore_person.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_ignore_person.setIcon(lucide_icon("x", "#f87171", 12))
        self.btn_ignore_person.setToolTip(
            "Dismiss the currently detected object on this camera as 'Not a Person' so it won't false-trigger again"
        )
        self.btn_ignore_person.clicked.connect(self._ignore_active_detection)
        ignore_row.addWidget(self.btn_ignore_person, 1)

        self.btn_reset_ignored = QPushButton("Reset (0)")
        self.btn_reset_ignored.setObjectName("optBtn")
        self.btn_reset_ignored.setMinimumWidth(0)
        self.btn_reset_ignored.setIcon(lucide_icon("refresh", "#a1a1aa", 11))
        self.btn_reset_ignored.setEnabled(False)
        self.btn_reset_ignored.setToolTip("Clear all saved 'Not a Person' ignored false-positive objects on this camera")
        self.btn_reset_ignored.clicked.connect(self._clear_ignored_detections)
        ignore_row.addWidget(self.btn_reset_ignored)
        ai_vbox.addLayout(ignore_row)

        gpu_row = QHBoxLayout()
        gpu_row.setSpacing(4)
        gpu_info = get_gpu_capabilities()
        gpu_dev = gpu_info.get("device_name", "GPU")
        short_gpu = _short_gpu_label(gpu_dev)
        self.btn_gpu_accel = QPushButton(f"Use GPU Power ({short_gpu})")
        self.btn_gpu_accel.setObjectName("optBtn")
        self.btn_gpu_accel.setCheckable(True)
        self.btn_gpu_accel.setChecked(True)
        self.btn_gpu_accel.setMinimumWidth(0)
        self.btn_gpu_accel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.btn_gpu_accel.setIcon(lucide_icon("zap", "#10b981", 12))
        self.btn_gpu_accel.setToolTip(
            f"GPU Acceleration — {gpu_dev} ({gpu_info.get('summary', 'OpenCL + D3D11VA')}):\n"
            "• Runs high-resolution 640x360 OpenCL UMat CV + CLAHE contrast boost on GPU\n"
            "• Enables Direct3D 11 (D3D11VA) hardware video decoding"
        )
        self.btn_gpu_accel.clicked.connect(self._toggle_gpu_acceleration)
        gpu_row.addWidget(self.btn_gpu_accel, 1)
        ai_vbox.addLayout(gpu_row)
        d_vbox.addWidget(ai_sec)

        # 5. Master Audio Gain
        aud_sec = QFrame()
        aud_sec.setObjectName("drawerSection")
        aud_sec.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        aud_vbox = QVBoxLayout(aud_sec)
        aud_vbox.setContentsMargins(8, 7, 8, 7)
        aud_vbox.setSpacing(6)

        aud_hdr = QLabel("AUDIO MONITOR GAIN")
        aud_hdr.setObjectName("sectionTitle")
        aud_hdr.setFixedHeight(13)
        aud_vbox.addWidget(aud_hdr)

        vol_row = QHBoxLayout()
        vol_icon = QLabel()
        vol_icon.setPixmap(lucide_pixmap("volume_2", "#a1a1aa", 14))
        vol_row.addWidget(vol_icon)
        self.sld_volume = QSlider(Qt.Orientation.Horizontal)
        self.sld_volume.setMinimumWidth(0)
        self.sld_volume.setRange(0, 100)
        self.sld_volume.setValue(85)
        self.sld_volume.valueChanged.connect(self._on_volume_changed)
        vol_row.addWidget(self.sld_volume, 1)
        self.lbl_vol_pct = QLabel("85%")
        self.lbl_vol_pct.setStyleSheet("color: #71717a; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px;")
        vol_row.addWidget(self.lbl_vol_pct)
        aud_vbox.addLayout(vol_row)
        d_vbox.addWidget(aud_sec)

        d_vbox.addStretch(1)
        self.drawer_scroll.setWidget(drawer_content)
        drawer_outer_vbox.addWidget(self.drawer_scroll)
        ws_hbox.addWidget(self.drawer)

        self.workspace_stack = QStackedWidget()
        self.live_workspace = workspace
        self.review_workspace = FootageReviewWorkspace(self, self.workspace_stack)
        self.workspace_stack.addWidget(self.live_workspace)
        self.workspace_stack.addWidget(self.review_workspace)
        main_vbox.addWidget(self.workspace_stack, 1)

        # --- Slim Status Bar ---
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage(
            "Double-click any viewport to solo/maximize  |  Scroll wheel to digital zoom  |  F2 to rename camera"
        )
        self.status_version_lbl = QLabel(f"OmniSight v{APP_VERSION}")
        self.status_version_lbl.setStyleSheet(
            "color: #a1a1aa; font-family: 'Cascadia Mono', 'Consolas', monospace; "
            "font-size: 11px; font-weight: 600; padding: 0 8px;"
        )
        self.status_bar.addPermanentWidget(self.status_version_lbl)

        self.btn_support_coffee = QPushButton()
        self.btn_support_coffee.setIcon(lucide_icon("coffee", "#fbbf24", 14))
        self.btn_support_coffee.setIconSize(QSize(14, 14))
        self.btn_support_coffee.setFixedSize(28, 22)
        self.btn_support_coffee.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_support_coffee.setToolTip("Buy Me a Coffee ? Support via GCash / QR Ph")
        self.btn_support_coffee.setStyleSheet(
            "QPushButton { background-color: #271705; border: 1px solid #d97706; "
            "border-radius: 11px; padding: 0px; margin-right: 6px; }"
            "QPushButton:hover { background-color: #451a03; border-color: #fbbf24; }"
        )
        self.btn_support_coffee.clicked.connect(self._open_support_qr_dialog)
        self.status_bar.addPermanentWidget(self.btn_support_coffee)

    def _open_support_qr_dialog(self) -> None:
        dlg = SupportQRDialog(self)
        dlg.exec()

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        self._apply_responsive_layout()

    def _apply_responsive_layout(self) -> None:
        w = self.width()
        # 1. Responsive Right Control Drawer Width
        if hasattr(self, "drawer"):
            if w < 980:
                target_dw = 262
            elif w < 1220:
                target_dw = 278
            else:
                target_dw = 292
            if self.drawer.width() != target_dw:
                self.drawer.setFixedWidth(target_dw)
            if hasattr(self, "drawer_scroll"):
                self.drawer_scroll._sync_content_width()

        # 2. Responsive Top Header Bar Labels & Spacing
        if hasattr(self, "header_bar") and self.header_bar.layout() is not None:
            hb = self.header_bar.layout()
            if w < 980:
                hb.setContentsMargins(6, 0, 6, 0)
                hb.setSpacing(3)
                self.btn_ws_live.setText("Live")
                self.btn_ws_review.setText("Review")
                self.btn_add.setText("Add")
                self.btn_discover.setText("")
                self.btn_start_all.setText("")
                self.btn_stop_all.setText("")
                self.btn_drawer_toggle.setText("")
                self.btn_monitor.setText("")
            elif w < 1200:
                hb.setContentsMargins(8, 0, 8, 0)
                hb.setSpacing(5)
                self.btn_ws_live.setText("Live Wall")
                self.btn_ws_review.setText("Review")
                self.btn_add.setText("Add")
                self.btn_discover.setText("Scan")
                self.btn_start_all.setText("Connect")
                self.btn_stop_all.setText("Stop")
                self.btn_drawer_toggle.setText("Controls")
                self.btn_monitor.setText("Monitor")
            else:
                hb.setContentsMargins(12, 0, 12, 0)
                hb.setSpacing(6)
                self.btn_ws_live.setText("Live Wall")
                self.btn_ws_review.setText("Footage Review")
                self.btn_add.setText("Add Camera")
                self.btn_discover.setText("Discover")
                self.btn_start_all.setText("Connect All")
                self.btn_stop_all.setText("Stop All")
                self.btn_drawer_toggle.setText("Controls")
                self.btn_monitor.setText("Monitor Mode")

        # 3. Responsive Vertical Compactness for Control Panel on Shorter Screens
        h = self.height()
        compact_h = h < 860
        if hasattr(self, "_drawer_vbox"):
            self._drawer_vbox.setContentsMargins(7, 6 if compact_h else 8, 7, 6 if compact_h else 8)
            self._drawer_vbox.setSpacing(5 if compact_h else 7)
        if hasattr(self, "ptz_hint"):
            self.ptz_hint.setVisible(h >= 820)
        if hasattr(self, "btn_ptz_up"):
            ptz_btn_h = 26 if compact_h else 30
            for b in (self.btn_ptz_up, self.btn_ptz_left, self.btn_ptz_stop, self.btn_ptz_right, self.btn_ptz_down):
                b.setFixedSize(42 if compact_h else 44, ptz_btn_h)
        if hasattr(self, "_opt_vbox"):
            self._opt_vbox.setSpacing(4 if compact_h else 5)

        self._sync_all_quality_btn()
        self._sync_all_gpu_btn()
        if hasattr(self, "cam_list"):
            self._update_cam_list_height()

    def set_workspace_mode(self, mode: str) -> None:
        is_review = mode == "review"
        self.btn_ws_live.setChecked(not is_review)
        self.btn_ws_review.setChecked(is_review)
        for live_only_btn in (
            getattr(self, "btn_start_all", None),
            getattr(self, "btn_stop_all", None),
            getattr(self, "btn_all_quality", None),
            getattr(self, "btn_all_gpu", None),
            getattr(self, "btn_grid_auto", None),
            getattr(self, "btn_grid_1", None),
            getattr(self, "btn_grid_2", None),
            getattr(self, "btn_grid_3", None),
            getattr(self, "btn_drawer_toggle", None),
        ):
            if live_only_btn is not None:
                live_only_btn.setVisible(not is_review)
        if is_review:
            self.review_workspace.set_cameras(self.cameras, self.selected_cam_id)
            self.workspace_stack.setCurrentWidget(self.review_workspace)
            self.review_workspace.select_camera_and_load(self.selected_cam_id)
        else:
            self.review_workspace.stop_playback()
            self.workspace_stack.setCurrentWidget(self.live_workspace)

    def _switch_drawer_tab(self, tab_idx: int) -> None:
        self.btn_dtab_ptz.setChecked(tab_idx == 0)
        self.btn_dtab_ai.setChecked(tab_idx == 1)
        self.ptz_sec.setVisible(tab_idx == 0)
        self.opt_sec.setVisible(tab_idx == 0)
        self.ai_sec.setVisible(tab_idx == 1)
        if hasattr(self, "drawer_scroll"):
            self.drawer_scroll._sync_content_width()

    def open_sd_review_for_camera(self, cam_id: str) -> None:
        self.select_camera(cam_id)
        self.set_workspace_mode("review")

    def _make_ptz_button(self, icon_name: str, direction: str, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setIcon(lucide_icon(icon_name, "#e4e4e7", 15))
        btn.setIconSize(QSize(15, 15))
        btn.setFixedSize(44, 30)
        btn.setToolTip(tooltip)
        btn.pressed.connect(lambda d=direction: self._start_ptz_hold(d))
        btn.released.connect(self._stop_ptz_hold)
        return btn

    def _toggle_drawer(self, checked: Optional[bool] = None) -> None:
        visible = checked if isinstance(checked, bool) else not self.drawer.isVisible()
        if visible:
            self._update_cam_list_height()
        self.drawer.setVisible(visible)
        self.btn_drawer_toggle.setChecked(visible)
        if hasattr(self, "_ui_settings"):
            self._ui_settings.setValue("drawer_open", visible)
        if visible and hasattr(self, "drawer_scroll"):
            self.drawer_scroll._sync_content_width()

    def _restore_ui_preferences(self) -> None:
        self.btn_grid_auto.setChecked(self.grid_mode == 0)
        self.btn_grid_1.setChecked(self.grid_mode == 1)
        self.btn_grid_2.setChecked(self.grid_mode == 2)
        self.btn_grid_3.setChecked(self.grid_mode == 3)
        if not self._drawer_was_open:
            self.drawer.setVisible(False)
            self.btn_drawer_toggle.setChecked(False)
        saved_vol = self._ui_settings.value("master_volume", 85)
        try:
            vol_int = max(0, min(100, int(saved_vol)))
        except Exception:
            vol_int = 85
        self.sld_volume.setValue(vol_int)
        self._on_volume_changed(vol_int)

    def _add_demo_camera_quick(self) -> None:
        cfg = CameraConfig(
            cam_id=uuid.uuid4().hex[:8],
            name=f"Simulator Cam {len(self.cameras) + 1}",
            protocol="demo",
            ip="127.0.0.1",
            port=8800,
            quality=1,
        )
        self._save_custom_name(cfg)
        self._add_camera_internal(cfg, auto_start=True)
        self._save_cameras_list(record_custom_names=True)
        self.select_camera(cfg.cam_id)

    def _open_controls_for_camera(self, cam_id: str) -> None:
        self.select_camera(cam_id)
        if not self.drawer.isVisible():
            self._toggle_drawer(True)

    # --- Persistence & Initialization ---
    def _load_custom_names(self) -> Dict[str, str]:
        if USER_NAMES_FILE.exists():
            try:
                data = json.loads(USER_NAMES_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {str(k): str(v) for k, v in data.items() if v}
            except Exception:
                pass
        return {}

    def _save_custom_name(self, cfg: CameraConfig) -> None:
        if not cfg.name or not cfg.name.strip():
            return
        names = self._load_custom_names()
        clean_name = cfg.name.strip()
        if cfg.cam_id:
            names[f"id:{cfg.cam_id}"] = clean_name
        if cfg.device_id:
            names[f"dev:{cfg.device_id}"] = clean_name
        if cfg.ip:
            names[f"ip:{cfg.ip}"] = clean_name
        try:
            USER_DATA_DIR.mkdir(parents=True, exist_ok=True)
            tmp_file = USER_NAMES_FILE.with_suffix(".tmp")
            tmp_file.write_text(json.dumps(names, indent=2, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp_file, USER_NAMES_FILE)
        except Exception:
            pass

    def _load_cameras(self) -> None:
        loaded: List[CameraConfig] = []
        needs_dpapi_migration = False
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            raw_pw = item.get("password", "")
                            raw_rtsp = item.get("rtsp_url", "")
                            if (isinstance(raw_pw, str) and raw_pw and not raw_pw.startswith("dpapi:")) or (
                                isinstance(raw_rtsp, str) and raw_rtsp and not raw_rtsp.startswith("dpapi:")
                            ):
                                needs_dpapi_migration = True
                        loaded.append(CameraConfig.from_dict(item))
            except Exception:
                pass

        custom_names = self._load_custom_names()
        names_restored = False
        for cfg in loaded:
            saved_name = (
                custom_names.get(f"id:{cfg.cam_id}")
                or (custom_names.get(f"dev:{cfg.device_id}") if cfg.device_id else None)
                or (custom_names.get(f"ip:{cfg.ip}") if cfg.ip else None)
            )
            if saved_name and saved_name != cfg.name:
                cfg.name = saved_name
                names_restored = True

        if (names_restored or needs_dpapi_migration) and loaded:
            self._save_cameras_list(loaded)

        for cfg in loaded:
            self._add_camera_internal(cfg, auto_start=True)

        if self.cameras:
            self.select_camera(self.cameras[0].cam_id)
        else:
            self._update_camera_count_badge()
            self._rebuild_grid()

    def _save_cameras_list(
        self,
        cam_list: Optional[List[CameraConfig]] = None,
        record_custom_names: bool = False,
    ) -> None:
        target = cam_list if cam_list is not None else self.cameras
        if record_custom_names:
            for c in target:
                self._save_custom_name(c)

        payload = json.dumps([c.to_dict() for c in target], indent=2, ensure_ascii=False)
        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = CONFIG_FILE.with_suffix(".tmp")
            tmp_path.write_text(payload, encoding="utf-8")
            os.replace(tmp_path, CONFIG_FILE)
        except Exception:
            try:
                CONFIG_FILE.write_text(payload, encoding="utf-8")
            except Exception:
                pass

    def _update_camera_count_badge(self) -> None:
        n = len(self.cameras)
        self.cam_count_badge.setText(f"{n} CAMERA" if n == 1 else f"{n} CAMERAS")
        self._update_cam_list_height()
        self._sync_all_quality_btn()
        self._sync_all_gpu_btn()
        if hasattr(self, "review_workspace"):
            self.review_workspace.set_cameras(self.cameras, self.selected_cam_id)

    def _sync_all_quality_btn(self) -> None:
        if not hasattr(self, "btn_all_quality"):
            return
        compact = self.width() < 1120
        pad = "0 7px" if compact else "0 10px"
        all_hd = bool(self.cameras) and all(c.quality == 1 for c in self.cameras)
        if all_hd:
            self.btn_all_quality.setText("HD" if compact else "All: HD")
            self.btn_all_quality.setToolTip("All cameras are streaming in HD — click to switch all cameras to SD")
            self.btn_all_quality.setStyleSheet(
                "QPushButton { background-color: #172554; border: 1px solid #2563eb; border-radius: 5px; min-width: 0px; "
                f"color: #93c5fd; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: {pad}; }}"
                "QPushButton:hover { background-color: #1e3a8a; border-color: #3b82f6; color: #ffffff; }"
            )
        else:
            self.btn_all_quality.setText("SD→HD" if compact else "All: SD → HD")
            self.btn_all_quality.setToolTip("Click to switch all cameras from SD to HD")
            self.btn_all_quality.setStyleSheet(
                "QPushButton { background-color: #16161b; border: 1px solid #2c2c35; border-radius: 5px; min-width: 0px; "
                f"color: #d4d4d8; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: {pad}; }}"
                "QPushButton:hover { background-color: #202028; border-color: #3b82f6; color: #ffffff; }"
            )

    def _sync_all_gpu_btn(self) -> None:
        if not hasattr(self, "btn_all_gpu"):
            return
        compact = self.width() < 1120
        pad = "0 7px" if compact else "0 10px"
        gpu_caps = get_gpu_capabilities()
        gpu_summary = gpu_caps.get("summary", "OpenCL + D3D11VA")
        any_gpu = not self.cameras or any(bool(getattr(c, "gpu_acceleration", True)) for c in self.cameras)
        if any_gpu:
            self.btn_all_gpu.setText("GPU" if compact else "GPU: ON")
            self.btn_all_gpu.setIcon(lucide_icon("zap", "#10b981", 13))
            self.btn_all_gpu.setToolTip(
                f"GPU Acceleration ACTIVE ({gpu_summary}) — High-res OpenCL person detection & D3D11 decode. Click to switch to CPU"
            )
            self.btn_all_gpu.setStyleSheet(
                "QPushButton { background-color: #064e3b; border: 1px solid #059669; border-radius: 5px; min-width: 0px; "
                f"color: #6ee7b7; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: {pad}; }}"
                "QPushButton:hover { background-color: #065f46; border-color: #10b981; color: #ffffff; }"
            )
        else:
            self.btn_all_gpu.setText("GPU" if compact else "GPU: OFF")
            self.btn_all_gpu.setIcon(lucide_icon("zap", "#71717a", 13))
            self.btn_all_gpu.setToolTip(
                f"GPU Acceleration OFF (CPU Mode) — Click to enable GPU power ({gpu_summary}) on all cameras"
            )
            self.btn_all_gpu.setStyleSheet(
                "QPushButton { background-color: #16161b; border: 1px solid #2c2c35; border-radius: 5px; min-width: 0px; "
                f"color: #a1a1aa; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: {pad}; }}"
                "QPushButton:hover { background-color: #202028; border-color: #10b981; color: #ffffff; }"
            )

    def _update_cam_list_height(self) -> None:
        self.cam_list.ensurePolished()
        count = self.cam_list.count()
        if count <= 0:
            self.cam_list.setFixedHeight(38)
            return
        total_h = 0
        sample_row_h = 32
        for i in range(count):
            row_h = self.cam_list.sizeHintForRow(i)
            eff_h = row_h if row_h > 0 else 32
            sample_row_h = eff_h
            total_h += eff_h
        padding = (self.cam_list.frameWidth() * 2) + 4
        win_h = self.height()
        max_rows = 3 if win_h < 760 else (4 if win_h < 900 else 6)
        max_visible_h = max_rows * sample_row_h + padding
        if total_h + padding > max_visible_h:
            self.cam_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
            self.cam_list.setFixedHeight(max_visible_h)
        else:
            self.cam_list.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
            self.cam_list.setFixedHeight(total_h + padding)

    def _add_camera_internal(self, cfg: CameraConfig, auto_start: bool = True) -> None:
        self.cameras.append(cfg)
        tile = CameraTileWidget(cfg, self.grid_container)
        tile.selected.connect(self.select_camera)
        tile.maximize_requested.connect(self.toggle_maximize_camera)
        tile.monitor_mode_requested.connect(self.toggle_monitor_mode)
        tile.rename_requested.connect(self.rename_camera_dialog)
        tile.edit_requested.connect(self._edit_camera_by_id)
        tile.delete_requested.connect(self._delete_camera_by_id)
        tile.controls_requested.connect(self._open_controls_for_camera)
        tile.review_sd_requested.connect(self.open_sd_review_for_camera)
        tile.config_changed.connect(self._on_tile_config_changed)
        tile.snapshot_saved.connect(
            lambda path: self.status_bar.showMessage(
                f"✓ Snapshot saved: {Path(path).name} (in captures/snapshots)", 6000
            )
        )
        tile.recording_toggled.connect(self._on_tile_recording_toggled)
        tile.reorder_requested.connect(self._on_camera_reordered)
        if self.monitor_mode:
            tile.set_monitor_mode(True)
        self.tiles[cfg.cam_id] = tile

        item = QListWidgetItem(lucide_icon("cctv", "#a1a1aa", 14), cfg.name)
        item.setSizeHint(QSize(0, 27))
        item.setData(Qt.ItemDataRole.UserRole, cfg.cam_id)
        self.cam_list.addItem(item)
        self._update_cam_list_height()

        self._update_camera_count_badge()
        self._rebuild_grid()
        if auto_start:
            tile.start_stream()

    def _rebuild_grid(self) -> None:
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.setVisible(False)

        for r in range(6):
            self.grid_layout.setRowStretch(r, 0)
            self.grid_layout.setColumnStretch(r, 0)

        if self.maximized_cam_id and self.maximized_cam_id in self.tiles:
            for cid, t in self.tiles.items():
                t.set_maximized_state(cid == self.maximized_cam_id)
            tile = self.tiles[self.maximized_cam_id]
            tile.setVisible(True)
            self.grid_layout.addWidget(tile, 0, 0)
            self.grid_layout.setRowStretch(0, 1)
            self.grid_layout.setColumnStretch(0, 1)
            return

        for t in self.tiles.values():
            t.set_maximized_state(False)

        count = len(self.cameras)
        if count == 0:
            empty_card = getattr(self, "_empty_state_widget", None)
            if empty_card is None:
                empty_card = QWidget(self.grid_container)
                ev = QVBoxLayout(empty_card)
                ev.setAlignment(Qt.AlignmentFlag.AlignCenter)
                card = QFrame()
                card.setFixedWidth(440)
                card.setStyleSheet(
                    "QFrame { background-color: #121216; border: 1px solid #27272d; border-radius: 10px; }"
                )
                cv = QVBoxLayout(card)
                cv.setContentsMargins(28, 28, 28, 28)
                cv.setSpacing(12)
                ico_lbl = QLabel()
                ico_lbl.setPixmap(lucide_pixmap("cctv", "#38bdf8", 32))
                ico_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                ico_lbl.setStyleSheet("border: none;")
                cv.addWidget(ico_lbl)
                title_lbl = QLabel("No Cameras Configured")
                title_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                title_lbl.setStyleSheet(
                    "color: #f4f4f5; font-size: 15px; font-weight: 700; border: none;"
                )
                cv.addWidget(title_lbl)
                sub_lbl = QLabel(
                    "Scan your local network for compatible cameras on TCP 8800 / UDP 10008, "
                    "add a camera manually, or launch an interactive simulator feed."
                )
                sub_lbl.setWordWrap(True)
                sub_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
                sub_lbl.setStyleSheet("color: #a1a1aa; font-size: 12px; border: none;")
                cv.addWidget(sub_lbl)
                btn_row = QHBoxLayout()
                btn_row.setSpacing(8)
                b_disc = QPushButton("Discover LAN")
                b_disc.setObjectName("primaryBtn")
                b_disc.setIcon(lucide_icon("radar", "#ffffff", 14))
                b_disc.setFixedHeight(32)
                b_disc.clicked.connect(self.discover_cameras_dialog)
                btn_row.addWidget(b_disc)
                b_add = QPushButton("Add Manually")
                b_add.setIcon(lucide_icon("plus", "#e4e4e7", 14))
                b_add.setFixedHeight(32)
                b_add.clicked.connect(self.add_camera_dialog)
                btn_row.addWidget(b_add)
                b_sim = QPushButton("Simulator")
                b_sim.setIcon(lucide_icon("play", "#10b981", 13))
                b_sim.setFixedHeight(32)
                b_sim.clicked.connect(self._add_demo_camera_quick)
                btn_row.addWidget(b_sim)
                cv.addLayout(btn_row)
                ev.addWidget(card)
                self._empty_state_widget = empty_card
            empty_card.setVisible(True)
            self.grid_layout.addWidget(empty_card, 0, 0)
            self.grid_layout.setRowStretch(0, 1)
            self.grid_layout.setColumnStretch(0, 1)
            return

        # Smart Auto Layout:
        # - 1 camera: full 1x1
        # - 2 cameras: side-by-side 1x2
        # - 3 cameras: 2 on top, 1 spanning bottom (or 2x2 balanced)
        # - 4 cameras: 2x2
        # - 5..9 cameras: 3x3
        if self.grid_mode == 0:
            if count == 1:
                cols = 1
            elif count <= 4:
                cols = 2
            else:
                cols = 3
        else:
            cols = max(1, self.grid_mode)

        for idx, cfg in enumerate(self.cameras):
            tile = self.tiles.get(cfg.cam_id)
            if tile is None:
                continue
            tile.setVisible(True)
            row = idx // cols
            col = idx % cols
            # In Auto mode with 3 cameras, let the 3rd camera span across both columns cleanly or sit in cell (1,0)
            if self.grid_mode == 0 and count == 3 and idx == 2:
                self.grid_layout.addWidget(tile, row, 0, 1, 2)
            else:
                self.grid_layout.addWidget(tile, row, col)
            self.grid_layout.setRowStretch(row, 1)
            self.grid_layout.setColumnStretch(col, 1)

    def _on_camera_reordered(self, src_cam_id: str, tgt_cam_id: str) -> None:
        if src_cam_id == tgt_cam_id:
            return
        src_idx = next((i for i, c in enumerate(self.cameras) if c.cam_id == src_cam_id), -1)
        tgt_idx = next((i for i, c in enumerate(self.cameras) if c.cam_id == tgt_cam_id), -1)
        if src_idx < 0 or tgt_idx < 0:
            return
        moved = self.cameras.pop(src_idx)
        self.cameras.insert(tgt_idx, moved)
        self._rebuild_grid()
        self._sync_cam_list_order()
        self._save_cameras_list(record_custom_names=True)
        self.select_camera(src_cam_id)
        moved_tile = self.tiles.get(src_cam_id)
        if moved_tile:
            moved_tile.flash_landing_glow()
        self.status_bar.showMessage(f"✓ Repositioned '{moved.name}' to slot #{tgt_idx + 1} (saved).", 4000)

    def _sync_cam_list_order(self) -> None:
        self.cam_list.blockSignals(True)
        self.cam_list.clear()
        for cfg in self.cameras:
            item = QListWidgetItem(lucide_icon("cctv", "#a1a1aa", 14), cfg.name)
            item.setSizeHint(QSize(0, 27))
            item.setData(Qt.ItemDataRole.UserRole, cfg.cam_id)
            self.cam_list.addItem(item)
        self.cam_list.blockSignals(False)
        self._update_cam_list_height()

    def set_grid_mode(self, mode: int) -> None:
        self.maximized_cam_id = None
        self.grid_mode = mode
        if hasattr(self, "_ui_settings"):
            self._ui_settings.setValue("grid_mode", mode)
        self.btn_grid_auto.setChecked(mode == 0)
        self.btn_grid_1.setChecked(mode == 1)
        self.btn_grid_2.setChecked(mode == 2)
        self.btn_grid_3.setChecked(mode == 3)
        self._rebuild_grid()

    def toggle_maximize_camera(self, cam_id: str) -> None:
        self.select_camera(cam_id)
        if self.maximized_cam_id == cam_id:
            self.maximized_cam_id = None
        else:
            self.maximized_cam_id = cam_id
        self._rebuild_grid()

    def select_camera(self, cam_id: str) -> None:
        self.selected_cam_id = cam_id
        for cid, tile in self.tiles.items():
            tile.set_selected(cid == cam_id)

        for i in range(self.cam_list.count()):
            it = self.cam_list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == cam_id:
                self.cam_list.blockSignals(True)
                self.cam_list.setCurrentRow(i)
                self.cam_list.blockSignals(False)
                break

        cfg = self._get_config(cam_id)
        if cfg:
            self.lbl_selected_name.setText(cfg.name)
            stream_profile = "HD Stream" if cfg.quality == 1 else "SD Stream"
            sens_lbl = getattr(cfg, "detection_sensitivity", "ultra").upper()
            gpu_lbl = "GPU" if bool(getattr(cfg, "gpu_acceleration", True)) else "CPU"
            self.lbl_selected_meta.setText(f"{cfg.protocol.upper()}  |  {stream_profile}  |  {sens_lbl} ({gpu_lbl})")
            self._sync_optics_buttons(cfg)

    def _on_tile_config_changed(self) -> None:
        self._save_cameras_list()
        self._sync_all_quality_btn()
        self._sync_all_gpu_btn()
        if self.selected_cam_id:
            cfg = self._get_config(self.selected_cam_id)
            if cfg:
                self._sync_optics_buttons(cfg)
                stream_profile = "HD Stream" if cfg.quality == 1 else "SD Stream"
                sens_lbl = getattr(cfg, "detection_sensitivity", "ultra").upper()
                gpu_lbl = "GPU" if bool(getattr(cfg, "gpu_acceleration", True)) else "CPU"
                self.lbl_selected_meta.setText(f"{cfg.protocol.upper()}  |  {stream_profile}  |  {sens_lbl} ({gpu_lbl})")

    def _sync_optics_buttons(self, cfg: CameraConfig) -> None:
        img_mode = (cfg.image_mode or "auto").lower()
        light_mode = (cfg.light_mode or "auto").lower()
        sens = (getattr(cfg, "detection_sensitivity", "ultra") or "ultra").lower()
        chime_on = bool(getattr(cfg, "detection_sound", True))
        cd_sec = int(getattr(cfg, "detection_sound_cooldown", 10) or 10)
        if cd_sec not in (5, 10, 20):
            cd_sec = 10
        gpu_on = bool(getattr(cfg, "gpu_acceleration", True))
        self.btn_img_color.setChecked(img_mode == "color")
        self.btn_img_bw.setChecked(img_mode == "bw")
        self.btn_img_auto.setChecked(img_mode not in ("color", "bw"))
        self.btn_l_on.setChecked(light_mode == "on")
        self.btn_l_off.setChecked(light_mode == "off")
        self.btn_l_auto.setChecked(light_mode not in ("on", "off"))
        self.btn_flip.setChecked(bool(cfg.flipped))
        self.btn_person_det.setChecked(bool(getattr(cfg, "person_detection", True)))
        self.btn_det_sound.setChecked(chime_on)
        self.btn_det_sound.setIcon(lucide_icon("bell" if chime_on else "bell_off", "#f59e0b" if chime_on else "#71717a", 13))
        self.btn_cd_5.setChecked(cd_sec == 5)
        self.btn_cd_10.setChecked(cd_sec == 10)
        self.btn_cd_20.setChecked(cd_sec == 20)
        zone = CameraConfig.normalize_chime_zone(getattr(cfg, "chime_zone", None))
        tile_ref = self.tiles.get(cfg.cam_id)
        editing_zone = bool(tile_ref and getattr(tile_ref.canvas, "_editing_chime_zone", False))
        self.btn_chime_zone.blockSignals(True)
        self.btn_chime_zone.setChecked(editing_zone or zone is not None)
        self.btn_chime_zone.blockSignals(False)
        if editing_zone:
            self.btn_chime_zone.setText("Done (Area)")
            self.btn_chime_zone.setIcon(lucide_icon("check", "#10b981", 12))
        elif zone is not None:
            self.btn_chime_zone.setText("Area: Custom")
            self.btn_chime_zone.setIcon(lucide_icon("scan", "#f59e0b", 12))
        else:
            self.btn_chime_zone.setText("Set Chime Area")
            self.btn_chime_zone.setIcon(lucide_icon("scan", "#a1a1aa", 12))
        self.btn_reset_chime_zone.setEnabled(zone is not None)
        self.btn_sens_std.setChecked(sens == "standard")
        self.btn_sens_high.setChecked(sens == "high")
        self.btn_sens_ultra.setChecked(sens not in ("standard", "high"))
        ignored_count = len(getattr(cfg, "ignored_objects", None) or [])
        self.btn_reset_ignored.setText(f"Reset ({ignored_count})")
        self.btn_reset_ignored.setEnabled(ignored_count > 0)
        self.btn_gpu_accel.setChecked(gpu_on)
        self.btn_gpu_accel.setIcon(lucide_icon("zap", "#10b981" if gpu_on else "#71717a", 13))
        self._sync_all_gpu_btn()

    def _on_list_row_changed(self, row: int) -> None:
        if 0 <= row < self.cam_list.count():
            cam_id = self.cam_list.item(row).data(Qt.ItemDataRole.UserRole)
            if cam_id:
                self.select_camera(cam_id)

    def _get_config(self, cam_id: Optional[str]) -> Optional[CameraConfig]:
        if not cam_id:
            return None
        for c in self.cameras:
            if c.cam_id == cam_id:
                return c
        return None

    # --- Camera CRUD & Discovery ---
    def _show_cam_list_context_menu(self, pos: QPoint) -> None:
        item = self.cam_list.itemAt(pos)
        if item is not None:
            cam_id = item.data(Qt.ItemDataRole.UserRole)
            if cam_id:
                self.select_camera(cam_id)
        if not self.selected_cam_id:
            return
        menu = QMenu(self)
        act_rename = menu.addAction("Rename Camera (F2)")
        act_edit = menu.addAction("Connection Settings...")
        menu.addSeparator()
        act_del = menu.addAction("Remove Camera")
        chosen = menu.exec(self.cam_list.viewport().mapToGlobal(pos))
        if chosen == act_rename:
            self.rename_camera_dialog(self.selected_cam_id)
        elif chosen == act_edit:
            self.edit_selected_camera()
        elif chosen == act_del:
            self.delete_selected_camera()

    def rename_camera_dialog(self, cam_id: Optional[str] = None) -> None:
        target_id = cam_id or self.selected_cam_id
        cfg = self._get_config(target_id)
        if cfg is None:
            return
        self.select_camera(cfg.cam_id)
        new_name, ok = QInputDialog.getText(
            self,
            "Rename Camera",
            "Camera name:",
            QLineEdit.EchoMode.Normal,
            cfg.name,
        )
        if not ok:
            return
        clean = new_name.strip()
        if not clean or clean == cfg.name:
            return
        cfg.name = clean
        self._save_custom_name(cfg)
        self._save_cameras_list(record_custom_names=True)

        tile = self.tiles.get(cfg.cam_id)
        if tile:
            tile.update_config(cfg)

        for i in range(self.cam_list.count()):
            it = self.cam_list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == cfg.cam_id:
                it.setText(cfg.name)
                break

        self.select_camera(cfg.cam_id)
        self.status_bar.showMessage(f"Camera renamed to '{cfg.name}' and saved.", 5000)

    def add_camera_dialog(self) -> None:
        dlg = CameraConfigDialog(None, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            cfg = dlg.get_config()
            self._save_custom_name(cfg)
            self._add_camera_internal(cfg, auto_start=True)
            self._save_cameras_list(record_custom_names=True)
            self.select_camera(cfg.cam_id)

    def discover_cameras_dialog(self) -> None:
        dlg = DiscoveryDialog(self, existing_cameras=self.cameras)
        if dlg.exec() == QDialog.DialogCode.Accepted and dlg.selected_camera:
            found = dlg.selected_camera
            existing = next(
                (
                    c
                    for c in self.cameras
                    if (found.dev_id and c.device_id and found.dev_id.strip() == c.device_id.strip())
                    or (found.ip and c.ip and found.ip.strip() == c.ip.strip())
                ),
                None,
            )
            if existing is not None:
                self.select_camera(existing.cam_id)
                self.edit_selected_camera()
                return
            preset = CameraConfig(
                cam_id=uuid.uuid4().hex[:8],
                name=found.name or "Camera",
                protocol="lan8800",
                ip=found.ip,
                port=found.port,
                device_id=found.dev_id,
                username=found.dev_id or "admin",
                password="",
                quality=1,
            )
            cfg_dlg = CameraConfigDialog(preset, self)
            if cfg_dlg.exec() == QDialog.DialogCode.Accepted:
                final_cfg = cfg_dlg.get_config()
                self._save_custom_name(final_cfg)
                self._add_camera_internal(final_cfg, auto_start=True)
                self._save_cameras_list(record_custom_names=True)
                self.select_camera(final_cfg.cam_id)

    def _edit_camera_by_id(self, cam_id: str) -> None:
        self.select_camera(cam_id)
        self.edit_selected_camera()

    def edit_selected_camera(self) -> None:
        cfg = self._get_config(self.selected_cam_id)
        if cfg is None:
            return
        dlg = CameraConfigDialog(cfg, self)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            updated = dlg.get_config()
            for i, existing in enumerate(self.cameras):
                if existing.cam_id == updated.cam_id:
                    self.cameras[i] = updated
                    break
            self._save_custom_name(updated)
            self._save_cameras_list(record_custom_names=True)
            for i in range(self.cam_list.count()):
                it = self.cam_list.item(i)
                if it and it.data(Qt.ItemDataRole.UserRole) == updated.cam_id:
                    it.setText(updated.name)
                    break
            tile = self.tiles.get(updated.cam_id)
            if tile:
                tile.update_config(updated)
            self.select_camera(updated.cam_id)
            self.status_bar.showMessage(f"Saved configuration for '{updated.name}'.", 5000)

    def _delete_camera_by_id(self, cam_id: str) -> None:
        self.select_camera(cam_id)
        self.delete_selected_camera()

    def delete_selected_camera(self) -> None:
        cfg = self._get_config(self.selected_cam_id)
        if cfg is None:
            return
        ans = QMessageBox.question(
            self,
            "Remove Camera",
            f"Remove '{cfg.name}' from OmniSight?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if ans != QMessageBox.StandardButton.Yes:
            return

        cid = cfg.cam_id
        tile = self.tiles.pop(cid, None)
        if tile:
            tile.stop_stream()
            tile.deleteLater()

        self.cameras = [c for c in self.cameras if c.cam_id != cid]
        for i in range(self.cam_list.count()):
            it = self.cam_list.item(i)
            if it and it.data(Qt.ItemDataRole.UserRole) == cid:
                self.cam_list.takeItem(i)
                break

        if self.maximized_cam_id == cid:
            self.maximized_cam_id = None
        self._save_cameras_list()
        self._update_camera_count_badge()
        self._rebuild_grid()

        if self.cameras:
            self.select_camera(self.cameras[0].cam_id)
        else:
            self.selected_cam_id = None
            self.lbl_selected_name.setText("None Selected")
            self.lbl_selected_meta.setText("Click 'Add Camera' to configure a device.")

    def start_all_streams(self) -> None:
        for tile in self.tiles.values():
            tile.start_stream()

    def stop_all_streams(self) -> None:
        for tile in self.tiles.values():
            tile.stop_stream()

    def toggle_all_streams_quality(self) -> None:
        if not self.cameras:
            self.status_bar.showMessage("Add a camera first to switch stream quality.", 3000)
            return
        # If any camera is on SD (0), switch all cameras to HD (1); otherwise switch all to SD (0)
        target_q = 0 if all(c.quality == 1 for c in self.cameras) else 1
        for tile in self.tiles.values():
            tile.set_quality_profile(target_q, emit_change=False)
        self._save_cameras_list()
        self._sync_all_quality_btn()
        if self.selected_cam_id:
            cfg = self._get_config(self.selected_cam_id)
            if cfg:
                stream_profile = "HD Stream" if cfg.quality == 1 else "SD Stream"
                self.lbl_selected_meta.setText(f"{cfg.protocol.upper()}  |  {stream_profile}")
        profile_lbl = "HD (Main Stream)" if target_q == 1 else "SD (Sub Stream)"
        self.status_bar.showMessage(f"Switched all {len(self.cameras)} camera(s) to {profile_lbl}.", 4000)

    # --- PTZ / Light / Audio Controls ---
    def _active_tile(self) -> Optional[CameraTileWidget]:
        if not self.selected_cam_id:
            return None
        return self.tiles.get(self.selected_cam_id)

    def _send_ptz(self, direction: str) -> None:
        tile = self._active_tile()
        if tile and tile.worker:
            tile.worker.ptz_move(direction)

    def _start_ptz_hold(self, direction: str) -> None:
        self._held_ptz_dir = direction
        self._send_ptz(direction)
        if not self._ptz_repeat_timer.isActive():
            self._ptz_repeat_timer.start()

    def _on_ptz_repeat_tick(self) -> None:
        if self._held_ptz_dir:
            self._send_ptz(self._held_ptz_dir)
        else:
            self._ptz_repeat_timer.stop()

    def _stop_ptz_hold(self) -> None:
        was_holding = self._held_ptz_dir is not None
        self._held_ptz_dir = None
        if self._ptz_repeat_timer.isActive():
            self._ptz_repeat_timer.stop()
        if was_holding:
            self._send_ptz("stop")

    def _ptz_stop_and_reset_zoom(self) -> None:
        self._stop_ptz_hold()
        tile = self._active_tile()
        if tile:
            tile.canvas.reset_zoom()
            if tile.worker:
                tile.worker.ptz_move("stop")

    def _open_ptz_presets_popup(self) -> None:
        tile = self._active_tile()
        if not tile:
            self.status_bar.showMessage("Select or add a camera first to manage PTZ Presets", 3000)
            return
        self._stop_ptz_hold()

        popup = PTZPresetPopup(
            tile=tile,
            on_save_config=self._save_cameras_list,
            on_status_msg=lambda msg: self.status_bar.showMessage(msg, 4000),
            parent=self,
        )
        popup.adjustSize()

        # Anchor flyout immediately to the left of the inspector drawer, vertically aligned with the gimbal
        drawer_tl = self.drawer.mapToGlobal(QPoint(0, 0))
        btn_global = self.btn_ptz_stop.mapToGlobal(QPoint(0, 0))
        x = max(12, drawer_tl.x() - popup.width() - 8)
        y = max(48, btn_global.y() - popup.height() // 2 + self.btn_ptz_stop.height() // 2)
        screen = self.screen()
        if screen is not None:
            avail = screen.availableGeometry()
            if y + popup.height() > avail.bottom() - 12:
                y = max(avail.top() + 12, avail.bottom() - popup.height() - 12)
        popup.move(x, y)
        popup.exec()

    def _send_light(self, mode: str) -> None:
        tile = self._active_tile()
        if not tile:
            return
        tile.config.light_mode = mode
        if mode == "auto":
            tile.config.image_mode = "auto"
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        if tile.worker:
            tile.worker.set_light(mode)
            self.status_bar.showMessage(f"Floodlight set to {mode.upper()} on {tile.config.name}", 3000)

    def _send_image_mode(self, mode: str) -> None:
        tile = self._active_tile()
        if not tile:
            return
        tile.config.image_mode = mode
        if mode == "auto":
            tile.config.light_mode = "auto"
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        if tile.worker:
            tile.worker.set_image_color(mode)
            label = "IR B&W" if mode == "bw" else mode.upper()
            self.status_bar.showMessage(f"Night Vision / Sensor set to {label} on {tile.config.name}", 3000)

    def _send_flip(self) -> None:
        tile = self._active_tile()
        if not tile:
            return
        tile.config.flipped = not bool(tile.config.flipped)
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        if tile.worker:
            tile.worker.flip_image()
            state_str = "ON" if tile.config.flipped else "OFF"
            self.status_bar.showMessage(f"180-degree orientation flip {state_str} on {tile.config.name}", 3000)

    def _toggle_person_detection(self) -> None:
        tile = self._active_tile()
        if not tile:
            return
        tile.config.person_detection = not bool(getattr(tile.config, "person_detection", True))
        if tile.worker:
            tile.worker.set_detection_options(
                enabled=tile.config.person_detection,
                sensitivity=getattr(tile.config, "detection_sensitivity", "ultra"),
                gpu_acceleration=bool(getattr(tile.config, "gpu_acceleration", True)),
            )
        if not tile.config.person_detection:
            tile.canvas.set_detections([], force_clear=True)
            tile._on_detections(tile.config.cam_id, [])
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        state_str = "ENABLED" if tile.config.person_detection else "DISABLED"
        self.status_bar.showMessage(f"Person Detection Alert {state_str} on {tile.config.name}", 3000)

    def _toggle_detection_sound(self) -> None:
        tile = self._active_tile()
        if not tile:
            return
        new_state = not bool(getattr(tile.config, "detection_sound", True))
        tile.config.detection_sound = new_state
        tile._update_chime_btn()
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        if new_state:
            DetectionChimePlayer.play_chime(force=True)
        cd_sec = int(getattr(tile.config, "detection_sound_cooldown", 10) or 10)
        state_str = f"ENABLED ({cd_sec}s cooldown)" if new_state else "MUTED"
        self.status_bar.showMessage(f"Person Detection Chime {state_str} on {tile.config.name}", 3000)

    def _set_detection_sound_cooldown(self, cooldown_sec: int) -> None:
        cd = int(cooldown_sec) if int(cooldown_sec) in (5, 10, 20) else 10
        tile = self._active_tile()
        if not tile:
            return
        tile.config.detection_sound_cooldown = cd
        tile.config.detection_sound = True
        tile._update_chime_btn()
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        DetectionChimePlayer.play_chime(force=True)
        self.status_bar.showMessage(
            f"Person Detection Chime Cooldown set to {cd}s on {tile.config.name}",
            3000,
        )

    def _toggle_chime_zone_editor(self) -> None:
        tile = self._active_tile()
        if not tile:
            self.status_bar.showMessage("Select a camera first to set its Chime Trigger Area", 3000)
            return
        for cid, other in self.tiles.items():
            if cid != tile.config.cam_id and getattr(other.canvas, "_editing_chime_zone", False):
                other.canvas.set_chime_zone_edit_mode(False)
        tile.toggle_chime_zone_editor()
        editing = bool(getattr(tile.canvas, "_editing_chime_zone", False))
        self._sync_optics_buttons(tile.config)
        if editing:
            self.status_bar.showMessage(
                f"Click and drag a box on '{tile.config.name}' footage to set the Chime Trigger Area (e.g. Gate). Click Done or press Esc when finished.",
                6000,
            )
        else:
            zone = CameraConfig.normalize_chime_zone(getattr(tile.config, "chime_zone", None))
            desc = "Custom Area saved" if zone is not None else "Whole Frame (Default)"
            self.status_bar.showMessage(f"Chime Trigger Area on {tile.config.name}: {desc}", 4000)

    def _reset_chime_zone(self) -> None:
        tile = self._active_tile()
        if not tile:
            return
        tile.reset_chime_zone()
        self._sync_optics_buttons(tile.config)
        self.status_bar.showMessage(
            f"✓ Reset Chime Trigger Area to Whole Frame on {tile.config.name}.",
            4000,
        )

    def _ignore_active_detection(self) -> None:
        tile = self._active_tile()
        if not tile:
            self.status_bar.showMessage("Select a camera first to mark an object as 'Not a Person'", 3000)
            return
        if tile.ignore_detection_box(None):
            cnt = len(getattr(tile.config, "ignored_objects", None) or [])
            self.status_bar.showMessage(
                f"✓ Marked current detection as 'Not a Person' on {tile.config.name} ({cnt} ignored).",
                4500,
            )
        else:
            self.status_bar.showMessage(
                f"No active person detection on {tile.config.name} right now (or hover red bracket & click '× Not a Person').",
                4500,
            )

    def _clear_ignored_detections(self) -> None:
        tile = self._active_tile()
        if not tile:
            return
        tile.clear_ignored_objects()
        self.status_bar.showMessage(
            f"✓ Cleared all saved 'Not a Person' ignored objects on {tile.config.name}.",
            4000,
        )

    def _set_detection_sensitivity(self, sensitivity: str) -> None:
        sens = (sensitivity or "ultra").lower()
        tile = self._active_tile()
        if not tile:
            return
        tile.config.detection_sensitivity = sens
        tile.config.person_detection = True
        if tile.worker:
            tile.worker.set_detection_options(
                enabled=True,
                sensitivity=sens,
                gpu_acceleration=bool(getattr(tile.config, "gpu_acceleration", True)),
            )
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        stream_profile = "HD Stream" if tile.config.quality == 1 else "SD Stream"
        gpu_lbl = "GPU" if bool(getattr(tile.config, "gpu_acceleration", True)) else "CPU"
        self.lbl_selected_meta.setText(f"{tile.config.protocol.upper()}  |  {stream_profile}  |  {sens.upper()} ({gpu_lbl})")
        label_map = {
            "standard": "Standard (Close/Mid Range)",
            "high": "High (Extended Range)",
            "ultra": "Ultra (Far Range — Detects Distant People)",
        }
        self.status_bar.showMessage(
            f"Person Detection Sensitivity set to {label_map.get(sens, sens.upper())} on {tile.config.name}",
            4000,
        )

    def _toggle_gpu_acceleration(self) -> None:
        tile = self._active_tile()
        if not tile:
            return
        new_gpu = not bool(getattr(tile.config, "gpu_acceleration", True))
        tile.config.gpu_acceleration = new_gpu
        if tile.worker:
            tile.worker.set_detection_options(
                enabled=bool(getattr(tile.config, "person_detection", True)),
                sensitivity=getattr(tile.config, "detection_sensitivity", "ultra"),
                gpu_acceleration=new_gpu,
            )
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        stream_profile = "HD Stream" if tile.config.quality == 1 else "SD Stream"
        sens_lbl = getattr(tile.config, "detection_sensitivity", "ultra").upper()
        gpu_lbl = "GPU" if new_gpu else "CPU"
        self.lbl_selected_meta.setText(f"{tile.config.protocol.upper()}  |  {stream_profile}  |  {sens_lbl} ({gpu_lbl})")
        gpu_summary = get_gpu_capabilities().get("summary", "OpenCL + D3D11VA")
        state_str = f"ENABLED ({gpu_summary})" if new_gpu else "DISABLED (CPU Mode)"
        self.status_bar.showMessage(f"GPU Acceleration {state_str} on {tile.config.name}", 4000)

    def toggle_all_gpu_acceleration(self) -> None:
        if not self.cameras:
            self.status_bar.showMessage("Add a camera first to toggle GPU acceleration.", 3000)
            return
        target_gpu = not all(bool(getattr(c, "gpu_acceleration", True)) for c in self.cameras)
        for c in self.cameras:
            c.gpu_acceleration = target_gpu
        for tile in self.tiles.values():
            tile.config.gpu_acceleration = target_gpu
            if tile.worker:
                tile.worker.set_detection_options(
                    enabled=bool(getattr(tile.config, "person_detection", True)),
                    sensitivity=getattr(tile.config, "detection_sensitivity", "ultra"),
                    gpu_acceleration=target_gpu,
                )
        self._save_cameras_list()
        if self.selected_cam_id:
            cfg = self._get_config(self.selected_cam_id)
            if cfg:
                self._sync_optics_buttons(cfg)
                stream_profile = "HD Stream" if cfg.quality == 1 else "SD Stream"
                sens_lbl = getattr(cfg, "detection_sensitivity", "ultra").upper()
                gpu_lbl = "GPU" if target_gpu else "CPU"
                self.lbl_selected_meta.setText(f"{cfg.protocol.upper()}  |  {stream_profile}  |  {sens_lbl} ({gpu_lbl})")
        else:
            self._sync_all_gpu_btn()
        gpu_summary = get_gpu_capabilities().get("summary", "OpenCL + D3D11VA")
        state_str = f"ENABLED ({gpu_summary})" if target_gpu else "DISABLED (CPU Mode)"
        self.status_bar.showMessage(f"GPU Acceleration {state_str} across all {len(self.cameras)} camera(s).", 4000)

    def _on_volume_changed(self, value: int) -> None:
        self.lbl_vol_pct.setText(f"{value}%")
        if hasattr(self, "_ui_settings"):
            self._ui_settings.setValue("master_volume", int(value))
        vol = value / 100.0
        DetectionChimePlayer.set_volume(vol)
        for tile in self.tiles.values():
            tile.audio_out.set_volume(vol)
        if hasattr(self, "review_workspace") and hasattr(self.review_workspace, "audio_out"):
            self.review_workspace.audio_out.set_volume(vol)

    def _on_tile_recording_toggled(self, cam_id: str, is_rec: bool, path: str) -> None:
        cfg = self._get_config(cam_id)
        name = cfg.name if cfg else cam_id
        if is_rec:
            self.status_bar.showMessage(f"Recording started ({name}): {path}", 5000)
        else:
            self.status_bar.showMessage(f"Recording saved ({name}): {path}", 6000)

    @staticmethod
    def _open_folder(folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        if os.name == "nt":
            os.startfile(str(folder))

    def set_monitor_mode(self, enabled: bool) -> None:
        if self.monitor_mode == enabled:
            return
        self.monitor_mode = enabled
        if enabled:
            self._drawer_was_open = self.drawer.isVisible()
            self.header_bar.setVisible(False)
            self.drawer.setVisible(False)
            self.status_bar.setVisible(False)
            self.grid_container.setStyleSheet("background-color: #000000;")
            self.grid_layout.setContentsMargins(0, 0, 0, 0)
            self.grid_layout.setSpacing(1)
            for tile in self.tiles.values():
                tile.set_monitor_mode(True)
            self.showFullScreen()
        else:
            if self.isFullScreen():
                self.showNormal()
            self.header_bar.setVisible(True)
            self.status_bar.setVisible(True)
            self.drawer.setVisible(self._drawer_was_open)
            self.btn_drawer_toggle.setChecked(self._drawer_was_open)
            self.grid_container.setStyleSheet("background-color: #09090b;")
            self.grid_layout.setContentsMargins(6, 6, 6, 6)
            self.grid_layout.setSpacing(6)
            for tile in self.tiles.values():
                tile.set_monitor_mode(False)

    def toggle_monitor_mode(self) -> None:
        self.set_monitor_mode(not self.monitor_mode)

    def toggle_fullscreen(self) -> None:
        self.toggle_monitor_mode()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        in_review = hasattr(self, "workspace_stack") and self.workspace_stack.currentIndex() == 1
        if in_review and hasattr(self, "review_workspace"):
            if event.key() == Qt.Key.Key_Space:
                self.review_workspace.toggle_play_pause()
                return
            if event.key() == Qt.Key.Key_S:
                self.review_workspace.save_snapshot()
                return
            if event.key() == Qt.Key.Key_Left:
                self.review_workspace.skip_seconds(-10)
                return
            if event.key() == Qt.Key.Key_Right:
                self.review_workspace.skip_seconds(10)
                return
            if event.key() == Qt.Key.Key_F11:
                self.toggle_monitor_mode()
                return
            super().keyPressEvent(event)
            return

        if event.key() == Qt.Key.Key_F2:
            self.rename_camera_dialog()
            return
        if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_S):
            tile = self._active_tile()
            if tile:
                tile.take_snapshot()
                return
        if event.key() == Qt.Key.Key_F11:
            self.toggle_monitor_mode()
            return
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            for tile in self.tiles.values():
                if getattr(tile.canvas, "_editing_chime_zone", False):
                    tile.canvas.set_chime_zone_edit_mode(False)
                    return
        if event.key() == Qt.Key.Key_Escape:
            for tile in self.tiles.values():
                if getattr(tile.canvas, "_editing_chime_zone", False):
                    tile.canvas.set_chime_zone_edit_mode(False)
                    return
            if self.monitor_mode or self.isFullScreen():
                self.set_monitor_mode(False)
                return
            if self.maximized_cam_id is not None:
                self.maximized_cam_id = None
                self._rebuild_grid()
                return
        if not event.isAutoRepeat():
            if event.key() == Qt.Key.Key_Up:
                self._start_ptz_hold("up")
                return
            if event.key() == Qt.Key.Key_Down:
                self._start_ptz_hold("down")
                return
            if event.key() == Qt.Key.Key_Left:
                self._start_ptz_hold("left")
                return
            if event.key() == Qt.Key.Key_Right:
                self._start_ptz_hold("right")
                return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event: QKeyEvent) -> None:
        if not event.isAutoRepeat() and event.key() in (
            Qt.Key.Key_Up,
            Qt.Key.Key_Down,
            Qt.Key.Key_Left,
            Qt.Key.Key_Right,
        ):
            self._stop_ptz_hold()
            return
        super().keyReleaseEvent(event)

    def closeEvent(self, event) -> None:
        self._save_cameras_list()
        if hasattr(self, "review_workspace"):
            self.review_workspace.stop_all()
        self.stop_all_streams()
        super().closeEvent(event)


def main() -> None:
    app = QApplication(sys.argv)
    app.setApplicationName("OmniSight")
    win = OmniSightMainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
