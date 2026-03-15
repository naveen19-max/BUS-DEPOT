from __future__ import annotations

import threading
import time
from datetime import datetime
from typing import Callable, Iterable, List

import cv2
import numpy as np


class CameraStream:
    def __init__(
        self,
        camera_index: int = 0,
        on_qr_scan: Callable[[str], None] | None = None,
        scan_cooldown_seconds: float = 4.0,
    ):
        self.camera_index = int(camera_index)
        self.on_qr_scan = on_qr_scan
        self.scan_cooldown_seconds = float(scan_cooldown_seconds)

        self.running = False
        self.worker = None
        self.capture = None

        self.qr_detector = cv2.QRCodeDetector()
        self.last_qr_code = ""
        self.last_qr_at = ""
        self.last_qr_emitted_at = 0.0
        self.capture_retry_at = 0.0

        self.last_frame_jpeg = self._placeholder("Starting camera...")
        self.last_frame_at = ""
        self.status = "Starting camera..."
        self.lock = threading.Lock()

    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self.worker = threading.Thread(target=self._loop, daemon=True)
        self.worker.start()

    def stop(self) -> None:
        self.running = False
        if self.worker and self.worker.is_alive():
            self.worker.join(timeout=2)
        self._release_capture()

    def restart(self, camera_index: int | None = None) -> None:
        if camera_index is not None:
            self.camera_index = int(camera_index)
        with self.lock:
            self.status = f"Restarting camera index {self.camera_index}..."
        self._release_capture()

    def get_state(self) -> dict:
        with self.lock:
            return {
                "camera_index": self.camera_index,
                "camera_status": self.status,
                "last_frame_at": self.last_frame_at,
                "last_qr_code": self.last_qr_code,
                "last_qr_at": self.last_qr_at,
            }

    def mjpeg_stream(self) -> Iterable[bytes]:
        while True:
            with self.lock:
                frame = self.last_frame_jpeg
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
            )
            time.sleep(0.08)

    def _loop(self) -> None:
        while self.running:
            if not self._ensure_capture():
                with self.lock:
                    self.last_frame_jpeg = self._placeholder(self.status)
                time.sleep(0.8)
                continue

            ok, frame = self.capture.read()
            if not ok:
                with self.lock:
                    self.status = "Camera read failed, retrying..."
                    self.last_frame_jpeg = self._placeholder(self.status)
                self._release_capture()
                time.sleep(0.4)
                continue

            overlay = frame.copy()
            height, width = overlay.shape[:2]
            roi_x1 = int(width * 0.18)
            roi_y1 = int(height * 0.16)
            roi_x2 = int(width * 0.82)
            roi_y2 = int(height * 0.84)

            cv2.rectangle(overlay, (roi_x1, roi_y1), (roi_x2, roi_y2), (92, 240, 175), 2)
            cv2.putText(
                overlay,
                "Scan driver/admin QR inside this box",
                (roi_x1 + 12, roi_y1 - 14),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.62,
                (156, 255, 213),
                2,
                cv2.LINE_AA
            )

            codes = self._scan_for_qr(overlay, roi_x1, roi_y1, roi_x2, roi_y2)
            for code in codes:
                self._emit_qr_code(code)

            cv2.rectangle(overlay, (0, 0), (overlay.shape[1], 56), (16, 20, 34), -1)
            cv2.putText(
                overlay,
                f"Bus Depot QR Monitor | Camera {self.camera_index}",
                (16, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.78,
                (241, 244, 255),
                2,
                cv2.LINE_AA
            )
            now_text = datetime.now().strftime("%d-%m-%Y %I:%M:%S %p")
            cv2.putText(
                overlay,
                now_text,
                (overlay.shape[1] - 350, 34),
                cv2.FONT_HERSHEY_SIMPLEX,
                0.58,
                (166, 212, 255),
                2,
                cv2.LINE_AA
            )

            with self.lock:
                latest_qr = self.last_qr_code
                latest_qr_at = self.last_qr_at
            if latest_qr:
                qr_text = f"Last QR: {latest_qr[:52]}"
                cv2.putText(
                    overlay,
                    qr_text,
                    (24, height - 48),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.62,
                    (236, 248, 255),
                    2,
                    cv2.LINE_AA
                )
                cv2.putText(
                    overlay,
                    latest_qr_at,
                    (24, height - 20),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.52,
                    (174, 217, 255),
                    1,
                    cv2.LINE_AA
                )

            ok, encoded = cv2.imencode(".jpg", overlay, [int(cv2.IMWRITE_JPEG_QUALITY), 82])
            if ok:
                with self.lock:
                    self.last_frame_jpeg = encoded.tobytes()
                    self.last_frame_at = datetime.now().isoformat(sep=" ", timespec="seconds")
                    self.status = "Camera connected."
            time.sleep(0.03)

    def _scan_for_qr(
        self,
        frame: np.ndarray,
        roi_x1: int,
        roi_y1: int,
        roi_x2: int,
        roi_y2: int
    ) -> List[str]:
        roi = frame[roi_y1:roi_y2, roi_x1:roi_x2]
        found: List[str] = []
        try:
            ok, decoded_infos, _, _ = self.qr_detector.detectAndDecodeMulti(roi)
            if ok and decoded_infos:
                for item in decoded_infos:
                    text = (item or "").strip()
                    if text:
                        found.append(text)
        except Exception:
            # Fall back to single decode path.
            pass

        if found:
            return found

        try:
            text, _, _ = self.qr_detector.detectAndDecode(roi)
            text = (text or "").strip()
            if text:
                return [text]
        except Exception:
            return []
        return []

    def _emit_qr_code(self, qr_code: str) -> None:
        code = (qr_code or "").strip()
        if not code:
            return

        now = time.time()
        with self.lock:
            if code == self.last_qr_code and (now - self.last_qr_emitted_at) < self.scan_cooldown_seconds:
                return
            self.last_qr_code = code
            self.last_qr_at = datetime.now().isoformat(sep=" ", timespec="seconds")
            self.last_qr_emitted_at = now

        if self.on_qr_scan:
            try:
                self.on_qr_scan(code)
            except Exception:
                # Keep capture loop alive even if callback fails.
                pass

    def _ensure_capture(self) -> bool:
        if self.capture is not None and self.capture.isOpened():
            return True

        if time.time() < self.capture_retry_at:
            return False

        capture = cv2.VideoCapture(self.camera_index)
        if not capture.isOpened():
            capture.release()
            capture = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if not capture.isOpened():
            capture.release()
            self.capture_retry_at = time.time() + 2.5
            with self.lock:
                self.status = f"Camera {self.camera_index} unavailable. Check index and permissions."
            return False

        capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        capture.set(cv2.CAP_PROP_FPS, 30)
        self.capture = capture
        self.capture_retry_at = 0.0
        with self.lock:
            self.status = "Camera connected."
        return True

    def _release_capture(self) -> None:
        if self.capture is not None:
            self.capture.release()
            self.capture = None

    def _placeholder(self, text: str) -> bytes:
        frame = np.zeros((720, 1280, 3), dtype=np.uint8)
        frame[:] = (22, 28, 44)
        cv2.putText(
            frame,
            "Bus Depot QR Scanner",
            (80, 220),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.8,
            (235, 242, 255),
            4,
            cv2.LINE_AA
        )
        cv2.putText(
            frame,
            text,
            (80, 300),
            cv2.FONT_HERSHEY_SIMPLEX,
            1.0,
            (150, 210, 255),
            2,
            cv2.LINE_AA
        )
        cv2.putText(
            frame,
            "Scan Driver QR for auto entry/exit. Scan Admin QR for secure pages.",
            (80, 360),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.95,
            (220, 224, 232),
            2,
            cv2.LINE_AA
        )
        ok, encoded = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 80])
        return encoded.tobytes() if ok else b""
