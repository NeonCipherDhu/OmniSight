"""
Native V380 / V380 Pro LAN Protocol Implementation (Port 8800 & UDP 10008/10009).

Supports:
- UDP Broadcast LAN Auto-Discovery (port 10008/10009 `NVDEVSEARCH^100`)
- Active TCP Port 8800 Subnet Scanner fallback (including 192.168.1.x & local subnets)
- V380 Dual-Stage AES-128-ECB Password Encryption (`macrovideo+*#!^@`)
- Auth Handshake (`cmd 1167` -> `resp 1168`, both v31/v32 and legacy v2 variants, with auto-username/port fallback)
- Stream Login & Start (`cmd 301` -> `resp 401` -> `cmd 303`)
- Media Frame Reassembly (`0x7F` 12-byte fragment headers)
- Firmware v31/v32+ Media Decryption (16-byte outer header + 80-byte AES-128-ECB video stride & audio decryption)
- Automatic H.264 (`0x00`/`0x01`) and H.265/HEVC (`0x28`/`0x29`) Annex-B NAL extraction
- Audio extraction for both AAC-LC ADTS (`0x18`) and IMA-ADPCM (`0x16`/`0x1A`)
- PTZ (Pan/Tilt/Zoom), IR/White Light, and Image Flip/Color controls
"""

from __future__ import annotations

import hashlib
import ipaddress
import json
import random
import socket
import string
import struct
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, List, Optional, Tuple

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

    def display_title(self) -> str:
        label = self.name or (f"V380 ({self.dev_id})" if self.dev_id else f"V380 @ {self.ip}")
        return f"{label} [{self.ip}:{self.port}]"


@dataclass
class V380Frame:
    is_audio: bool
    raw_type: int
    payload: bytes
    codec: VideoCodec = VideoCodec.UNKNOWN
    is_keyframe: bool = False
    frame_id: int = 0
    frame_type: int = 0
    frame_rate: int = 20
    timestamp: int = 0
    audio_codec: str = "adpcm"  # "adpcm" | "aac"


@dataclass
class V380RecordingSegment:
    """
    Represents a single recording clip stored on a V380 camera's microSD card
    (returned by Macrovideo SDK 6.0 Segment Search `cmd 361` / `cmd 362`).
    """

    seg_id: int
    raw_type: int  # 1 = Continuous/Normal, 2 = Motion, 3 = Anomaly/Alarm
    start_ts: int  # Camera local wall-clock seconds stored as UTC epoch seconds
    end_ts: int    # Camera local wall-clock seconds stored as UTC epoch seconds

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


class V380Commands:
    """Binary control packets sent over the active V380 stream socket."""

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


# IMA-ADPCM step and index tables used by V380 audio (8000 Hz, 16-bit mono)
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


def decode_v380_alaw_to_pcm16(alaw_bytes: bytes) -> bytes:
    """
    Decode V380 0x1A G.711 A-Law (PCMA, 8000 Hz, mono) audio payload into signed 16-bit LE PCM.
    """
    if not alaw_bytes:
        return b""
    out = bytearray(len(alaw_bytes) * 2)
    for idx, b in enumerate(alaw_bytes):
        struct.pack_into("<h", out, idx * 2, _ALAW_TO_PCM16[b])
    return bytes(out)


def decode_v380_adpcm_to_pcm16(adpcm_bytes: bytes) -> bytes:
    """
    Decode V380 IMA-ADPCM audio payload into signed 16-bit little-endian PCM (8000 Hz, mono).
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


class V380Discovery:
    """
    Discovers V380 cameras on the local network (including un-added cameras across subnets) via:
    1. Native UDP broadcast (`NVDEVSEARCH^100` to port 10008, receiving `NVDEVRESULT` on 10009)
    2. Two-pass paced Unicast UDP `NVDEVSEARCH^100` sweep across local & neighbor subnets
       (wakes idle/un-added Wi-Fi cameras from DTIM sleep, resolves router ARP without overflowing
       `neigh_table`, and works across routers that block broadcast packets between 192.168.0.x & 192.168.1.x)
    3. Multi-subnet TCP port 8800 scanner fallback with targeted UDP `NVDEVSEARCH^100` ID resolution
    """

    SEARCH_CMD = b"NVDEVSEARCH^100"

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
    def query_single_ip(cls, target_ip: str, timeout_sec: float = 0.8) -> Optional[DiscoveredCamera]:
        """Send targeted unicast UDP `NVDEVSEARCH^100` to a single IP and wait for `NVDEVRESULT`."""
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        cls._configure_udp_socket(sock)
        try:
            try:
                sock.bind(("0.0.0.0", 10009))
            except OSError:
                sock.bind(("0.0.0.0", 0))
            sock.settimeout(0.05)

            deadline = time.monotonic() + max(0.2, timeout_sec)
            next_send = 0.0
            while time.monotonic() < deadline:
                now = time.monotonic()
                if now >= next_send:
                    try:
                        sock.sendto(cls.SEARCH_CMD, (target_ip, 10008))
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
                progress_cb("Sending V380 UDP broadcast (port 10008/10009)...")
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
                _drain_replies(max(0.25, timeout_sec * 0.18), "UDP Unicast Scan")
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
                            name=f"V380 Camera ({ip})",
                            discovery_method="TCP Port 8800 Scan",
                        )

        return sorted(devices_by_ip.values(), key=lambda c: c.ip)

    @staticmethod
    def _parse_nvdevresult(
        raw: bytes, source_ip: str, method: str = "UDP Broadcast"
    ) -> Optional[DiscoveredCamera]:
        text = raw.decode("ascii", errors="ignore").strip("\x00\r\n ")
        parts = text.split("^")
        if len(parts) < 13 or parts[0] != "NVDEVRESULT":
            return None

        mac = parts[2].strip().lower()
        ip = parts[3].strip() or source_ip
        dev_id = parts[12].strip()
        return DiscoveredCamera(
            dev_id=dev_id,
            ip=ip,
            mac=mac,
            port=8800,
            name=f"V380 ({dev_id})" if dev_id else f"V380 ({ip})",
            discovery_method=method,
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
                        # Always send a 520-byte V380 handshake frame so the camera's
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


class V380Client:
    """
    Native TCP Port 8800 V380 Camera Client.
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
        on_frame: Optional[Callable[[V380Frame], None]] = None,
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
        self._quality_switch_pending = False

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
        Encrypt user password using V380's dual-stage AES-128-ECB scheme:
        Stage 1: AES-ECB encrypt with `macrovideo+*#!^@`
        Stage 2: AES-ECB encrypt with random 16-char printable key
        Output: 16-byte random key + encrypted password blocks
        """
        chars = string.ascii_letters + string.digits
        rk = "".join(random.choice(chars) for _ in range(16)).encode("ascii")

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
        single = V380Discovery.query_single_ip(self.ip, timeout_sec=0.9)
        if single and single.dev_id.isdigit():
            self.device_id = int(single.dev_id)
            self._emit_status(f"Found Device ID: {self.device_id}")
            return True
        devices = V380Discovery.discover(timeout_sec=1.5, scan_tcp_8800=False)
        for dev in devices:
            if dev.ip == self.ip and dev.dev_id.isdigit():
                self.device_id = int(dev.dev_id)
                self._emit_status(f"Found Device ID: {self.device_id}")
                return True
        return False

    def _wake_camera_port_8800(self) -> None:
        """
        Wake V380 cameras (especially v31/v32 firmware or cross-subnet cameras) whose
        local TCP port 8800 listener goes into standby until triggered by:
        1. Unicast UDP `NVDEVSEARCH^100` to <ip>:10008
        2. V380 Cloud Dispatch (`dispa1.av380.net:8001`) `cmd 1167` wakeup signal
        """
        # 1. Send unicast UDP discovery wakeup to camera IP on port 10008
        # Use an ephemeral local port so background stream workers never steal UDP 10009 replies during scans.
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as usock:
                V380Discovery._configure_udp_socket(usock)
                usock.bind(("0.0.0.0", 0))
                usock.sendto(b"NVDEVSEARCH^100", (self.ip, 10008))
        except Exception:
            pass

        # 2. If we have a Device ID, trigger V380 Cloud Dispatch wakeup so the camera
        # immediately opens its local TCP port 8800 listener
        if self.device_id <= 0:
            return

        relay_ips: List[str] = []
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
            with urllib.request.urlopen(req, timeout=1.8) as resp:
                data = json.loads(resp.read().decode("utf-8", errors="ignore"))
                if data.get("code") == 2000:
                    for item in data.get("data", []):
                        rip = item.get("ip")
                        if rip and rip not in relay_ips:
                            relay_ips.append(rip)
        except Exception:
            pass

        for fallback_ip in ("103.138.75.221", "129.226.139.226", "101.33.35.22"):
            if fallback_ip not in relay_ips:
                relay_ips.append(fallback_ip)

        user_str = self.username if self.username != "admin" else (str(self.device_id) or "admin")
        user_b = user_str.encode("ascii", errors="ignore")[:32]
        host_b = f"{self.device_id}.nvdvr.net".encode("ascii", errors="ignore")[:50]
        enc_pw = self.generate_password(self.password, block_bytes=48)

        cmd = bytearray(520)
        struct.pack_into("<I", cmd, 0, 1167)
        struct.pack_into("<I", cmd, 4, 1022)
        cmd[8] = 31
        struct.pack_into("<I", cmd, 9, 1)
        struct.pack_into("<I", cmd, 13, self.device_id & 0xFFFFFFFF)
        cmd[17 : 17 + len(host_b)] = host_b
        struct.pack_into("<I", cmd, 67, 8800)
        cmd[71 : 71 + len(user_b)] = user_b
        cmd[103 : 103 + len(enc_pw)] = enc_pw

        for rip in relay_ips[:3]:
            if self._stop_event.is_set():
                return
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rsock:
                    self._stream_sock = rsock
                    rsock.settimeout(1.5)
                    rsock.connect((rip, 8800))
                    rsock.sendall(cmd)
                    self._recv_response(rsock, 256, timeout=1.2)
            except Exception:
                continue
            finally:
                if self._stream_sock is rsock:
                    self._stream_sock = None

        # Allow ~0.5s for the cloud wakeup signal to reach the camera and open port 8800
        self._stop_event.wait(0.5)

    def authenticate(self, skip_cloud_wake: bool = False) -> Tuple[int, str]:
        """
        Perform port 8800 authentication (`cmd 1167` -> `resp 1168`).
        Sends the UDP 10008 + V380 Cloud Dispatch wakeup first (unless `skip_cloud_wake`
        is set during a fast HD/SD stream quality switch).
        """
        if self._stop_event.is_set():
            return -1, "Stopped"
        self._resolve_device_id_if_missing()
        if self._stop_event.is_set():
            return -1, "Stopped"
        if not skip_cloud_wake:
            self._wake_camera_port_8800()

        ports_to_try = [self.port] if self.port == 8800 else [self.port, 8800]
        usernames_to_try: List[str] = []
        for u in (self.username, str(self.device_id) if self.device_id else "", "admin"):
            if u and u not in usernames_to_try:
                usernames_to_try.append(u)

        variants = [
            (520, 120, 31, 48),  # Modern V380 Pro LAN (520 bytes, unk1=120, ver=31)
            (256, 1022, 2, 16),  # Classic V380 LAN (256 bytes, unk1=1022, ver=2)
        ]

        last_err = "Connection timed out"
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
                        sock.settimeout(4.0)
                        sock.connect((self.ip, candidate_port))
                        if self._stop_event.is_set():
                            return -1, "Stopped"
                        port_reachable = True

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
                            last_err = "Empty auth response from camera"
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
                        return -1, f"Camera rejected login (code {login_result})"

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

        if "1011" in last_err:
            return -1, last_err
        return 0, last_err

    def stream_login_and_start(self) -> Tuple[bool, str]:
        """Connect stream socket, send `cmd 301`, parse `resp 401`, and send `cmd 303`."""
        self._close_stream_sock()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 262144)
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
                return False, "Failed sending stream login (cmd 301)"

            resp401 = self._recv_response(sock, 412, timeout=10.0)
            if not resp401 or len(resp401) < 16:
                return False, "Stream login response too short"

            resp_cmd = struct.unpack_from("<I", resp401, 0)[0]
            if resp_cmd != 401:
                return False, f"Expected stream login 401, got {resp_cmd}"

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
            cmd303 = bytearray(256)
            struct.pack_into("<I", cmd303, 0, 303)
            if self.device_version > 30:
                struct.pack_into("<H", cmd303, 4, 0x3001)
            else:
                struct.pack_into("<i", cmd303, 4, result if result in (402, 1001) else 0x3001)

            if not self._send_raw(bytes(cmd303)):
                return False, "Failed sending stream start (cmd 303)"

            return True, f"Streaming ({self.frame_width}x{self.frame_height}, {'HD' if self.stream_quality else 'SD'})"

        except Exception as exc:
            self._close_stream_sock()
            return False, f"Stream handshake failed: {exc}"

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
                self._emit_status(f"Auth Error: {auth_msg}")
                break

            if auth_status == 0:
                self._emit_status(f"{auth_msg} — retrying in 3s...")
                self._stop_event.wait(3.0)
                continue

            self._emit_status(auth_msg)
            ok, stream_msg = self.stream_login_and_start()
            if not ok:
                self._emit_status(f"{stream_msg} — retrying in 2s...")
                self._stop_event.wait(2.0)
                continue

            self._emit_status(stream_msg)
            self._receive_frames_loop()

            if self._quality_switch_pending and not self._stop_event.is_set():
                self._quality_switch_pending = False
                fast_reconnect = True
                continue

            if not self._stop_event.is_set():
                self._emit_status("Stream disconnected — reconnecting...")
                self._stop_event.wait(1.5)

        self._close_stream_sock()

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

        while not self._stop_event.is_set() and not self._need_reconnect:
            hdr = self._read_exact(sock, 12, timeout=12.0)
            if len(hdr) < 12:
                break

            lead = hdr[0]
            if lead == 0x6F:
                time.sleep(0.01)
                continue
            if lead != 0x7F:
                continue

            pkt_type = hdr[1]
            total_frame, cur_frame, pay_len = struct.unpack_from("<HHH", hdr, 3)

            if pay_len == 0 or pay_len > 20000 or total_frame == 0 or cur_frame >= total_frame:
                continue

            payload = self._read_exact(sock, pay_len, timeout=12.0)
            if len(payload) < pay_len:
                break

            if pkt_type == 0x5B:
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
                V380Frame(
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

        codec = self._detect_video_codec(raw_type, normalized)
        is_key = self._is_key_video_frame(raw_type, outer_frame_type, normalized)

        self.on_frame(
            V380Frame(
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
        best_score = -10**9
        best_payload: Optional[bytes] = None

        # Always test offset 16 first! V380 outer media frame headers are 16 bytes,
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
        # V380 packet type tags explicitly distinguish HEVC (0x28/0x29) vs H.264 (0x00/0x01)
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

    def _read_exact(self, sock: socket.socket, count: int, timeout: float = 5.0) -> bytes:
        sock.settimeout(timeout)
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while len(buf) < count:
            if self._stop_event.is_set() or time.monotonic() > deadline:
                self._need_reconnect = True
                break
            try:
                chunk = sock.recv(count - len(buf))
                if not chunk:
                    self._need_reconnect = True
                    break
                buf.extend(chunk)
            except socket.timeout:
                self._need_reconnect = True
                break
            except Exception:
                self._need_reconnect = True
                break
        return bytes(buf)

    # --- Camera Control Commands ---
    def ptz_up(self) -> bool:
        return self._send_raw(V380Commands.PTZ_UP)

    def ptz_down(self) -> bool:
        return self._send_raw(V380Commands.PTZ_DOWN)

    def ptz_left(self) -> bool:
        return self._send_raw(V380Commands.PTZ_LEFT)

    def ptz_right(self) -> bool:
        return self._send_raw(V380Commands.PTZ_RIGHT)

    def ptz_stop(self) -> bool:
        return self._send_raw(V380Commands.PTZ_STOP)

    def light_on(self) -> bool:
        return self._send_raw(V380Commands.LIGHT_ON)

    def light_off(self) -> bool:
        return self._send_raw(V380Commands.LIGHT_OFF)

    def light_auto(self) -> bool:
        ok1 = self._send_raw(V380Commands.LIGHT_AUTO)
        time.sleep(0.03)
        ok2 = self._send_raw(V380Commands.IMAGE_AUTO)
        return ok1 or ok2

    def image_color(self) -> bool:
        return self._send_raw(V380Commands.IMAGE_COLOR)

    def image_bw(self) -> bool:
        return self._send_raw(V380Commands.IMAGE_BW)

    def image_auto(self) -> bool:
        ok1 = self._send_raw(V380Commands.IMAGE_AUTO)
        time.sleep(0.03)
        ok2 = self._send_raw(V380Commands.LIGHT_AUTO)
        return ok1 or ok2

    def image_flip(self) -> bool:
        return self._send_raw(V380Commands.IMAGE_FLIP)

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
            if self.device_version > 20 or ticket:
                struct.pack_into("<I", buf, 4, self.device_id & 0xFFFFFFFF)
                buf[8] = 0
                struct.pack_into("<I", buf, 9, ticket & 0xFFFFFFFF)
                struct.pack_into("<I", buf, 13, int(action))
                struct.pack_into("<I", buf, 17, int(slot_id))
            else:
                u_b = (self.username or "admin").encode("ascii", errors="ignore")[:32]
                p_b = self.password.encode("ascii", errors="ignore")[:32]
                buf[4 : 4 + len(u_b)] = u_b
                buf[36 : 36 + len(p_b)] = p_b
                struct.pack_into("<I", buf, 68, int(action))
                struct.pack_into("<I", buf, 72, int(slot_id))
                struct.pack_into("<I", buf, 76, self.device_id & 0xFFFFFFFF)

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
        ok_add = self._send_ptzx_port8800(V380Commands.PTZX_ACTION_ADD, slot_id)
        time.sleep(0.12)
        ok_reset = self._send_ptzx_port8800(V380Commands.PTZX_ACTION_RESET, slot_id)
        return ok_add or ok_reset

    def ptz_preset_set(self, slot_id: int) -> bool:
        """Save current camera PTZ position into hardware preset slot (0..5)."""
        slot = max(0, min(5, int(slot_id)))
        ok_add = self._send_raw(V380Commands.ptzx_packet(V380Commands.PTZX_ACTION_ADD, slot))
        time.sleep(0.04)
        ok_reset = self._send_raw(V380Commands.ptzx_packet(V380Commands.PTZX_ACTION_RESET, slot))
        threading.Thread(
            target=self._save_ptzx_port8800,
            args=(slot,),
            daemon=True,
        ).start()
        return ok_add or ok_reset

    def ptz_preset_goto(self, slot_id: int) -> bool:
        """Recall and auto-pan camera to hardware preset slot (0..5) via SetPTZXParam (0xAB, 103, slot)."""
        slot = max(0, min(5, int(slot_id)))
        ok_stream = self._send_raw(V380Commands.ptzx_packet(V380Commands.PTZX_ACTION_GOTO, slot))
        if not ok_stream:
            threading.Thread(
                target=self._send_ptzx_port8800,
                args=(V380Commands.PTZX_ACTION_GOTO, slot),
                daemon=True,
            ).start()
        return ok_stream

    def ptz_preset_clear(self, slot_id: int) -> bool:
        """Delete hardware preset slot (0..5)."""
        slot = max(0, min(5, int(slot_id)))
        ok_stream = self._send_raw(V380Commands.ptzx_packet(V380Commands.PTZX_ACTION_DEL, slot))
        threading.Thread(
            target=self._send_ptzx_port8800,
            args=(V380Commands.PTZX_ACTION_DEL, slot),
            daemon=True,
        ).start()
        return ok_stream

    # --- On-Camera microSD Card Segment Search & Playback (SDK 6.0 `361`/`362`/`363`/`364`) ---
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
    ) -> Tuple[bool, str, List[V380RecordingSegment]]:
        """
        Query the camera's microSD card for all recording segments on `(year, month, day)`
        using the Macrovideo SDK 6.0 Segment Search protocol (`cmd 361` -> `resp 461`, `cmd 362` -> `resp 462`).
        """
        msg = "Connection failed"
        for attempt in range(3):
            if self._stop_event.is_set():
                return False, "Cancelled", []
            if attempt == 1:
                self._stop_event.wait(0.25)
            res, msg = self.authenticate(skip_cloud_wake=(attempt < 2))
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
            segments: List[V380RecordingSegment] = []
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
                                V380RecordingSegment(
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
        segment: V380RecordingSegment,
        start_ts: Optional[int] = None,
        stop_event: Optional[threading.Event] = None,
        on_frame: Optional[Callable[[V380Frame], None]] = None,
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
        for attempt in range(3):
            if is_stopped():
                return False, "Cancelled"
            if attempt == 1:
                self._stop_event.wait(0.25)
            res, msg = self.authenticate(skip_cloud_wake=(attempt < 2))
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
                                V380Frame(
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
                        elif ftype in (21, 22, 24, 25, 26) and self.enable_audio:
                            if need_decrypt and len(fbody) >= 16:
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
                                V380Frame(
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
