# OmniSight (Unofficial Client for V380 Cameras)

> **Disclaimer:** This project is an independent, unofficial open-source client. It is not affiliated with, endorsed by, sponsored by, or connected to Macro-Video Technologies Co., Ltd. or the official V380 / V380 Pro brands. All product and company names are trademarks of their respective holders; their use here is strictly for identification and compatibility purposes.

**OmniSight** is an independent, open-source Windows desktop NVR / VMS client. It is an **unofficial client compatible with V380 cameras**, built for local Wi-Fi/LAN live streaming, PTZ camera control, microSD card playback, and AI person detection.

---

## ✨ Features

- **Local Wi-Fi / LAN Streaming** — Connect directly to compatible V380 cameras over your local network without needing to unlock RTSP:
  - Compatible with both newer and classic camera firmware versions
  - Instant **HD / SD** video quality switching
  - Smooth **H.264** & **H.265 (HEVC)** video decoding with Direct3D 11 hardware acceleration
  - **Live Audio** with per-camera mute/unmute and master volume boost
- **YOLOv8 AI Person Detection (GPU Accelerated)** — Built-in AI person detector with far-field zoom for distant walkers, foliage/wind false-alarm filtering, and Standard / High / Ultra sensitivity modes:
  - **Acoustic Detection Chime & Cooldown** — Optional two-note chime on person detection with per-camera mute/unmute toggle and configurable `5s`, `10s`, or `20s` alert cooldown.
  - **Custom Chime Trigger Zone** — Interactive on-screen drag-and-resize zone editor so audio alerts only trigger when a person enters a specific doorway, gate, or driveway area.
  - **One-Click False-Positive Suppression (`✕ Not a Person`)** — Hover over any detected box on the live video (or use the right-click menu) to ignore stationary objects without blinding the area to real people walking through.
- **Live Tile Status & Automatic Camera Wakeup** — Real-time Wi-Fi connection badge, active AI shield indicator, smooth blurred last-frame overlay during reconnection, and automatic wakeup for cameras that enter sleep or standby mode.
- **Built-In Security Protection** — Saved camera passwords and stream credentials are automatically encrypted on your PC, network connections are validated for safety, and the setup installer includes built-in file verification checks.
- **On-Camera microSD Card Footage Review & Backup** — Browse, filter (`All` / `Anomalies` / `Normal`), scrub on an interactive 24-hour zoomable timeline, stream directly from the camera's microSD card with full audio playback, and export or bulk-backup clips to local `.mp4` files with up to `16×` fast-forward playback.
- **PTZ, Hardware Presets, Floodlight & Night Vision Controls** — Directional Pan/Tilt gimbal control (on-video edge buttons, side panel D-pad, or keyboard arrow keys), up to 6 saved camera angle presets, LED Floodlight (`On` / `Off` / `Auto`), Night Vision (`Color` / `IR B&W` / `Auto`), and `180° Image Flip`.
- **Multi-Camera Grid Layouts** — Switch between **Auto**, **1×1**, **2×2**, and **3×3** views, drag-and-drop tiles to reorder, double-click any camera feed to solo/maximize, scroll the mouse wheel for **1.0×–5.0× digital zoom**, or press `F11` for **Fullscreen Monitor Mode**.

---

## 📦 Installation & Quick Start

You do **not** need Python or FFmpeg installed on your PC if you use the prebuilt Windows Setup Installer or Portable ZIP from [**GitHub Releases**](https://github.com/NeonCipherDhu/OmniSight/releases/latest).

### Option 1: Windows Setup Installer (`OmniSight_Setup.exe` — Recommended)

1. Go to the [**Latest Release**](https://github.com/NeonCipherDhu/OmniSight/releases/latest) page and download **`OmniSight_Setup.exe`**.
2. Run **`OmniSight_Setup.exe`**.
   - *Note: If Windows SmartScreen displays "Windows protected your PC", click **More info** → **Run anyway**.*
3. Choose your installation folder (defaults to `%LOCALAPPDATA%\Programs\OmniSight`), select whether to create **Desktop** and **Start Menu** shortcuts, and click **Install Now**.
4. Click **Finish** to launch **OmniSight**.
   - *Upgrading:* Installing a newer version over an existing folder automatically keeps your saved cameras, custom names, and recorded videos/snapshots.

### Option 2: Portable Version (`OmniSight_Portable.zip` — No Installation Required)

1. Go to the [**Latest Release**](https://github.com/NeonCipherDhu/OmniSight/releases/latest) page and download **`OmniSight_Portable.zip`**.
2. Extract the ZIP archive into any folder on your PC or external drive (for example, `C:\Tools\OmniSight` or `D:\OmniSight`).
3. Double-click **`OmniSight.exe`** inside the extracted folder to launch the app.
   - All saved camera settings (with encrypted passwords) and media captures (`snapshots`, `recordings`, `presets`) are stored directly inside that portable folder.

### Option 3: Run from Source (Developers / Python 3.10+)

#### 1. Install Dependencies
```powershell
pip install -r requirements.txt
```

#### 2. Launch OmniSight
Double-click **[`run_omnisight.bat`](run_omnisight.bat)** in Windows Explorer, or run from PowerShell / Command Prompt:

```powershell
python omnisight_app.py
```

*(Optional)* To build the standalone Windows `.exe`, Portable ZIP (`installer/OmniSight_Portable.zip`), and Setup Installer (`installer/OmniSight_Setup.exe`):
```powershell
python build_installer.py
```

---

## 📹 How to Connect a Camera

Make sure your Windows PC and your camera are connected to the **same local network / Wi-Fi router** (or routable local subnets such as `192.168.0.x` and `192.168.1.x`).

### Method 1: Automatic LAN Discovery (Recommended)

1. Click **Discover** in the top header bar (or click **Add Camera** -> **Discover** next to the IP Address field).
2. OmniSight will automatically scan your local Wi-Fi/LAN for compatible cameras.
3. Select your camera from the discovered list and click **Select Camera**.
4. The **IP Address**, **TCP Port (`8800`)**, and **Device ID** will be filled in automatically.
5. Enter your **Camera Password** and click **Save** (your password is automatically encrypted when saved).

---

### Method 2: Manually Adding a Camera

If your router blocks local discovery broadcasts or your camera is on a different subnet/VLAN, you can add it manually:

1. Click **Add Camera** (`+`) in the top header bar.
2. Fill in the camera configuration fields:
   - **Camera Name**: Any descriptive label (e.g., `Front Gate`, `Garage`, `Living Room`).
   - **Protocol**: Select `LAN Protocol TCP 8800 (Compatible with V380 cameras)`.
   - **IP Address**: Your camera's local IPv4 address (e.g., `192.168.1.105`).
   - **TCP Port**: `8800` (default LAN port).
   - **Device ID**: Your camera's 8- to 10-digit numeric Device ID (e.g., `12345678`).
   - **Username**: `admin` (default — OmniSight also automatically tries your numeric Device ID if needed).
   - **Password**: Your camera's device password.
   - **Stream Profile**: `HD (Main Stream)` or `SD (Sub Stream)`.
3. Click **Save** — your settings are saved with encrypted credentials and the camera tile will immediately start streaming live video and audio.

---

## 🔎 Where to Find Your Camera Details (Manual Setup Guide)

If you are adding a camera manually and do not know its **Device ID**, **IP Address**, or **Password**, here is where to find each detail:

| Field | Where to Find It |
| :--- | :--- |
| **Device ID**<br>*(8–10 digit number)* | **Option A — In the Camera's Mobile App:**<br>1. Open the mobile app used to set up your camera.<br>2. Tap the **`...` (Settings / Gear icon)** on your camera.<br>3. Tap **Device Info** (or **About Device**).<br>4. Copy the numeric **Device ID** shown at the top.<br><br>**Option B — On the Camera Body / Sticker:**<br>Look at the printed QR code sticker on the side, back, or bottom base of the camera. The numeric code labeled **`ID:`**, **`Device ID:`**, or printed directly below the QR code is your Device ID. |
| **IP Address**<br>*(e.g. `192.168.1.105`)* | **Option A — In the Camera's Mobile App:**<br>1. Open **Settings (`...`)** -> **Device Info** (or **Network** / **Wi-Fi Settings**).<br>2. Look for **IP Address** / **Local IP** (e.g., `192.168.1.xxx`).<br><br>**Option B — In Your Wi-Fi Router Admin Page:**<br>Log into your router's web interface (typically `192.168.1.1` or `192.168.0.1`) and check the **Connected Devices / DHCP Client List**. Look for the camera's MAC address or hostname.<br><br>💡 *Tip: Bind the camera's MAC address to a **Static IP / DHCP Reservation** in your router settings so its IP address never changes after a power outage.* |
| **Password** | Use the **device password** you set when you first configured the camera.<br><br>⚠️ *Note: This is the password for the **camera device itself**, not your cloud account login password. If you never set one or forgot it, open the mobile app -> **Settings (`...`)** -> **Change Device Password** (or **Password Settings**) to set or update it.* |
| **Username** | Leave as **`admin`** (the factory default). During login, OmniSight automatically tests your entered username, your numeric Device ID, and `admin`. |
| **TCP Port** | Leave as **`8800`** for LAN port 8800 connections. |
| **RTSP URL**<br>*(RTSP Mode only)* | Only needed if you select `RTSP / ONVIF Stream (TCP 554)` instead of the LAN 8800 protocol.<br>Format: `rtsp://admin:YOUR_PASSWORD@192.168.1.100:554/live/ch00_1` (`ch00_1` = HD, `ch00_0` = SD). |

---

## 📄 License

This project is licensed under the **MIT License** — see the [LICENSE](LICENSE) file for details.
