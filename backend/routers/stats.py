"""經理儀表板統計 API。"""

import sqlite3

from fastapi import APIRouter, Depends

from backend.database import get_db

router = APIRouter(prefix="/api/stats", tags=["stats"])

# 與前端吧台頁的 ANDON_TIMEOUT_MINUTES 一致
ANDON_TIMEOUT_MINUTES = 5

_TODAY = "date(placed_at) = date('now', 'localtime')"

# 各狀態的 Andon 計時基準（與吧台頁相同：算「在目前這一站停留多久」）
_REF_TS = """
    CASE status
        WHEN 'new' THEN placed_at
        WHEN 'preparing' THEN started_at
        WHEN 'completed' THEN completed_at
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

    # 超時訂單：在目前狀態停留超過門檻的進行中訂單
    overdue = [
        dict(row)
        for row in db.execute(
            f"""
            SELECT o.id, o.table_number, o.quantity, o.status, d.name AS drink_name,
                   ROUND((julianday('now', 'localtime') - julianday({_REF_TS})) * 1440)
                       AS minutes_stuck
            FROM orders o JOIN drinks d ON d.id = o.drink_id
            WHERE o.status IN ('new', 'preparing', 'completed')
              AND (julianday('now', 'localtime') - julianday({_REF_TS})) * 1440
                  >= ?
            ORDER BY minutes_stuck DESC
            """,
            (ANDON_TIMEOUT_MINUTES,),
        ).fetchall()
    ]

    return {
        "today_orders": today_orders,
        "drinks_sold": drinks_sold,
        "cancelled_today": cancelled_today,
        "unpaid": unpaid,
        "avg_prep_minutes": avg_prep,  # 今日還沒有完成的訂單時為 null
        "top_drinks": top_drinks,
        "overdue": overdue,
        "andon_timeout_minutes": ANDON_TIMEOUT_MINUTES,
    }
