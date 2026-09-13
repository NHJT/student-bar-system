"""PostgreSQL 連線（SQLAlchemy）與資料表初始化。

所有連線設定都從環境變數讀取，程式碼裡不寫死任何密碼或連線字串：

    DATABASE_URL   必填，例如 postgresql://user:pw@host:5432/dbname
                   （Railway 會自動注入這個變數）
    APP_TIMEZONE   選填，顯示用時區，預設 Asia/Taipei
    SQL_ECHO       選填，設為 1 會把實際送出的 SQL 印到 log
"""

import os
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Connection

BASE_DIR = Path(__file__).resolve().parent
SCHEMA_PATH = BASE_DIR / "schema.sql"

# 顯示用時區：時間在資料庫一律存 UTC，輸出前才轉成這個時區
APP_TIMEZONE = os.getenv("APP_TIMEZONE", "Asia/Taipei")

try:
    DISPLAY_TZ = ZoneInfo(APP_TIMEZONE)
except ZoneInfoNotFoundError as exc:
    raise RuntimeError(f"APP_TIMEZONE 不是有效的時區名稱: {APP_TIMEZONE}") from exc


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "缺少環境變數 DATABASE_URL。\n"
            "本機開發可先設定，例如：\n"
            "  export DATABASE_URL=postgresql://postgres@127.0.0.1:5432/studentbar"
        )
    # Railway／Heroku 有時給的是 postgres:// 開頭，SQLAlchemy 2.x 需要完整 driver 名稱
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql+psycopg2://", 1)
    elif url.startswith("postgresql://"):
        url = url.replace("postgresql://", "postgresql+psycopg2://", 1)
    return url


engine = create_engine(
    _database_url(),
    pool_pre_ping=True,  # 連線被雲端服務閒置回收後自動重連
    echo=os.getenv("SQL_ECHO") == "1",
)


@event.listens_for(engine, "connect")
def _set_session_timezone(dbapi_conn, _record):
    """把每條連線的時區設成 APP_TIMEZONE。

    這樣 SQL 裡的 NOW()::date 等運算就是「當地日期」，
    不必在每個查詢裡重複傳時區參數。
    """
    with dbapi_conn.cursor() as cur:
        cur.execute(f"SET TIME ZONE '{APP_TIMEZONE}'")


def get_db():
    """取得一條資料庫連線（FastAPI dependency 用）。

    採 SQLAlchemy 2.0 的 commit-as-you-go：由呼叫端自行 db.commit()／db.rollback()。
    """
    with engine.connect() as conn:
        yield conn


# ---------- 查詢結果轉換 ----------
def _convert(value):
    """把 TIMESTAMPTZ 轉成帶時區位移的 ISO-8601 字串。

    例如 "2026-08-14T20:05:00+08:00"。一定要帶位移量：前端用 new Date() 解析時
    才會得到正確的絕對時刻（否則瀏覽器會用自己的時區去解讀，Andon 計時就會算錯）；
    前端顯示時取 slice(11, 16) 得到的則是酒吧當地的時:分。
    """
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            value = value.astimezone(DISPLAY_TZ)
        else:  # 理論上不會發生（欄位都是 TIMESTAMPTZ），保險起見補上時區
            value = value.replace(tzinfo=DISPLAY_TZ)
        return value.isoformat(timespec="seconds")
    return value


def row_to_dict(row) -> dict:
    return {key: _convert(value) for key, value in row._mapping.items()}


def rows_to_dicts(result) -> list[dict]:
    return [row_to_dict(row) for row in result]


# ---------- 初始化與升級 ----------
# 舊資料庫升級用：(資料表, 欄位, 欄位定義)
# schema.sql 是 CREATE TABLE IF NOT EXISTS，已存在的資料表不會自動長出新欄位，
# 所以新增欄位時要同時登記在這裡。
_MIGRATIONS = [
    ("orders", "edit_count", "INTEGER NOT NULL DEFAULT 0"),
    # 舊資料無從得知當初的付款時間，補上欄位後只有之後的訂單才會有值
    ("orders", "payment_completed_at", "TIMESTAMPTZ"),
]


def _migrate(conn: Connection) -> None:
    for table, column, definition in _MIGRATIONS:
        exists = conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :table AND column_name = :column"
            ),
            {"table": table, "column": column},
        ).fetchone()
        if not exists:
            conn.execute(
                text(f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {definition}")
            )


def _table_exists(conn: Connection, table: str) -> bool:
    return bool(
        conn.execute(
            text("SELECT to_regclass(:name)"), {"name": f"public.{table}"}
        ).scalar()
    )


def _column_exists(conn: Connection, table: str, column: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT 1 FROM information_schema.columns "
                "WHERE table_name = :t AND column_name = :c"
            ),
            {"t": table, "c": column},
        ).fetchone()
    )


def _data_migrations(conn: Connection) -> list[str]:
    """結構改變時搬移既有資料。每一段都可重複執行。"""
    done = []

    # 一張訂單多品項：舊的 orders 每一筆轉成「一張訂單 + 一個品項」。
    # 沿用原本的 id，historical_orders.order_id 才對得上。
    if _table_exists(conn, "orders"):
        pending = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()
        already = conn.execute(text("SELECT COUNT(*) FROM order_groups")).scalar()
        if pending and not already:
            conn.execute(
                text(
                    """
                    INSERT INTO order_groups (id, table_number, status, payment_status,
                        payment_completed_at, edit_count, placed_at, started_at,
                        completed_at, delivered_at)
                    SELECT id, table_number, status, payment_status,
                           payment_completed_at, edit_count, placed_at, started_at,
                           completed_at, delivered_at
                    FROM orders
                    """
                )
            )
            conn.execute(
                text(
                    """
                    INSERT INTO order_items (group_id, drink_id, quantity,
                                             special_request, status)
                    SELECT id, drink_id, quantity, special_request, status FROM orders
                    """
                )
            )
            conn.execute(
                text(
                    "SELECT setval('order_groups_id_seq', "
                    "COALESCE((SELECT MAX(id) FROM order_groups), 0) + 1, false)"
                )
            )
            done.append(f"搬移 {pending} 筆舊訂單到 order_groups / order_items")
        # 舊表保留成備份，並拿掉外鍵以免擋住飲品刪除
        conn.execute(
            text("ALTER TABLE orders DROP CONSTRAINT IF EXISTS orders_drink_id_fkey")
        )
        conn.execute(text("ALTER TABLE orders RENAME TO orders_backup"))
        done.append("舊的 orders 表已更名為 orders_backup（確認無誤後可自行刪除）")

    # historical_orders：單一飲品欄位改為品項摘要
    if _table_exists(conn, "historical_orders") and _column_exists(
        conn, "historical_orders", "drink_name"
    ):
        conn.execute(
            text("ALTER TABLE historical_orders ADD COLUMN IF NOT EXISTS items TEXT")
        )
        conn.execute(
            text(
                "ALTER TABLE historical_orders "
                "ADD COLUMN IF NOT EXISTS item_count INTEGER"
            )
        )
        conn.execute(
            text(
                "UPDATE historical_orders "
                "SET items = drink_name || ' x' || quantity, item_count = 1 "
                "WHERE items IS NULL"
            )
        )
        conn.execute(text("ALTER TABLE historical_orders DROP COLUMN drink_name"))
        done.append("historical_orders 的 drink_name 已轉換為 items 品項摘要")

    return done


def init_db() -> None:
    """依 schema.sql 建立資料表（已存在則跳過），並補上缺少的欄位。"""
    # schema.sql 會整份送出，裡面可能出現 % 或 : 這類字元（例如註解文字）。
    # 只要經過參數替換就會出錯，所以直接用 DBAPI cursor 執行。
    raw = engine.raw_connection()
    try:
        with raw.cursor() as cur:
            cur.execute(SCHEMA_PATH.read_text(encoding="utf-8"))
        raw.commit()
    finally:
        raw.close()

    with engine.connect() as conn:
        _migrate(conn)
        for note in _data_migrations(conn):
            print(f"  · {note}")
        conn.commit()
