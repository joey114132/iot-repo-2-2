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
)

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

        for lbl in (
            self.label_server,
            self.label_devices,
            self.label_active_devices,
            self.label_udp,
        ):
            lbl.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            summary_layout.addWidget(lbl)

        main_layout.addWidget(summary_box)

        # ───────── 디바이스 테이블 ─────────
        devices_box = QGroupBox("디바이스 목록 (1.server 기준)")
        devices_layout = QVBoxLayout()
        devices_box.setLayout(devices_layout)

        self.table_devices = QTableWidget(0, 6)
        self.table_devices.setHorizontalHeaderLabels(
            ["ID", "이름", "타입", "IP", "PortInfo", "연결 상태"],
        )
        self.table_devices.horizontalHeader().setStretchLastSection(True)

        devices_layout.addWidget(self.table_devices)

        btn_row = QHBoxLayout()
        self.btn_refresh = QPushButton("지금 새로고침")
        self.btn_refresh.clicked.connect(self.refresh_from_server)
        btn_row.addWidget(self.btn_refresh)

        self.btn_gate_test = QPushButton("esp32 board_1_1 테스트")
        self.btn_gate_test.clicked.connect(self.open_gate_test_dialog)
        btn_row.addWidget(self.btn_gate_test)

        self.btn_exit_gate_test = QPushButton("esp32 board_1_2 테스트")
        self.btn_exit_gate_test.clicked.connect(self.open_exit_gate_test_dialog)
        btn_row.addWidget(self.btn_exit_gate_test)

        self.btn_parking_test = QPushButton("esp32_board2 파킹 가이드 테스트")
        self.btn_parking_test.clicked.connect(self.open_parking_guide_test_dialog)
        btn_row.addWidget(self.btn_parking_test)

        self.btn_lpr_test = QPushButton("입구 LPR 카메라 테스트 (esp32_lpr_enter)")
        self.btn_lpr_test.clicked.connect(self.open_lpr_enter_test_dialog)
        btn_row.addWidget(self.btn_lpr_test)

        self.btn_lpr_exit_test = QPushButton("출구 LPR 카메라 테스트 (esp32_lpr_exit)")
        self.btn_lpr_exit_test.clicked.connect(self.open_lpr_exit_test_dialog)
        btn_row.addWidget(self.btn_lpr_exit_test)

        btn_row.addStretch()
        devices_layout.addLayout(btn_row)

        main_layout.addWidget(devices_box, 1)

        # ───────── 타이머: 주기적 갱신 ─────────
        self._timer = QTimer(self)
        self._timer.setInterval(5000)
        self._timer.timeout.connect(self.refresh_from_server)
        self._timer.start()

        # 초기 한 번 불러오기
        self.refresh_from_server()

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

    def open_lpr_enter_test_dialog(self) -> None:
        """입구 LPR 카메라(esp32_lpr_enter) 테스트용 팝업을 연다."""
        if self._lpr_dialog is None:
            self._lpr_dialog = LprEnterTestDialog(
                self._tx,
                on_gate_open=lambda: self._device_mgr.open_gate(target_guid="DEV-GATE-1"),
                parent=self,
            )
        self._lpr_dialog.show()
        self._lpr_dialog.raise_()
        self._lpr_dialog.activateWindow()

    def open_lpr_exit_test_dialog(self) -> None:
        """출구 LPR 카메라(esp32_lpr_exit) 테스트용 팝업을 연다."""
        if self._lpr_exit_dialog is None:
            self._lpr_exit_dialog = LprExitTestDialog(
                self._tx,
                on_gate_open=lambda: self._device_mgr.open_gate(target_guid="DEV-GATE-2"),
                parent=self,
            )
        self._lpr_exit_dialog.show()
        self._lpr_exit_dialog.raise_()
        self._lpr_exit_dialog.activateWindow()

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

            is_conn = bool(dev.get("is_connected"))
            status_item = QTableWidgetItem("연결됨" if is_conn else "미연결")
            if is_conn:
                status_item.setBackground(Qt.GlobalColor.darkGreen)
            else:
                status_item.setBackground(Qt.GlobalColor.darkRed)
            self.table_devices.setItem(row, 5, status_item)

