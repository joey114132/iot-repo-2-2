from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from .. import models, schemas
from ..config import set_env_value, settings
from ..db import get_db


router = APIRouter(prefix="/parking", tags=["parking"])

# 입/출차 감지 센서 상태(대시보드 버튼 전용). T1/T2 슬롯과 분리해서 관리한다.
ENTRY_EXIT_SENSOR_STATE = {
    "entry_sensor_connected": False,
    "exit_sensor_connected": False,
    "entry_sensor_detected": False,
    "exit_sensor_detected": False,
}
OPERATION_MODE_ON = settings.operation_mode_on
# 0: 연결안됨, 1: 닫힘, 2: 열림, 3: 자동
GATE_SENSOR_STATE = settings.gate_sensor_state
# 0: 동작하지 않음, 1: 열림, 2: 닫힘
GATE_AUTO_STATE = settings.gate_auto_state


@router.get("/slots", response_model=List[schemas.ParkingSlotRead])
def list_slots(db: Session = Depends(get_db)):
    return db.query(models.ParkingSlot).all()


@router.post("/slots", response_model=schemas.ParkingSlotRead)
def create_slot(slot_in: schemas.ParkingSlotCreate, db: Session = Depends(get_db)):
    slot = models.ParkingSlot(**slot_in.model_dump())
    db.add(slot)
    db.commit()
    db.refresh(slot)
    return slot


@router.post("/slots/{slot_id}/occupy")
def occupy_slot(slot_id: int, plate: str | None = None, db: Session = Depends(get_db)):
    slot = db.query(models.ParkingSlot).filter(models.ParkingSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    slot.is_occupied = True
    slot.sensor_connected = True
    slot.last_vehicle_plate = plate
    db.add(slot)
    db.commit()
    return {"ok": True}


@router.post("/slots/{slot_id}/release")
def release_slot(slot_id: int, db: Session = Depends(get_db)):
    slot = db.query(models.ParkingSlot).filter(models.ParkingSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    slot.is_occupied = False
    slot.sensor_connected = True
    db.add(slot)
    db.commit()
    return {"ok": True}


@router.post("/slots/{slot_id}/sensor-connected")
def set_slot_sensor_connected(
    slot_id: int,
    connected: bool,
    db: Session = Depends(get_db),
):
    slot = db.query(models.ParkingSlot).filter(models.ParkingSlot.id == slot_id).first()
    if not slot:
        raise HTTPException(status_code=404, detail="Slot not found")
    slot.sensor_connected = connected
    db.add(slot)
    db.commit()
    return {"ok": True}


@router.post("/entry-exit-sensors")
def set_entry_exit_sensor_state(
    entry_sensor_connected: bool | None = None,
    exit_sensor_connected: bool | None = None,
    entry_sensor_detected: bool | None = None,
    exit_sensor_detected: bool | None = None,
):
    if entry_sensor_connected is not None:
        ENTRY_EXIT_SENSOR_STATE["entry_sensor_connected"] = entry_sensor_connected
    if exit_sensor_connected is not None:
        ENTRY_EXIT_SENSOR_STATE["exit_sensor_connected"] = exit_sensor_connected
    if entry_sensor_detected is not None:
        ENTRY_EXIT_SENSOR_STATE["entry_sensor_detected"] = entry_sensor_detected
    if exit_sensor_detected is not None:
        ENTRY_EXIT_SENSOR_STATE["exit_sensor_detected"] = exit_sensor_detected
    return {"ok": True, **ENTRY_EXIT_SENSOR_STATE}


@router.get("/operation-mode")
def get_operation_mode():
    return {"operation_mode_on": OPERATION_MODE_ON}


@router.post("/operation-mode")
def set_operation_mode(operation_mode_on: bool):
    global OPERATION_MODE_ON
    OPERATION_MODE_ON = operation_mode_on
    set_env_value("OPERATION_MODE_ON", "true" if OPERATION_MODE_ON else "false")
    return {"ok": True, "operation_mode_on": OPERATION_MODE_ON}


@router.get("/gate-state")
def get_gate_state():
    return {
        "gate_sensor_state": GATE_SENSOR_STATE,
        "gate_auto_state": GATE_AUTO_STATE,
    }


@router.post("/gate-state")
def set_gate_state(
    gate_sensor_state: int | None = None,
    gate_auto_state: int | None = None,
):
    global GATE_SENSOR_STATE, GATE_AUTO_STATE

    if gate_sensor_state is not None:
        GATE_SENSOR_STATE = int(gate_sensor_state)
        set_env_value("GATE_SENSOR_STATE", str(GATE_SENSOR_STATE))
    if gate_auto_state is not None:
        GATE_AUTO_STATE = int(gate_auto_state)
        set_env_value("GATE_AUTO_STATE", str(GATE_AUTO_STATE))

    return {
        "ok": True,
        "gate_sensor_state": GATE_SENSOR_STATE,
        "gate_auto_state": GATE_AUTO_STATE,
    }


@router.get("/dashboard", response_model=schemas.DashboardSummary)
def dashboard_summary(db: Session = Depends(get_db)):
    total_slots = db.query(func.count(models.ParkingSlot.id)).scalar() or 0
    occupied_slots = (
        db.query(func.count(models.ParkingSlot.id))
        .filter(models.ParkingSlot.is_occupied.is_(True))
        .scalar()
        or 0
    )
    active_devices = (
        db.query(func.count(models.Device.id))
        .filter(models.Device.is_active.is_(True))
        .scalar()
        or 0
    )
    recent_events = (
        db.query(models.EventLog)
        .order_by(models.EventLog.created_at.desc())
        .limit(20)
        .all()
    )
    slots = db.query(models.ParkingSlot).all()

    return schemas.DashboardSummary(
        total_slots=total_slots,
        occupied_slots=occupied_slots,
        free_slots=total_slots - occupied_slots,
        active_devices=active_devices,
        recent_events=recent_events,
        entry_sensor_connected=ENTRY_EXIT_SENSOR_STATE["entry_sensor_connected"],
        exit_sensor_connected=ENTRY_EXIT_SENSOR_STATE["exit_sensor_connected"],
        entry_sensor_detected=ENTRY_EXIT_SENSOR_STATE["entry_sensor_detected"],
        exit_sensor_detected=ENTRY_EXIT_SENSOR_STATE["exit_sensor_detected"],
        operation_mode_on=OPERATION_MODE_ON,
        gate_sensor_state=GATE_SENSOR_STATE,
        gate_auto_state=GATE_AUTO_STATE,
        slots=slots,
    )


import math
from datetime import datetime
from pydantic import BaseModel

class EntryEvent(BaseModel):
    license_plate: str

class ExitEvent(BaseModel):
    license_plate: str
    ext_rfid_registered: bool = False

class PaymentEvent(BaseModel):
    license_plate: str
    amount_paid: int


@router.post("/events/entry")
def handle_entry(event: EntryEvent, db: Session = Depends(get_db)):
    entry_time = datetime.utcnow()
    resident = db.query(models.Resident).filter(models.Resident.car_plate == event.license_plate).first()
    is_reg = bool(resident)

    record = models.ParkingRecord(
        license_plate=event.license_plate,
        entry_timestamp=entry_time,
        is_registered=is_reg
    )
    db.add(record)
    db.commit()

    global GATE_AUTO_STATE
    GATE_AUTO_STATE = 1
    set_env_value("GATE_AUTO_STATE", "1")

    return {"ok": True, "message": f"Vehicle {event.license_plate} entered.", "gate": "open"}


@router.post("/events/exit")
def handle_exit(event: ExitEvent, db: Session = Depends(get_db)):
    record = db.query(models.ParkingRecord).filter(
        models.ParkingRecord.license_plate == event.license_plate,
        models.ParkingRecord.exit_timestamp.is_(None)
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="Active parking record not found")

    global GATE_AUTO_STATE

    if event.ext_rfid_registered or record.is_registered:
        # Fetch the resident to check and deduct balance
        resident = db.query(models.Resident).filter(models.Resident.car_plate == record.license_plate).first()
        if resident:
            FEE_PER_INC = 500
            MIN_PER_INC = 10
            exit_time = datetime.utcnow()
            duration = exit_time - record.entry_timestamp
            duration_minutes = duration.total_seconds() / 60.0
            
            GRACE_PERIOD = 5
            if duration_minutes <= GRACE_PERIOD:
                total_fee = 0
            else:
                increments = math.ceil(duration_minutes / MIN_PER_INC)
                total_fee = increments * FEE_PER_INC

            if resident.balance >= total_fee:
                resident.balance -= total_fee
                record.exit_timestamp = exit_time
                record.charge_amount = 0  # Paid from balance
                db.commit()
                GATE_AUTO_STATE = 1
                set_env_value("GATE_AUTO_STATE", "1")
                return {"ok": True, "message": f"Registered vehicle. Paid {total_fee} from balance. Gate opening.", "charge": 0}
            else:
                # Not enough balance, fall through to normal parking flow
                record.charge_amount = total_fee
                db.commit()
                return {"ok": False, "message": "Fee required. Insufficient resident balance. Gate closed.", "charge": total_fee}
        else:
            # Fallback if resident somehow not found despite is_registered
            record.exit_timestamp = datetime.utcnow()
            record.charge_amount = 0
            db.commit()
            GATE_AUTO_STATE = 1
            set_env_value("GATE_AUTO_STATE", "1")
            return {"ok": True, "message": "Registered vehicle. Gate opening.", "charge": 0}

    exit_time = datetime.utcnow()
    duration = exit_time - record.entry_timestamp
    duration_minutes = duration.total_seconds() / 60.0

    GRACE_PERIOD = 5
    if duration_minutes <= GRACE_PERIOD:
        record.exit_timestamp = exit_time
        record.charge_amount = 0
        db.commit()
        GATE_AUTO_STATE = 1
        set_env_value("GATE_AUTO_STATE", "1")
        return {"ok": True, "message": "Under grace period. Gate opening.", "charge": 0}

    FEE_PER_INC = 500
    MIN_PER_INC = 10
    increments = math.ceil(duration_minutes / MIN_PER_INC)
    total_fee = increments * FEE_PER_INC

    record.charge_amount = total_fee
    db.commit()
    return {"ok": True, "message": "Fee required. Gate closed.", "charge": total_fee}


@router.post("/events/payment_cleared")
def handle_payment(event: PaymentEvent, db: Session = Depends(get_db)):
    record = db.query(models.ParkingRecord).filter(
        models.ParkingRecord.license_plate == event.license_plate,
        models.ParkingRecord.exit_timestamp.is_(None)
    ).first()

    if not record:
        raise HTTPException(status_code=404, detail="Active parking record not found")

    global GATE_AUTO_STATE
    if event.amount_paid >= record.charge_amount:
        record.exit_timestamp = datetime.utcnow()
        db.commit()
        GATE_AUTO_STATE = 1
        set_env_value("GATE_AUTO_STATE", "1")
        return {"ok": True, "message": "Payment cleared. Gate opening."}

    return {"ok": False, "message": "Insufficient payment", "owed": record.charge_amount}


class BalanceUpdate(BaseModel):
    amount: int

@router.post("/residents/{resident_id}/add_balance")
def add_resident_balance(resident_id: int, payload: BalanceUpdate, db: Session = Depends(get_db)):
    resident = db.query(models.Resident).filter(models.Resident.id == resident_id).first()
    if not resident:
        raise HTTPException(status_code=404, detail="Resident not found")
    
    resident.balance += payload.amount
    db.commit()
    db.refresh(resident)
    return {"ok": True, "message": f"Added {payload.amount} balance", "new_balance": resident.balance}
