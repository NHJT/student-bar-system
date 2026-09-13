"""訂單 API：下單、修改、狀態流轉、付款標記。"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend import inventory
from backend.database import get_db, row_to_dict, rows_to_dicts
from backend.models import (
    OrderCreate,
    OrderEdit,
    OrderPaymentUpdate,
    OrderStatusUpdate,
)
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


def _get_or_404(db: Connection, order_id: int) -> dict:
    row = db.execute(
        text(_SELECT_ORDER + " WHERE o.id = :id"), {"id": order_id}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這筆訂單")
    return row_to_dict(row)


@router.get("")
def list_orders(
    status: Optional[str] = None,
    table_number: Optional[int] = None,
    today: bool = False,
    db: Connection = Depends(get_db),
):
    """訂單列表，可用 ?status=new&table_number=3&today=true 過濾。

    today=true 只回傳今天下單的訂單，經理儀表板用它做到「每日結算後顯示歸零」；
    吧台與服務生不加這個條件，跨過午夜還沒做完的訂單才不會從佇列消失。
    """
    conditions, params = [], {}
    if status is not None:
        conditions.append("o.status = :status")
        params["status"] = status
    if table_number is not None:
        conditions.append("o.table_number = :table_number")
        params["table_number"] = table_number
    if today:
        conditions.append("o.placed_at::date = NOW()::date")
    sql = _SELECT_ORDER
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY o.placed_at ASC, o.id ASC"
    return rows_to_dicts(db.execute(text(sql), params))


@router.get("/{order_id}")
def get_order(order_id: int, db: Connection = Depends(get_db)):
    return _get_or_404(db, order_id)


def _available_drink_or_error(db: Connection, drink_id: int) -> dict:
    drink = db.execute(
        text("SELECT * FROM drinks WHERE id = :id"), {"id": drink_id}
    ).fetchone()
    if drink is None:
        raise HTTPException(status_code=404, detail="找不到這杯飲料")
    if not drink.is_available:
        raise HTTPException(status_code=409, detail=f"「{drink.name}」目前停售")
    return row_to_dict(drink)


@router.post("", status_code=201)
async def create_order(payload: OrderCreate, db: Connection = Depends(get_db)):
    if payload.quantity < 1:
        raise HTTPException(status_code=400, detail="數量至少為 1")
    _available_drink_or_error(db, payload.drink_id)

    # 依配方扣庫存；任何一項不足就整筆拒單
    affected, shortages = inventory.check_and_deduct(
        db, payload.drink_id, payload.quantity
    )
    if shortages:
        db.rollback()
        raise HTTPException(status_code=409, detail="庫存不足：" + "；".join(shortages))

    new_id = db.execute(
        text(
            "INSERT INTO orders (table_number, drink_id, quantity, special_request) "
            "VALUES (:table_number, :drink_id, :quantity, :special_request) "
            "RETURNING id"
        ),
        {
            "table_number": payload.table_number,
            "drink_id": payload.drink_id,
            "quantity": payload.quantity,
            "special_request": payload.special_request,
        },
    ).scalar_one()
    db.commit()

    order = _get_or_404(db, new_id)
    await manager.broadcast({"event": "order_created", "order": order})
    await inventory.broadcast_stock_events(db, manager, affected)
    return order


@router.patch("/{order_id}")
async def edit_order(
    order_id: int, payload: OrderEdit, db: Connection = Depends(get_db)
):
    """服務生修改訂單（品項／數量／特殊需求）。

    只有還沒進入製作（status = 'new'）的訂單可以改；每次修改 edit_count + 1，
    作為輸入錯誤率的量測依據。品項或數量改變時，庫存會先退回原配方用量、
    再依新內容扣料。
    """
    order = _get_or_404(db, order_id)
    if order["status"] != "new":
        raise HTTPException(
            status_code=409,
            detail=f"訂單已是「{order['status']}」狀態，無法修改",
        )

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有要修改的欄位")

    new_drink_id = fields.get("drink_id", order["drink_id"])
    new_quantity = fields.get("quantity", order["quantity"])
    if new_quantity < 1:
        raise HTTPException(status_code=400, detail="數量至少為 1")
    _available_drink_or_error(db, new_drink_id)

    # 品項或數量有變才動庫存：先退回原本用量，再依新內容扣料
    affected: list[int] = []
    if new_drink_id != order["drink_id"] or new_quantity != order["quantity"]:
        restored = inventory.restore(db, order["drink_id"], order["quantity"])
        deducted, shortages = inventory.check_and_deduct(
            db, new_drink_id, new_quantity
        )
        if shortages:
            db.rollback()  # 連同退料一起復原，訂單維持原樣
            raise HTTPException(
                status_code=409, detail="庫存不足：" + "；".join(shortages)
            )
        affected = list(dict.fromkeys(restored + deducted))

    fields["edit_count"] = order["edit_count"] + 1
    sets = ", ".join(f"{name} = :{name}" for name in fields)
    db.execute(
        text(f"UPDATE orders SET {sets} WHERE id = :id"), {**fields, "id": order_id}
    )
    db.commit()

    updated = _get_or_404(db, order_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    if affected:
        await inventory.broadcast_stock_events(db, manager, affected)
    return updated


@router.patch("/{order_id}/status")
async def update_status(
    order_id: int,
    payload: OrderStatusUpdate,
    db: Connection = Depends(get_db),
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
    sets = ["status = :status"]
    if target in STATUS_TIMESTAMP:
        sets.append(f"{STATUS_TIMESTAMP[target]} = NOW()")
    db.execute(
        text(f"UPDATE orders SET {', '.join(sets)} WHERE id = :id"),
        {"status": target, "id": order_id},
    )

    # 還沒開始製作就取消 → 把配方用量退回庫存
    restored: list[int] = []
    if target == "cancelled" and current == "new":
        restored = inventory.restore(db, order["drink_id"], order["quantity"])

    db.commit()
    updated = _get_or_404(db, order_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    if restored:
        await inventory.broadcast_stock_events(db, manager, restored)
    return updated


@router.patch("/{order_id}/payment")
async def update_payment(
    order_id: int,
    payload: OrderPaymentUpdate,
    db: Connection = Depends(get_db),
):
    """標記付款狀態（服務生用）。"""
    _get_or_404(db, order_id)
    if payload.payment_status not in ("unpaid", "paid"):
        raise HTTPException(
            status_code=400, detail=f"未知付款狀態: {payload.payment_status}"
        )
    # 標記為已付款時蓋上時間戳；改回未付款則清掉
    stamp = "NOW()" if payload.payment_status == "paid" else "NULL"
    db.execute(
        text(
            f"UPDATE orders SET payment_status = :ps, payment_completed_at = {stamp} "
            "WHERE id = :id"
        ),
        {"ps": payload.payment_status, "id": order_id},
    )
    db.commit()
    updated = _get_or_404(db, order_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    return updated
