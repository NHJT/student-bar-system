"""每日結算：把當日營運數字寫進 daily_summary。

排程每天 23:59 執行一次；當天沒有任何訂單就跳過不存。
原始訂單不會被刪除，儀表板只是改為只顯示「今天」的資料。
"""

import json
import logging
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.database import engine, rows_to_dicts
from backend.routers.stats import ANDON_THRESHOLDS

logger = logging.getLogger("uvicorn.error")

# 最多保留幾個營業日的歷史資料
HISTORY_LIMIT = 90

# 當日各項數字。Andon 門檻是「每一站」各自的停留時間，
# 只有兩端時間戳都存在才算得出來，所以沒做完的那一站不列入超時統計。
_DAY_NUMBERS = text(
    """
    SELECT
        COUNT(*)::int AS total_orders,
        COALESCE(SUM(quantity) FILTER (WHERE status <> 'cancelled'), 0)::int
            AS drinks_sold,
        ROUND(
            (AVG(EXTRACT(EPOCH FROM (completed_at - started_at)) / 60)
             FILTER (WHERE started_at IS NOT NULL AND completed_at IS NOT NULL)
            )::numeric, 1)::double precision AS avg_prep_minutes,
        ROUND(
            (MAX(EXTRACT(EPOCH FROM (delivered_at - placed_at)) / 60)
             FILTER (WHERE delivered_at IS NOT NULL)
            )::numeric, 1)::double precision AS max_wait_minutes,
        COUNT(*) FILTER (
            WHERE status <> 'cancelled' AND (
                (started_at IS NOT NULL
                 AND EXTRACT(EPOCH FROM (started_at - placed_at)) / 60 >= :t_new)
                OR (started_at IS NOT NULL AND completed_at IS NOT NULL
                    AND EXTRACT(EPOCH FROM (completed_at - started_at)) / 60
                        >= :t_preparing)
                OR (completed_at IS NOT NULL AND delivered_at IS NOT NULL
                    AND EXTRACT(EPOCH FROM (delivered_at - completed_at)) / 60
                        >= :t_completed)
            )
        )::int AS overdue_orders,
        COUNT(*) FILTER (WHERE edit_count > 0)::int AS edited_orders,
        COUNT(*) FILTER (
            WHERE payment_status = 'unpaid' AND status <> 'cancelled'
        )::int AS unpaid_orders
    FROM orders
    WHERE placed_at::date = :business_date
    """
)

_DAY_TOP_DRINKS = text(
    """
    SELECT d.name, SUM(o.quantity)::int AS qty
    FROM orders o JOIN drinks d ON d.id = o.drink_id
    WHERE o.placed_at::date = :business_date AND o.status <> 'cancelled'
    GROUP BY d.name
    ORDER BY qty DESC, d.name
    """
)

_UPSERT = text(
    """
    INSERT INTO daily_summary (
        business_date, total_orders, drinks_sold, avg_prep_minutes,
        max_wait_minutes, overdue_orders, edit_rate, unpaid_orders, top_drinks
    ) VALUES (
        :business_date, :total_orders, :drinks_sold, :avg_prep_minutes,
        :max_wait_minutes, :overdue_orders, :edit_rate, :unpaid_orders,
        CAST(:top_drinks AS JSONB)
    )
    ON CONFLICT (business_date) DO UPDATE SET
        total_orders     = EXCLUDED.total_orders,
        drinks_sold      = EXCLUDED.drinks_sold,
        avg_prep_minutes = EXCLUDED.avg_prep_minutes,
        max_wait_minutes = EXCLUDED.max_wait_minutes,
        overdue_orders   = EXCLUDED.overdue_orders,
        edit_rate        = EXCLUDED.edit_rate,
        unpaid_orders    = EXCLUDED.unpaid_orders,
        top_drinks       = EXCLUDED.top_drinks,
        created_at       = NOW()
    """
)


# 當日訂單原始紀錄，供事後自行計算指標
_SNAPSHOT_ORDERS = text(
    """
    INSERT INTO historical_orders (
        business_date, order_id, table_number, drink_name, quantity, status,
        placed_at, started_at, completed_at, delivered_at,
        payment_status, payment_completed_at, is_modified, modify_count
    )
    SELECT o.placed_at::date, o.id, o.table_number, d.name, o.quantity, o.status,
           o.placed_at, o.started_at, o.completed_at, o.delivered_at,
           o.payment_status, o.payment_completed_at,
           o.edit_count > 0, o.edit_count
    FROM orders o JOIN drinks d ON d.id = o.drink_id
    WHERE o.placed_at::date = :business_date
    """
)


def _trim_to_limit(conn: Connection) -> int:
    """只保留最新的 HISTORY_LIMIT 個營業日，多的從最舊的開始刪。

    historical_orders 跟著 daily_summary 一起裁，兩邊的日期永遠一致。
    回傳刪掉的營業日數。
    """
    trimmed = conn.execute(
        text(
            """
            DELETE FROM daily_summary
            WHERE business_date NOT IN (
                SELECT business_date FROM daily_summary
                ORDER BY business_date DESC LIMIT :limit
            )
            """
        ),
        {"limit": HISTORY_LIMIT},
    ).rowcount
    conn.execute(
        text(
            "DELETE FROM historical_orders WHERE business_date NOT IN "
            "(SELECT business_date FROM daily_summary)"
        )
    )
    return trimmed


def settle_day(conn: Connection, business_date: date) -> dict | None:
    """結算某一天並寫入歷史資料（未 commit）。當天沒有訂單則回傳 None。"""
    params = {
        "business_date": business_date,
        "t_new": ANDON_THRESHOLDS["new"],
        "t_preparing": ANDON_THRESHOLDS["preparing"],
        "t_completed": ANDON_THRESHOLDS["completed"],
    }
    numbers = conn.execute(_DAY_NUMBERS, params).fetchone()
    if not numbers.total_orders:
        return None  # 今日沒有實際訂單，不儲存

    top_drinks = rows_to_dicts(
        conn.execute(_DAY_TOP_DRINKS, {"business_date": business_date})
    )
    edit_rate = round(numbers.edited_orders / numbers.total_orders * 100, 1)

    record = {
        "business_date": business_date,
        "total_orders": numbers.total_orders,
        "drinks_sold": numbers.drinks_sold,
        "avg_prep_minutes": numbers.avg_prep_minutes,
        "max_wait_minutes": numbers.max_wait_minutes,
        "overdue_orders": numbers.overdue_orders,
        "edit_rate": edit_rate,
        "unpaid_orders": numbers.unpaid_orders,
    }
    conn.execute(
        _UPSERT,
        {**record, "top_drinks": json.dumps(top_drinks, ensure_ascii=False)},
    )

    # 原始訂單紀錄整日重存一次，重跑同一天不會產生重複
    conn.execute(
        text("DELETE FROM historical_orders WHERE business_date = :business_date"),
        {"business_date": business_date},
    )
    snapshot = conn.execute(_SNAPSHOT_ORDERS, {"business_date": business_date}).rowcount

    # 寫入後再裁切，確保永遠不超過上限
    trimmed = _trim_to_limit(conn)

    record["business_date"] = business_date.isoformat()
    record["top_drinks"] = top_drinks
    record["orders_archived"] = snapshot
    record["trimmed"] = trimmed
    return record


def settle_today() -> dict | None:
    """結算今天（排程每天 23:59 呼叫）。"""
    with engine.connect() as conn:
        today = conn.execute(text("SELECT NOW()::date")).scalar()
        result = settle_day(conn, today)
        conn.commit()
    if result:
        logger.info(
            "每日結算完成 %s：訂單 %s 筆、售出 %s 杯",
            result["business_date"],
            result["total_orders"],
            result["drinks_sold"],
        )
    else:
        logger.info("今日沒有訂單，略過結算")
    return result


def backfill_missing() -> list[str]:
    """補結算：把「有訂單但沒有歷史紀錄」的過去日期補上。

    服務在 23:59 剛好重啟或部署時排程會錯過，啟動時補一次可避免整天的
    數據消失。只往回補 HISTORY_LIMIT 天，再舊的反正也會被裁掉。
    """
    filled = []
    with engine.connect() as conn:
        missing = conn.execute(
            text(
                """
                SELECT DISTINCT placed_at::date AS business_date
                FROM orders
                WHERE placed_at::date < NOW()::date
                  AND placed_at::date > NOW()::date - :limit
                  AND placed_at::date NOT IN (SELECT business_date FROM daily_summary)
                ORDER BY business_date
                """
            ),
            {"limit": HISTORY_LIMIT},
        ).fetchall()
        for row in missing:
            if settle_day(conn, row.business_date):
                filled.append(row.business_date.isoformat())
        conn.commit()
    if filled:
        logger.info("補上遺漏的每日結算：%s", "、".join(filled))
    return filled
