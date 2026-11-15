"""
OmniSight LAN Protocol Implementation (TCP Port 8800 & UDP 10008/10009).
Unofficial client compatible with V380 cameras.

Supports:
- UDP Broadcast LAN Auto-Discovery (port 10008/10009 `NVDEVSEARCH^100`)
- Active TCP Port 8800 Subnet Scanner fallback (including 192.168.1.x & local subnets)
- Dual-Stage AES-128-ECB Password Encryption
- Auth Handshake (`cmd 1167` -> `resp 1168`, both v31/v32 and legacy v2 variants, with auto-username/port fallback)
- Stream Login & Start (`cmd 301` -> `resp 401` -> `cmd 303`)
- Media Frame Reassembly (`0x7F` 12-byte fragment headers)
- Firmware v31/v32+ Media Decryption (16-byte outer header + 80-byte AES-128-ECB video stride & audio decryption)
- Automatic H.264 (`0x00`/`0x01`) and H.265/HEVC (`0x28`/`0x29`) Annex-B NAL extraction
- Audio extraction for both AAC-LC ADTS (`0x18`) and IMA-ADPCM (`0x16`/`0x1A`)
- PTZ (Pan/Tilt/Zoom), IR/White Light, and Image Flip/Color controls
"""

from __future__ import annotations

import base64
import hashlib
import ipaddress
import json
import os
import random
import re
import secrets
import socket
import ssl
import string
import struct
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

from Crypto.Cipher import AES


class VideoCodec(str, Enum):
    UNKNOWN = "unknown"
    H264 = "h264"
    H265 = "hevc"


@dataclass
class DiscoveredCamera:
    dev_id: str
    ip: str
    mac: str = ""
    port: int = 8800
    name: str = ""
    discovery_method: str = "UDP Broadcast"
    protocol: str = "lan8800"  # "lan8800" | "hsc"

    def display_title(self) -> str:
        label = self.name or (f"Camera ({self.dev_id})" if self.dev_id else f"Camera @ {self.ip}")
        if self.protocol == "hsc":
            return f"{label} [{self.ip} • V380 Cloud/HSC]"
        return f"{label} [{self.ip}:{self.port}]"


@dataclass
class CameraFrame:
    is_audio: bool
    raw_type: int
    payload: bytes
    codec: VideoCodec = VideoCodec.UNKNOWN
    is_keyframe: bool = False
    frame_id: int = 0
    frame_type: int = 0
    frame_rate: int = 20
    timestamp: int = 0
    audio_codec: str = "adpcm"  # "adpcm" | "aac" | "alaw" | "pcm16"


@dataclass
class RecordingSegment:
    """
    Represents a single recording clip stored on a camera's microSD card
    (returned by Segment Search `cmd 361` / `cmd 362` or HSC `0x30220`).
    """

    seg_id: int
    raw_type: int  # 1 = Continuous/Normal, 2 = Motion, 3 = Anomaly/Alarm
    start_ts: int  # Camera local wall-clock seconds stored as UTC epoch seconds
    end_ts: int    # Camera local wall-clock seconds stored as UTC epoch seconds
    hsc_sts_ms: int = 0  # Optional UTC epoch milliseconds for V380 HSC SD playback

    @property
    def duration_sec(self) -> int:
        return max(0, int(self.end_ts) - int(self.start_ts))

    @property
    def is_anomaly(self) -> bool:
        return int(self.raw_type) in (2, 3)

    @property
    def kind_code(self) -> str:
        if self.raw_type == 3:
            return "alarm"
        if self.raw_type == 2:
            return "motion"
        return "normal"

    @property
    def kind_label(self) -> str:
        if self.raw_type == 3:
            return "ANOMALY"
        if self.raw_type == 2:
            return "MOTION"
        return "NORMAL"

    @property
    def start_time_str(self) -> str:
        return datetime.fromtimestamp(self.start_ts, timezone.utc).strftime("%I:%M:%S %p")

    @property
    def end_time_str(self) -> str:
        return datetime.fromtimestamp(self.end_ts, timezone.utc).strftime("%I:%M:%S %p")

    @property
    def date_str(self) -> str:
        dt = datetime.fromtimestamp(self.start_ts, timezone.utc)
        return f"{dt.strftime('%B')} {dt.day}, {dt.year}"

    @property
    def iso_date_str(self) -> str:
        return datetime.fromtimestamp(self.start_ts, timezone.utc).strftime("%Y-%m-%d")

    @property
    def duration_str(self) -> str:
        sec = self.duration_sec
        if sec >= 60:
            m, s = divmod(sec, 60)
            return f"{m}m {s:02d}s"
        return f"{sec}s"


class LanCameraCommands:
    """Binary control packets sent over the active camera stream socket."""

    PTZ_RIGHT = bytes([0xAA, 0x00, 0x00, 0x00, 0xE8, 0x03, 0xE8, 0x03, 0xEA, 0x03, 0xE8, 0x03, 0x00, 0x00, 0x01, 0x00])
    PTZ_LEFT  = bytes([0xAA, 0x00, 0x00, 0x00, 0xE8, 0x03, 0xE8, 0x03, 0xE9, 0x03, 0xE8, 0x03, 0x00, 0x00, 0x01, 0x00])
    PTZ_UP    = bytes([0xAA, 0x00, 0x00, 0x00, 0xE8, 0x03, 0xE8, 0x03, 0xE8, 0x03, 0xEB, 0x03, 0x00, 0x00, 0x01, 0x00])
    PTZ_DOWN  = bytes([0xAA, 0x00, 0x00, 0x00, 0xE8, 0x03, 0xE8, 0x03, 0xE8, 0x03, 0xEC, 0x03, 0x00, 0x00, 0x01, 0x00])
    PTZ_STOP  = bytes([0xAA, 0x00, 0x00, 0x00, 0xE8, 0x03, 0xE8, 0x03, 0xE8, 0x03, 0xE8, 0x03, 0x00, 0x00, 0x01, 0x00])

    LIGHT_ON   = bytes([0xC4, 0x00, 0x00, 0x00, 0xE9, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    LIGHT_OFF  = bytes([0xC4, 0x00, 0x00, 0x00, 0xEA, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    LIGHT_AUTO = bytes([0xC4, 0x00, 0x00, 0x00, 0xEB, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    IMAGE_COLOR = bytes([0xC5, 0x00, 0x00, 0x00, 0xE9, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    IMAGE_BW    = bytes([0xC5, 0x00, 0x00, 0x00, 0xEA, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    IMAGE_AUTO  = bytes([0xC5, 0x00, 0x00, 0x00, 0xEB, 0x03, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])
    IMAGE_FLIP  = bytes([0xBE, 0x00, 0x00, 0x00, 0xE8, 0x03, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00])

    # Hardware PTZ Preset (PTZX) Actions (Defines.NV_PRESET_ACTION_*)
    PTZX_ACTION_ADD   = 100  # Save current gimbal position to slot
    PTZX_ACTION_DEL   = 101  # Delete preset slot
    PTZX_ACTION_RESET = 102  # Overwrite / reset preset slot
    PTZX_ACTION_GOTO  = 103  # Auto-pan/tilt camera to preset slot

    @staticmethod
    def ptzx_packet(action: int, slot_id: int) -> bytes:
        """Build 16-byte SetPTZXParam (0xAB) control packet for active live stream socket."""
        return struct.pack("<IIII", 0xAB, int(action), int(slot_id), 0)


# IMA-ADPCM step and index tables used by 8000 Hz, 16-bit mono camera audio
_IMA_STEP_TABLE = [
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17,
    19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118,
    130, 143, 157, 173, 190, 209, 230, 253, 279, 307,
    337, 371, 408, 449, 494, 544, 598, 658, 724, 796,
    876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066,
    2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358,
    5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487, 12635, 13899,
    15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
]

_IMA_INDEX_TABLE = [
    -1, -1, -1, -1, 2, 4, 6, 8,
    -1, -1, -1, -1, 2, 4, 6, 8,
]


def _build_alaw_table() -> List[int]:
    table: List[int] = []
    for a in range(256):
        a_val = a ^ 0x55
        t = (a_val & 0x0F) << 4
        seg = (a_val & 0x70) >> 4
        if seg == 0:
            t += 8
        elif seg == 1:
            t += 0x108
        else:
            t += 0x108
            t <<= seg - 1
        table.append(t if (a_val & 0x80) else -t)
    return table


_ALAW_TO_PCM16 = _build_alaw_table()


def decode_alaw_to_pcm16(alaw_bytes: bytes) -> bytes:
    """
    Decode 0x1A G.711 A-Law (PCMA, 8000 Hz, mono) audio payload into signed 16-bit LE PCM.
    """
    if not alaw_bytes:
        return b""
    out = bytearray(len(alaw_bytes) * 2)
    for idx, b in enumerate(alaw_bytes):
        struct.pack_into("<h", out, idx * 2, _ALAW_TO_PCM16[b])
    return bytes(out)


def decode_adpcm_to_pcm16(adpcm_bytes: bytes) -> bytes:
    """
    Decode IMA-ADPCM audio payload into signed 16-bit little-endian PCM (8000 Hz, mono).
    """
    if len(adpcm_bytes) < 4:
        return b""

    predictor = struct.unpack_from("<h", adpcm_bytes, 0)[0]
    step_index = adpcm_bytes[2]
    if step_index < 0:
        step_index = 0
    elif step_index > 88:
        step_index = 88

    step = _IMA_STEP_TABLE[step_index]
    out = bytearray()
    out.extend(struct.pack("<h", predictor))

    for byte_val in adpcm_bytes[4:]:
        for nibble in (byte_val & 0x0F, (byte_val >> 4) & 0x0F):
            step_index += _IMA_INDEX_TABLE[nibble]
            if step_index < 0:
                step_index = 0
            elif step_index > 88:
                step_index = 88

            sign = nibble & 8
            delta = nibble & 7
            diff = step >> 3
            if delta & 4:
                diff += step
            if delta & 2:
                diff += step >> 1
            if delta & 1:
                diff += step >> 2

            if sign:
                predictor -= diff
            else:
                predictor += diff

            if predictor < -32768:
                predictor = -32768
            elif predictor > 32767:
                predictor = 32767

            step = _IMA_STEP_TABLE[step_index]
            out.extend(struct.pack("<h", predictor))

    return bytes(out)


def downsample_pcm16_2x(pcm16_bytes: bytes) -> bytes:
    """
    Decimate 16000 Hz signed 16-bit LE mono PCM by 2x into 8000 Hz signed 16-bit LE mono PCM
    using a 2-tap boxcar anti-aliasing average.
    """
    total_samples = len(pcm16_bytes) // 2
    if total_samples <= 0:
        return b""
    if total_samples == 1:
        return pcm16_bytes[:2]
    pairs = total_samples // 2
    samples = struct.unpack_from(f"<{pairs * 2}h", pcm16_bytes, 0)
    out = [(samples[2 * i] + samples[2 * i + 1]) >> 1 for i in range(pairs)]
    return struct.pack(f"<{pairs}h", *out)


def get_local_ipv4_addresses() -> List[str]:
    """Return all active local IPv4 addresses on this Windows machine."""
    ips = set()
    try:
        hostname = socket.gethostname()
        for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127.") and not ip.startswith("169.254."):
                ips.add(ip)
    except Exception:
        pass

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            if not ip.startswith("127."):
                ips.add(ip)
    except Exception:
        pass

    return sorted(ips)


class LanCameraDiscovery:
    """
    Discovers compatible LAN cameras on the local network (including un-added cameras across subnets) via:
    1. Native UDP broadcast (`NVDEVSEARCH^100` to port 10008, receiving `NVDEVRESULT` on 10009)
    2. Two-pass paced Unicast UDP `NVDEVSEARCH^100` sweep across local & neighbor subnets
       (wakes idle/un-added Wi-Fi cameras from DTIM sleep, resolves router ARP without overflowing
       `neigh_table`, and works across routers that block broadcast packets between 192.168.0.x & 192.168.1.x)
    3. Multi-subnet TCP port 8800 scanner fallback with targeted UDP `NVDEVSEARCH^100` ID resolution
    """

    SEARCH_CMD = b"NVDEVSEARCH^100"
    _REGISTRY_LOCK = threading.Lock()
    _UDP_PROBE_LOCK = threading.Lock()
    _DETECTED_HSC_DEVICES: set[str] = set()
    _DETECTED_LAN_DEVICES: set[str] = set()
    _NEGATIVE_PROBE_TS: Dict[str, float] = {}
    _ARP_MAC_CACHE: Dict[str, Tuple[float, str]] = {}

    @staticmethod
    def _configure_udp_socket(sock: socket.socket) -> None:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SIO_UDP_CONNRESET"):
            try:
                sock.ioctl(socket.SIO_UDP_CONNRESET, False)
            except Exception:
                pass

    @classmethod
    def _lookup_arp_mac(cls, ip: str) -> str:
        ip_clean = (ip or "").strip()
        if not ip_clean:
            return ""
        now = time.monotonic()
        with cls._REGISTRY_LOCK:
            cached = cls._ARP_MAC_CACHE.get(ip_clean)
            if cached and (now - cached[0]) < 90.0:
                return cached[1]
        mac_found = ""
        try:
            cflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.run(
                ["arp", "-a", ip_clean],
                capture_output=True,
                text=True,
                timeout=1.2,
                creationflags=cflags,
            )
            m = re.search(r"([0-9a-fA-F]{2}(?:[:-][0-9a-fA-F]{2}){5})", proc.stdout or "")
            if m:
                cand = m.group(1).replace("-", ":").lower()
                if cand != "ff:ff:ff:ff:ff:ff":
                    mac_found = cand
        except Exception:
            pass
        with cls._REGISTRY_LOCK:
            cls._ARP_MAC_CACHE[ip_clean] = (now, mac_found)
        return mac_found

    @staticmethod
    def _probe_tcp_8800_open(ip: str, timeout_sec: float = 0.55) -> bool:
        """Return True if local TCP port 8800 accepts connections (sending a safe 520-byte frame)."""
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(timeout_sec)
                if s.connect_ex((ip, 8800)) == 0:
                    probe_pkt = bytearray(520)
                    struct.pack_into("<I", probe_pkt, 0, 1167)
                    struct.pack_into("<I", probe_pkt, 4, 120)
                    probe_pkt[8] = 31
                    struct.pack_into("<I", probe_pkt, 9, 1)
                    s.sendall(probe_pkt)
                    try:
                        s.recv(256)
                    except Exception:
                        pass
                    return True
        except Exception:
            pass
        return False

    @staticmethod
    def _is_lan_ip_reachable(ip: str) -> bool:
        """Return True if `ip` responds to a quick ICMP echo on the local network."""
        try:
            cflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            proc = subprocess.run(
                ["ping", "-n", "1", "-w", "650", ip],
                capture_output=True,
                text=True,
                timeout=1.5,
                creationflags=cflags,
            )
            out = (proc.stdout or "").upper()
            return "TTL=" in out
        except Exception:
            return False

    @staticmethod
    def _probe_legacy_platform1_status(dev_id: int) -> Optional[Tuple[int, int]]:
        """
        Query V380 legacy dispatch (`platform=10001`) and send a neutral `cmd 1167` probe.
        Returns `(sub_status, ver)` from `resp 1168`, where:
        - `sub_status >= 1` means the camera IS registered on legacy Platform-10001 (TCP 8800).
        - `sub_status == -1` and `ver == 2` on a live LAN camera means Platform-2 (HSC).
        """
        try:
            ts_now = int(time.time())
            base = f"dev_id={dev_id}&platform=10001&timestamp={ts_now}hsdata2022"
            sign = hashlib.sha1(base.encode("utf-8")).hexdigest().lower()
            req = urllib.request.Request(
                "http://dispa1.av380.net:8001/api/v1/get_stream_server",
                data=json.dumps(
                    {"dev_id": dev_id, "platform": 10001, "timestamp": ts_now, "sign": sign}
                ).encode("utf-8"),
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
            rows = data.get("data") if isinstance(data, dict) else None
            if not isinstance(rows, list) or not rows:
                return None
            rip = str(rows[0].get("ip") or "").strip()
            if not rip:
                return None

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(2.2)
                s.connect((rip, 8800))
                host_b = f"{dev_id}.nvdvr.net".encode("ascii", errors="ignore")[:50]
                enc_pw = LanCameraClient.generate_password("", block_bytes=48)
                pkt = bytearray(520)
                struct.pack_into("<I", pkt, 0, 1167)
                struct.pack_into("<I", pkt, 4, 1022)
                pkt[8] = 31
                struct.pack_into("<I", pkt, 9, 1)
                struct.pack_into("<I", pkt, 13, dev_id & 0xFFFFFFFF)
                pkt[17 : 17 + len(host_b)] = host_b
                struct.pack_into("<I", pkt, 67, 8800)
                pkt[71 : 76] = b"admin"
                pkt[103 : 103 + len(enc_pw)] = enc_pw
                s.sendall(pkt)
                reply = s.recv(256)
                if reply and len(reply) >= 13:
                    rcmd, _rres = struct.unpack_from("<II", reply, 0)
                    if rcmd == 1168:
                        sub = struct.unpack_from("<i", reply, 8)[0]
                        ver = int(reply[12])
                        return sub, ver
        except Exception:
            pass
        return None

    @classmethod
    def is_hsc_camera(cls, ip: str, device_id: int | str = "") -> bool:
        """
        Return True if the camera at `ip` / `device_id` is a V380 Gen-3 HSC (`NVDEVRESULT^300`)
        cloud/IOTC camera with local TCP port 8800 disabled.
        """
        ip_key = (ip or "").strip()
        dev_key = str(device_id or "").strip()
        with cls._REGISTRY_LOCK:
            if (ip_key and ip_key in cls._DETECTED_HSC_DEVICES) or (
                dev_key and dev_key != "0" and dev_key in cls._DETECTED_HSC_DEVICES
            ):
                return True
            if (ip_key and ip_key in cls._DETECTED_LAN_DEVICES) or (
                dev_key and dev_key != "0" and dev_key in cls._DETECTED_LAN_DEVICES
            ):
                return False
            last_neg = cls._NEGATIVE_PROBE_TS.get(ip_key, 0.0)
            if ip_key and (time.monotonic() - last_neg) < 20.0:
                return False

        # 1. Fast local TCP 8800 check: legacy Gen-2 cameras answer in ~10ms
        if ip_key and cls._probe_tcp_8800_open(ip_key, timeout_sec=0.55):
            with cls._REGISTRY_LOCK:
                cls._DETECTED_LAN_DEVICES.add(ip_key)
                if dev_key and dev_key != "0":
                    cls._DETECTED_LAN_DEVICES.add(dev_key)
            return False

        # 2. Local UDP NVDEVSEARCH^100 check (`NVDEVRESULT^300` vs `NVDEVRESULT^200`)
        if ip_key:
            cam = cls.query_single_ip(ip_key, timeout_sec=0.75)
            if cam is not None:
                return cam.protocol == "hsc"

        # 3. Fallback when HSC camera's UDP 10008 listener is asleep in idle standby:
        #    If the IP is alive on the LAN (ICMP ping OK) with TCP 8800 closed, AND legacy
        #    Platform-10001 relay reports `sub == -1, ver == 2`, it is a Platform-2 HSC camera.
        if ip_key and dev_key.isdigit() and int(dev_key) > 0:
            if cls._is_lan_ip_reachable(ip_key):
                p1 = cls._probe_legacy_platform1_status(int(dev_key))
                if p1 is not None:
                    sub, ver = p1
                    if sub == -1 and ver == 2:
                        with cls._REGISTRY_LOCK:
                            cls._DETECTED_HSC_DEVICES.add(ip_key)
                            cls._DETECTED_HSC_DEVICES.add(dev_key)
                        return True
                    if sub > 0:
                        with cls._REGISTRY_LOCK:
                            cls._DETECTED_LAN_DEVICES.add(ip_key)
                            cls._DETECTED_LAN_DEVICES.add(dev_key)
                        return False

        if ip_key:
            with cls._REGISTRY_LOCK:
                cls._NEGATIVE_PROBE_TS[ip_key] = time.monotonic()
        return False

    @classmethod
    def query_single_ip(cls, target_ip: str, timeout_sec: float = 0.95) -> Optional[DiscoveredCamera]:
        """Send targeted unicast UDP `NVDEVSEARCH^100` to a single IP and wait for `NVDEVRESULT`."""
        with cls._UDP_PROBE_LOCK:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            cls._configure_udp_socket(sock)
            try:
                try:
                    sock.bind(("0.0.0.0", 10009))
                except OSError:
                    sock.bind(("0.0.0.0", 0))
                sock.settimeout(0.05)

                deadline = time.monotonic() + max(0.25, timeout_sec)
                next_send = 0.0
                while time.monotonic() < deadline:
                    now = time.monotonic()
                    if now >= next_send:
                        try:
                            sock.sendto(cls.SEARCH_CMD, (target_ip, 10008))
                            sock.sendto(cls.SEARCH_CMD.ljust(128, b"\x00"), (target_ip, 10008))
                        except Exception:
                            pass
                        next_send = now + 0.20
                    try:
                        data, addr = sock.recvfrom(2048)
                        cam = cls._parse_nvdevresult(data, addr[0], method="UDP Unicast Scan")
                        if cam and (cam.ip == target_ip or addr[0] == target_ip):
                            return cam
                    except socket.timeout:
                        continue
                    except Exception:
                        continue
            finally:
                try:
                    sock.close()
                except Exception:
                    pass
        return None

    @classmethod
    def discover(
        cls,
        timeout_sec: float = 2.0,
        scan_tcp_8800: bool = True,
        progress_cb: Optional[Callable[[str], None]] = None,
    ) -> List[DiscoveredCamera]:
        devices_by_ip: dict[str, DiscoveredCamera] = {}

        local_ips = get_local_ipv4_addresses()
        local_set = set(local_ips)
        subnets = {"192.168.0.0/24", "192.168.1.0/24"}
        broadcast_targets = {"255.255.255.255", "192.168.1.255", "192.168.0.255"}
        for lip in local_ips:
            try:
                net = ipaddress.IPv4Network(f"{lip}/24", strict=False)
                subnets.add(str(net))
                broadcast_targets.add(str(net.broadcast_address))
            except Exception:
                pass

        unicast_targets: List[str] = []
        for net_str in sorted(subnets):
            try:
                net = ipaddress.IPv4Network(net_str, strict=False)
                for host in net.hosts():
                    hip = str(host)
                    if hip not in local_set:
                        unicast_targets.append(hip)
            except Exception:
                pass

        socks: List[socket.socket] = []
        try:
            s_main = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
            cls._configure_udp_socket(s_main)
            try:
                s_main.bind(("0.0.0.0", 10009))
            except OSError:
                s_main.bind(("0.0.0.0", 0))
            s_main.settimeout(0.015)
            socks.append(s_main)

            for lip in local_ips:
                try:
                    s_if = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
                    cls._configure_udp_socket(s_if)
                    s_if.bind((lip, 0))
                    s_if.settimeout(0.005)
                    socks.append(s_if)
                except Exception:
                    pass

            def _drain_replies(wait_sec: float, method_label: str) -> None:
                deadline = time.monotonic() + wait_sec
                while time.monotonic() < deadline:
                    got_any = False
                    for sock in socks:
                        while True:
                            try:
                                data, addr = sock.recvfrom(2048)
                                got_any = True
                                cam = cls._parse_nvdevresult(data, addr[0], method=method_label)
                                if cam:
                                    devices_by_ip[cam.ip] = cam
                            except socket.timeout:
                                break
                            except Exception:
                                break
                    if not got_any:
                        time.sleep(0.01)

            # Stage 1: Broadcast UDP discovery on local segment
            if progress_cb:
                progress_cb("Sending UDP discovery broadcast (port 10008/10009)...")
            for _ in range(2):
                for sock in socks:
                    for bcast in broadcast_targets:
                        try:
                            sock.sendto(cls.SEARCH_CMD, (bcast, 10008))
                        except Exception:
                            pass
                _drain_replies(0.18, "UDP Broadcast")

            # Stage 2: Two-pass paced Unicast UDP discovery across all target subnets
            # Pass 1 warms router ARP tables & wakes idle Wi-Fi cameras from DTIM sleep;
            # Pass 2 delivers NVDEVSEARCH^100 to UDP 10008 and collects NVDEVRESULT on UDP 10009.
            batch_size = 16
            for pass_idx in range(2):
                if progress_cb:
                    subnet_list = ", ".join(sorted(subnets))
                    progress_cb(
                        f"Probing subnets ({subnet_list}) via UDP 10008 (pass {pass_idx + 1}/2) "
                        f"— {len(devices_by_ip)} found..."
                    )
                for start_i in range(0, len(unicast_targets), batch_size):
                    batch = unicast_targets[start_i : start_i + batch_size]
                    for target_ip in batch:
                        try:
                            s_main.sendto(cls.SEARCH_CMD, (target_ip, 10008))
                        except Exception:
                            pass
                    _drain_replies(0.025, "UDP Unicast Scan")
                tail_wait = 0.30 if pass_idx == 0 else max(0.65, timeout_sec * 0.32)
                _drain_replies(tail_wait, "UDP Unicast Scan")
        finally:
            for sock in socks:
                try:
                    sock.close()
                except Exception:
                    pass

        # Stage 3: Active TCP Port 8800 fallback scan + targeted UDP Device ID resolution
        if scan_tcp_8800:
            if progress_cb:
                progress_cb(
                    f"Scanning local subnets for open TCP port 8800 ({len(devices_by_ip)} found via UDP)..."
                )
            known_ips = set(devices_by_ip.keys())
            tcp_hits = cls._scan_subnet_port_8800(local_ips, exclude_ips=known_ips)
            for ip in tcp_hits:
                if ip not in devices_by_ip:
                    resolved = cls.query_single_ip(ip, timeout_sec=0.45)
                    if resolved:
                        devices_by_ip[ip] = resolved
                    else:
                        devices_by_ip[ip] = DiscoveredCamera(
                            dev_id="",
                            ip=ip,
                            mac="",
                            port=8800,
                            name=f"Camera ({ip})",
                            discovery_method="TCP Port 8800 Scan",
                        )

        return sorted(devices_by_ip.values(), key=lambda c: c.ip)

    @classmethod
    def _parse_nvdevresult(
        cls, raw: bytes, source_ip: str, method: str = "UDP Broadcast"
    ) -> Optional[DiscoveredCamera]:
        text = raw.decode("ascii", errors="ignore").strip("\x00\r\n ")
        parts = text.split("^")
        if len(parts) < 13 or parts[0] != "NVDEVRESULT":
            return None

        ver_tag = parts[1].strip()
        mac = parts[2].strip().lower()
        ip = parts[3].strip() or source_ip
        port_str = parts[9].strip() if len(parts) > 9 else "8800"
        dev_id = parts[12].strip()
        plat_gen = parts[14].strip() if len(parts) > 14 else ""

        is_hsc = ver_tag == "300" or (port_str == "0" and plat_gen == "2")
        proto = "hsc" if is_hsc else "lan8800"
        port = int(port_str) if (port_str.isdigit() and int(port_str) > 0) else 8800

        with cls._REGISTRY_LOCK:
            target_set = cls._DETECTED_HSC_DEVICES if is_hsc else cls._DETECTED_LAN_DEVICES
            other_set = cls._DETECTED_LAN_DEVICES if is_hsc else cls._DETECTED_HSC_DEVICES
            if ip:
                target_set.add(ip)
                other_set.discard(ip)
                cls._NEGATIVE_PROBE_TS.pop(ip, None)
            if dev_id and dev_id != "0":
                target_set.add(dev_id)
                other_set.discard(dev_id)

        if not mac and ip:
            mac = cls._lookup_arp_mac(ip)

        method_label = method
        if is_hsc and "HSC" not in method_label:
            method_label = f"{method_label} (V380 Cloud / HSC)"

        return DiscoveredCamera(
            dev_id=dev_id,
            ip=ip,
            mac=mac,
            port=port,
            name=f"Camera ({dev_id})" if dev_id else f"Camera ({ip})",
            discovery_method=method_label,
            protocol=proto,
        )

    @staticmethod
    def _scan_subnet_port_8800(local_ips: List[str], exclude_ips: set[str]) -> List[str]:
        subnets = {"192.168.0.0/24", "192.168.1.0/24"}
        for lip in local_ips[:2]:
            try:
                net = ipaddress.IPv4Network(f"{lip}/24", strict=False)
                subnets.add(str(net))
            except Exception:
                pass

        candidates: List[str] = []
        local_set = set(local_ips)
        for net_str in sorted(subnets):
            try:
                net = ipaddress.IPv4Network(net_str, strict=False)
                for host in net.hosts():
                    hip = str(host)
                    if hip not in local_set and hip not in exclude_ips:
                        candidates.append(hip)
            except Exception:
                pass

        found: List[str] = []

        def _probe(target_ip: str) -> Optional[str]:
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(0.65)
                    if s.connect_ex((target_ip, 8800)) == 0:
                        # Always send a 520-byte handshake frame so the camera's
                        # blocking recv() loop finishes immediately and never locks port 8800.
                        probe_pkt = bytearray(520)
                        struct.pack_into("<I", probe_pkt, 0, 1167)
                        struct.pack_into("<I", probe_pkt, 4, 120)
                        probe_pkt[8] = 31
                        struct.pack_into("<I", probe_pkt, 9, 1)
                        s.sendall(probe_pkt)
                        try:
                            s.recv(256)
                        except Exception:
                            pass
                        return target_ip
            except Exception:
                pass
            return None

        if not candidates:
            return found

        with ThreadPoolExecutor(max_workers=32) as pool:
            futures = [pool.submit(_probe, ip) for ip in candidates]
            for fut in as_completed(futures):
                res = fut.result()
                if res:
                    found.append(res)

        return sorted(found)


class LanCameraClient:
    """
    Unofficial TCP Port 8800 LAN Camera Client (compatible with V380 cameras).
    Handles authentication, stream login, media decryption, NAL extraction, and PTZ/Light controls.
    """

    STATIC_KEY = b"macrovideo+*#!^@"

    def __init__(
        self,
        ip: str,
        port: int = 8800,
        device_id: int | str = 0,
        username: str = "admin",
        password: str = "",
        stream_quality: int = 1,  # 1 = HD, 0 = SD
        enable_audio: bool = True,
        on_frame: Optional[Callable[[CameraFrame], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.ip = ip.strip()
        self.port = int(port)
        self.device_id = int(device_id) if str(device_id).strip().isdigit() else 0
        self.username = username.strip() or "admin"
        self.password = password
        self.stream_quality = int(stream_quality)
        self.enable_audio = enable_audio
        self.on_frame = on_frame
        self.on_status = on_status

        self.auth_ticket: int = 0
        self.session_id: int = 0
        self.device_version: int = 0
        self.communication_version: int = 0
        self.frame_width: int = 1280
        self.frame_height: int = 720
        self.aes_key: bytes = b"\x00" * 16

        self._stream_sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._need_reconnect = False
        self._force_cloud_wake = False
        self._quality_switch_pending = False
        self._last_video_frame_mono: float = 0.0
        self._session_video_frames: int = 0
        self._last_keepalive_mono: float = 0.0
        self._last_udp_wake_mono: float = 0.0
        self._last_encoder_prod_mono: float = 0.0

    _RELAY_IP_CACHE: Dict[int, List[str]] = {}

    @staticmethod
    def _configure_tcp_keepalive(sock: socket.socket) -> None:
        """Enable OS-level TCP keepalive probes (8s idle, 2.5s interval) so idle/cross-subnet streams never drop."""
        try:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            if hasattr(socket, "SIO_KEEPALIVE_VALS"):
                sock.ioctl(socket.SIO_KEEPALIVE_VALS, (1, 8000, 2500))
        except Exception:
            pass

    def _build_stream_start_cmd303(self, result_code: int = 0x3001) -> bytes:
        cmd303 = bytearray(256)
        struct.pack_into("<I", cmd303, 0, 303)
        if self.device_version > 30:
            struct.pack_into("<H", cmd303, 4, 0x3001)
        else:
            struct.pack_into("<i", cmd303, 4, result_code if result_code in (402, 1001) else 0x3001)
        return bytes(cmd303)

    def send_wake_pulse(self) -> None:
        """
        Send an active anti-sleep wakeup pulse over both UDP (10008/10009/5040) and the active
        TCP 8800 stream socket (`PTZ_STOP` framed neutral heartbeat).
        Prevents V380 cameras from entering idle/no-person sleep without corrupting the framed stream.
        """
        self._wake_camera_lan_udp(burst_count=2)
        if self._stream_sock is not None:
            self._send_raw(LanCameraCommands.PTZ_STOP)

    def trigger_wake_reconnect(self) -> None:
        """Force immediate stream reconnect with full LAN + Cloud wakeup sequence."""
        self._force_cloud_wake = True
        self._need_reconnect = True
        self._close_stream_sock()

    def set_stream_quality(self, quality: int) -> None:
        """Switch between HD (1) and SD (0) stream quality and immediately reconnect stream."""
        new_q = 1 if int(quality) else 0
        self.stream_quality = new_q
        self._quality_switch_pending = True
        self._need_reconnect = True
        self._close_stream_sock()

    def _emit_status(self, msg: str) -> None:
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_event.set()
        self._close_stream_sock()

    def _close_stream_sock(self) -> None:
        sock = self._stream_sock
        self._stream_sock = None
        if sock is not None:
            def _do_close(s: socket.socket = sock) -> None:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass

            if threading.current_thread() is threading.main_thread():
                threading.Thread(target=_do_close, daemon=True).start()
            else:
                _do_close()

    @classmethod
    def generate_password(cls, password: str, block_bytes: int = 48) -> bytes:
        """
        Encrypt user password using the camera's dual-stage AES-128-ECB scheme:
        Stage 1: AES-ECB encrypt with static protocol key
        Stage 2: AES-ECB encrypt with random 16-char printable key
        Output: 16-byte random key + encrypted password blocks
        """
        chars = string.ascii_letters + string.digits
        rk = "".join(secrets.choice(chars) for _ in range(16)).encode("ascii")

        pw_bytes = password.encode("utf-8")
        pad_buf = bytearray(block_bytes)
        copy_len = min(len(pw_bytes), block_bytes)
        pad_buf[:copy_len] = pw_bytes[:copy_len]

        cipher1 = AES.new(cls.STATIC_KEY, AES.MODE_ECB)
        enc1 = cipher1.encrypt(bytes(pad_buf))

        cipher2 = AES.new(rk, AES.MODE_ECB)
        enc2 = cipher2.encrypt(enc1)

        return rk + enc2

    def _generate_media_key(self, ticket: int) -> None:
        key = bytearray(16)
        struct.pack_into("<I", key, 0, ticket & 0xFFFFFFFF)
        struct.pack_into("<Q", key, 4, 0x618123462C14795C)
        struct.pack_into("<I", key, 12, 0x82800DF0)
        self.aes_key = bytes(key)

    def _resolve_device_id_if_missing(self) -> bool:
        if self.device_id > 0:
            return True
        self._emit_status("Discovering device ID...")
        single = LanCameraDiscovery.query_single_ip(self.ip, timeout_sec=0.9)
        if single and single.dev_id.isdigit():
            self.device_id = int(single.dev_id)
            self._emit_status(f"Found Device ID: {self.device_id}")
            return True
        devices = LanCameraDiscovery.discover(timeout_sec=1.5, scan_tcp_8800=False)
        for dev in devices:
            if dev.ip == self.ip and dev.dev_id.isdigit():
                self.device_id = int(dev.dev_id)
                self._emit_status(f"Found Device ID: {self.device_id}")
                return True
        return False

    def _wake_camera_lan_udp(self, burst_count: int = 3) -> None:
        """
        Send a multi-target local UDP discovery & wakeup burst to the camera IP on ports
        10008, 10009, and 5040 (plus subnet broadcast) to wake Wi-Fi from DTIM power-save
        sleep and bring port 8800 out of no-person standby.
        """
        targets = [
            (self.ip, 10008),
            (self.ip, 10009),
            (self.ip, 5040),
            ("255.255.255.255", 10008),
        ]
        try:
            net = ipaddress.IPv4Network(f"{self.ip}/24", strict=False)
            bcast = str(net.broadcast_address)
            if (bcast, 10008) not in targets:
                targets.append((bcast, 10008))
        except Exception:
            pass

        rounds = max(1, int(burst_count))
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as usock:
                LanCameraDiscovery._configure_udp_socket(usock)
                usock.bind(("0.0.0.0", 0))
                for idx in range(rounds):
                    for addr in targets:
                        try:
                            usock.sendto(b"NVDEVSEARCH^100", addr)
                        except Exception:
                            pass
                    if idx + 1 < rounds and not self._stop_event.is_set():
                        time.sleep(0.04)
        except Exception:
            pass

    def _wake_camera_port_8800(self, include_cloud: bool = True) -> None:
        """
        Wake compatible cameras (especially v31/v32 firmware or cross-subnet cameras) whose
        local TCP port 8800 listener or video encoder goes into sleep/standby when no person
        is detected, using:
        1. Multi-packet UDP `NVDEVSEARCH^100` wakeup burst to <ip>:10008/10009/5040 + broadcast
        2. Authenticated Cloud Dispatch (`dispa1.av380.net:8001`) `cmd 1167` wakeup signal
           so the cloud relay pushes the wakeup handshake to the sleeping camera.
        """
        self._wake_camera_lan_udp(burst_count=3)
        if not include_cloud or self.device_id <= 0:
            return

        relay_ips: List[str] = []
        for _ in range(2):
            if self._stop_event.is_set() or relay_ips:
                break
            try:
                ts = int(time.time())
                base = f"dev_id={self.device_id}&platform=10001&timestamp={ts}hsdata2022"
                sign = hashlib.sha1(base.encode("utf-8")).hexdigest().lower()
                body = json.dumps(
                    {
                        "dev_id": self.device_id,
                        "platform": 10001,
                        "timestamp": ts,
                        "sign": sign,
                    }
                ).encode("utf-8")
                req = urllib.request.Request(
                    "http://dispa1.av380.net:8001/api/v1/get_stream_server",
                    data=body,
                    headers={"Content-Type": "application/json"},
                )
                with urllib.request.urlopen(req, timeout=2.2) as resp:
                    data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                    if data.get("code") == 2000:
                        for item in data.get("data", []):
                            rip = str(item.get("ip") or "").strip()
                            if rip and rip not in relay_ips:
                                try:
                                    if ipaddress.IPv4Address(rip).is_global:
                                        relay_ips.append(rip)
                                except Exception:
                                    pass
            except Exception:
                pass

        if relay_ips:
            self._RELAY_IP_CACHE[self.device_id] = list(relay_ips)
        else:
            for cached_ip in self._RELAY_IP_CACHE.get(self.device_id, []):
                if cached_ip not in relay_ips:
                    relay_ips.append(cached_ip)
            for other_ips in self._RELAY_IP_CACHE.values():
                for cached_ip in other_ips:
                    if cached_ip not in relay_ips:
                        relay_ips.append(cached_ip)

        for fallback_ip in (
            "103.138.75.134",
            "103.138.75.212",
            "103.138.75.221",
            "43.154.60.102",
            "43.128.44.102",
            "43.154.16.42",
            "129.226.135.99",
            "129.226.139.226",
            "101.33.35.22",
        ):
            if fallback_ip not in relay_ips:
                relay_ips.append(fallback_ip)

        candidate_users: List[str] = []
        primary_user = self.username if self.username != "admin" else (str(self.device_id) or "admin")
        for u in (primary_user, str(self.device_id) if self.device_id else "", "admin"):
            if u and u not in candidate_users:
                candidate_users.append(u)

        host_b = f"{self.device_id}.nvdvr.net".encode("ascii", errors="ignore")[:50]
        enc_pw = self.generate_password(self.password, block_bytes=48)

        def _build_relay_cmd(u_str: str) -> bytearray:
            user_b = u_str.encode("ascii", errors="ignore")[:32]
            c = bytearray(520)
            struct.pack_into("<I", c, 0, 1167)
            struct.pack_into("<I", c, 4, 1022)
            c[8] = 31
            struct.pack_into("<I", c, 9, 1)
            struct.pack_into("<I", c, 13, self.device_id & 0xFFFFFFFF)
            c[17 : 17 + len(host_b)] = host_b
            struct.pack_into("<I", c, 67, 8800)
            c[71 : 71 + len(user_b)] = user_b
            c[103 : 103 + len(enc_pw)] = enc_pw
            return c

        cmd_primary = _build_relay_cmd(candidate_users[0])

        for rip in relay_ips[:4]:
            if self._stop_event.is_set():
                return
            rsock: Optional[socket.socket] = None
            try:
                rsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._stream_sock = rsock
                rsock.settimeout(1.8)
                rsock.connect((rip, 8800))
                rsock.sendall(cmd_primary)
                resp = self._recv_response(rsock, 256, timeout=1.5)
                if resp and len(resp) >= 8:
                    rcmd, rres = struct.unpack_from("<II", resp, 0)
                    if rcmd == 1168 and rres in (1002, 1011) and len(candidate_users) > 1:
                        rsock.close()
                        rsock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                        self._stream_sock = rsock
                        rsock.settimeout(1.8)
                        rsock.connect((rip, 8800))
                        rsock.sendall(_build_relay_cmd(candidate_users[1]))
                        self._recv_response(rsock, 256, timeout=1.5)
            except Exception:
                continue
            finally:
                if rsock is not None:
                    if self._stream_sock is rsock:
                        self._stream_sock = None
                    try:
                        rsock.close()
                    except Exception:
                        pass

        self._wake_camera_lan_udp(burst_count=2)
        self._stop_event.wait(0.4)

    def authenticate(self, skip_cloud_wake: bool = False) -> Tuple[int, str]:
        """
        Perform port 8800 authentication (`cmd 1167` -> `resp 1168`).
        Attempts fast LAN UDP + TCP 8800 first when `self._force_cloud_wake` is False;
        automatically triggers authenticated Cloud Dispatch wakeup and polls TCP port 8800
        readiness (up to ~5.5s) whenever the camera is in sleep/standby mode.
        """
        self._close_stream_sock()
        if self._stop_event.is_set():
            return -1, "Stopped"
        force_cloud = self._force_cloud_wake
        if force_cloud:
            skip_cloud_wake = False
            self._force_cloud_wake = False
        self._resolve_device_id_if_missing()
        if self._stop_event.is_set():
            return -1, "Stopped"
        self._wake_camera_lan_udp(burst_count=2)

        ports_to_try = [self.port] if self.port == 8800 else [self.port, 8800]
        usernames_to_try: List[str] = []
        for u in (self.username, str(self.device_id) if self.device_id else "", "admin"):
            if u and u not in usernames_to_try:
                usernames_to_try.append(u)

        variants = [
            (520, 120, 31, 48),  # Modern LAN auth (520 bytes, unk1=120, ver=31)
            (256, 1022, 2, 16),  # Classic LAN auth (256 bytes, unk1=1022, ver=2)
        ]

        last_err = "Connection timed out"
        cloud_woken = False
        if force_cloud and not skip_cloud_wake:
            self._emit_status("Waking sleeping camera...")
            self._wake_camera_port_8800(include_cloud=True)
            cloud_woken = True

        max_passes = 1 if (skip_cloud_wake or cloud_woken) else 2
        for pass_idx in range(max_passes):
            # When the cloud relay wakeup was just sent, the sleeping camera on Wi-Fi
            # typically takes 1.5s - 5.5s to wake its radio, resolve ARP, and open TCP port 8800.
            # Poll connect up to 8 times so we immediately authenticate the moment port 8800 opens.
            connect_attempts = 8 if cloud_woken else 1
            last_attempt_fast_fail = False
            for conn_try in range(connect_attempts):
                if self._stop_event.is_set():
                    return -1, "Stopped"
                if conn_try > 0:
                    self._wake_camera_lan_udp(burst_count=2)
                    wait_sec = 0.85 if last_attempt_fast_fail else 0.35
                    self._stop_event.wait(wait_sec)
                    if self._stop_event.is_set():
                        return -1, "Stopped"

                t_conn_start = time.monotonic()
                any_port_reachable = False
                for candidate_port in ports_to_try:
                    port_reachable = False
                    for candidate_user in usernames_to_try:
                        for pkt_size, unk1, unk2_ver, pw_block_len in variants:
                            if self._stop_event.is_set():
                                return -1, "Stopped"

                            sock: Optional[socket.socket] = None
                            try:
                                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                                self._stream_sock = sock
                                if not cloud_woken and max_passes > 1 and pass_idx == 0:
                                    sock.settimeout(1.2)
                                elif cloud_woken and conn_try + 1 < connect_attempts:
                                    sock.settimeout(1.5)
                                else:
                                    sock.settimeout(3.0)
                                sock.connect((self.ip, candidate_port))
                                if self._stop_event.is_set():
                                    return -1, "Stopped"
                                port_reachable = True
                                any_port_reachable = True

                                enc_pw = self.generate_password(self.password, block_bytes=pw_block_len)
                                user_bytes = candidate_user.encode("ascii", errors="ignore")[:32]

                                cmd1167 = bytearray(pkt_size)
                                struct.pack_into("<I", cmd1167, 0, 1167)
                                struct.pack_into("<I", cmd1167, 4, unk1)
                                cmd1167[8] = unk2_ver
                                struct.pack_into("<I", cmd1167, 9, 1)
                                struct.pack_into("<I", cmd1167, 13, self.device_id & 0xFFFFFFFF)
                                cmd1167[49 : 49 + len(user_bytes)] = user_bytes
                                cmd1167[81 : 81 + len(enc_pw)] = enc_pw

                                sock.sendall(cmd1167)
                                resp = self._recv_response(sock, 256, timeout=4.0)
                                if not resp or len(resp) < 32:
                                    last_err = "Camera in sleep/standby (empty auth response)"
                                    continue

                                resp_cmd = struct.unpack_from("<I", resp, 0)[0]
                                if resp_cmd != 1168:
                                    last_err = f"Unexpected auth response command: {resp_cmd}"
                                    continue

                                login_result = struct.unpack_from("<I", resp, 4)[0]
                                if login_result == 1001:
                                    self.port = candidate_port
                                    self.username = candidate_user
                                    self.device_version = resp[12]
                                    self.auth_ticket = struct.unpack_from("<I", resp, 13)[0]
                                    self.session_id = struct.unpack_from("<I", resp, 17)[0]
                                    return 1, f"Authenticated (v{self.device_version}, port {self.port})"

                                if login_result == 1011:
                                    last_err = "Invalid camera username (error 1011)"
                                    break
                                if login_result == 1012:
                                    return -1, "Invalid camera password (error 1012)"
                                if login_result == 1018:
                                    return (
                                        -1,
                                        f"Invalid Device ID ({self.device_id}) — check camera sticker (error 1018)",
                                    )
                                last_err = f"Camera standby/busy (code {login_result})"
                                break

                            except (socket.timeout, ConnectionRefusedError, OSError) as exc:
                                if self._stop_event.is_set():
                                    return -1, "Stopped"
                                last_err = f"Cannot connect to {self.ip}:{candidate_port} ({exc})"
                                break
                            finally:
                                if sock is not None:
                                    if self._stream_sock is sock:
                                        self._stream_sock = None
                                    try:
                                        sock.close()
                                    except Exception:
                                        pass

                        if not port_reachable:
                            break

                last_attempt_fast_fail = (time.monotonic() - t_conn_start) < 0.6
                if any_port_reachable and "1011" in last_err:
                    break

            if pass_idx + 1 >= max_passes:
                break
            # Camera did not authenticate on Pass 0 (either port 8800 closed OR sleeping daemon returned empty/busy);
            # send full LAN + Cloud wakeup signal and retry!
            self._emit_status("Waking sleeping camera...")
            self._wake_camera_port_8800(include_cloud=True)
            cloud_woken = True

        if "1011" in last_err:
            return -1, last_err
        return 0, last_err

    def stream_login_and_start(self) -> Tuple[bool, str]:
        """Connect stream socket, send `cmd 301`, parse `resp 401`, and send `cmd 303`."""
        last_err = "Stream handshake failed"
        for attempt in range(2):
            self._close_stream_sock()
            if self._stop_event.is_set():
                return False, "Stopped"
            if attempt > 0:
                self._wake_camera_lan_udp(burst_count=1)
                self._stop_event.wait(0.25)
                if self._stop_event.is_set():
                    return False, "Stopped"
            try:
                sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1048576)
                self._configure_tcp_keepalive(sock)
                sock.settimeout(6.0)
                sock.connect((self.ip, self.port))
                self._stream_sock = sock

                cmd301 = bytearray(256)
                struct.pack_into("<I", cmd301, 0, 301)
                struct.pack_into("<I", cmd301, 4, self.device_id & 0xFFFFFFFF)
                struct.pack_into("<I", cmd301, 8, 0)
                struct.pack_into("<H", cmd301, 12, 20)  # Target FPS
                struct.pack_into("<I", cmd301, 14, self.auth_ticket & 0xFFFFFFFF)
                struct.pack_into("<I", cmd301, 22, 4097 if self.enable_audio else 4096)
                struct.pack_into("<I", cmd301, 26, self.stream_quality)

                if not self._send_raw(bytes(cmd301)):
                    last_err = "Failed sending stream login (cmd 301)"
                    continue

                resp401 = self._recv_response(sock, 412, timeout=10.0)
                if not resp401 or len(resp401) < 16:
                    last_err = "Stream login response too short"
                    continue

                resp_cmd = struct.unpack_from("<I", resp401, 0)[0]
                if resp_cmd != 401:
                    last_err = f"Expected stream login 401, got {resp_cmd}"
                    continue

                result = struct.unpack_from("<i", resp401, 4)[0]
                if result in (-11, -12):
                    return False, f"Camera stream rejected (code {result})"

                version = struct.unpack_from("<H", resp401, 8)[0]
                width = struct.unpack_from("<I", resp401, 10)[0]
                height = struct.unpack_from("<I", resp401, 14)[0]

                self.communication_version = version
                if 160 <= width <= 4096:
                    self.frame_width = int(width)
                if 120 <= height <= 4096:
                    self.frame_height = int(height)

                if self.device_version > 30:
                    self._generate_media_key(self.auth_ticket)

                # Start stream (cmd 303)
                cmd303 = self._build_stream_start_cmd303(result)
                if not self._send_raw(cmd303):
                    last_err = "Failed sending stream start (cmd 303)"
                    continue

                return True, f"Streaming ({self.frame_width}x{self.frame_height}, {'HD' if self.stream_quality else 'SD'})"

            except Exception as exc:
                self._close_stream_sock()
                last_err = f"Stream handshake failed: {exc}"
        return False, last_err

    def run_forever(self) -> None:
        """Main loop: authenticate, start stream, receive & demux media packets until stopped."""
        self._stop_event.clear()
        fast_reconnect = False
        while not self._stop_event.is_set():
            if fast_reconnect:
                self._emit_status(f"Switching to {'HD' if self.stream_quality else 'SD'} stream...")
            else:
                self._emit_status("Connecting to camera stream...")
            auth_status, auth_msg = self.authenticate(skip_cloud_wake=fast_reconnect)
            if auth_status == 0 and fast_reconnect and not self._stop_event.is_set():
                auth_status, auth_msg = self.authenticate(skip_cloud_wake=False)
            fast_reconnect = False

            if self._stop_event.is_set():
                break

            if auth_status == -1:
                if self._stop_event.is_set():
                    break
                self._emit_status(f"Auth Error: {auth_msg} — retrying in 5s...")
                self._stop_event.wait(5.0)
                continue

            if auth_status == 0:
                self._force_cloud_wake = True
                self._emit_status(f"{auth_msg} — waking & retrying in 1.5s...")
                self._stop_event.wait(1.5)
                continue

            self._emit_status(auth_msg)
            ok, stream_msg = self.stream_login_and_start()
            if not ok:
                self._force_cloud_wake = True
                self._emit_status(f"{stream_msg} — waking camera & retrying...")
                self._stop_event.wait(0.8)
                continue

            self._emit_status(stream_msg)
            self._receive_frames_loop()
            self._close_stream_sock()

            if self._quality_switch_pending and not self._stop_event.is_set():
                self._quality_switch_pending = False
                fast_reconnect = True
                continue

            if not self._stop_event.is_set():
                # If the stream exited without delivering video frames, the camera is in sleep mode:
                # trigger full LAN + Cloud wakeup on the next authenticate() pass.
                if self._session_video_frames == 0 or self._force_cloud_wake:
                    self._force_cloud_wake = True
                    self._emit_status("Camera sleeping — sending wakeup signal...")
                    self._stop_event.wait(0.5)
                else:
                    self._emit_status("Stream disconnected — waking & reconnecting...")
                    self._wake_camera_lan_udp(burst_count=3)
                    self._stop_event.wait(0.8)

        self._close_stream_sock()

    def _tick_stream_keepalive(self, force_tcp: bool = False) -> bool:
        """
        Send periodic upstream keepalives during live streaming so V380 cameras never enter
        no-person / idle sleep mode, and prod the encoder if video frames pause.
        Returns False if the video watchdog timed out and requires a full reconnect.
        """
        now = time.monotonic()
        no_video_sec = now - self._last_video_frame_mono
        if force_tcp or (no_video_sec >= 4.0 and (now - self._last_keepalive_mono) >= 8.0):
            self._last_keepalive_mono = now
            self._send_raw(LanCameraCommands.PTZ_STOP)

        if no_video_sec >= 4.0 and (now - self._last_udp_wake_mono) >= 12.0:
            self._last_udp_wake_mono = now
            self._wake_camera_lan_udp(burst_count=1)

        if no_video_sec >= 8.5 and (now - self._last_encoder_prod_mono) >= 4.0:
            self._last_encoder_prod_mono = now
            self.send_wake_pulse()

        if no_video_sec >= 24.0:
            self._force_cloud_wake = True
            self._need_reconnect = True
            return False
        return True

    def _receive_frames_loop(self) -> None:
        sock = self._stream_sock
        if sock is None:
            return

        need_decrypt = self.device_version > 30
        frame_frags = bytearray()
        frame_total = 0
        next_fragment = 0
        frame_start_type = 0
        assembling = False
        self._need_reconnect = False

        now = time.monotonic()
        self._last_video_frame_mono = now
        self._last_keepalive_mono = now
        self._last_udp_wake_mono = now
        self._last_encoder_prod_mono = now
        self._session_video_frames = 0

        while not self._stop_event.is_set() and not self._need_reconnect:
            if not self._tick_stream_keepalive():
                break

            hdr = self._read_exact(sock, 12, timeout=12.0, allow_idle_prod=True)
            if len(hdr) < 12:
                break

            lead = hdr[0]
            if lead == 0x6F:
                # Camera sent a 0x6F server keepalive probe: reply immediately so it stays awake
                self._tick_stream_keepalive(force_tcp=True)
                continue
            if lead != 0x7F:
                # Resynchronize if a 0x7F frame header starts inside `hdr`
                idx = hdr.find(b"\x7f", 1)
                if idx > 0:
                    tail = self._read_exact(sock, idx, timeout=6.0)
                    if len(tail) < idx:
                        break
                    hdr = hdr[idx:] + tail
                else:
                    continue

            pkt_type = hdr[1]
            total_frame, cur_frame, pay_len = struct.unpack_from("<HHH", hdr, 3)

            if pay_len == 0 or pay_len > 20000 or total_frame == 0 or cur_frame >= total_frame:
                continue

            payload = self._read_exact(sock, pay_len, timeout=12.0)
            if len(payload) < pay_len:
                break

            if pkt_type == 0x5B:
                self._tick_stream_keepalive(force_tcp=True)
                continue

            if cur_frame == 0 or not assembling or total_frame != frame_total or cur_frame != next_fragment:
                frame_frags.clear()
                frame_total = total_frame
                next_fragment = 0
                frame_start_type = pkt_type
                assembling = True

            frame_frags.extend(payload)
            next_fragment = cur_frame + 1

            if cur_frame != total_frame - 1:
                continue

            if len(frame_frags) < 16:
                frame_frags.clear()
                assembling = False
                continue

            full_frame = bytes(frame_frags)
            frame_frags.clear()
            assembling = False

            self._handle_media_frame(frame_start_type, full_frame, need_decrypt)

    def _handle_media_frame(self, raw_type: int, full: bytes, need_decrypt: bool) -> None:
        if self.on_frame is None:
            return

        outer_frame_id = struct.unpack_from("<I", full, 0)[0] if len(full) >= 4 else 0
        outer_frame_type = struct.unpack_from("<H", full, 4)[0] if len(full) >= 6 else 0
        outer_frame_rate = struct.unpack_from("<H", full, 6)[0] if len(full) >= 8 else 20
        outer_timestamp = struct.unpack_from("<Q", full, 8)[0] if len(full) >= 16 else 0

        # Audio packet types:
        # 0x18 = AAC-LC ADTS (v31/v32 firmware, 16-byte header)
        # 0x1A = Audio with 16-byte header
        # 0x16 = IMA-ADPCM with 20-byte header
        if raw_type in (0x18, 0x1A, 0x16):
            if not self.enable_audio:
                return
            header_skip = 20 if raw_type == 0x16 else 16
            if len(full) <= header_skip:
                return
            audio_payload = bytearray(full[header_skip:])
            if need_decrypt and len(audio_payload) >= 16:
                if self.communication_version == 21:
                    self._decrypt_media_pre2k(audio_payload, mode=1)
                else:
                    self._decrypt_audio_frame(audio_payload)

            # Determine exact audio codec:
            # 0x18 (or ADTS syncword 0xFFFx) = AAC-LC
            # 0x1A = G.711 A-Law (PCMA 8000 Hz)
            # 0x16 = IMA-ADPCM (8000 Hz)
            if raw_type == 0x18 or (
                len(audio_payload) >= 4
                and audio_payload[0] == 0xFF
                and (audio_payload[1] & 0xF0) == 0xF0
            ):
                acodec = "aac"
            elif raw_type == 0x1A:
                acodec = "alaw"
            else:
                acodec = "adpcm"

            self.on_frame(
                CameraFrame(
                    is_audio=True,
                    raw_type=raw_type,
                    payload=bytes(audio_payload),
                    frame_id=outer_frame_id,
                    frame_type=outer_frame_type,
                    frame_rate=outer_frame_rate,
                    timestamp=outer_timestamp,
                    audio_codec=acodec,
                )
            )
            return

        # Fast-path for standard 16-byte header encrypted/unencrypted video frames:
        # 0x28 = HEVC Keyframe, 0x29 = HEVC P-frame, 0x00 = H.264 Keyframe, 0x01 = H.264 P-frame
        normalized = self._try_extract_video_payload(full, need_decrypt)
        if not normalized:
            return

        self._last_video_frame_mono = time.monotonic()
        self._session_video_frames += 1

        codec = self._detect_video_codec(raw_type, normalized)
        is_key = self._is_key_video_frame(raw_type, outer_frame_type, normalized)

        self.on_frame(
            CameraFrame(
                is_audio=False,
                raw_type=raw_type,
                payload=normalized,
                codec=codec,
                is_keyframe=is_key,
                frame_id=outer_frame_id,
                frame_type=outer_frame_type,
                frame_rate=outer_frame_rate or 20,
                timestamp=outer_timestamp,
            )
        )

    def _try_extract_video_payload(self, full: bytes, need_decrypt: bool) -> Optional[bytes]:
        # Fast-path for standard 16-byte outer media frame header:
        # AES 80-byte stride decryption aligns with offset 16, and decrypted v31/v32 frames
        # start directly with Annex-B 00 00 00 01 or 00 00 01 at offset 16.
        if len(full) > 20:
            d16 = full[16:]
            if need_decrypt and len(d16) >= 16:
                dec16 = bytearray(d16)
                if self.communication_version == 21:
                    self._decrypt_media_pre2k(dec16, mode=1)
                else:
                    self._decrypt_video_frame(dec16)
                if dec16[:4] == b"\x00\x00\x00\x01" and self._is_likely_nal_header(dec16, 4):
                    return bytes(dec16)
                if dec16[:3] == b"\x00\x00\x01" and self._is_likely_nal_header(dec16, 3):
                    return b"\x00" + bytes(dec16)
            elif d16[:4] == b"\x00\x00\x00\x01" and self._is_likely_nal_header(d16, 4):
                return d16
            elif d16[:3] == b"\x00\x00\x01" and self._is_likely_nal_header(d16, 3):
                return b"\x00" + d16

        best_score = -10**9
        best_payload: Optional[bytes] = None

        # Always test offset 16 first! Outer media frame headers are 16 bytes,
        # and AES 80-byte stride decryption MUST align with offset 16.
        for offset in (16, 20, 12, 8, 4, 0):
            if len(full) <= offset:
                continue
            direct = full[offset:]
            candidates = []

            if need_decrypt and len(direct) >= 16:
                dec = bytearray(direct)
                if self.communication_version == 21:
                    self._decrypt_media_pre2k(dec, mode=1)
                else:
                    self._decrypt_video_frame(dec)
                candidates.append(bytes(dec))

            candidates.append(direct)

            for cand in candidates:
                # If candidate at offset 16 already starts with 00 00 00 01 or 00 00 01,
                # return it immediately if it scores positively!
                norm = self._normalize_video_payload(cand)
                if norm is None:
                    continue
                score = self._score_video_payload(norm)
                if offset == 16 and cand[:4] == b"\x00\x00\x00\x01" and score > 0:
                    return norm
                if score > best_score:
                    best_score = score
                    best_payload = norm

        return best_payload

    def _normalize_video_payload(self, payload: bytes) -> Optional[bytes]:
        start = self._find_video_start_code(payload)
        if start < 0:
            return None
        if payload[start : start + 3] == b"\x00\x00\x01":
            return b"\x00" + payload[start:]
        return payload[start:]

    def _find_video_start_code(self, payload: bytes) -> int:
        limit = min(len(payload) - 4, 128)
        for i in range(max(0, limit + 1)):
            if payload[i : i + 4] == b"\x00\x00\x00\x01":
                if self._is_likely_nal_header(payload, i + 4):
                    return i
            elif payload[i : i + 3] == b"\x00\x00\x01":
                if self._is_likely_nal_header(payload, i + 3):
                    return i
        return -1

    def _score_video_payload(self, payload: bytes) -> int:
        score = 0
        nal_count = 0
        param_count = 0
        idr_count = 0
        i = 0
        n = len(payload)

        while i < n - 4 and nal_count < 12:
            sc = self._find_next_start_code(payload, i)
            if sc < 0:
                break
            sc_len = 3 if payload[sc + 2] == 1 else 4
            nal_start = sc + sc_len
            if not self._is_likely_nal_header(payload, nal_start):
                i = sc + 1
                score -= 20
                continue

            nal_count += 1
            nal_hdr = payload[nal_start]
            h264_type = nal_hdr & 0x1F
            h265_type = (nal_hdr >> 1) & 0x3F

            if h264_type in (7, 8) or h265_type in (32, 33, 34):
                param_count += 1
                score += 50
            if h264_type == 5 or h265_type in (19, 20):
                idr_count += 1
                score += 30

            score += 10
            i = nal_start + 1

        if nal_count == 0:
            return -10**9

        return score + nal_count * 5 + param_count * 40 + idr_count * 20

    @staticmethod
    def _find_next_start_code(payload: bytes, start_from: int) -> int:
        idx3 = payload.find(b"\x00\x00\x01", start_from)
        if idx3 < 0:
            return -1
        if idx3 > start_from and payload[idx3 - 1] == 0:
            return idx3 - 1
        return idx3

    @staticmethod
    def _is_likely_nal_header(payload: bytes, index: int) -> bool:
        if index >= len(payload):
            return False
        nal_hdr = payload[index]
        h264_type = nal_hdr & 0x1F
        if 0 < h264_type < 24:
            return True
        h265_type = (nal_hdr >> 1) & 0x3F
        return 0 < h265_type < 48

    @staticmethod
    def _detect_video_codec(raw_type: int, payload: bytes) -> VideoCodec:
        # Packet type tags explicitly distinguish HEVC (0x28/0x29) vs H.264 (0x00/0x01)
        if raw_type in (0x28, 0x29):
            return VideoCodec.H265
        if raw_type in (0x00, 0x01):
            return VideoCodec.H264

        if len(payload) < 6:
            return VideoCodec.UNKNOWN
        offset = 3 if payload[2] == 1 else 4
        if len(payload) <= offset + 1:
            return VideoCodec.UNKNOWN
        nal_hdr = payload[offset]
        nal_b1 = payload[offset + 1]
        h265_type = (nal_hdr >> 1) & 0x3F
        if (nal_hdr & 0x80) == 0 and nal_b1 == 0x01 and h265_type in (1, 19, 20, 32, 33, 34):
            return VideoCodec.H265

        h264_type = nal_hdr & 0x1F
        if 0 < h264_type < 24:
            return VideoCodec.H264
        if 0 < h265_type < 48:
            return VideoCodec.H265
        return VideoCodec.UNKNOWN

    @staticmethod
    def _is_key_video_frame(raw_type: int, frame_type: int, payload: bytes) -> bool:
        if raw_type in (0x00, 0x28):
            return True
        nal_offset = 4 if (len(payload) >= 4 and payload[2] == 0 and payload[3] == 1) else 3
        if len(payload) <= nal_offset:
            return False
        nal_hdr = payload[nal_offset]
        h264_type = nal_hdr & 0x1F
        if h264_type in (5, 7, 8):
            return True
        h265_type = (nal_hdr >> 1) & 0x3F
        if h265_type in (19, 20, 32, 33, 34):
            return True
        return frame_type == 0

    def _decrypt_video_frame(self, data: bytearray) -> None:
        cipher = AES.new(self.aes_key, AES.MODE_ECB)
        length = len(data)
        offset = 0
        while offset + 64 <= length:
            data[offset : offset + 64] = cipher.decrypt(bytes(data[offset : offset + 64]))
            offset += 80

    def _decrypt_audio_frame(self, data: bytearray) -> None:
        aligned = (len(data) // 16) * 16
        if aligned <= 0:
            return
        cipher = AES.new(self.aes_key, AES.MODE_ECB)
        data[:aligned] = cipher.decrypt(bytes(data[:aligned]))

    def _decrypt_media_pre2k(self, data: bytearray, mode: int = 1) -> None:
        decrypt_len = 2048 if (mode == 0 and len(data) > 2048) else (len(data) // 16) * 16
        if decrypt_len <= 0:
            return
        cipher = AES.new(self.aes_key, AES.MODE_ECB)
        data[:decrypt_len] = cipher.decrypt(bytes(data[:decrypt_len]))

    def _send_raw(self, payload: bytes) -> bool:
        sock = self._stream_sock
        if sock is None:
            return False
        with self._send_lock:
            try:
                sock.sendall(payload)
                return True
            except Exception:
                return False

    @staticmethod
    def _recv_response(sock: socket.socket, max_len: int, timeout: float = 5.0) -> bytes:
        sock.settimeout(timeout)
        chunks = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline and len(chunks) < max_len:
            try:
                data = sock.recv(max_len - len(chunks))
                if not data:
                    break
                chunks.extend(data)
                if len(chunks) >= 16:
                    break
            except socket.timeout:
                break
            except Exception:
                break
        return bytes(chunks)

    def _read_exact(
        self,
        sock: socket.socket,
        count: int,
        timeout: float = 5.0,
        allow_idle_prod: bool = False,
    ) -> bytes:
        buf = bytearray()
        deadline = time.monotonic() + timeout
        prodded = False
        while len(buf) < count:
            now = time.monotonic()
            rem = deadline - now
            if self._stop_event.is_set() or rem <= 0:
                self._need_reconnect = True
                break
            step_timeout = min(rem, 4.0) if (allow_idle_prod and len(buf) == 0) else rem
            try:
                sock.settimeout(max(0.1, step_timeout))
                chunk = sock.recv(count - len(buf))
                if not chunk:
                    self._need_reconnect = True
                    break
                buf.extend(chunk)
            except socket.timeout:
                if allow_idle_prod and len(buf) == 0 and time.monotonic() < deadline:
                    # Camera paused sending media packets on the wire (e.g. entering no-person standby):
                    # immediately prod the stream & UDP wakeup port before the socket deadline expires!
                    if not prodded:
                        prodded = True
                        self.send_wake_pulse()
                    else:
                        self._tick_stream_keepalive(force_tcp=True)
                    continue
                self._need_reconnect = True
                break
            except Exception:
                self._need_reconnect = True
                break
        return bytes(buf)

    # --- Camera Control Commands ---
    def ptz_up(self) -> bool:
        return self._send_raw(LanCameraCommands.PTZ_UP)

    def ptz_down(self) -> bool:
        return self._send_raw(LanCameraCommands.PTZ_DOWN)

    def ptz_left(self) -> bool:
        return self._send_raw(LanCameraCommands.PTZ_LEFT)

    def ptz_right(self) -> bool:
        return self._send_raw(LanCameraCommands.PTZ_RIGHT)

    def ptz_stop(self) -> bool:
        return self._send_raw(LanCameraCommands.PTZ_STOP)

    def light_on(self) -> bool:
        return self._send_raw(LanCameraCommands.LIGHT_ON)

    def light_off(self) -> bool:
        return self._send_raw(LanCameraCommands.LIGHT_OFF)

    def light_auto(self) -> bool:
        ok1 = self._send_raw(LanCameraCommands.LIGHT_AUTO)
        time.sleep(0.03)
        ok2 = self._send_raw(LanCameraCommands.IMAGE_AUTO)
        return ok1 or ok2

    def image_color(self) -> bool:
        return self._send_raw(LanCameraCommands.IMAGE_COLOR)

    def image_bw(self) -> bool:
        return self._send_raw(LanCameraCommands.IMAGE_BW)

    def image_auto(self) -> bool:
        ok1 = self._send_raw(LanCameraCommands.IMAGE_AUTO)
        time.sleep(0.03)
        ok2 = self._send_raw(LanCameraCommands.LIGHT_AUTO)
        return ok1 or ok2

    def image_flip(self) -> bool:
        return self._send_raw(LanCameraCommands.IMAGE_FLIP)

    # --- Hardware PTZ Preset (PTZX) Commands ---
    def _send_ptzx_port8800(self, action: int, slot_id: int) -> bool:
        """Send cmd 129 (NV_IP_PTZX_REQUEST) on TCP port 8800 for persistent hardware preset storage/recall."""
        try:
            ticket = self.auth_ticket
            if not ticket:
                st, _ = self.authenticate(skip_cloud_wake=True)
                if st != 1:
                    return False
                ticket = self.auth_ticket

            buf = bytearray(256)
            struct.pack_into("<I", buf, 0, 129)
            struct.pack_into("<I", buf, 4, self.device_id & 0xFFFFFFFF)
            buf[8] = 0
            struct.pack_into("<I", buf, 9, ticket & 0xFFFFFFFF)
            struct.pack_into("<I", buf, 13, int(action))
            struct.pack_into("<I", buf, 17, int(slot_id))

            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                s.settimeout(3.0)
                s.connect((self.ip, self.port))
                s.sendall(buf)
                resp = self._recv_response(s, 256, timeout=3.0)
                if resp and len(resp) >= 12:
                    r_cmd, r_res = struct.unpack_from("<II", resp, 0)
                    if r_cmd in (229, 429) and r_res == 1001:
                        return True
        except Exception:
            pass
        return False

    def _save_ptzx_port8800(self, slot_id: int) -> bool:
        """
        Save/overwrite a hardware preset slot on TCP port 8800 using both:
        - 100 (NV_PRESET_ACTION_ADD) to initialize an empty slot
        - 102 (NV_PRESET_ACTION_RESET, used by NVMediaPlayer.SetPTZXLocationID) to overwrite existing coordinates
        """
        ok_add = self._send_ptzx_port8800(LanCameraCommands.PTZX_ACTION_ADD, slot_id)
        time.sleep(0.12)
        ok_reset = self._send_ptzx_port8800(LanCameraCommands.PTZX_ACTION_RESET, slot_id)
        return ok_add or ok_reset

    def ptz_preset_set(self, slot_id: int) -> bool:
        """Save current camera PTZ position into hardware preset slot (0..5)."""
        slot = max(0, min(5, int(slot_id)))
        ok_add = self._send_raw(LanCameraCommands.ptzx_packet(LanCameraCommands.PTZX_ACTION_ADD, slot))
        time.sleep(0.04)
        ok_reset = self._send_raw(LanCameraCommands.ptzx_packet(LanCameraCommands.PTZX_ACTION_RESET, slot))
        threading.Thread(
            target=self._save_ptzx_port8800,
            args=(slot,),
            daemon=True,
        ).start()
        return ok_add or ok_reset

    def ptz_preset_goto(self, slot_id: int) -> bool:
        """Recall and auto-pan camera to hardware preset slot (0..5) via SetPTZXParam (0xAB, 103, slot) + Port 8800 cmd 129."""
        slot = max(0, min(5, int(slot_id)))
        ok_stream = self._send_raw(LanCameraCommands.ptzx_packet(LanCameraCommands.PTZX_ACTION_GOTO, slot))
        threading.Thread(
            target=self._send_ptzx_port8800,
            args=(LanCameraCommands.PTZX_ACTION_GOTO, slot),
            daemon=True,
        ).start()
        return ok_stream

    def ptz_preset_clear(self, slot_id: int) -> bool:
        """Delete hardware preset slot (0..5)."""
        slot = max(0, min(5, int(slot_id)))
        ok_stream = self._send_raw(LanCameraCommands.ptzx_packet(LanCameraCommands.PTZX_ACTION_DEL, slot))
        threading.Thread(
            target=self._send_ptzx_port8800,
            args=(LanCameraCommands.PTZX_ACTION_DEL, slot),
            daemon=True,
        ).start()
        return ok_stream

    # --- On-Camera microSD Card Segment Search & Playback (`361`/`362`/`363`/`364`) ---
    @staticmethod
    def _put_wall_clock(buf: bytearray, offset: int, ts_sec: int) -> None:
        dt = datetime.fromtimestamp(max(0, int(ts_sec)), timezone.utc)
        struct.pack_into(
            "<HBBBBB",
            buf,
            offset,
            dt.year,
            dt.month,
            dt.day,
            dt.hour,
            dt.minute,
            dt.second,
        )

    def list_sd_recordings(
        self, year: int, month: int, day: int
    ) -> Tuple[bool, str, List[RecordingSegment]]:
        """
        Query the camera's microSD card for all recording segments on `(year, month, day)`
        using the Segment Search protocol (`cmd 361` -> `resp 461`, `cmd 362` -> `resp 462`).
        """
        msg = "Connection failed"
        for attempt in range(2):
            if self._stop_event.is_set():
                return False, "Cancelled", []
            if attempt > 0:
                self._force_cloud_wake = True
                self._stop_event.wait(0.25)
            res, msg = self.authenticate(skip_cloud_wake=False)
            if res == 1:
                break
        else:
            return False, f"SD card auth failed: {msg}", []

        last_err = "SD card read failed"
        for read_try in range(2):
            if self._stop_event.is_set():
                return False, "Cancelled", []
            if read_try > 0:
                res, msg = self.authenticate(skip_cloud_wake=True)
                if res != 1:
                    break
            segments: List[RecordingSegment] = []
            s: Optional[socket.socket] = None
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                self._stream_sock = s
                s.settimeout(4.0)
                s.connect((self.ip, self.port))
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                s.settimeout(5.0)

                cmd361 = bytearray(256)
                struct.pack_into("<I", cmd361, 0, 361)
                struct.pack_into("<I", cmd361, 4, self.device_id & 0xFFFFFFFF)
                struct.pack_into("<I", cmd361, 8, self.auth_ticket & 0xFFFFFFFF)
                cmd361[12] = 0  # channel
                cmd361[13] = 0  # all types (normal + motion + alarm)
                for base, h, m, sec in ((14, 0, 0, 0), (34, 23, 59, 59)):
                    struct.pack_into("<HHHHHH", cmd361, base, int(year), int(month), int(day), h, m, sec)
                s.sendall(cmd361)

                head = self._read_exact(s, 32, timeout=5.0)
                while len(head) == 32 and struct.unpack_from("<I", head, 0)[0] == 2000:
                    head = self._read_exact(s, 32, timeout=5.0)
                if len(head) < 10:
                    last_err = "Short response from camera SD card search"
                    continue

                rcmd, rres, _count = struct.unpack_from("<iiH", head, 0)
                if rcmd != 461 and rres != 1:
                    last_err = f"Camera refused SD card search ({rcmd}/{rres})"
                    continue
                if rres < 0:
                    return True, "No recordings found for this date", []

                ask362 = bytearray(32)
                struct.pack_into("<I", ask362, 0, 362)
                s.sendall(ask362)

                while not self._stop_event.is_set():
                    blk = self._read_exact(s, 512, timeout=5.0)
                    if len(blk) < 8:
                        break
                    bcmd = struct.unpack_from("<I", blk, 0)[0]
                    if bcmd == 460:
                        break
                    if bcmd == 2000:
                        continue
                    if bcmd != 462:
                        continue
                    flag, n = struct.unpack_from("<HH", blk, 4)
                    rec_count = min(int(n), (512 - 8) // 13)
                    for i in range(rec_count):
                        o = 8 + 13 * i
                        sid = struct.unpack_from("<I", blk, o)[0]
                        rtype = blk[o + 4]
                        st, et = struct.unpack_from("<II", blk, o + 5)
                        if sid > 0 and et >= st:
                            segments.append(
                                RecordingSegment(
                                    seg_id=int(sid),
                                    raw_type=int(rtype),
                                    start_ts=int(st),
                                    end_ts=int(et),
                                )
                            )
                    if flag == 12:
                        break

                segments.sort(key=lambda seg: seg.start_ts)
                anomaly_cnt = sum(1 for seg in segments if seg.is_anomaly)
                summary = f"{len(segments)} clips ({anomaly_cnt} anomalies)" if anomaly_cnt else f"{len(segments)} clips"
                return True, summary, segments
            except Exception as exc:
                if self._stop_event.is_set():
                    return False, "Cancelled", []
                last_err = f"SD card read error: {exc}"
            finally:
                if s is not None:
                    if self._stream_sock is s:
                        self._stream_sock = None
                    try:
                        s.close()
                    except Exception:
                        pass
        return False, last_err, []

    def stream_sd_segment(
        self,
        segment: RecordingSegment,
        start_ts: Optional[int] = None,
        stop_event: Optional[threading.Event] = None,
        on_frame: Optional[Callable[[CameraFrame], None]] = None,
    ) -> Tuple[bool, str]:
        """
        Stream a recorded SD card segment starting at `start_ts` (`cmd 363` -> `resp 463`, `cmd 364`).
        Decrypts video (80-byte AES-128-ECB stride) and audio frames identically to live streams.
        """
        cb = on_frame or self.on_frame
        if cb is None:
            return False, "No frame callback registered"

        def is_stopped() -> bool:
            return self._stop_event.is_set() or (stop_event is not None and stop_event.is_set())

        msg = "Connection failed"
        for attempt in range(2):
            if is_stopped():
                return False, "Cancelled"
            if attempt > 0:
                self._force_cloud_wake = True
                self._stop_event.wait(0.25)
            res, msg = self.authenticate(skip_cloud_wake=False)
            if res == 1:
                break
        else:
            return False, f"Playback auth failed: {msg}"

        if self.device_version > 30:
            self._generate_media_key(self.auth_ticket)

        from_ts = int(start_ts if start_ts is not None else segment.start_ts)
        from_ts = max(segment.start_ts, min(from_ts, max(segment.start_ts, segment.end_ts - 1)))

        sock: Optional[socket.socket] = None
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
            sock.settimeout(6.0)
            sock.connect((self.ip, self.port))
            self._stream_sock = sock

            req363 = bytearray(256)
            struct.pack_into("<I", req363, 0, 363)
            struct.pack_into("<I", req363, 4, self.device_id & 0xFFFFFFFF)
            struct.pack_into("<I", req363, 8, self.auth_ticket & 0xFFFFFFFF)
            struct.pack_into("<I", req363, 12, segment.seg_id & 0xFFFFFFFF)
            self._put_wall_clock(req363, 16, from_ts)
            sock.sendall(req363)

            resp = self._read_exact(sock, 64, timeout=6.0)
            while len(resp) == 64 and struct.unpack_from("<I", resp, 0)[0] == 2000:
                resp = self._read_exact(sock, 64, timeout=6.0)
            if len(resp) < 18:
                return False, "Short playback handshake response"

            rcmd, rres = struct.unpack_from("<ii", resp, 0)
            if rcmd != 463 or rres != 1000:
                return False, f"Camera refused playback ({rcmd}/{rres})"

            w, h = struct.unpack_from("<hh", resp, 14)
            if 160 <= w <= 4096:
                self.frame_width = int(w)
            if 120 <= h <= 4096:
                self.frame_height = int(h)

            playback_note = 0
            last_fid = 0
            last_pkt = 0

            start364 = bytearray(32)
            struct.pack_into("<I", start364, 0, 364)
            start364[4] = 1
            start364[5] = playback_note
            struct.pack_into("<H", start364, 6, 200)
            struct.pack_into("<I", start364, 8, 0)
            struct.pack_into("<I", start364, 12, segment.seg_id & 0xFFFFFFFF)
            self._put_wall_clock(start364, 16, from_ts)
            struct.pack_into("<H", start364, 23, 0)
            sock.sendall(start364)

            def send_ack() -> None:
                ack = bytearray(32)
                struct.pack_into("<I", ack, 0, 364)
                ack[4] = 2
                ack[5] = playback_note
                struct.pack_into("<H", ack, 6, 200)
                struct.pack_into("<H", ack, 8, last_fid & 0xFFFF)
                struct.pack_into("<I", ack, 12, segment.seg_id & 0xFFFFFFFF)
                struct.pack_into("<H", ack, 23, last_pkt & 0xFFFF)
                sock.sendall(ack)

            pending = bytearray()
            packets_since_ack = 0
            need_decrypt = self.device_version > 30

            while not is_stopped():
                hdr = self._read_exact(sock, 8, timeout=8.0)
                if len(hdr) < 8:
                    break
                ptype, note, pkt_no, psize = struct.unpack_from("<BBHH", hdr, 0)
                payload = self._read_exact(sock, psize, timeout=8.0) if psize > 0 else b""
                if psize > 0 and len(payload) < psize:
                    break

                if ptype == 0x7F:
                    playback_note = note
                    last_pkt = pkt_no
                    pending.extend(payload)
                    packets_since_ack += 1

                    while len(pending) >= 22 and not is_stopped():
                        fid, ftype = struct.unpack_from("<IH", pending, 0)
                        fts = struct.unpack_from("<Q", pending, 10)[0]
                        fsize = struct.unpack_from("<I", pending, 18)[0]
                        if fsize > 0x80000:
                            pending.clear()
                            break
                        if len(pending) < 22 + fsize:
                            break
                        fbody = bytearray(pending[22 : 22 + fsize])
                        del pending[: 22 + fsize]
                        last_fid = fid

                        if ftype in (0, 1, 40, 41, 50, 51, 52, 53):
                            if need_decrypt and self._find_video_start_code(fbody) < 0:
                                self._decrypt_video_frame(fbody)
                            norm = self._normalize_video_payload(bytes(fbody))
                            if not norm:
                                continue
                            codec = VideoCodec.H265 if ftype in (40, 41, 50, 51, 52, 53) else VideoCodec.H264
                            is_key = ftype in (0, 40, 50, 52)
                            cb(
                                CameraFrame(
                                    is_audio=False,
                                    raw_type=ftype,
                                    payload=norm,
                                    codec=codec,
                                    is_keyframe=is_key,
                                    frame_id=fid,
                                    frame_type=ftype,
                                    frame_rate=25,
                                    timestamp=fts,
                                )
                            )
                        elif ftype in (20, 21, 22, 23, 24, 25, 26, 27) and self.enable_audio:
                            if need_decrypt and len(fbody) >= 16:
                                if self.communication_version == 21:
                                    self._decrypt_media_pre2k(fbody, mode=1)
                                else:
                                    self._decrypt_audio_frame(fbody)
                            if ftype == 24 or (
                                len(fbody) >= 4 and fbody[0] == 0xFF and (fbody[1] & 0xF0) == 0xF0
                            ):
                                acodec = "aac"
                            elif ftype == 26:
                                acodec = "alaw"
                            else:
                                acodec = "adpcm"
                            cb(
                                CameraFrame(
                                    is_audio=True,
                                    raw_type=ftype,
                                    payload=bytes(fbody),
                                    frame_id=fid,
                                    frame_type=ftype,
                                    frame_rate=25,
                                    timestamp=fts,
                                    audio_codec=acodec,
                                )
                            )

                    if packets_since_ack >= 200 and not is_stopped():
                        send_ack()
                        packets_since_ack = 0

                elif ptype == 0x6F:
                    send_ack()
                    packets_since_ack = 0
                elif ptype == 0xEF:
                    return True, "Segment finished"
                elif ptype == 0x1F:
                    continue

            return True, "Stopped"
        except Exception as exc:
            if is_stopped():
                return True, "Stopped"
            return False, f"Playback error: {exc}"
        finally:
            if sock is not None:
                if not is_stopped():
                    try:
                        sock.settimeout(0.1)
                        stop364 = bytearray(32)
                        struct.pack_into("<I", stop364, 0, 364)
                        stop364[4] = 3
                        sock.sendall(stop364)
                    except Exception:
                        pass
                try:
                    sock.close()
                except Exception:
                    pass
            self._close_stream_sock()


# ===========================================================================
# V380 Gen-3 HSC / Cloud IOTC Protocol Implementation (`NVDEVRESULT^300`)
# ===========================================================================
_X25519_P = (1 << 255) - 19
_X25519_A24 = 121665  # (486662 - 2) // 4 per RFC 7748 Section 5


def _x25519_cswap(swap: int, x2: int, x3: int) -> Tuple[int, int]:
    mask = (-swap) & (x2 ^ x3)
    return x2 ^ mask, x3 ^ mask


def x25519(private_bytes: bytes, peer_public_bytes: bytes) -> bytes:
    """Pure-Python RFC 7748 Section 5 X25519 scalar multiplication."""
    if len(private_bytes) != 32 or len(peer_public_bytes) != 32:
        raise ValueError("X25519 keys must be exactly 32 bytes")

    k_buf = bytearray(private_bytes)
    k_buf[0] &= 248
    k_buf[31] &= 127
    k_buf[31] |= 64
    k = int.from_bytes(k_buf, "little")

    u_buf = bytearray(peer_public_bytes)
    u_buf[31] &= 127
    u = int.from_bytes(u_buf, "little")

    p = _X25519_P
    a24 = _X25519_A24

    x1 = u
    x2 = 1
    z2 = 0
    x3 = u
    z3 = 1
    swap = 0

    for t in range(254, -1, -1):
        kt = (k >> t) & 1
        swap ^= kt
        x2, x3 = _x25519_cswap(swap, x2, x3)
        z2, z3 = _x25519_cswap(swap, z2, z3)
        swap = kt

        a = (x2 + z2) % p
        aa = (a * a) % p
        b = (x2 - z2) % p
        bb = (b * b) % p
        e = (aa - bb) % p
        c = (x3 + z3) % p
        d = (x3 - z3) % p
        da = (d * a) % p
        cb = (c * b) % p
        x3 = pow(da + cb, 2, p)
        z3 = (x1 * pow(da - cb, 2, p)) % p
        x2 = (aa * bb) % p
        z2 = (e * (aa + a24 * e)) % p

    x2, x3 = _x25519_cswap(swap, x2, x3)
    z2, z3 = _x25519_cswap(swap, z2, z3)
    res = (x2 * pow(z2, p - 2, p)) % p
    return res.to_bytes(32, "little")


def x25519_public_from_private(private_bytes: bytes) -> bytes:
    """Derive 32-byte X25519 public key from 32-byte private key material."""
    base_point = (9).to_bytes(32, "little")
    return x25519(private_bytes, base_point)


class _HscAuthError(Exception):
    """Raised when V380 Cloud rejects account credentials or authorization."""


class HscCameraClient:
    """
    V380 Gen-3 HSC (`NVDEVRESULT^300`, Platform 20001) Cloud + IOTC Camera Client.

    Supports newer V380 Pro cameras (e.g. Allwinner HSC models) that disable local
    TCP port 8800 and authenticate via V380 Pro account login + X25519 key exchange
    + IOTC selective-AES-128-CBC media streaming.
    """

    API_BASE = "https://mapi.av380.net"
    EXCH_URL = "http://secret.av380.net/v1/app/exch"
    DISPATCH_URL = "http://dispatch.av380.net:8001/api/v1/get_stream_server"

    _APP_LOGIN_AES_KEY = b"1234567890123456"
    _APP_SIGN_SALT = "hsshop2016"
    _DISPATCH_SALT = "hsdata2022"
    _MR_SIGN_SALT = "2021Mr"

    # IOTC Commands & Method IDs
    IOTC_CMD_JSON_REQ = 0x38030818
    IOTC_CMD_MEDIA = 0x38030820
    IOTC_CMD_JSON_PUSH = 0x38030822

    METHOD_KEY_EXCH = 0x30102     # 196866
    METHOD_KEEPALIVE = 0x30200    # 197120
    METHOD_LIVE_START = 0x30202   # 197122
    METHOD_MEDIA_INFO = 0x30204   # 197124
    METHOD_SD_LIST = 0x30220      # 197152
    METHOD_SD_REPLAY = 0x30222    # 197154
    METHOD_SD_INFO = 0x30224      # 197156
    METHOD_SD_STOP = 0x30990      # 199056
    METHOD_PROP_READ = 0x30300    # 197376
    METHOD_PROP_WRITE = 0x30302   # 197378
    METHOD_ACTION = 0x30308       # 197384

    DAID_PTZ = 0x60200            # 393728
    DPID_WHITE_LIGHT = 0x50100    # 327936
    DPID_NIGHT_VISION = 0x80105   # 524549

    # Shared process-wide account & key-session caches (prevents login rate-limiting)
    _GLOBAL_LOCK = threading.Lock()
    _REJECTED_CREDS: Dict[Tuple[str, str], str] = {}
    _ACCOUNT_CACHE: Dict[Tuple[str, str], Dict[str, Any]] = {}
    _DEVICE_SESSIONS: Dict[int, Dict[str, Any]] = {}
    _DISPATCH_CACHE: Dict[int, Tuple[float, List[Dict[str, Any]]]] = {}

    def __init__(
        self,
        ip: str,
        port: int = 8800,
        device_id: int | str = 0,
        username: str = "admin",
        password: str = "",
        stream_quality: int = 1,  # 1 = HD, 0 = SD
        enable_audio: bool = True,
        on_frame: Optional[Callable[[CameraFrame], None]] = None,
        on_status: Optional[Callable[[str], None]] = None,
    ) -> None:
        self.ip = (ip or "").strip()
        self.port = int(port or 8800)
        self.device_id = int(device_id) if str(device_id).strip().isdigit() else 0
        self.username = (username or "").strip()
        self.password = password or ""
        self.stream_quality = 1 if int(stream_quality) else 0
        self.enable_audio = bool(enable_audio)
        self.on_frame = on_frame
        self.on_status = on_status

        self.frame_width: int = 1920
        self.frame_height: int = 1080

        self._sec_type: int = 2
        self._sid: int = 0
        self._aes_key: bytes = b"\x00" * 16
        self._aes_iv: bytes = b"\x00" * 16
        self._rk_bytes: bytes = b""
        self._sub_server: str = ""
        self._video_codec_hint: VideoCodec = VideoCodec.H265
        self._audio_codec_id: int = 101  # 101 = IMA-ADPCM, 100 = PCM16
        self._audio_sample_rate: int = 16000
        self._locked_video_stream: Optional[int] = None

        self._stream_sock: Optional[socket.socket] = None
        self._send_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._need_reconnect = False
        self._force_cloud_wake = False
        self._last_video_frame_mono: float = 0.0
        self._last_keepalive_mono: float = 0.0
        self._req_seq: int = random.randint(1000, 50000)

    # --- Helpers & Cryptography ---
    @staticmethod
    def _compact_json(obj: Any) -> str:
        return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _md5_hex(text: str) -> str:
        return hashlib.md5(text.encode("utf-8")).hexdigest()

    @staticmethod
    def _pkcs7_pad(data: bytes, block_size: int = 16) -> bytes:
        pad_len = block_size - (len(data) % block_size)
        return data + bytes([pad_len]) * pad_len

    @staticmethod
    def _pkcs7_unpad(data: bytes, block_size: int = 16) -> bytes:
        if not data or (len(data) % block_size) != 0:
            raise ValueError("Invalid PKCS#7 block length")
        pad_len = data[-1]
        if pad_len < 1 or pad_len > block_size:
            raise ValueError("Invalid PKCS#7 pad value")
        if data[-pad_len:] != bytes([pad_len]) * pad_len:
            raise ValueError("Corrupt PKCS#7 padding")
        return data[:-pad_len]

    @classmethod
    def _encode_login_password(cls, password: str) -> str:
        padded = cls._pkcs7_pad(password.encode("utf-8"), 16)
        return AES.new(cls._APP_LOGIN_AES_KEY, AES.MODE_ECB).encrypt(padded).hex()

    @classmethod
    def _mr_signature(cls, compact_body: str, timestamp: int) -> str:
        inner = cls._md5_hex(compact_body + str(timestamp))
        return cls._md5_hex(inner + cls._MR_SIGN_SALT)

    @staticmethod
    def _cred_key(username: str, password: str) -> Tuple[str, str]:
        pw_hash = hashlib.sha256(password.encode("utf-8")).hexdigest()
        return (username.strip(), pw_hash)

    def _next_req_id(self) -> int:
        self._req_seq = (self._req_seq + 1) & 0x7FFFFFFF
        if self._req_seq == 0:
            self._req_seq = 1
        return self._req_seq

    def _emit_status(self, msg: str) -> None:
        if self.on_status:
            try:
                self.on_status(msg)
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_event.set()
        self._close_stream_sock()

    def _close_stream_sock(self) -> None:
        sock = self._stream_sock
        self._stream_sock = None
        if sock is not None:
            def _do_close(s: socket.socket = sock) -> None:
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except Exception:
                    pass
                try:
                    s.close()
                except Exception:
                    pass

            if threading.current_thread() is threading.main_thread():
                threading.Thread(target=_do_close, daemon=True).start()
            else:
                _do_close()

    def _wake_lan_udp(self, burst_count: int = 2) -> None:
        """Wake Wi-Fi radio from DTIM power-save on the local subnet if reachable."""
        if not self.ip:
            return
        padded_cmd = LanCameraDiscovery.SEARCH_CMD.ljust(128, b"\x00")
        targets = [(self.ip, 10008)]
        parts = self.ip.split(".")
        if len(parts) == 4 and all(p.isdigit() and 0 <= int(p) <= 255 for p in parts):
            bcast_ip = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
            if bcast_ip != self.ip:
                targets.append((bcast_ip, 10008))
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as s:
                LanCameraDiscovery._configure_udp_socket(s)
                try:
                    s.bind(("0.0.0.0", 10009))
                except OSError:
                    pass
                for _ in range(max(1, burst_count)):
                    for dst in targets:
                        try:
                            s.sendto(LanCameraDiscovery.SEARCH_CMD, dst)
                            s.sendto(padded_cmd, dst)
                        except Exception:
                            pass
                    time.sleep(0.02)
        except Exception:
            pass

    def send_wake_pulse(self) -> None:
        self._wake_lan_udp(burst_count=1)
        sock = self._stream_sock
        if sock is not None and (self._sid > 0 or self._sec_type in (0, 1)):
            try:
                self._send_iotc_json(
                    sock,
                    self.METHOD_KEEPALIVE,
                    {"flag": 2, "tms1": 0, "decode_tms": 0},
                )
                self._last_keepalive_mono = time.monotonic()
            except Exception:
                pass

    def trigger_wake_reconnect(self) -> None:
        self._force_cloud_wake = True
        self._need_reconnect = True
        self._close_stream_sock()

    def set_stream_quality(self, quality: int) -> None:
        new_q = 1 if int(quality) else 0
        self.stream_quality = new_q
        sock = self._stream_sock
        if sock is not None and (self._sid > 0 or self._sec_type in (0, 1)):
            try:
                self._send_iotc_json(
                    sock,
                    self.METHOD_LIVE_START,
                    {
                        "up": {
                            "quality": new_q,
                            "video": 1,
                            "audio": 1 if self.enable_audio else 0,
                            "IDRreq": 1,
                        }
                    },
                )
                self._emit_status(f"Streaming {'HD' if new_q else 'SD'} (V380 HSC)")
                return
            except Exception:
                pass
        self._need_reconnect = True
        self._close_stream_sock()

    # --- HTTP JSON Helpers ---
    @classmethod
    def _http_post_json(
        cls,
        url: str,
        body_obj: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
        timeout: float = 10.0,
    ) -> Tuple[Dict[str, Any], str]:
        raw_str = cls._compact_json(body_obj)
        raw_bytes = raw_str.encode("utf-8")
        headers = {
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
            "User-Agent": "okhttp/4.9.3",
        }
        if extra_headers:
            headers.update(extra_headers)
        req = urllib.request.Request(url, data=raw_bytes, headers=headers, method="POST")
        ctx = ssl.create_default_context() if url.startswith("https://") else None
        try:
            with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
                resp_bytes = resp.read(8 * 1024 * 1024)
            parsed = json.loads(resp_bytes.decode("utf-8", errors="replace"))
            if not isinstance(parsed, dict):
                raise RuntimeError("Unexpected JSON format from V380 Cloud")
            return parsed, raw_str
        except urllib.error.HTTPError as he:
            if he.code in (401, 403):
                raise _HscAuthError(f"V380 Cloud HTTP {he.code} authorization rejected") from None
            raise RuntimeError(f"V380 Cloud HTTP {he.code}") from None
        except urllib.error.URLError as ue:
            raise RuntimeError(f"Cannot reach V380 Cloud ({ue.reason})") from None

    # --- Account Login, Public Key Discovery & X25519 Session ---
    def _has_app_credentials(self) -> bool:
        u = self.username.strip()
        if not u or not self.password:
            return False
        if u.lower() == "admin":
            return False
        if self.device_id > 0 and u == str(self.device_id):
            return False
        return True

    def _resolve_device_id_if_missing(self) -> bool:
        if self.device_id > 0:
            return True
        if not self.ip:
            return False
        self._emit_status("Discovering HSC Device ID...")
        cam = LanCameraDiscovery.query_single_ip(self.ip, timeout_sec=1.0)
        if cam and cam.dev_id.isdigit():
            self.device_id = int(cam.dev_id)
            return True
        return False

    @classmethod
    def _decode_pubkey_b64(cls, val: Any) -> Optional[bytes]:
        if not isinstance(val, str) or len(val.strip()) < 40:
            return None
        try:
            raw = base64.b64decode(val.strip())
            if len(raw) == 32 and raw != (b"\x00" * 32):
                return raw
        except Exception:
            pass
        return None

    @classmethod
    def _extract_device_meta_map(cls, obj: Any, out_map: Dict[int, Dict[str, Any]]) -> None:
        """Extract device metadata (`pk_bytes`, `ike`, `rk_bytes`, `sub_server`, `model`, `pver`) from API JSON."""
        now_m = time.monotonic()
        if isinstance(obj, dict):
            did_val = obj.get("device_id")
            if did_val is None:
                did_val = obj.get("dev_id") if obj.get("dev_id") is not None else obj.get("deviceid")
            if did_val is not None and str(did_val).strip().isdigit():
                did_int = int(str(did_val).strip())
                if did_int > 0:
                    entry = out_map.setdefault(
                        did_int,
                        {
                            "pk_bytes": None,
                            "ike": 0,
                            "rk_bytes": b"",
                            "sub_server": "",
                            "model": "",
                            "pver": 0,
                            "checked_mono": now_m,
                        },
                    )
                    entry["checked_mono"] = now_m
                    for k_name in ("public_key", "pKey", "p_key", "publicKey"):
                        pk = cls._decode_pubkey_b64(obj.get(k_name))
                        if pk is not None:
                            entry["pk_bytes"] = pk
                            entry["ike"] = 1
                            break
                    if "ike" in obj and obj.get("ike") is not None:
                        try:
                            entry["ike"] = int(obj["ike"])
                        except Exception:
                            pass
                    rk_val = obj.get("rk")
                    if isinstance(rk_val, str):
                        rk_b = rk_val.encode("utf-8")
                        if len(rk_b) == 16:
                            entry["rk_bytes"] = rk_b
                    sub_srv = obj.get("sub_server")
                    if isinstance(sub_srv, str) and sub_srv.strip():
                        entry["sub_server"] = sub_srv.strip()
                    mdl = obj.get("model")
                    if isinstance(mdl, str) and mdl.strip():
                        entry["model"] = mdl.strip()
                    if obj.get("pver") is not None:
                        try:
                            entry["pver"] = int(obj["pver"])
                        except Exception:
                            pass
            for v in obj.values():
                if isinstance(v, (dict, list)):
                    cls._extract_device_meta_map(v, out_map)
                elif isinstance(v, str) and len(v) > 24 and v.startswith(("eyJ", "{")):
                    try:
                        decoded_str = v if v.startswith("{") else base64.b64decode(v).decode("utf-8")
                        cls._extract_device_meta_map(json.loads(decoded_str), out_map)
                    except Exception:
                        pass
        elif isinstance(obj, list):
            for item in obj:
                if isinstance(item, str) and len(item) > 24:
                    try:
                        decoded_str = item if item.startswith("{") else base64.b64decode(item).decode("utf-8")
                        item = json.loads(decoded_str)
                    except Exception:
                        continue
                cls._extract_device_meta_map(item, out_map)

    def _ensure_account_token_and_pubkey(
        self, force_renew_pubkey: bool = False, force_relogin: bool = False
    ) -> Tuple[str, Optional[bytes], Dict[str, Any]]:
        ckey = self._cred_key(self.username, self.password)
        with self._GLOBAL_LOCK:
            rejected_msg = self._REJECTED_CREDS.get(ckey)
            if rejected_msg:
                raise _HscAuthError(rejected_msg)
            acct = self._ACCOUNT_CACHE.get(ckey)
            if acct is None:
                acct = {
                    "lock": threading.Lock(),
                    "access_token": "",
                    "pubkeys": {},
                    "dev_meta": {},
                    "login_mono": 0.0,
                }
                self._ACCOUNT_CACHE[ckey] = acct

        with acct["lock"]:
            with self._GLOBAL_LOCK:
                rejected_msg = self._REJECTED_CREDS.get(ckey)
                if rejected_msg:
                    raise _HscAuthError(rejected_msg)

            if force_relogin:
                acct["access_token"] = ""

            if not acct["access_token"]:
                self._emit_status("Signing in to V380 Pro Cloud...")
                ts = int(time.time())
                enc_pw = self._encode_login_password(self.password)
                sign = self._md5_hex(
                    f"password={enc_pw}&phonetoken=100&timestamp={ts}&username={self.username}{self._APP_SIGN_SALT}"
                )
                login_body = {
                    "username": self.username,
                    "password": enc_pw,
                    "phonetoken": "100",
                    "sign": sign,
                    "timestamp": ts,
                }
                resp, _ = self._http_post_json(f"{self.API_BASE}/user/login", login_body, timeout=12.0)
                res_code = resp.get("result")
                err_code = int(resp.get("error_code") or 0)
                if res_code != 0 or err_code != 0:
                    if err_code in (21004, 21005, 21006, 21007):
                        rem = resp.get("remaining_time")
                        rem_note = f" ({rem} tries left)" if isinstance(rem, int) and rem > 0 else ""
                        msg = f"Invalid V380 Pro account or password{rem_note} — update in Camera Settings"
                        with self._GLOBAL_LOCK:
                            self._REJECTED_CREDS[ckey] = msg
                        raise _HscAuthError(msg)
                    raise RuntimeError(f"V380 Cloud login error (result={res_code}, code={err_code})")

                data_obj = resp.get("data") if isinstance(resp.get("data"), dict) else {}
                token = str(data_obj.get("access_token") or "").strip()
                if not token:
                    raise RuntimeError("V380 Cloud login returned empty access_token")
                acct["access_token"] = token
                acct["login_mono"] = time.monotonic()

            token = acct["access_token"]
            dev_meta_map: Dict[int, Dict[str, Any]] = acct.setdefault("dev_meta", {})
            existing_meta = dev_meta_map.get(self.device_id)
            cached_pk: Optional[bytes] = None if force_renew_pubkey else acct["pubkeys"].get(self.device_id)
            if not force_renew_pubkey and existing_meta is not None:
                if cached_pk is not None and len(cached_pk) == 32:
                    return token, cached_pk, dict(existing_meta)
                if (
                    cached_pk is None
                    and int(existing_meta.get("ike") or 0) == 0
                    and (time.monotonic() - float(existing_meta.get("checked_mono") or 0.0)) < 25.0
                ):
                    return token, None, dict(existing_meta)

            # 1. Try /device/renew-public-key first when explicitly refreshing session keys
            if force_renew_pubkey and existing_meta is not None:
                self._emit_status("Waking HSC camera via V380 Cloud...")
                pk_renewed, ike_renewed = self._request_renew_pubkey_info(token)
                if pk_renewed is not None:
                    acct["pubkeys"][self.device_id] = pk_renewed
                    existing_meta["pk_bytes"] = pk_renewed
                    existing_meta["ike"] = 1
                    existing_meta["checked_mono"] = time.monotonic()
                    return token, pk_renewed, dict(existing_meta)

            # 2. Query /device/list (get_sub_server=10 so regional sub_server is populated)
            resp_list: Dict[str, Any] = {}
            for list_try in range(2):
                ts = int(time.time())
                canon = (
                    f"accesstoken={token}&from_app=V380Pro&get_sub_server=10"
                    f"&language=en&registrationid=&timestamp={ts}&type=all&ver=1008"
                )
                list_body = {
                    "accesstoken": token,
                    "from_app": "V380Pro",
                    "get_sub_server": 10,
                    "language": "en",
                    "registrationid": "",
                    "sign": self._md5_hex(canon + self._APP_SIGN_SALT),
                    "timestamp": ts,
                    "type": "all",
                    "ver": 1008,
                }
                resp_list, _ = self._http_post_json(f"{self.API_BASE}/device/list", list_body, timeout=12.0)
                err_list = int(resp_list.get("error_code") or 0)
                if err_list == 21011 and not force_relogin:
                    acct["access_token"] = ""
                    return self._ensure_account_token_and_pubkey(
                        force_renew_pubkey=force_renew_pubkey, force_relogin=True
                    )

                if resp_list.get("renew_token") is True:
                    try:
                        ts_r = int(time.time())
                        r_resp, _ = self._http_post_json(
                            f"{self.API_BASE}/user/get-new-token",
                            {
                                "accesstoken": token,
                                "timestamp": ts_r,
                                "sign": self._md5_hex(f"accesstoken={token}&timestamp={ts_r}{self._APP_SIGN_SALT}"),
                            },
                            timeout=10.0,
                        )
                        new_tok = str(r_resp.get("access_token") or "").strip()
                        if new_tok:
                            token = new_tok
                            acct["access_token"] = new_tok
                    except Exception:
                        pass

                self._extract_device_meta_map(resp_list, dev_meta_map)
                for did_k, m_val in dev_meta_map.items():
                    pk_b = m_val.get("pk_bytes")
                    if isinstance(pk_b, bytes) and len(pk_b) == 32:
                        acct["pubkeys"][did_k] = pk_b

                if self.device_id in dev_meta_map:
                    break
                if resp_list.get("result") in (10005, 10007) and list_try == 0:
                    time.sleep(0.7)
                    continue
                break

            meta = dev_meta_map.get(self.device_id)
            if meta is None:
                list_res = resp_list.get("result")
                if list_res not in (0, None):
                    raise RuntimeError(f"V380 Cloud device list incomplete (result={list_res})")
                raise _HscAuthError(
                    f"Camera {self.device_id} not found in this V380 Pro account — verify account in Settings"
                )

            if meta.get("sub_server"):
                self._sub_server = str(meta["sub_server"])
            if meta.get("rk_bytes"):
                self._rk_bytes = bytes(meta["rk_bytes"])

            pk_bytes: Optional[bytes] = meta.get("pk_bytes") or acct["pubkeys"].get(self.device_id)
            if pk_bytes is None or force_renew_pubkey:
                self._emit_status("Waking HSC camera via V380 Cloud...")
                pk_renewed, ike_renewed = self._request_renew_pubkey_info(token)
                if pk_renewed is not None:
                    pk_bytes = pk_renewed
                    meta["pk_bytes"] = pk_renewed
                    meta["ike"] = 1
                elif ike_renewed is not None and pk_bytes is None:
                    meta["ike"] = ike_renewed

            meta["checked_mono"] = time.monotonic()
            if pk_bytes is not None and len(pk_bytes) == 32:
                acct["pubkeys"][self.device_id] = pk_bytes
                return token, pk_bytes, dict(meta)

            # Camera is bound to account with ike=0 / public_key=False (uses rk / plain IOTC or is asleep)
            if int(meta.get("ike") or 0) == 0:
                return token, None, dict(meta)

            raise RuntimeError(f"Camera {self.device_id} is asleep/offline (no X25519 session key published)")

    def _request_renew_pubkey_info(self, token: str) -> Tuple[Optional[bytes], Optional[int]]:
        try:
            ts = int(time.time())
            sign = self._md5_hex(f"accesstoken={token}&deviceid={self.device_id}&timestamp={ts}{self._APP_SIGN_SALT}")
            body = {
                "accesstoken": token,
                "deviceid": int(self.device_id),
                "timestamp": ts,
                "sign": sign,
            }
            resp, _ = self._http_post_json(f"{self.API_BASE}/device/renew-public-key", body, timeout=10.0)
            ike_val: Optional[int] = None
            if resp.get("ike") is not None:
                try:
                    ike_val = int(resp["ike"])
                except Exception:
                    pass
            elif isinstance(resp.get("data"), dict) and resp["data"].get("ike") is not None:
                try:
                    ike_val = int(resp["data"]["ike"])
                except Exception:
                    pass
            for k_name in ("public_key", "pKey", "p_key", "publicKey"):
                pk = self._decode_pubkey_b64(resp.get(k_name))
                if pk is not None:
                    return pk, 1
                if isinstance(resp.get("data"), dict):
                    pk = self._decode_pubkey_b64(resp["data"].get(k_name))
                    if pk is not None:
                        return pk, 1
            tmp_map: Dict[int, Dict[str, Any]] = {}
            self._extract_device_meta_map(resp, tmp_map)
            if self.device_id in tmp_map and tmp_map[self.device_id].get("pk_bytes") is not None:
                return tmp_map[self.device_id]["pk_bytes"], 1
            return None, ike_val
        except Exception:
            return None, None

    def _ensure_hsc_key_session(self, force_refresh: bool = False) -> Tuple[str, int, int, bytes, bytes]:
        """
        Ensure a valid HSC IOTC session `(access_token, sec_type, sid, aes_key, aes_iv)` for
        `self.device_id`:
          - `ike == 1` (`dev_pub` 32B): X25519 `/v1/app/exch` -> `sec_type = 2` (AES-128-CBC)
          - `ike == 0` (`dev_pub is None`): `sec_type = 1` (AES-128-ECB via 16B `rk`) or `0` (plain)
        """
        with self._GLOBAL_LOCK:
            sess = self._DEVICE_SESSIONS.get(self.device_id)
            if sess is None:
                sess = {
                    "lock": threading.Lock(),
                    "sec_type": 2,
                    "ike0_sec_type": None,
                    "sid": 0,
                    "aes_key": b"",
                    "aes_iv": b"",
                    "created_mono": 0.0,
                }
                self._DEVICE_SESSIONS[self.device_id] = sess

        with sess["lock"]:
            token, dev_pub, dev_meta = self._ensure_account_token_and_pubkey(force_renew_pubkey=force_refresh)
            if dev_meta.get("sub_server"):
                self._sub_server = str(dev_meta["sub_server"])
            if dev_meta.get("rk_bytes"):
                self._rk_bytes = bytes(dev_meta["rk_bytes"])

            if dev_pub is None:
                rk_b = self._rk_bytes
                pref_sec = sess.get("ike0_sec_type")
                if pref_sec in (0, 1):
                    sec_t = int(pref_sec)
                else:
                    sec_t = 1 if len(rk_b) == 16 else 0
                use_key = rk_b if len(rk_b) == 16 else (b"\x00" * 16)
                use_iv = b"\x00" * 16
                self._sec_type = sec_t
                self._sid = 0
                self._aes_key = use_key
                self._aes_iv = use_iv
                return token, sec_t, 0, use_key, use_iv

            age = time.monotonic() - float(sess.get("created_mono") or 0.0)
            if (
                not force_refresh
                and sess.get("sec_type") == 2
                and sess.get("sid", 0) > 0
                and len(sess.get("aes_key", b"")) == 16
                and len(sess.get("aes_iv", b"")) == 16
                and age < 1800.0
            ):
                self._sec_type = 2
                self._sid = int(sess["sid"])
                self._aes_key = bytes(sess["aes_key"])
                self._aes_iv = bytes(sess["aes_iv"])
                return token, 2, self._sid, self._aes_key, self._aes_iv

            self._wake_lan_udp(burst_count=2)
            last_err = "HSC key exchange failed"
            for attempt in range(2):
                if attempt > 0:
                    token, dev_pub, dev_meta = self._ensure_account_token_and_pubkey(force_renew_pubkey=True)
                    if dev_pub is None:
                        rk_b = self._rk_bytes
                        sec_t = 1 if len(rk_b) == 16 else 0
                        use_key = rk_b if len(rk_b) == 16 else (b"\x00" * 16)
                        self._sec_type = sec_t
                        self._sid = 0
                        self._aes_key = use_key
                        self._aes_iv = b"\x00" * 16
                        return token, sec_t, 0, use_key, self._aes_iv
                self._emit_status("Negotiating HSC X25519 session key...")
                priv = secrets.token_bytes(32)
                client_pub = x25519_public_from_private(priv)
                shared = x25519(priv, dev_pub)
                if shared == (b"\x00" * 32):
                    last_err = "Invalid X25519 public key from camera"
                    continue

                md5_c = hashlib.md5(client_pub).digest()
                md5_d = hashlib.md5(dev_pub).digest()
                ds_bytes = bytes(a ^ b for a, b in zip(md5_c, md5_d))

                ts = int(time.time())
                body = {
                    "mr": {"params": {"access_token": token, "device_id": int(self.device_id)}},
                    "tr": {
                        "id": self._next_req_id(),
                        "method_id": self.METHOD_KEY_EXCH,
                        "chn": 1,
                        "ts": ts,
                        "params": {
                            "cKM": base64.b64encode(client_pub).decode("ascii"),
                            "dS": base64.b64encode(ds_bytes).decode("ascii"),
                        },
                    },
                    "timestamp": ts,
                }
                sig = self._mr_signature(self._compact_json(body), ts)
                resp, _ = self._http_post_json(self.EXCH_URL, body, extra_headers={"Signature": sig}, timeout=12.0)

                outer_code = resp.get("code")
                tr = resp.get("tr") if isinstance(resp.get("tr"), dict) else {}
                tr_res = tr.get("result") if isinstance(tr.get("result"), dict) else {}
                inner_code = tr_res.get("code")
                if outer_code == 1000 and inner_code == 1000:
                    tr_data = tr.get("data") if isinstance(tr.get("data"), dict) else {}
                    sid = int(tr_data.get("sid") or 0)
                    aes_key = shared[:16]
                    aes_iv = shared[16:32]
                    sess["sec_type"] = 2
                    sess["sid"] = sid
                    sess["aes_key"] = aes_key
                    sess["aes_iv"] = aes_iv
                    sess["created_mono"] = time.monotonic()
                    self._sec_type = 2
                    self._sid = sid
                    self._aes_key = aes_key
                    self._aes_iv = aes_iv
                    return token, 2, sid, aes_key, aes_iv

                if outer_code in (21011, 1004, 401):
                    token, dev_pub, _ = self._ensure_account_token_and_pubkey(
                        force_renew_pubkey=True, force_relogin=True
                    )
                    if dev_pub is None:
                        break
                if outer_code == 1003:
                    last_err = (
                        f"Camera {self.device_id} is asleep/offline in V380 Cloud — "
                        "open in V380 Pro app or power-cycle"
                    )
                else:
                    last_err = f"Key exchange rejected (code={outer_code}, tr={inner_code})"

            raise RuntimeError(last_err)

    def _invalidate_hsc_key_session(self, clear_meta: bool = False) -> None:
        ckey = self._cred_key(self.username, self.password)
        with self._GLOBAL_LOCK:
            sess = self._DEVICE_SESSIONS.get(self.device_id)
            if sess is not None:
                sess["sid"] = 0
                sess["created_mono"] = 0.0
            acct = self._ACCOUNT_CACHE.get(ckey) if clear_meta else None
        if acct is not None:
            with acct["lock"]:
                acct.get("pubkeys", {}).pop(self.device_id, None)
                meta = acct.get("dev_meta", {}).get(self.device_id)
                if isinstance(meta, dict):
                    meta["checked_mono"] = 0.0

    # --- Dispatch & Relay Channel Allocation ---
    def _get_dispatch_relays(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        now = time.monotonic()
        if not force_refresh:
            with self._GLOBAL_LOCK:
                cached = self._DISPATCH_CACHE.get(self.device_id)
                if cached and (now - cached[0]) < 120.0 and cached[1]:
                    return list(cached[1])

        dispatch_urls: List[str] = []
        sub_srv = (self._sub_server or "").strip()
        if sub_srv:
            if ":" in sub_srv:
                h_part, p_part = sub_srv.rsplit(":", 1)
                d_port = int(p_part) if p_part.isdigit() else 8001
                dispatch_urls.append(f"http://{h_part}:{d_port}/api/v1/get_stream_server")
            else:
                dispatch_urls.append(f"http://{sub_srv}:8001/api/v1/get_stream_server")
        if self.DISPATCH_URL not in dispatch_urls:
            dispatch_urls.append(self.DISPATCH_URL)

        relays: List[Dict[str, Any]] = []
        seen_relays: set[Tuple[str, int, int]] = set()
        for d_url in dispatch_urls:
            try:
                ts = int(time.time())
                sign_src = f"dev_id={self.device_id}&platform=20001&timestamp={ts}{self._DISPATCH_SALT}"
                sign = hashlib.sha1(sign_src.encode("utf-8")).hexdigest()
                body = {
                    "dev_id": int(self.device_id),
                    "platform": 20001,
                    "timestamp": ts,
                    "sign": sign,
                }
                resp, _ = self._http_post_json(d_url, body, timeout=7.0)
                rows = resp.get("data")
                if isinstance(rows, list):
                    for item in rows:
                        if not isinstance(item, dict):
                            continue
                        host = str(item.get("ip") or item.get("domain") or "").strip()
                        if not host:
                            continue
                        tp = item.get("tp") if isinstance(item.get("tp"), dict) else {}
                        call_port = int(tp.get("call_port") or 80)
                        trans_port = int(tp.get("trans_port") or 32100)
                        r_key = (host, call_port, trans_port)
                        if r_key not in seen_relays:
                            seen_relays.add(r_key)
                            relays.append({"host": host, "call_port": call_port, "trans_port": trans_port})
            except Exception:
                continue

        if not relays:
            raise RuntimeError(f"No V380 HSC relay servers returned for {self.device_id}")
        with self._GLOBAL_LOCK:
            self._DISPATCH_CACHE[self.device_id] = (now, relays)
        return relays

    def _open_relay_channel(self, access_token: str, call_type: int) -> socket.socket:
        """
        Allocate an HSC channel (`call=2` live, `3` SD list, `5` SD replay) via `/v1/app/call`,
        connect TCP to `trs_ip:trs_port`, and verify the 1500-byte `1100 -> 1101/1000` handshake.
        """
        self._wake_lan_udp(burst_count=2)
        relays = self._get_dispatch_relays(force_refresh=False)
        last_err = "Relay connection failed"

        for pass_idx in range(2):
            if pass_idx > 0:
                relays = self._get_dispatch_relays(force_refresh=True)
            saw_1003 = False
            saw_other_err = False
            for r_idx, r_info in enumerate(relays):
                if self._stop_event.is_set():
                    raise InterruptedError("Stopped")
                # If multiple relays confirm 1003 ("not found" in MR registry), avoid hammering all backups
                if saw_1003 and not saw_other_err and r_idx >= 3:
                    break
                host = r_info["host"]
                call_port = r_info["call_port"]
                default_trans_port = r_info["trans_port"]
                try:
                    ts = int(time.time())
                    body = {
                        "mr": {
                            "params": {
                                "access_token": access_token,
                                "device_id": int(self.device_id),
                                "call": int(call_type),
                            }
                        },
                        "timestamp": ts,
                    }
                    sig = self._mr_signature(self._compact_json(body), ts)
                    call_url = f"http://{host}:{call_port}/v1/app/call"
                    resp, _ = self._http_post_json(
                        call_url, body, extra_headers={"Signature": sig}, timeout=7.0
                    )
                    outer_code = resp.get("code")
                    mr = resp.get("mr") if isinstance(resp.get("mr"), dict) else {}
                    info: Dict[str, Any] = {}
                    if isinstance(resp.get("data"), dict):
                        info.update(resp["data"])
                    if isinstance(mr.get("data"), dict):
                        info.update(mr["data"])
                    if isinstance(mr.get("result"), dict):
                        info.update(mr["result"])

                    ch_token = str(info.get("channel_token") or "").strip()
                    if outer_code in (21011, 1004, 401) and not ch_token:
                        ckey = self._cred_key(self.username, self.password)
                        with self._GLOBAL_LOCK:
                            acct = self._ACCOUNT_CACHE.get(ckey)
                        if acct is not None:
                            with acct["lock"]:
                                acct["access_token"] = ""
                        raise RuntimeError("V380 Cloud token expired — refreshing...")

                    if outer_code == 1003 and not ch_token:
                        saw_1003 = True
                        last_err = (
                            f"Camera {self.device_id} is asleep/offline in V380 Cloud — "
                            "open in V380 Pro app or power-cycle"
                        )
                        continue
                    if outer_code not in (None, 1000) and not ch_token:
                        saw_other_err = True
                        last_err = f"Relay call rejected (code={outer_code})"
                        continue
                    if not ch_token:
                        mr_code = info.get("code", outer_code)
                        if mr_code == 1003:
                            saw_1003 = True
                            last_err = (
                                f"Camera {self.device_id} is asleep/offline in V380 Cloud — "
                                "open in V380 Pro app or power-cycle"
                            )
                        else:
                            saw_other_err = True
                            last_err = f"Camera offline or busy (call code={mr_code})"
                        continue

                    trs_ip = str(info.get("app_trs_ip") or info.get("trs_ip") or host).strip()
                    trs_port = int(info.get("app_trs_port") or info.get("trs_port") or default_trans_port)
                    ch_handle = int(info.get("channel_handle") or 0)

                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    try:
                        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                        sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 524288)
                        LanCameraClient._configure_tcp_keepalive(sock)
                        sock.settimeout(7.0)
                        sock.connect((trs_ip, trs_port))

                        hs = bytearray(1500)
                        struct.pack_into(
                            "<III",
                            hs,
                            0,
                            1100,
                            int(self.device_id) & 0xFFFFFFFF,
                            ch_handle & 0xFFFFFFFF,
                        )
                        tok_b = ch_token.encode("utf-8")[:36]
                        hs[12 : 12 + len(tok_b)] = tok_b
                        sock.sendall(hs)

                        ack = self._read_exact(sock, 64, timeout=7.0)
                        if len(ack) < 8:
                            raise RuntimeError("Short relay handshake reply")
                        rcmd, rcode = struct.unpack_from("<II", ack, 0)
                        if rcmd != 1101 or rcode != 1000:
                            raise RuntimeError(f"Relay forward rejected ({rcmd}/{rcode})")
                        return sock
                    except Exception:
                        try:
                            sock.close()
                        except Exception:
                            pass
                        raise
                except InterruptedError:
                    raise
                except Exception as exc:
                    saw_other_err = True
                    last_err = str(exc)
                    continue

            if saw_1003 and not saw_other_err:
                self._invalidate_hsc_key_session(clear_meta=True)
                break

        raise RuntimeError(last_err)

    # --- IOTC Packet Transport & Selective AES Decryption ---
    def _read_exact(
        self,
        sock: socket.socket,
        count: int,
        timeout: float = 8.0,
        stop_event: Optional[threading.Event] = None,
    ) -> bytes:
        buf = bytearray()
        deadline = time.monotonic() + timeout
        prev_to = sock.gettimeout()
        try:
            while len(buf) < count:
                if self._stop_event.is_set() or (stop_event is not None and stop_event.is_set()):
                    return bytes(buf)
                rem = deadline - time.monotonic()
                if rem <= 0:
                    raise socket.timeout("Read timed out")
                sock.settimeout(min(1.0, max(0.05, rem)))
                try:
                    chunk = sock.recv(count - len(buf))
                except socket.timeout:
                    if time.monotonic() >= deadline:
                        raise
                    continue
                if not chunk:
                    return bytes(buf)
                buf.extend(chunk)
            return bytes(buf)
        finally:
            try:
                sock.settimeout(prev_to)
            except Exception:
                pass

    def _send_iotc_json(
        self,
        sock: socket.socket,
        method_id: int,
        params: Dict[str, Any],
        sid: Optional[int] = None,
        aes_key: Optional[bytes] = None,
        aes_iv: Optional[bytes] = None,
        sec_type: Optional[int] = None,
    ) -> int:
        use_sid = self._sid if sid is None else int(sid)
        use_key = self._aes_key if aes_key is None else aes_key
        use_iv = self._aes_iv if aes_iv is None else aes_iv
        use_sec = self._sec_type if sec_type is None else int(sec_type)

        req_id = self._next_req_id()
        envelope = {
            "id": req_id,
            "chn": 1,
            "method_id": int(method_id),
            "ts": int(time.time()),
            "params": params,
        }
        plain = self._compact_json(envelope).encode("utf-8")
        if use_sec == 0:
            enc = plain
        elif use_sec == 1:
            enc = AES.new(use_key, AES.MODE_ECB).encrypt(self._pkcs7_pad(plain, 16))
        else:
            use_sec = 2
            enc = AES.new(use_key, AES.MODE_CBC, iv=use_iv).encrypt(self._pkcs7_pad(plain, 16))

        iotc_hdr = bytearray(16)
        struct.pack_into("<I", iotc_hdr, 0, self.IOTC_CMD_JSON_REQ)
        iotc_hdr[4] = 1
        iotc_hdr[5] = use_sec & 0xFF  # 0=plain, 1=AES-128-ECB, 2=AES-128-CBC
        iotc_hdr[6] = use_sid & 0xFF
        struct.pack_into("<I", iotc_hdr, 12, len(enc))
        iotc_pkt = bytes(iotc_hdr) + enc

        trans_hdr = bytearray(64)
        struct.pack_into("<II", trans_hdr, 0, 1200, len(iotc_pkt))
        with self._send_lock:
            sock.sendall(bytes(trans_hdr) + iotc_pkt)
        return req_id

    def _recv_iotc_packet(
        self,
        sock: socket.socket,
        header_timeout: float = 1.2,
        stop_event: Optional[threading.Event] = None,
    ) -> Tuple[int, int, int, bytes]:
        """
        Read one incoming 16-byte IOTC header + payload.
        Returns `(iotc_cmd, sec_type, pkt_sid, payload_bytes)`.
        Raises `socket.timeout` if no packet arrives within `header_timeout` so callers
        can service periodic keepalives cleanly.
        """
        hdr = self._read_exact(sock, 16, timeout=header_timeout, stop_event=stop_event)
        if len(hdr) == 0:
            raise EOFError("Relay socket closed")
        if len(hdr) < 16:
            raise EOFError("Incomplete IOTC header")

        iotc_cmd = struct.unpack_from("<I", hdr, 0)[0]
        sec_type = hdr[5]
        pkt_sid = hdr[6]
        plen = struct.unpack_from("<I", hdr, 12)[0]
        if plen > 16 * 1024 * 1024:
            raise RuntimeError(f"IOTC packet exceeds 16 MB limit ({plen})")
        payload = b""
        if plen > 0:
            payload = self._read_exact(sock, plen, timeout=10.0, stop_event=stop_event)
            if len(payload) < plen:
                raise EOFError("Relay socket closed mid-payload")
        return iotc_cmd, sec_type, pkt_sid, payload

    @classmethod
    def decrypt_iotc_json(
        cls, sec_type: int, payload: bytes, aes_key: bytes, aes_iv: bytes
    ) -> Dict[str, Any]:
        if not payload:
            return {}
        if sec_type == 0:
            raw = payload.rstrip(b"\x00")
        elif sec_type == 1:
            raw = cls._pkcs7_unpad(AES.new(aes_key, AES.MODE_ECB).decrypt(payload), 16)
        elif sec_type == 2:
            raw = cls._pkcs7_unpad(AES.new(aes_key, AES.MODE_CBC, iv=aes_iv).decrypt(payload), 16)
        else:
            raise ValueError(f"Unsupported IOTC JSON secure_type={sec_type}")
        obj = json.loads(raw.decode("utf-8"))
        if not isinstance(obj, dict):
            raise ValueError("IOTC JSON payload is not an object")
        return obj

    @staticmethod
    def decrypt_iotc_media(
        sec_type: int, payload: bytes, aes_key: bytes, aes_iv: bytes
    ) -> Optional[Tuple[int, int, int, int, int, bytes]]:
        """
        Decrypt 32-byte HSC media header + selective AES blocks with continuing cipher context.
        Returns `(quality, mtype, seq, ts_ms, stream_idx, media_bytes)` or `None` if malformed.
        """
        if len(payload) < 32:
            return None

        if sec_type == 0:
            decryptor = None
            hdr = payload[:32]
        elif sec_type == 1:
            decryptor = AES.new(aes_key, AES.MODE_ECB)
            hdr = decryptor.decrypt(payload[:32])
        elif sec_type == 2:
            decryptor = AES.new(aes_key, AES.MODE_CBC, iv=aes_iv)
            hdr = decryptor.decrypt(payload[:32])
        else:
            return None

        (
            quality,
            mtype,
            seq,
            length,
            ts_ms,
            enc_offset,
            block_align,
            skip,
            stream_idx,
            _reserved,
        ) = struct.unpack_from("<BBIIqIIIBB", hdr, 0)

        tail_len = len(payload) - 32
        if length > tail_len:
            return None
        if block_align > 0 and (block_align % 16) != 0:
            return None

        data = bytearray(payload[32:])
        if decryptor is not None and block_align > 0 and length > 0:
            if mtype == 4:
                # Audio: decrypt initial min(block_align, length) region (16-byte aligned)
                enc_len = (min(block_align, length) // 16) * 16
                if enc_len > 0:
                    data[:enc_len] = decryptor.decrypt(bytes(data[:enc_len]))
            else:
                # Video: selective stride decryption starting at enc_offset with continuing CBC state
                pos = min(enc_offset, length)
                while pos + block_align <= length:
                    data[pos : pos + block_align] = decryptor.decrypt(
                        bytes(data[pos : pos + block_align])
                    )
                    pos += block_align + skip

        return int(quality), int(mtype), int(seq), int(ts_ms), int(stream_idx), bytes(data[:length])

    def _update_media_attrs(self, obj: Dict[str, Any]) -> None:
        """Update video/audio codec & sample-rate attributes from `0x30204` or `0x30224`."""
        containers = [obj]
        for k in ("params", "data", "result"):
            sub = obj.get(k)
            if isinstance(sub, dict):
                containers.append(sub)
        for c in containers:
            vattr = c.get("vattr")
            if isinstance(vattr, dict):
                vc = vattr.get("codec")
                if vc == 5:
                    self._video_codec_hint = VideoCodec.H265
                elif vc in (1, 4):
                    self._video_codec_hint = VideoCodec.H264
                w = vattr.get("width") or vattr.get("w")
                h = vattr.get("height") or vattr.get("h")
                if isinstance(w, int) and 160 <= w <= 8192:
                    self.frame_width = w
                if isinstance(h, int) and 120 <= h <= 8192:
                    self.frame_height = h
            aattr = c.get("aattr")
            if isinstance(aattr, dict):
                ac = aattr.get("codec")
                if isinstance(ac, int):
                    self._audio_codec_id = ac
                sr = aattr.get("samplerate") or aattr.get("sample_rate") or aattr.get("rate")
                if isinstance(sr, int) and 4000 <= sr <= 48000:
                    self._audio_sample_rate = sr

    @staticmethod
    def _inspect_annexb_video(payload: bytes, hint: VideoCodec) -> Optional[Tuple[bytes, VideoCodec, bool]]:
        """Normalize Annex-B start codes and inspect NAL headers for ground-truth codec & keyframe."""
        idx3 = payload.find(b"\x00\x00\x01")
        if idx3 < 0:
            return None
        start = idx3 - 1 if (idx3 > 0 and payload[idx3 - 1] == 0) else idx3
        norm = (b"\x00" + payload[start:]) if payload[start : start + 3] == b"\x00\x00\x01" else payload[start:]
        if len(norm) < 5:
            return None

        detected_codec = VideoCodec.UNKNOWN
        has_h264_key = False
        has_h265_key = False
        pos = 0
        scanned = 0
        n = len(norm)
        while pos < n - 4 and scanned < 10:
            sc = norm.find(b"\x00\x00\x01", pos)
            if sc < 0:
                break
            nal_off = sc + 3
            if nal_off >= n:
                break
            nal_b0 = norm[nal_off]
            nal_b1 = norm[nal_off + 1] if (nal_off + 1) < n else 0
            h265_t = (nal_b0 >> 1) & 0x3F
            h264_t = nal_b0 & 0x1F

            if (nal_b0 & 0x80) == 0 and nal_b1 == 0x01 and h265_t in (1, 19, 20, 21, 32, 33, 34):
                if detected_codec == VideoCodec.UNKNOWN:
                    detected_codec = VideoCodec.H265
                if h265_t in (19, 20, 21, 32, 33, 34):
                    has_h265_key = True
            elif (nal_b0 & 0x80) == 0 and 0 < h264_t < 24:
                if detected_codec == VideoCodec.UNKNOWN:
                    detected_codec = VideoCodec.H264
                if h264_t in (5, 7, 8):
                    has_h264_key = True

            scanned += 1
            pos = nal_off + 1

        final_codec = detected_codec if detected_codec != VideoCodec.UNKNOWN else (
            hint if hint != VideoCodec.UNKNOWN else VideoCodec.H265
        )
        is_key = has_h265_key if final_codec == VideoCodec.H265 else has_h264_key
        return norm, final_codec, is_key

    def _convert_hsc_audio_to_frame(self, seq: int, ts_ms: int, media_bytes: bytes) -> Optional[CameraFrame]:
        if not media_bytes:
            return None
        # Check for ADTS AAC header just in case
        if len(media_bytes) >= 7 and media_bytes[0] == 0xFF and (media_bytes[1] & 0xF0) == 0xF0:
            return CameraFrame(
                is_audio=True,
                raw_type=24,
                payload=media_bytes,
                frame_id=seq,
                timestamp=ts_ms,
                audio_codec="aac",
            )
        if self._audio_codec_id == 100:
            pcm = media_bytes
        else:
            pcm = decode_adpcm_to_pcm16(media_bytes)
        if not pcm:
            return None
        if self._audio_sample_rate >= 14000:
            pcm = downsample_pcm16_2x(pcm)
        if not pcm:
            return None
        return CameraFrame(
            is_audio=True,
            raw_type=21,
            payload=pcm,
            frame_id=seq,
            timestamp=ts_ms,
            audio_codec="pcm16",
        )

    # --- Main Live Stream Loop ---
    def run_forever(self) -> None:
        backoff = 2.0
        force_key_refresh = False

        while not self._stop_event.is_set():
            self._need_reconnect = False
            self._locked_video_stream = None
            if self._force_cloud_wake:
                self._force_cloud_wake = False
                force_key_refresh = True
                self._wake_lan_udp(burst_count=4)

            if not self._resolve_device_id_if_missing():
                self._emit_status("Cannot find HSC Device ID — enter 9-digit Device ID in Settings")
                self._stop_event.wait(4.0)
                continue

            if not self._has_app_credentials():
                self._emit_status(
                    "Invalid / missing V380 Pro login — enter V380 Pro account & password in Settings"
                )
                self._stop_event.wait(4.0)
                continue

            sock: Optional[socket.socket] = None
            try:
                token, send_sec, sid, aes_key, aes_iv = self._ensure_hsc_key_session(
                    force_refresh=force_key_refresh
                )
                force_key_refresh = False

                self._emit_status("Connecting to V380 HSC live channel...")
                sock = self._open_relay_channel(token, call_type=2)
                self._stream_sock = sock

                req_q = 1 if self.stream_quality else 0
                tried_alt_q = False
                tried_alt_ike0_sec = False
                live_req_id = self._send_iotc_json(
                    sock,
                    self.METHOD_LIVE_START,
                    {
                        "up": {
                            "quality": req_q,
                            "video": 1,
                            "audio": 1 if self.enable_audio else 0,
                            "IDRreq": 1,
                        }
                    },
                    sid=sid,
                    aes_key=aes_key,
                    aes_iv=aes_iv,
                    sec_type=send_sec,
                )
                self._last_keepalive_mono = time.monotonic()
                live_started_mono = time.monotonic()
                emitted_streaming = False
                video_count = 0
                backoff = 2.0

                while not self._stop_event.is_set() and not self._need_reconnect:
                    now_m = time.monotonic()
                    if (now_m - self._last_keepalive_mono) >= 3.8:
                        self._send_iotc_json(
                            sock,
                            self.METHOD_KEEPALIVE,
                            {"flag": 2, "tms1": 0, "decode_tms": 0},
                            sid=sid,
                            aes_key=aes_key,
                            aes_iv=aes_iv,
                            sec_type=send_sec,
                        )
                        self._last_keepalive_mono = now_m

                    if (
                        video_count == 0
                        and send_sec in (0, 1)
                        and len(self._rk_bytes) == 16
                        and not tried_alt_ike0_sec
                        and (now_m - live_started_mono) >= 2.2
                    ):
                        tried_alt_ike0_sec = True
                        send_sec = 0 if send_sec == 1 else 1
                        self._sec_type = send_sec
                        live_req_id = self._send_iotc_json(
                            sock,
                            self.METHOD_LIVE_START,
                            {
                                "up": {
                                    "quality": req_q,
                                    "video": 1,
                                    "audio": 1 if self.enable_audio else 0,
                                    "IDRreq": 1,
                                }
                            },
                            sid=sid,
                            aes_key=aes_key,
                            aes_iv=aes_iv,
                            sec_type=send_sec,
                        )

                    if video_count == 0 and not tried_alt_q and (now_m - live_started_mono) >= 4.5:
                        tried_alt_q = True
                        alt_q = 1 - req_q
                        live_req_id = self._send_iotc_json(
                            sock,
                            self.METHOD_LIVE_START,
                            {
                                "up": {
                                    "quality": alt_q,
                                    "video": 1,
                                    "audio": 1 if self.enable_audio else 0,
                                    "IDRreq": 1,
                                }
                            },
                            sid=sid,
                            aes_key=aes_key,
                            aes_iv=aes_iv,
                            sec_type=send_sec,
                        )

                    try:
                        iotc_cmd, pkt_sec, pkt_sid, payload = self._recv_iotc_packet(sock, header_timeout=1.2)
                    except socket.timeout:
                        continue

                    if send_sec == 2 and pkt_sid not in (0, sid & 0xFF):
                        continue

                    if send_sec in (0, 1) and pkt_sec in (0, 1) and pkt_sec != send_sec:
                        send_sec = pkt_sec
                        self._sec_type = pkt_sec
                        with self._GLOBAL_LOCK:
                            sess_ref = self._DEVICE_SESSIONS.get(self.device_id)
                            if sess_ref is not None:
                                sess_ref["ike0_sec_type"] = pkt_sec

                    if iotc_cmd in (self.IOTC_CMD_JSON_REQ, self.IOTC_CMD_JSON_PUSH):
                        try:
                            jobj = self.decrypt_iotc_json(pkt_sec, payload, aes_key, aes_iv)
                        except Exception:
                            continue
                        res_obj = jobj.get("result") if isinstance(jobj.get("result"), dict) else {}
                        rcode = res_obj.get("code")
                        if rcode == 1104:
                            self._invalidate_hsc_key_session(clear_meta=True)
                            force_key_refresh = True
                            break
                        mid = jobj.get("method_id")
                        if mid == self.METHOD_MEDIA_INFO or "vattr" in jobj or "vattr" in (jobj.get("params") or {}):
                            self._update_media_attrs(jobj)
                        if jobj.get("id") == live_req_id and rcode not in (None, 1000) and not tried_alt_q:
                            tried_alt_q = True
                            alt_q = 1 - req_q
                            live_req_id = self._send_iotc_json(
                                sock,
                                self.METHOD_LIVE_START,
                                {
                                    "up": {
                                        "quality": alt_q,
                                        "video": 1,
                                        "audio": 1 if self.enable_audio else 0,
                                        "IDRreq": 1,
                                    }
                                },
                                sid=sid,
                                aes_key=aes_key,
                                aes_iv=aes_iv,
                                sec_type=send_sec,
                            )

                    elif iotc_cmd == self.IOTC_CMD_MEDIA:
                        parsed_media = self.decrypt_iotc_media(pkt_sec, payload, aes_key, aes_iv)
                        if parsed_media is None:
                            continue
                        _q, mtype, seq, ts_ms, stream_idx, media_bytes = parsed_media
                        if mtype == 4:
                            if self.enable_audio and self.on_frame:
                                aframe = self._convert_hsc_audio_to_frame(seq, ts_ms, media_bytes)
                                if aframe is not None:
                                    self.on_frame(aframe)
                        else:
                            if self._locked_video_stream is None:
                                self._locked_video_stream = stream_idx
                            elif stream_idx != self._locked_video_stream:
                                continue

                            inspected = self._inspect_annexb_video(media_bytes, self._video_codec_hint)
                            if inspected is None:
                                continue
                            norm_nal, vcodec, is_key = inspected
                            self._video_codec_hint = vcodec
                            self._last_video_frame_mono = time.monotonic()
                            video_count += 1
                            if not emitted_streaming:
                                emitted_streaming = True
                                self._emit_status(
                                    f"Streaming {'HD' if self.stream_quality else 'SD'} (V380 HSC)"
                                )
                            if self.on_frame:
                                self.on_frame(
                                    CameraFrame(
                                        is_audio=False,
                                        raw_type=0x28 if vcodec == VideoCodec.H265 else 0x00,
                                        payload=norm_nal,
                                        codec=vcodec,
                                        is_keyframe=is_key,
                                        frame_id=seq,
                                        frame_type=0 if is_key else 1,
                                        frame_rate=20,
                                        timestamp=ts_ms,
                                    )
                                )

            except _HscAuthError as auth_err:
                self._emit_status(str(auth_err))
                self._close_stream_sock()
                self._stop_event.wait(5.0)
            except InterruptedError:
                break
            except Exception as exc:
                self._close_stream_sock()
                if self._stop_event.is_set():
                    break
                if not self._need_reconnect:
                    err_text = str(exc)
                    if err_text.startswith("Camera "):
                        self._emit_status(err_text)
                    else:
                        self._emit_status(f"HSC connection error ({err_text}) — retrying...")
                    self._stop_event.wait(backoff)
                    backoff = min(8.0, backoff * 1.4)
            finally:
                self._close_stream_sock()

    # --- PTZ, White Light & Night Vision Controls (`0x30308` / `0x30302`) ---
    def _send_live_iotc_control(self, method_id: int, params: Dict[str, Any]) -> bool:
        sock = self._stream_sock
        if sock is None or (self._sid <= 0 and self._sec_type not in (0, 1)):
            return False
        try:
            self._send_iotc_json(sock, method_id, params)
            return True
        except Exception:
            return False

    def _ptz_op(self, op_code: int) -> bool:
        return self._send_live_iotc_control(
            self.METHOD_ACTION,
            {"daid": self.DAID_PTZ, "vsbmp": 1, "in": {"op": int(op_code), "cnt": 1}},
        )

    def ptz_stop(self) -> bool:
        return self._ptz_op(0)

    def ptz_left(self) -> bool:
        return self._ptz_op(1)

    def ptz_right(self) -> bool:
        return self._ptz_op(2)

    def ptz_up(self) -> bool:
        return self._ptz_op(3)

    def ptz_down(self) -> bool:
        return self._ptz_op(4)

    def light_auto(self) -> bool:
        return self._send_live_iotc_control(
            self.METHOD_PROP_WRITE, {"dplist": [{"dpid": self.DPID_WHITE_LIGHT, "value": 0}]}
        )

    def light_on(self) -> bool:
        return self._send_live_iotc_control(
            self.METHOD_PROP_WRITE, {"dplist": [{"dpid": self.DPID_WHITE_LIGHT, "value": 1}]}
        )

    def light_off(self) -> bool:
        return self._send_live_iotc_control(
            self.METHOD_PROP_WRITE, {"dplist": [{"dpid": self.DPID_WHITE_LIGHT, "value": 2}]}
        )

    def image_auto(self) -> bool:
        return self._send_live_iotc_control(
            self.METHOD_PROP_WRITE, {"dplist": [{"dpid": self.DPID_NIGHT_VISION, "value": 0}]}
        )

    def image_color(self) -> bool:
        return self._send_live_iotc_control(
            self.METHOD_PROP_WRITE, {"dplist": [{"dpid": self.DPID_NIGHT_VISION, "value": 1}]}
        )

    def image_bw(self) -> bool:
        return self._send_live_iotc_control(
            self.METHOD_PROP_WRITE, {"dplist": [{"dpid": self.DPID_NIGHT_VISION, "value": 2}]}
        )

    def image_flip(self) -> bool:
        return True

    def ptz_preset_set(self, slot_id: int) -> bool:
        return False

    def ptz_preset_goto(self, slot_id: int) -> bool:
        return False

    def ptz_preset_clear(self, slot_id: int) -> bool:
        return False

    # --- microSD Card Recording List (`call=3`, `0x30220`) & Playback (`call=5`, `0x30222`) ---
    @staticmethod
    def _local_day_utc_bounds_ms(year: int, month: int, day: int) -> Tuple[int, int, Any]:
        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        start_local = datetime(int(year), int(month), int(day), 0, 0, 0, tzinfo=local_tz)
        end_local = datetime(int(year), int(month), int(day), 23, 59, 59, 999000, tzinfo=local_tz)
        return int(start_local.timestamp() * 1000), int(end_local.timestamp() * 1000), local_tz

    @staticmethod
    def _utc_ms_to_wall_ts(utc_ms: int, local_tz: Any) -> int:
        dt_loc = datetime.fromtimestamp(utc_ms / 1000.0, tz=local_tz)
        return int(dt_loc.replace(tzinfo=timezone.utc).timestamp())

    @staticmethod
    def _wall_ts_to_utc_ms(wall_ts: int, local_tz: Any) -> int:
        dt_wall = datetime.fromtimestamp(int(wall_ts), tz=timezone.utc)
        dt_loc = datetime(
            dt_wall.year,
            dt_wall.month,
            dt_wall.day,
            dt_wall.hour,
            dt_wall.minute,
            dt_wall.second,
            tzinfo=local_tz,
        )
        return int(dt_loc.timestamp() * 1000)

    def list_sd_recordings(
        self, year: int, month: int, day: int
    ) -> Tuple[bool, str, List[RecordingSegment]]:
        if not self._resolve_device_id_if_missing():
            return False, "Missing HSC Device ID", []
        if not self._has_app_credentials():
            return False, "Enter V380 Pro account & password in Camera Settings", []

        start_ms, end_ms, local_tz = self._local_day_utc_bounds_ms(year, month, day)
        last_err = "HSC SD card list failed"

        for attempt in range(2):
            if self._stop_event.is_set():
                return False, "Cancelled", []
            sock: Optional[socket.socket] = None
            try:
                token, send_sec, sid, aes_key, aes_iv = self._ensure_hsc_key_session(force_refresh=(attempt > 0))
                sock = self._open_relay_channel(token, call_type=3)
                self._stream_sock = sock

                self._send_iotc_json(
                    sock,
                    self.METHOD_SD_LIST,
                    {"filter": 11, "mode": 2, "start": start_ms, "end": end_ms},
                    sid=sid,
                    aes_key=aes_key,
                    aes_iv=aes_iv,
                    sec_type=send_sec,
                )

                segments: List[RecordingSegment] = []
                seen_sts: set[int] = set()
                deadline = time.monotonic() + 14.0

                while time.monotonic() < deadline and not self._stop_event.is_set():
                    try:
                        iotc_cmd, pkt_sec, pkt_sid, payload = self._recv_iotc_packet(sock, header_timeout=2.5)
                    except socket.timeout:
                        break
                    if send_sec == 2 and pkt_sid not in (0, sid & 0xFF):
                        continue
                    if iotc_cmd not in (self.IOTC_CMD_JSON_REQ, self.IOTC_CMD_JSON_PUSH):
                        continue

                    jobj = self.decrypt_iotc_json(pkt_sec, payload, aes_key, aes_iv)
                    res_obj = jobj.get("result") if isinstance(jobj.get("result"), dict) else {}
                    rcode = res_obj.get("code")
                    if rcode == 1104:
                        self._invalidate_hsc_key_session(clear_meta=True)
                        last_err = "HSC session expired (1104)"
                        break

                    data_obj = jobj.get("data") if isinstance(jobj.get("data"), dict) else (
                        jobj.get("params") if isinstance(jobj.get("params"), dict) else jobj
                    )
                    rows = data_obj.get("list")
                    if isinstance(rows, list):
                        for item in rows:
                            if not isinstance(item, dict):
                                continue
                            s_off = int(item.get("s") or 0)
                            dur_ms = int(item.get("d") or 0)
                            t_val = int(item.get("t") or 1)
                            if dur_ms <= 0:
                                continue
                            sts_ms = start_ms + s_off
                            if sts_ms in seen_sts:
                                continue
                            seen_sts.add(sts_ms)

                            rtype = 2 if t_val == 2 else 1
                            w_start = self._utc_ms_to_wall_ts(sts_ms, local_tz)
                            w_end = w_start + max(1, int(round(dur_ms / 1000.0)))
                            seg_id = int((sts_ms // 1000) & 0x7FFFFFFF) or 1
                            segments.append(
                                RecordingSegment(
                                    seg_id=seg_id,
                                    raw_type=rtype,
                                    start_ts=w_start,
                                    end_ts=w_end,
                                    hsc_sts_ms=sts_ms,
                                )
                            )

                    flag = data_obj.get("flag")
                    if flag == 0 or (rcode not in (None, 1000) and not rows):
                        segments.sort(key=lambda s: s.start_ts)
                        if not segments:
                            return True, "No recordings found for this date", []
                        anom = sum(1 for s in segments if s.is_anomaly)
                        summary = f"{len(segments)} clips ({anom} events)" if anom else f"{len(segments)} clips"
                        return True, summary, segments

                if last_err == "HSC session expired (1104)" and attempt == 0:
                    continue

                segments.sort(key=lambda s: s.start_ts)
                if segments:
                    anom = sum(1 for s in segments if s.is_anomaly)
                    summary = f"{len(segments)} clips ({anom} events)" if anom else f"{len(segments)} clips"
                    return True, summary, segments
                return True, "No recordings found for this date", []

            except _HscAuthError as ae:
                return False, str(ae), []
            except Exception as exc:
                if self._stop_event.is_set():
                    return False, "Cancelled", []
                last_err = str(exc) if str(exc).startswith("Camera ") else f"HSC SD read error: {exc}"
            finally:
                self._close_stream_sock()

        return False, last_err, []

    def stream_sd_segment(
        self,
        segment: RecordingSegment,
        start_ts: Optional[int] = None,
        stop_event: Optional[threading.Event] = None,
        on_frame: Optional[Callable[[CameraFrame], None]] = None,
    ) -> Tuple[bool, str]:
        cb = on_frame or self.on_frame
        if cb is None:
            return False, "No frame callback registered"
        if not self._resolve_device_id_if_missing():
            return False, "Missing HSC Device ID"
        if not self._has_app_credentials():
            return False, "Enter V380 Pro account & password in Camera Settings"

        def is_stopped() -> bool:
            return self._stop_event.is_set() or (stop_event is not None and stop_event.is_set())

        local_tz = datetime.now().astimezone().tzinfo or timezone.utc
        if getattr(segment, "hsc_sts_ms", 0) > 0:
            sts_ms = int(segment.hsc_sts_ms)
        else:
            sts_ms = self._wall_ts_to_utc_ms(segment.start_ts, local_tz)

        from_wall = int(start_ts if start_ts is not None else segment.start_ts)
        seek_sec = max(0, min(from_wall - int(segment.start_ts), max(0, segment.duration_sec - 1)))
        pts_ms = sts_ms + (seek_sec * 1000) + 1
        wall_offset_ms = (int(segment.start_ts) * 1000) - sts_ms

        last_err = "HSC SD playback failed"
        for attempt in range(2):
            if is_stopped():
                return False, "Cancelled"
            sock: Optional[socket.socket] = None
            received_any_media = False
            finished_cleanly = False
            try:
                token, send_sec, sid, aes_key, aes_iv = self._ensure_hsc_key_session(force_refresh=(attempt > 0))
                sock = self._open_relay_channel(token, call_type=5)
                self._stream_sock = sock
                self._locked_video_stream = None

                self._send_iotc_json(
                    sock,
                    self.METHOD_SD_REPLAY,
                    {
                        "sts": sts_ms,
                        "pts": pts_ms,
                        "time_id": 0,
                        "frames": 100,
                        "speed": 10,
                        "mode": 2,
                        "video": 1,
                        "audio": 1 if self.enable_audio else 0,
                        "rtype": 1,
                        "vsspec": 1,
                    },
                    sid=sid,
                    aes_key=aes_key,
                    aes_iv=aes_iv,
                    sec_type=send_sec,
                )

                window_packets = 0
                session_expired_before_media = False

                while not is_stopped():
                    try:
                        iotc_cmd, pkt_sec, pkt_sid, payload = self._recv_iotc_packet(
                            sock, header_timeout=5.0, stop_event=stop_event
                        )
                    except socket.timeout:
                        break

                    if send_sec == 2 and pkt_sid not in (0, sid & 0xFF):
                        continue

                    if send_sec in (0, 1) and pkt_sec in (0, 1) and pkt_sec != send_sec:
                        send_sec = pkt_sec
                        self._sec_type = pkt_sec

                    if iotc_cmd in (self.IOTC_CMD_JSON_REQ, self.IOTC_CMD_JSON_PUSH):
                        try:
                            jobj = self.decrypt_iotc_json(pkt_sec, payload, aes_key, aes_iv)
                        except Exception:
                            continue
                        res_obj = jobj.get("result") if isinstance(jobj.get("result"), dict) else {}
                        if res_obj.get("code") == 1104:
                            self._invalidate_hsc_key_session(clear_meta=True)
                            if not received_any_media:
                                session_expired_before_media = True
                            break

                        mid = jobj.get("method_id")
                        p_obj = jobj.get("params") if isinstance(jobj.get("params"), dict) else (
                            jobj.get("data") if isinstance(jobj.get("data"), dict) else jobj
                        )
                        if mid == self.METHOD_SD_INFO or "info_id" in p_obj:
                            info_id = p_obj.get("info_id")
                            if info_id == 1:
                                self._update_media_attrs(jobj)
                            elif info_id == 3:
                                finished_cleanly = True
                                return True, "Segment finished"

                    elif iotc_cmd == self.IOTC_CMD_MEDIA:
                        parsed_media = self.decrypt_iotc_media(pkt_sec, payload, aes_key, aes_iv)
                        if parsed_media is None:
                            continue
                        _q, mtype, seq, ts_ms, stream_idx, media_bytes = parsed_media
                        received_any_media = True
                        window_packets += 1
                        if window_packets >= 90 and not is_stopped():
                            window_packets = 0
                            self._send_iotc_json(
                                sock,
                                self.METHOD_SD_REPLAY,
                                {"frames": 100},
                                sid=sid,
                                aes_key=aes_key,
                                aes_iv=aes_iv,
                                sec_type=send_sec,
                            )

                        wall_ts_ms = ts_ms + wall_offset_ms if ts_ms > 0 else 0
                        if mtype == 4:
                            if self.enable_audio:
                                aframe = self._convert_hsc_audio_to_frame(seq, wall_ts_ms, media_bytes)
                                if aframe is not None:
                                    cb(aframe)
                        else:
                            if self._locked_video_stream is None:
                                self._locked_video_stream = stream_idx
                            elif stream_idx != self._locked_video_stream:
                                continue
                            inspected = self._inspect_annexb_video(media_bytes, self._video_codec_hint)
                            if inspected is None:
                                continue
                            norm_nal, vcodec, is_key = inspected
                            self._video_codec_hint = vcodec
                            cb(
                                CameraFrame(
                                    is_audio=False,
                                    raw_type=0x28 if vcodec == VideoCodec.H265 else 0x00,
                                    payload=norm_nal,
                                    codec=vcodec,
                                    is_keyframe=is_key,
                                    frame_id=seq,
                                    frame_type=0 if is_key else 1,
                                    frame_rate=20,
                                    timestamp=wall_ts_ms,
                                )
                            )

                if session_expired_before_media and attempt == 0:
                    continue
                return True, "Stopped"

            except _HscAuthError as ae:
                return False, str(ae)
            except Exception as exc:
                if is_stopped():
                    return True, "Stopped"
                last_err = str(exc) if str(exc).startswith("Camera ") else f"HSC playback error: {exc}"
                break
            finally:
                if sock is not None and not finished_cleanly:
                    try:
                        sock.settimeout(0.2)
                        self._send_iotc_json(sock, self.METHOD_SD_STOP, {}, sec_type=send_sec)
                    except Exception:
                        pass
                self._close_stream_sock()

        return False, last_err
