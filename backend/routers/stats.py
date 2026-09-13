"""經理儀表板統計 API。"""

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.database import get_db, rows_to_dicts

router = APIRouter(prefix="/api/stats", tags=["stats"])

# 三段式 Andon 門檻（分鐘），與前端 common.js 的 ANDON_THRESHOLDS 一致
ANDON_THRESHOLDS = {
    "new": 5,  # 未開始製作
    "preparing": 10,  # 製作中
    "completed": 3,  # 完成後未送達
}

# 連線的 session timezone 已設為 APP_TIMEZONE，所以這裡的日期比較就是「當地日期」
_TODAY = "placed_at::date = NOW()::date"

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

# 在目前狀態已停留幾分鐘
_MINUTES_STUCK = f"(EXTRACT(EPOCH FROM (NOW() - ({_REF_TS}))) / 60)::double precision"


@router.get("")
def get_stats(db: Connection = Depends(get_db)):
    def one(sql: str, params: dict | None = None):
        return db.execute(text(sql), params or {}).scalar()

    today_orders = one(f"SELECT COUNT(*) FROM orders WHERE {_TODAY}")
    drinks_sold = one(
        f"SELECT COALESCE(SUM(quantity), 0) FROM orders "
        f"WHERE {_TODAY} AND status != 'cancelled'"
    )
    cancelled_today = one(
        f"SELECT COUNT(*) FROM orders WHERE {_TODAY} AND status = 'cancelled'"
    )
    # 未付款只看今天，與儀表板「每日結算後歸零」一致
    unpaid = one(
        f"SELECT COUNT(*) FROM orders "
        f"WHERE {_TODAY} AND payment_status = 'unpaid' AND status != 'cancelled'"
    )

    # 輸入錯誤率量測：今日被改過的訂單數與總修改次數
    edited_orders = one(f"SELECT COUNT(*) FROM orders WHERE {_TODAY} AND edit_count > 0")
    total_edits = one(f"SELECT COALESCE(SUM(edit_count), 0) FROM orders WHERE {_TODAY}")
    edit_rate = round(edited_orders / today_orders * 100, 1) if today_orders else 0.0

    # 今日完成訂單的平均製作時間（started_at → completed_at，分鐘）
    avg_prep = one(
        f"""
        SELECT ROUND(
            AVG(EXTRACT(EPOCH FROM (completed_at - started_at)) / 60)::numeric, 1
        )
        FROM orders
        WHERE {_TODAY} AND started_at IS NOT NULL AND completed_at IS NOT NULL
        """
    )

    # 今日熱門品項（杯數前 5 名，不含已取消）
    top_drinks = rows_to_dicts(
        db.execute(
            text(
                f"""
                SELECT d.name, SUM(o.quantity) AS qty
                FROM orders o JOIN drinks d ON d.id = o.drink_id
                WHERE {_TODAY} AND o.status != 'cancelled'
                GROUP BY o.drink_id, d.name ORDER BY qty DESC, d.name LIMIT 5
                """
            )
        )
    )

    # 超時訂單：在目前狀態停留超過「該狀態門檻」的進行中訂單
    overdue = rows_to_dicts(
        db.execute(
            text(
                f"""
                SELECT o.id, o.table_number, o.quantity, o.status,
                       d.name AS drink_name,
                       ROUND(({_MINUTES_STUCK})::numeric)::double precision
                           AS minutes_stuck,
                       {_ANDON_LIMIT} AS threshold_minutes
                FROM orders o JOIN drinks d ON d.id = o.drink_id
                WHERE o.status IN ('new', 'preparing', 'completed')
                  AND ({_MINUTES_STUCK}) >= ({_ANDON_LIMIT})
                ORDER BY minutes_stuck DESC
                """
            ),
            ANDON_THRESHOLDS,
        )
    )

    return {
        "today_orders": today_orders,
        "drinks_sold": drinks_sold,
        "cancelled_today": cancelled_today,
        "unpaid": unpaid,
        "avg_prep_minutes": float(avg_prep) if avg_prep is not None else None,
        "edited_orders": edited_orders,
        "total_edits": total_edits,
        "edit_rate": edit_rate,  # 今日被修改過的訂單占比（%）
        "top_drinks": top_drinks,
        "overdue": overdue,
        "andon_thresholds": ANDON_THRESHOLDS,
    }
