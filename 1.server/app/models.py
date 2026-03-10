from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.orm import relationship

from .db import Base


class Device(Base):
    __tablename__ = "devices"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    type = Column(String(50), nullable=False)  # esp32, esp32-cam, arduino 등
    device_type = Column(String(10), nullable=False)  # 'CLIENT' or 'SERVER'
    connection_type = Column(String(20), nullable=False, default="ethernet")
    connection_detail = Column(String(100), nullable=True)
    control_method = Column(String(50), nullable=True)
    ip_address = Column(String(45), nullable=True)
    port_info = Column(String(50), nullable=True)  # ethernet: port, serial: port name
    is_connected = Column(Boolean, default=False)
    sensor_guids = Column(String(255), nullable=True)
    # devices.device_guid 컬럼과 매핑 (장비 단위 고유 GUID)
    device_guid = Column(String(64), nullable=True, index=True)
    config = Column(String(255), nullable=True)  # JSON 문자열로 간단 설정 저장
    is_active = Column(Boolean, default=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    events = relationship("EventLog", back_populates="device")


class ParkingSlot(Base):
    __tablename__ = "parking_slots"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)  # 예: A-01
    level = Column(String(20), nullable=True)
    is_occupied = Column(Boolean, default=False)
    # 센서(ESP32/Arduino) 연결 여부. 연결되지 않았으면 대시보드에서 회색으로 표시.
    sensor_connected = Column(Boolean, default=False)
    last_vehicle_plate = Column(String(20), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class EventLog(Base):
    __tablename__ = "event_logs"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    event_type = Column(String(50), nullable=False)  # ENTER, EXIT, ERROR, STATUS 등
    message = Column(String(255), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)

    device = relationship("Device", back_populates="events")


class Resident(Base):
    __tablename__ = "residents"

    id = Column(Integer, primary_key=True, index=True)
    unit_number = Column(String(20), nullable=False)  # 몇 호
    name = Column(String(50), nullable=False)
    phone = Column(String(20), nullable=False)
    car_plate = Column(String(20), nullable=False)
    balance = Column(Integer, default=0)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    rfid_cards = relationship(
        "RfidCard",
        back_populates="resident",
        cascade="all, delete-orphan",
    )


class RfidCard(Base):
    __tablename__ = "rfid_cards"

    id = Column(Integer, primary_key=True, index=True)
    card_uid = Column(String(64), unique=True, nullable=False)
    resident_id = Column(Integer, ForeignKey("residents.id"), nullable=True)
    is_active = Column(Boolean, default=True)
    description = Column(String(100), nullable=True)

    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )

    resident = relationship("Resident", back_populates="rfid_cards")


class Sensor(Base):
    __tablename__ = "sensors"

    id = Column(Integer, primary_key=True, index=True)
    guid = Column(String(32), nullable=False, unique=True)
    name = Column(String(50), nullable=False)
    sensor_type = Column(String(30), nullable=False)
    # 0: 연결안됨, 1: 닫힘, 2: 열림, 3: 자동
    sensor_states = Column(Integer, nullable=False, default=0)
    # 자동 모드일 때 게이트 실제 상태(0: 동작없음, 1: 열림, 2: 닫힘)
    gate_auto_state = Column(Integer, nullable=False, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    created_by = Column(String(50), nullable=False)


class DeviceClient(Base):
    __tablename__ = "device_clients"

    id = Column(Integer, primary_key=True, index=True)
    device_no = Column(String(50), nullable=False, unique=True)
    name = Column(String(100), nullable=False)
    devices_ids = Column(String(255), nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
    )


class ParkingRecord(Base):
    __tablename__ = "parking_records"

    record_id = Column(Integer, primary_key=True, autoincrement=True)
    license_plate = Column(String(15), nullable=False)
    entry_timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)
    exit_timestamp = Column(DateTime, nullable=True)
    is_registered = Column(Boolean, default=False, nullable=False)
    charge_amount = Column(Integer, default=0)
