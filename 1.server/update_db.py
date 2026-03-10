from app.db import engine
from sqlalchemy import text

try:
    with engine.connect() as conn:
        conn.execute(text("ALTER TABLE residents ADD COLUMN balance INTEGER DEFAULT 0;"))
        conn.commit()
    print("Success")
except Exception as e:
    print("Error:", e)
