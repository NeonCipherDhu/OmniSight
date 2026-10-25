# V380 Pro Windows Studio

A native Windows desktop security camera client for **V380 / V380 Pro** IP cameras, built with Python, PyQt6, OpenCV, FFmpeg, and the native **V380 TCP Port 8800 / UDP 10008** local network protocol.

---

## ✨ Features

- **Native V380 LAN Protocol (Port 8800)** — Connects directly to V380 and V380 Pro cameras over your local Wi-Fi/LAN without requiring RTSP to be unlocked:
  - Dual-stage AES-128-ECB password authentication (`macrovideo+*#!^@` key handshake, `cmd 1167` -> `resp 1168`, supporting both modern `v31` and classic `v2` firmware)
  - Stream login & control (`cmd 301` -> `resp 401` -> `cmd 303`)
  - Automatic firmware `v31+` media frame AES-ECB decryption
  - Smart **H.264** & **H.265 (HEVC)** Annex-B NAL unit extraction piped into a zero-latency FFmpeg decoder
  - **Live Audio** (`8000 Hz` IMA-ADPCM decoding to 16-bit PCM with per-tile mute/unmute & live audio level meter)
- **LAN Auto-Discovery** — Scans your local network using both:
  1. Native V380 UDP broadcast (`NVDEVSEARCH^100` on UDP ports `10008`/`10009`)
  2. Multi-threaded TCP Port `8800` subnet scanner fallback
- **Multi-Camera Grid Layouts** — Switch between **1×1**, **2×2**, and **3×3** views, double-click any camera feed to maximize/restore, scroll the mouse wheel for **1.0×–5.0× digital zoom**, or press `F11` for **Fullscreen**.
- **PTZ, Floodlight & Night Vision Controls** — Directional Pan/Tilt D-Pad (mouse press-and-hold or keyboard arrow keys), LED Light (`On` / `Off` / `Auto`), Night Vision (`Color` / `IR B&W`), and `180° Image Flip`.
- **Snapshots & MP4 Recording** — Capture instant `.jpg` snapshots (`captures/snapshots/`) and `.mp4` video recordings (`captures/recordings/`).
- **RTSP & Built-In Simulator Support** — Also supports standard RTSP streams (`rtsp://...:554/live/ch00_1`) and includes 2 interactive simulated V380 Pro cameras out of the box so you can test the multi-camera grid immediately.

---

## 🚀 Quick Start

### 1. Launch the App
Double-click **[`run_v380.bat`](run_v380.bat)** in Windows Explorer, or run from PowerShell / Command Prompt:

```powershell
python v380_app.py
```

### 2. Connect Your Real V380 Camera on LAN
1. Click **🔍 Auto-Discover LAN (Port 8800)** in the top toolbar to scan your Wi-Fi/LAN for V380 cameras, **or** click **➕ Add Camera**.
2. Set **Connection Mode** to `Native V380 Protocol (TCP Port 8800)`.
3. Enter:
   - **Camera IP Address**: Your camera's local IP (e.g. `192.168.0.105`)
   - **TCP Port**: `8800` (default)
   - **V380 Device ID**: The 8–10 digit numeric ID printed on the camera's QR sticker or shown in the V380 mobile app under *Device Info*
   - **Username**: `admin` (default)
   - **Password**: The password you set for the camera in the V380 mobile app
4. Click **Save** — the tile will immediately authenticate and start streaming video and audio.
