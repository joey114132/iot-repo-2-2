from __future__ import annotations

from typing import Optional
import cv2
import numpy as np
from PyQt6.QtCore import QTimer, Qt, QThread
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QVBoxLayout,
    QTextEdit,
    QPushButton,
)

from config import settings
from transmission_manager import TransmissionManager
from lpr_detector import LprRecognitionWorker


class LprExitTestDialog(QDialog):
    """
    출구 LPR 카메라(esp32_lpr_exit) 테스트용 팝업.

    - UDP 7090 영상 표시 (실시간, 지연 없음)
    - 번호판 인식: 별도 스레드에서 YOLO+OCR 실행, 인식 결과를 하단 에디터에 표시
    """

    def __init__(
        self,
        transmission_manager: TransmissionManager,
        on_gate_open=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._tx = transmission_manager
        self._on_gate_open = on_gate_open  # callable() → opens exit gate
        self._last_frame_time: float = 0.0

        self.setWindowTitle("출구 LPR 카메라 테스트 (esp32_lpr_exit)")
        self.resize(680, 620)

        layout = QVBoxLayout()

        self.label_status = QLabel("상태: UDP 스트림 대기 중...")
        layout.addWidget(self.label_status)

        layout.addWidget(QLabel("출구 LPR UDP 영상 (포트 7090):"))
        self.video_label = QLabel()
        self.video_label.setMinimumSize(320, 240)
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setStyleSheet("background-color: #333; color: #aaa;")
        self.video_label.setText("영상 대기 중... (ESP32에서 UDP 전송 시 표시)")
        layout.addWidget(self.video_label, 1)

        layout.addWidget(QLabel("번호판 인식 결과:"))
        self.result_edit = QTextEdit()
        self.result_edit.setReadOnly(True)
        self.result_edit.setMaximumHeight(140)
        self.result_edit.setPlaceholderText("인식된 번호가 여기 표시됩니다. (YOLO+PaddleOCR)")
        layout.addWidget(self.result_edit)

        self.btn_pay = QPushButton("시뮬레이션: 요금 결제 완료")
        self.btn_pay.clicked.connect(self._on_simulate_payment)
        layout.addWidget(self.btn_pay)

        self.btn_manual = QPushButton("수동 감지 시작 (3초간 OCR 활성화)")
        self.btn_manual.clicked.connect(self._on_manual_trigger)
        layout.addWidget(self.btn_manual)

        self._last_plate: str | None = None

        self._last_frame: Optional[np.ndarray] = None
        self._refresh_count = 0

        # LPR 워커: 샘플 standalone(esp32_cam_server_exit)과 동일 파라미터
        self._lpr_worker = LprRecognitionWorker(
            model_path=settings.lpr_plate_model_path,
            plate_conf_threshold=0.12,
            stability_threshold=1,
            cooldown_seconds=3.0,
            mirror_flip=False,
        )
        self._lpr_worker.result_ready.connect(self._on_lpr_result)
        self._lpr_thread = QThread()
        self._lpr_worker.moveToThread(self._lpr_thread)
        self._lpr_thread.started.connect(self._lpr_worker.run_loop)
        if self._lpr_worker.is_available():
            self._lpr_thread.start()
        else:
            self.result_edit.append("[LPR] 번호판 인식 모듈을 사용할 수 없습니다. (ultralytics, paddleocr 설치 필요)")

        self._timer = QTimer(self)
        self._timer.setInterval(40)
        self._timer.timeout.connect(self._refresh_ui)
        self._timer.start()

        self.setLayout(layout)
        self._tx.set_lpr_ocr_ui_active(is_exit=True, active=True)

    def _on_lpr_result(self, line: str) -> None:
        self.result_edit.append(line)
        if " | " in line:
            parts = line.split(" | ")
            if len(parts) >= 2:
                plate = parts[1].strip()
                try:
                    res = self._tx.record_exit(plate)
                    msg = res.get("message", "")
                    charge = res.get("charge", 0)
                    self._last_plate = plate
                    self.result_edit.append(f"📡 [SERVER-EXIT] {msg} (Charge: {charge} won)")
                    if charge == 0:
                        self.result_edit.append("🔓 [HARDWARE] Exit Gate Triggered (OPEN)")
                        if callable(self._on_gate_open):
                            self._on_gate_open()
                    else:
                        self.result_edit.append("🔒 [HARDWARE] Exit Gate Remains CLOSED (Payment Required)")
                except Exception as e:
                    self.result_edit.append(f"❌ [API-ERROR] {e}")

        cursor = self.result_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.result_edit.setTextCursor(cursor)

    def _refresh_ui(self) -> None:
        import time as _time
        frame = self._tx.get_lpr_exit_frame()
        if frame is not None:
            fno, img = frame
            if img is not None:
                self._last_frame_time = _time.time()
                # 출구 LPR 영상 보정: 좌우 반전 (번호판 글자 정상 방향)
                img = cv2.flip(img, 1)
                self._last_frame = img
                img = np.ascontiguousarray(img)
                h, w, ch = img.shape
                bytes_per_line = ch * w
                qimg = QImage(
                    img.data, w, h, bytes_per_line, QImage.Format.Format_BGR888
                ).copy()
                pix = QPixmap.fromImage(
                    qimg.scaled(
                        self.video_label.width(),
                        self.video_label.height(),
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
                self.video_label.setPixmap(pix)
                self.video_label.setText("")
        else:
            if self._last_frame_time > 0 and _time.time() - self._last_frame_time > 3.0:
                self.video_label.clear()
                self.video_label.setText("영상 없음 - 차량 대기 중...")
                self._last_frame = None
                self._last_frame_time = 0.0

        self._refresh_count += 1
        if (
            self._last_frame is not None
            and self._lpr_worker.is_available()
            and (self._refresh_count % 5 == 0)
            and self._tx.should_run_lpr_ocr(is_exit=True)
        ):
            self._lpr_worker.submit_frame(self._last_frame.copy())

        has_frame = self._tx.has_recent_lpr_exit_frame(timeout_sec=3.0)
        status = "영상 수신 중" if has_frame else "영상 없음 (UDP 7090 패킷 대기)"
        self.label_status.setText(f"상태: {status}")

    def _on_simulate_payment(self) -> None:
        if not self._last_plate:
            self.result_edit.append("⚠ 인식된 차량 번호가 없습니다.")
            return
        
        try:
            # 시뮬레이션: 전액 결제 (10000원 정도면 충분할 듯)
            res = self._tx._api.clear_payment(self._last_plate, 10000)
            msg = res.get("message", "")
            self.result_edit.append(f"💰 [PAYMENT] {msg}")
            if res.get("ok"):
                self.result_edit.append("🔓 [HARDWARE] Exit Gate Triggered (OPEN)")
                if callable(self._on_gate_open):
                    self._on_gate_open()
        except Exception as e:
            # Check if it's an HTTP 404 from the API client (if using requests, it raises HTTPError)
            err_msg = str(e)
            if "404" in err_msg:
                self.result_edit.append("❌ [PAY-ERROR] No active parking session found for this license plate. Please trigger Entry Gate first.")
            else:
                self.result_edit.append(f"❌ [PAY-ERROR] {e}")

    def _on_manual_trigger(self) -> None:
        self._tx.mark_lpr_apds_detected(is_exit=True)
        self.result_edit.append("⚡ [MANUAL] OCR Triggered (3 seconds)")

    def closeEvent(self, event) -> None:
        self._tx.set_lpr_ocr_ui_active(is_exit=True, active=False)
        if self._lpr_worker.is_available():
            self._lpr_worker.stop()
            self._lpr_thread.quit()
            self._lpr_thread.wait(2000)
        super().closeEvent(event)
