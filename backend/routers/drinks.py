"""飲料 CRUD API。"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from backend.database import get_db
from backend.models import DrinkCreate, DrinkUpdate

router = APIRouter(prefix="/api/drinks", tags=["drinks"])


def _get_or_404(db: sqlite3.Connection, drink_id: int) -> sqlite3.Row:
    row = db.execute("SELECT * FROM drinks WHERE id = ?", (drink_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這杯飲料")
    return row


@router.get("")
def list_drinks(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM drinks ORDER BY category, name").fetchall()
    return [dict(row) for row in rows]


@router.get("/{drink_id}")
def get_drink(drink_id: int, db: sqlite3.Connection = Depends(get_db)):
    return dict(_get_or_404(db, drink_id))


@router.post("", status_code=201)
def create_drink(payload: DrinkCreate, db: sqlite3.Connection = Depends(get_db)):
    try:
        cur = db.execute(
            "INSERT INTO drinks (name, category, is_available) VALUES (?, ?, ?)",
            (payload.name, payload.category, int(payload.is_available)),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="飲料名稱已存在")
    return dict(_get_or_404(db, cur.lastrowid))


@router.patch("/{drink_id}")
def update_drink(
    drink_id: int, payload: DrinkUpdate, db: sqlite3.Connection = Depends(get_db)
):
    _get_or_404(db, drink_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有要更新的欄位")
    if "is_available" in fields:
        fields["is_available"] = int(fields["is_available"])
    sets = ", ".join(f"{name} = ?" for name in fields)
    try:
        db.execute(
            f"UPDATE drinks SET {sets} WHERE id = ?", (*fields.values(), drink_id)
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="飲料名稱已存在")
    return dict(_get_or_404(db, drink_id))


@router.delete("/{drink_id}", status_code=204)
def delete_drink(drink_id: int, db: sqlite3.Connection = Depends(get_db)):
    _get_or_404(db, drink_id)
    try:
        db.execute("DELETE FROM drinks WHERE id = ?", (drink_id,))
        db.commit()
    except sqlite3.IntegrityError:
        # 已有訂單引用這杯飲料時擋下刪除；請改用 is_available=false 下架
        raise HTTPException(
            status_code=409, detail="此飲料已有訂單紀錄，無法刪除；可改為停售"
        )
