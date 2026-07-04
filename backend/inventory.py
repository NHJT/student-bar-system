"""配方庫存連動：下單扣料、取消退料、補貨點警示。"""

import sqlite3

_RECIPE_SQL = """
    SELECT r.ingredient_id, r.quantity_needed,
           i.name, i.unit, i.current_stock, i.reorder_point
    FROM recipes r
    JOIN ingredients i ON i.id = r.ingredient_id
    WHERE r.drink_id = ?
"""


def check_and_deduct(
    db: sqlite3.Connection, drink_id: int, quantity: int
) -> tuple[list[int], list[str]]:
    """依配方扣減庫存（未 commit，由呼叫端決定）。

    回傳 (受影響的 ingredient_id 列表, 缺料訊息列表)。
    只要有任何一項不足就完全不扣，回傳缺料訊息。
    """
    rows = db.execute(_RECIPE_SQL, (drink_id,)).fetchall()
    shortages = []
    for row in rows:
        need = row["quantity_needed"] * quantity
        if row["current_stock"] < need:
            shortages.append(
                f"{row['name']} 需要 {round(need, 3)} {row['unit']}"
                f"（僅剩 {round(row['current_stock'], 3)} {row['unit']}）"
            )
    if shortages:
        return [], shortages

    for row in rows:
        # ROUND 避免浮點誤差累積（例如 5 - 3.2 = 1.7999999…）
        db.execute(
            "UPDATE ingredients SET current_stock = ROUND(current_stock - ?, 3) "
            "WHERE id = ?",
            (row["quantity_needed"] * quantity, row["ingredient_id"]),
        )
    return [row["ingredient_id"] for row in rows], []


def restore(db: sqlite3.Connection, drink_id: int, quantity: int) -> list[int]:
    """把一筆訂單的配方用量加回庫存（未 commit）。訂單尚未製作就取消時使用。"""
    rows = db.execute(_RECIPE_SQL, (drink_id,)).fetchall()
    for row in rows:
        db.execute(
            "UPDATE ingredients SET current_stock = ROUND(current_stock + ?, 3) "
            "WHERE id = ?",
            (row["quantity_needed"] * quantity, row["ingredient_id"]),
        )
    return [row["ingredient_id"] for row in rows]


def low_stock(db: sqlite3.Connection) -> list[dict]:
    """低於補貨點的原料列表。"""
    rows = db.execute(
        "SELECT * FROM ingredients WHERE current_stock < reorder_point ORDER BY name"
    ).fetchall()
    return [dict(row) for row in rows]


async def broadcast_stock_events(db: sqlite3.Connection, manager, affected: list[int]):
    """庫存變動後的推播：先通知刷新，再檢查是否需要補貨警示。"""
    if affected:
        await manager.broadcast(
            {"event": "ingredient_updated", "ingredient_ids": affected}
        )
    alerts = low_stock(db)
    if alerts:
        await manager.broadcast({"event": "stock_alert", "alerts": alerts})
