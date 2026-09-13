"""歷史數據 API：讀取每日結算紀錄、手動觸發結算。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend import summary
from backend.database import get_db, row_to_dict, rows_to_dicts
from backend.ws import manager

router = APIRouter(prefix="/api/history", tags=["history"])

_SELECT = """
    SELECT business_date, total_orders, drinks_sold, avg_prep_minutes,
           max_wait_minutes, overdue_orders, edit_rate, unpaid_orders,
           top_drinks, created_at
    FROM daily_summary
"""


@router.get("")
def list_history(db: Connection = Depends(get_db)):
    """所有歷史營業日，新的在前。最多 90 筆，一次回傳供前端展開明細。"""
    return {
        "limit": summary.HISTORY_LIMIT,
        "days": rows_to_dicts(
            db.execute(text(_SELECT + " ORDER BY business_date DESC"))
        ),
    }


@router.get("/{business_date}")
def get_history(business_date: str, db: Connection = Depends(get_db)):
    row = db.execute(
        text(_SELECT + " WHERE business_date = :d"), {"d": business_date}
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="這一天沒有歷史紀錄")
    return row_to_dict(row)


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
