"""SQLite 連線與初始化。"""

import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "bar.db"
SCHEMA_PATH = BASE_DIR / "schema.sql"


def get_db() -> sqlite3.Connection:
    """取得一條資料庫連線（FastAPI dependency 用）。

    用法:
        @app.get(...)
        def endpoint(db: sqlite3.Connection = Depends(get_db)): ...
    """
    # FastAPI 同步端點跑在 threadpool，同一請求的建立與收尾可能在不同執行緒；
    # 連線不跨請求共用，因此關閉同執行緒檢查是安全的
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row  # 讓查詢結果可以用欄位名取值
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """依 schema.sql 建立資料表（已存在則跳過）。應用啟動時呼叫。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        conn.commit()
    finally:
        conn.close()
