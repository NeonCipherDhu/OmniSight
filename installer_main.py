"""
Standalone Windows Setup Installer for OmniSight — Local NVR.
Unofficial client compatible with V380 cameras.
Extracts the bundled application payload (including FFmpeg, PyQt6, OpenCV, and ONNX Runtime),
creates Desktop & Start Menu shortcuts, registers with Windows Installed Apps, and launches OmniSight.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import winreg
import zipfile
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "OmniSight"
APP_EXE = "OmniSight.exe"
APP_VERSION = "1.2.0"
APP_PUBLISHER = "OmniSight Open Source NVR"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\OmniSight"


def _bundle_dir() -> Path:
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))


def _default_install_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local"))
    return Path(local_app_data) / "Programs" / "OmniSight"


def _create_shortcut(target_exe: Path, shortcut_path: Path, working_dir: Path, description: str) -> None:
    shortcut_path.parent.mkdir(parents=True, exist_ok=True)
    ps_script = (
        f"$ws = New-Object -ComObject WScript.Shell; "
        f"$s = $ws.CreateShortcut('{str(shortcut_path).replace(chr(39), chr(39)*2)}'); "
        f"$s.TargetPath = '{str(target_exe).replace(chr(39), chr(39)*2)}'; "
        f"$s.WorkingDirectory = '{str(working_dir).replace(chr(39), chr(39)*2)}'; "
        f"$s.IconLocation = '{str(target_exe).replace(chr(39), chr(39)*2)},0'; "
        f"$s.Description = '{description}'; "
        f"$s.Save()"
    )
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script],
        capture_output=True,
        creationflags=creationflags,
    )


def _register_uninstaller(install_dir: Path, exe_path: Path) -> None:
    uninstaller_bat = install_dir / "Uninstall_OmniSight.bat"
    uninstaller_bat.write_text(
        "@echo off\r\n"
        "echo Uninstalling OmniSight...\r\n"
        f"taskkill /F /IM {APP_EXE} >nul 2>&1\r\n"
        f'del /F /Q "%USERPROFILE%\\Desktop\\{APP_NAME}.lnk" >nul 2>&1\r\n'
        f'rmdir /S /Q "%APPDATA%\\Microsoft\\Windows\\Start Menu\\Programs\\{APP_NAME}" >nul 2>&1\r\n'
        f'reg delete "HKCU\\{UNINSTALL_KEY}" /f >nul 2>&1\r\n'
        f'cd /d "%TEMP%"\r\n'
        f'start /b "" cmd /c "timeout /t 2 /nobreak >nul & rmdir /S /Q ""{install_dir}"""\r\n',
        encoding="utf-8",
    )
    try:
        with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            winreg.SetValueEx(key, "DisplayName", 0, winreg.REG_SZ, APP_NAME)
            winreg.SetValueEx(key, "DisplayVersion", 0, winreg.REG_SZ, APP_VERSION)
            winreg.SetValueEx(key, "Publisher", 0, winreg.REG_SZ, APP_PUBLISHER)
            winreg.SetValueEx(key, "DisplayIcon", 0, winreg.REG_SZ, str(exe_path))
            winreg.SetValueEx(key, "InstallLocation", 0, winreg.REG_SZ, str(install_dir))
            winreg.SetValueEx(key, "UninstallString", 0, winreg.REG_SZ, f'"{uninstaller_bat}"')
            winreg.SetValueEx(key, "NoModify", 0, winreg.REG_DWORD, 1)
            winreg.SetValueEx(key, "NoRepair", 0, winreg.REG_DWORD, 1)
    except Exception:
        pass


class InstallWorker(QObject):
    progress = pyqtSignal(int, str)
    finished = pyqtSignal(bool, str, str)

    def __init__(
        self,
        install_dir: Path,
        create_desktop: bool,
        create_start_menu: bool,
    ) -> None:
        super().__init__()
        self.install_dir = install_dir
        self.create_desktop = create_desktop
        self.create_start_menu = create_start_menu

    def run(self) -> None:
        try:
            payload_zip = _bundle_dir() / "OmniSight_Payload.zip"
            if not payload_zip.is_file():
                self.finished.emit(False, "Installer payload archive not found.", "")
                return

            self.progress.emit(5, "Stopping any running OmniSight instances...")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            subprocess.run(
                ["taskkill", "/F", "/IM", APP_EXE],
                capture_output=True,
                creationflags=creationflags,
            )

            self.install_dir.mkdir(parents=True, exist_ok=True)

            with zipfile.ZipFile(payload_zip, "r") as zf:
                members = zf.infolist()
                total = max(1, len(members))
                for idx, member in enumerate(members, 1):
                    zf.extract(member, self.install_dir)
                    if idx % 15 == 0 or idx == total:
                        pct = 10 + int((idx / total) * 75)
                        self.progress.emit(pct, f"Extracting files ({idx}/{total})...")

            exe_path = self.install_dir / APP_EXE

            if self.create_desktop:
                self.progress.emit(88, "Creating Desktop shortcut...")
                desktop_dir = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
                _create_shortcut(
                    exe_path,
                    desktop_dir / f"{APP_NAME}.lnk",
                    self.install_dir,
                    "OmniSight — Unofficial client compatible with V380 cameras",
                )

            if self.create_start_menu:
                self.progress.emit(93, "Creating Start Menu shortcut...")
                start_menu_dir = (
                    Path(os.environ.get("APPDATA", str(Path.home())))
                    / "Microsoft"
                    / "Windows"
                    / "Start Menu"
                    / "Programs"
                    / APP_NAME
                )
                _create_shortcut(
                    exe_path,
                    start_menu_dir / f"{APP_NAME}.lnk",
                    self.install_dir,
                    "OmniSight — Unofficial client compatible with V380 cameras",
                )

            self.progress.emit(97, "Registering application...")
            _register_uninstaller(self.install_dir, exe_path)

            self.progress.emit(100, "Installation complete.")
            self.finished.emit(True, "OmniSight has been installed successfully.", str(exe_path))
        except Exception as exc:
            self.finished.emit(False, f"Installation failed: {exc}", "")


class InstallerWindow(QWidget):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("installerRoot")
        self.setWindowTitle(f"{APP_NAME} v{APP_VERSION} — Setup")
        self.setFixedSize(540, 420)
        ico = _bundle_dir() / "omnisight.ico"
        if ico.is_file():
            self.setWindowIcon(QIcon(str(ico)))

        self.setStyleSheet(
            """
            QWidget#installerRoot {
                background-color: #09090b;
            }
            QWidget {
                color: #f4f4f5;
                font-family: "Segoe UI", "Inter", sans-serif;
                font-size: 12px;
            }
            QLabel, QCheckBox {
                background-color: transparent;
            }
            QCheckBox {
                spacing: 8px;
                padding: 3px 0px;
                color: #e4e4e7;
            }
            QFrame#card {
                background-color: #141418;
                border: 1px solid #222228;
                border-radius: 6px;
            }
            QLineEdit {
                background-color: #111115;
                border: 1px solid #27272d;
                border-radius: 5px;
                padding: 0px 10px;
                color: #e4e4e7;
                font-size: 12px;
            }
            QLineEdit:focus {
                border-color: #3b82f6;
            }
            QPushButton {
                background-color: #18181c;
                border: 1px solid #27272d;
                border-radius: 5px;
                padding: 0px 14px;
                color: #e4e4e7;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #222228;
                border-color: #3f3f46;
            }
            QPushButton#primaryBtn {
                background-color: #2563eb;
                border: 1px solid #3b82f6;
                color: #ffffff;
            }
            QPushButton#primaryBtn:hover {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #18181c;
                color: #52525b;
                border-color: #222228;
            }
            QProgressBar {
                background-color: #141418;
                border: 1px solid #27272d;
                border-radius: 4px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #2563eb;
                border-radius: 3px;
            }
            """
        )

        self._installed_exe: Optional[str] = None
        self._worker: Optional[InstallWorker] = None

        root = QVBoxLayout(self)
        root.setContentsMargins(24, 20, 24, 20)
        root.setSpacing(12)

        sub = QLabel(
            "Install OmniSight Local NVR (Unofficial client compatible with V380 cameras) "
            "with bundled FFmpeg video and audio decoding engine."
        )
        sub.setWordWrap(True)
        sub.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        root.addWidget(sub)

        card = QFrame()
        card.setObjectName("card")
        c_vbox = QVBoxLayout(card)
        c_vbox.setContentsMargins(16, 14, 16, 14)
        c_vbox.setSpacing(10)

        lbl_dir = QLabel("Installation Folder:")
        lbl_dir.setStyleSheet("color: #a1a1aa; font-size: 11px; font-weight: 600;")
        c_vbox.addWidget(lbl_dir)

        dir_row = QHBoxLayout()
        dir_row.setSpacing(8)
        self.edt_dir = QLineEdit(str(_default_install_dir()))
        self.edt_dir.setFixedHeight(32)
        dir_row.addWidget(self.edt_dir, 1)
        self.btn_browse = QPushButton("Browse...")
        self.btn_browse.setFixedHeight(32)
        self.btn_browse.setMinimumWidth(88)
        self.btn_browse.clicked.connect(self._browse_dir)
        dir_row.addWidget(self.btn_browse)
        c_vbox.addLayout(dir_row)

        self.chk_desktop = QCheckBox("Create Desktop shortcut")
        self.chk_desktop.setChecked(True)
        c_vbox.addWidget(self.chk_desktop)

        self.chk_start = QCheckBox("Create Start Menu shortcut")
        self.chk_start.setChecked(True)
        c_vbox.addWidget(self.chk_start)

        self.chk_launch = QCheckBox("Launch OmniSight when installation finishes")
        self.chk_launch.setChecked(True)
        c_vbox.addWidget(self.chk_launch)

        root.addWidget(card)

        self.lbl_status = QLabel("Ready to install.")
        self.lbl_status.setStyleSheet("color: #a1a1aa; font-size: 11px;")
        root.addWidget(self.lbl_status)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(12)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        root.addWidget(self.progress_bar)

        root.addStretch(1)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(8)
        btn_row.addStretch(1)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setFixedHeight(34)
        self.btn_cancel.setMinimumWidth(92)
        self.btn_cancel.clicked.connect(self.close)
        btn_row.addWidget(self.btn_cancel)

        self.btn_install = QPushButton("Install Now")
        self.btn_install.setObjectName("primaryBtn")
        self.btn_install.setFixedHeight(34)
        self.btn_install.setMinimumWidth(112)
        self.btn_install.clicked.connect(self._start_install)
        btn_row.addWidget(self.btn_install)
        root.addLayout(btn_row)

    def _browse_dir(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "Select Installation Folder", self.edt_dir.text())
        if chosen:
            self.edt_dir.setText(str(Path(chosen) / "OmniSight"))

    def _start_install(self) -> None:
        if self._installed_exe:
            if self.chk_launch.isChecked() and os.path.isfile(self._installed_exe):
                subprocess.Popen([self._installed_exe], cwd=str(Path(self._installed_exe).parent))
            self.close()
            return

        target_dir = Path(self.edt_dir.text().strip())
        self.btn_install.setEnabled(False)
        self.btn_browse.setEnabled(False)
        self.edt_dir.setEnabled(False)
        self.chk_desktop.setEnabled(False)
        self.chk_start.setEnabled(False)

        self._worker = InstallWorker(
            install_dir=target_dir,
            create_desktop=self.chk_desktop.isChecked(),
            create_start_menu=self.chk_start.isChecked(),
        )
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_finished)
        threading.Thread(target=self._worker.run, daemon=True).start()

    def _on_progress(self, pct: int, msg: str) -> None:
        self.progress_bar.setValue(pct)
        self.lbl_status.setText(msg)

    def _on_finished(self, ok: bool, msg: str, exe_path: str) -> None:
        self.lbl_status.setText(msg)
        if ok:
            self._installed_exe = exe_path
            self.btn_cancel.setVisible(False)
            self.btn_install.setText("Finish")
            self.btn_install.setEnabled(True)
            if self.chk_launch.isChecked() and os.path.isfile(exe_path):
                subprocess.Popen([exe_path], cwd=str(Path(exe_path).parent))
                self.close()
        else:
            self.btn_install.setEnabled(True)
            self.btn_browse.setEnabled(True)
            self.edt_dir.setEnabled(True)
            QMessageBox.critical(self, "Installation Error", msg)


def main() -> None:
    if "--silent" in sys.argv:
        app = QApplication(sys.argv)
        worker = InstallWorker(_default_install_dir(), True, True)
        worker.run()
        return

    app = QApplication(sys.argv)
    win = InstallerWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
