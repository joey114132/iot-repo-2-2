from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class DeviceBase(BaseModel):
    name: str
    type: str
    device_type: str  # 'CLIENT' or 'SERVER'
    connection_type: str = "ethernet"
    connection_detail: Optional[str] = None  # 예: 'tcp', 'udp,tcp'
    control_method: Optional[str] = None     # 예: 'socket', 'restapi'
    ip_address: Optional[str] = None
    port_info: Optional[str] = None
    is_connected: bool = False
    sensor_guids: Optional[str] = None
    # devices.device_guid 컬럼과 매핑 (장비 단위 고유 GUID)
    device_guid: Optional[str] = None
    config: Optional[str] = None
    is_active: bool = True


class DeviceCreate(DeviceBase):
    pass


class DeviceRead(DeviceBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DeviceIpUpdate(BaseModel):
    """device_guid 기준으로 IP 만 갱신할 때 사용하는 스키마."""

    ip_address: str


class ParkingSlotBase(BaseModel):
    name: str
    level: Optional[str] = None
    sensor_connected: bool = False


class ParkingSlotCreate(ParkingSlotBase):
    pass


class ParkingSlotRead(ParkingSlotBase):
    id: int
    is_occupied: bool
    last_vehicle_plate: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class EventLogRead(BaseModel):
    id: int
    device_id: Optional[int] = None
    event_type: str
    message: Optional[str] = None
    created_at: datetime

    class Config:
        from_attributes = True


class ResidentBase(BaseModel):
    unit_number: str
    name: str
    phone: str
    car_plate: str
    balance: int = 0


class ResidentCreate(ResidentBase):
    pass


class ResidentRead(ResidentBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RfidCardBase(BaseModel):
    card_uid: str
    resident_id: Optional[int] = None
    is_active: bool = True
    description: Optional[str] = None


class RfidCardCreate(RfidCardBase):
    pass


class RfidCardRead(RfidCardBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DashboardSummary(BaseModel):
    total_slots: int
    occupied_slots: int
    free_slots: int
    active_devices: int
    recent_events: List[EventLogRead]
    entry_sensor_connected: bool = False
    exit_sensor_connected: bool = False
    entry_sensor_detected: bool = False
    exit_sensor_detected: bool = False
    operation_mode_on: bool = True
    gate_sensor_state: int = 1
    gate_auto_state: int = 0
    # 관리 클라이언트에서 슬롯별 센서 상태(디바이스 클라에서 올린 것)를
    # 한 번에 볼 수 있도록 상세 슬롯 목록도 포함
    slots: List[ParkingSlotRead] = []


class SensorBase(BaseModel):
    guid: str
    name: str
    sensor_type: str
    # 0: 연결안됨, 1: 닫힘, 2: 열림, 3: 자동
    sensor_states: int = 0
    # 자동 모드일 때 게이트 실제 상태(0: 동작없음, 1: 열림, 2: 닫힘)
    gate_auto_state: int = 0
    is_active: bool = True
    created_by: str


class SensorCreate(SensorBase):
    pass


class SensorRead(SensorBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class DeviceClientBase(BaseModel):
    device_no: str
    name: str
    devices_ids: Optional[str] = None
    is_active: bool = True


class DeviceClientCreate(DeviceClientBase):
    pass


class DeviceClientRead(DeviceClientBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ParkingRecordBase(BaseModel):
    license_plate: str
    is_registered: bool = False
    charge_amount: int = 0

class ParkingRecordCreate(ParkingRecordBase):
    entry_timestamp: Optional[datetime] = None

class ParkingRecordRead(ParkingRecordBase):
    record_id: int
    entry_timestamp: datetime
    exit_timestamp: Optional[datetime] = None

    class Config:
        from_attributes = True
