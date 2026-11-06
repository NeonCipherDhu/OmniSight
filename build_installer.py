"""
Build script for OmniSight Windows Application & Standalone Setup Installer.
Unofficial client compatible with V380 cameras.

Produces:
1. `installer/OmniSight_Setup.exe` — Single-file GUI Windows Installer (bundles OmniSight, FFmpeg, and YOLOv8n)
2. `installer/OmniSight_Portable.zip` — Portable ZIP archive of the standalone application
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

ROOT = Path(__file__).resolve().parent
BUILD_DIR = ROOT / "build"
DIST_DIR = ROOT / "dist"
INSTALLER_DIR = ROOT / "installer"
ICO_PATH = ROOT / "omnisight.ico"


def generate_ico(ico_path: Path) -> None:
    """Render a crisp multi-resolution Windows .ico file for OmniSight."""
    from PyQt6.QtCore import QBuffer, QByteArray, QIODevice, QRectF, Qt
    from PyQt6.QtGui import QColor, QGuiApplication, QImage, QPainter, QPen
    from PyQt6.QtSvg import QSvgRenderer

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
    import imageio_ffmpeg

    ff_src = Path(imageio_ffmpeg.get_ffmpeg_exe()).resolve()
    ff_copy = BUILD_DIR / "ffmpeg.exe"
    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ff_src, ff_copy)

    ts = int(time.time())
    work_dir = Path(tempfile.gettempdir()) / f"omnisight_app_work_{ts}"
    dist_tmp = Path(tempfile.gettempdir()) / f"omnisight_app_dist_{ts}"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onedir",
        "--noconsole",
        "--name",
        "OmniSight",
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
    cmd.append(str(ROOT / "omnisight_app.py"))
    print("[2/4] Building standalone OmniSight application via PyInstaller...")
    subprocess.run(cmd, cwd=str(ROOT), check=True)

    app_dist = dist_tmp / "OmniSight"
    return app_dist


def create_payload_zip(app_dist: Path) -> Path:
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    payload_zip = BUILD_DIR / "OmniSight_Payload.zip"
    portable_zip = INSTALLER_DIR / "OmniSight_Portable.zip"

    print(f"[3/4] Compressing application payload -> {payload_zip} ...")
    with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for file_path in app_dist.rglob("*"):
            if file_path.is_file():
                rel = file_path.relative_to(app_dist)
                zf.write(file_path, rel)

    shutil.copy2(payload_zip, portable_zip)
    return payload_zip


def build_setup_exe(payload_zip: Path) -> Path:
    print("[4/4] Building single-file Windows installer: installer/OmniSight_Setup.exe ...")
    work_dir = Path(tempfile.gettempdir()) / f"omnisight_setup_work_{int(time.time())}"
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--onefile",
        "--noconsole",
        "--name",
        "OmniSight_Setup",
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
    setup_exe = INSTALLER_DIR / "OmniSight_Setup.exe"
    print(f"Done! Installer created at: {setup_exe}")
    return setup_exe


def _detect_installed_dir() -> Path:
    """Locate an existing installation directory dynamically."""
    env_dir = os.environ.get("OMNISIGHT_INSTALL_DIR", "").strip()
    if env_dir:
        return Path(env_dir)

    if os.name == "nt":
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OmniSight",
            ) as key:
                loc, _ = winreg.QueryValueEx(key, "InstallLocation")
                if loc and Path(loc).exists():
                    return Path(loc)
        except Exception:
            pass

        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            cand = Path(f"{letter}:\\Program Files\\OmniSight")
            if cand.exists():
                return cand

        for letter in "CDEFGHIJKLMNOPQRSTUVWXYZ":
            pf = Path(f"{letter}:\\Program Files")
            if not pf.is_dir():
                continue
            try:
                for sub in pf.iterdir():
                    if sub.is_dir() and (sub / "_internal").is_dir() and (sub / "cameras.json").is_file():
                        return sub
            except Exception:
                continue

    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(local_app_data) / "Programs" / "OmniSight"


def _stop_running_instances(install_dir: Path | None = None) -> None:
    exe_names = {"OmniSight.exe"}
    if install_dir is not None:
        exe_names.add(f"{install_dir.name}.exe")
    for exe_name in exe_names:
        subprocess.run(
            ["taskkill", "/F", "/IM", exe_name, "/T"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )


def _sync_legacy_exe_alias(install_dir: Path) -> None:
    """If patching an existing installation folder with a custom folder name, sync its main executable."""
    new_exe = install_dir / "OmniSight.exe"
    folder_exe = install_dir / f"{install_dir.name}.exe"
    if new_exe.is_file() and folder_exe != new_exe and folder_exe.exists():
        try:
            shutil.copy2(new_exe, folder_exe)
        except Exception:
            pass


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
    _stop_running_instances(install_dir)
    time.sleep(0.4)

    test_file = install_dir / ".write_probe"
    can_write = False
    try:
        test_file.write_text("ok", encoding="utf-8")
        test_file.unlink(missing_ok=True)
        can_write = True
    except PermissionError:
        can_write = False

    if not can_write:
        cur_user = os.environ.get("USERNAME", "*S-1-5-32-545")
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".ps1", prefix="omnisight_patch_", delete=False, encoding="utf-8"
        ) as tf:
            ps1_path = Path(tf.name)
            tf.write(
                f'Stop-Process -Name "OmniSight" -Force -ErrorAction SilentlyContinue\n'
                f'icacls "{install_dir}" /grant "{cur_user}:(OI)(CI)M" /T /C /Q\n'
            )
        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-Command",
                    f'Start-Process powershell -ArgumentList \'-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "{ps1_path}"\' -Verb RunAs -Wait',
                ],
                check=False,
            )
        finally:
            ps1_path.unlink(missing_ok=True)

    preserved_names = {"cameras.json", "camera_names.json"}
    try:
        with zipfile.ZipFile(payload_zip, "r") as zf:
            for member in zf.infolist():
                if Path(member.filename).name.lower() in preserved_names:
                    continue
                zf.extract(member, install_dir)
        _sync_legacy_exe_alias(install_dir)
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
    _stop_running_instances(install_dir)
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
                if dst_file.exists() and dst_file.stat().st_size == src_file.stat().st_size:
                    continue
                raise
        _sync_legacy_exe_alias(install_dir)
        print(f"Successfully patched {install_dir} (preserved cameras.json & user data).")
        return True
    except Exception as exc:
        print(f"Warning: Could not patch {install_dir} directly: {exc}")
        return False


def patch_installed_exe_carchive(install_dir: Path | None = None) -> bool:
    """Patch OmniSight.exe's PyInstaller CArchive in-place using its own bundled python*.dll."""
    if install_dir is None:
        install_dir = _detect_installed_dir()
    exe_path = install_dir / "OmniSight.exe"
    internal_dir = install_dir / "_internal"
    if not exe_path.is_file() or not internal_dir.is_dir():
        return False

    py_dlls = [p for p in internal_dir.glob("python3*.dll") if p.name.lower() != "python3.dll"]
    if not py_dlls:
        return False
    py_dll_name = py_dlls[0].name

    csc_path = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")
    if not csc_path.is_file():
        return False

    print(f"Patching installed CArchive directly at: {exe_path} (using bundled {py_dll_name}) ...")
    tmp_dir = Path(tempfile.gettempdir()) / f"omnisight_carchive_patch_{int(time.time())}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cs_file = tmp_dir / "py_host.cs"
    host_exe = tmp_dir / "py_host.exe"
    patch_py = tmp_dir / "repack_carchive.py"
    staged_exe = tmp_dir / "OmniSight.exe.new"

    cs_code = f"""using System;
using System.IO;
using System.Runtime.InteropServices;

class Program {{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetDllDirectory(string lpPathName);

    [DllImport("{py_dll_name}", CallingConvention = CallingConvention.Cdecl)]
    static extern void Py_Initialize();

    [DllImport("{py_dll_name}", CallingConvention = CallingConvention.Cdecl)]
    static extern int PyRun_SimpleString(byte[] command);

    [DllImport("{py_dll_name}", CallingConvention = CallingConvention.Cdecl)]
    static extern int Py_FinalizeEx();

    static int Main(string[] args) {{
        string internalDir = { '@"' + str(internal_dir) + '"' };
        SetDllDirectory(internalDir);
        Environment.SetEnvironmentVariable("PYTHONHOME", internalDir);
        Environment.SetEnvironmentVariable("PYTHONPATH", internalDir + ";" + Path.Combine(internalDir, "base_library.zip"));
        Py_Initialize();
        string script = File.ReadAllText(args[0]);
        byte[] utf8 = System.Text.Encoding.UTF8.GetBytes(script + "\\0");
        int rc = PyRun_SimpleString(utf8);
        Py_FinalizeEx();
        return rc;
    }}
}}
"""
    cs_file.write_text(cs_code, encoding="utf-8")
    subprocess.run(
        [str(csc_path), "/nologo", "/platform:x64", f"/out:{host_exe}", str(cs_file)],
        check=True,
    )

    patch_script = f"""import marshal
import os
from _struct import pack, unpack
import zlib

root_dir = {repr(str(ROOT))}
exe_path = {repr(str(exe_path))}
staged_exe = {repr(str(staged_exe))}

with open(exe_path, "rb") as f:
    data = f.read()

MAGIC = b"MEI\\x0c\\x0b\\x0a\\x0b\\x0e"
cookie_pos = data.rfind(MAGIC)
if cookie_pos < 0:
    raise RuntimeError("PyInstaller cookie not found in OmniSight.exe")

magic, pkg_len, toc_off, toc_len, pyvers, pylib = unpack("!8siiii64s", data[cookie_pos : cookie_pos + 88])
pkg_start = cookie_pos + 88 - pkg_len
prefix = data[:pkg_start]
toc_bytes = data[pkg_start + toc_off : pkg_start + toc_off + toc_len]

# Compile updated omnisight_app.py
with open(os.path.join(root_dir, "omnisight_app.py"), "rb") as f:
    app_src = f.read()
app_code_raw = marshal.dumps(compile(app_src, "omnisight_app.py", "exec"))
app_code_comp = zlib.compress(app_code_raw, 6)

# Helper to update PYZ.pyz with stream_worker.py and v380_protocol.py
def update_pyz(pyz_bytes):
    pyz_magic = pyz_bytes[:8]
    old_toc_off = unpack("!i", pyz_bytes[8:12])[0]
    header_rest = pyz_bytes[12:17]
    toc = marshal.loads(pyz_bytes[old_toc_off:])
    is_list = isinstance(toc, list)
    toc_dict = dict(toc) if is_list else dict(toc)

    body = bytearray(pyz_bytes[:old_toc_off])
    for mod_name, src_filename in (
        ("stream_worker", "stream_worker.py"),
        ("omnisight_protocol", "omnisight_protocol.py"),
        ("v380_protocol", "omnisight_protocol.py"),
    ):
        mod_file = os.path.join(root_dir, src_filename)
        if os.path.isfile(mod_file) and mod_name in toc_dict:
            with open(mod_file, "rb") as mf:
                msrc = mf.read()
            mcode = marshal.dumps(compile(msrc, src_filename, "exec"))
            mcomp = zlib.compress(mcode, 6)
            new_pos = len(body)
            body.extend(mcomp)
            toc_dict[mod_name] = (0, new_pos, len(mcomp))

    new_pyz_toc_off = len(body)
    toc_out = list(toc_dict.items()) if is_list else toc_dict
    body.extend(marshal.dumps(toc_out))
    body[8:12] = pack("!i", new_pyz_toc_off)
    return bytes(body)

new_pkg_body = bytearray()
new_toc = bytearray()
pos = 0
while pos < len(toc_bytes):
    elen, dpos, dlen, ulen, cflag, typcd = unpack("!iiiiBB", toc_bytes[pos : pos + 18])
    raw_name_padded = toc_bytes[pos + 18 : pos + elen]
    name = raw_name_padded.rstrip(b"\\x00").decode("utf-8", "replace")
    entry_bytes = data[pkg_start + dpos : pkg_start + dpos + dlen]

    if name == "omnisight_app" and chr(typcd) == "s":
        entry_bytes = app_code_comp
        dlen = len(app_code_comp)
        ulen = len(app_code_raw)
        cflag = 1
    elif name == "PYZ.pyz" and chr(typcd) == "z":
        entry_bytes = update_pyz(entry_bytes)
        dlen = len(entry_bytes)
        ulen = len(entry_bytes)
        cflag = 0

    new_dpos = len(new_pkg_body)
    new_pkg_body.extend(entry_bytes)
    new_toc.extend(pack("!iiiiBB", elen, new_dpos, dlen, ulen, cflag, typcd))
    new_toc.extend(raw_name_padded)
    pos += elen

new_toc_off = len(new_pkg_body)
new_toc_len = len(new_toc)
new_pkg_len = new_toc_off + new_toc_len + 88
new_cookie = pack("!8siiii64s", magic, new_pkg_len, new_toc_off, new_toc_len, pyvers, pylib)

with open(staged_exe, "wb") as f:
    f.write(prefix)
    f.write(new_pkg_body)
    f.write(new_toc)
    f.write(new_cookie)

print("Built patched executable:", os.path.getsize(staged_exe), "bytes")
"""
    patch_py.write_text(patch_script, encoding="utf-8")
    subprocess.run([str(host_exe), str(patch_py)], check=True)

    _stop_running_instances(install_dir)
    time.sleep(0.6)
    shutil.copy2(staged_exe, exe_path)
    _sync_legacy_exe_alias(install_dir)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    print(f"Successfully patched {install_dir} in-place (preserved cameras.json & user data).")
    return True


def update_installer_carchive(install_dir: Path | None = None) -> Path:
    """Update OmniSight_Payload.zip, OmniSight_Portable.zip, and OmniSight_Setup.exe in-place using bundled python*.dll."""
    if install_dir is None:
        install_dir = _detect_installed_dir()
    if not patch_installed_exe_carchive(install_dir):
        raise RuntimeError("Failed to patch installed OmniSight.exe prior to updating installer.")

    exe_path = install_dir / "OmniSight.exe"
    internal_dir = install_dir / "_internal"
    py_dlls = [p for p in internal_dir.glob("python3*.dll") if p.name.lower() != "python3.dll"]
    py_dll_name = py_dlls[0].name
    csc_path = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")

    BUILD_DIR.mkdir(parents=True, exist_ok=True)
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)
    payload_zip = BUILD_DIR / "OmniSight_Payload.zip"
    portable_zip = INSTALLER_DIR / "OmniSight_Portable.zip"
    setup_exe = INSTALLER_DIR / "OmniSight_Setup.exe"

    # 1. Update OmniSight.exe inside OmniSight_Payload.zip and OmniSight_Portable.zip
    print(f"[2/3] Updating {payload_zip.name} and {portable_zip.name} with patched OmniSight.exe ...")
    tmp_zip = BUILD_DIR / "OmniSight_Payload.zip.tmp"
    if payload_zip.is_file():
        with zipfile.ZipFile(payload_zip, "r") as zin, zipfile.ZipFile(
            tmp_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6
        ) as zout:
            for item in zin.infolist():
                if item.filename.replace("\\", "/") == "OmniSight.exe":
                    continue
                zout.writestr(item, zin.read(item.filename))
            zout.write(exe_path, "OmniSight.exe")
        os.replace(tmp_zip, payload_zip)
    else:
        with zipfile.ZipFile(payload_zip, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zout:
            zout.write(exe_path, "OmniSight.exe")
            for fp in internal_dir.rglob("*"):
                if fp.is_file():
                    zout.write(fp, fp.relative_to(install_dir))
    shutil.copy2(payload_zip, portable_zip)

    # 2. Repackage installer/OmniSight_Setup.exe CArchive with updated installer_main.py and OmniSight_Payload.zip
    print(f"[3/3] Updating single-file installer CArchive: {setup_exe} ...")
    tmp_dir = Path(tempfile.gettempdir()) / f"omnisight_setup_patch_{int(time.time())}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    cs_file = tmp_dir / "py_host.cs"
    host_exe = tmp_dir / "py_host.exe"
    patch_py = tmp_dir / "repack_setup.py"
    staged_setup = tmp_dir / "OmniSight_Setup.exe.new"

    cs_code = f"""using System;
using System.IO;
using System.Runtime.InteropServices;

class Program {{
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool SetDllDirectory(string lpPathName);

    [DllImport("{py_dll_name}", CallingConvention = CallingConvention.Cdecl)]
    static extern void Py_Initialize();

    [DllImport("{py_dll_name}", CallingConvention = CallingConvention.Cdecl)]
    static extern int PyRun_SimpleString(byte[] command);

    [DllImport("{py_dll_name}", CallingConvention = CallingConvention.Cdecl)]
    static extern int Py_FinalizeEx();

    static int Main(string[] args) {{
        string internalDir = { '@"' + str(internal_dir) + '"' };
        SetDllDirectory(internalDir);
        Environment.SetEnvironmentVariable("PYTHONHOME", internalDir);
        Environment.SetEnvironmentVariable("PYTHONPATH", internalDir + ";" + Path.Combine(internalDir, "base_library.zip"));
        Py_Initialize();
        string script = File.ReadAllText(args[0]);
        byte[] utf8 = System.Text.Encoding.UTF8.GetBytes(script + "\\0");
        int rc = PyRun_SimpleString(utf8);
        Py_FinalizeEx();
        return rc;
    }}
}}
"""
    cs_file.write_text(cs_code, encoding="utf-8")
    subprocess.run(
        [str(csc_path), "/nologo", "/platform:x64", f"/out:{host_exe}", str(cs_file)],
        check=True,
    )

    repack_script = f"""import marshal
import os
from _struct import pack, unpack
import zlib

root_dir = {repr(str(ROOT))}
setup_exe = {repr(str(setup_exe))}
payload_zip = {repr(str(payload_zip))}
staged_setup = {repr(str(staged_setup))}

with open(setup_exe, "rb") as f:
    data = f.read()

MAGIC = b"MEI\\x0c\\x0b\\x0a\\x0b\\x0e"
cookie_pos = data.rfind(MAGIC)
if cookie_pos < 0:
    raise RuntimeError("PyInstaller cookie not found in OmniSight_Setup.exe")

magic, pkg_len, toc_off, toc_len, pyvers, pylib = unpack("!8siiii64s", data[cookie_pos : cookie_pos + 88])
pkg_start = cookie_pos + 88 - pkg_len
prefix = data[:pkg_start]
toc_bytes = data[pkg_start + toc_off : pkg_start + toc_off + toc_len]

with open(os.path.join(root_dir, "installer_main.py"), "rb") as f:
    inst_src = f.read()
inst_raw = marshal.dumps(compile(inst_src, "installer_main.py", "exec"))
inst_comp = zlib.compress(inst_raw, 6)

with open(payload_zip, "rb") as f:
    payload_raw = f.read()
payload_comp = zlib.compress(payload_raw, 6)

new_pkg_body = bytearray()
new_toc = bytearray()
pos = 0
while pos < len(toc_bytes):
    elen, dpos, dlen, ulen, cflag, typcd = unpack("!iiiiBB", toc_bytes[pos : pos + 18])
    raw_name_padded = toc_bytes[pos + 18 : pos + elen]
    name = raw_name_padded.rstrip(b"\\x00").decode("utf-8", "replace")
    entry_bytes = data[pkg_start + dpos : pkg_start + dpos + dlen]

    if name == "installer_main" and chr(typcd) == "s":
        entry_bytes = inst_comp
        dlen = len(inst_comp)
        ulen = len(inst_raw)
        cflag = 1
    elif name == "OmniSight_Payload.zip" and chr(typcd) == "b":
        entry_bytes = payload_comp
        dlen = len(payload_comp)
        ulen = len(payload_raw)
        cflag = 1

    new_dpos = len(new_pkg_body)
    new_pkg_body.extend(entry_bytes)
    new_toc.extend(pack("!iiiiBB", elen, new_dpos, dlen, ulen, cflag, typcd))
    new_toc.extend(raw_name_padded)
    pos += elen

new_toc_off = len(new_pkg_body)
new_toc_len = len(new_toc)
new_pkg_len = new_toc_off + new_toc_len + 88
new_cookie = pack("!8siiii64s", magic, new_pkg_len, new_toc_off, new_toc_len, pyvers, pylib)

with open(staged_setup, "wb") as f:
    f.write(prefix)
    f.write(new_pkg_body)
    f.write(new_toc)
    f.write(new_cookie)

print("Built updated installer:", os.path.getsize(staged_setup), "bytes")
"""
    patch_py.write_text(repack_script, encoding="utf-8")
    subprocess.run([str(host_exe), str(patch_py)], check=True)
    shutil.copy2(staged_setup, setup_exe)
    shutil.rmtree(tmp_dir, ignore_errors=True)
    return setup_exe


def _has_pyinstaller() -> bool:
    try:
        res = subprocess.run(
            [sys.executable, "-c", "import PyInstaller, imageio_ffmpeg, PyQt6"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return res.returncode == 0
    except Exception:
        return False


def main() -> None:
    if "--patch-only" in sys.argv:
        try:
            if patch_installed_exe_carchive():
                return
        except Exception as exc:
            print(f"Fast CArchive patch skipped ({exc}), falling back to PyInstaller build...")
        if _has_pyinstaller():
            if not ICO_PATH.exists():
                generate_ico(ICO_PATH)
            app_dist = build_app()
            patch_installed_from_dir(app_dist)
        else:
            raise RuntimeError("Could not patch installed OmniSight executable.")
        return

    if _has_pyinstaller():
        generate_ico(ICO_PATH)
        app_dist = build_app()
        payload_zip = create_payload_zip(app_dist)
        setup_exe = build_setup_exe(payload_zip)
        patch_installed_app(payload_zip)
    else:
        setup_exe = update_installer_carchive()
    size_mb = setup_exe.stat().st_size / (1024 * 1024)
    print(f"\nSUCCESS: {setup_exe} ({size_mb:.1f} MB)")


if __name__ == "__main__":
    main()
