from __future__ import annotations

import threading
import time

from info_manager import InfoManager
from transmission_manager import TransmissionManager
from esp32_gate_server import Esp32GateServer

ENTRY_GATE_GUID = "DEV-GATE-1"
EXIT_GATE_GUID = "DEV-GATE-2"
MANUAL_GATE_RESEND_INTERVAL_SEC = 2.0


class DeviceManager:
    """
    ESP32 / ESP32-CAM / 기타 장치를 관리하는 매니저의 뼈대.

    - 현재는 InfoManager / TransmissionManager 와의 연결만 잡아두고,
      실제 장치 제어 로직은 이후 단계에서 채워 넣는다.
    """

    def __init__(
        self,
        info_manager: InfoManager,
        transmission_manager: TransmissionManager,
    ) -> None:
        self._info = info_manager
        self._tx = transmission_manager
        self._gate_server: Esp32GateServer | None = None
        self._gate_logs: list[str] = []
        self._gate_devices: list[dict] = []
        self._gate_connected_ips: set[str] = set()  # 연결된 보드 IP 목록
        self._gate_motor_status: dict[str, object] = {
            "state": "UNKNOWN",
            "source": "",
            "detail": "",
            "updated_at": 0.0,
        }
        self._last_exit_lcd_signature: tuple[bool, int, int, int, str] | None = None
        self._last_manual_gate_cmd_at: float = 0.0
        # parking_slots, device 등록 정보 등을 위한 내부 상태
        self._parking_state: dict[str, bool] = {}

        # APDS 센서 상태 추적 및 스마트 게이트 제어용
        self._entry_sensor_blocked = False
        self._exit_sensor_blocked = False
        self._gate_opened_by_sensor: dict[str, bool] = {
            ENTRY_GATE_GUID: False,
            "DEV-GATE-2": False,
        }
        # 센서 트리거 게이트 닫기 후 sync 루프가 다시 열지 않도록 하는 타임스탬프
        self._sensor_close_until: float = 0.0
        # 백그라운드 OCR 워커 대신 다이얼로그 팝업 콜백 사용
        self.on_lpr_popup = None  # Callable[[bool, bool], None] (is_exit, show)
        # RFID 스캔 이벤트 콜백
        self.on_rfid_scan = None

    def start(self) -> None:
        """
        - ESP32 보드1(입구 차단기) TCP 서버 스레드 시작
        - 향후 ESP32-CAM/시리얼 장치 스레드도 여기서 시작 예정
        """
        if self._gate_server is None:
            self._gate_server = Esp32GateServer(
                host="0.0.0.0",
                port=8080,
                on_log=self._append_gate_log,
                on_device_list=self._set_gate_devices,
                on_state_change=self._on_gate_state_change,
                on_register=self._on_device_register,
                on_parking_event=self._on_parking_event,
                on_gate_motor_event=self._on_gate_motor_event,
                on_gate_event=self._on_gate_event,
                on_rfid=self._on_gate_rfid,
            )
            self._gate_server.start()

        # (백그라운드 OCR은 SIGABRT 오류로 인해 다이얼로그 팝업으로 대체됨)

        # 앱 기동 직후에는 DB 에 남아있던 이전 연결 상태를 신뢰하지 않고,
        # gate_controller / street_parking_controller 를 모두 끊김으로 초기화한다.
        # 이후 실제 소켓 연결/keep-alive 기준으로만 is_connected 를 다시 세팅.
        try:
            self._tx.set_gate_connected(False)
            self._tx.set_street_parking_connected(False)
            self._tx.set_tower_slots_inactive()
        except Exception:
            # 초기화 실패는 치명적이지 않으므로 로그만 남기고 무시
            self._append_gate_log("[GATE] 초기 연결 상태 리셋 실패 (DB)")

    def stop(self) -> None:
        """start 에서 시작한 장치 관련 스레드를 종료."""
        if self._gate_server is not None:
            self._gate_server.stop()
            self._gate_server = None
        try:
            self._tx.set_gate_connected(False)
            self._tx.set_street_parking_connected(False)
        except Exception:
            pass


    # ───────── ESP32 게이트 관련 헬퍼 (UI에서 사용) ─────────
    def _append_gate_log(self, msg: str) -> None:
        print(f"[GATE-LOG-TERM] {msg}")
        self._gate_logs.append(msg)
        # 로그 길이 무한 증가 방지
        if len(self._gate_logs) > 500:
            self._gate_logs = self._gate_logs[-200:]

    def _set_gate_devices(self, devices: list[dict]) -> None:
        self._gate_devices = devices

    def _on_gate_state_change(self, ip: str, connected: bool, guid: str | None = None) -> None:
        """ESP32 보드 연결/해제 시 GUID 기준으로 gate_controller 연결 상태 반영 (1/2 혼동 방지)."""
        if connected:
            self._gate_connected_ips.add(ip)
        else:
            self._gate_connected_ips.discard(ip)
        try:
            if guid:
                self._tx.set_gate_connected_by_guid(guid, connected)
            elif not connected:
                self._tx.set_gate_connected_by_ip(ip, False)
            self._tx.set_street_parking_connected_by_ip(ip, connected)
            self._tx.set_entry_exit_sensor_connected(connected)
            if guid == ENTRY_GATE_GUID:
                # 보드1_1 연결 정책:
                # - 연결됨: 기본 '닫힘(1)' 상태로 서버/UI 동기화 (OCR 승인 시에만 열림)
                # - 끊김: '연결 안됨(0)' 상태로 서버/UI 동기화
                self._tx.set_gate_state(
                    gate_sensor_state=1 if connected else 0,
                    gate_auto_state=0 if not connected else None,
                )
        except Exception:
            state = "연결" if connected else "해제"
            self._append_gate_log(f"[GATE] 서버 반영 실패 ip={ip} guid={guid} 상태={state}")

    # ───────── 장비 등록(DEVICE_GUID 기반 IP 갱신) ─────────
    def _on_device_register(self, device_guid: str, device_name: str, ip: str) -> None:
        """
        ESP32 보드에서 TYPE_DEV_REGISTER 패킷을 보냈을 때 호출된다.

        - device_guid / device_name / ip 를 받아서 TransmissionManager 에 전달
        - FastAPI → DB 의 devices.ip_address 를 갱신 (DHCP 대응)
        """
        self._append_gate_log(
            f"[REG] 등록 요청 수신 guid={device_guid} name={device_name} ip={ip}"
        )
        try:
            print(f"[DEBUG-REG] Calling update_device_ip_by_guid for {device_guid} -> {ip}")
            self._tx.update_device_ip_by_guid(device_guid, ip, device_name=device_name)
            print(f"[DEBUG-REG] Calling set_gate_connected_by_guid for {device_guid} -> True")
            self._tx.set_gate_connected_by_guid(device_guid, True)
            if device_guid == ENTRY_GATE_GUID:
                # 기보드 연결 시 닫힀 상태(1)로 시작 — OCR 승인 시에만 열림
                self._tx.set_gate_state(gate_sensor_state=1)
            self._append_gate_log(
                f"[REG] DB ip_address 갱신 완료 guid={device_guid} → {ip}"
            )
        except Exception as exc:
            self._append_gate_log(
                f"[REG] DB ip_address 갱신 실패 guid={device_guid} ({exc})"
            )

    # ───────── 노상 주차면 이벤트 처리 (parking_slots 동기화) ─────────
    def _on_parking_event(self, spot_name: str, is_occupied: bool) -> None:
        """
        ESP32 보드2(노상 주차면 센서 컨트롤러)에서 SPOT_1~4 이벤트가 올 때 호출된다.

        - 내부 상태(self._parking_state)에 기록
        - TransmissionManager 를 통해 /parking/slots API 호출 → DB parking_slots 동기화
        """
        self._parking_state[spot_name] = is_occupied
        state_txt = "OCCUPIED" if is_occupied else "EMPTY"
        self._append_gate_log(f"[PARKING] {spot_name} -> {state_txt}")

        # SPOT_1~4 → S1~S4 로 매핑
        mapping = {
            "SPOT_1": "S1",
            "SPOT_2": "S2",
            "SPOT_3": "S3",
            "SPOT_4": "S4",
        }
        slot_name = mapping.get(spot_name)
        if not slot_name:
            return

        try:
            # 번호판 정보는 현재 없으므로 plate=None
            self._tx.set_slot_occupied(slot_name, is_occupied, plate=None)
            self._append_gate_log(
                f"[PARKING] DB 슬롯 갱신 완료 slot={slot_name} occupied={is_occupied}"
            )
        except Exception as exc:  # noqa: BLE001
            self._append_gate_log(
                f"[PARKING] DB 슬롯 갱신 실패 slot={slot_name} ({exc})"
            )

    def get_gate_logs(self) -> list[str]:
        return list(self._gate_logs)

    def get_gate_devices(self) -> list[dict]:
        return list(self._gate_devices)

    def _on_gate_motor_event(self, state: str, src: str, detail: str) -> None:
        self._gate_motor_status = {
            "state": state,
            "source": src,
            "detail": detail,
            "updated_at": time.time(),
        }

    def _on_gate_event(self, ev: int, src: str, ext: str) -> None:
        """게이트 이벤트를 받아 입/출구 APDS 감지 기반 OCR 플래그를 갱신한다."""
        print(f"[DEBUG-EVENT] ev={ev}, src={src}, ext={ext}")
        
        # 입출구 게이트 센서 이벤트(EV_1, EV_2)가 아니면 OCR 트리거 무시
        if ev not in (1, 2):
            return

        is_exit = (ev == 2)
        target_guid = "DEV-GATE-2" if is_exit else ENTRY_GATE_GUID

        # ── 차량 감지 (DETECTED) ──
        if ext == "DETECTED":
            if is_exit:
                self._exit_sensor_blocked = True
            else:
                self._entry_sensor_blocked = True

            # 센서가 감지되는 동안: sync 루프가 게이트를 닫지 못하도록 매우 긴 시간 잠금
            self._sensor_close_until = time.time() + 9999.0
            self._gate_opened_by_sensor[target_guid] = True

            motor_status = self.get_gate_motor_status()
            current_state = str(motor_status.get("state", "UNKNOWN")).upper()
            self._append_gate_log(f"[GATE-LOG] {src} 감지 -> LPR OCR 트리거 (motor={current_state})")

            # APDS 플래그 설정 (테스트 다이얼로그용 OCR 허용 창 오픈)
            self._tx.mark_lpr_apds_detected(is_exit=is_exit)
            self._tx.pulse_entry_exit_sensor_detected(is_exit=is_exit)

            # 다이얼로그 팝업 호출 (백그라운드 OCR 대신 안전한 단일 UI 인스턴스 사용)
            if callable(self.on_lpr_popup):
                self.on_lpr_popup(is_exit, True)

        # ── 차량 해제 (CLEAR) ──
        elif ext == "CLEAR":
            if is_exit:
                self._exit_sensor_blocked = False
            else:
                self._entry_sensor_blocked = False

            # 차량 통과 시 다이얼로그 닫기
            if callable(self.on_lpr_popup):
                self.on_lpr_popup(is_exit, False)

            self._tx.pulse_entry_exit_sensor_detected(is_exit=is_exit)
            self._append_gate_log(f"[GATE-LOG] {src} 해제됨 → 5초 후 게이트 닫기 예약")

            # 5초 후 닫기: 차량이 완전히 지나갔는지 재확인 후 닫음
            def _delayed_close(guid=target_guid, exit_=is_exit, s=src) -> None:
                time.sleep(5.0)  # 5초 대기 (차량 통과 충분히 기다림)
                # 5초 사이에 또 차량이 들어왔으면 닫지 않는다
                still_blocked = self._exit_sensor_blocked if exit_ else self._entry_sensor_blocked
                if still_blocked:
                    self._append_gate_log(f"[GATE-LOG] {s} 닫기 취소 (센서 재감지됨)")
                    return
                self._append_gate_log(f"[GATE-LOG] {s} 5초 경과 → 게이트 자동 닫기")
                self.close_gate(target_guid=guid)
                self._sensor_close_until = time.time() + 10.0  # 닫은 후 10초 추가 잠금
                self._tx.set_gate_state(gate_sensor_state=1)
                self._tx.set_gate_auto_state(2)
                self._gate_opened_by_sensor[guid] = False

            threading.Thread(target=_delayed_close, daemon=True).start()

        return dict(self._gate_motor_status)

    def open_gate(self, target_guid: str = ENTRY_GATE_GUID) -> dict[str, object]:
        if not self._gate_server:
            return {"ok": False, "state": "UNKNOWN", "detail": "NO_SERVER"}
        result = self._gate_server.send_open_gate(target_guid=target_guid)
        self._gate_motor_status = self._gate_server.get_gate_motor_status()
        self._append_gate_log(
            f"[CMD-RESULT] {target_guid} OPEN ok={result.get('ok')} state={result.get('state')} detail={result.get('detail')}"
        )
        # OCR 가 게이트를 열 때마다 sync 루프 잠금을 10초 연장
        # → 차가 카메라 앞에 있는 동안 OCR 이 계속 열 때마다 갱신되므로 게이트가 계속 열려 있음
        if result.get("ok") or result.get("state") in ("OPEN", "ALREADY_OPEN"):
            self._sensor_close_until = max(
                self._sensor_close_until, time.time() + 10.0
            )
        return result

    def close_gate(self, target_guid: str = ENTRY_GATE_GUID) -> dict[str, object]:
        if not self._gate_server:
            return {"ok": False, "state": "UNKNOWN", "detail": "NO_SERVER"}
        result = self._gate_server.send_close_gate(target_guid=target_guid)
        self._gate_motor_status = self._gate_server.get_gate_motor_status()
        self._append_gate_log(
            f"[CMD-RESULT] {target_guid} CLOSE ok={result.get('ok')} state={result.get('state')} detail={result.get('detail')}"
        )
        return result

    def sync_entry_gate_mode(
        self,
        gate_connected: bool,
        gate_sensor_state: int,
        entry_sensor_detected: bool,
        exit_sensor_detected: bool,
        gate_auto_state_from_server: int = 0,
    ) -> None:
        """
        대시보드의 차단기 상태(닫힘/열림/자동)를 실제 보드1_1 모터 상태와 동기화한다.
        - 연결 해제 시: 서버 상태를 0(연결 안됨)으로 강제
        - 연결 시 0이면: 기본값 2(열림)으로 복구
        - 수동(1/2) 선택 시: 해당 상태를 유지하도록 주기적으로 재명령
        - 자동(3) 선택 시: 서버의 gate_auto_state 가 1(열림)이면 열고, 2(닫힘)이면 닫음
        """
        if not gate_connected:
            self._tx.set_gate_state(gate_sensor_state=0, gate_auto_state=0)
            return

        # 차량이 센서 위에 있는 동안, 또는 닫기 잠금 시간 동안 sync 루프 차단
        if self._entry_sensor_blocked or self._exit_sensor_blocked:
            return
        if time.time() < self._sensor_close_until:
            return

        target_state = int(gate_sensor_state)
        if target_state == 0:
            # 미연결/알 수 없는 상태인 실제: 기본 '닫힀(1)'로 복구 (이전데비 OPEN 이었음)
            self._tx.set_gate_state(gate_sensor_state=1)
            target_state = 1

        if target_state in (1, 2):
            desired_motor_state = "CLOSED" if target_state == 1 else "OPEN"
            # 수동 모드일 때는 보드가 알아서 멈추지만, 서버 상태 동기화를 위해 auto_state 도 맞춰준다.
            desired_auto_state = 2 if target_state == 1 else 1
        elif target_state == 3:
            # 자동 모드: 서버(LPR/결제 로직)에서 결정한 결론(GATE_AUTO_STATE)을 따른다.
            # 1: 열림, 2: 닫힘
            if gate_auto_state_from_server == 1:
                desired_motor_state = "OPEN"
            elif gate_auto_state_from_server == 2:
                desired_motor_state = "CLOSED"
            else:
                # 서버에 명시적 명령이 없으면 기본 닫힘
                desired_motor_state = "CLOSED"
            desired_auto_state = gate_auto_state_from_server
        else:
            return

        current_motor_state = str(self._gate_motor_status.get("state") or "").upper()
        now = time.time()
        if (
            current_motor_state == desired_motor_state
            and (now - self._last_manual_gate_cmd_at) < MANUAL_GATE_RESEND_INTERVAL_SEC
        ):
            return
        if (now - self._last_manual_gate_cmd_at) < MANUAL_GATE_RESEND_INTERVAL_SEC:
            return

        if target_state == 3:
            result = self.open_gate() if desired_motor_state == "OPEN" else self.close_gate()
        else:
            result = self.close_gate() if target_state == 1 else self.open_gate()
        self._last_manual_gate_cmd_at = now
        if result.get("ok"):
            if target_state in (1, 2):
                self._tx.set_gate_state(gate_sensor_state=target_state)
            self._tx.set_gate_auto_state(desired_auto_state)

    def sync_exit_gate_mode(
        self,
        gate_connected: bool,
        gate_sensor_state: int,
        gate_auto_state_from_server: int = 0,
    ) -> None:
        """
        출구 차단기(DEV-GATE-2)를 서버 상태와 동기화한다.
        """
        if not gate_connected:
            return

        target_state = int(gate_sensor_state)
        # 출구는 수동 모드(1, 2) 보다는 서버 결론(auto_state)을 따르는 것이 핵심
        if target_state == 3:
            if gate_auto_state_from_server == 1:
                desired_motor_state = "OPEN"
            elif gate_auto_state_from_server == 2:
                desired_motor_state = "CLOSED"
            else:
                desired_motor_state = "CLOSED"
            
            # 현재 상태와 비교하여 불필요한 명령 중복 방지
            current_motor_state = str(self._gate_motor_status.get("state") or "").upper()
            now = time.time()
            if current_motor_state == desired_motor_state and (now - self._last_manual_gate_cmd_at) < MANUAL_GATE_RESEND_INTERVAL_SEC:
                return

            if desired_motor_state == "OPEN":
                result = self.open_gate(target_guid=EXIT_GATE_GUID)
            else:
                result = self.close_gate(target_guid=EXIT_GATE_GUID)
            
            if result.get("ok"):
                self._last_manual_gate_cmd_at = now
                # 동기화 완료 후 서버 상태도 맞춰준다 (혹시라도 sensor 에 의해 닫힌 경우 등 대응)
                self._tx.set_gate_auto_state(gate_auto_state_from_server)

    def write_siteid(self, site_id: str) -> None:
        if self._gate_server:
            self._gate_server.send_write_siteid(site_id)

    def send_exit_display(self, line1: str, line2: str) -> bool:
        """출구 차단기(esp32_board1_2, DEV-GATE-2) LCD 2줄 출력 명령."""
        if self._gate_server:
            return self._gate_server.send_display(line1, line2)
        return False

    def sync_exit_lcd_base(
        self,
        operation_mode_on: bool,
        free_slots: int,
        gate_sensor_state: int,
        gate_auto_state: int,
        message_type: str = "default",
    ) -> None:
        """
        출구 보드 LCD 기본 메시지 동기화.
        - message_type 으로 향후 입차 상황별 메시지 분기 확장 가능
        """
        signature = (operation_mode_on, int(free_slots), int(gate_sensor_state), int(gate_auto_state), message_type)
        if self._last_exit_lcd_signature == signature:
            return

        # 0: 연결안됨, 1: 닫힘, 2: 열림, 3: 자동
        # 자동(3)일 때만 운영상태 기반 메시지/자동게이트 상태를 동기화한다.
        if int(gate_sensor_state) == 3:
            if operation_mode_on:
                line1 = "Welcom PARKING"
                line2 = f"empty {int(free_slots)}"
                if int(gate_auto_state) != 1:
                    self._tx.set_gate_auto_state(1)
            else:
                line1 = "PARKING Closed."
                line2 = "Sorry."
                if int(gate_auto_state) != 2:
                    self._tx.set_gate_auto_state(2)
        else:
            # 수동/미연결 모드에서는 기본 베이스만 제공(향후 타입별 확장 포인트)
            if int(gate_sensor_state) == 1:
                line1 = "GATE MANUAL"
                line2 = "CLOSED"
            elif int(gate_sensor_state) == 2:
                line1 = "GATE MANUAL"
                line2 = "OPEN"
            else:
                line1 = "GATE OFFLINE"
                line2 = "CHECK CONNECT"

        sent = self.send_exit_display(line1[:16], line2[:16])
        if sent:
            self._last_exit_lcd_signature = signature


