"""原料庫存 CRUD API。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from backend import inventory
from backend.database import get_db, row_to_dict, rows_to_dicts
from backend.models import IngredientCreate, IngredientUpdate, StockAdjust
from backend.ws import manager

router = APIRouter(prefix="/api/ingredients", tags=["ingredients"])


def _get_or_404(db: Connection, ingredient_id: int) -> dict:
    row = db.execute(
        text("SELECT * FROM ingredients WHERE id = :id"), {"id": ingredient_id}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這項原料")
    return row_to_dict(row)


@router.get("")
def list_ingredients(db: Connection = Depends(get_db)):
    return rows_to_dicts(db.execute(text("SELECT * FROM ingredients ORDER BY name")))


@router.get("/alerts")
def list_alerts(db: Connection = Depends(get_db)):
    """低於補貨點（reorder point）的原料列表。"""
    return inventory.low_stock(db)


@router.get("/{ingredient_id}")
def get_ingredient(ingredient_id: int, db: Connection = Depends(get_db)):
    return _get_or_404(db, ingredient_id)


@router.post("", status_code=201)
async def create_ingredient(
    payload: IngredientCreate, db: Connection = Depends(get_db)
):
    if not payload.name.strip() or not payload.unit.strip():
        raise HTTPException(status_code=400, detail="名稱與單位不可空白")
    if payload.current_stock < 0 or payload.reorder_point < 0:
        raise HTTPException(status_code=400, detail="庫存與補貨點不可為負數")
    try:
        new_id = db.execute(
            text(
                "INSERT INTO ingredients (name, unit, current_stock, reorder_point) "
                "VALUES (:name, :unit, :current_stock, :reorder_point) RETURNING id"
            ),
            {
                "name": payload.name.strip(),
                "unit": payload.unit.strip(),
                "current_stock": payload.current_stock,
                "reorder_point": payload.reorder_point,
            },
        ).scalar_one()
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="原料名稱已存在")
    ingredient = _get_or_404(db, new_id)
    await inventory.broadcast_stock_events(db, manager, [new_id])
    return ingredient


@router.patch("/{ingredient_id}")
async def update_ingredient(
    ingredient_id: int,
    payload: IngredientUpdate,
    db: Connection = Depends(get_db),
):
    _get_or_404(db, ingredient_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有要更新的欄位")
    for name in ("current_stock", "reorder_point"):
        if fields.get(name) is not None and fields[name] < 0:
            raise HTTPException(status_code=400, detail="庫存與補貨點不可為負數")
    for name in ("name", "unit"):
        if name in fields:
            if not fields[name].strip():
                raise HTTPException(status_code=400, detail="名稱與單位不可空白")
            fields[name] = fields[name].strip()

    sets = ", ".join(f"{name} = :{name}" for name in fields)
    try:
        db.execute(
            text(f"UPDATE ingredients SET {sets} WHERE id = :id"),
            {**fields, "id": ingredient_id},
        )
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="原料名稱已存在")
    await inventory.broadcast_stock_events(db, manager, [ingredient_id])
    return _get_or_404(db, ingredient_id)


@router.patch("/{ingredient_id}/stock")
async def adjust_stock(
    ingredient_id: int,
    payload: StockAdjust,
    db: Connection = Depends(get_db),
):
    """庫存增減（正數進貨、負數耗損），不會扣到負值以下。"""
    row = _get_or_404(db, ingredient_id)
    new_stock = round(row["current_stock"] + payload.delta, 3)
    if new_stock < 0:
        raise HTTPException(
            status_code=409,
            detail=f"庫存不足：目前 {row['current_stock']} {row['unit']}",
        )
    db.execute(
        text("UPDATE ingredients SET current_stock = :stock WHERE id = :id"),
        {"stock": new_stock, "id": ingredient_id},
    )
    db.commit()
    await inventory.broadcast_stock_events(db, manager, [ingredient_id])
    return _get_or_404(db, ingredient_id)


@router.delete("/{ingredient_id}", status_code=204)
async def delete_ingredient(ingredient_id: int, db: Connection = Depends(get_db)):
    _get_or_404(db, ingredient_id)
    # recipes 設了 ON DELETE CASCADE，刪原料會一併移除相關配方項目
    db.execute(text("DELETE FROM ingredients WHERE id = :id"), {"id": ingredient_id})
    db.commit()
    await manager.broadcast(
        {"event": "ingredient_deleted", "ingredient_id": ingredient_id}
    )
