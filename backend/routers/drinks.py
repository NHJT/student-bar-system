"""飲料 CRUD API（第二步實作內容）。"""

import sqlite3

from fastapi import APIRouter, Depends

from backend.database import get_db

router = APIRouter(prefix="/api/drinks", tags=["drinks"])


@router.get("")
def list_drinks(db: sqlite3.Connection = Depends(get_db)):
    rows = db.execute("SELECT * FROM drinks ORDER BY category, name").fetchall()
    return [dict(row) for row in rows]


# TODO(第二步): POST 新增、PUT 更新、切換 is_available
