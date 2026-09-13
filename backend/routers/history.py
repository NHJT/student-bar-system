"""歷史數據 API：讀取每日結算紀錄、下載原始訂單、手動觸發結算。"""

import csv
import io
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend import summary
from backend.database import DISPLAY_TZ, get_db, row_to_dict, rows_to_dicts
from backend.ws import manager

router = APIRouter(prefix="/api/history", tags=["history"])

# raw_orders：該日留存的原始訂單筆數。舊版結算的日子沒有原始紀錄，
# 前端據此決定要不要顯示「下載 CSV」，避免點了得到壞掉的檔案。
_SELECT = """
    SELECT d.business_date, d.total_orders, d.drinks_sold, d.avg_prep_minutes,
           d.max_wait_minutes, d.overdue_orders, d.edit_rate, d.unpaid_orders,
           d.top_drinks, d.created_at,
           (SELECT COUNT(*) FROM historical_orders h
            WHERE h.business_date = d.business_date)::int AS raw_orders
    FROM daily_summary d
"""


@router.get("")
def list_history(db: Connection = Depends(get_db)):
    """所有歷史營業日，新的在前。最多 90 筆，一次回傳供前端展開明細。"""
    return {
        "limit": summary.HISTORY_LIMIT,
        "days": rows_to_dicts(
            db.execute(text(_SELECT + " ORDER BY d.business_date DESC"))
        ),
    }


@router.get("/{business_date}")
def get_history(business_date: str, db: Connection = Depends(get_db)):
    row = db.execute(
        text(_SELECT + " WHERE d.business_date = :d"), {"d": business_date}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="這一天沒有歷史紀錄")
    return row_to_dict(row)


# CSV 欄位：(標題, 資料庫欄位)
_CSV_COLUMNS = [
    ("business_date", "business_date"),
    ("order_id", "order_id"),
    ("table_number", "table_number"),
    ("drink_name", "drink_name"),
    ("quantity", "quantity"),
    ("status", "status"),
    ("placed_at", "placed_at"),
    ("started_at", "started_at"),
    ("completed_at", "completed_at"),
    ("delivered_at", "delivered_at"),
    ("payment_status", "payment_status"),
    ("payment_completed_at", "payment_completed_at"),
    ("is_modified", "is_modified"),
    ("modify_count", "modify_count"),
]


def _csv_value(value):
    """時間輸出成 Excel 認得的當地時間字串；其餘原樣輸出。"""
    if isinstance(value, datetime):
        return value.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")
    if value is None:
        return ""
    return value


@router.get("/{business_date}/orders.csv")
def download_orders_csv(business_date: str, db: Connection = Depends(get_db)):
    """下載該營業日的原始訂單紀錄（CSV）。

    時間欄位一律輸出 APP_TIMEZONE 當地時間的 "YYYY-MM-DD HH:MM:SS"，
    Excel 與 pandas 都能直接當成時間解析。
    """
    try:
        day = date.fromisoformat(business_date)
    except ValueError:
        raise HTTPException(status_code=400, detail="日期格式應為 YYYY-MM-DD")

    rows = db.execute(
        text(
            f"SELECT {', '.join(col for _, col in _CSV_COLUMNS)} "
            "FROM historical_orders WHERE business_date = :d ORDER BY order_id"
        ),
        {"d": day},
    ).fetchall()
    if not rows:
        raise HTTPException(status_code=404, detail="這一天沒有原始訂單紀錄")

    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow([title for title, _ in _CSV_COLUMNS])
    for row in rows:
        writer.writerow([_csv_value(v) for v in row])

    # 加上 BOM，Excel 開啟時才不會把中文品名顯示成亂碼
    body = "﻿" + buffer.getvalue()
    return Response(
        content=body.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="orders-{day.isoformat()}.csv"'
        },
    )


@router.post("/settle")
async def settle_now():
    """手動執行一次今日結算（排程每天 23:59 會自動跑）。

    同一天重複執行會覆蓋既有紀錄，不會產生重複資料。
    """
    result = summary.settle_today()
    if result is None:
        return {"settled": False, "reason": "今日沒有訂單，未儲存"}
    await manager.broadcast(
        {"event": "day_settled", "business_date": result["business_date"]}
    )
    return {"settled": True, "summary": result}
