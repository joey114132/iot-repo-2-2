from typing import List

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from .. import models, schemas
from ..db import get_db


router = APIRouter(prefix="/residents", tags=["residents"])


@router.get("/", response_model=List[schemas.ResidentRead])
def list_residents(db: Session = Depends(get_db)):
    return db.query(models.Resident).order_by(models.Resident.unit_number).all()


@router.post(
    "/",
    response_model=schemas.ResidentRead,
    status_code=status.HTTP_201_CREATED,
)
def create_resident(
    resident_in: schemas.ResidentCreate,
    db: Session = Depends(get_db),
):
    resident = models.Resident(**resident_in.model_dump())
    db.add(resident)
    db.commit()
    db.refresh(resident)
    return resident


@router.put("/{resident_id}", response_model=schemas.ResidentRead)
def update_resident(
    resident_id: int,
    resident_in: schemas.ResidentCreate,
    db: Session = Depends(get_db),
):
    resident = (
        db.query(models.Resident)
        .filter(models.Resident.id == resident_id)
        .first()
    )
    if not resident:
        raise HTTPException(status_code=404, detail="Resident not found")

    for field, value in resident_in.model_dump().items():
        setattr(resident, field, value)

    db.add(resident)
    db.commit()
    db.refresh(resident)
    return resident


@router.delete("/{resident_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_resident(resident_id: int, db: Session = Depends(get_db)):
    resident = (
        db.query(models.Resident)
        .filter(models.Resident.id == resident_id)
        .first()
    )
    if not resident:
        raise HTTPException(status_code=404, detail="Resident not found")

    db.delete(resident)
    db.commit()
    return None


@router.get("/rfid", response_model=List[schemas.RfidCardRead])
def list_rfid_cards(db: Session = Depends(get_db)):
    return db.query(models.RfidCard).order_by(models.RfidCard.card_uid).all()


@router.post(
    "/rfid",
    response_model=schemas.RfidCardRead,
    status_code=status.HTTP_201_CREATED,
)
def create_rfid_card(
    card_in: schemas.RfidCardCreate,
    db: Session = Depends(get_db),
):
    # 간단히 UID 중복만 체크
    exists = (
        db.query(models.RfidCard)
        .filter(models.RfidCard.card_uid == card_in.card_uid)
        .first()
    )
    if exists:
        raise HTTPException(status_code=400, detail="card_uid already exists")

    card = models.RfidCard(**card_in.model_dump())
    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@router.put("/rfid/{card_id}", response_model=schemas.RfidCardRead)
def update_rfid_card(
    card_id: int,
    card_in: schemas.RfidCardCreate,
    db: Session = Depends(get_db),
):
    card = (
        db.query(models.RfidCard)
        .filter(models.RfidCard.id == card_id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="RFID card not found")

    for field, value in card_in.model_dump().items():
        setattr(card, field, value)

    db.add(card)
    db.commit()
    db.refresh(card)
    return card


@router.delete("/rfid/{card_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_rfid_card(card_id: int, db: Session = Depends(get_db)):
    card = (
        db.query(models.RfidCard)
        .filter(models.RfidCard.id == card_id)
        .first()
    )
    if not card:
        raise HTTPException(status_code=404, detail="RFID card not found")

    db.delete(card)
    db.commit()
    return None


@router.get("/rfid/by-uid/{card_uid}", response_model=schemas.ResidentRead)
def get_resident_by_card_uid(card_uid: str, db: Session = Depends(get_db)):
    """
    RFID 카드 UID 로 연결된 입주민 정보를 조회한다.
    - 카드가 없거나 resident_id 가 비어 있으면 404 반환.
    """
    card = (
        db.query(models.RfidCard)
        .filter(models.RfidCard.card_uid == card_uid)
        .first()
    )
    if not card or not card.resident_id:
        raise HTTPException(status_code=404, detail="Resident not found for this card UID")

    resident = (
        db.query(models.Resident)
        .filter(models.Resident.id == card.resident_id)
        .first()
    )
    if not resident:
        raise HTTPException(status_code=404, detail="Resident not found")

    return resident

