"""
Multi-Camera Video & Audio Stream Worker for OmniSight NVR.
Unofficial client compatible with V380 cameras.

Supports three connection modes per camera:
1. `lan8800` — LAN Port 8800 protocol (H.264 / H.265 via low-latency FFmpeg pipe + AAC / IMA-ADPCM 8kHz audio)
2. `rtsp` — Standard RTSP / HTTP stream (e.g. `rtsp://admin:pass@ip:554/live/ch00_1` via OpenCV/FFmpeg)
3. `demo` — Interactive Camera Simulator (supports PTZ pan/tilt, IR/Night mode, image flip, audio, OSD)
"""

from __future__ import annotations

import math
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from PyQt6.QtCore import QByteArray, QIODevice, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QImage
from PyQt6.QtMultimedia import QAudioFormat, QAudioSink, QMediaDevices

from omnisight_protocol import (
    CameraFrame,
    LanCameraClient,
    RecordingSegment,
    VideoCodec,
    decode_adpcm_to_pcm16,
    decode_alaw_to_pcm16,
)


def get_ffmpeg_executable() -> str:
    """Locate bundled ffmpeg.exe (PyInstaller / app dir / imageio-ffmpeg) or system PATH."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "ffmpeg.exe")
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).resolve().parent / "ffmpeg.exe")
    candidates.append(Path(__file__).resolve().parent / "ffmpeg.exe")

    for cand in candidates:
        if cand.is_file():
            return str(cand)

    try:
        import imageio_ffmpeg

        ff = imageio_ffmpeg.get_ffmpeg_exe()
        if ff and os.path.isfile(ff):
            return ff
    except Exception:
        pass

    return shutil.which("ffmpeg") or "ffmpeg"


def get_yolo_model_path() -> Optional[Path]:
    """Locate bundled models/yolov8n.onnx across PyInstaller, installed dir, or workspace."""
    candidates = []
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "models" / "yolov8n.onnx")
        candidates.append(Path(meipass) / "yolov8n.onnx")
    if getattr(sys, "frozen", False):
        exe_dir = Path(sys.executable).resolve().parent
        candidates.append(exe_dir / "models" / "yolov8n.onnx")
        candidates.append(exe_dir / "_internal" / "models" / "yolov8n.onnx")
    here = Path(__file__).resolve().parent
    candidates.append(here / "models" / "yolov8n.onnx")
    candidates.append(here / "_internal" / "models" / "yolov8n.onnx")

    for cand in candidates:
        if cand.is_file():
            return cand
    return None


_GPU_CAPS_CACHE: Optional[Dict[str, Any]] = None


def get_gpu_capabilities() -> Dict[str, Any]:
    """
    Detect DirectML AI GPU acceleration (ONNX Runtime), OpenCL GPU (cv2.UMat),
    and Windows Direct3D 11 (d3d11va) video decode support.
    Cached so repeated calls have zero overhead.
    """
    global _GPU_CAPS_CACHE
    if _GPU_CAPS_CACHE is not None:
        return _GPU_CAPS_CACHE

    dml_ok = False
    try:
        import onnxruntime as ort

        dml_ok = "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:
        dml_ok = False

    opencl_ok = False
    dev_name = ""
    try:
        if cv2.ocl.haveOpenCL():
            cv2.ocl.setUseOpenCL(True)
            opencl_ok = bool(cv2.ocl.useOpenCL())
            dev = cv2.ocl.Device.getDefault()
            if dev is not None:
                dev_name = (dev.name() or "").strip()
    except Exception:
        opencl_ok = False

    d3d11_ok = os.name == "nt"
    parts = []
    if dml_ok:
        parts.append("DirectML AI")
    if opencl_ok:
        parts.append(f"OpenCL ({dev_name})" if dev_name else "OpenCL")
    if d3d11_ok:
        parts.append("D3D11VA")

    avail = dml_ok or opencl_ok or d3d11_ok
    summary = " + ".join(parts) if parts else "CPU Only"
    _GPU_CAPS_CACHE = {
        "available": avail,
        "directml": dml_ok,
        "opencl": opencl_ok,
        "d3d11va": d3d11_ok,
        "device_name": dev_name or ("DirectML / D3D11 GPU" if (dml_ok or d3d11_ok) else "CPU"),
        "summary": summary,
    }
    return _GPU_CAPS_CACHE


@dataclass
class CameraConfig:
    cam_id: str
    name: str
    protocol: str = "lan8800"  # "lan8800" | "rtsp" | "demo"
    ip: str = "192.168.1.100"
    port: int = 8800
    device_id: str = ""
    username: str = "admin"
    password: str = ""
    quality: int = 1  # 1 = HD, 0 = SD
    rtsp_url: str = ""
    audio_enabled: bool = True
    light_mode: str = "auto"  # "on" | "off" | "auto"
    image_mode: str = "auto"  # "color" | "bw" | "auto"
    flipped: bool = False
    person_detection: bool = True
    detection_sensitivity: str = "ultra"  # "standard" | "high" | "ultra"
    gpu_acceleration: bool = True
    ptz_presets: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CameraConfig":
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        if str(filtered.get("protocol", "")).lower() == "v380":
            filtered["protocol"] = "lan8800"
        return cls(**filtered)


class LiveAudioOutput(QObject):
    """Plays 8000 Hz 16-bit mono PCM audio chunks on the Windows default audio device."""

    def __init__(self, sample_rate: int = 8000, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.sample_rate = sample_rate
        self.muted = True
        self.volume = 0.85
        self._sink: Optional[QAudioSink] = None
        self._io_device: Optional[QIODevice] = None

    def _init_sink(self) -> None:
        if self._sink is not None and self._io_device is not None:
            return
        try:
            device = QMediaDevices.defaultAudioOutput()
            if device.isNull():
                return
            fmt = QAudioFormat()
            fmt.setSampleRate(self.sample_rate)
            fmt.setChannelCount(1)
            fmt.setSampleFormat(QAudioFormat.SampleFormat.Int16)
            self._sink = QAudioSink(device, fmt, self)
            self._sink.setVolume(self.volume)
            self._io_device = self._sink.start()
        except Exception:
            self._sink = None
            self._io_device = None

    def set_muted(self, muted: bool) -> None:
        self.muted = muted
        if muted:
            self.close()

    def set_volume(self, volume: float) -> None:
        self.volume = max(0.0, min(1.0, volume))
        if self._sink is not None:
            try:
                self._sink.setVolume(self.volume)
            except Exception:
                pass

    def write_pcm(self, pcm_bytes: bytes) -> None:
        if self.muted or not pcm_bytes:
            return
        if self._io_device is None:
            self._init_sink()
        if self._io_device is not None:
            try:
                self._io_device.write(QByteArray(pcm_bytes))
            except Exception:
                pass

    def flush(self) -> None:
        """Keep the underlying WASAPI QAudioSink alive across clip switches to avoid Windows COM deadlocks."""
        return

    def close(self) -> None:
        if self._sink is not None:
            try:
                self._sink.stop()
            except Exception:
                pass
            self._sink = None
            self._io_device = None


class _YoloOnnxEngine:
    """
    Thread-safe shared YOLOv8n ONNX Runtime inference engine.
    Supports DirectML GPU acceleration ('DmlExecutionProvider') and CPU execution ('CPUExecutionProvider').
    Shared across all camera workers to minimize VRAM usage and eliminate DirectML device contention.
    """

    _lock = threading.Lock()
    _sess_gpu: Any = None
    _sess_cpu: Any = None
    _inp_name: str = "images"
    _load_failed: bool = False

    @classmethod
    def _get_session(cls, use_gpu: bool) -> Any:
        if cls._load_failed:
            return None
        if use_gpu and cls._sess_gpu is not None:
            return cls._sess_gpu
        if not use_gpu and cls._sess_cpu is not None:
            return cls._sess_cpu

        model_path = get_yolo_model_path()
        if model_path is None:
            cls._load_failed = True
            return None

        try:
            import onnxruntime as ort

            avail = ort.get_available_providers()
            sess_opts = ort.SessionOptions()
            sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            sess_opts.intra_op_num_threads = 2

            if use_gpu and "DmlExecutionProvider" in avail:
                sess = ort.InferenceSession(
                    str(model_path),
                    sess_options=sess_opts,
                    providers=["DmlExecutionProvider", "CPUExecutionProvider"],
                )
                cls._sess_gpu = sess
                cls._inp_name = sess.get_inputs()[0].name
                return sess
            else:
                if cls._sess_cpu is None:
                    sess = ort.InferenceSession(
                        str(model_path),
                        sess_options=sess_opts,
                        providers=["CPUExecutionProvider"],
                    )
                    cls._sess_cpu = sess
                    cls._inp_name = sess.get_inputs()[0].name
                if use_gpu:
                    cls._sess_gpu = cls._sess_cpu
                return cls._sess_cpu
        except Exception:
            cls._load_failed = True
            return None

    @classmethod
    def detect_persons(
        cls,
        bgr_img: np.ndarray,
        conf_thresh: float = 0.24,
        use_gpu: bool = True,
    ) -> List[Tuple[float, float, float, float, float]]:
        """
        Run YOLOv8n person detection (COCO class 0) on a BGR image or crop.
        Returns normalized [(x1, y1, x2, y2, confidence), ...] in [0..1] relative to `bgr_img`.
        """
        if bgr_img is None or bgr_img.size == 0:
            return []
        h, w = bgr_img.shape[:2]
        if h < 16 or w < 16:
            return []

        scale = min(640.0 / float(w), 640.0 / float(h))
        nw = max(1, int(round(w * scale)))
        nh = max(1, int(round(h * scale)))
        resized = cv2.resize(bgr_img, (nw, nh), interpolation=cv2.INTER_LINEAR)
        canvas = np.full((640, 640, 3), 114, dtype=np.uint8)
        dw = (640 - nw) // 2
        dh = (640 - nh) // 2
        canvas[dh : dh + nh, dw : dw + nw] = resized

        # RGB float32 NCHW [1, 3, 640, 640] in [0, 1]
        blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) * (1.0 / 255.0)
        blob = np.expand_dims(blob, axis=0)

        with cls._lock:
            sess = cls._get_session(use_gpu=use_gpu)
            if sess is None:
                return []
            try:
                preds = sess.run(None, {cls._inp_name: blob})[0][0]  # shape: (84, 8400)
            except Exception:
                if use_gpu:
                    sess = cls._get_session(use_gpu=False)
                    if sess is None:
                        return []
                    preds = sess.run(None, {cls._inp_name: blob})[0][0]
                else:
                    return []

        # Class 0 in COCO is 'person' -> row index 4
        person_scores = preds[4, :]
        mask = person_scores >= conf_thresh
        if not np.any(mask):
            return []

        boxes_xywh = preds[:4, mask].T
        scores = person_scores[mask]
        boxes_xywh_px: List[List[float]] = []
        for cx, cy, bw, bh in boxes_xywh:
            x1 = (float(cx) - float(bw) * 0.5 - dw) / scale
            y1 = (float(cy) - float(bh) * 0.5 - dh) / scale
            w_px = float(bw) / scale
            h_px = float(bh) / scale
            boxes_xywh_px.append([x1, y1, w_px, h_px])

        indices = cv2.dnn.NMSBoxes(boxes_xywh_px, scores.tolist(), float(conf_thresh), 0.45)
        if len(indices) == 0:
            return []

        results: List[Tuple[float, float, float, float, float]] = []
        for idx in np.array(indices).flatten():
            bx, by, bw_px, bh_px = boxes_xywh_px[int(idx)]
            x1n = max(0.0, min(1.0, bx / float(w)))
            y1n = max(0.0, min(1.0, by / float(h)))
            x2n = max(0.0, min(1.0, (bx + bw_px) / float(w)))
            y2n = max(0.0, min(1.0, (by + bh_px) / float(h)))
            if (x2n - x1n) > 0.008 and (y2n - y1n) > 0.025:
                results.append((x1n, y1n, x2n, y2n, float(scores[int(idx)])))
        return results


class ClassicalPersonDetector:
    """
    Hybrid YOLOv8 Deep Learning AI + Motion-Guided Optical Zoom (SAHI) Person Detector:
    1. DirectML GPU Acceleration ('DmlExecutionProvider' on Windows GPU) + OpenCL scene motion analysis
    2. Stage 1 — Full-Frame YOLOv8n AI Pass:
       - Recognizes real human anatomy (head, shoulders, torso, legs) and ignores bushes, trees, windows, and shadows
       - Static Inanimate Clutter Suppression: prevents stationary piles of tarp, clotheslines, or furniture from false-alarming
    3. Stage 2 — Motion-Guided 3.2x Far-Field Optical Zoom (SAHI) Pass:
       - Locates non-flickering movement in the far/mid field and crops a high-resolution 3.2x zoomed window
       - Verifies the distant moving target with YOLOv8n AI so distant walkers down to ~4.4% frame height are caught as 'PERSON (FAR)'
         while wind-blown distant bushes are 100% rejected by the neural network
    4. Multi-Frame Temporal AI Tracker with smooth bounding box interpolation
    """

    def __init__(self, sensitivity: str = "ultra", use_gpu: bool = True) -> None:
        self.sensitivity: str = (sensitivity or "ultra").lower()
        self.use_gpu: bool = bool(use_gpu)
        caps = get_gpu_capabilities()
        self._gpu_available: bool = bool(caps.get("directml", False) or caps.get("opencl", False))
        self._opencl_available: bool = bool(caps.get("opencl", False))
        self._kern_open = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        self._kern_close = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 11))
        self._last_run_time: float = 0.0
        self._ptz_cooldown_until: float = 0.0
        self._active_boxes: List[Tuple[float, float, float, float, str]] = []
        self._last_hit_time: float = 0.0
        self._last_full_yolo_time: float = 0.0
        self._warmup_frames: int = 0
        self._empty_passes: int = 0

        # Scene motion & wind-flicker state (320x180 grid)
        self._bg_gray: Optional[np.ndarray] = None
        self._prev_gray: Optional[np.ndarray] = None
        self._prev_motion: Optional[np.ndarray] = None
        self._flicker_map: Optional[np.ndarray] = None
        self._tracks: List[dict] = []

    @property
    def min_interval(self) -> float:
        """Target interval between AI passes (~9 FPS on DirectML GPU, ~6 FPS on CPU)."""
        if self.use_gpu and self._gpu_available:
            return 0.110
        return 0.165

    def configure(self, sensitivity: str = "ultra", use_gpu: bool = True) -> None:
        new_sens = (sensitivity or "ultra").lower()
        if new_sens not in ("standard", "high", "ultra"):
            new_sens = "ultra"
        self.sensitivity = new_sens
        self.use_gpu = bool(use_gpu)

    def notify_camera_motion(self, cooldown_sec: float = 1.8) -> None:
        """Pause detection briefly when PTZ gimbal moves or stream resets."""
        self._ptz_cooldown_until = max(self._ptz_cooldown_until, time.monotonic() + cooldown_sec)
        self._active_boxes.clear()
        self._tracks.clear()
        self._bg_gray = None
        self._prev_gray = None
        self._prev_motion = None
        self._flicker_map = None
        self._warmup_frames = 0

    def process_bgr(
        self,
        bgr: np.ndarray,
        enabled: bool = True,
        sensitivity: Optional[str] = None,
        use_gpu: Optional[bool] = None,
    ) -> List[Tuple[float, float, float, float, str]]:
        """
        Return normalized bounding boxes: [(x1_norm, y1_norm, x2_norm, y2_norm, label), ...]
        """
        if sensitivity is not None or use_gpu is not None:
            self.configure(
                sensitivity if sensitivity is not None else self.sensitivity,
                use_gpu if use_gpu is not None else self.use_gpu,
            )

        if not enabled or bgr is None or bgr.size == 0:
            self._active_boxes.clear()
            self._tracks.clear()
            return []

        now = time.monotonic()
        if now < self._ptz_cooldown_until:
            self._active_boxes.clear()
            self._tracks.clear()
            return []

        if (now - self._last_run_time) < self.min_interval:
            if (now - self._last_hit_time) > 0.95:
                self._active_boxes.clear()
            return list(self._active_boxes)
        self._last_run_time = now

        h_orig, w_orig = bgr.shape[:2]
        if w_orig <= 0 or h_orig <= 0:
            return []

        sens = self.sensitivity
        use_gpu_flag = self.use_gpu

        # --- Step 1: Lightweight 320x180 3-Channel Motion & Wind-Flicker Map ---
        mw, mh = 320, 180
        small_bgr = cv2.GaussianBlur(
            cv2.resize(bgr, (mw, mh), interpolation=cv2.INTER_LINEAR), (3, 3), 0
        )
        bgr_f32 = small_bgr.astype(np.float32)

        if self._bg_gray is None or self._bg_gray.shape != bgr_f32.shape:
            self._bg_gray = bgr_f32.copy()
            self._prev_gray = small_bgr.copy()
            self._prev_motion = np.zeros((mh, mw), dtype=np.uint8)
            self._flicker_map = np.zeros((mh, mw), dtype=np.float32)
            self._warmup_frames = 0

        bg_u8 = cv2.convertScaleAbs(self._bg_gray)
        bg_diff = np.max(cv2.absdiff(small_bgr, bg_u8), axis=2)
        step_diff = (
            np.max(cv2.absdiff(small_bgr, self._prev_gray), axis=2)
            if self._prev_gray is not None
            else bg_diff
        )
        self._prev_gray = small_bgr.copy()

        # Adapt background slowly outside confirmed tracks so standing people remain distinct from background
        cv2.accumulateWeighted(bgr_f32, self._bg_gray, 0.015)
        self._warmup_frames += 1

        _, bg_bin = cv2.threshold(bg_diff, 12, 255, cv2.THRESH_BINARY)
        _, step_bin = cv2.threshold(step_diff, 8, 255, cv2.THRESH_BINARY)
        raw_motion = cv2.bitwise_or(bg_bin, step_bin)

        # Mask out OSD timestamp (top-right) and REC watermark (bottom-right)
        raw_motion[: int(mh * 0.11), int(mw * 0.64) :] = 0
        raw_motion[int(mh * 0.89) :, int(mw * 0.85) :] = 0

        # Global illumination/camera-shake reset check
        if float(cv2.countNonZero(raw_motion)) / float(mw * mh) > 0.50:
            self._bg_gray = bgr_f32.copy()
            self._active_boxes.clear()
            self._tracks.clear()
            return []

        # Per-pixel wind/foliage flicker suppression
        toggled = cv2.bitwise_xor(step_bin, self._prev_motion)
        self._prev_motion = step_bin.copy()
        self._flicker_map *= 0.92
        self._flicker_map[toggled > 0] += 0.16
        np.clip(self._flicker_map, 0.0, 1.5, out=self._flicker_map)
        foliage_mask = (self._flicker_map > 0.62).astype(np.uint8) * 255
        clean_motion = cv2.bitwise_and(raw_motion, cv2.bitwise_not(foliage_mask))
        clean_step = cv2.bitwise_and(step_bin, cv2.bitwise_not(foliage_mask))

        # Helper to measure motion activity inside a normalized box
        def _box_motion_metrics(x1n: float, y1n: float, x2n: float, y2n: float) -> Tuple[float, float]:
            mx1 = max(0, min(mw - 1, int(x1n * mw)))
            my1 = max(0, min(mh - 1, int(y1n * mh)))
            mx2 = max(mx1 + 1, min(mw, int(math.ceil(x2n * mw))))
            my2 = max(my1 + 1, min(mh, int(math.ceil(y2n * mh))))
            area = float((mx2 - mx1) * (my2 - my1))
            m_nz = float(cv2.countNonZero(clean_motion[my1:my2, mx1:mx2]))
            s_nz = float(cv2.countNonZero(clean_step[my1:my2, mx1:mx2]))
            return m_nz / area, s_nz / area

        def _matches_confirmed_track(x1n: float, y1n: float, x2n: float, y2n: float) -> bool:
            cx, cy = (x1n + x2n) * 0.5, (y1n + y2n) * 0.5
            bh = y2n - y1n
            for tr in self._tracks:
                if tr.get("confirmed", False):
                    if math.hypot(cx - tr["cx"], cy - tr["cy"]) <= max(0.08, bh * 0.85):
                        return True
            return False

        # Sensitivity thresholds
        if sens == "ultra":
            base_conf = 0.25
            static_conf = 0.58
            zoom_conf = 0.22
        elif sens == "high":
            base_conf = 0.30
            static_conf = 0.60
            zoom_conf = 0.27
        else:
            base_conf = 0.38
            static_conf = 0.62
            zoom_conf = 0.34

        # Fast Motion Pre-Gate:
        # When the scene has no active non-flickering motion and no active tracks, skip neural network
        # inference except for a periodic 1.2s heartbeat check so idle cameras consume ~0% GPU/CPU.
        total_step_nz = int(cv2.countNonZero(clean_step))
        total_mot_nz = int(cv2.countNonZero(clean_motion))
        scene_has_activity = (
            total_step_nz >= 6
            or total_mot_nz >= 20
            or bool(self._tracks)
            or bool(self._active_boxes)
            or (now - self._last_full_yolo_time) >= 1.20
        )
        if not scene_has_activity:
            return []

        self._last_full_yolo_time = now
        ai_candidates: List[Tuple[float, float, float, float, float]] = []

        # --- Step 2: Stage 1 — Full-Frame YOLOv8n AI Pass ---
        full_dets = _YoloOnnxEngine.detect_persons(bgr, conf_thresh=base_conf, use_gpu=use_gpu_flag)
        for x1n, y1n, x2n, y2n, conf in full_dets:
            bw = x2n - x1n
            bh = y2n - y1n
            aspect = bh / max(1e-4, bw)
            if bh < 0.038 or bh > 0.96 or bw < 0.012 or bw > 0.65:
                continue
            if aspect < 1.05 or aspect > 5.8:
                continue

            mot_ratio, step_ratio = _box_motion_metrics(x1n, y1n, x2n, y2n)
            has_motion = (mot_ratio >= 0.018) or (step_ratio >= 0.008)
            is_tracked = _matches_confirmed_track(x1n, y1n, x2n, y2n)

            # Accept if:
            # 1) High-confidence human appearance (conf >= static_conf), OR
            # 2) Verified human appearance (conf >= base_conf) corroborated by scene motion or active track
            if conf >= static_conf or has_motion or is_tracked:
                ai_candidates.append((x1n, y1n, x2n, y2n, conf))

        # --- Step 3: Stage 2 — Motion-Guided 3.2x Optical Zoom (SAHI) Pass for Distant People ---
        if sens in ("ultra", "high") and self._warmup_frames >= 3:
            # Group active motion blobs in the far/mid field to guide zoomed AI crops
            far_motion = cv2.morphologyEx(clean_motion, cv2.MORPH_CLOSE, self._kern_close)
            contours, _ = cv2.findContours(far_motion, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

            zoom_rois: List[Tuple[float, float, float]] = []  # (score, cx_norm, cy_norm)
            for cnt in contours:
                x, y, w, h = cv2.boundingRect(cnt)
                bh_frac = float(h) / float(mh)
                bw_frac = float(w) / float(mw)
                # Far/mid-field moving target size range (3.5% to 28% of frame height)
                if bh_frac < 0.035 or bh_frac > 0.28 or bw_frac > 0.25:
                    continue
                if (y + h) / float(mh) < 0.12:
                    continue

                cx_n = (x + w * 0.5) / float(mw)
                cy_n = (y + h * 0.5) / float(mh)

                # Skip if already covered by a Full-Frame YOLOv8 detection
                already_covered = False
                for ax1, ay1, ax2, ay2, _ in ai_candidates:
                    if (ax1 - 0.04) <= cx_n <= (ax2 + 0.04) and (ay1 - 0.04) <= cy_n <= (ay2 + 0.04):
                        already_covered = True
                        break
                if already_covered:
                    continue

                step_energy = float(cv2.countNonZero(clean_step[y : y + h, x : x + w]))
                if step_energy < 2.0:
                    continue
                zoom_rois.append((step_energy, cx_n, cy_n))

            # Also include active far-field confirmed tracks so a distant person who briefly pauses stays zoomed
            for tr in self._tracks:
                if tr.get("confirmed", False) and tr["h"] < 0.16:
                    already_covered = any(
                        abs(tr["cx"] - ax1) < 0.08 for ax1, _, _, _, _ in ai_candidates
                    )
                    if not already_covered:
                        zoom_rois.append((100.0, tr["cx"], tr["cy"]))

            zoom_rois.sort(key=lambda item: item[0], reverse=True)
            max_zoom_crops = 2 if (use_gpu_flag and self._gpu_available and sens == "ultra") else 1

            used_centers: List[Tuple[float, float]] = []
            for _, cx_n, cy_n in zoom_rois:
                if len(used_centers) >= max_zoom_crops:
                    break
                if any(math.hypot(cx_n - ux, cy_n - uy) < 0.18 for ux, uy in used_centers):
                    continue
                used_centers.append((cx_n, cy_n))

                # 3.2x optical zoom window (31% of original frame width & height)
                cw = max(96, int(round(w_orig * 0.31)))
                ch = max(72, int(round(h_orig * 0.31)))
                cx_px = int(round(cx_n * w_orig))
                cy_px = int(round(cy_n * h_orig))
                x0 = max(0, min(w_orig - cw, cx_px - cw // 2))
                y0 = max(0, min(h_orig - ch, cy_px - ch // 2))

                crop_bgr = bgr[y0 : y0 + ch, x0 : x0 + cw]
                zoom_dets = _YoloOnnxEngine.detect_persons(
                    crop_bgr, conf_thresh=zoom_conf, use_gpu=use_gpu_flag
                )
                for zx1, zy1, zx2, zy2, zconf in zoom_dets:
                    zbw = zx2 - zx1
                    zbh = zy2 - zy1
                    # Must be a genuinely distant silhouette inside the zoom tile and not clipped at tile borders
                    if not (0.11 <= zbh <= 0.52 and 0.025 <= zbw <= 0.26):
                        continue
                    if zy1 < 0.07 or zy2 > 0.93 or zx1 < 0.04 or zx2 > 0.96:
                        continue
                    zaspect = zbh / max(1e-4, zbw)
                    if zaspect < 1.15 or zaspect > 5.5:
                        continue

                    # Map zoomed detection back to full-frame normalized coordinates
                    fx1 = (x0 + zx1 * cw) / float(w_orig)
                    fy1 = (y0 + zy1 * ch) / float(h_orig)
                    fx2 = (x0 + zx2 * cw) / float(w_orig)
                    fy2 = (y0 + zy2 * ch) / float(h_orig)

                    mot_r, step_r = _box_motion_metrics(fx1, fy1, fx2, fy2)
                    if mot_r >= 0.015 or step_r >= 0.008 or _matches_confirmed_track(fx1, fy1, fx2, fy2):
                        ai_candidates.append((fx1, fy1, fx2, fy2, zconf))

        merged = self._merge_overlapping_scored(ai_candidates)
        confirmed_boxes = self._update_tracks(merged, now)

        if confirmed_boxes:
            self._last_hit_time = now
            self._empty_passes = 0
            self._active_boxes = self._smooth_boxes(confirmed_boxes)
        else:
            self._empty_passes += 1
            if self._empty_passes >= 3 or (now - self._last_hit_time) > 0.80:
                self._active_boxes.clear()

        return list(self._active_boxes)

    def _update_tracks(
        self,
        scored_boxes: List[Tuple[float, float, float, float, float]],
        now: float,
    ) -> List[Tuple[float, float, float, float]]:
        """
        Multi-frame AI tracker:
        - High-confidence detections (conf >= 0.52) lock on immediately
        - Medium/Far-Zoom detections confirm in 2 consecutive hits
        - Suppresses permanently static false objects that never exhibit displacement
        """
        matched_track_indices = set()
        for bx1, by1, bx2, by2, conf in scored_boxes:
            bcx = (bx1 + bx2) * 0.5
            bcy = (by1 + by2) * 0.5
            bw = bx2 - bx1
            bh = by2 - by1

            best_idx = -1
            best_dist = max(0.08, min(0.20, bh * 1.15))
            for idx, tr in enumerate(self._tracks):
                if idx in matched_track_indices:
                    continue
                dist = math.hypot(bcx - tr["cx"], bcy - tr["cy"])
                if dist < best_dist:
                    best_dist = dist
                    best_idx = idx

            if best_idx >= 0:
                tr = self._tracks[best_idx]
                matched_track_indices.add(best_idx)
                step_dist = math.hypot(bcx - tr["cx"], bcy - tr["cy"])
                tr["path_len"] += step_dist
                tr["cx"] = bcx
                tr["cy"] = bcy
                tr["w"] = bw
                tr["h"] = bh
                tr["box"] = (bx1, by1, bx2, by2)
                tr["hits"] += 1
                tr["misses"] = 0
                tr["last_seen"] = now
                tr["conf"] = max(tr["conf"] * 0.7 + conf * 0.3, conf)

                net_disp = math.hypot(bcx - tr["anchor_cx"], bcy - tr["anchor_cy"])
                if net_disp >= max(0.005, bh * 0.08):
                    tr["has_translated"] = True

                # Confirm track: 1 hit if high AI confidence, or 2 hits for far/medium AI confidence
                if tr["hits"] >= 2 or tr["conf"] >= 0.52:
                    tr["confirmed"] = True

                # If an object sits completely frozen at the exact same spot for >35 hits with mediocre confidence,
                # demote it as static background clutter (e.g. hanging jacket or mannequin-like object)
                if tr["hits"] > 35 and not tr["has_translated"] and tr["conf"] < 0.68:
                    tr["confirmed"] = False
            else:
                is_immediate = conf >= 0.52
                self._tracks.append(
                    {
                        "cx": bcx,
                        "cy": bcy,
                        "w": bw,
                        "h": bh,
                        "anchor_cx": bcx,
                        "anchor_cy": bcy,
                        "path_len": 0.0,
                        "box": (bx1, by1, bx2, by2),
                        "hits": 1,
                        "misses": 0,
                        "last_seen": now,
                        "conf": conf,
                        "has_translated": False,
                        "confirmed": is_immediate,
                    }
                )

        alive_tracks: List[dict] = []
        confirmed_out: List[Tuple[float, float, float, float]] = []
        for idx, tr in enumerate(self._tracks):
            if idx not in matched_track_indices:
                tr["misses"] += 1
            max_misses = 3 if tr["confirmed"] else 1
            if tr["misses"] <= max_misses and (now - tr["last_seen"]) <= 0.95:
                alive_tracks.append(tr)
                if tr["confirmed"] and tr["misses"] <= 2:
                    confirmed_out.append(tr["box"])

        self._tracks = alive_tracks
        return confirmed_out

    @staticmethod
    def _merge_overlapping_scored(
        boxes: List[Tuple[float, float, float, float, float]],
    ) -> List[Tuple[float, float, float, float, float]]:
        if not boxes:
            return []
        # Sort highest confidence first
        ordered = sorted(boxes, key=lambda b: b[4], reverse=True)
        kept: List[List[float]] = []
        for bx1, by1, bx2, by2, conf in ordered:
            merged = False
            for k in kept:
                kx1, ky1, kx2, ky2, kconf = k
                ix1, iy1 = max(bx1, kx1), max(by1, ky1)
                ix2, iy2 = min(bx2, kx2), min(by2, ky2)
                inter = max(0.0, ix2 - ix1) * max(0.0, iy2 - iy1)
                area_b = max(1e-6, (bx2 - bx1) * (by2 - by1))
                area_k = max(1e-6, (kx2 - kx1) * (ky2 - ky1))
                iou = inter / max(1e-6, area_b + area_k - inter)
                if iou >= 0.30 or (inter / min(area_b, area_k)) >= 0.65:
                    k[0] = min(kx1, bx1)
                    k[1] = min(ky1, by1)
                    k[2] = max(kx2, bx2)
                    k[3] = max(ky2, by2)
                    k[4] = max(kconf, conf)
                    merged = True
                    break
            if not merged:
                kept.append([bx1, by1, bx2, by2, conf])
        return [(r[0], r[1], r[2], r[3], r[4]) for r in kept[:5]]

    def _smooth_boxes(
        self, new_boxes: List[Tuple[float, float, float, float]]
    ) -> List[Tuple[float, float, float, float, str]]:
        alpha = 0.56
        out: List[Tuple[float, float, float, float, str]] = []
        for nx1, ny1, nx2, ny2 in new_boxes:
            ncx, ncy = (nx1 + nx2) * 0.5, (ny1 + ny2) * 0.5
            best_prev = None
            best_dist = 0.22
            for px1, py1, px2, py2, _ in self._active_boxes:
                pcx, pcy = (px1 + px2) * 0.5, (py1 + py2) * 0.5
                dist = math.hypot(ncx - pcx, ncy - pcy)
                if dist < best_dist:
                    best_dist = dist
                    best_prev = (px1, py1, px2, py2)
            if best_prev is not None:
                px1, py1, px2, py2 = best_prev
                sx1 = px1 + alpha * (nx1 - px1)
                sy1 = py1 + alpha * (ny1 - py1)
                sx2 = px2 + alpha * (nx2 - px2)
                sy2 = py2 + alpha * (ny2 - py2)
            else:
                sx1, sy1, sx2, sy2 = nx1, ny1, nx2, ny2
            bh = sy2 - sy1
            label = "PERSON (FAR)" if bh < 0.165 else "PERSON"
            out.append((sx1, sy1, sx2, sy2, label))
        return out

    @staticmethod
    def draw_boxes_on_bgr(
        bgr: np.ndarray, boxes: List[Tuple[float, float, float, float, str]]
    ) -> np.ndarray:
        """Draw 4-corner L-brackets (no rectangle sides, no text label) around detected persons on a BGR frame."""
        if not boxes or bgr is None:
            return bgr
        out = bgr.copy()
        h, w = out.shape[:2]
        color = (59, 59, 255)
        thickness = 2
        for box in boxes:
            if len(box) < 4:
                continue
            x1n, y1n, x2n, y2n = float(box[0]), float(box[1]), float(box[2]), float(box[3])
            x1, y1 = int(x1n * w), int(y1n * h)
            x2, y2 = int(x2n * w), int(y2n * h)
            bw = max(1, x2 - x1)
            bh = max(1, y2 - y1)
            arm = max(8, min(22, min(bw, bh) // 4))
            # Top-Left corner
            cv2.line(out, (x1, y1), (x1 + arm, y1), color, thickness, cv2.LINE_AA)
            cv2.line(out, (x1, y1), (x1, y1 + arm), color, thickness, cv2.LINE_AA)
            # Top-Right corner
            cv2.line(out, (x2 - arm, y1), (x2, y1), color, thickness, cv2.LINE_AA)
            cv2.line(out, (x2, y1), (x2, y1 + arm), color, thickness, cv2.LINE_AA)
            # Bottom-Left corner
            cv2.line(out, (x1, y2 - arm), (x1, y2), color, thickness, cv2.LINE_AA)
            cv2.line(out, (x1, y2), (x1 + arm, y2), color, thickness, cv2.LINE_AA)
            # Bottom-Right corner
            cv2.line(out, (x2 - arm, y2), (x2, y2), color, thickness, cv2.LINE_AA)
            cv2.line(out, (x2, y2 - arm), (x2, y2), color, thickness, cv2.LINE_AA)
        return out


class CameraStreamWorker(QThread):
    """
    Background worker thread for a single camera tile.
    Emits decoded `QImage` frames, audio PCM chunks, audio level, connection status, and stream stats.
    """

    frame_ready = pyqtSignal(str, QImage, float, float)  # cam_id, image, fps, kbps
    detections_ready = pyqtSignal(str, list)             # cam_id, [(x1, y1, x2, y2, label), ...]
    audio_ready = pyqtSignal(str, bytes, float)          # cam_id, pcm16_bytes, level (0.0..1.0)
    status_changed = pyqtSignal(str, str, bool)          # cam_id, status_text, is_connected
    recording_changed = pyqtSignal(str, bool, str)       # cam_id, is_recording, file_path

    def __init__(self, config: CameraConfig, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.config = config
        self._stop_flag = threading.Event()
        self._person_detector = ClassicalPersonDetector(
            sensitivity=getattr(config, "detection_sensitivity", "ultra"),
            use_gpu=bool(getattr(config, "gpu_acceleration", True)),
        )
        self._latest_detections: List[Tuple[float, float, float, float, str]] = []
        self._hwaccel_failed: bool = False
        self._decoded_any_frame: bool = False

        # LAN client reference for live PTZ / Light / Flip commands
        self._lan_client: Optional[LanCameraClient] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._ffmpeg_audio_proc: Optional[subprocess.Popen] = None
        self._ffmpeg_lock = threading.Lock()
        self._active_codec: VideoCodec = VideoCodec.UNKNOWN
        self._seen_keyframe = False

        # Recording state
        self._rec_lock = threading.Lock()
        self._video_writer: Optional[cv2.VideoWriter] = None
        self._recording_path: str = ""
        self._latest_qimage: Optional[QImage] = None
        self._latest_jpeg_bytes: Optional[bytes] = None
        self._recording_active = False
        self._preset_autopan_token: int = 0

        # Demo mode interactive state
        self._demo_pan = 0.0
        self._demo_tilt = 0.0
        self._demo_pan_vel = 0.0
        self._demo_tilt_vel = 0.0
        self._demo_target_pan: Optional[float] = None
        self._demo_target_tilt: Optional[float] = None
        self._demo_light_mode = "auto"  # "on" | "off" | "auto"
        self._demo_image_mode = "color"  # "color" | "bw" | "auto"
        self._demo_flipped = False

        # Asynchronous AI detection & recording queue (1-slot latest-frame buffer so video decode never blocks)
        self._det_event = threading.Event()
        self._pending_det_jpeg: Optional[bytes] = None
        self._pending_det_bgr: Optional[np.ndarray] = None

        # Telemetry counters
        self._bytes_window = 0
        self._frames_window = 0
        self._last_stat_time = time.monotonic()
        self._current_fps = 0.0
        self._current_kbps = 0.0

    def stop_stream(self) -> None:
        self._stop_flag.set()
        self._det_event.set()
        if self._lan_client is not None:
            self._lan_client.stop()
        self._stop_ffmpeg()
        self.stop_recording()

    def set_quality(self, quality: int) -> None:
        """Switch between HD (1) and SD (0) stream quality."""
        new_q = 1 if int(quality) else 0
        self.config.quality = new_q
        self._seen_keyframe = False
        with self._ffmpeg_lock:
            self._stop_ffmpeg_video_unlocked()
        if self._lan_client is not None:
            self._lan_client.set_stream_quality(new_q)
        elif (self.config.protocol or "lan8800").lower() == "demo":
            res_label = "HD 1080p" if new_q == 1 else "SD 360p"
            self.status_changed.emit(self.config.cam_id, f"Streaming ({res_label} • Port 8800 Sim)", True)

    def set_detection_options(
        self,
        enabled: bool,
        sensitivity: str = "ultra",
        gpu_acceleration: bool = True,
    ) -> None:
        """Update person detection toggle, long-range sensitivity, and GPU acceleration live."""
        self.config.person_detection = bool(enabled)
        self.config.detection_sensitivity = (sensitivity or "ultra").lower()
        self.config.gpu_acceleration = bool(gpu_acceleration)
        self._person_detector.configure(
            sensitivity=self.config.detection_sensitivity,
            use_gpu=self.config.gpu_acceleration,
        )
        if not self.config.person_detection and self._latest_detections:
            self._latest_detections = []
            self.detections_ready.emit(self.config.cam_id, [])

    # --- Camera Control Actions ---
    def ptz_move(self, direction: str) -> None:
        self._preset_autopan_token += 1
        self._person_detector.notify_camera_motion(1.6)
        if self._latest_detections:
            self._latest_detections = []
            self.detections_ready.emit(self.config.cam_id, [])
        if self.config.protocol == "demo":
            self._demo_target_pan = None
            self._demo_target_tilt = None
            speed = 2.8
            if direction == "up":
                self._demo_tilt_vel = -speed
            elif direction == "down":
                self._demo_tilt_vel = speed
            elif direction == "left":
                self._demo_pan_vel = -speed
            elif direction == "right":
                self._demo_pan_vel = speed
            elif direction == "stop":
                self._demo_pan_vel = 0.0
                self._demo_tilt_vel = 0.0
            return

        client = self._lan_client
        if client is None:
            return
        if direction == "up":
            client.ptz_up()
        elif direction == "down":
            client.ptz_down()
        elif direction == "left":
            client.ptz_left()
        elif direction == "right":
            client.ptz_right()
        elif direction == "stop":
            client.ptz_stop()

    def ptz_preset_set(self, slot_id: int) -> Tuple[float, float]:
        """Save current PTZ position into hardware preset slot (0..5)."""
        self._preset_autopan_token += 1
        if self.config.protocol == "demo":
            return (round(self._demo_pan, 2), round(self._demo_tilt, 2))
        if self._lan_client is not None:
            self._lan_client.ptz_preset_set(slot_id)
        return (0.0, 0.0)

    def _get_live_gray320(self) -> Optional[np.ndarray]:
        jpg = self._latest_jpeg_bytes
        if not jpg:
            return None
        try:
            arr = np.frombuffer(jpg, dtype=np.uint8)
            im = cv2.imdecode(arr, cv2.IMREAD_GRAYSCALE)
            if im is not None:
                return cv2.resize(im, (320, 180))
        except Exception:
            pass
        return None

    def _visual_autopan_fallback(self, token: int, slot_id: int, thumb_path: Optional[str]) -> None:
        """
        Wait for native hardware SetPTZXParam (0xAB, 103, slot) recall to finish (~2.0s),
        then verify alignment against the saved preset thumbnail and step-pan if still offset.
        """
        for _ in range(10):
            if self._stop_flag.is_set() or self._preset_autopan_token != token:
                return
            time.sleep(0.2)

        if not thumb_path or not os.path.isfile(thumb_path):
            return

        try:
            ref_gray = cv2.imread(thumb_path, cv2.IMREAD_GRAYSCALE)
            if ref_gray is None:
                return
            ref_gray = cv2.resize(ref_gray, (320, 180))
            tpl = ref_gray[45:135, 80:240]
        except Exception:
            return

        stall_count = 0
        prev_err = 9999.0
        for _ in range(28):
            if self._stop_flag.is_set() or self._preset_autopan_token != token:
                return
            client = self._lan_client
            if client is None:
                return
            live = self._get_live_gray320()
            if live is None:
                return
            res = cv2.matchTemplate(live, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            if max_val < 0.45:
                return

            dx = max_loc[0] - 80
            dy = max_loc[1] - 45
            err = math.hypot(dx, dy)
            if abs(dx) <= 6 and abs(dy) <= 6:
                return

            if err >= prev_err - 0.5:
                stall_count += 1
                if stall_count >= 5:
                    return
            else:
                stall_count = 0
            prev_err = min(prev_err, err)

            self._person_detector.notify_camera_motion(1.2)
            if abs(dx) >= abs(dy):
                if dx < -6:
                    client.ptz_left()
                elif dx > 6:
                    client.ptz_right()
            else:
                if dy < -6:
                    client.ptz_up()
                elif dy > 6:
                    client.ptz_down()

            time.sleep(0.24)

    def ptz_preset_goto(
        self,
        slot_id: int,
        demo_coords: Optional[Tuple[float, float]] = None,
        thumb_path: Optional[str] = None,
    ) -> None:
        """Auto-pan/tilt camera to hardware preset slot (0..5)."""
        self._preset_autopan_token += 1
        token = self._preset_autopan_token
        self._person_detector.notify_camera_motion(3.5)
        if self._latest_detections:
            self._latest_detections = []
            self.detections_ready.emit(self.config.cam_id, [])
        if self.config.protocol == "demo":
            self._demo_pan_vel = 0.0
            self._demo_tilt_vel = 0.0
            if demo_coords is not None:
                self._demo_target_pan = float(demo_coords[0])
                self._demo_target_tilt = float(demo_coords[1])
            return
        if self._lan_client is not None:
            self._lan_client.ptz_preset_goto(slot_id)
            if thumb_path:
                threading.Thread(
                    target=self._visual_autopan_fallback,
                    args=(token, int(slot_id), thumb_path),
                    daemon=True,
                ).start()

    def ptz_preset_clear(self, slot_id: int) -> None:
        """Clear hardware preset slot (0..5)."""
        if self.config.protocol != "demo" and self._lan_client is not None:
            self._lan_client.ptz_preset_clear(slot_id)

    def capture_preset_thumbnail(self, output_dir: Path, slot_id: int) -> Optional[str]:
        """Save a 320x180 JPEG thumbnail of the current live frame for a PTZ preset slot."""
        qimg = self._latest_qimage
        if qimg is None or qimg.isNull():
            return None
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_id = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in (self.config.cam_id or "cam"))
            filepath = output_dir / f"preset_{safe_id}_slot{int(slot_id)}.jpg"
            scaled = qimg.scaled(320, 180)
            if scaled.save(str(filepath), "JPG", 90):
                return str(filepath)
        except Exception:
            pass
        return None

    def set_light(self, mode: str) -> None:
        if self.config.protocol == "demo":
            self._demo_light_mode = mode
            return
        client = self._lan_client
        if client is None:
            return
        if mode == "on":
            client.light_on()
        elif mode == "off":
            client.light_off()
        else:
            client.light_auto()

    def set_image_color(self, mode: str) -> None:
        if self.config.protocol == "demo":
            self._demo_image_mode = mode
            return
        client = self._lan_client
        if client is None:
            return
        if mode == "color":
            client.image_color()
        elif mode == "bw":
            client.image_bw()
        else:
            client.image_auto()

    def flip_image(self) -> None:
        if self.config.protocol == "demo":
            self._demo_flipped = not self._demo_flipped
            return
        if self._lan_client is not None:
            self._lan_client.image_flip()

    # --- Snapshot & Recording ---
    def take_snapshot(self, output_dir: Path) -> Optional[str]:
        try:
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(
                c if c.isalnum() or c in ("-", "_") else "_" for c in self.config.name
            ).strip("_") or "Camera"
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = output_dir / f"{safe_name}_{ts}.jpg"

            # 1. Prefer pristine JPEG bytes from stream decode (lossless, instant, zero recompression)
            raw_bytes = getattr(self, "_latest_jpeg_bytes", None)
            if raw_bytes and len(raw_bytes) > 200:
                filepath.write_bytes(raw_bytes)
                return str(filepath)

            # 2. Fallback to QImage
            qimg = self._latest_qimage
            if qimg is not None and not qimg.isNull():
                if qimg.save(str(filepath), "JPG", 95) or qimg.save(str(filepath), "JPEG", 95):
                    return str(filepath)
                # Try saving as PNG if JPEG writer plugin is missing
                filepath_png = output_dir / f"{safe_name}_{ts}.png"
                if qimg.save(str(filepath_png), "PNG"):
                    return str(filepath_png)
        except Exception as exc:
            logger.warning("take_snapshot failed: %s", exc)
        return None

    def start_recording(self, output_dir: Path) -> Optional[str]:
        with self._rec_lock:
            if self._video_writer is not None:
                return self._recording_path
            output_dir.mkdir(parents=True, exist_ok=True)
            safe_name = "".join(c if c.isalnum() or c in ("-", "_") else "_" for c in self.config.name)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            filepath = output_dir / f"{safe_name}_{ts}.mp4"
            self._recording_path = str(filepath)
            self._video_writer = None
            self._recording_active = True

        self.recording_changed.emit(self.config.cam_id, True, self._recording_path)
        return self._recording_path

    def stop_recording(self) -> Optional[str]:
        with self._rec_lock:
            was_active = self._recording_active
            self._recording_active = False
            writer = self._video_writer
            self._video_writer = None
            saved_path = self._recording_path
            self._recording_path = ""
            if writer is not None:
                try:
                    writer.release()
                except Exception:
                    pass
        if was_active:
            self.recording_changed.emit(self.config.cam_id, False, saved_path)
            return saved_path
        return None

    def is_recording(self) -> bool:
        return self._recording_active

    # --- Thread Execution ---
    def run(self) -> None:
        self._stop_flag.clear()
        self._last_stat_time = time.monotonic()

        # Launch dedicated background thread for AI person detection & recording
        # so the live video stream NEVER blocks or drops FPS waiting on inference.
        det_thread = threading.Thread(target=self._detection_worker_loop, daemon=True)
        det_thread.start()

        try:
            proto = (self.config.protocol or "lan8800").lower()
            if proto == "demo":
                self._run_demo_loop()
            elif proto == "rtsp":
                self._run_rtsp_loop()
            else:
                self._run_lan_loop()
        except Exception as exc:
            self.status_changed.emit(self.config.cam_id, f"Stream Error: {exc}", False)
        finally:
            self._det_event.set()
            self._stop_ffmpeg()
            self.stop_recording()
            self.status_changed.emit(self.config.cam_id, "Offline / Stopped", False)

    def _detection_worker_loop(self) -> None:
        """
        Asynchronous AI detection & recording loop.
        Always processes the latest available frame (dropping stale intermediate frames)
        so live video rendering runs at full native camera FPS with zero lag.
        """
        while not self._stop_flag.is_set():
            self._det_event.wait(0.20)
            self._det_event.clear()
            if self._stop_flag.is_set():
                break

            jpeg_data = self._pending_det_jpeg
            bgr = self._pending_det_bgr
            self._pending_det_jpeg = None
            self._pending_det_bgr = None

            if not self.config.person_detection and not self._recording_active:
                if self._latest_detections:
                    self._latest_detections = []
                    self.detections_ready.emit(self.config.cam_id, [])
                continue

            if bgr is None and jpeg_data is not None:
                now = time.monotonic()
                need_cv = self._recording_active or (
                    bool(self.config.person_detection)
                    and (now - self._person_detector._last_run_time) >= self._person_detector.min_interval
                )
                if not need_cv:
                    continue
                try:
                    arr = np.frombuffer(jpeg_data, dtype=np.uint8)
                    bgr = cv2.imdecode(arr, cv2.IMREAD_COLOR)
                except Exception:
                    bgr = None

            if bgr is None:
                continue

            try:
                if self.config.person_detection:
                    boxes = self._person_detector.process_bgr(
                        bgr,
                        enabled=True,
                        sensitivity=getattr(self.config, "detection_sensitivity", "ultra"),
                        use_gpu=bool(getattr(self.config, "gpu_acceleration", True)),
                    )
                    if boxes != self._latest_detections:
                        self._latest_detections = boxes
                        self.detections_ready.emit(self.config.cam_id, boxes)
                elif self._latest_detections:
                    self._latest_detections = []
                    self.detections_ready.emit(self.config.cam_id, [])

                if self._recording_active:
                    rec_bgr = ClassicalPersonDetector.draw_boxes_on_bgr(bgr, self._latest_detections)
                    self._write_recording_frame(rec_bgr)
            except Exception:
                pass

    # --- Mode 1: LAN Port 8800 Protocol ---
    def _run_lan_loop(self) -> None:
        def on_status(msg: str) -> None:
            connected = msg.startswith("Streaming")
            if connected:
                self._last_stat_time = time.monotonic()
                self._frames_window = 0
                self._bytes_window = 0
            else:
                self._seen_keyframe = False
            self.status_changed.emit(self.config.cam_id, msg, connected)

        def on_frame(frame: CameraFrame) -> None:
            if self._stop_flag.is_set():
                return
            self._bytes_window += len(frame.payload)

            if frame.is_audio:
                if frame.audio_codec == "aac":
                    self._feed_aac_to_ffmpeg(frame.payload)
                elif frame.audio_codec == "alaw":
                    pcm = decode_alaw_to_pcm16(frame.payload)
                    if pcm:
                        level = self._compute_pcm_level(pcm)
                        self.audio_ready.emit(self.config.cam_id, pcm, level)
                else:
                    pcm = decode_adpcm_to_pcm16(frame.payload)
                    if pcm:
                        level = self._compute_pcm_level(pcm)
                        self.audio_ready.emit(self.config.cam_id, pcm, level)
                return

            # Wait for first keyframe so FFmpeg always starts with VPS/SPS/PPS headers
            is_first_kf = False
            if not self._seen_keyframe:
                if not frame.is_keyframe:
                    return
                self._seen_keyframe = True
                is_first_kf = True
                if frame.codec != VideoCodec.UNKNOWN:
                    self._active_codec = frame.codec
                else:
                    self._active_codec = VideoCodec.H265 if frame.raw_type in (0x28, 0x29) else VideoCodec.H264

            codec = self._active_codec if self._active_codec != VideoCodec.UNKNOWN else VideoCodec.H264
            self._feed_nal_to_ffmpeg(frame.payload, codec, is_first_keyframe=is_first_kf)

        self._lan_client = LanCameraClient(
            ip=self.config.ip,
            port=self.config.port,
            device_id=self.config.device_id,
            username=self.config.username,
            password=self.config.password,
            stream_quality=self.config.quality,
            enable_audio=self.config.audio_enabled,
            on_frame=on_frame,
            on_status=on_status,
        )
        self._lan_client.run_forever()

    def _ensure_ffmpeg_started(self, codec: VideoCodec) -> None:
        with self._ffmpeg_lock:
            if self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None:
                return
            self._stop_ffmpeg_video_unlocked()
            self._active_codec = codec
            self._decoded_any_frame = False
            fmt = "hevc" if codec == VideoCodec.H265 else "h264"

            # Use low-latency software decode for MJPEG pipe (avoids GPU->CPU hwdownload stall
            # and reserves 100% of GPU bandwidth for DirectML YOLOv8 AI inference).
            cmd = [
                get_ffmpeg_executable(),
                "-hide_banner",
                "-loglevel", "error",
                "-probesize", "320000",
                "-analyzeduration", "0",
                "-fpsprobesize", "0",
                "-fflags", "nobuffer",
                "-flags", "low_delay",
                "-f", fmt,
                "-i", "pipe:0",
                "-f", "image2pipe",
                "-vcodec", "mjpeg",
                "-q:v", "3",
                "pipe:1",
            ]

            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                self._ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    creationflags=creationflags,
                )
                reader_thread = threading.Thread(
                    target=self._ffmpeg_jpeg_reader_loop,
                    args=(self._ffmpeg_proc, False),
                    daemon=True,
                )
                reader_thread.start()
            except Exception as exc:
                self.status_changed.emit(self.config.cam_id, f"FFmpeg error: {exc}", False)

    def _feed_nal_to_ffmpeg(
        self, nal_bytes: bytes, codec: VideoCodec, is_first_keyframe: bool = False
    ) -> None:
        self._ensure_ffmpeg_started(codec)
        proc = self._ffmpeg_proc
        if proc is not None and proc.stdin is not None:
            try:
                aud = (
                    b"\x00\x00\x00\x01\x46\x01\x50"
                    if codec == VideoCodec.H265
                    else b"\x00\x00\x00\x01\x09\xf0"
                )
                pkt = nal_bytes + aud
                if is_first_keyframe and len(pkt) > 0:
                    repeats = max(2, (340000 // len(pkt)) + 1)
                    proc.stdin.write(pkt * repeats)
                else:
                    proc.stdin.write(pkt)
                proc.stdin.flush()
            except Exception:
                self._seen_keyframe = False
                with self._ffmpeg_lock:
                    self._stop_ffmpeg_video_unlocked()

    def _ffmpeg_jpeg_reader_loop(self, proc: subprocess.Popen, used_hwaccel: bool = False) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        buf = bytearray()
        while not self._stop_flag.is_set() and proc.poll() is None:
            try:
                chunk = stdout.read(32768)
                if not chunk:
                    break
                buf.extend(chunk)

                while True:
                    soi = buf.find(b"\xff\xd8")
                    if soi < 0:
                        if len(buf) > 1048576:
                            buf.clear()
                        break
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if eoi < 0:
                        if soi > 0:
                            del buf[:soi]
                        break

                    jpeg_data = bytes(buf[soi : eoi + 2])
                    del buf[: eoi + 2]
                    self._decoded_any_frame = True
                    self._process_decoded_jpeg(jpeg_data)
            except Exception:
                break

        # If hardware acceleration exited without producing a frame, seamlessly fall back to CPU decode
        if used_hwaccel and not self._decoded_any_frame and not self._stop_flag.is_set():
            self._hwaccel_failed = True
            self._seen_keyframe = False

    def _process_decoded_jpeg(self, jpeg_data: bytes) -> None:
        qimg = QImage.fromData(jpeg_data, "JPG")
        if qimg.isNull():
            return

        self._latest_qimage = qimg
        self._latest_jpeg_bytes = jpeg_data
        self._frames_window += 1
        self._update_telemetry()

        # Emit video frame immediately at full native FPS with zero AI blocking
        self.frame_ready.emit(self.config.cam_id, qimg, self._current_fps, self._current_kbps)

        # Hand off latest JPEG to background AI detection / recording worker
        if self.config.person_detection or self._recording_active:
            self._pending_det_jpeg = jpeg_data
            self._det_event.set()
        elif self._latest_detections:
            self._latest_detections = []
            self.detections_ready.emit(self.config.cam_id, [])

    def _feed_aac_to_ffmpeg(self, aac_bytes: bytes) -> None:
        with self._ffmpeg_lock:
            if self._ffmpeg_audio_proc is None or self._ffmpeg_audio_proc.poll() is not None:
                self._stop_ffmpeg_audio_unlocked()
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                try:
                    self._ffmpeg_audio_proc = subprocess.Popen(
                        [
                            get_ffmpeg_executable(),
                            "-hide_banner",
                            "-loglevel", "error",
                            "-probesize", "512",
                            "-analyzeduration", "0",
                            "-fflags", "nobuffer",
                            "-f", "aac",
                            "-i", "pipe:0",
                            "-f", "s16le",
                            "-ar", "8000",
                            "-ac", "1",
                            "pipe:1",
                        ],
                        stdin=subprocess.PIPE,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL,
                        bufsize=0,
                        creationflags=creationflags,
                    )
                    threading.Thread(
                        target=self._ffmpeg_pcm_reader_loop,
                        args=(self._ffmpeg_audio_proc,),
                        daemon=True,
                    ).start()
                except Exception:
                    return

            aproc = self._ffmpeg_audio_proc
        if aproc is not None and aproc.stdin is not None:
            try:
                aproc.stdin.write(aac_bytes)
                aproc.stdin.flush()
            except Exception:
                with self._ffmpeg_lock:
                    self._stop_ffmpeg_audio_unlocked()

    def _ffmpeg_pcm_reader_loop(self, proc: subprocess.Popen) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        while not self._stop_flag.is_set() and proc.poll() is None:
            try:
                pcm = stdout.read(800)
                if not pcm:
                    break
                level = self._compute_pcm_level(pcm)
                self.audio_ready.emit(self.config.cam_id, pcm, level)
            except Exception:
                break

    def _stop_ffmpeg(self) -> None:
        with self._ffmpeg_lock:
            self._stop_ffmpeg_video_unlocked()
            self._stop_ffmpeg_audio_unlocked()

    def _stop_ffmpeg_video_unlocked(self) -> None:
        proc = self._ffmpeg_proc
        self._ffmpeg_proc = None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass

    def _stop_ffmpeg_audio_unlocked(self) -> None:
        proc = self._ffmpeg_audio_proc
        self._ffmpeg_audio_proc = None
        if proc is not None:
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                proc.kill()
            except Exception:
                pass

    # --- Mode 2: RTSP Stream ---
    def _run_rtsp_loop(self) -> None:
        url = self.config.rtsp_url.strip()
        if not url:
            auth_part = f"{self.config.username}:{self.config.password}@" if self.config.username else ""
            stream_path = "ch00_1" if self.config.quality == 1 else "ch00_0"
            url = f"rtsp://{auth_part}{self.config.ip}:554/live/{stream_path}"

        while not self._stop_flag.is_set():
            self.status_changed.emit(self.config.cam_id, "Connecting RTSP Stream...", False)
            os.environ["OPENCV_FFMPEG_CAPTURE_OPTIONS"] = "rtsp_transport;tcp|fflags;nobuffer"
            cap = cv2.VideoCapture(url, cv2.CAP_FFMPEG)
            if not cap.isOpened():
                self.status_changed.emit(self.config.cam_id, "RTSP Connection Failed — Retrying...", False)
                self._stop_flag.wait(3.0)
                continue

            self.status_changed.emit(self.config.cam_id, "Streaming (RTSP Live)", True)
            while not self._stop_flag.is_set():
                ret, bgr = cap.read()
                if not ret or bgr is None:
                    break
                self._bytes_window += bgr.nbytes // 12
                self._frames_window += 1
                self._update_telemetry()

                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                self._latest_qimage = qimg
                self.frame_ready.emit(self.config.cam_id, qimg, self._current_fps, self._current_kbps)

                if self.config.person_detection or self._recording_active:
                    self._pending_det_bgr = bgr
                    self._det_event.set()
                elif self._latest_detections:
                    self._latest_detections = []
                    self.detections_ready.emit(self.config.cam_id, [])

            cap.release()
            if not self._stop_flag.is_set():
                self.status_changed.emit(self.config.cam_id, "RTSP Lost — Reconnecting...", False)
                self._stop_flag.wait(2.0)

    # --- Mode 3: Interactive Simulator (Demo Mode) ---
    def _run_demo_loop(self) -> None:
        self.status_changed.emit(self.config.cam_id, "Connecting Camera Simulator...", False)
        self._stop_flag.wait(0.35)
        if self._stop_flag.is_set():
            return

        res_label = "HD 1080p" if self.config.quality == 1 else "SD 360p"
        self.status_changed.emit(self.config.cam_id, f"Streaming ({res_label} • Port 8800 Sim)", True)

        seed = sum(ord(c) for c in (self.config.cam_id + self.config.name))
        rng = np.random.default_rng(seed)
        stars = rng.integers(0, 1000, size=(45, 2))
        t0 = time.monotonic()
        audio_phase = 0.0

        while not self._stop_flag.is_set():
            loop_start = time.monotonic()
            elapsed = loop_start - t0

            w, h = (960, 540) if self.config.quality == 1 else (640, 360)

            if self._demo_target_pan is not None and self._demo_target_tilt is not None:
                dp = self._demo_target_pan - self._demo_pan
                dt_ang = self._demo_target_tilt - self._demo_tilt
                if abs(dp) < 1.5 and abs(dt_ang) < 1.5:
                    self._demo_pan = self._demo_target_pan
                    self._demo_tilt = self._demo_target_tilt
                    self._demo_target_pan = None
                    self._demo_target_tilt = None
                else:
                    self._demo_pan += max(-5.0, min(5.0, dp * 0.18))
                    self._demo_tilt += max(-3.5, min(3.5, dt_ang * 0.18))
            else:
                self._demo_pan = max(-180.0, min(180.0, self._demo_pan + self._demo_pan_vel))
                self._demo_tilt = max(-65.0, min(65.0, self._demo_tilt + self._demo_tilt_vel))

            bgr = self._render_demo_scene(w, h, elapsed, seed, stars)

            if self._demo_flipped:
                bgr = cv2.flip(bgr, -1)

            self._frames_window += 1
            self._bytes_window += 8200 if self.config.quality == 1 else 3400
            self._update_telemetry()

            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            self._latest_qimage = qimg
            self.frame_ready.emit(self.config.cam_id, qimg, self._current_fps, self._current_kbps)

            if self.config.person_detection or self._recording_active:
                self._pending_det_bgr = bgr
                self._det_event.set()
            elif self._latest_detections:
                self._latest_detections = []
                self.detections_ready.emit(self.config.cam_id, [])

            if self.config.audio_enabled:
                num_samples = 400
                t_arr = np.arange(num_samples, dtype=np.float32) / 8000.0 + audio_phase
                audio_phase += num_samples / 8000.0
                wave = 0.04 * np.sin(2 * math.pi * 220.0 * t_arr) + 0.02 * np.sin(2 * math.pi * 440.0 * t_arr)
                if int(elapsed * 2) % 9 == 0:
                    wave += 0.08 * np.sin(2 * math.pi * 880.0 * t_arr)
                pcm_arr = np.clip(wave * 32767.0, -32768, 32767).astype(np.int16)
                pcm_bytes = pcm_arr.tobytes()
                level = min(1.0, float(np.abs(wave).mean()) * 8.5)
                self.audio_ready.emit(self.config.cam_id, pcm_bytes, level)

            dt = time.monotonic() - loop_start
            sleep_sec = max(0.005, 0.05 - dt)
            self._stop_flag.wait(sleep_sec)

    def _render_demo_scene(
        self, w: int, h: int, elapsed: float, seed: int, stars: np.ndarray
    ) -> np.ndarray:
        _ = stars
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        pan_px = int((self._demo_pan / 180.0) * (w * 0.35))
        tilt_px = int((self._demo_tilt / 65.0) * (h * 0.25))
        horizon_y = int(h * 0.56) - tilt_px
        horizon_y = max(40, min(h - 40, horizon_y))

        variant = seed % 4
        sky_top = [(38, 24, 14), (42, 30, 18), (28, 34, 22), (35, 22, 28)][variant]
        sky_bot = [(78, 52, 32), (82, 64, 38), (58, 70, 48), (68, 48, 56)][variant]
        gnd_col = [(32, 46, 28), (38, 42, 44), (30, 38, 34), (36, 36, 42)][variant]

        y_sky = np.linspace(0, 1, horizon_y, dtype=np.float32)[:, None]
        sky_grad = (
            (1.0 - y_sky) * np.array(sky_top, dtype=np.float32)
            + y_sky * np.array(sky_bot, dtype=np.float32)
        ).astype(np.uint8)
        frame[:horizon_y, :] = sky_grad[:, None, :]
        frame[horizon_y:, :] = gnd_col

        for gx in range(-6, 7):
            top_x = w // 2 + gx * (w // 14) - pan_px
            bot_x = w // 2 + gx * (w // 4) - pan_px * 2
            cv2.line(frame, (top_x, horizon_y), (bot_x, h), (48, 65, 45), 1, cv2.LINE_AA)

        for gy in range(1, 6):
            yy = horizon_y + int((h - horizon_y) * (gy / 5.0) ** 1.4)
            cv2.line(frame, (0, yy), (w, yy), (45, 60, 42), 1)

        for b_idx in range(-3, 5):
            bx = (w // 2) + b_idx * (w // 5) - pan_px
            bw = w // 9
            bh = int(h * (0.14 + 0.04 * ((b_idx * 7 + seed) % 4)))
            by = horizon_y - bh
            cv2.rectangle(frame, (bx, by), (bx + bw, horizon_y), (42, 48, 56), -1)
            cv2.rectangle(frame, (bx, by), (bx + bw, horizon_y), (62, 72, 84), 1)
            for wy in range(by + 8, horizon_y - 6, 14):
                for wx in range(bx + 6, bx + bw - 6, 12):
                    if ((wx + wy + seed) % 3) != 0:
                        cv2.rectangle(frame, (wx, wy), (wx + 5, wy + 7), (120, 195, 235), -1)

        # Distant person walking near the horizon (tests far-range detection)
        far_x = int(w - ((elapsed * 26 + seed * 11) % (w + 80)) + 40) - pan_px // 2
        far_y = horizon_y + int((h - horizon_y) * 0.14)
        far_w, far_h = max(7, int(w * 0.018)), max(18, int(h * 0.068))
        cv2.rectangle(frame, (far_x, far_y - far_h), (far_x + far_w, far_y), (185, 205, 230), -1)
        cv2.circle(
            frame,
            (far_x + far_w // 2, far_y - far_h - max(3, int(h * 0.012))),
            max(3, int(h * 0.011)),
            (220, 230, 240),
            -1,
        )

        obj_x = int(((elapsed * 55 + seed * 17) % (w + 160)) - 80) - pan_px // 2
        obj_y = horizon_y + int((h - horizon_y) * 0.42)
        obj_w, obj_h = int(w * 0.08), int(h * 0.14)
        cv2.rectangle(frame, (obj_x, obj_y - obj_h), (obj_x + obj_w, obj_y), (70, 140, 230), -1)
        cv2.circle(frame, (obj_x + obj_w // 2, obj_y - obj_h - int(h * 0.03)), int(h * 0.025), (180, 200, 220), -1)
        cv2.rectangle(
            frame,
            (obj_x - 6, obj_y - obj_h - int(h * 0.06)),
            (obj_x + obj_w + 6, obj_y + 6),
            (0, 230, 118),
            2,
        )
        cv2.putText(
            frame,
            "MOTION",
            (obj_x - 4, obj_y - obj_h - int(h * 0.075)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.42,
            (0, 230, 118),
            1,
            cv2.LINE_AA,
        )

        if self._demo_light_mode == "on":
            overlay = np.zeros_like(frame, dtype=np.uint8)
            cv2.circle(overlay, (w // 2, h // 2), int(w * 0.48), (65, 65, 55), -1)
            frame = cv2.add(frame, overlay)

        if self._demo_image_mode == "bw" or self._demo_light_mode == "off":
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            frame = cv2.merge([gray, gray, gray])

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cv2.rectangle(frame, (8, 8), (245, 34), (0, 0, 0), -1)
        cv2.putText(
            frame,
            now_str,
            (14, 26),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.52,
            (240, 240, 240),
            1,
            cv2.LINE_AA,
        )

        ptz_str = f"{self.config.name} | Pan:{self._demo_pan:+.0f} Tilt:{self._demo_tilt:+.0f} | Light:{self._demo_light_mode.upper()}"
        cv2.rectangle(frame, (8, h - 34), (min(w - 8, 14 + len(ptz_str) * 8), h - 8), (0, 0, 0), -1)
        cv2.putText(
            frame,
            ptz_str,
            (14, h - 15),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.45,
            (180, 225, 255),
            1,
            cv2.LINE_AA,
        )

        return frame

    # --- Helpers ---
    def _write_recording_frame(self, bgr: np.ndarray) -> None:
        with self._rec_lock:
            if not self._recording_active or not self._recording_path:
                return
            h, w = bgr.shape[:2]
            if self._video_writer is None:
                fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                self._video_writer = cv2.VideoWriter(self._recording_path, fourcc, 20.0, (w, h))
            try:
                self._video_writer.write(bgr)
            except Exception:
                pass

    def _update_telemetry(self) -> None:
        now = time.monotonic()
        dt = now - self._last_stat_time
        if dt >= 1.0:
            self._current_fps = self._frames_window / dt
            self._current_kbps = (self._bytes_window * 8.0 / 1000.0) / dt
            self._frames_window = 0
            self._bytes_window = 0
            self._last_stat_time = now

    _audio_floor_state: Dict[int, List[float]] = {}

    @classmethod
    def _compute_pcm_level(cls, pcm_bytes: bytes) -> float:
        """
        High-sensitivity acoustic level detector (0.0 .. 1.0) for 16-bit 8 kHz PCM.
        Combines DC removal, 1st/2nd-order high-pass acoustic band extraction (isolating
        faint whispers, taps, footsteps, and voices from low-frequency mic hum),
        96th-percentile micro-transient peak envelope detection, per-thread adaptive
        noise-floor subtraction, and a logarithmic + power-law compression curve.
        """
        if len(pcm_bytes) < 4:
            return 0.0
        arr = np.frombuffer(pcm_bytes, dtype=np.int16).astype(np.float32) * (1.0 / 32768.0)
        if arr.size == 0:
            return 0.0

        # Remove static DC bias so constant codec/ADC offset does not mask AC sound
        centered = arr - float(np.mean(arr))
        rms = float(np.sqrt(np.mean(centered * centered)))

        # High-pass acoustic energy (suppresses <150 Hz power-supply hum and highlights
        # 300-3800 Hz faint whispers, footsteps, clicks, rustling, and speech)
        if centered.size > 2:
            hp1 = np.diff(centered)
            hp2 = np.diff(hp1)
            hp_rms = float(np.sqrt(np.mean(hp1 * hp1))) * 0.80 + float(np.sqrt(np.mean(hp2 * hp2))) * 0.50
            peak_env = max(
                float(np.percentile(np.abs(centered), 96)) * 0.60,
                float(np.percentile(np.abs(hp1), 96)) * 0.72,
            )
        else:
            hp_rms = rms
            peak_env = float(np.max(np.abs(centered))) * 0.60

        tid = threading.get_ident()
        state = cls._audio_floor_state.get(tid)
        if state is None:
            state = [
                max(0.00018, min(rms, 0.015)),
                max(0.00015, min(hp_rms, 0.012)),
                max(0.00022, min(peak_env, 0.018)),
            ]
            cls._audio_floor_state[tid] = state
            if len(cls._audio_floor_state) > 64:
                cls._audio_floor_state.pop(next(iter(cls._audio_floor_state)))
        else:
            vals = (rms, hp_rms, peak_env)
            mins = (0.00018, 0.00015, 0.00022)
            maxs = (0.030, 0.025, 0.035)
            for idx in range(3):
                v = vals[idx]
                f = state[idx]
                if v <= f * 1.04:
                    # Fast downward adaptation to lock onto the quietest ambient floor
                    f = f * 0.80 + v * 0.20
                elif v < f * 1.32 and v < maxs[idx]:
                    # Slow drift only for stationary background hiss (never climbs during speech/transients)
                    f = f * 0.988 + v * 0.012
                state[idx] = max(mins[idx], min(f, maxs[idx]))

        f_rms, f_hp, f_pk = state[0], state[1], state[2]

        # Acoustic excitation above the camera microphone's steady noise floor
        d_rms = max(0.0, rms - f_rms * 1.04)
        d_hp = max(0.0, hp_rms - f_hp * 1.04)
        d_pk = max(0.0, peak_env - f_pk * 1.05)
        delta = max(d_rms * 1.15, d_hp * 1.45, d_pk * 1.30)

        # Relative SNR jump above the ambient floor (catches faint sounds riding on top of mic hiss)
        rel_jump = max(
            d_rms / max(0.00035, f_rms),
            d_hp / max(0.00030, f_hp),
            d_pk / max(0.00045, f_pk),
        )

        # High-sensitivity non-linear compression so faint sounds (~ -64 dBFS) clearly register
        raw_sig = max(rms, hp_rms, peak_env)
        db = 20.0 * math.log10(max(raw_sig, 1e-6))
        abs_norm = max(0.0, min(1.0, (db + 68.0) / 48.0)) ** 0.68

        if delta > 0.00008 or rel_jump > 0.06:
            delta_norm = min(1.0, (delta * 85.0) ** 0.36)
            rel_norm = min(1.0, (rel_jump * 1.10) ** 0.46)
            return float(min(1.0, max(delta_norm, rel_norm, abs_norm * 0.88)))

        return float(min(0.06, abs_norm * 0.14))


_CAMERA_SD_LOCKS_GUARD = threading.Lock()
_CAMERA_SD_LOCKS: Dict[str, threading.Lock] = {}
_CAMERA_SD_LAST_CLOSE: Dict[str, float] = {}


def _get_camera_sd_lock(ip: str) -> threading.Lock:
    key = (ip or "default").strip()
    with _CAMERA_SD_LOCKS_GUARD:
        lk = _CAMERA_SD_LOCKS.get(key)
        if lk is None:
            lk = threading.Lock()
            _CAMERA_SD_LOCKS[key] = lk
        return lk


def _wait_camera_sd_cooldown(ip: str, stop_event: threading.Event, min_gap_sec: float = 0.18) -> None:
    key = (ip or "default").strip()
    with _CAMERA_SD_LOCKS_GUARD:
        last_t = _CAMERA_SD_LAST_CLOSE.get(key, 0.0)
    rem = (last_t + min_gap_sec) - time.monotonic()
    if rem > 0.005:
        stop_event.wait(min(rem, min_gap_sec))


def _mark_camera_sd_closed(ip: str) -> None:
    key = (ip or "default").strip()
    with _CAMERA_SD_LOCKS_GUARD:
        _CAMERA_SD_LAST_CLOSE[key] = time.monotonic()


class SDCardListWorker(QThread):
    """Background worker that queries a camera's onboard microSD card for a calendar day's recordings."""

    finished_list = pyqtSignal(bool, str, list)  # (ok, summary_msg, List[RecordingSegment])

    def __init__(
        self,
        config: CameraConfig,
        year: int,
        month: int,
        day: int,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.year = int(year)
        self.month = int(month)
        self.day = int(day)
        self._stop_flag = threading.Event()
        self._client: Optional[LanCameraClient] = None

    def request_stop(self) -> None:
        self._stop_flag.set()
        if self._client is not None:
            self._client.stop()

    def run(self) -> None:
        proto = (self.config.protocol or "lan8800").lower()
        if proto == "demo":
            self._stop_flag.wait(0.25)
            if self._stop_flag.is_set():
                return
            day_dt = datetime(self.year, self.month, self.day, 0, 0, 0, tzinfo=timezone.utc)
            base_ts = int(day_dt.timestamp())
            segments: List[RecordingSegment] = []
            # Create realistic synthetic continuous + anomaly segments up to 08:30
            cur = base_ts + 3600
            seg_idx = 1
            for block in range(18):
                is_alarm = block in (3, 7, 11, 14, 16)
                is_motion = block in (5, 13)
                rtype = 3 if is_alarm else (2 if is_motion else 1)
                dur = 26 if rtype in (2, 3) else 600
                segments.append(
                    RecordingSegment(
                        seg_id=cur - 28800 + seg_idx,
                        raw_type=rtype,
                        start_ts=cur,
                        end_ts=cur + dur,
                    )
                )
                cur += dur + (12 if rtype in (2, 3) else 2)
                seg_idx += 1
            anom = sum(1 for s in segments if s.is_anomaly)
            if not self._stop_flag.is_set():
                self.finished_list.emit(True, f"{len(segments)} clips ({anom} anomalies • Demo SD)", segments)
            return

        client = LanCameraClient(
            ip=self.config.ip,
            port=self.config.port,
            device_id=self.config.device_id,
            username=self.config.username,
            password=self.config.password,
        )
        self._client = client
        if self._stop_flag.is_set():
            return
        with _get_camera_sd_lock(self.config.ip):
            if self._stop_flag.is_set():
                return
            _wait_camera_sd_cooldown(self.config.ip, self._stop_flag, 0.12)
            if self._stop_flag.is_set():
                return
            try:
                ok, msg, segments = client.list_sd_recordings(self.year, self.month, self.day)
            finally:
                _mark_camera_sd_closed(self.config.ip)
        if not self._stop_flag.is_set():
            self.finished_list.emit(ok, msg, segments)


class SDCardPlaybackWorker(QThread):
    """
    Streams and decodes either:
    1. An on-camera microSD card `RecordingSegment` (via `cmd 363`/`364` + FFmpeg pipe), OR
    2. A local `.mp4` recording file from `captures/recordings/`.
    Supports pause/resume, variable playback speed (1x, 2x, 4x, 8x, 16x), and audio output.
    """

    frame_ready = pyqtSignal(QImage, int, float)  # (qimg, current_ts_sec, fps)
    audio_ready = pyqtSignal(bytes, float)         # (pcm16_bytes, level_0_to_1)
    status_changed = pyqtSignal(str, bool)         # (status_text, is_playing)
    segment_finished = pyqtSignal()

    def __init__(
        self,
        config: CameraConfig,
        segment: Optional[RecordingSegment] = None,
        start_ts: Optional[int] = None,
        local_file: Optional[str] = None,
        speed: float = 1.0,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.segment = segment
        self.start_ts = start_ts
        self.local_file = local_file

        self._stop_flag = threading.Event()
        self._state_lock = threading.Lock()
        self._paused = False
        self._speed = max(0.5, float(speed))
        self._rebase_clock = False
        self._current_ts: int = int(start_ts or (segment.start_ts if segment else 0))

        self._lan_client: Optional[LanCameraClient] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None
        self._ffmpeg_reader_thread: Optional[threading.Thread] = None
        self._ffmpeg_audio_proc: Optional[subprocess.Popen] = None
        self._ffmpeg_lock = threading.Lock()
        self._active_codec: VideoCodec = VideoCodec.UNKNOWN
        self._seen_keyframe = False
        self._latest_qimage: Optional[QImage] = None
        self._frames_window = 0
        self._last_stat_time = time.monotonic()
        self._current_fps = 25.0

    @property
    def latest_qimage(self) -> Optional[QImage]:
        return self._latest_qimage

    def set_paused(self, paused: bool) -> None:
        with self._state_lock:
            self._paused = bool(paused)
            if not self._paused:
                self._rebase_clock = True

    def is_paused(self) -> bool:
        with self._state_lock:
            return self._paused

    def set_speed(self, speed: float) -> None:
        with self._state_lock:
            self._speed = max(0.5, float(speed))
            self._rebase_clock = True

    def request_stop(self) -> None:
        """
        Signal playback socket & FFmpeg pipeline to stop immediately without blocking the GUI thread.
        Never acquire _ffmpeg_lock or call stdin.close()/stdout.close() from the GUI thread on Windows,
        as closing an anonymous pipe while a worker thread is blocked in ReadFile/WriteFile deadlocks CRT.
        """
        self._stop_flag.set()
        client = self._lan_client
        if client is not None:
            client.stop()
        proc = self._ffmpeg_proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def stop_playback(self) -> None:
        self.request_stop()
        self.wait(600)

    def run(self) -> None:
        self._stop_flag.clear()
        try:
            if self.local_file:
                self._run_local_file_playback()
            elif self.segment is not None:
                if (self.config.protocol or "lan8800").lower() == "demo":
                    self._run_demo_sd_playback()
                else:
                    self._run_sd_segment_playback()
        finally:
            self._stop_ffmpeg()
            ff_t = self._ffmpeg_reader_thread
            if ff_t is not None and ff_t.is_alive():
                ff_t.join(timeout=0.5)

    def _run_local_file_playback(self) -> None:
        path = self.local_file or ""
        if not os.path.isfile(path):
            self.status_changed.emit("Local recording file not found", False)
            return

        cap = cv2.VideoCapture(path)
        if not cap.isOpened():
            self.status_changed.emit("Failed to open local video file", False)
            return

        fps = float(cap.get(cv2.CAP_PROP_FPS) or 25.0)
        if fps <= 1.0 or fps > 120.0:
            fps = 25.0

        seg = self.segment
        if seg is not None:
            offset_sec = 0
            if self.start_ts and self.start_ts > seg.start_ts:
                offset_sec = max(0, min(seg.duration_sec - 1, int(self.start_ts) - int(seg.start_ts)))
            if offset_sec > 0:
                cap.set(cv2.CAP_PROP_POS_MSEC, float(offset_sec) * 1000.0)
            self.status_changed.emit(
                f"Playing Local Backup ({seg.kind_label} • {seg.start_time_str})", True
            )
        else:
            if self.start_ts and self.start_ts > 0:
                cap.set(cv2.CAP_PROP_POS_MSEC, float(self.start_ts) * 1000.0)
            self.status_changed.emit(f"Playing Local File ({Path(path).name})", True)

        frame_idx = 0
        if seg is not None and offset_sec > 0:
            frame_idx = int(round(offset_sec * fps))

        anchor_wall = time.monotonic()
        anchor_frame = frame_idx

        try:
            while not self._stop_flag.is_set():
                with self._state_lock:
                    paused = self._paused
                    speed = self._speed
                    rebase = self._rebase_clock
                    if rebase:
                        self._rebase_clock = False
                if paused:
                    self._stop_flag.wait(0.04)
                    anchor_wall = time.monotonic()
                    anchor_frame = frame_idx
                    continue
                if rebase:
                    anchor_wall = time.monotonic()
                    anchor_frame = frame_idx

                elapsed = max(0.0, time.monotonic() - anchor_wall)
                target_frame = anchor_frame + int(elapsed * fps * speed)
                steps = max(1, target_frame - frame_idx)
                # Cap per-iteration grab burst so speed changes/stops remain responsive
                steps = min(steps, max(2, int(speed * 2)))

                grab_ok = True
                for _ in range(steps):
                    if not cap.grab():
                        grab_ok = False
                        break
                    frame_idx += 1

                if not grab_ok:
                    self.status_changed.emit(
                        "Local backup playback complete" if seg is not None else "End of local recording",
                        False,
                    )
                    self.segment_finished.emit()
                    break

                ret, bgr = cap.retrieve()
                if not ret or bgr is None:
                    self.status_changed.emit(
                        "Local backup playback complete" if seg is not None else "End of local recording",
                        False,
                    )
                    self.segment_finished.emit()
                    break

                pos_sec = int(frame_idx / fps)
                emit_ts = (int(seg.start_ts) + pos_sec) if seg is not None else pos_sec
                self._current_ts = emit_ts

                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                h, w, ch = rgb.shape
                qimg = QImage(rgb.data, w, h, ch * w, QImage.Format.Format_RGB888).copy()
                self._latest_qimage = qimg
                self.frame_ready.emit(qimg, emit_ts, fps * speed)

                # Wait until the next target frame time (capped at ~30 UI updates/sec for >1x speeds)
                next_frame_wall = anchor_wall + ((frame_idx + 1 - anchor_frame) / (fps * speed))
                min_ui_interval = 1.0 / 30.0 if speed > 1.0 else (1.0 / fps)
                wait_s = max(0.002, min(min_ui_interval, next_frame_wall - time.monotonic()))
                self._stop_flag.wait(wait_s)
        finally:
            cap.release()

    def _run_demo_sd_playback(self) -> None:
        seg = self.segment
        if seg is None:
            return
        cur_ts = float(self.start_ts if self.start_ts is not None else seg.start_ts)
        cur_ts = max(float(seg.start_ts), min(cur_ts, float(seg.end_ts)))
        self.status_changed.emit(f"Playing Demo SD Clip ({seg.start_time_str})", True)

        w, h = 960, 540
        while not self._stop_flag.is_set() and cur_ts <= float(seg.end_ts):
            with self._state_lock:
                paused = self._paused
                speed = self._speed
            if paused:
                self._stop_flag.wait(0.04)
                continue

            t0 = time.monotonic()
            frame = np.zeros((h, w, 3), dtype=np.uint8)
            frame[:, :] = (28, 34, 42)
            # Draw moving subject
            rel = cur_ts - float(seg.start_ts)
            px = int((rel * 65) % (w - 120)) + 40
            py = int(h * 0.62)
            col = (40, 60, 235) if seg.is_anomaly else (80, 175, 110)
            cv2.rectangle(frame, (px, py - 95), (px + 48, py), col, -1)
            cv2.circle(frame, (px + 24, py - 112), 14, (220, 225, 235), -1)
            if seg.is_anomaly:
                cv2.rectangle(frame, (px - 6, py - 132), (px + 54, py + 6), (0, 60, 255), 2)
                cv2.putText(
                    frame,
                    seg.kind_label,
                    (px - 4, py - 140),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    (80, 120, 255),
                    1,
                    cv2.LINE_AA,
                )

            ts_str = datetime.fromtimestamp(int(cur_ts), timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            cv2.rectangle(frame, (10, 10), (360, 40), (0, 0, 0), -1)
            cv2.putText(
                frame,
                f"SD PLAYBACK  {ts_str}",
                (18, 31),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.55,
                (240, 240, 245),
                1,
                cv2.LINE_AA,
            )

            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            qimg = QImage(rgb.data, w, h, 3 * w, QImage.Format.Format_RGB888).copy()
            self._latest_qimage = qimg
            self._current_ts = int(cur_ts)
            self.frame_ready.emit(qimg, int(cur_ts), 20.0 * speed)

            cur_ts += 0.05 * speed
            dt = time.monotonic() - t0
            self._stop_flag.wait(max(0.004, 0.05 - dt))

        if not self._stop_flag.is_set():
            self.status_changed.emit("Segment finished", False)
            self.segment_finished.emit()

    def _run_sd_segment_playback(self) -> None:
        seg = self.segment
        if seg is None:
            return

        from_ts = int(self.start_ts if self.start_ts is not None else seg.start_ts)
        from_ts = max(seg.start_ts, min(from_ts, max(seg.start_ts, seg.end_ts - 1)))
        ts_label = datetime.fromtimestamp(from_ts, timezone.utc).strftime("%I:%M:%S %p")
        self.status_changed.emit(f"Loading SD Card @ {ts_label}...", False)

        frame_q: "queue.Queue[Optional[CameraFrame]]" = queue.Queue(maxsize=650)
        stream_result: List[Tuple[bool, str]] = []

        def on_sd_frame(frame: CameraFrame) -> None:
            while not self._stop_flag.is_set():
                try:
                    frame_q.put(frame, timeout=0.15)
                    return
                except queue.Full:
                    continue

        client = LanCameraClient(
            ip=self.config.ip,
            port=self.config.port,
            device_id=self.config.device_id,
            username=self.config.username,
            password=self.config.password,
            enable_audio=True,
        )
        self._lan_client = client

        def reader_thread_fn() -> None:
            with _get_camera_sd_lock(self.config.ip):
                if self._stop_flag.is_set():
                    stream_result.append((False, "Cancelled"))
                else:
                    _wait_camera_sd_cooldown(self.config.ip, self._stop_flag, 0.16)
                    if self._stop_flag.is_set():
                        stream_result.append((False, "Cancelled"))
                    else:
                        try:
                            ok, msg = client.stream_sd_segment(
                                segment=seg,
                                start_ts=from_ts,
                                stop_event=self._stop_flag,
                                on_frame=on_sd_frame,
                            )
                            stream_result.append((ok, msg))
                        finally:
                            _mark_camera_sd_closed(self.config.ip)
            try:
                frame_q.put(None, timeout=0.5)
            except Exception:
                pass

        reader = threading.Thread(target=reader_thread_fn, daemon=True)
        reader.start()

        base_ts_ms: Optional[int] = None
        base_clock: float = time.monotonic()
        shown_video = 0

        while not self._stop_flag.is_set():
            with self._state_lock:
                paused = self._paused
            if paused:
                self._stop_flag.wait(0.04)
                continue

            try:
                frame = frame_q.get(timeout=0.15)
            except queue.Empty:
                if not reader.is_alive():
                    break
                continue

            if frame is None:
                break

            with self._state_lock:
                speed = self._speed
                if self._rebase_clock:
                    base_ts_ms = None
                    self._rebase_clock = False

            keyframe_scan = speed >= 16.0
            if keyframe_scan and not frame.is_audio and not frame.is_keyframe:
                if frame.timestamp > 0:
                    sec = int(frame.timestamp // 1000)
                    if seg.start_ts - 86400 <= sec <= seg.end_ts + 86400:
                        self._current_ts = sec
                continue

            fts = int(frame.timestamp)
            if fts > 0:
                if base_ts_ms is None or fts < base_ts_ms:
                    base_ts_ms = fts
                    base_clock = time.monotonic()
                elif not keyframe_scan:
                    target_time = base_clock + ((fts - base_ts_ms) / 1000.0 / speed)
                    wait_s = target_time - time.monotonic()
                    if wait_s > 3.0 or wait_s < -1.0:
                        base_ts_ms = fts
                        base_clock = time.monotonic()
                    elif wait_s > 0.002:
                        self._stop_flag.wait(wait_s)

            if frame.is_audio:
                if abs(speed - 1.0) < 0.05:
                    if frame.audio_codec == "alaw":
                        pcm = decode_alaw_to_pcm16(frame.payload)
                        if pcm:
                            lvl = CameraStreamWorker._compute_pcm_level(pcm)
                            self.audio_ready.emit(pcm, lvl)
                    elif frame.audio_codec == "adpcm":
                        pcm = decode_adpcm_to_pcm16(frame.payload)
                        if pcm:
                            lvl = CameraStreamWorker._compute_pcm_level(pcm)
                            self.audio_ready.emit(pcm, lvl)
                continue

            # Video frame
            is_first_kf = False
            if not self._seen_keyframe:
                if not frame.is_keyframe:
                    continue
                self._seen_keyframe = True
                is_first_kf = True
                self._active_codec = (
                    frame.codec if frame.codec != VideoCodec.UNKNOWN else VideoCodec.H265
                )

            if fts > 0:
                sec = int(fts // 1000)
                if seg.start_ts - 86400 <= sec <= seg.end_ts + 86400:
                    self._current_ts = sec

            if shown_video == 0 and not self._stop_flag.is_set():
                self.status_changed.emit(
                    f"Playing SD Card ({seg.kind_label} • {seg.start_time_str})", True
                )
            shown_video += 1
            codec = self._active_codec if self._active_codec != VideoCodec.UNKNOWN else VideoCodec.H265
            self._feed_nal_to_ffmpeg(frame.payload, codec, is_first_keyframe=is_first_kf)

        client.stop()
        if reader.is_alive():
            reader.join(timeout=0.5)

        if not self._stop_flag.is_set():
            if stream_result and not stream_result[0][0]:
                self.status_changed.emit(stream_result[0][1], False)
            else:
                self.status_changed.emit("Segment playback complete", False)
                self.segment_finished.emit()

    def _ensure_ffmpeg_started(self, codec: VideoCodec) -> None:
        with self._ffmpeg_lock:
            if self._stop_flag.is_set():
                return
            if self._ffmpeg_proc is not None and self._ffmpeg_proc.poll() is None:
                return
            self._stop_ffmpeg_video_unlocked()
            self._active_codec = codec
            fmt = "hevc" if codec == VideoCodec.H265 else "h264"
            cmd = [
                get_ffmpeg_executable(),
                "-hide_banner",
                "-loglevel",
                "error",
                "-probesize",
                "320000",
                "-analyzeduration",
                "0",
                "-fpsprobesize",
                "0",
                "-fflags",
                "nobuffer",
                "-flags",
                "low_delay",
                "-f",
                fmt,
                "-i",
                "pipe:0",
                "-f",
                "image2pipe",
                "-vcodec",
                "mjpeg",
                "-q:v",
                "3",
                "pipe:1",
            ]
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            try:
                self._ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    bufsize=0,
                    creationflags=creationflags,
                )
                t = threading.Thread(
                    target=self._ffmpeg_jpeg_reader_loop,
                    args=(self._ffmpeg_proc,),
                    daemon=True,
                )
                self._ffmpeg_reader_thread = t
                t.start()
            except Exception as exc:
                if not self._stop_flag.is_set():
                    self.status_changed.emit(f"FFmpeg decode error: {exc}", False)

    def _feed_nal_to_ffmpeg(
        self, nal_bytes: bytes, codec: VideoCodec, is_first_keyframe: bool = False
    ) -> None:
        if self._stop_flag.is_set():
            return
        self._ensure_ffmpeg_started(codec)
        proc = self._ffmpeg_proc
        if proc is not None and proc.stdin is not None:
            try:
                aud = (
                    b"\x00\x00\x00\x01\x46\x01\x50"
                    if codec == VideoCodec.H265
                    else b"\x00\x00\x00\x01\x09\xf0"
                )
                pkt = nal_bytes + aud
                if is_first_keyframe and len(pkt) > 0:
                    repeats = max(2, (340000 // len(pkt)) + 1)
                    proc.stdin.write(pkt * repeats)
                else:
                    proc.stdin.write(pkt)
                proc.stdin.flush()
            except Exception:
                self._seen_keyframe = False
                with self._ffmpeg_lock:
                    self._stop_ffmpeg_video_unlocked()

    def _ffmpeg_jpeg_reader_loop(self, proc: subprocess.Popen) -> None:
        stdout = proc.stdout
        if stdout is None:
            return
        buf = bytearray()
        while not self._stop_flag.is_set() and proc.poll() is None:
            try:
                chunk = stdout.read(32768)
                if not chunk or self._stop_flag.is_set():
                    break
                buf.extend(chunk)
                while not self._stop_flag.is_set():
                    soi = buf.find(b"\xff\xd8")
                    if soi < 0:
                        if len(buf) > 1048576:
                            buf.clear()
                        break
                    eoi = buf.find(b"\xff\xd9", soi + 2)
                    if eoi < 0:
                        if soi > 0:
                            del buf[:soi]
                        break
                    jpeg_data = bytes(buf[soi : eoi + 2])
                    del buf[: eoi + 2]
                    if self._stop_flag.is_set():
                        break
                    qimg = QImage.fromData(jpeg_data, "JPG")
                    if not qimg.isNull() and not self._stop_flag.is_set():
                        qimg_copy = qimg.copy()
                        self._latest_qimage = qimg_copy
                        self._frames_window += 1
                        now = time.monotonic()
                        dt = now - self._last_stat_time
                        if dt >= 1.0:
                            self._current_fps = self._frames_window / dt
                            self._frames_window = 0
                            self._last_stat_time = now
                        self.frame_ready.emit(qimg_copy, self._current_ts, self._current_fps)
            except Exception:
                break

    def _stop_ffmpeg(self) -> None:
        with self._ffmpeg_lock:
            self._stop_ffmpeg_video_unlocked()

    def _stop_ffmpeg_video_unlocked(self) -> None:
        proc = self._ffmpeg_proc
        self._ffmpeg_proc = None
        if proc is not None:
            # Always kill the child process FIRST on Windows so OS pipe handles break immediately
            # before closing Python pipe wrappers!
            try:
                proc.kill()
            except Exception:
                pass
            try:
                if proc.stdin:
                    proc.stdin.close()
            except Exception:
                pass
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass


class SDCardExportWorker(QThread):
    """
    Downloads a segment (or sub-range `[start_ts, end_ts]`) from the camera's microSD card
    and muxes the native H.264/H.265 stream directly into an `.mp4` file in `captures/recordings/`
    without re-encoding (`-c:v copy`).
    """

    progress_changed = pyqtSignal(int)             # 0..100
    export_finished = pyqtSignal(bool, str, str)   # (ok, message, output_path)

    def __init__(
        self,
        config: CameraConfig,
        segment: RecordingSegment,
        start_ts: int,
        end_ts: int,
        output_path: str,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.segment = segment
        self.start_ts = int(start_ts)
        self.end_ts = max(self.start_ts + 1, int(end_ts))
        self.output_path = output_path
        self._stop_flag = threading.Event()
        self._cancelled = threading.Event()
        self._client: Optional[LanCameraClient] = None
        self._ffmpeg_proc: Optional[subprocess.Popen] = None

    def request_stop(self) -> None:
        self.cancel()

    def cancel(self) -> None:
        self._cancelled.set()
        self._stop_flag.set()
        if self._client is not None:
            self._client.stop()
        proc = self._ffmpeg_proc
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self) -> None:
        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        total_sec = max(1.0, float(self.end_ts - self.start_ts))

        if (self.config.protocol or "lan8800").lower() == "demo":
            fourcc = cv2.VideoWriter_fourcc(*"mp4v")
            writer = cv2.VideoWriter(self.output_path, fourcc, 20.0, (960, 540))
            steps = int(min(120, total_sec * 10))
            for i in range(steps):
                if self._stop_flag.is_set():
                    writer.release()
                    try:
                        os.remove(self.output_path)
                    except Exception:
                        pass
                    if not self._cancelled.is_set():
                        self.export_finished.emit(False, "Export cancelled", "")
                    return
                frm = np.zeros((540, 960, 3), dtype=np.uint8)
                frm[:, :] = (30, 36, 44)
                cv2.putText(
                    frm,
                    f"DEMO SD EXPORT  {self.segment.start_time_str}",
                    (24, 42),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.7,
                    (240, 240, 245),
                    2,
                    cv2.LINE_AA,
                )
                writer.write(frm)
                if not self._cancelled.is_set():
                    self.progress_changed.emit(min(99, int((i + 1) * 100 / max(1, steps))))
                time.sleep(0.01)
            writer.release()
            if not self._cancelled.is_set():
                self.progress_changed.emit(100)
                self.export_finished.emit(True, f"Saved {Path(self.output_path).name}", self.output_path)
            return

        client = LanCameraClient(
            ip=self.config.ip,
            port=self.config.port,
            device_id=self.config.device_id,
            username=self.config.username,
            password=self.config.password,
            enable_audio=False,
        )
        self._client = client

        ffmpeg_proc: Optional[subprocess.Popen] = None
        seen_key = False
        video_frames_written = 0
        reached_end = False

        def on_export_frame(frame: CameraFrame) -> None:
            nonlocal ffmpeg_proc, seen_key, video_frames_written, reached_end
            if self._stop_flag.is_set() or frame.is_audio:
                return

            sec = int(frame.timestamp // 1000) if frame.timestamp > 0 else self.start_ts
            if sec >= self.end_ts and video_frames_written > 0:
                reached_end = True
                self._stop_flag.set()
                client.stop()
                return

            if not seen_key:
                if not frame.is_keyframe:
                    return
                seen_key = True
                fmt = "hevc" if frame.codec == VideoCodec.H265 else "h264"
                creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
                cmd = [
                    get_ffmpeg_executable(),
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-r",
                    "25",
                    "-f",
                    fmt,
                    "-i",
                    "pipe:0",
                    "-c:v",
                    "copy",
                    "-movflags",
                    "+faststart",
                    self.output_path,
                ]
                ffmpeg_proc = subprocess.Popen(
                    cmd,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=creationflags,
                )
                self._ffmpeg_proc = ffmpeg_proc

            if ffmpeg_proc is not None and ffmpeg_proc.stdin is not None:
                try:
                    ffmpeg_proc.stdin.write(frame.payload)
                    video_frames_written += 1
                    pct = int(min(98.0, max(1.0, ((sec - self.start_ts) / total_sec) * 100.0)))
                    if not self._cancelled.is_set():
                        self.progress_changed.emit(pct)
                except Exception:
                    self._stop_flag.set()

        with _get_camera_sd_lock(self.config.ip):
            if self._cancelled.is_set():
                return
            _wait_camera_sd_cooldown(self.config.ip, self._stop_flag, 0.22)
            if self._cancelled.is_set():
                return
            try:
                ok, msg = client.stream_sd_segment(
                    segment=self.segment,
                    start_ts=self.start_ts,
                    stop_event=self._stop_flag,
                    on_frame=on_export_frame,
                )
            finally:
                _mark_camera_sd_closed(self.config.ip)

        self._ffmpeg_proc = None
        if ffmpeg_proc is not None:
            try:
                if ffmpeg_proc.stdin:
                    ffmpeg_proc.stdin.close()
                ffmpeg_proc.wait(timeout=5.0)
            except Exception:
                try:
                    ffmpeg_proc.kill()
                except Exception:
                    pass

        if self._cancelled.is_set():
            try:
                if os.path.isfile(self.output_path):
                    os.remove(self.output_path)
            except Exception:
                pass
            return

        if video_frames_written > 0 and os.path.isfile(self.output_path) and os.path.getsize(self.output_path) > 512:
            self.progress_changed.emit(100)
            self.export_finished.emit(True, f"Saved {Path(self.output_path).name}", self.output_path)
        else:
            try:
                if os.path.isfile(self.output_path):
                    os.remove(self.output_path)
            except Exception:
                pass
            err_msg = msg if not ok else "No video frames received in selected range"
            self.export_finished.emit(False, f"Export failed: {err_msg}", "")


def _safe_camera_folder_name(cfg: CameraConfig) -> str:
    clean = "".join(c if c.isalnum() or c in ("-", "_", " ") else "_" for c in (cfg.name or "Camera")).strip()
    return clean.replace(" ", "_") or f"Cam_{cfg.device_id or cfg.cam_id}"


def get_segment_backup_path(backup_root: Path, cfg: CameraConfig, seg: RecordingSegment) -> Path:
    """
    Canonical local backup `.mp4` path for an SD card recording segment:
    `captures/recordings/backups/<CameraName>/<YYYY-MM-DD>/<HH-MM-SS>_<kind>_seg<id>.mp4`
    """
    cam_dir = _safe_camera_folder_name(cfg)
    day_dir = seg.iso_date_str
    t_stamp = datetime.fromtimestamp(seg.start_ts, timezone.utc).strftime("%H-%M-%S")
    fname = f"{t_stamp}_{seg.kind_code}_seg{seg.seg_id}.mp4"
    return backup_root / cam_dir / day_dir / fname


def find_segment_backup_file(backup_root: Path, cfg: CameraConfig, seg: RecordingSegment) -> Optional[Path]:
    """Return the local `.mp4` backup Path if `seg` has already been backed up to disk, else None."""
    target = get_segment_backup_path(backup_root, cfg, seg)
    if target.is_file() and target.stat().st_size > 512:
        return target
    day_folder = target.parent
    if day_folder.is_dir():
        for cand in day_folder.glob(f"*_seg{seg.seg_id}.mp4"):
            if cand.is_file() and cand.stat().st_size > 512:
                return cand
    return None


def save_segment_backup_meta(backup_root: Path, cfg: CameraConfig, segments: List[RecordingSegment]) -> None:
    """
    Persist exact segment metadata (`seg_id`, `raw_type`, `start_ts`, `end_ts`) in
    `captures/recordings/backups/<Camera>/<YYYY-MM-DD>/segments_meta.json` so backed-up clips
    can be listed and previewed immediately inside the On-Camera SD Card list on every app restart.
    """
    import json

    by_day: Dict[str, List[RecordingSegment]] = {}
    for s in segments:
        by_day.setdefault(s.iso_date_str, []).append(s)

    cam_dir = _safe_camera_folder_name(cfg)
    for day_str, day_segs in by_day.items():
        day_folder = backup_root / cam_dir / day_str
        if not day_folder.is_dir():
            continue
        meta_path = day_folder / "segments_meta.json"
        meta_map: Dict[str, dict] = {}
        if meta_path.is_file():
            try:
                loaded = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    meta_map = loaded
            except Exception:
                pass
        changed = False
        for seg in day_segs:
            key = str(seg.seg_id)
            entry = {
                "seg_id": int(seg.seg_id),
                "raw_type": int(seg.raw_type),
                "start_ts": int(seg.start_ts),
                "end_ts": int(seg.end_ts),
            }
            if meta_map.get(key) != entry:
                meta_map[key] = entry
                changed = True
        if changed:
            try:
                meta_path.write_text(json.dumps(meta_map, indent=2), encoding="utf-8")
            except Exception:
                pass


def load_local_backup_segments(
    backup_root: Path, cfg: CameraConfig, year: int, month: int, day: int
) -> List[RecordingSegment]:
    """
    Reconstruct `RecordingSegment` items for all backed-up `.mp4` files on `(year, month, day)`
    in `captures/recordings/backups/<Camera>/<YYYY-MM-DD>/`.
    Used by the On-Camera SD Card view so local backups are always previewed alongside SD card clips
    every time the app starts (even before the camera responds or if the SD card overwrites old clips).
    """
    import json
    import re

    day_str = f"{int(year):04d}-{int(month):02d}-{int(day):02d}"
    day_folder = backup_root / _safe_camera_folder_name(cfg) / day_str
    if not day_folder.is_dir():
        return []

    meta_path = day_folder / "segments_meta.json"
    meta_map: Dict[str, dict] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                meta_map = loaded
        except Exception:
            pass

    pat = re.compile(r"^(\d{2})-(\d{2})-(\d{2})_([a-zA-Z]+)_seg(\d+)\.mp4$", re.IGNORECASE)
    results: List[RecordingSegment] = []
    meta_dirty = False

    for mp4_file in sorted(day_folder.glob("*_seg*.mp4")):
        try:
            if not mp4_file.is_file() or mp4_file.stat().st_size <= 512:
                continue
        except Exception:
            continue

        m = pat.match(mp4_file.name)
        if not m:
            continue
        hh, mm, ss = int(m.group(1)), int(m.group(2)), int(m.group(3))
        kind_str = m.group(4).lower()
        seg_id = int(m.group(5))

        cached = meta_map.get(str(seg_id))
        if isinstance(cached, dict) and "start_ts" in cached and "end_ts" in cached:
            results.append(
                RecordingSegment(
                    seg_id=int(cached.get("seg_id", seg_id)),
                    raw_type=int(cached.get("raw_type", 3 if kind_str == "alarm" else (2 if kind_str == "motion" else 1))),
                    start_ts=int(cached["start_ts"]),
                    end_ts=int(cached["end_ts"]),
                )
            )
            continue

        raw_type = 3 if kind_str == "alarm" else (2 if kind_str == "motion" else 1)
        start_ts = int(datetime(int(year), int(month), int(day), hh, mm, ss, tzinfo=timezone.utc).timestamp())
        dur_sec = 25
        try:
            cap = cv2.VideoCapture(str(mp4_file))
            if cap.isOpened():
                fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
                fcount = float(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0.0)
                if fps > 1.0 and fcount > 0:
                    dur_sec = max(1, int(round(fcount / fps)))
            cap.release()
        except Exception:
            pass
        end_ts = start_ts + dur_sec
        seg = RecordingSegment(seg_id=seg_id, raw_type=raw_type, start_ts=start_ts, end_ts=end_ts)
        results.append(seg)
        meta_map[str(seg_id)] = {
            "seg_id": seg_id,
            "raw_type": raw_type,
            "start_ts": start_ts,
            "end_ts": end_ts,
        }
        meta_dirty = True

    if meta_dirty:
        try:
            meta_path.write_text(json.dumps(meta_map, indent=2), encoding="utf-8")
        except Exception:
            pass

    results.sort(key=lambda s: s.start_ts)
    return results


class SDCardBulkBackupWorker(QThread):
    """
    Downloads and backs up all SD card recording segments within a `[start_ts, end_ts]`
    date/time range (filtered by All / Anomalies Only / Normal Only) into
    `captures/recordings/backups/<Camera>/<YYYY-MM-DD>/`.
    """

    status_updated = pyqtSignal(str, int, int, int)  # (label, done_count, total_count, current_file_pct)
    segment_backed_up = pyqtSignal(int, str)         # (seg_id, saved_file_path)
    backup_finished = pyqtSignal(bool, str, int, str)  # (ok, summary_msg, saved_count, backup_folder)

    def __init__(
        self,
        config: CameraConfig,
        start_ts: int,
        end_ts: int,
        event_filter: str,
        backup_root: Path,
        skip_existing: bool = True,
        segments_override: Optional[List[RecordingSegment]] = None,
        parent: Optional[QObject] = None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.start_ts = int(min(start_ts, end_ts))
        self.end_ts = int(max(start_ts, end_ts))
        self.event_filter = (event_filter or "all").lower()
        self.backup_root = Path(backup_root)
        self.skip_existing = bool(skip_existing)
        self.segments_override = list(segments_override) if segments_override is not None else None
        self._stop_flag = threading.Event()
        self._active_exporter: Optional[SDCardExportWorker] = None

    def request_stop(self) -> None:
        self.cancel()

    def cancel(self) -> None:
        self._stop_flag.set()
        if self._active_exporter is not None:
            self._active_exporter.cancel()

    def run(self) -> None:
        cam_folder = self.backup_root / _safe_camera_folder_name(self.config)
        cam_folder.mkdir(parents=True, exist_ok=True)

        # 1. Gather candidate segments across the date range (or use override list)
        candidates: List[RecordingSegment] = []
        if self.segments_override is not None:
            candidates = list(self.segments_override)
        else:
            start_dt = datetime.fromtimestamp(self.start_ts, timezone.utc).date()
            end_dt = datetime.fromtimestamp(self.end_ts, timezone.utc).date()
            cur_dt = start_dt
            from datetime import timedelta

            while cur_dt <= end_dt and not self._stop_flag.is_set():
                self.status_updated.emit(
                    f"Scanning SD card ({cur_dt.strftime('%b %d, %Y')})...", 0, 0, 0
                )
                if (self.config.protocol or "lan8800").lower() == "demo":
                    lw = SDCardListWorker(self.config, cur_dt.year, cur_dt.month, cur_dt.day)
                    res_list: List[RecordingSegment] = []
                    lw.finished_list.connect(lambda ok, m, segs: res_list.extend(segs))
                    lw.run()
                    candidates.extend(res_list)
                else:
                    client = LanCameraClient(
                        ip=self.config.ip,
                        port=self.config.port,
                        device_id=self.config.device_id,
                        username=self.config.username,
                        password=self.config.password,
                    )
                    ok, _msg, day_segs = client.list_sd_recordings(cur_dt.year, cur_dt.month, cur_dt.day)
                    if ok and day_segs:
                        candidates.extend(day_segs)
                cur_dt += timedelta(days=1)

            # Filter by time range overlap and event type
            filtered: List[RecordingSegment] = []
            for seg in candidates:
                if seg.end_ts < self.start_ts or seg.start_ts > self.end_ts:
                    continue
                if self.event_filter == "anomaly" and not seg.is_anomaly:
                    continue
                if self.event_filter == "normal" and seg.is_anomaly:
                    continue
                filtered.append(seg)
            candidates = filtered

        if self._stop_flag.is_set():
            self.backup_finished.emit(False, "Backup cancelled", 0, str(cam_folder))
            return

        total = len(candidates)
        if total == 0:
            self.backup_finished.emit(
                False,
                "No matching SD card clips found in the selected date/time range",
                0,
                str(cam_folder),
            )
            return

        saved_count = 0
        skipped_count = 0
        last_folder = str(cam_folder)

        for idx, seg in enumerate(candidates):
            if self._stop_flag.is_set():
                break

            out_path = get_segment_backup_path(self.backup_root, self.config, seg)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            last_folder = str(out_path.parent)

            if self.skip_existing:
                existing = find_segment_backup_file(self.backup_root, self.config, seg)
                if existing is not None:
                    saved_count += 1
                    skipped_count += 1
                    save_segment_backup_meta(self.backup_root, self.config, [seg])
                    self.segment_backed_up.emit(seg.seg_id, str(existing))
                    self.status_updated.emit(
                        f"Already backed up ({idx + 1}/{total}): {seg.start_time_str}",
                        idx + 1,
                        total,
                        100,
                    )
                    continue

            label = f"Backing up {idx + 1}/{total}: {seg.start_time_str}"
            self.status_updated.emit(label, idx, total, 0)

            exporter = SDCardExportWorker(
                config=self.config,
                segment=seg,
                start_ts=seg.start_ts,
                end_ts=seg.end_ts,
                output_path=str(out_path),
            )
            self._active_exporter = exporter
            export_ok = [False]

            def on_pct(pct: int, cur_i: int = idx, cur_lbl: str = label) -> None:
                self.status_updated.emit(cur_lbl, cur_i, total, pct)

            def on_done(ok: bool, _m: str, path_str: str) -> None:
                export_ok[0] = ok and bool(path_str)

            exporter.progress_changed.connect(on_pct)
            exporter.export_finished.connect(on_done)
            exporter.run()
            self._active_exporter = None

            if export_ok[0] and out_path.is_file() and out_path.stat().st_size > 512:
                saved_count += 1
                save_segment_backup_meta(self.backup_root, self.config, [seg])
                self.segment_backed_up.emit(seg.seg_id, str(out_path))

        if self._stop_flag.is_set():
            self.backup_finished.emit(
                False,
                f"Backup stopped ({saved_count}/{total} clips saved)",
                saved_count,
                last_folder,
            )
        else:
            msg = f"Backed up {saved_count} of {total} clips to local storage"
            if skipped_count > 0:
                msg += f" ({skipped_count} already saved)"
            self.backup_finished.emit(saved_count > 0, msg, saved_count, last_folder)
