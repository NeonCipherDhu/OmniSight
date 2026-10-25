# V380 Protect — Windows NVR / VMS Desktop Client

A native Windows desktop security camera client (NVR / VMS) for **V380 / V380 Pro** IP cameras, built with Python, PyQt6, OpenCV, FFmpeg, ONNX Runtime, and the native **V380 TCP Port 8800 / UDP 10008** local network protocol.

---

## ✨ Features

- **Native V380 LAN Protocol (Port 8800)** — Connects directly to V380 and V380 Pro cameras over your local Wi-Fi/LAN without requiring RTSP to be unlocked:
  - Dual-stage AES-128-ECB password authentication (`macrovideo+*#!^@` key handshake, `cmd 1167` -> `resp 1168`, supporting both modern `v31/v32` and classic `v2` firmware)
  - Stream login & control (`cmd 301` -> `resp 401` -> `cmd 303`) with instant **HD / SD** stream switching
  - Automatic firmware `v31/v32+` media frame AES-ECB decryption
  - Smart **H.264** & **H.265 (HEVC)** Annex-B NAL unit extraction piped into a zero-latency FFmpeg decoder (with Direct3D 11 `d3d11va` hardware acceleration)
  - **Live Audio** (`AAC-LC`, `G.711 A-Law`, and `IMA-ADPCM` decoding to 16-bit PCM with per-tile mute/unmute & master audio gain)
- **YOLOv8 AI Person Detection (DirectML GPU Accelerated)** — Built-in `yolov8n.onnx` person detector with 3.2× far-field optical zoom (SAHI) for distant walkers, wind/foliage flicker suppression, and Standard / High / Ultra sensitivity modes.
- **On-Camera microSD Card Footage Review & Backup** — Browse, filter (`All` / `Motion` / `Anomaly`), scrub on an interactive 24-hour zoomable timeline, stream directly from the camera's microSD card (`cmd 361`/`362`/`363`), and export or bulk-backup clips to local `.mp4` files with up to `16×` fast-forward playback.
- **PTZ, Hardware Presets, Floodlight & Night Vision Controls** — Directional Pan/Tilt gimbal control (mouse press-and-hold or keyboard arrow keys), up to 6 persistent hardware PTZ preset slots (`PTZX`), LED Floodlight (`On` / `Off` / `Auto`), Night Vision (`Color` / `IR B&W` / `Auto`), and `180° Image Flip`.
- **Multi-Camera Grid Layouts** — Switch between **Auto**, **1×1**, **2×2**, and **3×3** views, drag-and-drop tiles to reorder, double-click any camera feed to solo/maximize, scroll the mouse wheel for **1.0×–5.0× digital zoom**, or press `F11` for **Fullscreen Monitor Mode**.

---

## 🚀 Quick Start

### 1. Install Dependencies
```powershell
pip install -r requirements.txt
```

### 2. Launch V380 Protect
Double-click **[`run_v380.bat`](run_v380.bat)** in Windows Explorer, or run from PowerShell / Command Prompt:

```powershell
python v380_app.py
```

*(Optional)* To build the standalone Windows `.exe` and Setup Installer (`installer/V380Protect_Setup.exe`):
```powershell
python build_installer.py
```

---

## 📹 How to Connect a Camera

 Make sure your Windows PC and your V380 camera are connected to the **same local network / Wi-Fi router** (or routable local subnets such as `192.168.0.x` and `192.168.1.x`).

### Method 1: Automatic LAN Discovery (Recommended)

1. Click **Discover** in the top header bar (or click **Add Camera** -> **Discover** next to the IP Address field).
2. V380 Protect will scan your local network using:
   - Native V380 UDP broadcast & unicast (`NVDEVSEARCH^100` on UDP ports `10008`/`10009`)
   - Multi-threaded TCP Port `8800` subnet probe
3. Select your camera from the discovered list and click **Select Camera**.
4. The **IP Address**, **TCP Port (`8800`)**, and **Device ID** will be filled in automatically.
5. Enter your **Camera Password** and click **Save**.

---

### Method 2: Manually Adding a Camera

If your router blocks UDP discovery broadcasts or your camera is on a different subnet/VLAN, you can add it manually:

1. Click **Add Camera** (`+`) in the top header bar.
2. Fill in the camera configuration fields:
   - **Camera Name**: Any descriptive label (e.g., `Front Gate`, `Garage`, `Living Room`).
   - **Protocol**: Select `V380 Native Protocol (TCP 8800)`.
   - **IP Address**: Your camera's local IPv4 address (e.g., `192.168.1.105`).
   - **TCP Port**: `8800` (default V380 native port).
   - **Device ID**: Your camera's 8- to 10-digit numeric V380 Device ID (e.g., `12345678`).
   - **Username**: `admin` (default — V380 Protect also automatically tries your numeric Device ID if needed).
   - **Password**: Your camera's device password.
   - **Stream Profile**: `HD (Main Stream)` or `SD (Sub Stream)`.
3. Click **Save** — the tile will immediately authenticate and start streaming live video and audio.

---

## 🔎 Where to Find Your Camera Details (Manual Setup Guide)

If you are adding a camera manually and do not know its **Device ID**, **IP Address**, or **Password**, here is where to find each detail:

| Field | Where to Find It |
| :--- | :--- |
| **Device ID**<br>*(8–10 digit number)* | **Option A — In the V380 / V380 Pro Mobile App:**<br>1. Open the **V380** or **V380 Pro** app on your phone.<br>2. Tap the **`...` (Settings / Gear icon)** on your camera.<br>3. Tap **Device Info** (or **About Device**).<br>4. Copy the numeric **Device ID** shown at the top.<br><br>**Option B — On the Camera Body / Sticker:**<br>Look at the printed QR code sticker on the side, back, or bottom base of the camera. The numeric code labeled **`ID:`**, **`Device ID:`**, or printed directly below the QR code is your Device ID. |
| **IP Address**<br>*(e.g. `192.168.1.105`)* | **Option A — In the V380 / V380 Pro Mobile App:**<br>1. Open **Settings (`...`)** -> **Device Info** (or **Network** / **Wi-Fi Settings**).<br>2. Look for **IP Address** / **Local IP** (e.g., `192.168.1.xxx`).<br><br>**Option B — In Your Wi-Fi Router Admin Page:**<br>Log into your router's web interface (typically `192.168.1.1` or `192.168.0.1`) and check the **Connected Devices / DHCP Client List**. Look for the camera's MAC address or hostname.<br><br>💡 *Tip: Bind the camera's MAC address to a **Static IP / DHCP Reservation** in your router settings so its IP address never changes after a power outage.* |
| **Password** | Use the **device password** you set when you first configured the camera in the V380 mobile app.<br><br>⚠️ *Note: This is the password for the **camera itself**, not your V380 cloud account login password. If you never set one or forgot it, open the V380 mobile app -> **Settings (`...`)** -> **Change Device Password** (or **Password Settings**) to set or update it.* |
| **Username** | Leave as **`admin`** (the factory default on all V380 / V380 Pro cameras). During login, V380 Protect automatically tests your entered username, your numeric Device ID, and `admin`. |
| **TCP Port** | Leave as **`8800`** for native V380 connections. |
| **RTSP URL**<br>*(RTSP Mode only)* | Only needed if you select `RTSP / ONVIF Stream (TCP 554)` instead of the native V380 protocol.<br>Format: `rtsp://admin:YOUR_PASSWORD@192.168.1.100:554/live/ch00_1` (`ch00_1` = HD, `ch00_0` = SD). |

---

## 📁 Configuration File (`cameras.json`)

Camera settings are automatically saved to `cameras.json` in the application folder when you add or edit cameras in the UI. You can also create `cameras.json` manually by copying [`cameras.example.json`](cameras.example.json):

```json
[
  {
    "cam_id": "v380_example01",
    "name": "Camera 01",
    "protocol": "v380",
    "ip": "192.168.1.100",
    "port": 8800,
    "device_id": "12345678",
    "username": "admin",
    "password": "YOUR_CAMERA_PASSWORD",
    "quality": 1,
    "rtsp_url": "",
    "audio_enabled": true,
    "light_mode": "auto",
    "image_mode": "auto",
    "flipped": false,
    "person_detection": true,
    "detection_sensitivity": "ultra",
    "gpu_acceleration": true,
    "ptz_presets": []
  }
]
```
