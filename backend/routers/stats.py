"""經理儀表板統計 API。"""

import sqlite3

from fastapi import APIRouter, Depends

from backend.database import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])

# 三段式 Andon 門檻（分鐘），與前端 common.js 的 ANDON_THRESHOLDS 一致
ANDON_THRESHOLDS = {
    "new": 5,  # 未開始製作
    "preparing": 10,  # 製作中
    "completed": 3,  # 完成後未送達
}

_TODAY = "date(placed_at) = date('now', 'localtime')"

# 各狀態的 Andon 計時基準（與吧台頁相同：算「在目前這一站停留多久」）
_REF_TS = """
    CASE status
        WHEN 'new' THEN placed_at
        WHEN 'preparing' THEN started_at
        WHEN 'completed' THEN completed_at
    END
"""

# 各狀態套用自己的門檻
_ANDON_LIMIT = """
    CASE status
        WHEN 'new' THEN :new
        WHEN 'preparing' THEN :preparing
        WHEN 'completed' THEN :completed
    END
"""


@router.get("")
def get_stats(db: sqlite3.Connection = Depends(get_db)):
    one = lambda sql, *p: db.execute(sql, p).fetchone()[0]  # noqa: E731

    today_orders = one(f"SELECT COUNT(*) FROM orders WHERE {_TODAY}")
    drinks_sold = one(
        f"SELECT COALESCE(SUM(quantity), 0) FROM orders "
        f"WHERE {_TODAY} AND status != 'cancelled'"
    )
    cancelled_today = one(
        f"SELECT COUNT(*) FROM orders WHERE {_TODAY} AND status = 'cancelled'"
    )
    unpaid = one(
        "SELECT COUNT(*) FROM orders "
        "WHERE payment_status = 'unpaid' AND status != 'cancelled'"
    )

    # 輸入錯誤率量測：今日被改過的訂單數與總修改次數
    edited_orders = one(f"SELECT COUNT(*) FROM orders WHERE {_TODAY} AND edit_count > 0")
    total_edits = one(f"SELECT COALESCE(SUM(edit_count), 0) FROM orders WHERE {_TODAY}")
    edit_rate = round(edited_orders / today_orders * 100, 1) if today_orders else 0.0

    # 今日完成訂單的平均製作時間（started_at → completed_at，分鐘）
    avg_prep = one(
        f"""
        SELECT ROUND(AVG((julianday(completed_at) - julianday(started_at)) * 1440), 1)
        FROM orders
        WHERE {_TODAY} AND started_at IS NOT NULL AND completed_at IS NOT NULL
        """
    )

    # 今日熱門品項（杯數前 5 名，不含已取消）
    top_drinks = [
        dict(row)
        for row in db.execute(
            f"""
            SELECT d.name, SUM(o.quantity) AS qty
            FROM orders o JOIN drinks d ON d.id = o.drink_id
            WHERE {_TODAY} AND o.status != 'cancelled'
            GROUP BY o.drink_id ORDER BY qty DESC, d.name LIMIT 5
            """
        ).fetchall()
    ]

    # 超時訂單：在目前狀態停留超過「該狀態門檻」的進行中訂單
    overdue = [
        dict(row)
        for row in db.execute(
            f"""
            SELECT o.id, o.table_number, o.quantity, o.status, d.name AS drink_name,
                   ROUND((julianday('now', 'localtime') - julianday({_REF_TS})) * 1440)
                       AS minutes_stuck,
                   {_ANDON_LIMIT} AS threshold_minutes
            FROM orders o JOIN drinks d ON d.id = o.drink_id
            WHERE o.status IN ('new', 'preparing', 'completed')
              AND (julianday('now', 'localtime') - julianday({_REF_TS})) * 1440
                  >= {_ANDON_LIMIT}
            ORDER BY minutes_stuck DESC
            """,
            ANDON_THRESHOLDS,
        ).fetchall()
    ]

    return {
        "today_orders": today_orders,
        "drinks_sold": drinks_sold,
        "cancelled_today": cancelled_today,
        "unpaid": unpaid,
        "avg_prep_minutes": avg_prep,  # 今日還沒有完成的訂單時為 null
        "edited_orders": edited_orders,
        "total_edits": total_edits,
        "edit_rate": edit_rate,  # 今日被修改過的訂單占比（%）
        "top_drinks": top_drinks,
        "overdue": overdue,
        "andon_thresholds": ANDON_THRESHOLDS,
    }
