"""原料庫存 API（第二步實作內容）。"""

import sqlite3

from fastapi import APIRouter, Depends

from backend.database import get_db

router = APIRouter(prefix="/api/ingredients", tags=["ingredients"])


@router.get("")
def list_ingredients(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM ingredients ORDER BY name").fetchall()
    return [dict(row) for row in rows]


# TODO(第二步): POST 新增、PUT 調整庫存
# TODO(第六步): 低於 reorder_point 的警示查詢
