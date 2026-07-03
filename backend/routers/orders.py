"""訂單 API（第二步實作內容）。"""

import sqlite3

from fastapi import APIRouter, Depends

from backend.database import get_db

router = APIRouter(prefix="/api/orders", tags=["orders"])


@router.get("")
def list_orders(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM orders ORDER BY placed_at DESC").fetchall()
    return [dict(row) for row in rows]


# TODO(第二步): POST 下單、PATCH 切換 status（new → preparing → completed → delivered）
#               與 payment_status（unpaid → paid），切換時寫入對應時間戳
# TODO(第四步): 狀態變更時透過 WebSocket 廣播
# TODO(第六步): 下單時依 recipes 扣減 ingredients.current_stock
