"""訂單 API：一張訂單（order_group）可含多個品項（order_items）。

狀態與時間戳記在訂單層級。各品項也有自己的狀態，吧台可以單獨切換；
訂單狀態一律取「最落後的品項」，所有品項都做完才算完成。
"""

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend import inventory
from backend.database import get_db, row_to_dict, rows_to_dicts, table_exists
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

# 「退回上一個狀態」的對照。刻意另外列一份而不是放寬 ALLOWED_TRANSITIONS，
# 前進的規則完全不受影響，而且退回一次只能退一步，不能任意跳。
# 還能改桌號的狀態：送到客人手上（或取消）之後就不能再改了。
# 品項的限制更嚴，只有 new 能改，見 edit_order。
EDITABLE_TABLE_STATUSES = {"new", "preparing", "completed"}

REVERT_TARGET = {
    "preparing": "new",
    "completed": "preparing",
    # 已送達也可以退，但只限當日訂單（見 _ensure_same_day），
    # 以免動到已經結算過的日子
    "delivered": "completed",
}

# 只有從這些狀態退回時才檢查是不是當日訂單
REVERT_TODAY_ONLY = {"delivered"}

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
                       i.quantity, i.special_request, i.status, i.unit_price
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


def _reverts_of(db: Connection, group_ids: list[int]) -> dict[int, int]:
    """一次撈出多張訂單被退回過幾次（唯讀，只讀 order_events）。

    外場的待送達卡片用它顯示「已退回」標記。

    這是訂單查詢的必經之路，而 order_events 是後來才加的表。還沒跑過
    init_db.py 的資料庫沒有這張表，所以先用 to_regclass 探一次路：表不在
    就當成沒人退回過，訂單照常列出，只是少了「已退回」標記。
    不用 try/except 接——PostgreSQL 一旦報錯，整個交易就變成 aborted，
    後面的查詢全部跟著失敗，吞掉例外救不回來。
    """
    if not group_ids or not table_exists(db, "order_events"):
        return {}
    rows = db.execute(
        text(
            "SELECT group_id, COUNT(*) AS n FROM order_events "
            "WHERE event_type = 'revert' AND group_id = ANY(:ids) "
            "GROUP BY group_id"
        ),
        {"ids": group_ids},
    ).fetchall()
    return {r.group_id: r.n for r in rows}


def _with_items(db: Connection, groups: list[dict]) -> list[dict]:
    items = _items_of(db, [g["id"] for g in groups])
    reverts = _reverts_of(db, [g["id"] for g in groups])
    for group in groups:
        group["revert_count"] = reverts.get(group["id"], 0)
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
    table_number: Optional[str] = None,  # 文字桌號：「3」「吧台」「站位」
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
    # unit_price 直接從 drinks 取當下的售價存成快照，
    # 之後經理改價不會影響已經成立的訂單；沒定價就是 NULL
    db.execute(
        text(
            "INSERT INTO order_items "
            "(group_id, drink_id, quantity, special_request, unit_price) "
            "SELECT :group_id, :drink_id, :quantity, :special_request, d.price "
            "FROM drinks d WHERE d.id = :drink_id"
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


def _items_differ(current: list[dict], incoming: list[OrderItemIn]) -> bool:
    """送出的品項和訂單現有的內容是不是真的不一樣。

    只看飲品、數量、備註三件事，順序不算差異（同樣兩杯調換位置不是修改）。
    備註的空字串和 None 視為相同，免得前端送 "" 就被當成改過。
    """

    def key(drink_id, quantity, note):
        # 備註統一成字串，None 和 "" 一樣，排序時也才不會拿 None 去比字串
        return (drink_id, quantity, (note or "").strip())

    now = sorted(
        key(i["drink_id"], i["quantity"], i["special_request"]) for i in current
    )
    new = sorted(key(i.drink_id, i.quantity, i.special_request) for i in incoming)
    return now != new


@router.patch("/{group_id}")
async def edit_order(
    group_id: int, payload: OrderEdit, db: Connection = Depends(get_db)
):
    """服務生修改訂單（桌號／品項）。

    品項只有還沒進入製作的訂單可以改（庫存已經扣了，吧台也可能開始調了）；
    桌號到送達前都還能改——客人換位子跟服務生按錯一樣常見，而且改桌號不影響
    庫存與製作內容。

    edit_count 是輸入錯誤率的量測依據，所以只有品項或備註真的變了才 +1：
    單純把客人換個位子不算服務生填錯。同一次連品項一起改就照常 +1。
    帶了 items 就整組取代，庫存先全部退回再依新內容重扣。
    """
    order = _get_or_404(db, group_id)
    if payload.items is not None and order["status"] != "new":
        raise HTTPException(
            status_code=409,
            detail=f"訂單已是「{order['status']}」狀態，品項無法再修改",
        )
    if order["status"] not in EDITABLE_TABLE_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"訂單已是「{order['status']}」狀態，無法修改",
        )

    fields = payload.model_dump(exclude_unset=True)
    if not fields:
        raise HTTPException(status_code=400, detail="沒有要修改的欄位")

    items_changed = payload.items is not None and _items_differ(
        order["items"], payload.items
    )

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

    sets = []
    params = {"id": group_id}
    if items_changed:
        sets.append("edit_count = edit_count + 1")
    if payload.table_number is not None:
        sets.append("table_number = :table_number")
        params["table_number"] = payload.table_number
    if sets:  # 只改桌號又送出同一組品項時，這裡可能完全沒有要更新的欄位
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
    # 往回退時，把「比新狀態更後面」的階段時間戳清掉。
    # 重新推進時上面的 COALESCE 會蓋上新的時間，誤觸不會污染製作時間的計算。
    # 前進時這段不會執行，原本的行為完全不變。
    if (
        current in STAGE_ORDER
        and target in STAGE_ORDER
        and STAGE_ORDER.index(target) < STAGE_ORDER.index(current)
    ):
        for stage in STAGE_ORDER[STAGE_ORDER.index(target) + 1 :]:
            if stage in STATUS_TIMESTAMP:
                sets.append(f"{STATUS_TIMESTAMP[stage]} = NULL")
    db.execute(
        text(f"UPDATE order_groups SET {', '.join(sets)} WHERE id = :id"),
        {"status": target, "id": group_id},
    )
    return target


def _revert_target(status: str) -> str:
    """回傳可以退回的上一個狀態；不能退就直接回 409。"""
    target = REVERT_TARGET.get(status)
    if target is None:
        raise HTTPException(
            status_code=409, detail=f"「{status}」沒有可以退回的上一個狀態"
        )
    return target


def _ensure_same_day(db: Connection, group_id: int, status: str) -> None:
    """已送達的訂單只允許當日退回，避免改到已經結算過的資料。"""
    if status not in REVERT_TODAY_ONLY:
        return
    today = db.execute(
        text(
            "SELECT placed_at::date = NOW()::date FROM order_groups WHERE id = :id"
        ),
        {"id": group_id},
    ).scalar()
    if not today:
        raise HTTPException(
            status_code=409, detail="只有當日的訂單可以從「已送達」退回"
        )


def _log_revert(
    db: Connection,
    group_id: int,
    item_id: Optional[int],
    from_status: str,
    to_status: str,
    before: dict,
    actor: str,
) -> None:
    """把退回動作寫進 order_events，連同即將被清掉的時間戳。

    這是獨立的誤觸追蹤，不會動到 order_groups.edit_count
    （那個算的是服務生的輸入錯誤率，兩個指標不要混在一起）。

    這張表是後來才加的，沒跑過 init_db.py 的資料庫沒有它。退回一定要留紀錄，
    所以這裡不像 _reverts_of 那樣默默略過，而是明說要跑 migration。
    """
    if not table_exists(db, "order_events"):
        raise HTTPException(
            status_code=503,
            detail="資料庫還沒有 order_events 表，請先執行 init_db.py 再使用退回功能",
        )
    db.execute(
        text(
            """
            INSERT INTO order_events
                (group_id, item_id, event_type, from_status, to_status,
                 prev_started_at, prev_completed_at, prev_delivered_at, actor)
            VALUES (:group_id, :item_id, 'revert', :from_status, :to_status,
                    :prev_started_at, :prev_completed_at, :prev_delivered_at, :actor)
            """
        ),
        {
            "group_id": group_id,
            "item_id": item_id,
            "from_status": from_status,
            "to_status": to_status,
            "prev_started_at": before.get("started_at"),
            "prev_completed_at": before.get("completed_at"),
            "prev_delivered_at": before.get("delivered_at"),
            "actor": actor,
        },
    )


@router.patch("/{group_id}/revert")
async def revert_status(group_id: int, db: Connection = Depends(get_db)):
    """把整張訂單退回上一個狀態（製作中→新訂單、待送達→製作中）。

    已送達不能退。庫存不受影響：扣料只在建立訂單時發生，
    退料只在「還沒開工就取消」時發生，狀態前後移動都不碰庫存。
    """
    order = _get_or_404(db, group_id)
    current = order["status"]
    target = _revert_target(current)
    _ensure_same_day(db, group_id, current)

    # 走在前面的品項一起退回來，落後的維持原狀
    db.execute(
        text(
            "UPDATE order_items SET status = :target "
            "WHERE group_id = :id AND status <> 'cancelled' "
            "AND array_position(:order, status) > array_position(:order, :target)"
        ),
        {"target": target, "id": group_id, "order": STAGE_ORDER},
    )
    _log_revert(db, group_id, None, current, target, order, "bar")
    _sync_group_status(db, group_id, current)
    db.commit()

    updated = _get_or_404(db, group_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    return updated


@router.patch("/{group_id}/items/{item_id}/revert")
async def revert_item_status(
    group_id: int, item_id: int, db: Connection = Depends(get_db)
):
    """把單一品項退回上一個狀態；同一張訂單的其他品項不受影響。"""
    order = _get_or_404(db, group_id)
    item = next((i for i in order["items"] if i["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="這張訂單沒有這個品項")

    current = item["status"]
    target = _revert_target(current)
    _ensure_same_day(db, group_id, current)
    db.execute(
        text("UPDATE order_items SET status = :status WHERE id = :id"),
        {"status": target, "id": item_id},
    )
    _log_revert(db, group_id, item_id, current, target, order, "bar")
    _sync_group_status(db, group_id, order["status"])
    db.commit()

    updated = _get_or_404(db, group_id)
    await manager.broadcast({"event": "order_updated", "order": updated})
    return updated


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
