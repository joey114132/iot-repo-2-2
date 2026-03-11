from __future__ import annotations

from typing import List, Dict, Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QGroupBox,
    QTextEdit,
)
from PyQt6.QtGui import QImage, QPixmap

from architect_manager import ArchitectManager
from device_manager import DeviceManager
from info_manager import InfoManager
from transmission_manager import TransmissionManager
from gate_test_dialog import GateTestDialog
from exit_gate_test_dialog import ExitGateTestDialog
from parking_guide_test_dialog import ParkingGuideTestDialog
from lpr_enter_test_dialog import LprEnterTestDialog
from lpr_exit_test_dialog import LprExitTestDialog
from rfid_management_tab import RfidManagementTab

from PyQt6.QtWidgets import QTabWidget

ENTRY_GATE_GUID = "DEV-GATE-1"


class MainWindow(QMainWindow):
    """
    3.device_client 메인 대시보드 창.

    - InfoManager 에 저장된 정보를 읽어와
      서버 연결 상태, 디바이스 리스트, UDP 관련 정보 등을 표시한다.
    - TransmissionManager 를 통해 서버와 통신하여 정보를 갱신한다.
    """

    lpr_popup_signal = pyqtSignal(bool, bool)  # is_exit, show
    rfid_scanned_signal = pyqtSignal(str)

    def __init__(
        self,
        info_manager: InfoManager,
        device_manager: DeviceManager,
        architect_manager: ArchitectManager,
        transmission_manager: TransmissionManager,
    ) -> None:
        super().__init__()
        self._info = info_manager
        self._device_mgr = device_manager
        self._arch_mgr = architect_manager
        self._tx = transmission_manager
        self._gate_dialog: GateTestDialog | None = None
        self._exit_gate_dialog: ExitGateTestDialog | None = None
        self._parking_dialog: ParkingGuideTestDialog | None = None
        self._lpr_dialog: LprEnterTestDialog | None = None
        self._lpr_exit_dialog: LprExitTestDialog | None = None

        self.lpr_popup_signal.connect(self._handle_lpr_popup)
        self._device_mgr.on_lpr_popup = self._emit_lpr_popup

        self.setWindowTitle("스마트 주차장 - 디바이스 클라이언트 대시보드")
        self.resize(1100, 700)

        # 탭 위젯 생성
        self.tabs = QTabWidget()
        self.setCentralWidget(self.tabs)

        # 1. 메인 대시보드 탭
        self.dashboard_tab = QWidget()
        main_layout = QVBoxLayout(self.dashboard_tab)
        
        # 2. 입주민 관리 탭
        self.rfid_tab = RfidManagementTab(self._tx._api)
        self.rfid_scanned_signal.connect(self.rfid_tab.on_rfid_scanned)
        self._device_mgr.on_rfid_scan = self.rfid_scanned_signal.emit

        self.tabs.addTab(self.dashboard_tab, "기기 대시보드")
        self.tabs.addTab(self.rfid_tab, "입주민(RFID) / 캐시 관리")

        # ───────── 상단 요약 영역 (대시보드 탭에 추가) ─────────
        summary_box = QGroupBox("연결 요약")
        summary_layout = QHBoxLayout()
        summary_box.setLayout(summary_layout)

        self.label_server = QLabel("서버 상태: 확인 중...")
        self.label_devices = QLabel("등록 디바이스: -")
        self.label_active_devices = QLabel("활성 디바이스: -")
        self.label_udp = QLabel("UDP 스트림: -")

        for lbl in (self.label_server, self.label_devices, self.label_active_devices, self.label_udp):
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            summary_layout.addWidget(lbl)

        # ───────── 중앙 분할 레이아웃 ─────────
        content_layout = QHBoxLayout()
        main_layout.addLayout(content_layout)

        # [LEFT] 카메라 모니터링 영역 (1.5 비율)
        left_layout = QVBoxLayout()
        content_layout.addLayout(left_layout, 2)

        cam_box = QGroupBox("카메라 및 LPR 모니터링")
        cam_box_inner = QVBoxLayout()
        cam_box.setLayout(cam_box_inner)
        left_layout.addWidget(cam_box)

        # 입구 카메라 영역
        entry_cam_layout = QVBoxLayout()
        entry_cam_layout.addWidget(QLabel("<b>[입구]</b> LPR 카메라 (UDP 7070)"))
        self.video_entry = QLabel("영상 대기 중...")
        self.video_entry.setFixedSize(400, 240)
        self.video_entry.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_entry.setStyleSheet("background-color: #222; border: 1px solid #444;")
        entry_cam_layout.addWidget(self.video_entry)
        
        self.log_entry = QTextEdit()
        self.log_entry.setReadOnly(True)
        self.log_entry.setMaximumHeight(80)
        self.log_entry.setPlaceholderText("입구 OCR 결과...")
        entry_cam_layout.addWidget(self.log_entry)
        cam_box_inner.addLayout(entry_cam_layout)
        
        cam_box_inner.addSpacing(10)

        # 출구 카메라 영역
        exit_cam_layout = QVBoxLayout()
        exit_cam_layout.addWidget(QLabel("<b>[출구]</b> LPR 카메라 (UDP 7090)"))
        self.video_exit = QLabel("영상 대기 중...")
        self.video_exit.setFixedSize(400, 240)
        self.video_exit.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_exit.setStyleSheet("background-color: #222; border: 1px solid #444;")
        exit_cam_layout.addWidget(self.video_exit)
        
        self.log_exit = QTextEdit()
        self.log_exit.setReadOnly(True)
        self.log_exit.setMaximumHeight(80)
        self.log_exit.setPlaceholderText("출구 OCR 결과...")
        exit_cam_layout.addWidget(self.log_exit)
        cam_box_inner.addLayout(exit_cam_layout)

        # [RIGHT] 디바이스 상태 및 제어 (1 비율)
        right_layout = QVBoxLayout()
        content_layout.addLayout(right_layout, 1)

        right_layout.addWidget(summary_box)

        devices_box = QGroupBox("디바이스 목록")
        devices_inner = QVBoxLayout()
        devices_box.setLayout(devices_inner)
        
        self.table_devices = QTableWidget(0, 6)
        self.table_devices.setHorizontalHeaderLabels(["ID", "이름", "타입", "IP", "Port", "상태"])
        self.table_devices.horizontalHeader().setStretchLastSection(True)
        devices_inner.addWidget(self.table_devices)
        right_layout.addWidget(devices_box, 1)

        # 하단 버튼 모음
        btn_box = QGroupBox("수동 제어 및 테스트")
        btn_grid = QVBoxLayout()
        btn_box.setLayout(btn_grid)
        
        self.btn_gate_test = QPushButton("입구 게이트 제어")
        self.btn_gate_test.clicked.connect(self.open_gate_test_dialog)
        btn_grid.addWidget(self.btn_gate_test)

        self.btn_exit_gate_test = QPushButton("출구 게이트 제어")
        self.btn_exit_gate_test.clicked.connect(self.open_exit_gate_test_dialog)
        btn_grid.addWidget(self.btn_exit_gate_test)

        self.btn_refresh = QPushButton("지금 새로고침")
        self.btn_refresh.clicked.connect(self.refresh_from_server)
        btn_grid.addWidget(self.btn_refresh)
        
        right_layout.addWidget(btn_box)

        # ───────── 타이머: 주기적 갱신 ─────────
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh_from_server)
        self._timer.start()

        # 실시간 영상 갱신 타이머 (25fps 근사)
        self._video_timer = QTimer(self)
        self._video_timer.setInterval(40)
        self._video_timer.timeout.connect(self._update_videos)
        self._video_timer.start()

        # 초기 한 번 불러오기
        self.refresh_from_server()

        # ───────── LPR 카메라 모니터링 활성화 ─────────
        # 다이얼로그를 미리 생성하여 백그라운드 OCR 워커들이 돌게 한다.
        self._ensure_lpr_dialogs()
        self._tx.set_lpr_ocr_ui_active(is_exit=False, active=True)
        self._tx.set_lpr_ocr_ui_active(is_exit=True, active=True)

    # ───────── 데이터 로드 및 UI 반영 ─────────
    def refresh_from_server(self) -> None:
        try:
            self._tx.refresh_from_server()
        except Exception as e:  # noqa: BLE001
            self.statusBar().showMessage(f"서버 통신 오류: {e}", 3000)
            self.label_server.setText(f"서버 상태: 연결 실패 ({e})")
            return

        # 이 device_client 가 관리하는 devices 만 표시 (device_clients.devices_ids 기준)
        managed_ids = self._tx.get_my_managed_device_ids()
        all_devices = self._info.devices
        if managed_ids:
            id_set = set(managed_ids)
            dev_by_id = {int(d.get("id")): d for d in all_devices if d.get("id") is not None}
            devices_to_show = [dev_by_id[i] for i in managed_ids if i in dev_by_id]
        else:
            devices_to_show = all_devices

        self._update_summary(devices_to_show)
        self._update_devices_table(devices_to_show)

        entry_gate_connected = False
        exit_gate_connected = False
        for dev in all_devices:
            if (dev.get("device_guid") or "").strip() == ENTRY_GATE_GUID:
                entry_gate_connected = bool(dev.get("is_connected"))
            elif (dev.get("device_guid") or "").strip() == "DEV-GATE-2":
                exit_gate_connected = bool(dev.get("is_connected"))

        operation_mode_on, free_slots, gate_sensor_state, gate_auto_state = self._tx.get_operation_mode_snapshot()
        entry_detected, exit_detected = self._tx.get_entry_exit_detection_snapshot()
        self._device_mgr.sync_entry_gate_mode(
            entry_gate_connected,
            gate_sensor_state,
            entry_detected,
            exit_detected,
            gate_auto_state_from_server=gate_auto_state,
        )
        self._device_mgr.sync_exit_gate_mode(
            exit_gate_connected,
            gate_sensor_state,
            gate_auto_state_from_server=gate_auto_state,
        )
        self._device_mgr.sync_exit_lcd_base(
            operation_mode_on,
            free_slots,
            gate_sensor_state,
            gate_auto_state,
            message_type="default",
        )
        self.statusBar().showMessage("데이터 갱신 완료", 2000)

    def open_gate_test_dialog(self) -> None:
        """입구 차단기(ESP32 보드1_1) 테스트용 팝업을 연다."""
        if self._gate_dialog is None:
            self._gate_dialog = GateTestDialog(self._device_mgr, self)
        self._gate_dialog.show()
        self._gate_dialog.raise_()
        self._gate_dialog.activateWindow()

    def open_exit_gate_test_dialog(self) -> None:
        """출구 차단기(ESP32 보드1_2) 테스트용 팝업을 연다."""
        if self._exit_gate_dialog is None:
            self._exit_gate_dialog = ExitGateTestDialog(self._device_mgr, self)
        self._exit_gate_dialog.show()
        self._exit_gate_dialog.raise_()
        self._exit_gate_dialog.activateWindow()

    def open_parking_guide_test_dialog(self) -> None:
        """esp32_board2(파킹 가이드) 이벤트를 확인하는 팝업을 연다."""
        if self._parking_dialog is None:
            self._parking_dialog = ParkingGuideTestDialog(self._device_mgr, self)
        self._parking_dialog.show()
        self._parking_dialog.raise_()
        self._parking_dialog.activateWindow()

    def _ensure_lpr_dialogs(self) -> None:
        """LPR 다이얼로그를 미리 생성하고 OCR 시그널을 대시보드에 연결한다."""
        if self._lpr_dialog is None:
            self._lpr_dialog = LprEnterTestDialog(
                self._tx,
                on_gate_open=lambda: self._device_mgr.open_gate(target_guid="DEV-GATE-1"),
                parent=self,
            )
            # OCR 인식 결과 시그널을 메인 대시보드 로그창에 연결
            self._lpr_dialog._lpr_worker.result_ready.connect(lambda msg: self.log_entry.append(msg))
            
        if self._lpr_exit_dialog is None:
            self._lpr_exit_dialog = LprExitTestDialog(
                self._tx,
                on_gate_open=lambda: self._device_mgr.open_gate(target_guid="DEV-GATE-2"),
                parent=self,
            )
            self._lpr_exit_dialog._lpr_worker.result_ready.connect(lambda msg: self.log_exit.append(msg))

    def open_lpr_enter_test_dialog(self) -> None:
        """입구 LPR 카메라(esp32_lpr_enter) 테스트용 팝업을 연다."""
        self._ensure_lpr_dialogs()
        self._lpr_dialog.show()
        self._lpr_dialog.raise_()
        self._lpr_dialog.activateWindow()

    def open_lpr_exit_test_dialog(self) -> None:
        """출구 LPR 카메라(esp32_lpr_exit) 테스트용 팝업을 연다."""
        self._ensure_lpr_dialogs()
        self._lpr_exit_dialog.show()
        self._lpr_exit_dialog.raise_()
        self._lpr_exit_dialog.activateWindow()

    def _update_videos(self) -> None:
        """TransmissionManager 로부터 최신 프레임을 가져와 대시보드에 표시 (Non-consuming)."""
        # 입구 영상
        entry_frame_data = self._tx.get_latest_lpr_frame(is_exit=False)
        if entry_frame_data:
            _, img = entry_frame_data
            self._set_pixmap_on_label(self.video_entry, img)
        
        # 출구 영상
        exit_frame_data = self._tx.get_latest_lpr_frame(is_exit=True)
        if exit_frame_data:
            _, img = exit_frame_data
            self._set_pixmap_on_label(self.video_exit, img)

    def _set_pixmap_on_label(self, label: QLabel, cv_img: Any) -> None:
        """OpenCV 이미지를 QLabel 에 맞춰 QPixmap 으로 변환/출력."""
        try:
            h, w, c = cv_img.shape
            bytes_per_line = c * w
            # QImage 는 원본 데이터 포인터를 참조하므로 .copy() 를 수행하여 안전하게 Pixmap 변환
            q_img = QImage(cv_img.data, w, h, bytes_per_line, QImage.Format.Format_RGB888).rgbSwapped().copy()
            pix = QPixmap.fromImage(q_img)
            label.setPixmap(pix.scaled(label.width(), label.height(), Qt.AspectRatioMode.KeepAspectRatio))
        except Exception as e:
            # 디버깅을 위해 에러 무시하지 않음
            pass

    def _emit_lpr_popup(self, is_exit: bool, show: bool) -> None:
        self.lpr_popup_signal.emit(is_exit, show)

    def _handle_lpr_popup(self, is_exit: bool, show: bool) -> None:
        if show:
            if is_exit:
                self.open_lpr_exit_test_dialog()
            else:
                self.open_lpr_enter_test_dialog()
        else:
            if is_exit and self._lpr_exit_dialog is not None:
                self._lpr_exit_dialog.close()
                self._lpr_exit_dialog = None
            elif not is_exit and self._lpr_dialog is not None:
                self._lpr_dialog.close()
                self._lpr_dialog = None

    def _update_summary(self, devices: List[Dict[str, Any]] | None = None) -> None:
        health = self._info.server_health or {}
        status = health.get("status", "unknown")
        self.label_server.setText(
            f"서버 상태: {status} ({self._info.env.server_base_url})",
        )

        if devices is None:
            devices = self._info.devices
        total = len(devices)
        active = sum(1 for d in devices if d.get("is_connected"))
        self.label_devices.setText(f"등록 디바이스: {total}개")
        self.label_active_devices.setText(f"활성 디바이스: {active}개")

        # UDP 관련 정보는 .env + LPR 카메라 서버 config 를 기준으로 단순 표시
        udp_host = self._info.env.udp_listen_host
        udp_port = self._info.env.udp_listen_port
        self.label_udp.setText(f"UDP 수신: {udp_host}:{udp_port}")

    def _update_devices_table(self, devices: List[Dict[str, Any]]) -> None:
        self.table_devices.setRowCount(len(devices))
        for row, dev in enumerate(devices):
            self.table_devices.setItem(
                row,
                0,
                QTableWidgetItem(str(dev.get("id", ""))),
            )
            self.table_devices.setItem(
                row,
                1,
                QTableWidgetItem(dev.get("name", "")),
            )
            self.table_devices.setItem(
                row,
                2,
                QTableWidgetItem(dev.get("type", "")),
            )
            self.table_devices.setItem(
                row,
                3,
                QTableWidgetItem(dev.get("ip_address", "") or ""),
            )
            self.table_devices.setItem(
                row,
                4,
                QTableWidgetItem(dev.get("port_info", "") or ""),
            )
            self.table_devices.setItem(
                row,
                5,
                QTableWidgetItem("연결됨" if dev.get("is_connected") else "미연결"),
            )

            is_conn = bool(dev.get("is_connected"))
            for col in range(6):
                item = self.table_devices.item(row, col)
                if item:
                    item.setForeground(Qt.GlobalColor.white if is_conn else Qt.GlobalColor.gray)
                    if col == 5: # 상태 컬럼 색상 강조
                        item.setBackground(Qt.GlobalColor.darkGreen if is_conn else Qt.GlobalColor.darkRed)

