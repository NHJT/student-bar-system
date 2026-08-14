"""飲料 CRUD API。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from backend.database import get_db, row_to_dict, rows_to_dicts
from backend.models import DrinkCreate, DrinkUpdate
from backend.ws import manager

router = APIRouter(prefix="/api/drinks", tags=["drinks"])


def _get_or_404(db: Connection, drink_id: int) -> dict:
    row = db.execute(
        text("SELECT * FROM drinks WHERE id = :id"), {"id": drink_id}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這杯飲料")
    return row_to_dict(row)


@router.get("")
def list_drinks(db: Connection = Depends(get_db)):
    return rows_to_dicts(
        db.execute(text("SELECT * FROM drinks ORDER BY category, name"))
    )


@router.get("/{drink_id}")
def get_drink(drink_id: int, db: Connection = Depends(get_db)):
    return _get_or_404(db, drink_id)


@router.post("", status_code=201)
async def create_drink(payload: DrinkCreate, db: Connection = Depends(get_db)):
    if not payload.name.strip() or not payload.category.strip():
        raise HTTPException(status_code=400, detail="名稱與分類不可空白")
    try:
        new_id = db.execute(
            text(
                "INSERT INTO drinks (name, category, is_available) "
                "VALUES (:name, :category, :is_available) RETURNING id"
            ),
            {
                "name": payload.name.strip(),
                "category": payload.category.strip(),
                "is_available": payload.is_available,
            },
        ).scalar_one()
        db.commit()
    except IntegrityError:
        db.rollback()  # Postgres 出錯後整個交易會停擺，必須先回滾
        raise HTTPException(status_code=409, detail="飲料名稱已存在")
    drink = _get_or_404(db, new_id)
    await manager.broadcast({"event": "drink_updated", "drink": drink})
    return drink


@router.patch("/{drink_id}")
async def update_drink(
    drink_id: int, payload: DrinkUpdate, db: Connection = Depends(get_db)
):
    _get_or_404(db, drink_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有要更新的欄位")
    for name in ("name", "category"):
        if name in fields:
            if not fields[name].strip():
                raise HTTPException(status_code=400, detail="名稱與分類不可空白")
            fields[name] = fields[name].strip()

    sets = ", ".join(f"{name} = :{name}" for name in fields)
    try:
        db.execute(
            text(f"UPDATE drinks SET {sets} WHERE id = :id"), {**fields, "id": drink_id}
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="飲料名稱已存在")
    drink = _get_or_404(db, drink_id)
    await manager.broadcast({"event": "drink_updated", "drink": drink})
    return drink


@router.delete("/{drink_id}", status_code=204)
async def delete_drink(drink_id: int, db: Connection = Depends(get_db)):
    _get_or_404(db, drink_id)
    try:
        # recipes.drink_id 設了 ON DELETE CASCADE，關聯的配方會一併刪除
        db.execute(text("DELETE FROM drinks WHERE id = :id"), {"id": drink_id})
        db.commit()
    except IntegrityError:
        db.rollback()
        # 已有訂單引用這杯飲料時擋下刪除；請改用 is_available=false 下架
        raise HTTPException(
            status_code=409, detail="此飲料已有訂單紀錄，無法刪除；可改為停售"
        )
    await manager.broadcast({"event": "drink_deleted", "drink_id": drink_id})
