"""訂單 API：一張訂單（order_group）可含多個品項（order_items）。

狀態與時間戳記在訂單層級。各品項也有自己的狀態，吧台可以單獨切換；
訂單狀態一律取「最落後的品項」，所有品項都做完才算完成。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend import inventory
from backend.database import get_db, row_to_dict, rows_to_dicts
from backend.models import (
    OrderCreate,
    OrderEdit,
    OrderItemIn,
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

# 訂單狀態取最落後的品項，所以每個階段有先後順序
STAGE_ORDER = ["new", "preparing", "completed", "delivered"]

# 進入某狀態時要蓋上的時間戳欄位
STATUS_TIMESTAMP = {
    "preparing": "started_at",
    "completed": "completed_at",
    "delivered": "delivered_at",
}

_SELECT_GROUP = "SELECT * FROM order_groups"


def _items_of(db: Connection, group_ids: list[int]) -> dict[int, list[dict]]:
    """一次撈出多張訂單的品項，避免逐張查詢。"""
    if not group_ids:
        return {}
    rows = rows_to_dicts(
        db.execute(
            text(
                """
                SELECT i.id, i.group_id, i.drink_id, d.name AS drink_name,
                       i.quantity, i.special_request, i.status
                FROM order_items i JOIN drinks d ON d.id = i.drink_id
                WHERE i.group_id = ANY(:ids)
                ORDER BY i.id
                """
            ),
            {"ids": group_ids},
        )
    )
    grouped: dict[int, list[dict]] = {gid: [] for gid in group_ids}
    for row in rows:
        grouped[row.pop("group_id")].append(row)
    return grouped


def _with_items(db: Connection, groups: list[dict]) -> list[dict]:
    items = _items_of(db, [g["id"] for g in groups])
    for group in groups:
        group["items"] = items.get(group["id"], [])
        group["item_count"] = len(group["items"])
        group["total_quantity"] = sum(i["quantity"] for i in group["items"])
        # 一眼看得出內容，吧台與經理的清單都用得到
        group["summary"] = " | ".join(
            f"{i['drink_name']} x{i['quantity']}" for i in group["items"]
        )
    return groups


def _get_or_404(db: Connection, group_id: int) -> dict:
    row = db.execute(
        text(_SELECT_GROUP + " WHERE id = :id"), {"id": group_id}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="找不到這筆訂單")
    return _with_items(db, [row_to_dict(row)])[0]


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
        conditions.append("status = :status")
        params["status"] = status
    if table_number is not None:
        conditions.append("table_number = :table_number")
        params["table_number"] = table_number
    if today:
        conditions.append("placed_at::date = NOW()::date")
    sql = _SELECT_GROUP
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY placed_at ASC, id ASC"
    return _with_items(db, rows_to_dicts(db.execute(text(sql), params)))


@router.get("/{group_id}")
def get_order(group_id: int, db: Connection = Depends(get_db)):
    return _get_or_404(db, group_id)


# ---------- 建立與修改 ----------
def _validate_items(db: Connection, items: list[OrderItemIn]) -> None:
    if not items:
        raise HTTPException(status_code=400, detail="訂單至少要有一個品項")
    for item in items:
        if item.quantity < 1:
            raise HTTPException(status_code=400, detail="每個品項數量至少為 1")
        drink = db.execute(
            text("SELECT * FROM drinks WHERE id = :id"), {"id": item.drink_id}
        ).fetchone()
        if drink is None:
            raise HTTPException(status_code=404, detail="找不到這杯飲料")
        if not drink.is_available:
            raise HTTPException(status_code=409, detail=f"「{drink.name}」目前停售")


def _deduct_items(db: Connection, items: list[OrderItemIn]) -> list[int]:
    """依所有品項的配方扣庫存；任何一項不足就整張訂單拒收。"""
    affected: list[int] = []
    for item in items:
        ids, shortages = inventory.check_and_deduct(db, item.drink_id, item.quantity)
        if shortages:
            db.rollback()
            raise HTTPException(
                status_code=409, detail="庫存不足：" + "；".join(shortages)
            )
        affected.extend(ids)
    return list(dict.fromkeys(affected))


def _restore_items(db: Connection, items: list[dict]) -> list[int]:
    restored: list[int] = []
    for item in items:
        restored.extend(inventory.restore(db, item["drink_id"], item["quantity"]))
    return list(dict.fromkeys(restored))


def _insert_items(db: Connection, group_id: int, items: list[OrderItemIn]) -> None:
    db.execute(
        text(
            "INSERT INTO order_items (group_id, drink_id, quantity, special_request) "
            "VALUES (:group_id, :drink_id, :quantity, :special_request)"
        ),
        [
            {
                "group_id": group_id,
                "drink_id": item.drink_id,
                "quantity": item.quantity,
                "special_request": item.special_request,
            }
            for item in items
        ],
    )


@router.post("", status_code=201)
async def create_order(payload: OrderCreate, db: Connection = Depends(get_db)):
    _validate_items(db, payload.items)
    affected = _deduct_items(db, payload.items)

    group_id = db.execute(
        text(
            "INSERT INTO order_groups (table_number) VALUES (:table_number) "
            "RETURNING id"
        ),
        {"table_number": payload.table_number},
    ).scalar_one()
    _insert_items(db, group_id, payload.items)
    db.commit()

    order = _get_or_404(db, group_id)
    await manager.broadcast({"event": "order_created", "order": order})
    await inventory.broadcast_stock_events(db, manager, affected)
    return order


@router.patch("/{group_id}")
async def edit_order(
    group_id: int, payload: OrderEdit, db: Connection = Depends(get_db)
):
    """服務生修改訂單（桌號／品項），只有還沒進入製作的訂單可以改。

    每次修改 edit_count + 1，作為輸入錯誤率的量測依據。
    帶了 items 就整組取代，庫存先全部退回再依新內容重扣。
    """
    order = _get_or_404(db, group_id)
    if order["status"] != "new":
        raise HTTPException(
            status_code=409,
            detail=f"訂單已是「{order['status']}」狀態，無法修改",
        )

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有要修改的欄位")

    affected: list[int] = []
    if payload.items is not None:
        _validate_items(db, payload.items)
        restored = _restore_items(db, order["items"])
        deducted = _deduct_items(db, payload.items)
        affected = list(dict.fromkeys(restored + deducted))
        db.execute(
            text("DELETE FROM order_items WHERE group_id = :id"), {"id": group_id}
        )
        _insert_items(db, group_id, payload.items)

    sets = ["edit_count = edit_count + 1"]
    params = {"id": group_id}
    if payload.table_number is not None:
        sets.append("table_number = :table_number")
        params["table_number"] = payload.table_number
    db.execute(
        text(f"UPDATE order_groups SET {', '.join(sets)} WHERE id = :id"), params
    )
    db.commit()

    updated = _get_or_404(db, group_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    if affected:
        await inventory.broadcast_stock_events(db, manager, affected)
    return updated


# ---------- 狀態流轉 ----------
def _sync_group_status(db: Connection, group_id: int, current: str) -> str:
    """依品項狀態重算訂單狀態，需要時補上時間戳。

    取最落後的品項：三杯裡有一杯還沒開始做，整張訂單就還是新單。
    全部取消才算整張取消。
    """
    rows = db.execute(
        text("SELECT status FROM order_items WHERE group_id = :id"), {"id": group_id}
    ).fetchall()
    active = [r.status for r in rows if r.status != "cancelled"]
    target = "cancelled" if not active else min(active, key=STAGE_ORDER.index)
    if target == current:
        return current

    sets = ["status = :status"]
    # 只在第一次進入該階段時蓋時間戳，之後品項狀態再變動也不覆寫
    if target in STATUS_TIMESTAMP:
        column = STATUS_TIMESTAMP[target]
        sets.append(f"{column} = COALESCE({column}, NOW())")
    db.execute(
        text(f"UPDATE order_groups SET {', '.join(sets)} WHERE id = :id"),
        {"status": target, "id": group_id},
    )
    return target


@router.patch("/{group_id}/status")
async def update_status(
    group_id: int,
    payload: OrderStatusUpdate,
    db: Connection = Depends(get_db),
):
    """推進整張訂單的狀態，所有還沒到該階段的品項一起帶過去。"""
    order = _get_or_404(db, group_id)
    current, target = order["status"], payload.status
    if target not in ALLOWED_TRANSITIONS:
        raise HTTPException(status_code=400, detail=f"未知狀態: {target}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise HTTPException(
            status_code=409, detail=f"不能從 {current} 切換到 {target}"
        )

    if target == "cancelled":
        db.execute(
            text(
                "UPDATE order_items SET status = 'cancelled' "
                "WHERE group_id = :id AND status <> 'cancelled'"
            ),
            {"id": group_id},
        )
    else:
        # 已經走在前面的品項不倒退
        db.execute(
            text(
                "UPDATE order_items SET status = :status "
                "WHERE group_id = :id AND status <> 'cancelled' "
                "AND array_position(:order, status) < array_position(:order, :status)"
            ),
            {"status": target, "id": group_id, "order": STAGE_ORDER},
        )
    _sync_group_status(db, group_id, current)

    # 還沒開始製作就取消 → 把配方用量退回庫存
    restored: list[int] = []
    if target == "cancelled" and current == "new":
        restored = _restore_items(db, order["items"])

    db.commit()
    updated = _get_or_404(db, group_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    if restored:
        await inventory.broadcast_stock_events(db, manager, restored)
    return updated


@router.patch("/{group_id}/items/{item_id}/status")
async def update_item_status(
    group_id: int,
    item_id: int,
    payload: OrderStatusUpdate,
    db: Connection = Depends(get_db),
):
    """單獨推進某個品項的狀態；訂單狀態隨之重算。"""
    order = _get_or_404(db, group_id)
    item = next((i for i in order["items"] if i["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="這張訂單沒有這個品項")

    current, target = item["status"], payload.status
    if target not in ALLOWED_TRANSITIONS:
        raise HTTPException(status_code=400, detail=f"未知狀態: {target}")
    if target not in ALLOWED_TRANSITIONS[current]:
        raise HTTPException(
            status_code=409, detail=f"不能從 {current} 切換到 {target}"
        )

    db.execute(
        text("UPDATE order_items SET status = :status WHERE id = :id"),
        {"status": target, "id": item_id},
    )
    _sync_group_status(db, group_id, order["status"])

    # 整張訂單都還沒開工時取消某個品項 → 退回該品項的料
    restored: list[int] = []
    if target == "cancelled" and current == "new":
        restored = _restore_items(db, [item])

    db.commit()
    updated = _get_or_404(db, group_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    if restored:
        await inventory.broadcast_stock_events(db, manager, restored)
    return updated


@router.patch("/{group_id}/payment")
async def update_payment(
    group_id: int,
    payload: OrderPaymentUpdate,
    db: Connection = Depends(get_db),
):
    """標記付款狀態（服務生用），以整張訂單為單位。"""
    _get_or_404(db, group_id)
    if payload.payment_status not in ("unpaid", "paid"):
        raise HTTPException(
            status_code=400, detail=f"未知付款狀態: {payload.payment_status}"
        )
    # 標記為已付款時蓋上時間戳；改回未付款則清掉
    stamp = "NOW()" if payload.payment_status == "paid" else "NULL"
    db.execute(
        text(
            f"UPDATE order_groups SET payment_status = :ps, "
            f"payment_completed_at = {stamp} WHERE id = :id"
        ),
        {"ps": payload.payment_status, "id": group_id},
    )
    db.commit()
    updated = _get_or_404(db, group_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    return updated
