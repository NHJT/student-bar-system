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


@router.get("")
def list_recipes(db: Connection = Depends(get_db)):
    """一次取回所有飲品的配方（唯讀）。

    吧台頁的酒譜清單用它。改成一次取回，是因為逐杯打 /recipes/{id} 會變成
    二十幾個請求；這裡只是把同一份資料一次給完，沒有新的權限或寫入行為。

    沒有配方的飲品也會出現，items 是空陣列——特調與無酒精飲料沒有固定配方，
    前端要據此顯示「無固定配方」，不能讓它們整個從清單消失。
    """
    rows = rows_to_dicts(
        db.execute(
            text(
                """
                SELECT d.id AS drink_id, d.name AS drink_name, d.category,
                       d.is_available,
                       i.name AS ingredient_name, i.unit,
                       r.quantity_needed, r.note
                FROM drinks d
                LEFT JOIN recipes r ON r.drink_id = d.id
                LEFT JOIN ingredients i ON i.id = r.ingredient_id
                ORDER BY d.category, d.name, i.name
                """
            )
        )
    )

    drinks: dict[int, dict] = {}
    for row in rows:
        drink = drinks.setdefault(
            row["drink_id"],
            {
                "drink_id": row["drink_id"],
                "drink_name": row["drink_name"],
                "category": row["category"],
                "is_available": row["is_available"],
                "items": [],
            },
        )
        if row["ingredient_name"] is not None:  # LEFT JOIN 的空列不算配方
            drink["items"].append(
                {
                    "ingredient_name": row["ingredient_name"],
                    "unit": row["unit"],
                    "quantity_needed": row["quantity_needed"],
                    "note": row["note"],
                }
            )
    return list(drinks.values())


@router.get("/{drink_id}")
def get_recipe(drink_id: int, db: Connection = Depends(get_db)):
    _ensure_drink(db, drink_id)
    return rows_to_dicts(
        db.execute(
            text(
                """
                SELECT r.ingredient_id, i.name AS ingredient_name, i.unit,
                       r.quantity_needed, r.note
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

    # 「換算估值」這類註記說的是「這個數字是換算來的」。經理把用量改掉之後
    # 那句話就不成立了，所以用量有變動的列要把註記清掉，沒動到的才保留。
    previous = {
        row.ingredient_id: (row.quantity_needed, row.note)
        for row in db.execute(
            text("SELECT ingredient_id, quantity_needed, note FROM recipes "
                 "WHERE drink_id = :id"),
            {"id": drink_id},
        )
    }

    def kept_note(item: RecipeItemIn) -> str | None:
        before = previous.get(item.ingredient_id)
        if before is None or before[0] != item.quantity_needed:
            return None
        return before[1]

    db.execute(text("DELETE FROM recipes WHERE drink_id = :id"), {"id": drink_id})
    if items:
        db.execute(
            text(
                "INSERT INTO recipes (drink_id, ingredient_id, quantity_needed, note) "
                "VALUES (:drink_id, :ingredient_id, :quantity_needed, :note)"
            ),
            [
                {
                    "drink_id": drink_id,
                    "ingredient_id": item.ingredient_id,
                    "quantity_needed": item.quantity_needed,
                    "note": kept_note(item),
                }
                for item in items
            ],
        )
    db.commit()
    await manager.broadcast({"event": "recipe_updated", "drink_id": drink_id})
    return get_recipe(drink_id, db)
