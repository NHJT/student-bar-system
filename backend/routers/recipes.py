"""配方 API：查詢與整組設定某杯飲料的配方。"""

import sqlite3

from fastapi import APIRouter, Depends, HTTPException

from backend.database import get_db
from backend.models import RecipeItemIn
from backend.ws import manager

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


def _ensure_drink(db: sqlite3.Connection, drink_id: int) -> None:
    if db.execute("SELECT 1 FROM drinks WHERE id = ?", (drink_id,)).fetchone() is None:
        raise HTTPException(status_code=404, detail="找不到這杯飲料")


@router.get("/{drink_id}")
def get_recipe(drink_id: int, db: sqlite3.Connection = Depends(get_db)):
    _ensure_drink(db, drink_id)
    rows = db.execute(
        """
        SELECT r.ingredient_id, i.name AS ingredient_name, i.unit, r.quantity_needed
        FROM recipes r
        JOIN ingredients i ON i.id = r.ingredient_id
        WHERE r.drink_id = ?
        ORDER BY i.name
        """,
        (drink_id,),
    ).fetchall()
    return [dict(row) for row in rows]


@router.put("/{drink_id}")
async def set_recipe(
    drink_id: int,
    items: list[RecipeItemIn],
    db: sqlite3.Connection = Depends(get_db),
):
    """整組覆蓋配方：傳入完整原料清單，舊配方會被取代。"""
    _ensure_drink(db, drink_id)
    ingredient_ids = [item.ingredient_id for item in items]
    if len(set(ingredient_ids)) != len(ingredient_ids):
        raise HTTPException(status_code=400, detail="同一原料重複出現")
    for item in items:
        if item.quantity_needed <= 0:
            raise HTTPException(status_code=400, detail="用量必須大於 0")
        if (
            db.execute(
                "SELECT 1 FROM ingredients WHERE id = ?", (item.ingredient_id,)
            ).fetchone()
            is None
        ):
            raise HTTPException(
                status_code=404, detail=f"找不到原料 id={item.ingredient_id}"
            )
    db.execute("DELETE FROM recipes WHERE drink_id = ?", (drink_id,))
    db.executemany(
        "INSERT INTO recipes (drink_id, ingredient_id, quantity_needed) VALUES (?, ?, ?)",
        [(drink_id, item.ingredient_id, item.quantity_needed) for item in items],
    )
    db.commit()
    await manager.broadcast({"event": "recipe_updated", "drink_id": drink_id})
    return get_recipe(drink_id, db)
