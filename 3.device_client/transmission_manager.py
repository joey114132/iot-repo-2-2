from __future__ import annotations

from queue import Empty, Queue
from typing import Any, Dict, List, Optional

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse
import cv2

from api_client import DeviceApiClient
from esp32_receiver import Esp32UdpReceiver
from info_manager import InfoManager
from config import settings


class TransmissionManager:
    """
    - 서버(FastAPI)와의 HTTP 통신 담당 (DeviceApiClient 래핑)
    - 향후 아두이노/ESP32/UDP 등의 전송 스레드를 함께 관리하는 허브 역할
    """

    def __init__(self, info_manager: InfoManager) -> None:
        self._info = info_manager
        self._api = DeviceApiClient()
        # LPR 입구/출구 카메라
        # - 입구: UDP 포트 settings.lpr_enter_udp_port (기본 7070)
        # - 출구: UDP 포트 settings.lpr_exit_udp_port  (기본 7090)
        # - 연결 = 각 포트로 UDP 패킷 수신 여부 (패킷 있으면 연결, 없으면 10초 후 끊김)
        self._lpr_last_seen: float = 0.0
        self._lpr_connected: bool = False
        self._lpr_command_queue: list[str] = []
        self._lpr_frame_queue: Queue = Queue(maxsize=5)
        self._lpr_last_frame_ts: float = 0.0
        self._lpr_udp_receiver: Optional[Esp32UdpReceiver] = None
        # 출구 LPR 전용 상태/버퍼
        self._lpr_exit_last_seen: float = 0.0
        self._lpr_exit_connected: bool = False
        self._lpr_exit_frame_queue: Queue = Queue(maxsize=5)
        self._lpr_exit_last_frame_ts: float = 0.0
        self._lpr_exit_udp_receiver: Optional[Esp32UdpReceiver] = None
        # 최근 프레임 보유 (백그라운드 OCR 워커용, non-consuming)
        self._latest_lpr_frame: Optional[tuple] = None
        self._latest_lpr_exit_frame: Optional[tuple] = None
        # OCR 실행 제어 플래그
        self._lpr_ocr_entry_ui_active: bool = False
        self._lpr_ocr_exit_ui_active: bool = False
        self._lpr_ocr_entry_apds_until: float = 0.0
        self._lpr_ocr_exit_apds_until: float = 0.0
        self._lpr_ocr_apds_hold_sec: float = 3.0
        self._lpr_ocr_test_bootstrap_sec: float = 15.0
        self._sensor_detect_hold_sec: float = 1.5
        self._entry_sensor_seq: int = 0
        self._exit_sensor_seq: int = 0
        self._operation_mode_on: bool = True
        self._dashboard_free_slots: int = 0
        self._gate_sensor_state: int = 1
        self._gate_auto_state: int = 0
        self._entry_sensor_detected: bool = False
        self._exit_sensor_detected: bool = False

        # LPR 용 REST 서버 (등록/config/command) — 연결 상태는 UDP 기준으로만 갱신
        self._start_lpr_rest_server()
        self._start_lpr_monitor()
        # LPR 입구 UDP 수신 시작 → 패킷 들어오면 _mark_lpr_seen(), 프레임은 _lpr_frame_queue 에 적재
        self._lpr_udp_receiver = Esp32UdpReceiver(
            host=settings.udp_listen_host,
            port=settings.lpr_enter_udp_port,
            on_frame=self._on_lpr_udp_frame,
        )
        self._lpr_udp_receiver.start()
        # LPR 출구 UDP 수신 시작 → 프레임은 _lpr_exit_frame_queue 에 적재
        self._lpr_exit_udp_receiver = Esp32UdpReceiver(
            host=settings.udp_listen_host,
            port=settings.lpr_exit_udp_port,
            on_frame=self._on_lpr_exit_udp_frame,
        )
        self._lpr_exit_udp_receiver.start()
        # 시작 시 입구/출구 LPR 의 is_connected 를 끊김(false) 으로 한 번 강제 동기화해 두면
        # 예전에 켜져 있던 상태가 DB 에 남아 있어도 실제 상태와 맞출 수 있다.
        self._set_lpr_connected(False, force=True)
        self._set_lpr_exit_connected(False, force=True)

    # ───────── LPR OCR 실행 제어 (UI 활성 + APDS 감지) ─────────
    def set_lpr_ocr_ui_active(self, is_exit: bool, active: bool) -> None:
        now = time.time()
        if is_exit:
            self._lpr_ocr_exit_ui_active = active
            # 테스트 UI를 열면 모델 로딩/초기 프레임 동안 OCR이 막히지 않도록
            # 출구 APDS 허용 윈도우를 한 번 넉넉히 열어 둔다.
            if active:
                self._lpr_ocr_exit_apds_until = max(
                    self._lpr_ocr_exit_apds_until,
                    now + self._lpr_ocr_test_bootstrap_sec,
                )
        else:
            self._lpr_ocr_entry_ui_active = active
            # 테스트 UI를 열면 모델 로딩/초기 프레임 동안 OCR이 막히지 않도록
            # 입구 APDS 허용 윈도우를 한 번 넉넉히 열어 둔다.
            if active:
                self._lpr_ocr_entry_apds_until = max(
                    self._lpr_ocr_entry_apds_until,
                    now + self._lpr_ocr_test_bootstrap_sec,
                )

    def mark_lpr_apds_detected(self, is_exit: bool) -> None:
        """APDS 감지 이벤트를 받으면 짧은 시간 동안 OCR 허용 플래그를 활성화한다."""
        expire_at = time.time() + self._lpr_ocr_apds_hold_sec
        if is_exit:
            self._lpr_ocr_exit_apds_until = expire_at
        else:
            self._lpr_ocr_entry_apds_until = expire_at

    def should_run_lpr_ocr(self, is_exit: bool) -> bool:
        """
        테스트 UI가 열려 있거나, APDS(입/출차 감지) 센서가 최근에 반응했을 때만 OCR을 허용한다.
        """
        now = time.time()
        if is_exit:
            return self._lpr_ocr_exit_ui_active or (now <= self._lpr_ocr_exit_apds_until)
        return self._lpr_ocr_entry_ui_active or (now <= self._lpr_ocr_entry_apds_until)

    # ───────── 입/출차 감지 센서(T1/T2) 상태 반영 ─────────
    def set_entry_exit_sensor_connected(self, connected: bool) -> None:
        """게이트 연결 상태에 맞춰 입/출차 감지 센서 연결 상태를 서버에 반영."""
        self._api.set_entry_exit_sensor_state(
            entry_sensor_connected=connected,
            exit_sensor_connected=connected,
        )
        if not connected:
            self._api.set_entry_exit_sensor_state(
                entry_sensor_detected=False,
                exit_sensor_detected=False,
            )

    def pulse_entry_exit_sensor_detected(self, is_exit: bool) -> None:
        """
        APDS 감지 이벤트를 짧게 점등(빨강) 후 자동 해제(초록)한다.
        - is_exit=False: 입차(T1)
        - is_exit=True:  출차(T2)
        """
        if is_exit:
            self._exit_sensor_seq += 1
            seq = self._exit_sensor_seq
        else:
            self._entry_sensor_seq += 1
            seq = self._entry_sensor_seq

        if is_exit:
            self._api.set_entry_exit_sensor_state(
                exit_sensor_connected=True,
                exit_sensor_detected=True,
            )
        else:
            self._api.set_entry_exit_sensor_state(
                entry_sensor_connected=True,
                entry_sensor_detected=True,
            )

        def _release_later() -> None:
            time.sleep(self._sensor_detect_hold_sec)
            if is_exit:
                if seq != self._exit_sensor_seq:
                    return
            else:
                if seq != self._entry_sensor_seq:
                    return
            if is_exit:
                self._api.set_entry_exit_sensor_state(exit_sensor_detected=False)
            else:
                self._api.set_entry_exit_sensor_state(entry_sensor_detected=False)

        threading.Thread(target=_release_later, daemon=True).start()

    # ───────── 서버와의 통신 ─────────
    def get_my_managed_device_ids(self) -> List[int]:
        """
        .env 의 device_no 와 device_clients 테이블을 매칭해
        이 device_client 가 관리하는 devices.id 목록을 반환.
        실패 또는 미설정 시 빈 리스트.
        """
        try:
            dc_list = self._api.list_device_clients()
            for item in dc_list:
                if item.get("device_no") == settings.device_no:
                    ids_str = item.get("devices_ids") or ""
                    if not ids_str.strip():
                        return []
                    result = []
                    for x in ids_str.split(","):
                        s = x.strip()
                        if not s:
                            continue
                        try:
                            result.append(int(s))
                        except ValueError:
                            continue
                    return result
        except Exception:
            pass
        return []

    def refresh_from_server(self) -> None:
        """
        서버 /health, /devices 를 조회해서 InfoManager 에 반영.
        예외는 상위(UI)에서 처리할 수 있도록 그대로 올린다.
        """
        health: Dict[str, Any] = self._api.health()
        devices: List[Dict[str, Any]] = self._api.list_devices()
        dashboard: Dict[str, Any] = self._api.get_dashboard()
        self._operation_mode_on = bool(dashboard.get("operation_mode_on", True))
        self._dashboard_free_slots = int(dashboard.get("free_slots", 0))
        self._gate_sensor_state = int(dashboard.get("gate_sensor_state", 1))
        self._gate_auto_state = int(dashboard.get("gate_auto_state", 0))
        self._entry_sensor_detected = bool(dashboard.get("entry_sensor_detected", False))
        self._exit_sensor_detected = bool(dashboard.get("exit_sensor_detected", False))
        self._info.update_from_server(health=health, devices=devices)

    def get_operation_mode_snapshot(self) -> tuple[bool, int, int, int]:
        """(운영상태 ON/OFF, 빈 주차면 수, 게이트 상태, 자동게이트 상태) 반환."""
        return (
            self._operation_mode_on,
            self._dashboard_free_slots,
            self._gate_sensor_state,
            self._gate_auto_state,
        )

    def get_entry_exit_detection_snapshot(self) -> tuple[bool, bool]:
        """(입차 감지 여부, 출차 감지 여부) 반환."""
        return (self._entry_sensor_detected, self._exit_sensor_detected)

    def set_gate_state(
        self,
        *,
        gate_sensor_state: int | None = None,
        gate_auto_state: int | None = None,
    ) -> None:
        data = self._api.set_gate_state(
            gate_sensor_state=gate_sensor_state,
            gate_auto_state=gate_auto_state,
        )
        if "gate_sensor_state" in data:
            self._gate_sensor_state = int(data["gate_sensor_state"])
        if "gate_auto_state" in data:
            self._gate_auto_state = int(data["gate_auto_state"])

    def set_gate_auto_state(self, gate_auto_state: int) -> None:
        self.set_gate_state(gate_auto_state=int(gate_auto_state))

    def record_entry(self, license_plate: str) -> Dict[str, Any]:
        """입차 기록 (LPR -> 서버)"""
        return self._api.record_entry(license_plate)

    def record_exit(self, license_plate: str, ext_rfid_registered: bool = False) -> Dict[str, Any]:
        """출차 기록 및 요금 계산 (LPR -> 서버)"""
        return self._api.record_exit(license_plate, ext_rfid_registered)

    def set_tower_slots_inactive(self) -> None:
        """
        주차타워 슬롯(T1~T6)은 입/출차 감지센서와 무관하므로
        sensor_connected=False / is_occupied=False 로 고정한다.
        """
        for slot_name in ("T1", "T2", "T3", "T4", "T5", "T6"):
            self._api.set_slot_sensor_connected(slot_name, False)
            self._api.set_slot_occupied(slot_name, False)

    # ───────── 디바이스 연결 상태 업데이트 ─────────
    def _managed_device_ids(self) -> Optional[List[int]]:
        """이 device_client 가 관리하는 device id 집합. 비어있으면 None(전체 허용)."""
        ids = self.get_my_managed_device_ids()
        return ids if ids else None

    def set_gate_connected(self, connected: bool) -> None:
        """
        gate_controller 타입 장비의 is_connected 플래그를 서버/DB 에 반영하고,
        InfoManager 상태도 갱신한다. (device_clients.devices_ids 에 있는 장비만)
        """
        devices: List[Dict[str, Any]] = self._api.list_devices()
        managed = self._managed_device_ids()
        changed = False
        for d in devices:
            if (d.get("type") or "").lower() != "gate_controller":
                continue
            if managed is not None and d.get("id") not in managed:
                continue
            if bool(d.get("is_connected")) != connected:
                self._api.update_device_is_connected(d, connected)
                d["is_connected"] = connected
                changed = True
        if changed:
            # 변경된 devices 리스트를 바로 InfoManager 에 반영
            self._info.update_from_server(
                health=self._info.server_health,
                devices=devices,
            )

    def set_gate_connected_by_ip(self, ip: str, connected: bool) -> None:
        """
        gate_controller 타입 중 특정 IP 에 해당하는 장비만 is_connected 업데이트.
        (device_clients.devices_ids 에 있는 장비만)
        """
        devices: List[Dict[str, Any]] = self._api.list_devices()
        managed = self._managed_device_ids()
        changed = False
        for d in devices:
            if (d.get("type") or "").lower() != "gate_controller" or (d.get("ip_address") or "") != ip:
                continue
            if managed is not None and d.get("id") not in managed:
                continue
            if bool(d.get("is_connected")) != connected:
                self._api.update_device_is_connected(d, connected)
                d["is_connected"] = connected
                changed = True
        if changed:
            self._info.update_from_server(
                health=self._info.server_health,
                devices=devices,
            )

    def set_gate_connected_by_guid(self, device_guid: str, connected: bool) -> None:
        """
        gate_controller 타입 중 device_guid 가 일치하는 장비만 is_connected 업데이트.
        등록 패킷(TYPE_DEV_REGISTER) 수신 시 GUID 기준으로 연결 표시해, 1/2 혼동 방지.
        """
        if not (device_guid or "").strip():
            return
        guid_stripped = device_guid.strip()
        devices: List[Dict[str, Any]] = self._api.list_devices()
        managed = self._managed_device_ids()
        changed = False
        for d in devices:
            if (d.get("type") or "").lower() != "gate_controller":
                continue
            if (d.get("device_guid") or "").strip() != guid_stripped:
                continue
            if managed is not None and d.get("id") not in managed:
                continue
            if bool(d.get("is_connected")) != connected:
                self._api.update_device_is_connected(d, connected)
                d["is_connected"] = connected
                changed = True
            break
        if changed:
            self._info.update_from_server(
                health=self._info.server_health,
                devices=devices,
            )

    def set_street_parking_connected(self, connected: bool) -> None:
        """
        street_parking_controller 타입(esp32_board2) 장비의 is_connected 플래그를
        서버/DB 에 반영. (device_clients.devices_ids 에 있는 장비만)
        """
        devices: List[Dict[str, Any]] = self._api.list_devices()
        managed = self._managed_device_ids()
        changed = False
        for d in devices:
            if (d.get("type") or "").lower() != "street_parking_controller":
                continue
            if managed is not None and d.get("id") not in managed:
                continue
            if bool(d.get("is_connected")) != connected:
                self._api.update_device_is_connected(d, connected)
                d["is_connected"] = connected
                changed = True
        if changed:
            self._info.update_from_server(
                health=self._info.server_health,
                devices=devices,
            )

    def set_street_parking_connected_by_ip(self, ip: str, connected: bool) -> None:
        """
        street_parking_controller 타입(esp32_board2) 중 특정 IP 장비만 업데이트.
        (device_clients.devices_ids 에 있는 장비만)
        """
        devices: List[Dict[str, Any]] = self._api.list_devices()
        managed = self._managed_device_ids()
        changed = False
        for d in devices:
            if (d.get("type") or "").lower() != "street_parking_controller" or (d.get("ip_address") or "") != ip:
                continue
            if managed is not None and d.get("id") not in managed:
                continue
            if bool(d.get("is_connected")) != connected:
                self._api.update_device_is_connected(d, connected)
                d["is_connected"] = connected
                changed = True
        if changed:
            self._info.update_from_server(
                health=self._info.server_health,
                devices=devices,
            )

    # ───────── device_guid 기반 IP 업데이트 (등록 패킷 처리용) ─────────
    def update_device_ip_by_guid(
        self,
        device_guid: str,
        ip: str,
        device_name: str | None = None,
    ) -> None:
        """
        esp32_board1_1 / esp32_board2 / esp32_lpr_enter 에서
        TYPE_DEV_REGISTER 패킷 또는 REST 등록으로 device_guid 와 현재 IP 를 보내오면,
        해당 guid 를 가진 devices 행의 ip_address 를 최신 값으로 갱신한다.
        """
        if not device_guid and not device_name:
            print("[LPR-IP] skip: guid/name 모두 없음")
            return

        # 먼저 서버의 devices 목록에서 guid/name 이 모두 일치하는 장비가 있는지 확인한다.
        devices: List[Dict[str, Any]] = self._api.list_devices()
        target: Dict[str, Any] | None = None

        for d in devices:
            guid_in_db = (d.get("device_guid") or "").strip()
            name_in_db = (d.get("name") or "").strip()
            # 1순위: device_guid 가 있으면 guid 만으로 매칭 (name 은 참고용)
            if device_guid:
                if guid_in_db == device_guid.strip():
                    target = d
                    break
                # guid 가 다르면 이름만 맞아도 다른 장비일 수 있으므로 스킵
                continue
            # 2순위: guid 가 없고 device_name 만 있는 경우 이름으로 매칭
            if device_name and name_in_db == (device_name or "").strip():
                target = d
                break

        if target is None:
            # 등록 정보와 매칭되는 devices 레코드가 없으면 IP 갱신을 하지 않는다.
            print(f"[LPR-IP] not found in devices: guid={device_guid!r}, name={device_name!r}")
            return

        # 1순위: device_guid 로 서버 전용 엔드포인트 호출
        before_ip = target.get("ip_address")
        print(f"[LPR-IP] updating: guid={device_guid!r}, name={device_name!r}, {before_ip} -> {ip}")
        if device_guid:
            self._api.update_device_ip_by_guid(device_guid, ip)
        else:
            # guid 가 비어 있고 device_name 만 있는 경우에는 기존 방식으로 fallback
            if (target.get("ip_address") or "") != ip:
                target["ip_address"] = ip
                self._api.update_device(target)

        # 갱신 후 최신 devices 목록을 다시 받아 InfoManager 에 반영
        devices_after: List[Dict[str, Any]] = self._api.list_devices()
        self._info.update_from_server(
            health=self._info.server_health,
            devices=devices_after,
        )

    # ───────── 주차면 점유 상태 업데이트 (parking_slots 동기화) ─────────
    def set_slot_occupied(
        self,
        slot_name: str,
        occupied: bool,
        plate: str | None = None,
    ) -> None:
        """
        parking_slots 테이블의 특정 슬롯(S1~S4, T1~T6 등)에 대해
        is_occupied / sensor_connected 상태를 업데이트한다.

        - esp32_board2 의 SPOT_1~4 이벤트를 DeviceManager 가 받아서 호출.
        - 2.client 는 /parking/dashboard 를 통해 이 정보를 읽어와
          주차 대수/빈자리/색상(UI)을 자동으로 갱신한다.
        """
        self._api.set_slot_occupied(slot_name, occupied, plate)

    # ───────── LPR 입구 카메라용 REST 서버/keep-alive ─────────
    def _start_lpr_rest_server(self) -> None:
        """esp32_lpr_enter 가 접속하는 소형 REST 서버를 백그라운드로 기동."""

        manager = self

        class LprHandler(BaseHTTPRequestHandler):
            def _send_json(self, status: int, payload: Dict[str, Any]) -> None:
                body = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self) -> None:  # noqa: N802
                if self.path == "/api/device/register":
                    length = int(self.headers.get("Content-Length", "0"))
                    raw = self.rfile.read(length).decode("utf-8") if length > 0 else "{}"
                    try:
                        data = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        data = {}
                    guid = str(data.get("guid") or "DEV-LPR-1")
                    name = str(data.get("name") or "입구 LPR 카메라")
                    ip = str(data.get("ip") or self.client_address[0])
                    manager._handle_lpr_register(guid, name, ip)
                    self._send_json(200, {"status": "ok"})
                else:
                    self.send_error(404)

            def do_GET(self) -> None:  # noqa: N802
                parsed = urlparse(self.path)
                path = parsed.path

                if path == "/api/device/config":
                    # 단순 설정 반환: 서버(host), REST 포트, UDP 포트 (연결 상태는 UDP 패킷 기준으로만 갱신)
                    # guid 쿼리 파라미터를 사용해 입구/출구 LPR 을 구분한다.
                    from config import settings as _settings  # 로컬 import

                    qs = parse_qs(parsed.query or "")
                    guid = ((qs.get("guid") or [""])[0] or "").strip().upper()

                    host_header = self.headers.get("Host", "")
                    host_ip = host_header.split(":")[0] if host_header else ""
                    if not host_ip or host_ip in ("0.0.0.0", "127.0.0.1", "localhost"):
                        host_ip = self.client_address[0]

                    # 기본값: 입구 LPR 설정
                    udp_port = _settings.lpr_enter_udp_port
                    # 출구 LPR(DEV-LPR-2) 인 경우에는 출구용 UDP 포트로 내려준다.
                    if guid == "DEV-LPR-2":
                        udp_port = _settings.lpr_exit_udp_port

                    payload = {
                        "server_host": host_ip,
                        "rest_port": _settings.lpr_enter_rest_port,
                        "udp_port": udp_port,
                    }
                    self._send_json(200, payload)
                    return

                if path == "/api/device/command":
                    qs = parse_qs(parsed.query or "")
                    guid = (qs.get("guid") or [""])[0]
                    # guid 를 기반으로 현재 클라이언트 IP 를 devices.ip_address 에 반영
                    if guid:
                        try:
                            manager.update_device_ip_by_guid(guid, self.client_address[0])
                        except Exception:
                            # IP 갱신 실패는 치명적이지 않으므로 무시
                            pass

                    cmd = manager._pop_lpr_command(guid)
                    # 디버그용 로그: 어떤 guid 에 어떤 명령이 내려갔는지 확인
                    print(f"[LPR-CMD] guid={guid!r}, ip={self.client_address[0]}, cmd={cmd!r}")
                    if cmd:
                        self._send_json(200, {"command": cmd})
                    else:
                        self._send_json(200, {"command": "none"})
                    return

                if path == "/api/devices":
                    try:
                        devices = manager._api.list_devices()
                    except Exception:  # noqa: BLE001
                        devices = []
                    self._send_json(200, {"devices": devices})
                    return

                self.send_error(404)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                # 콘솔 로그는 너무 시끄러우니 무시
                return

        from config import settings

        server = HTTPServer(("0.0.0.0", settings.lpr_enter_rest_port), LprHandler)

        def _serve() -> None:
            server.serve_forever()

        thread = threading.Thread(target=_serve, daemon=True)
        thread.start()

    def _start_lpr_monitor(self) -> None:
        """LPR 카메라 연결 모니터: 각 UDP 포트(입구/출구)에 10초 동안 패킷이 없으면 끊김으로 간주."""

        def _loop() -> None:
            while True:
                time.sleep(2.0)
                now = time.time()
                if self._lpr_connected and (now - self._lpr_last_seen > 10.0):
                    self._set_lpr_connected(False)
                if self._lpr_exit_connected and (now - self._lpr_exit_last_seen > 10.0):
                    self._set_lpr_exit_connected(False)

        t = threading.Thread(target=_loop, daemon=True)
        t.start()

    def _on_lpr_udp_frame(self, fno: int, img: Any) -> None:
        if fno % 30 == 0:
            print(f"[UDP-DEBUG] Entry Frame received: fno={fno}")
        self._mark_lpr_seen()
        self._lpr_last_frame_ts = time.time()
        self._latest_lpr_frame = (fno, img)  # non-consuming latest frame
        try:
            if self._lpr_frame_queue.full():
                self._lpr_frame_queue.get_nowait()
            self._lpr_frame_queue.put((fno, img))
        except Exception:
            pass

    def _on_lpr_exit_udp_frame(self, fno: int, img: Any) -> None:
        if fno % 30 == 0:
            print(f"[UDP-DEBUG] Exit Frame received: fno={fno}")
        self._mark_lpr_exit_seen()
        self._lpr_exit_last_frame_ts = time.time()
        try:
            if self._lpr_exit_frame_queue.full():
                self._lpr_exit_frame_queue.get_nowait()
            if img is not None:
                img = cv2.flip(img, 1)
            self._latest_lpr_exit_frame = (fno, img)  # non-consuming latest frame
            self._lpr_exit_frame_queue.put((fno, img))
        except Exception:
            pass

    def get_lpr_frame(self) -> Optional[tuple[int, Any]]:
        """테스트 다이얼로그에서 호출: 수신된 LPR 프레임 1개 반환 (없으면 None)."""
        try:
            return self._lpr_frame_queue.get_nowait()
        except Empty:
            return None

    def has_recent_lpr_frame(self, timeout_sec: float = 5.0) -> bool:
        """최근 timeout_sec 초 이내에 LPR UDP 프레임을 받았는지."""
        if self._lpr_last_frame_ts <= 0:
            return False
        return (time.time() - self._lpr_last_frame_ts) <= timeout_sec

    def get_latest_lpr_frame(self, is_exit: bool) -> Optional[tuple]:
        """Non-consuming: 백그라운드 OCR 워커가 가장 최근 프레임을 가져갈 때 사용."""
        if is_exit:
            return self._latest_lpr_exit_frame
        return self._latest_lpr_frame

    # 입구/출구를 명시적으로 구분하는 헬퍼 (향후 출구 테스트 UI 등에서 사용)
    def get_lpr_entry_frame(self) -> Optional[tuple[int, Any]]:
        return self.get_lpr_frame()

    def has_recent_lpr_entry_frame(self, timeout_sec: float = 5.0) -> bool:
        return self.has_recent_lpr_frame(timeout_sec=timeout_sec)

    def get_lpr_exit_frame(self) -> Optional[tuple[int, Any]]:
        try:
            return self._lpr_exit_frame_queue.get_nowait()
        except Empty:
            return None

    def has_recent_lpr_exit_frame(self, timeout_sec: float = 5.0) -> bool:
        if self._lpr_exit_last_frame_ts <= 0:
            return False
        return (time.time() - self._lpr_exit_last_frame_ts) <= timeout_sec

    def _mark_lpr_seen(self) -> None:
        self._lpr_last_seen = time.time()
        if not self._lpr_connected:
            self._set_lpr_connected(True)

    def _mark_lpr_exit_seen(self) -> None:
        self._lpr_exit_last_seen = time.time()
        if not self._lpr_exit_connected:
            self._set_lpr_exit_connected(True)

    def _handle_lpr_register(self, device_guid: str, device_name: str, ip: str) -> None:
        """esp32_lpr_enter 가 POST /api/device/register 를 호출했을 때 처리."""
        print(f"[LPR-REST] /api/device/register guid={device_guid!r}, name={device_name!r}, ip={ip}")
        try:
            self.update_device_ip_by_guid(device_guid, ip, device_name=device_name)
        except Exception:
            # IP 갱신 실패는 치명적이지 않으므로 무시
            pass
        # 연결 상태는 UDP 7070 패킷 수신으로만 갱신 (여기서는 _mark_lpr_seen 호출 안 함)

    def _set_lpr_connected(self, connected: bool, force: bool = False) -> None:
        """입구 LPR(DEV-LPR-1, udp_port=7070) 장비의 is_connected 플래그를 갱신."""
        if self._lpr_connected == connected and not force:
            return
        self._lpr_connected = connected

        devices = self._api.list_devices()
        managed = self._managed_device_ids()
        changed = False

        for d in devices:
            typ = (d.get("type") or "").lower()
            if typ in ("lpr_camera", "lpr_camera_server"):
                name = (d.get("name") or "").strip()
                guid = (d.get("device_guid") or "").strip()

                udp_port = None
                cfg = d.get("config")
                if isinstance(cfg, str):
                    try:
                        cfg_obj = json.loads(cfg)
                    except Exception:  # noqa: BLE001
                        cfg_obj = {}
                elif isinstance(cfg, dict):
                    cfg_obj = cfg
                else:
                    cfg_obj = {}

                try:
                    udp_port = int(cfg_obj.get("udp_port")) if "udp_port" in cfg_obj else None
                except Exception:
                    udp_port = None

                is_entry_by_name = "입구" in name
                is_entry_by_guid = guid.upper() in ("DEV-LPR-1",)

                is_entry = False
                if udp_port is not None:
                    is_entry = udp_port == settings.lpr_enter_udp_port
                else:
                    is_entry = is_entry_by_name or is_entry_by_guid

                if not is_entry:
                    continue
                if managed is not None and d.get("id") not in managed:
                    continue

                if bool(d.get("is_connected")) != connected:
                    self._api.update_device_is_connected(d, connected)
                    d["is_connected"] = connected
                    changed = True

        if changed:
            self._info.update_from_server(
                health=self._info.server_health,
                devices=devices,
            )

    def _set_lpr_exit_connected(self, connected: bool, force: bool = False) -> None:
        """출구 LPR(DEV-LPR-2, udp_port=7090) 장비의 is_connected 플래그를 갱신.
        device_clients.devices_ids 에 있는 장비만 갱신한다.
        """
        if self._lpr_exit_connected == connected and not force:
            return
        self._lpr_exit_connected = connected

        devices: List[Dict[str, Any]] = self._api.list_devices()
        managed = self._managed_device_ids()
        changed = False
        for d in devices:
            typ = (d.get("type") or "").lower()
            if typ in ("lpr_camera", "lpr_camera_server"):
                name = (d.get("name") or "").strip()
                guid = (d.get("device_guid") or "").strip()

                udp_port = None
                cfg = d.get("config")
                if isinstance(cfg, str):
                    try:
                        cfg_obj = json.loads(cfg)
                    except Exception:  # noqa: BLE001
                        cfg_obj = {}
                elif isinstance(cfg, dict):
                    cfg_obj = cfg
                else:
                    cfg_obj = {}
                try:
                    udp_port = int(cfg_obj.get("udp_port")) if "udp_port" in cfg_obj else None
                except Exception:
                    udp_port = None

                is_exit_by_name = "출구" in name
                is_exit_by_guid = guid.upper() in ("DEV-LPR-2",)

                is_exit = False
                if udp_port is not None:
                    is_exit = udp_port == settings.lpr_exit_udp_port
                else:
                    is_exit = is_exit_by_name or is_exit_by_guid

                if not is_exit:
                    continue
                if managed is not None and d.get("id") not in managed:
                    continue

                if bool(d.get("is_connected")) != connected:
                    self._api.update_device_is_connected(d, connected)
                    d["is_connected"] = connected
                    changed = True
        if changed:
            self._info.update_from_server(
                health=self._info.server_health,
                devices=devices,
            )

    def enqueue_lpr_enter_command(self, command: str) -> None:
        """테스트 UI 에서 호출: 다음 /api/device/command 폴링 때 전달할 명령을 큐에 적재."""
        self._lpr_command_queue.append(command)

    def _pop_lpr_command(self, guid: str) -> str | None:
        if not self._lpr_command_queue:
            return None
        # guid 를 사용해 향후 여러 LPR 카메라 구분 가능하도록 확장 여지를 남긴다.
        return self._lpr_command_queue.pop(0)

    # ───────── 종료 처리 ─────────
    def close(self) -> None:
        if self._lpr_udp_receiver:
            self._lpr_udp_receiver.stop()
            self._lpr_udp_receiver = None
        if self._lpr_exit_udp_receiver:
            self._lpr_exit_udp_receiver.stop()
            self._lpr_exit_udp_receiver = None
        self._api.close()


