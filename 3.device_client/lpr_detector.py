"""
LPR(번호판 인식) 워커: 영상 프레임을 별도 스레드에서 YOLO + PaddleOCR 로 처리하여
실시간 영상 표시에 지연을 주지 않음.

- 모델: 3.device_client/lpr_models/best.pt (config.lpr_plate_model_path)
- submit_frame() 으로 프레임만 넘기고, 인식 결과는 result_ready 시그널로 전달
"""
from __future__ import annotations

import re
import threading
import time
from pathlib import Path
from typing import Any, Optional

from PyQt6.QtCore import QObject, pyqtSignal

# 선택 의존: 없으면 인식 비활성화
try:
    import cv2
    import numpy as np
    _CV2_AVAILABLE = True
except ImportError:
    _CV2_AVAILABLE = False

try:
    from ultralytics import YOLO
    _YOLO_AVAILABLE = True
except ImportError:
    YOLO = None
    _YOLO_AVAILABLE = False

try:
    from paddleocr import PaddleOCR
    _PADDLE_AVAILABLE = True
except ImportError:
    PaddleOCR = None
    _PADDLE_AVAILABLE = False


def _extract_plate_text_from_ocr_result(ocr_results: Any) -> str:
    """PaddleOCR 결과에서 번호판 문자만 추출."""
    text_parts = []
    if not ocr_results:
        return ""
    for line in ocr_results:
        if hasattr(line, "rec_texts") and line.rec_texts:
            text_parts.append("".join(line.rec_texts))
        elif isinstance(line, dict) and "rec_texts" in line:
            text_parts.append("".join(line["rec_texts"]))
        elif isinstance(line, (list, tuple)):
            for item in line:
                if isinstance(item, (list, tuple)) and len(item) >= 2:
                    txt = item[1]
                    if isinstance(txt, tuple):
                        txt = txt[0] if len(txt) > 0 else ""
                    text_parts.append(str(txt))
    raw = "".join(text_parts)
    return "".join(re.findall(r"[0-9가-힣]", raw))


class LprRecognitionWorker(QObject):
    """
    별도 스레드에서 YOLO(번호판 영역) + PaddleOCR(문자 인식) 실행.
    UI는 submit_frame() 만 호출하고, 인식 결과는 result_ready(str) 시그널로 받음.
    """

    result_ready = pyqtSignal(str)  # 한 줄 결과 예: "2025-03-03 12:00:00 | 12가 3456"

    # 클래스 레벨에서 모델 공유 (메모리 부족 방지)
    _shared_models: dict[str, Any] = {}
    _shared_lock = threading.Lock()

    def __init__(
        self,
        model_path: str,
        plate_conf_threshold: float = 0.25,
        stability_threshold: int = 5,
        cooldown_seconds: float = 5.0,
        mirror_flip: bool = True,
    ) -> None:
        super().__init__()
        self._model_path = model_path
        self._plate_conf = plate_conf_threshold
        self._stability_threshold = stability_threshold
        self._cooldown = cooldown_seconds
        self._mirror_flip = mirror_flip
        self._pending_frame: Optional[Any] = None
        self._lock = threading.Lock()
        self._running = True
        self._loaded = False
        self._load_error: Optional[str] = None

    def is_available(self) -> bool:
        return _CV2_AVAILABLE and _YOLO_AVAILABLE and _PADDLE_AVAILABLE

    def load_models(self) -> bool:
        """첫 프레임 전에 호출하거나 run_loop() 내부에서 lazy load."""
        if self._loaded:
            return True
        if not self.is_available():
            self._load_error = "ultralytics 또는 paddleocr 미설치"
            return False
            
        with self._shared_lock:
            try:
                model_key = str(Path(self._model_path).resolve())
                
                # YOLO 공유 로드
                if "yolo" not in self._shared_models:
                    self._shared_models["yolo"] = YOLO(model_key)
                
                # PaddleOCR 공유 로드
                if "ocr" not in self._shared_models:
                    self._shared_models["ocr"] = PaddleOCR(
                        lang="korean",
                        use_textline_orientation=True,
                        enable_mkldnn=False,
                    )
                
                self._loaded = True
                return True
            except Exception as e:
                self._load_error = str(e)
                return False

    @property
    def _plate_model(self):
        return self._shared_models.get("yolo")

    @property
    def _ocr(self):
        return self._shared_models.get("ocr")

    def submit_frame(self, frame: Any) -> None:
        """UI 스레드에서 호출. 복사본을 넘기면 됨. 블로킹 없음."""
        if not _CV2_AVAILABLE or frame is None:
            return
        with self._lock:
            self._pending_frame = frame.copy() if hasattr(frame, "copy") else frame

    def _get_pending_frame(self) -> Any:
        with self._lock:
            f = self._pending_frame
            self._pending_frame = None
            return f

    def run_loop(self) -> None:
        """워커 스레드에서 호출. 프레임이 들어올 때마다 YOLO → (안정 시) OCR → 시그널."""
        if not self.load_models():
            self.result_ready.emit(f"[LPR] 모델 로드 실패: {self._load_error}")
            return

        plate_stability_counter = 0
        consecutive_empty = 0
        last_trigger_time = 0.0
        last_ocr_text = ""
        shared_boxes = []

        while self._running:
            frame = self._get_pending_frame()
            if frame is None:
                time.sleep(0.02)
                continue

            if self._mirror_flip:
                try:
                    # ESP32 캠 거울 보정
                    frame = cv2.flip(frame, 1)
                except Exception:
                    continue

            h, w = frame.shape[:2]
            current_time = time.time()
            is_cooldown = (current_time - last_trigger_time) < self._cooldown

            # YOLO (동일 스레드에서 실행, 프레임은 이미 복사본)
            try:
                with self._shared_lock:
                    results = self._plate_model(frame, conf=self._plate_conf, verbose=False)
                boxes = results[0].boxes.xyxy.cpu().numpy() if len(results) > 0 and results[0].boxes is not None else []
            except Exception:
                boxes = []

            if len(boxes) == 0:
                consecutive_empty += 1
                if consecutive_empty > 3:
                    plate_stability_counter = 0
            else:
                consecutive_empty = 0
                if not is_cooldown:
                    plate_stability_counter += 1

            for box in boxes:
                x1, y1, x2, y2 = map(int, box)
                pad_y = max(5, int((y2 - y1) * 0.15))
                pad_x = max(5, int((x2 - x1) * 0.05))
                y1_pad = max(0, y1 - pad_y)
                y2_pad = min(h, y2 + pad_y)
                x1_pad = max(0, x1 - pad_x)
                x2_pad = min(w, x2 + pad_x)
                cropped_plate = frame[y1_pad:y2_pad, x1_pad:x2_pad]

                if cropped_plate.size == 0:
                    continue

                h_c, w_c = cropped_plate.shape[:2]
                scaled = cv2.resize(cropped_plate, (w_c * 4, h_c * 4), interpolation=cv2.INTER_CUBIC)

                if plate_stability_counter >= self._stability_threshold and not is_cooldown:
                    last_trigger_time = current_time
                    plate_stability_counter = 0
                    try:
                        with self._shared_lock:
                            ocr_results = self._ocr.ocr(scaled)
                        final_text = _extract_plate_text_from_ocr_result(ocr_results)
                        last_ocr_text = final_text
                        if len(final_text) >= 5:
                            from datetime import datetime
                            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                            self.result_ready.emit(f"{ts} | {final_text}")
                        elif final_text:
                            self.result_ready.emit(f"[미완성] {final_text}")
                    except Exception as ex:
                        self.result_ready.emit(f"[OCR 오류] {ex}")
                break  # 한 프레임에서 첫 박스만 OCR

        # stop() 호출 시에만 _running = False 되어 스레드 종료

    def stop(self) -> None:
        self._running = False
