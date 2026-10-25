"""
Build script for V380 Protect Windows Application & Standalone Setup Installer.

Produces:
1. `installer/V380Protect_Setup.exe` — Single-file GUI Windows Installer (bundles V380Protect, FFmpeg, and cameras.json)
2. `installer/V380Protect_Portable.zip` — Portable ZIP archive of the standalone application
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import imageio_ffmpeg
from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QRectF, Qt
from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
from PyQt6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
INSTALLER_DIR = ROOT / "installer"
ICO_PATH = ROOT / "v380_protect.ico"


def generate_ico(ico_path: Path) -> None:
    """Render a crisp multi-resolution Windows .ico file for V380 Protect."""
    _app = QGuiApplication.instance() or QGuiApplication([])
    svg_xml = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" '
        'fill="none" stroke="#3b82f6" stroke-width="2.1" '
        'stroke-linecap="round" stroke-linejoin="round">'
        '<path d="M16.75 12h3.632a1 1 0 0 1 .894 1.447l-2.034 4.069a1 1 0 0 1-1.708.134l-2.124-2.97"/>'
        '<path d="M17.106 9.053a1 1 0 0 1 .447 1.341l-3.106 6.211a1 1 0 0 1-1.342.447L3.61 12.3a2.92 2.92 0 0 1-1.3-3.91L3.69 5.6a2.92 2.92 0 0 1 3.92-1.3z"/>'
        '<path d="M2 19h3.76a2 2 0 0 0 1.8-1.1L9 15"/>'
        '<path d="M2 21v-4"/>'
        '<path d="M7 9h.01"/>'
        "</svg>"
    )
    renderer = QSvgRenderer(QByteArray(svg_xml.encode("utf-8")))

    sizes = [16, 32, 48, 64, 128, 256]
    png_blobs = []
    for sz in sizes:
        img = QImage(sz, sz, QImage.Format.Format_ARGB32)
        img.fill(Qt.GlobalColor.transparent)
        painter = QPainter(img)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        pad = max(1.0, sz * 0.04)
        radius = sz * 0.22
        rect = QRectF(pad, pad, sz - pad * 2, sz - pad * 2)
        painter.setBrush(QColor("#09090b"))
        painter.setPen(QPen(QColor("#27272d"), max(1.0, sz * 0.03)))
        painter.drawRoundedRect(rect, radius, radius)

        icon_pad = sz * 0.20
        renderer.render(painter, QRectF(icon_pad, icon_pad, sz - icon_pad * 2, sz - icon_pad * 2))
        painter.end()

        buf = QBuffer()
        buf.open(QIODevice.OpenModeFlag.WriteOnly)
        img.save(buf, "PNG")
        png_blobs.append((sz, bytes(buf.data())))

    # Write standard Windows ICO container with PNG payloads
    header = struct.pack("<HHH", 0, 1, len(png_blobs))
    offset = 6 + 16 * len(png_blobs)
    entries = bytearray()
    data_area = bytearray()
    for sz, blob in png_blobs:
        w_byte = 0 if sz >= 256 else sz
        h_byte = 0 if sz >= 256 else sz
        entries.extend(struct.pack("<BBBBHHII", w_byte, h_byte, 0, 0, 1, 32, len(blob), offset))
        data_area.extend(blob)
        offset += len(blob)

    ico_path.write_bytes(header + bytes(entries) + bytes(data_area))
    print(f"[1/4] Generated icon: {ico_path}")


def build_app() -> Path:
    ff_src = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    ff_copy = BUILD_DIR / "ffmpeg.exe"
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ff_src, ff_copy)

    cams_file = ROOT / "cameras.json"
    ts = int(time.time())
    work_dir = Path(tempfile.gettempdir()) / f"v380_app_work_{ts}"
    dist_tmp = Path(tempfile.gettempdir()) / f"v380_app_dist_{ts}"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--noconsole",
        "--name",
        "V380Protect",
        "--workpath",
        str(work_dir),
        "--specpath",
        str(work_dir),
        "--distpath",
        str(dist_tmp),
        "--icon",
        str(ICO_PATH),
        "--add-binary",
        f"{ff_copy};.",
        "--exclude-module",
        "imageio_ffmpeg",
        "--hidden-import",
        "PyQt6.QtSvg",
        "--hidden-import",
        "PyQt6.QtMultimedia",
        "--hidden-import",
        "Crypto.Cipher.AES",
        "--hidden-import",
        "onnxruntime",
    ]
    yolo_model = ROOT / "models" / "yolov8n.onnx"
    if yolo_model.is_file():
        cmd.extend(["--add-data", f"{yolo_model};models"])
    cmd.append(str(ROOT / "v380_app.py"))
    print("[2/4] Building standalone V380Protect application via PyInstaller...")
    subprocess.run(cmd, cwd=str(ROOT), check=True)

    app_dist = dist_tmp / "V380Protect"
    return app_dist


def create_payload_zip(app_dist: Path) -> Path:
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    payload_zip = BUILD_DIR / "V380Protect_Payload.zip"
    portable_zip = INSTALLER_DIR / "V380Protect_Portable.zip"

    print(f"[3/4] Compressing application payload -> {payload_zip} ...")
    with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in app_dist.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(app_dist)
                zf.write(file_path, rel)

    shutil.copy2(payload_zip, portable_zip)
    return payload_zip


def build_setup_exe(payload_zip: Path) -> Path:
    print("[4/4] Building single-file Windows installer: installer/V380Protect_Setup.exe ...")
    work_dir = Path(tempfile.gettempdir()) / f"v380_setup_work_{int(time.time())}"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--noconsole",
        "--name",
        "V380Protect_Setup",
        "--workpath",
        str(work_dir),
        "--specpath",
        str(work_dir),
        "--icon",
        str(ICO_PATH),
        "--distpath",
        str(INSTALLER_DIR),
        "--add-data",
        f"{payload_zip};.",
        "--add-data",
        f"{ICO_PATH};.",
        str(ROOT / "installer_main.py"),
    ]
    subprocess.run(cmd, cwd=str(ROOT), check=True)
    setup_exe = INSTALLER_DIR / "V380Protect_Setup.exe"
    print(f"Done! Installer created at: {setup_exe}")
    return setup_exe


def _detect_installed_dir() -> Path:
    """Locate an existing V380Protect installation directory dynamically."""
    env_dir = os.environ.get("V380_INSTALL_DIR", "").strip()
    if env_dir:
        return Path(env_dir)

    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\V380Protect",
            ) as key:
                loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                if loc and Path(loc).exists():
                    return Path(loc)
        except Exception:
            pass

        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            cand = Path(f"{letter}:\\Program Files\\V380Protect")
            if cand.exists():
                return cand

    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(local_app_data) / "Programs" / "V380Protect"


def patch_installed_app(
    payload_zip: Path,
    install_dir: Path | None = None,
) -> bool:
    """Directly patch the existing installation in-place while preserving cameras.json and user data."""
    if install_dir is None:
        install_dir = _detect_installed_dir()
    if not install_dir.exists():
        return False

    print(f"[5/5] Patching installed application directly at: {install_dir} ...")
    subprocess.run(
        ["taskkill", "/F", "/IM", "V380Protect.exe"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.4)

    # Test if install_dir is directly writable without elevation
    test_file = install_dir / ".write_probe"
    can_write = False
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        can_write = True
    except PermissionError:
        can_write = False

    if not can_write:
        # Grant Users Full Control on the installation folder once via elevated PowerShell script
        ps1_path = Path(tempfile.gettempdir()) / "v380_grant_and_patch.ps1"
        ps1_path.write_text(
            f'Stop-Process -Name "V380Protect" -Force -ErrorAction SilentlyContinue\n'
            f'icacls "{install_dir}" /grant "*S-1-5-32-545:(OI)(CI)F" /T /C /Q\n',
            encoding="utf-8",
        )
        subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f'Start-Process powershell -ArgumentList \'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1_path}"\' -Verb RunAs -Wait',
            ],
            check=False,
        )

    preserved_names = {"cameras.json", "camera_names.json"}
    try:
        with zipfile.ZipFile(payload_zip, "r") as zf:
            for member in zf.infolist():
                if Path(member.filename).name.lower() in preserved_names:
                    continue
                zf.extract(member, install_dir)
        print(f"      Successfully patched {install_dir} (preserved cameras.json & user data).")
        return True
    except Exception as exc:
        print(f"      Warning: Could not patch {install_dir} directly: {exc}")
        return False


def patch_installed_from_dir(
    app_dist: Path,
    install_dir: Path | None = None,
) -> bool:
    """Directly copy built app_dist files into the installed directory without building the installer."""
    if install_dir is None:
        install_dir = _detect_installed_dir()
    if not install_dir.exists():
        return False

    print(f"Patching installed application directly at: {install_dir} ...")
    subprocess.run(
        ["taskkill", "/F", "/IM", "V380Protect.exe", "/T"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.8)

    preserved_names = {"cameras.json", "camera_names.json"}
    try:
        for src_file in app_dist.rglob("*"):
            if not src_file.is_file():
                continue
            if src_file.name.lower() in preserved_names:
                continue
            rel = src_file.relative_to(app_dist)
            dst_file = install_dir / rel
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_file, dst_file)
            except PermissionError:
                # Skip locked binary dependencies (e.g. ffmpeg.exe or Qt6 DLLs) if already identical in size
                if dst_file.exists() and dst_file.stat().st_size == src_file.stat().st_size:
                    continue
                raise
        print(f"Successfully patched {install_dir} (preserved cameras.json & user data).")
        return True
    except Exception as exc:
        print(f"Warning: Could not patch {install_dir} directly: {exc}")
        return False


def main() -> None:
    if "--patch-only" in sys.argv:
        if not ICO_PATH.exists():
            generate_ico(ICO_PATH)
        app_dist = build_app()
        patch_installed_from_dir(app_dist)
        return

    generate_ico(ICO_PATH)
    app_dist = build_app()
    payload_zip = create_payload_zip(app_dist)
    setup_exe = build_setup_exe(payload_zip)
    patch_installed_app(payload_zip)
    size_mb = setup_exe.stat().st_size / (1024 * 1024)
    print(f"\nSUCCESS: {setup_exe} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
