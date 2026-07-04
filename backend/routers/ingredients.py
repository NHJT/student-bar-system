"""原料庫存 CRUD API。"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from backend.database import get_db
from backend.models import IngredientCreate, IngredientUpdate, StockAdjust

router = APIRouter(prefix="/api/ingredients", tags=["ingredients"])


def _get_or_404(db: sqlite3.Connection, ingredient_id: int) -> sqlite3.Row:
    row = db.execute(
        "SELECT * FROM ingredients WHERE id = ?", (ingredient_id,)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這項原料")
    return row


@router.get("")
def list_ingredients(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM ingredients ORDER BY name").fetchall()
    return [dict(row) for row in rows]


@router.get("/{ingredient_id}")
def get_ingredient(ingredient_id: int, db: sqlite3.Connection = Depends(get_db)):
    return dict(_get_or_404(db, ingredient_id))


@router.post("", status_code=201)
def create_ingredient(
    payload: IngredientCreate, db: sqlite3.Connection = Depends(get_db)
):
    try:
        cur = db.execute(
            "INSERT INTO ingredients (name, unit, current_stock, reorder_point) "
            "VALUES (?, ?, ?, ?)",
            (payload.name, payload.unit, payload.current_stock, payload.reorder_point),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="原料名稱已存在")
    return dict(_get_or_404(db, cur.lastrowid))


@router.patch("/{ingredient_id}")
def update_ingredient(
    ingredient_id: int,
    payload: IngredientUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    _get_or_404(db, ingredient_id)
    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有要更新的欄位")
    sets = ", ".join(f"{name} = ?" for name in fields)
    try:
        db.execute(
            f"UPDATE ingredients SET {sets} WHERE id = ?",
            (*fields.values(), ingredient_id),
        )
        db.commit()
    except sqlite3.IntegrityError:
        raise HTTPException(status_code=409, detail="原料名稱已存在")
    return dict(_get_or_404(db, ingredient_id))


@router.patch("/{ingredient_id}/stock")
def adjust_stock(
    ingredient_id: int,
    payload: StockAdjust,
    db: sqlite3.Connection = Depends(get_db),
):
    """庫存增減（正數進貨、負數耗損），不會扣到負值以下。"""
    row = _get_or_404(db, ingredient_id)
    new_stock = row["current_stock"] + payload.delta
    if new_stock < 0:
        raise HTTPException(
            status_code=409,
            detail=f"庫存不足：目前 {row['current_stock']} {row['unit']}",
        )
    db.execute(
        "UPDATE ingredients SET current_stock = ? WHERE id = ?",
        (new_stock, ingredient_id),
    )
    db.commit()
    return dict(_get_or_404(db, ingredient_id))


@router.delete("/{ingredient_id}", status_code=204)
def delete_ingredient(ingredient_id: int, db: sqlite3.Connection = Depends(get_db)):
    _get_or_404(db, ingredient_id)
    # recipes 設了 ON DELETE CASCADE，刪原料會一併移除相關配方項目
    db.execute("DELETE FROM ingredients WHERE id = ?", (ingredient_id,))
    db.commit()


# TODO(第六步): 低於 reorder_point 的警示查詢
