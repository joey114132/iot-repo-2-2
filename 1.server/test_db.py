from app.db import SessionLocal
from app import models
try:
    with SessionLocal() as db:
        record = db.query(models.ParkingRecord).first()
        print(f"Connected! Record: {record}")
except Exception as e:
    print(f"Failed: {e}")
