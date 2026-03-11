from __future__ import annotations

"""
ESP32 보드1(입구 차단기)와 TCP 소켓으로 통신하는 백엔드 서버 스레드.

- ESP32 스케치: 4.arduino/esp32_board1/esp32_board1_1.ino
- 포트: 8080 (ESP32 → device_client PC 192.168.0.149:8080 접속)

기능:
- PING/PONG(keepalive)
- IR/시스템 이벤트 수신
- RFID UID/SiteID 수신
- 장비 목록(TYPE_DEVICE_LIST) 수신
- 게이트 열기/닫기/카드 SiteID 쓰기 명령 전송

UI 와의 연결은 콜백(on_log, on_device_list) 으로만 처리한다.
"""

import socket
import struct
import threading
import time
from typing import Any, Callable, Dict, List, Optional

STRUCT_FORMAT = "<B 32s"  # type(1) + payload(32)
SIZE = struct.calcsize(STRUCT_FORMAT)

TYPE_PING = 0xFE
TYPE_PONG = 0xFD
TYPE_IR_EVENT = 0
TYPE_RFID = 1
TYPE_CMD_OPEN = 2
TYPE_CMD_CLOSE = 5
TYPE_CMD_WRITE = 3
TYPE_DEVICE_LIST = 4
TYPE_DEV_REGISTER = 6   # 장비 등록 패킷 (device_guid, device_name)
TYPE_CMD_DISPLAY = 7    # 서버 → 출구 보드(DEV-GATE-2): LCD 2줄 출력
ENTRY_GATE_GUID = "DEV-GATE-1"
EXIT_GATE_GUID = "DEV-GATE-2"

EV_NAMES = {
    1: "ENTRY_DETECTED",
    2: "EXIT_DETECTED",
    3: "RFID",
    4: "GATE_OPEN",
    5: "GATE_CLOSED",
    99: "H/W SENSOR ERROR",
}

KEEPALIVE_TIMEOUT_SEC = 12
RECV_CHECK_INTERVAL_SEC = 2


class Esp32GateServer(threading.Thread):
    """
    ESP32 입구 차단기 보드와 통신하는 소켓 서버 스레드.

    - device_client PC 에서 0.0.0.0:8080 을 리슨하고,
      ESP32 가 클라이언트로 접속하는 구조.
    - 수신 이벤트는 on_log / on_device_list 콜백으로 전달한다.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8080,
        on_log: Optional[Callable[[str], None]] = None,
        on_device_list: Optional[Callable[[List[Dict[str, Any]]], None]] = None,
        on_state_change: Optional[Callable[[str, bool, Optional[str]], None]] = None,
        on_register: Optional[Callable[[str, str, str], None]] = None,
        on_parking_event: Optional[Callable[[str, bool], None]] = None,
        on_gate_motor_event: Optional[Callable[[str, str, str], None]] = None,
        on_gate_event: Optional[Callable[[int, str, str], None]] = None,
        on_rfid: Optional[Callable[[str], None]] = None,
    ) -> None:
        super().__init__(daemon=True)
        self._host = host
        self._port = port
        self._on_log = on_log or (lambda msg: None)
        self._on_device_list = on_device_list or (lambda lst: None)
        self._on_state_change = on_state_change or (lambda ip, connected, guid=None: None)
        # device_guid, device_name, ip 를 전달하는 콜백
        self._on_register = on_register or (lambda guid, name, ip: None)
        # 노상 주차면 이벤트(SPOT_1~4 OCCUPIED/EMPTY)를 서버/DB 동기화용으로 전달
        self._on_parking_event = on_parking_event or (lambda spot, occ: None)
        # 게이트 모터 이벤트(OPEN/CLOSED) 상태 전달
        self._on_gate_motor_event = on_gate_motor_event or (lambda state, src, detail: None)
        # 원본 게이트 이벤트(ev/src/ext) 전달 (APDS 감지 플래그 제어용)
        self._on_gate_event = on_gate_event or (lambda ev, src, ext: None)

        self._clients: Dict[tuple, socket.socket] = {}  # (ip, port) -> conn
        self._client_guids: Dict[tuple, str] = {}      # addr -> device_guid (DEV-GATE-1, DEV-GATE-2 등)
        self._clients_lock = threading.Lock()
        self._stop_flag = threading.Event()
        self._current_ip: Optional[str] = None  # 마지막 연결 IP (호환용)
        self._gate_state_lock = threading.Lock()
        # GUID별 게이트 상태 관리를 위해 딕셔너리로 변경
        self._gate_statuses: Dict[str, Dict[str, Any]] = {}
        self._pending_cmd: Optional[str] = None  # "OPEN" | "CLOSE"
        self._pending_target_guid: Optional[str] = None
        self._pending_result: Optional[Dict[str, Any]] = None
        self._pending_event = threading.Event()

    def _send_to_all(self, pkt_type: int, payload: bytes) -> None:
        """연결된 모든 클라이언트에 패킷 전송."""
        data = struct.pack(STRUCT_FORMAT, pkt_type, payload)
        with self._clients_lock:
            dead = []
            for a, conn in list(self._clients.items()):
                try:
                    conn.sendall(data)
                except OSError:
                    dead.append(a)
            for a in dead:
                self._clients.pop(a, None)

    def _send_to_guid(self, pkt_type: int, payload: bytes, target_guid: str) -> bool:
        """특정 device_guid 로 등록된 클라이언트 1대에만 패킷 전송."""
        data = struct.pack(STRUCT_FORMAT, pkt_type, payload)
        with self._clients_lock:
            for a, conn in list(self._clients.items()):
                if self._client_guids.get(a) != target_guid:
                    continue
                try:
                    conn.sendall(data)
                    return True
                except OSError:
                    return False
        return False

    # ───────── 외부 호출용 API (명령 전송) ─────────
    def _send_gate_command(
        self,
        pkt_type: int,
        cmd_name: str,
        wait_result_sec: float,
        target_guid: str = ENTRY_GATE_GUID,
    ) -> Dict[str, Any]:
        with self._clients_lock:
            target_connected = any(
                self._client_guids.get(a) == target_guid for a in self._clients
            )
        if not target_connected:
            with self._gate_state_lock:
                current_state = self._gate_statuses.get(target_guid, {}).get("state", "UNKNOWN")
            return {
                "ok": False,
                "command": cmd_name,
                "state": current_state,
                "detail": "NO_CLIENT",
                "message": f"게이트 보드({target_guid}) 미연결",
            }
        with self._gate_state_lock:
            self._pending_cmd = cmd_name
            self._pending_target_guid = target_guid
            self._pending_result = None
            self._pending_event.clear()
        try:
            sent = self._send_to_guid(pkt_type, b"\x00" * 32, target_guid)
            if not sent:
                raise OSError("target send failed")
            self._on_log(f"[CMD] 게이트 {cmd_name} 전송")
        except OSError:
            with self._gate_state_lock:
                self._pending_cmd = None
                self._pending_target_guid = None
                current_state = self._gate_statuses.get(target_guid, {}).get("state", "UNKNOWN")
            self._on_log(f"[CMD] 게이트 {cmd_name} 전송 실패 (소켓 에러)")
            return {
                "ok": False,
                "command": cmd_name,
                "state": current_state,
                "detail": "SEND_ERROR",
                "message": "명령 전송 실패",
            }

        if not self._pending_event.wait(wait_result_sec):
            with self._gate_state_lock:
                self._pending_cmd = None
                self._pending_target_guid = None
                current_state = self._gate_statuses.get(target_guid, {}).get("state", "UNKNOWN")
            return {
                "ok": False,
                "command": cmd_name,
                "state": current_state,
                "detail": "TIMEOUT",
                "message": f"응답 대기 타임아웃({wait_result_sec:.1f}s)",
            }

        with self._gate_state_lock:
            if self._pending_result is None:
                self._pending_target_guid = None
                current_state = self._gate_statuses.get(target_guid, {}).get("state", "UNKNOWN")
                return {
                    "ok": False,
                    "command": cmd_name,
                    "state": current_state,
                    "detail": "EMPTY_RESULT",
                    "message": "응답 수신 실패",
                }
            return dict(self._pending_result)

    def send_open_gate(self, wait_result_sec: float = 3.0, target_guid: str = ENTRY_GATE_GUID) -> Dict[str, Any]:
        return self._send_gate_command(TYPE_CMD_OPEN, "OPEN", wait_result_sec, target_guid)

    def send_close_gate(self, wait_result_sec: float = 3.0, target_guid: str = ENTRY_GATE_GUID) -> Dict[str, Any]:
        return self._send_gate_command(TYPE_CMD_CLOSE, "CLOSE", wait_result_sec, target_guid)

    def get_gate_motor_status(self, target_guid: str = ENTRY_GATE_GUID) -> Dict[str, Any]:
        with self._gate_state_lock:
            status = self._gate_statuses.get(target_guid)
            if status:
                return dict(status)
            return {
                "state": "UNKNOWN",
                "source": "",
                "detail": "",
                "updated_at": 0.0,
            }

    def send_write_siteid(self, site_id: str) -> None:
        with self._clients_lock:
            if not self._clients:
                return
        payload = b"\x00" + site_id.encode().ljust(16, b"\x00")[:16] + b"\x00" * 15
        try:
            self._send_to_all(TYPE_CMD_WRITE, payload)
            self._on_log(f"[CMD] 카드 SiteID 쓰기 명령 전송 (SiteID={site_id})")
        except OSError:
            self._on_log("[CMD] 카드 SiteID 쓰기 전송 실패 (소켓 에러)")

    def send_display(self, line1: str, line2: str) -> bool:
        """출구 차단기(DEV-GATE-2) LCD 2줄 출력 명령. 해당 guid 로 등록된 클라이언트에만 전송."""
        line1_b = line1.encode("utf-8", errors="replace")[:16].ljust(16, b"\x00")
        line2_b = line2.encode("utf-8", errors="replace")[:16].ljust(16, b"\x00")
        payload = line1_b + line2_b
        data = struct.pack(STRUCT_FORMAT, TYPE_CMD_DISPLAY, payload)
        with self._clients_lock:
            for a, conn in list(self._clients.items()):
                if self._client_guids.get(a) != EXIT_GATE_GUID:
                    continue
                try:
                    conn.sendall(data)
                    self._on_log(f"[CMD] 출구 LCD 전송: '{line1}' / '{line2}'")
                    return True
                except OSError:
                    self._on_log("[CMD] 출구 LCD 전송 실패 (소켓 에러)")
                    return False
        self._on_log(f"[CMD] 출구 보드({EXIT_GATE_GUID}) 미연결, LCD 전송 스킵")
        return False

    # ───────── 스레드 메인 루프 ─────────
    def _serve_client(self, conn: socket.socket, addr: tuple) -> None:
        """한 클라이언트를 담당하는 스레드: 등록 후 _handle_client 실행, 종료 시 해제."""
        conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        with self._clients_lock:
            self._clients[addr] = conn
            self._current_ip = addr[0]
        self._on_log(f"[GATE] ESP32 보드 연결됨 ({addr[0]}:{addr[1]})")
        self._on_state_change(addr[0], True, None)
        try:
            self._handle_client(conn, addr)
        finally:
            with self._clients_lock:
                guid = self._client_guids.get(addr)
                print(f"[DEBUG-CONN-TERM] ESP32 보드 연결 해제 (addr={addr}, guid={guid!r})")
                self._clients.pop(addr, None)
                self._client_guids.pop(addr, None)
                self._current_ip = next(iter(self._clients))[0] if self._clients else None
            try:
                self._on_state_change(addr[0], False, guid)
                conn.close()
            except Exception:
                pass
            self._on_log(f"[GATE] ESP32 보드 연결 종료 ({addr[0]}:{addr[1]})")
            self._on_state_change(addr[0], False, guid)

    def run(self) -> None:  # type: ignore[override]
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((self._host, self._port))
        server.listen(5)
        self._on_log(f"[GATE] ESP32 게이트 서버 리슨 시작 ({self._host}:{self._port})")

        try:
            while not self._stop_flag.is_set():
                try:
                    server.settimeout(1.0)
                    conn, addr = server.accept()
                    print(f"[TCP-DEBUG] Accepted connection from {addr}")
                except socket.timeout:
                    continue
                t = threading.Thread(target=self._serve_client, args=(conn, addr), daemon=True)
                t.start()
        finally:
            try:
                server.close()
            except Exception:
                pass

    def stop(self) -> None:
        self._stop_flag.set()
        with self._clients_lock:
            for conn in self._clients.values():
                try:
                    conn.close()
                except Exception:
                    pass
            self._clients.clear()
            self._client_guids.clear()
            self._current_ip = None

    # ───────── 내부 처리 ─────────
    def _handle_client(self, conn: socket.socket, addr: Any) -> None:
        conn.settimeout(RECV_CHECK_INTERVAL_SEC)
        last_activity = time.monotonic()
        device_list_buf: Dict[int, Dict[str, str]] = {}  # 연결별 버퍼

        try:
            while not self._stop_flag.is_set():
                try:
                    data = conn.recv(SIZE)
                except socket.timeout:
                    if time.monotonic() - last_activity > KEEPALIVE_TIMEOUT_SEC:
                        self._on_log("[GATE] keepalive 타임아웃, 연결 종료")
                        break
                    continue
                except OSError:
                    break

                if not data:
                    break

                last_activity = time.monotonic()
                typ = data[0]
                payload = data[1:33]

                # 1. PING → PONG
                if typ == TYPE_PING:
                    try:
                        conn.sendall(struct.pack(STRUCT_FORMAT, TYPE_PONG, b"\x00" * 32))
                    except OSError:
                        break
                    continue

                # 2. IR / 시스템 이벤트
                if typ == TYPE_IR_EVENT:
                    ev = payload[0]
                    src = payload[1:17].decode("utf-8", errors="ignore").strip("\x00 ")
                    ext = payload[17:32].decode("utf-8", errors="ignore").strip("\x00 ")
                    self._on_gate_event(ev, src, ext)

                    if ev == 99:
                        line = f"[ERROR] {src} 초기화 실패 ({ext})"
                    else:
                        name = EV_NAMES.get(ev, f"EV_{ev}")
                        line = f"[EVENT] {name} | {src}" + (f" | {ext}" if ext else "")
                    self._on_log(line)

                    if ev in (4, 5):
                        motor_state = "OPEN" if ev == 4 else "CLOSED"
                        client_guid = ""
                        with self._clients_lock:
                            client_guid = self._client_guids.get(addr, "")
                        with self._gate_state_lock:
                            # GUID별 상태 저장
                            self._gate_statuses[client_guid] = {
                                "state": motor_state,
                                "source": src,
                                "detail": ext,
                                "updated_at": time.time(),
                            }
                            
                            expected_ev = 4 if self._pending_cmd == "OPEN" else 5 if self._pending_cmd == "CLOSE" else None
                            if expected_ev == ev and (
                                self._pending_target_guid is None or self._pending_target_guid == client_guid
                            ):
                                self._pending_result = {
                                    "ok": True,
                                    "command": self._pending_cmd,
                                    "state": motor_state,
                                    "source": client_guid, # Use GUID as source for consistency
                                    "detail": ext or "ACK_OK",
                                    "message": "명령 수행 완료",
                                }
                                self._pending_cmd = None
                                self._pending_target_guid = None
                                self._pending_event.set()
                        if self._on_gate_motor_event:
                            self._on_gate_motor_event(motor_state, client_guid, ext)

                    if src.startswith("SPOT_") and ext in ("OCCUPIED", "EMPTY"):
                        is_occupied = ext == "OCCUPIED"
                        self._on_parking_event(src, is_occupied)
                    continue

                # 3. RFID
                if typ == TYPE_RFID:
                    mode = payload[0]
                    uid = payload[1:17].decode("utf-8", errors="ignore").strip("\x00 ")
                    siteid = payload[17:32].decode("utf-8", errors="ignore").strip("\x00 ")
                    self._on_log(f"[RFID] mode={mode} UID={uid} SiteID={siteid}")
                    if self.on_rfid:
                        self.on_rfid(uid)
                    continue

                # 4. 장비 목록 (연결별 버퍼 사용)
                if typ == TYPE_DEVICE_LIST:
                    idx = payload[0]
                    total = payload[1]
                    guid = payload[2:18].decode("utf-8", errors="ignore").strip("\x00 ")
                    name = payload[18:32].decode("utf-8", errors="ignore").strip("\x00 ")
                    device_list_buf[idx] = {"guid": guid, "name": name}
                    if len(device_list_buf) >= total:
                        lst = [device_list_buf[i] for i in range(total)]
                        device_list_buf.clear()
                        self._on_device_list(lst)
                    continue

                # 5. 장비 등록 패킷 (addr → guid 매핑 저장, 출구 LCD 명령 대상 식별용)
                if typ == TYPE_DEV_REGISTER:
                    guid = payload[0:16].decode("utf-8", errors="ignore").strip("\x00 ")
                    name = payload[16:32].decode("utf-8", errors="ignore").strip("\x00 ")
                    ip = addr[0]
                    with self._clients_lock:
                        self._client_guids[addr] = guid
                    self._on_log(
                        f"[REG] device_register 수신 guid={guid} name={name} ip={ip}"
                    )
                    self._on_register(guid, name, ip)
                    continue
        finally:
            pass

