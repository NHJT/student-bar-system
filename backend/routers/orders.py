"""訂單 API：下單、狀態流轉、付款標記。"""

import sqlite3
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException

from backend.database import get_db
from backend.models import OrderCreate, OrderPaymentUpdate, OrderStatusUpdate
from backend.ws import manager

router = APIRouter(prefix="/api/orders", tags=["orders"])

# 合法的狀態流轉：new → preparing → completed → delivered，途中可取消
ALLOWED_TRANSITIONS = {
    "new": {"preparing", "cancelled"},
    "preparing": {"completed", "cancelled"},
    "completed": {"delivered", "cancelled"},
    "delivered": set(),
    "cancelled": set(),
}

# 進入某狀態時要蓋上的時間戳欄位
STATUS_TIMESTAMP = {
    "preparing": "started_at",
    "completed": "completed_at",
    "delivered": "delivered_at",
}

_SELECT_ORDER = """
    SELECT o.*, d.name AS drink_name, d.category AS drink_category
    FROM orders o
    JOIN drinks d ON d.id = o.drink_id
"""


def _get_or_404(db: sqlite3.Connection, order_id: int) -> sqlite3.Row:
    row = db.execute(_SELECT_ORDER + " WHERE o.id = ?", (order_id,)).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這筆訂單")
    return row


@router.get("")
def list_orders(
    status: Optional[str] = None,
    table_number: Optional[int] = None,
    db: sqlite3.Connection = Depends(get_db),
):
    """訂單列表，可用 ?status=new&table_number=3 過濾。"""
    conditions, params = [], []
    if status is not None:
        conditions.append("o.status = ?")
        params.append(status)
    if table_number is not None:
        conditions.append("o.table_number = ?")
        params.append(table_number)
    sql = _SELECT_ORDER
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY o.placed_at ASC, o.id ASC"
    return [dict(row) for row in db.execute(sql, params).fetchall()]


@router.get("/{order_id}")
def get_order(order_id: int, db: sqlite3.Connection = Depends(get_db)):
    return dict(_get_or_404(db, order_id))


@router.post("", status_code=201)
async def create_order(payload: OrderCreate, db: sqlite3.Connection = Depends(get_db)):
    if payload.quantity < 1:
        raise HTTPException(status_code=400, detail="數量至少為 1")
    drink = db.execute(
        "SELECT * FROM drinks WHERE id = ?", (payload.drink_id,)
    ).fetchone()
    if drink is None:
        raise HTTPException(status_code=404, detail="找不到這杯飲料")
    if not drink["is_available"]:
        raise HTTPException(status_code=409, detail=f"「{drink['name']}」目前停售")
    cur = db.execute(
        "INSERT INTO orders (table_number, drink_id, quantity, special_request) "
        "VALUES (?, ?, ?, ?)",
        (
            payload.table_number,
            payload.drink_id,
            payload.quantity,
            payload.special_request,
        ),
    )
    db.commit()
    # TODO(第六步): 依 recipes 扣減 ingredients.current_stock
    order = dict(_get_or_404(db, cur.lastrowid))
    await manager.broadcast({"event": "order_created", "order": order})
    return order


@router.patch("/{order_id}/status")
async def update_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    """推進訂單狀態，並蓋上對應時間戳。"""
    order = _get_or_404(db, order_id)
    current, target = order["status"], payload.status
    if target not in ALLOWED_TRANSITIONS:
        raise HTTPException(status_code=400, detail=f"未知狀態: {target}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise HTTPException(
            status_code=409, detail=f"不能從 {current} 切換到 {target}"
        )
    sets = ["status = ?"]
    if target in STATUS_TIMESTAMP:
        sets.append(f"{STATUS_TIMESTAMP[target]} = datetime('now', 'localtime')")
    db.execute(
        f"UPDATE orders SET {', '.join(sets)} WHERE id = ?", (target, order_id)
    )
    db.commit()
    updated = dict(_get_or_404(db, order_id))
    await manager.broadcast({"event": "order_updated", "order": updated})
    return updated


@router.patch("/{order_id}/payment")
async def update_payment(
    order_id: int,
    payload: OrderPaymentUpdate,
    db: sqlite3.Connection = Depends(get_db),
):
    """標記付款狀態（服務生用）。"""
    _get_or_404(db, order_id)
    if payload.payment_status not in ("unpaid", "paid"):
        raise HTTPException(
            status_code=400, detail=f"未知付款狀態: {payload.payment_status}"
        )
    db.execute(
        "UPDATE orders SET payment_status = ? WHERE id = ?",
        (payload.payment_status, order_id),
    )
    db.commit()
    updated = dict(_get_or_404(db, order_id))
    await manager.broadcast({"event": "order_updated", "order": updated})
    return updated
