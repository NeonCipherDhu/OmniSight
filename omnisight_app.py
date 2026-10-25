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
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from PyQt6.QtCore import (
    QByteArray,
    QDate,
    QEvent,
    QMimeData,
    QPoint,
    QRect,
    QRectF,
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
    LanCameraDiscovery,
    RecordingSegment,
    get_local_ipv4_addresses,
)

BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
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
}

_ICON_CACHE: Dict[str, QIcon] = {}


def lucide_pixmap(name: str, color: str = "#e4e4e7", size: int = 16, stroke_width: float = 1.85) -> QPixmap:
    body = LUCIDE_PATHS.get(name, LUCIDE_PATHS["cctv"])
    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke_width}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )
    dpr = 2.0
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        dpr = max(2.0, float(app.primaryScreen().devicePixelRatio()))
    px_size = int(size * dpr)
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
    Renders a composite 34x16 HiDPI QIcon for SD card clip rows:
    - Left slot (14x14): event type icon (`alert_triangle`, `zap`, or `film`)
    - Right slot (14x14): green circular checkmark badge (`#10b981`) when the clip has a local backup on disk
    Using a fixed 34px width ensures all clip timestamps stay perfectly aligned whether backed up or not.
    """
    key = f"cliprow:{event_icon_name}:{event_color}:{int(is_backed_up)}"
    cached = _ICON_CACHE.get(key)
    if cached is not None:
        return cached

    dpr = 2.0
    app = QApplication.instance()
    if app is not None and app.primaryScreen() is not None:
        dpr = max(2.0, float(app.primaryScreen().devicePixelRatio()))

    w, h = 34, 16
    pixmap = QPixmap(int(w * dpr), int(h * dpr))
    pixmap.fill(Qt.GlobalColor.transparent)
    pixmap.setDevicePixelRatio(dpr)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

    # 1. Event icon on the left
    ev_px = lucide_pixmap(event_icon_name, color=event_color, size=14, stroke_width=2.0)
    painter.drawPixmap(1, 1, ev_px)

    # 2. Local backup green checkmark badge on the right
    if is_backed_up:
        painter.setPen(QPen(QColor("#10b981"), 1.2))
        painter.setBrush(QColor("#064e3b"))
        painter.drawEllipse(QRectF(19.0, 1.5, 13.0, 13.0))
        chk_px = lucide_pixmap("check", color="#34d399", size=10, stroke_width=2.8)
        painter.drawPixmap(20, 3, chk_px)

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
    padding: 5px 10px;
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
    padding: 4px 6px;
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
    padding: 4px 6px;
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
    padding: 4px 9px;
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
    background-color: #0c0c0f;
    border: 1px solid #18181e;
    color: #3f3f46;
}
QPushButton#optBtn {
    background-color: #18181c;
    border: 1px solid #27272d;
    border-radius: 5px;
    padding: 5px 6px;
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
    padding: 6px 8px;
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
    padding: 6px 8px;
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
        self.resize(740, 420)
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
        self.resize(460, 420)
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
            gpu_acceleration=self.chk_gpu.isChecked(),
            ptz_presets=list(self._initial.ptz_presets) if (self._initial and self._initial.ptz_presets) else [],
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

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(240, 150)
        self._qimage: Optional[QImage] = None
        self._status_text: str = "Standby"
        self._is_connected: bool = False
        self._download_progress: Optional[int] = None
        self._zoom: float = 1.0
        self._pan_offset = QPoint(0, 0)
        self._drag_last: Optional[QPoint] = None
        self._mouse_down_pos: Optional[QPoint] = None
        self._detections: List[tuple] = []
        self._flash_step: int = 0
        self._flash_timer = QTimer(self)
        self._flash_timer.setInterval(95)
        self._flash_timer.timeout.connect(self._on_flash_tick)

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

    def set_detections(self, boxes: list) -> None:
        self._detections = list(boxes) if boxes else []
        if self._detections:
            if not self._flash_timer.isActive():
                self._flash_step = 0
                self._flash_timer.start()
        else:
            if self._flash_timer.isActive():
                self._flash_timer.stop()
        self.update()

    def set_frame(self, image: QImage) -> None:
        self._qimage = image
        if self._anim_timer.isActive():
            self._anim_timer.stop()
        self.update()

    def set_status(self, text: str, connected: bool) -> None:
        self._status_text = text
        self._is_connected = connected
        low = (text or "").lower()
        if not any(w in low for w in ("downloading", "backing up", "exporting")):
            self._download_progress = None
        if not connected:
            self._qimage = None
            self.set_detections([])
            if not self._anim_timer.isActive():
                self._anim_timer.start()
        elif self._anim_timer.isActive():
            self._anim_timer.stop()
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

    def resizeEvent(self, event: QResizeEvent) -> None:
        super().resizeEvent(event)
        if hasattr(self, "_toast") and self._toast.isVisible():
            self._toast._reposition()

    def wheelEvent(self, event: QWheelEvent) -> None:
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
        if event.button() == Qt.MouseButton.LeftButton:
            if self._zoom > 1.0:
                self._drag_last = event.pos()
            else:
                self._mouse_down_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
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
        self._drag_last = None
        self._mouse_down_pos = None
        super().mouseReleaseEvent(event)

    def mouseDoubleClickEvent(self, event: QMouseEvent) -> None:
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

                        bx = int(target.left() + x1n * target.width())
                        by = int(target.top() + y1n * target.height())
                        bw = max(16, int((x2n - x1n) * target.width()))
                        bh = max(24, int((y2n - y1n) * target.height()))
                        box_rect = QRect(bx, by, bw, bh)

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
        else:
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
            is_error = any(w in st for w in ("error", "failed", "invalid", "lost"))
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
            is_stopped = any(w in st for w in ("stopped", "standby", "offline", "idle")) and not is_error
            is_connecting = not is_stopped and not is_error and not is_downloading and not is_review_idle

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
                painter.drawText(QRect(rect.left() + 20, cy + 66, rect.width() - 40, 20), Qt.AlignmentFlag.AlignCenter, 'Click "Connect" below to view live feed')

        # Camera shutter flash effect
        if self._shutter_alpha > 0.01:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(255, 255, 255, int(self._shutter_alpha * 220)))
            painter.drawRect(rect)

        painter.end()


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
        self.audio_out = LiveAudioOutput(sample_rate=8000, parent=self)
        self._is_selected = False
        self._is_maximized = False
        self._monitor_mode = False
        self._person_detected = False
        self._last_saved_snapshot: Optional[str] = None
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

        self.person_lbl = QLabel("PERSON")
        self.person_lbl.setStyleSheet(
            "background-color: #7f1d1d; color: #ffffff; border: 1px solid #ef4444; "
            "border-radius: 3px; padding: 1px 6px; font-family: 'Cascadia Mono', 'Consolas', monospace; "
            "font-weight: 700; font-size: 10px;"
        )
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
        self.canvas.clicked.connect(lambda: self.selected.emit(self.config.cam_id))
        self.canvas.double_clicked.connect(lambda: self.maximize_requested.emit(self.config.cam_id))
        self.canvas.drag_requested.connect(self._start_trello_drag)
        self.canvas.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.canvas.customContextMenuRequested.connect(self._show_context_menu)
        self.canvas.set_camera_info(self.config.name, "Live Feed")
        self._root_layout.addWidget(self.canvas, 1)

        # --- Bottom Control Bar (34px) ---
        self.ctrl_bar = QWidget()
        self.ctrl_bar.setFixedHeight(34)
        self.ctrl_bar.setStyleSheet("background-color: #0e0e12; border-top: 1px solid #1a1a20;")
        c_layout = QHBoxLayout(self.ctrl_bar)
        c_layout.setContentsMargins(8, 0, 8, 0)
        c_layout.setSpacing(4)

        self.btn_power = QPushButton("Connect")
        self.btn_power.setObjectName("ghostBtn")
        self.btn_power.setIcon(lucide_icon("play", "#38bdf8", 13))
        self.btn_power.setIconSize(QSize(13, 13))
        self.btn_power.setFixedSize(76, 24)
        self.btn_power.setToolTip("Connect to Live Stream")
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

    def _show_context_menu(self, pos: QPoint) -> None:
        self.selected.emit(self.config.cam_id)
        menu = QMenu(self)
        act_sd = menu.addAction("Review SD Card Footage & Anomalies...")
        menu.addSeparator()
        act_mon = menu.addAction(
            "Exit Monitor Mode (Esc / F11)" if self._monitor_mode else "Monitor Mode — Pure Footage Only (F11)"
        )
        act_max = menu.addAction("Solo / Restore This Camera (Double-Click)")
        act_ptz = menu.addAction("PTZ & Camera Controls")
        menu.addSeparator()
        act_snap = menu.addAction("Take Photo / Snapshot (Space / S)")
        act_folder = menu.addAction("Open Snapshots Folder")
        menu.addSeparator()
        act_rename = menu.addAction("Rename Camera (F2)")
        act_edit = menu.addAction("Connection Settings...")
        menu.addSeparator()
        act_del = menu.addAction("Remove Camera")
        chosen = menu.exec(self.canvas.mapToGlobal(pos))
        if chosen == act_sd:
            self.review_sd_requested.emit(self.config.cam_id)
        elif chosen == act_mon:
            self.monitor_mode_requested.emit()
        elif chosen == act_max:
            self.maximize_requested.emit(self.config.cam_id)
        elif chosen == act_ptz:
            self.controls_requested.emit(self.config.cam_id)
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
        self._update_quality_btn()
        if self.worker is not None and not stream_changed:
            self.worker.config.name = config.name
            self.worker.set_detection_options(
                enabled=bool(getattr(config, "person_detection", True)),
                sensitivity=getattr(config, "detection_sensitivity", "ultra"),
                gpu_acceleration=bool(getattr(config, "gpu_acceleration", True)),
            )
        if was_running and stream_changed:
            self.start_stream()

    def is_streaming(self) -> bool:
        return self.worker is not None and self.worker.isRunning()

    def toggle_stream(self) -> None:
        self.selected.emit(self.config.cam_id)
        if self.is_streaming():
            self.stop_stream()
        else:
            self.start_stream()

    def start_stream(self) -> None:
        if self.is_streaming():
            return
        self.worker = CameraStreamWorker(self.config, self)
        self.worker.frame_ready.connect(self._on_frame)
        self.worker.detections_ready.connect(self._on_detections)
        self.worker.audio_ready.connect(self._on_audio)
        self.worker.status_changed.connect(self._on_status)
        self.worker.recording_changed.connect(self._on_recording_state)
        self.worker.start()
        # Red stop icon without the word "Stop"
        self.btn_power.setText("")
        self.btn_power.setToolTip("Stop Stream")
        self.btn_power.setIcon(lucide_icon("square", "#ef4444", 12))
        self.btn_power.setFixedSize(26, 24)

    def stop_stream(self) -> None:
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
            worker.stop_stream()
            if worker.wait(3000):
                worker.deleteLater()
            else:
                worker.finished.connect(worker.deleteLater)
        self._audio_decay_timer.stop()
        self._audio_meter_level = 0.0
        self.audio_bar.setValue(0)
        # Show the word "Connect"
        self.btn_power.setText("Connect")
        self.btn_power.setToolTip("Connect to Live Stream")
        self.btn_power.setIcon(lucide_icon("play", "#38bdf8", 13))
        self.btn_power.setFixedSize(76, 24)
        self.btn_rec.setChecked(False)
        self.btn_rec.setIcon(lucide_icon("disc", "#a1a1aa", 14))
        self.rec_lbl.setVisible(False)
        self.person_lbl.setVisible(False)
        self._person_detected = False
        self._update_border()
        self.canvas.set_status("Stopped", False)
        self.status_dot.set_state("idle")
        self.stats_lbl.setText("0.0 FPS  0 kbps")

    def _on_frame(self, cam_id: str, image: QImage, fps: float, kbps: float) -> None:
        _ = cam_id
        self.canvas.set_frame(image)
        self.stats_lbl.setText(f"{fps:.1f} FPS  {kbps:.0f} kbps")

    def _on_detections(self, cam_id: str, boxes: list) -> None:
        _ = cam_id
        has_person = bool(boxes)
        self.canvas.set_detections(boxes)
        if has_person != self._person_detected:
            self._person_detected = has_person
            self.person_lbl.setVisible(has_person)
            self._update_border()

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
        self.canvas.set_status(status_text, is_connected)
        if is_connected:
            self.status_dot.set_state("live")
        elif "Error" in status_text or "Failed" in status_text or "Invalid" in status_text:
            self.status_dot.set_state("error")
        else:
            self.status_dot.set_state("connecting")

    def _on_mute_toggled(self, checked: bool) -> None:
        self.selected.emit(self.config.cam_id)
        self.audio_out.set_muted(not checked)
        self.btn_mute.setIcon(
            lucide_icon("volume_2", "#10b981", 14) if checked else lucide_icon("volume_x", "#71717a", 14)
        )

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
        if self._last_saved_snapshot and os.path.exists(self._last_saved_snapshot):
            try:
                os.startfile(self._last_saved_snapshot)
                return
            except Exception:
                pass
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
        self.btn_rec.setChecked(is_rec)
        self.btn_rec.setIcon(lucide_icon("disc", "#ef4444" if is_rec else "#a1a1aa", 14))
        self.rec_lbl.setVisible(is_rec)
        self.recording_toggled.emit(cam_id, is_rec, path)


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

        # --- Left Sidebar: Camera / Source / Date / Event Filter / Clip List (330px) ---
        self.sidebar = QFrame()
        self.sidebar.setObjectName("inspectorDrawer")
        self.sidebar.setFixedWidth(330)
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
        self.cmb_camera.setFixedHeight(30)
        self.cmb_camera.currentIndexChanged.connect(self._on_camera_combo_changed)
        src_vbox.addWidget(self.cmb_camera)

        src_row = QHBoxLayout()
        src_row.setSpacing(4)
        self.btn_src_sd = QPushButton("On-Camera SD Card")
        self.btn_src_sd.setObjectName("optBtn")
        self.btn_src_sd.setCheckable(True)
        self.btn_src_sd.setChecked(True)
        self.btn_src_sd.setIcon(lucide_icon("hard_drive", "#38bdf8", 13))
        self.btn_src_sd.clicked.connect(lambda: self._set_source_mode("sdcard"))

        self.btn_src_local = QPushButton("Local PC Captures")
        self.btn_src_local.setObjectName("optBtn")
        self.btn_src_local.setCheckable(True)
        self.btn_src_local.setIcon(lucide_icon("folder", "#a1a1aa", 13))
        self.btn_src_local.clicked.connect(lambda: self._set_source_mode("local"))

        src_row.addWidget(self.btn_src_sd)
        src_row.addWidget(self.btn_src_local)
        src_vbox.addLayout(src_row)
        sb_vbox.addWidget(src_sec)

        # 2. Date Navigator, Event Filter & Date/Time Range Backup Section
        flt_sec = QFrame()
        flt_sec.setObjectName("drawerSection")
        flt_vbox = QVBoxLayout(flt_sec)
        flt_vbox.setContentsMargins(10, 10, 10, 10)
        flt_vbox.setSpacing(8)

        hdr_date = QLabel("RECORDING DATE & EVENT FILTER")
        hdr_date.setObjectName("sectionTitle")
        flt_vbox.addWidget(hdr_date)

        date_row = QHBoxLayout()
        date_row.setSpacing(4)
        self.btn_prev_day = QPushButton()
        self.btn_prev_day.setIcon(lucide_icon("chevron_left", "#e4e4e7", 14))
        self.btn_prev_day.setFixedSize(28, 28)
        self.btn_prev_day.setToolTip("Previous Day")
        self.btn_prev_day.clicked.connect(lambda: self._shift_day(-1))
        date_row.addWidget(self.btn_prev_day)

        self.date_edit = QDateEdit(QDate.currentDate())
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDisplayFormat("MMMM d, yyyy")
        self.date_edit.setFixedHeight(28)
        self.date_edit.setStyleSheet(
            "QDateEdit { background-color: #141418; border: 1px solid #2e2e36; border-radius: 5px; "
            "color: #f4f4f5; padding: 2px 8px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; }"
        )
        self.date_edit.dateChanged.connect(lambda _: self.refresh_recordings())
        date_row.addWidget(self.date_edit, 1)

        self.btn_next_day = QPushButton()
        self.btn_next_day.setIcon(lucide_icon("chevron_right", "#e4e4e7", 14))
        self.btn_next_day.setFixedSize(28, 28)
        self.btn_next_day.setToolTip("Next Day")
        self.btn_next_day.clicked.connect(lambda: self._shift_day(1))
        date_row.addWidget(self.btn_next_day)

        self.btn_today = QPushButton("Today")
        self.btn_today.setFixedHeight(28)
        self.btn_today.setToolTip("Jump to Today")
        self.btn_today.clicked.connect(self._jump_today)
        date_row.addWidget(self.btn_today)

        self.btn_refresh_sd = QPushButton()
        self.btn_refresh_sd.setIcon(lucide_icon("refresh", "#38bdf8", 14))
        self.btn_refresh_sd.setFixedSize(28, 28)
        self.btn_refresh_sd.setToolTip("Refresh Recordings List")
        self.btn_refresh_sd.clicked.connect(self.refresh_recordings)
        date_row.addWidget(self.btn_refresh_sd)
        flt_vbox.addLayout(date_row)

        # Event Type Filter Pills
        ev_row = QHBoxLayout()
        ev_row.setSpacing(4)
        self.btn_flt_all = QPushButton("All (0)")
        self.btn_flt_all.setObjectName("optBtn")
        self.btn_flt_all.setCheckable(True)
        self.btn_flt_all.setChecked(True)
        self.btn_flt_all.clicked.connect(lambda: self._set_event_filter("all"))

        self.btn_flt_anom = QPushButton("Anomalies (0)")
        self.btn_flt_anom.setObjectName("optBtn")
        self.btn_flt_anom.setCheckable(True)
        self.btn_flt_anom.setIcon(lucide_icon("alert_triangle", "#ef4444", 12))
        self.btn_flt_anom.setToolTip("Show Only Anomaly / Alarm & Motion Triggered Clips")
        self.btn_flt_anom.clicked.connect(lambda: self._set_event_filter("anomaly"))

        self.btn_flt_norm = QPushButton("Normal (0)")
        self.btn_flt_norm.setObjectName("optBtn")
        self.btn_flt_norm.setCheckable(True)
        self.btn_flt_norm.clicked.connect(lambda: self._set_event_filter("normal"))

        ev_row.addWidget(self.btn_flt_all)
        ev_row.addWidget(self.btn_flt_anom)
        ev_row.addWidget(self.btn_flt_norm)
        flt_vbox.addLayout(ev_row)

        # Date & Time Range Backup Button
        self.btn_backup_range = QPushButton("Backup Footage (Date / Time Range)...")
        self.btn_backup_range.setIcon(lucide_icon("download", "#10b981", 13))
        self.btn_backup_range.setFixedHeight(28)
        self.btn_backup_range.setToolTip(
            "Download & back up SD card clips for a chosen date and time range so they play directly from your PC"
        )
        self.btn_backup_range.setStyleSheet(
            "QPushButton { background-color: #064e3b; color: #ecfdf5; border: 1px solid #10b981; "
            "border-radius: 5px; font-size: 11px; font-weight: 600; padding: 3px 8px; } "
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
        self.lbl_list_status = QLabel("Idle")
        self.lbl_list_status.setStyleSheet(
            "color: #71717a; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px;"
        )
        list_hdr_row.addWidget(self.lbl_list_status)

        self.btn_sort_order = QPushButton("↓ New → Old")
        self.btn_sort_order.setObjectName("segBtn")
        self.btn_sort_order.setFixedHeight(20)
        self.btn_sort_order.setToolTip("Switch footage list sort order between Newest First (New → Old) and Oldest First (Old → New)")
        self.btn_sort_order.setStyleSheet(
            "QPushButton { background-color: #16161d; color: #a1a1aa; border: 1px solid #272732; "
            "border-radius: 4px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 600; padding: 1px 6px; }"
            "QPushButton:hover { background-color: #1f1f29; color: #f4f4f5; border-color: #3b82f6; }"
        )
        self.btn_sort_order.clicked.connect(self._toggle_sort_order)
        list_hdr_row.addWidget(self.btn_sort_order)
        list_vbox.addLayout(list_hdr_row)

        self.clip_list = _ClipListWidget()
        self.clip_list.setIconSize(QSize(34, 16))
        self.clip_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.clip_list.customContextMenuRequested.connect(self._show_clip_context_menu)
        self.clip_list.setStyleSheet(
            "QListWidget { background-color: #0e0e12; border: 1px solid #23232b; border-radius: 5px; outline: none; }"
            "QListWidget::item { padding: 7px 8px; border-bottom: 1px solid #18181f; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; }"
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
        top_info.setFixedHeight(34)
        top_info.setStyleSheet(
            "background-color: #121216; border: 1px solid #22222a; border-radius: 5px;"
        )
        ti_hbox = QHBoxLayout(top_info)
        ti_hbox.setContentsMargins(10, 0, 10, 0)
        ti_hbox.setSpacing(8)

        self.lbl_player_title = QLabel("Select a recording clip or click the 24-hour timeline below")
        self.lbl_player_title.setStyleSheet("color: #f4f4f5; font-size: 12px; font-weight: 600;")
        ti_hbox.addWidget(self.lbl_player_title)

        self.lbl_event_badge = QLabel("ANOMALY")
        self.lbl_event_badge.setStyleSheet(
            "background-color: #7f1d1d; color: #ffffff; border: 1px solid #ef4444; "
            "border-radius: 3px; padding: 1px 7px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700;"
        )
        self.lbl_event_badge.setVisible(False)
        ti_hbox.addWidget(self.lbl_event_badge)

        self.lbl_backup_badge = QLabel("✓ LOCAL BACKUP")
        self.lbl_backup_badge.setToolTip("Playing directly from local PC backup instead of reading from camera SD card")
        self.lbl_backup_badge.setStyleSheet(
            "background-color: #064e3b; color: #6ee7b7; border: 1px solid #10b981; "
            "border-radius: 3px; padding: 1px 7px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 700;"
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
        btn_reset_zoom.setFixedHeight(24)
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
        tl_vbox.setContentsMargins(8, 6, 8, 6)
        tl_vbox.setSpacing(4)

        tl_legend = QHBoxLayout()
        tl_legend.setSpacing(6)
        lbl_tl = QLabel("SD TIMELINE")
        lbl_tl.setObjectName("sectionTitle")
        lbl_tl.setToolTip(
            "Left-click/drag to scrub • Mouse wheel to zoom at cursor • Right-drag or bottom bar to pan"
        )
        tl_legend.addWidget(lbl_tl)

        tl_legend.addSpacing(4)

        self.btn_tl_pan_left = QPushButton("◀")
        self.btn_tl_pan_left.setObjectName("segBtn")
        self.btn_tl_pan_left.setFixedSize(22, 20)
        self.btn_tl_pan_left.setToolTip("Pan Timeline Earlier (or Right-Click Drag on Timeline)")
        self.btn_tl_pan_left.clicked.connect(lambda: self.timeline.pan_by_fraction(-0.35))
        tl_legend.addWidget(self.btn_tl_pan_left)

        self.btn_tl_zoom_out = QPushButton("−")
        self.btn_tl_zoom_out.setObjectName("segBtn")
        self.btn_tl_zoom_out.setFixedSize(22, 20)
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
            bz.setObjectName("segBtn")
            bz.setCheckable(True)
            bz.setChecked(span_sec == 86400)
            bz.setFixedSize(34, 20)
            bz.setToolTip(f"Zoom timeline to {lbl_z} window (or use Mouse Wheel over timeline)")
            bz.clicked.connect(lambda checked, s=span_sec: self.timeline.set_view_span(float(s)))
            tl_legend.addWidget(bz)
            self.tl_zoom_btns[span_sec] = bz

        self.btn_tl_zoom_in = QPushButton("+")
        self.btn_tl_zoom_in.setObjectName("segBtn")
        self.btn_tl_zoom_in.setFixedSize(22, 20)
        self.btn_tl_zoom_in.setToolTip("Zoom In Timeline for Precise Time Selection (Mouse Wheel Up)")
        self.btn_tl_zoom_in.clicked.connect(lambda: self.timeline.zoom_in())
        tl_legend.addWidget(self.btn_tl_zoom_in)

        self.btn_tl_pan_right = QPushButton("▶")
        self.btn_tl_pan_right.setObjectName("segBtn")
        self.btn_tl_pan_right.setFixedSize(22, 20)
        self.btn_tl_pan_right.setToolTip("Pan Timeline Later (or Right-Click Drag on Timeline)")
        self.btn_tl_pan_right.clicked.connect(lambda: self.timeline.pan_by_fraction(0.35))
        tl_legend.addWidget(self.btn_tl_pan_right)

        self.lbl_tl_zoom_range = QLabel("Full Day (Wheel to Zoom)")
        self.lbl_tl_zoom_range.setStyleSheet(
            "color: #38bdf8; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 600; padding-left: 4px;"
        )
        tl_legend.addWidget(self.lbl_tl_zoom_range)

        tl_legend.addStretch()

        for dot_col, txt in (
            ("#ef4444", "Anomaly / Alarm"),
            ("#f59e0b", "Motion"),
            ("#3b82f6", "Continuous"),
            ("#10b981", "Local Backup"),
        ):
            dot = QLabel(f"■ {txt}")
            dot.setStyleSheet(
                f"color: {dot_col}; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 10px; font-weight: 600;"
            )
            tl_legend.addWidget(dot)
        tl_vbox.addLayout(tl_legend)

        self.timeline = DayTimelineWidget(self)
        self.timeline.seek_requested.connect(self._on_timeline_seek)
        self.timeline.zoom_changed.connect(self._on_timeline_zoom_changed)
        tl_vbox.addWidget(self.timeline)
        self._on_timeline_zoom_changed(86400.0, 0, 0)
        p_vbox.addWidget(timeline_frame)

        # Transport & MP4 Export Control Bar
        ctrl_frame = QFrame()
        ctrl_frame.setFixedHeight(46)
        ctrl_frame.setStyleSheet(
            "background-color: #121216; border: 1px solid #22222a; border-radius: 5px;"
        )
        c_hbox = QHBoxLayout(ctrl_frame)
        c_hbox.setContentsMargins(10, 0, 10, 0)
        c_hbox.setSpacing(6)

        self.btn_back10 = QPushButton("-10s")
        self.btn_back10.setIcon(lucide_icon("skip_back", "#e4e4e7", 13))
        self.btn_back10.setFixedHeight(30)
        self.btn_back10.setToolTip("Rewind 10 Seconds")
        self.btn_back10.clicked.connect(lambda: self.skip_seconds(-10))
        c_hbox.addWidget(self.btn_back10)

        self.btn_play_pause = QPushButton("Play")
        self.btn_play_pause.setObjectName("primaryBtn")
        self.btn_play_pause.setIcon(lucide_icon("play", "#ffffff", 14))
        self.btn_play_pause.setFixedSize(88, 30)
        self.btn_play_pause.setToolTip("Play / Pause Playback")
        self.btn_play_pause.clicked.connect(self.toggle_play_pause)
        c_hbox.addWidget(self.btn_play_pause)

        self.btn_fwd10 = QPushButton("+10s")
        self.btn_fwd10.setIcon(lucide_icon("skip_forward", "#e4e4e7", 13))
        self.btn_fwd10.setFixedHeight(30)
        self.btn_fwd10.setToolTip("Forward 10 Seconds")
        self.btn_fwd10.clicked.connect(lambda: self.skip_seconds(10))
        c_hbox.addWidget(self.btn_fwd10)

        self.btn_stop = QPushButton()
        self.btn_stop.setIcon(lucide_icon("square", "#ef4444", 13))
        self.btn_stop.setFixedSize(32, 30)
        self.btn_stop.setToolTip("Stop Playback")
        self.btn_stop.clicked.connect(self.stop_playback)
        c_hbox.addWidget(self.btn_stop)

        c_hbox.addSpacing(6)

        self.lbl_clock_pos = QLabel("--:--:--")
        self.lbl_clock_pos.setStyleSheet(
            "background-color: #09090b; color: #38bdf8; border: 1px solid #27272d; border-radius: 4px; "
            "padding: 4px 10px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 13px; font-weight: 700;"
        )
        c_hbox.addWidget(self.lbl_clock_pos)

        c_hbox.addSpacing(6)

        # Speed Selector Pills (1x always available; 2x, 4x, 8x, 16x unlocked only when footage is downloaded/local)
        self.speed_btns: Dict[float, QPushButton] = {}
        for spd in (1.0, 2.0, 4.0, 8.0, 16.0):
            b = QPushButton(f"{int(spd)}x")
            b.setObjectName("segBtn")
            b.setCheckable(True)
            b.setChecked(spd == 1.0)
            b.setFixedSize(36, 26)
            b.clicked.connect(lambda checked, s=spd: self.set_playback_speed(s))
            c_hbox.addWidget(b)
            self.speed_btns[spd] = b
        self._update_speed_controls_lock(unlocked=False)

        c_hbox.addStretch()

        # Audio Mute Toggle
        self.btn_mute = QPushButton()
        self.btn_mute.setCheckable(True)
        self.btn_mute.setChecked(True)
        self.btn_mute.setIcon(lucide_icon("volume_2", "#10b981", 14))
        self.btn_mute.setFixedSize(32, 30)
        self.btn_mute.setToolTip("Toggle Recorded Audio Output (1x speed)")
        self.btn_mute.clicked.connect(self._on_mute_toggled)
        self.audio_out.set_muted(False)
        c_hbox.addWidget(self.btn_mute)

        self._review_audio_level: float = 0.0
        self.audio_bar = QProgressBar()
        self.audio_bar.setRange(0, 100)
        self.audio_bar.setValue(0)
        self.audio_bar.setTextVisible(False)
        self.audio_bar.setFixedSize(40, 4)
        self.audio_bar.setToolTip("Playback Audio Level")
        c_hbox.addWidget(self.audio_bar)

        self._review_audio_decay_timer = QTimer(self)
        self._review_audio_decay_timer.setInterval(55)
        self._review_audio_decay_timer.timeout.connect(self._decay_review_audio_meter)

        # Snapshot Button
        self.btn_snap = QPushButton("Snapshot")
        self.btn_snap.setIcon(lucide_icon("camera", "#e4e4e7", 14))
        self.btn_snap.setFixedHeight(30)
        self.btn_snap.setToolTip("Save Current Playback Frame as JPG Snapshot")
        self.btn_snap.clicked.connect(self.save_snapshot)
        c_hbox.addWidget(self.btn_snap)

        # Export Progress Bar (visible during SD Card MP4 export)
        self.export_bar = QProgressBar()
        self.export_bar.setRange(0, 100)
        self.export_bar.setValue(0)
        self.export_bar.setFixedSize(110, 18)
        self.export_bar.setVisible(False)
        c_hbox.addWidget(self.export_bar)

        # Backup / Export Menu Button
        self.btn_export = QPushButton("Backup / Export .MP4")
        self.btn_export.setObjectName("primaryBtn")
        self.btn_export.setIcon(lucide_icon("download", "#ffffff", 14))
        self.btn_export.setFixedHeight(30)
        self.btn_export.setToolTip("Back up current clip, back up a date/time range, or export a sub-clip as .MP4")
        self.btn_export.clicked.connect(self._show_export_menu)
        c_hbox.addWidget(self.btn_export)

        p_vbox.addWidget(ctrl_frame)
        root_hbox.addWidget(player_container, 1)

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
            item.setIcon(lucide_icon("film" if is_vid else "camera", "#38bdf8" if is_vid else "#10b981", 13))
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
# Main Video-First NVR Window
# ---------------------------------------------------------------------------
class OmniSightMainWindow(QMainWindow):
    """UniFi Protect / Frigate NVR style video-first multi-camera client."""

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("OmniSight — Unofficial Client Compatible with V380 Cameras")
        self.resize(1440, 860)
        self.setStyleSheet(NVR_STYLESHEET)
        self.setWindowIcon(lucide_icon("cctv", "#3b82f6", 24))

        self.cameras: List[CameraConfig] = []
        self.tiles: Dict[str, CameraTileWidget] = {}
        self.selected_cam_id: Optional[str] = None
        self.grid_mode: int = 2  # 0 = Auto, 1 = 1x1, 2 = 2x2 (Default), 3 = 3x3
        self.maximized_cam_id: Optional[str] = None
        self.monitor_mode: bool = False
        self._drawer_was_open: bool = True
        self._held_ptz_dir: Optional[str] = None
        self._ptz_repeat_timer = QTimer(self)
        self._ptz_repeat_timer.setInterval(180)
        self._ptz_repeat_timer.timeout.connect(self._on_ptz_repeat_tick)

        self._build_ui()
        self._load_cameras()

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
        hb.setContentsMargins(14, 0, 14, 0)
        hb.setSpacing(8)

        self.cam_count_badge = QLabel("0 CAMERAS")
        self.cam_count_badge.setFixedHeight(28)
        self.cam_count_badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.cam_count_badge.setStyleSheet(
            "background-color: #141418; color: #a1a1aa; border: 1px solid #27272d; "
            "border-radius: 5px; padding: 4px 10px; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 600;"
        )
        hb.addWidget(self.cam_count_badge)

        hb.addSpacing(6)

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

        hb.addSpacing(8)

        btn_add = QPushButton("Add Camera")
        btn_add.setObjectName("primaryBtn")
        btn_add.setIcon(lucide_icon("plus", "#ffffff", 14))
        btn_add.setFixedHeight(28)
        btn_add.clicked.connect(self.add_camera_dialog)
        hb.addWidget(btn_add)

        btn_discover = QPushButton("Discover")
        btn_discover.setIcon(lucide_icon("radar", "#e4e4e7", 14))
        btn_discover.setFixedHeight(28)
        btn_discover.setToolTip("Scan LAN for Compatible Cameras (UDP 10008 / TCP 8800)")
        btn_discover.clicked.connect(self.discover_cameras_dialog)
        hb.addWidget(btn_discover)

        btn_start_all = QPushButton("Connect All")
        btn_start_all.setIcon(lucide_icon("play", "#10b981", 13))
        btn_start_all.setFixedHeight(28)
        btn_start_all.clicked.connect(self.start_all_streams)
        hb.addWidget(btn_start_all)

        btn_stop_all = QPushButton("Stop All")
        btn_stop_all.setIcon(lucide_icon("square", "#a1a1aa", 12))
        btn_stop_all.setFixedHeight(28)
        btn_stop_all.clicked.connect(self.stop_all_streams)
        hb.addWidget(btn_stop_all)

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

        hb.addSpacing(8)

        btn_snaps = QPushButton()
        btn_snaps.setObjectName("ghostBtn")
        btn_snaps.setIcon(lucide_icon("folder", "#a1a1aa", 15))
        btn_snaps.setFixedSize(30, 28)
        btn_snaps.setToolTip("Open Snapshots Folder")
        btn_snaps.clicked.connect(lambda: self._open_folder(SNAPSHOT_DIR))
        hb.addWidget(btn_snaps)

        btn_recs = QPushButton()
        btn_recs.setObjectName("ghostBtn")
        btn_recs.setIcon(lucide_icon("film", "#a1a1aa", 15))
        btn_recs.setFixedSize(30, 28)
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

        # Right Collapsible PTZ & Device Inspector Drawer (Open by default)
        self.drawer = QFrame()
        self.drawer.setObjectName("inspectorDrawer")
        self.drawer.setFixedWidth(275)
        self.drawer.setVisible(True)
        d_vbox = QVBoxLayout(self.drawer)
        d_vbox.setContentsMargins(12, 12, 12, 12)
        d_vbox.setSpacing(10)

        # 1. Camera Switcher Section (Auto-sizes height to fit items cleanly without scrollbar)
        cam_sec = QFrame()
        cam_sec.setObjectName("drawerSection")
        cam_sec.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        cs_vbox = QVBoxLayout(cam_sec)
        cs_vbox.setContentsMargins(10, 10, 10, 10)
        cs_vbox.setSpacing(10)

        cs_hdr = QLabel("DEVICES")
        cs_hdr.setObjectName("sectionTitle")
        cs_hdr.setFixedHeight(14)
        cs_vbox.addWidget(cs_hdr)

        self.cam_list = QListWidget()
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
        btn_rename.setToolTip("Rename Selected Camera (F2)")
        btn_rename.clicked.connect(lambda: self.rename_camera_dialog())
        dev_btns.addWidget(btn_rename)

        btn_edit = QPushButton("Config")
        btn_edit.setIcon(lucide_icon("settings", "#e4e4e7", 12))
        btn_edit.setToolTip("Edit Connection Settings")
        btn_edit.clicked.connect(self.edit_selected_camera)
        dev_btns.addWidget(btn_edit)

        btn_del = QPushButton()
        btn_del.setObjectName("dangerGhostBtn")
        btn_del.setIcon(lucide_icon("trash", "#a1a1aa", 13))
        btn_del.setFixedWidth(30)
        btn_del.setToolTip("Remove Camera")
        btn_del.clicked.connect(self.delete_selected_camera)
        dev_btns.addWidget(btn_del)
        cs_vbox.addLayout(dev_btns)
        d_vbox.addWidget(cam_sec)

        # 2. Active Camera Telemetry
        sel_sec = QFrame()
        sel_sec.setObjectName("drawerSection")
        sel_sec.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ss_vbox = QVBoxLayout(sel_sec)
        ss_vbox.setContentsMargins(10, 10, 10, 10)
        ss_vbox.setSpacing(10)

        ss_hdr = QLabel("ACTIVE CAMERA")
        ss_hdr.setObjectName("sectionTitle")
        ss_hdr.setFixedHeight(14)
        ss_vbox.addWidget(ss_hdr)

        self.lbl_selected_name = QLabel("None Selected")
        self.lbl_selected_name.setStyleSheet("font-size: 13px; font-weight: 600; color: #f4f4f5;")
        ss_vbox.addWidget(self.lbl_selected_name)

        self.lbl_selected_meta = QLabel("Select a viewport to control PTZ")
        self.lbl_selected_meta.setStyleSheet(
            "color: #71717a; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px;"
        )
        self.lbl_selected_meta.setWordWrap(True)
        ss_vbox.addWidget(self.lbl_selected_meta)
        d_vbox.addWidget(sel_sec)

        # 3. PTZ Control Section
        ptz_sec = QFrame()
        ptz_sec.setObjectName("drawerSection")
        ptz_sec.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        ptz_vbox = QVBoxLayout(ptz_sec)
        ptz_vbox.setContentsMargins(10, 10, 10, 12)
        ptz_vbox.setSpacing(10)

        ptz_hdr = QLabel("PTZ GIMBAL CONTROL")
        ptz_hdr.setObjectName("sectionTitle")
        ptz_hdr.setFixedHeight(14)
        ptz_vbox.addWidget(ptz_hdr)

        ptz_grid = QGridLayout()
        ptz_grid.setSpacing(6)

        self.btn_ptz_up = self._make_ptz_button("chevron_up", "up", "Tilt Up (Up Arrow)")
        self.btn_ptz_left = self._make_ptz_button("chevron_left", "left", "Pan Left (Left Arrow)")
        self.btn_ptz_stop = QPushButton()
        self.btn_ptz_stop.setIcon(lucide_icon("crosshair", "#93c5fd", 16))
        self.btn_ptz_stop.setFixedSize(46, 38)
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
        ptz_vbox.addLayout(ptz_grid)

        ptz_hint = QLabel("Click center crosshair for PTZ Presets")
        ptz_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        ptz_hint.setStyleSheet("color: #71717a; font-size: 10px; padding-top: 2px;")
        ptz_vbox.addWidget(ptz_hint)
        d_vbox.addWidget(ptz_sec)

        # 4. Illumination & Optics Section
        opt_sec = QFrame()
        opt_sec.setObjectName("drawerSection")
        opt_sec.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        opt_vbox = QVBoxLayout(opt_sec)
        opt_vbox.setContentsMargins(10, 10, 10, 10)
        opt_vbox.setSpacing(6)

        opt_hdr = QLabel("ILLUMINATION & OPTICS")
        opt_hdr.setObjectName("sectionTitle")
        opt_hdr.setFixedHeight(14)
        opt_vbox.addWidget(opt_hdr)

        lbl_sensor = QLabel("Night Vision / Sensor")
        lbl_sensor.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 500; padding-top: 2px;")
        opt_vbox.addWidget(lbl_sensor)

        img_row = QHBoxLayout()
        img_row.setSpacing(4)
        self.btn_img_color = QPushButton("Color")
        self.btn_img_color.setObjectName("optBtn")
        self.btn_img_color.setCheckable(True)
        self.btn_img_color.setIcon(lucide_icon("sun", "#e4e4e7", 13))
        self.btn_img_color.setToolTip("Force Full-Color Day Mode")
        self.btn_img_color.clicked.connect(lambda: self._send_image_mode("color"))

        self.btn_img_bw = QPushButton("IR B&W")
        self.btn_img_bw.setObjectName("optBtn")
        self.btn_img_bw.setCheckable(True)
        self.btn_img_bw.setIcon(lucide_icon("eye", "#a1a1aa", 13))
        self.btn_img_bw.setToolTip("Force Infrared B&W Night Vision")
        self.btn_img_bw.clicked.connect(lambda: self._send_image_mode("bw"))

        self.btn_img_auto = QPushButton("Auto")
        self.btn_img_auto.setObjectName("optBtn")
        self.btn_img_auto.setCheckable(True)
        self.btn_img_auto.setIcon(lucide_icon("zap", "#f59e0b", 13))
        self.btn_img_auto.setToolTip("Automatic Day/Night Sensor Switching")
        self.btn_img_auto.clicked.connect(lambda: self._send_image_mode("auto"))

        img_row.addWidget(self.btn_img_color)
        img_row.addWidget(self.btn_img_bw)
        img_row.addWidget(self.btn_img_auto)
        opt_vbox.addLayout(img_row)

        lbl_flood = QLabel("White Floodlight")
        lbl_flood.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 500; padding-top: 2px;")
        opt_vbox.addWidget(lbl_flood)

        light_row = QHBoxLayout()
        light_row.setSpacing(4)
        self.btn_l_on = QPushButton("On")
        self.btn_l_on.setObjectName("optBtn")
        self.btn_l_on.setCheckable(True)
        self.btn_l_on.setIcon(lucide_icon("sun", "#e4e4e7", 13))
        self.btn_l_on.setToolTip("Turn White Floodlight On")
        self.btn_l_on.clicked.connect(lambda: self._send_light("on"))

        self.btn_l_off = QPushButton("Off")
        self.btn_l_off.setObjectName("optBtn")
        self.btn_l_off.setCheckable(True)
        self.btn_l_off.setIcon(lucide_icon("moon", "#a1a1aa", 13))
        self.btn_l_off.setToolTip("Turn White Floodlight Off")
        self.btn_l_off.clicked.connect(lambda: self._send_light("off"))

        self.btn_l_auto = QPushButton("Auto")
        self.btn_l_auto.setObjectName("optBtn")
        self.btn_l_auto.setCheckable(True)
        self.btn_l_auto.setIcon(lucide_icon("zap", "#f59e0b", 13))
        self.btn_l_auto.setToolTip("Automatic Floodlight & Night Vision")
        self.btn_l_auto.clicked.connect(lambda: self._send_light("auto"))

        light_row.addWidget(self.btn_l_on)
        light_row.addWidget(self.btn_l_off)
        light_row.addWidget(self.btn_l_auto)
        opt_vbox.addLayout(light_row)

        flip_row = QHBoxLayout()
        flip_row.setSpacing(4)
        self.btn_flip = QPushButton("Flip Orientation 180°")
        self.btn_flip.setObjectName("optBtn")
        self.btn_flip.setCheckable(True)
        self.btn_flip.setIcon(lucide_icon("flip_vertical", "#a1a1aa", 13))
        self.btn_flip.setToolTip("Invert Camera Image 180 Degrees")
        self.btn_flip.clicked.connect(self._send_flip)
        flip_row.addWidget(self.btn_flip)
        opt_vbox.addLayout(flip_row)

        lbl_det = QLabel("Person Detection & GPU")
        lbl_det.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 500; padding-top: 2px;")
        opt_vbox.addWidget(lbl_det)

        det_row = QHBoxLayout()
        det_row.setSpacing(4)
        self.btn_person_det = QPushButton("Person Detection Alert")
        self.btn_person_det.setObjectName("optBtn")
        self.btn_person_det.setCheckable(True)
        self.btn_person_det.setChecked(True)
        self.btn_person_det.setIcon(lucide_icon("crosshair", "#ef4444", 13))
        self.btn_person_det.setToolTip(
            "Long-Range Person Detection — Draws PERSON / PERSON (FAR) bounding box and flashes red corner outline"
        )
        self.btn_person_det.clicked.connect(self._toggle_person_detection)
        det_row.addWidget(self.btn_person_det)
        opt_vbox.addLayout(det_row)

        sens_row = QHBoxLayout()
        sens_row.setSpacing(4)
        self.btn_sens_std = QPushButton("Std")
        self.btn_sens_std.setObjectName("optBtn")
        self.btn_sens_std.setCheckable(True)
        self.btn_sens_std.setToolTip("Standard Sensitivity — Close & Mid-range person detection")
        self.btn_sens_std.clicked.connect(lambda: self._set_detection_sensitivity("standard"))

        self.btn_sens_high = QPushButton("High")
        self.btn_sens_high.setObjectName("optBtn")
        self.btn_sens_high.setCheckable(True)
        self.btn_sens_high.setToolTip("High Sensitivity — Extended range person detection")
        self.btn_sens_high.clicked.connect(lambda: self._set_detection_sensitivity("high"))

        self.btn_sens_ultra = QPushButton("Ultra (Far)")
        self.btn_sens_ultra.setObjectName("optBtn")
        self.btn_sens_ultra.setCheckable(True)
        self.btn_sens_ultra.setChecked(True)
        self.btn_sens_ultra.setToolTip(
            "Ultra Far-Range Sensitivity — Detects people even far from the camera (down to 4% frame height)"
        )
        self.btn_sens_ultra.clicked.connect(lambda: self._set_detection_sensitivity("ultra"))

        sens_row.addWidget(self.btn_sens_std)
        sens_row.addWidget(self.btn_sens_high)
        sens_row.addWidget(self.btn_sens_ultra)
        opt_vbox.addLayout(sens_row)

        gpu_row = QHBoxLayout()
        gpu_row.setSpacing(4)
        gpu_info = get_gpu_capabilities()
        gpu_dev = gpu_info.get("device_name", "GPU")
        self.btn_gpu_accel = QPushButton(f"Use GPU Power ({gpu_dev})")
        self.btn_gpu_accel.setObjectName("optBtn")
        self.btn_gpu_accel.setCheckable(True)
        self.btn_gpu_accel.setChecked(True)
        self.btn_gpu_accel.setIcon(lucide_icon("zap", "#10b981", 13))
        self.btn_gpu_accel.setToolTip(
            f"GPU Acceleration ({gpu_info.get('summary', 'OpenCL + D3D11VA')}):\n"
            "• Runs high-resolution 640x360 OpenCL UMat CV + CLAHE contrast boost on GPU\n"
            "• Enables Direct3D 11 (D3D11VA) hardware video decoding"
        )
        self.btn_gpu_accel.clicked.connect(self._toggle_gpu_acceleration)
        gpu_row.addWidget(self.btn_gpu_accel)
        opt_vbox.addLayout(gpu_row)
        d_vbox.addWidget(opt_sec)

        # 5. Master Audio Gain
        aud_sec = QFrame()
        aud_sec.setObjectName("drawerSection")
        aud_sec.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        aud_vbox = QVBoxLayout(aud_sec)
        aud_vbox.setContentsMargins(10, 10, 10, 10)
        aud_vbox.setSpacing(10)

        aud_hdr = QLabel("AUDIO MONITOR GAIN")
        aud_hdr.setObjectName("sectionTitle")
        aud_hdr.setFixedHeight(14)
        aud_vbox.addWidget(aud_hdr)

        vol_row = QHBoxLayout()
        vol_icon = QLabel()
        vol_icon.setPixmap(lucide_pixmap("volume_2", "#a1a1aa", 14))
        vol_row.addWidget(vol_icon)
        self.sld_volume = QSlider(Qt.Orientation.Horizontal)
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

    def set_workspace_mode(self, mode: str) -> None:
        is_review = mode == "review"
        self.btn_ws_live.setChecked(not is_review)
        self.btn_ws_review.setChecked(is_review)
        if is_review:
            self.review_workspace.set_cameras(self.cameras, self.selected_cam_id)
            self.workspace_stack.setCurrentWidget(self.review_workspace)
            self.review_workspace.select_camera_and_load(self.selected_cam_id)
        else:
            self.review_workspace.stop_playback()
            self.workspace_stack.setCurrentWidget(self.live_workspace)

    def open_sd_review_for_camera(self, cam_id: str) -> None:
        self.select_camera(cam_id)
        self.set_workspace_mode("review")

    def _make_ptz_button(self, icon_name: str, direction: str, tooltip: str) -> QPushButton:
        btn = QPushButton()
        btn.setIcon(lucide_icon(icon_name, "#e4e4e7", 16))
        btn.setIconSize(QSize(16, 16))
        btn.setFixedSize(46, 38)
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
        if CONFIG_FILE.exists():
            try:
                data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if isinstance(data, list):
                    for item in data:
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

        if names_restored and loaded:
            self._save_cameras_list(loaded)

        for cfg in loaded:
            self._add_camera_internal(cfg, auto_start=True)

        if self.cameras:
            self.select_camera(self.cameras[0].cam_id)
        else:
            self._update_camera_count_badge()

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
        all_hd = bool(self.cameras) and all(c.quality == 1 for c in self.cameras)
        if all_hd:
            self.btn_all_quality.setText("All: HD")
            self.btn_all_quality.setToolTip("All cameras are streaming in HD — click to switch all cameras to SD")
            self.btn_all_quality.setStyleSheet(
                "QPushButton { background-color: #172554; border: 1px solid #2563eb; border-radius: 5px; "
                "color: #93c5fd; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: 0 10px; }"
                "QPushButton:hover { background-color: #1e3a8a; border-color: #3b82f6; color: #ffffff; }"
            )
        else:
            self.btn_all_quality.setText("All: SD → HD")
            self.btn_all_quality.setToolTip("Click to switch all cameras from SD to HD")
            self.btn_all_quality.setStyleSheet(
                "QPushButton { background-color: #16161b; border: 1px solid #2c2c35; border-radius: 5px; "
                "color: #d4d4d8; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: 0 10px; }"
                "QPushButton:hover { background-color: #202028; border-color: #3b82f6; color: #ffffff; }"
            )

    def _sync_all_gpu_btn(self) -> None:
        if not hasattr(self, "btn_all_gpu"):
            return
        gpu_caps = get_gpu_capabilities()
        gpu_summary = gpu_caps.get("summary", "OpenCL + D3D11VA")
        any_gpu = not self.cameras or any(bool(getattr(c, "gpu_acceleration", True)) for c in self.cameras)
        if any_gpu:
            self.btn_all_gpu.setText("GPU: ON")
            self.btn_all_gpu.setIcon(lucide_icon("zap", "#10b981", 13))
            self.btn_all_gpu.setToolTip(
                f"GPU Acceleration ACTIVE ({gpu_summary}) — High-res OpenCL person detection & D3D11 decode. Click to switch to CPU"
            )
            self.btn_all_gpu.setStyleSheet(
                "QPushButton { background-color: #064e3b; border: 1px solid #059669; border-radius: 5px; "
                "color: #6ee7b7; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: 0 10px; }"
                "QPushButton:hover { background-color: #065f46; border-color: #10b981; color: #ffffff; }"
            )
        else:
            self.btn_all_gpu.setText("GPU: OFF")
            self.btn_all_gpu.setIcon(lucide_icon("zap", "#71717a", 13))
            self.btn_all_gpu.setToolTip(
                f"GPU Acceleration OFF (CPU Mode) — Click to enable GPU power ({gpu_summary}) on all cameras"
            )
            self.btn_all_gpu.setStyleSheet(
                "QPushButton { background-color: #16161b; border: 1px solid #2c2c35; border-radius: 5px; "
                "color: #a1a1aa; font-family: 'Cascadia Mono', 'Consolas', monospace; font-size: 11px; font-weight: 700; padding: 0 10px; }"
                "QPushButton:hover { background-color: #202028; border-color: #10b981; color: #ffffff; }"
            )

    def _update_cam_list_height(self) -> None:
        self.cam_list.ensurePolished()
        count = self.cam_list.count()
        if count <= 0:
            self.cam_list.setFixedHeight(46)
            return
        total_h = 0
        for i in range(count):
            row_h = self.cam_list.sizeHintForRow(i)
            total_h += row_h if row_h > 0 else 45
        padding = (self.cam_list.frameWidth() * 2) + 4
        max_visible_h = 6 * 45 + padding
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
            item.setData(Qt.ItemDataRole.UserRole, cfg.cam_id)
            self.cam_list.addItem(item)
        self.cam_list.blockSignals(False)
        self._update_cam_list_height()

    def set_grid_mode(self, mode: int) -> None:
        self.maximized_cam_id = None
        self.grid_mode = mode
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
                stream_profile = "HD Stream" if cfg.quality == 1 else "SD Stream"
                sens_lbl = getattr(cfg, "detection_sensitivity", "ultra").upper()
                gpu_lbl = "GPU" if bool(getattr(cfg, "gpu_acceleration", True)) else "CPU"
                self.lbl_selected_meta.setText(f"{cfg.protocol.upper()}  |  {stream_profile}  |  {sens_lbl} ({gpu_lbl})")
                self.status_bar.showMessage(f"Switched {cfg.name} to {stream_profile}.", 3000)

    def _sync_optics_buttons(self, cfg: CameraConfig) -> None:
        img_mode = (cfg.image_mode or "auto").lower()
        light_mode = (cfg.light_mode or "auto").lower()
        sens = (getattr(cfg, "detection_sensitivity", "ultra") or "ultra").lower()
        gpu_on = bool(getattr(cfg, "gpu_acceleration", True))
        self.btn_img_color.setChecked(img_mode == "color")
        self.btn_img_bw.setChecked(img_mode == "bw")
        self.btn_img_auto.setChecked(img_mode not in ("color", "bw"))
        self.btn_l_on.setChecked(light_mode == "on")
        self.btn_l_off.setChecked(light_mode == "off")
        self.btn_l_auto.setChecked(light_mode not in ("on", "off"))
        self.btn_flip.setChecked(bool(cfg.flipped))
        self.btn_person_det.setChecked(bool(getattr(cfg, "person_detection", True)))
        self.btn_sens_std.setChecked(sens == "standard")
        self.btn_sens_high.setChecked(sens == "high")
        self.btn_sens_ultra.setChecked(sens not in ("standard", "high"))
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
            tile._on_detections(tile.config.cam_id, [])
        self._sync_optics_buttons(tile.config)
        self._save_cameras_list()
        state_str = "ENABLED" if tile.config.person_detection else "DISABLED"
        self.status_bar.showMessage(f"Person Detection Alert {state_str} on {tile.config.name}", 3000)

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
        vol = value / 100.0
        for tile in self.tiles.values():
            tile.audio_out.set_volume(vol)

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
        if event.key() == Qt.Key.Key_Escape:
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
