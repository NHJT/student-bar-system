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


# 舊資料庫升級用：(資料表, 欄位, 欄位定義)
# schema.sql 是 CREATE TABLE IF NOT EXISTS，已存在的資料表不會自動長出新欄位，
# 所以新增欄位時要同時登記在這裡，既有的 bar.db 才會一起升級。
_MIGRATIONS = [
    ("orders", "edit_count", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    for table, column, definition in _MIGRATIONS:
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:  # 資料表還不存在，schema.sql 會建到最新版
            continue
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def init_db() -> None:
    """依 schema.sql 建立資料表（已存在則跳過），並補上缺少的欄位。應用啟動時呼叫。"""
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()
