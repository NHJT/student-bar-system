"""配方 API：查詢與整組設定某杯飲品的配方。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.database import get_db, rows_to_dicts
from backend.models import RecipeItemIn
from backend.ws import manager

router = APIRouter(prefix="/api/recipes", tags=["recipes"])


def _ensure_drink(db: Connection, drink_id: int) -> None:
    exists = db.execute(
        text("SELECT 1 FROM drinks WHERE id = :id"), {"id": drink_id}
    ).fetchone()
    if exists is None:
        raise HTTPException(status_code=404, detail="找不到這杯飲料")


@router.get("/{drink_id}")
def get_recipe(drink_id: int, db: Connection = Depends(get_db)):
    _ensure_drink(db, drink_id)
    return rows_to_dicts(
        db.execute(
            text(
                """
                SELECT r.ingredient_id, i.name AS ingredient_name, i.unit,
                       r.quantity_needed
                FROM recipes r
                JOIN ingredients i ON i.id = r.ingredient_id
                WHERE r.drink_id = :drink_id
                ORDER BY i.name
                """
            ),
            {"drink_id": drink_id},
        )
    )


@router.put("/{drink_id}")
async def set_recipe(
    drink_id: int,
    items: list[RecipeItemIn],
    db: Connection = Depends(get_db),
):
    """整組覆蓋配方：傳入完整原料清單，舊配方會被取代。"""
    _ensure_drink(db, drink_id)
    ingredient_ids = [item.ingredient_id for item in items]
    if len(set(ingredient_ids)) != len(ingredient_ids):
        raise HTTPException(status_code=400, detail="同一原料重複出現")
    for item in items:
        if item.quantity_needed <= 0:
            raise HTTPException(status_code=400, detail="用量必須大於 0")
        exists = db.execute(
            text("SELECT 1 FROM ingredients WHERE id = :id"),
            {"id": item.ingredient_id},
        ).fetchone()
        if exists is None:
            raise HTTPException(
                status_code=404, detail=f"找不到原料 id={item.ingredient_id}"
            )

    db.execute(text("DELETE FROM recipes WHERE drink_id = :id"), {"id": drink_id})
    if items:
        db.execute(
            text(
                "INSERT INTO recipes (drink_id, ingredient_id, quantity_needed) "
                "VALUES (:drink_id, :ingredient_id, :quantity_needed)"
            ),
            [
                {
                    "drink_id": drink_id,
                    "ingredient_id": item.ingredient_id,
                    "quantity_needed": item.quantity_needed,
                }
                for item in items
            ],
        )
    db.commit()
    await manager.broadcast({"event": "recipe_updated", "drink_id": drink_id})
    return get_recipe(drink_id, db)
