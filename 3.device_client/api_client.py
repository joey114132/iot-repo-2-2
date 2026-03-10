from typing import Any, Dict

import httpx

from config import settings


class DeviceApiClient:
    """디바이스 PC에서 서버(FastAPI)로 상태를 올리는 용도."""

    def __init__(self) -> None:
        self._client = httpx.Client(base_url=settings.server_base_url, timeout=5.0)

    def health(self) -> Dict[str, Any]:
        resp = self._client.get("/health")
        resp.raise_for_status()
        return resp.json()

    def get_dashboard(self) -> Dict[str, Any]:
        resp = self._client.get("/parking/dashboard")
        resp.raise_for_status()
        return resp.json()

    def set_gate_state(
        self,
        *,
        gate_sensor_state: int | None = None,
        gate_auto_state: int | None = None,
    ) -> Dict[str, Any]:
        params: dict[str, str] = {}
        if gate_sensor_state is not None:
            params["gate_sensor_state"] = str(int(gate_sensor_state))
        if gate_auto_state is not None:
            params["gate_auto_state"] = str(int(gate_auto_state))
        resp = self._client.post("/parking/gate-state", params=params)
        resp.raise_for_status()
        return resp.json()

    def set_slot_occupied(self, slot_name: str, occupied: bool, plate: str | None = None) -> None:
        """슬롯 이름(S1~S4, T1~T6) 기준으로 점유 상태를 서버에 반영."""
        # 슬롯 id를 얻기 위해 이름으로 조회
        resp = self._client.get("/parking/slots")
        resp.raise_for_status()
        slots = resp.json()
        slot_id = None
        for s in slots:
            if s.get("name") == slot_name:
                slot_id = s.get("id")
                break
        if slot_id is None:
            return

        if occupied:
            params = {}
            if plate:
                params["plate"] = plate
            self._client.post(f"/parking/slots/{slot_id}/occupy", params=params)
        else:
            self._client.post(f"/parking/slots/{slot_id}/release")

    def set_slot_sensor_connected(self, slot_name: str, connected: bool) -> None:
        """슬롯 이름 기준으로 sensor_connected 상태만 반영."""
        resp = self._client.get("/parking/slots")
        resp.raise_for_status()
        slots = resp.json()
        slot_id = None
        for s in slots:
            if s.get("name") == slot_name:
                slot_id = s.get("id")
                break
        if slot_id is None:
            return
        self._client.post(
            f"/parking/slots/{slot_id}/sensor-connected",
            params={"connected": str(connected).lower()},
        )

    def set_entry_exit_sensor_state(
        self,
        *,
        entry_sensor_connected: bool | None = None,
        exit_sensor_connected: bool | None = None,
        entry_sensor_detected: bool | None = None,
        exit_sensor_detected: bool | None = None,
    ) -> None:
        params: dict[str, str] = {}
        if entry_sensor_connected is not None:
            params["entry_sensor_connected"] = str(entry_sensor_connected).lower()
        if exit_sensor_connected is not None:
            params["exit_sensor_connected"] = str(exit_sensor_connected).lower()
        if entry_sensor_detected is not None:
            params["entry_sensor_detected"] = str(entry_sensor_detected).lower()
        if exit_sensor_detected is not None:
            params["exit_sensor_detected"] = str(exit_sensor_detected).lower()
        if not params:
            return
        self._client.post("/parking/entry-exit-sensors", params=params)

    def log_event(self, event_type: str, message: str = "") -> None:
        # 서버에 EventLog 전용 엔드포인트를 아직 안 만들었으므로
        # TODO: 필요 시 /events API 추가
        print(f"[EVENT] {event_type}: {message}")

    def record_entry(self, license_plate: str) -> Dict[str, Any]:
        """차량 입차 기록 요청"""
        resp = self._client.post("/parking/events/entry", json={"license_plate": license_plate})
        resp.raise_for_status()
        return resp.json()

    def record_exit(self, license_plate: str, ext_rfid_registered: bool = False) -> Dict[str, Any]:
        """차량 출차 기록 요청 (요금 계산 포함)"""
        resp = self._client.post(
            "/parking/events/exit",
            json={"license_plate": license_plate, "ext_rfid_registered": ext_rfid_registered}
        )
        resp.raise_for_status()
        return resp.json()

    def clear_payment(self, license_plate: str, amount_paid: int) -> Dict[str, Any]:
        """요금 결제 완료 처리"""
        resp = self._client.post(
            "/parking/events/payment_cleared",
            json={"license_plate": license_plate, "amount_paid": amount_paid}
        )
        resp.raise_for_status()
        return resp.json()

    # ─── devices / device_clients 연동 ─────────────────────────
    def list_device_clients(self) -> list[dict[str, Any]]:
        resp = self._client.get("/device-clients/")
        resp.raise_for_status()
        return resp.json()

    # ─── residents / rfid 연동 ──────────────────────────────
    def create_resident(self, resident_data: dict[str, Any]) -> dict[str, Any]:
        resp = self._client.post("/residents/", json=resident_data)
        resp.raise_for_status()
        return resp.json()

    def register_rfid_card(self, resident_id: int, card_uid: str, description: str = "") -> dict[str, Any]:
        resp = self._client.post("/residents/rfid", json={
            "card_uid": card_uid,
            "resident_id": resident_id,
            "description": description
        })
        resp.raise_for_status()
        return resp.json()

    def add_resident_balance(self, resident_id: int, amount: int) -> dict[str, Any]:
        resp = self._client.post(f"/parking/residents/{resident_id}/add_balance", json={"amount": amount})
        resp.raise_for_status()
        return resp.json()

    def list_devices(self) -> list[dict[str, Any]]:
        resp = self._client.get("/devices/")
        resp.raise_for_status()
        return resp.json()

    def update_device_is_connected(
        self,
        device: dict[str, Any],
        is_connected: bool,
    ) -> None:
        """
        devices 테이블의 is_connected 값을 갱신.

        서버 쪽 DeviceCreate 스키마에 맞춰 전체 payload를 전송한다.
        """
        payload: dict[str, Any] = {
            "name": device.get("name"),
            "type": device.get("type"),
            "device_type": device.get("device_type"),
            "connection_type": device.get("connection_type") or "ethernet",
            "connection_detail": device.get("connection_detail"),
            "control_method": device.get("control_method"),
            "ip_address": device.get("ip_address"),
            "port_info": device.get("port_info"),
            "is_connected": is_connected,
            "sensor_guids": device.get("sensor_guids"),
            "config": device.get("config"),
            "is_active": device.get("is_active", True),
        }
        device_id = device.get("id")
        if device_id is None:
            return
        resp = self._client.put(f"/devices/{device_id}", json=payload)
        resp.raise_for_status()

    def update_device(self, device: dict[str, Any]) -> None:
        """
        devices 레코드 전체를 갱신할 때 사용.

        - ip_address, is_connected, config 등 여러 필드가 변경될 수 있다.
        - 서버 측 DeviceCreate / DeviceUpdate 스키마와 동일한 필드 구성을 사용한다.
        """
        device_id = device.get("id")
        if device_id is None:
            return

        payload: dict[str, Any] = {
            "name": device.get("name"),
            "type": device.get("type"),
            "device_type": device.get("device_type"),
            "connection_type": device.get("connection_type") or "ethernet",
            "connection_detail": device.get("connection_detail"),
            "control_method": device.get("control_method"),
            "ip_address": device.get("ip_address"),
            "port_info": device.get("port_info"),
            "is_connected": device.get("is_connected", False),
            "sensor_guids": device.get("sensor_guids"),
            "device_guid": device.get("device_guid"),
            "config": device.get("config"),
            "is_active": device.get("is_active", True),
        }

        resp = self._client.put(f"/devices/{device_id}", json=payload)
        resp.raise_for_status()

    def update_device_ip_by_guid(self, device_guid: str, ip: str) -> dict[str, Any]:
        """
        device_guid 기준으로 해당 devices 행의 ip_address 만 갱신.
        """
        resp = self._client.put(
            f"/devices/by-guid/{device_guid}/ip",
            json={"ip_address": ip},
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()

