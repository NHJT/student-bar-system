#!/usr/bin/env python
"""一次性清理：清掉測試訂單，保留飲品／原料／配方的設定。

兩種模式，都是預設只盤點、加 --execute 才真的刪、整批一個 transaction。

時間界線模式（原本的行為，不帶 --date 就是這個）:
    python cleanup_test_orders.py             # 只盤點，不刪任何東西
    python cleanup_test_orders.py --execute   # 真的刪除

單一營業日模式:
    python cleanup_test_orders.py --date 2026-10-16
    python cleanup_test_orders.py --date 2026-10-16 --execute

資料表名稱與欄位都對照 backend/schema.sql 與 backend/models.py 確認過：

    order_groups       一張訂單。id / table_number / status / payment_status /
                       payment_completed_at / edit_count / placed_at /
                       started_at / completed_at / delivered_at
    order_items        訂單明細（不是 order_details）。id / group_id →
                       order_groups.id / drink_id / quantity /
                       special_request / status
    order_events       退回上一個狀態的紀錄。group_id → order_groups.id
    historical_orders  每日結算存的原始訂單。PK (business_date, order_id)
    daily_summary      每日彙總。PK business_date

時間界線模式的刪除範圍
----------------------
1. placed_at 早於 2026-10-16 18:59（Asia/Taipei）的 order_groups，
   以及這些訂單底下的 order_items
2. historical_orders 與 daily_summary 中 business_date 早於 2026-10-16 的資料
   （含 9/13 那批測試資料）

單一營業日模式的刪除範圍
------------------------
指定那一天的 order_groups（placed_at::date，和每日結算用的是同一個定義）、
底下的 order_items 與 order_events，加上 historical_orders 與 daily_summary
裡 business_date 等於那一天的資料。只動這一天，前後都不受影響。

兩種模式都完全不會碰 drinks、ingredients、recipes，也不調整任何庫存數字——
這支腳本的 SQL 只出現上面那五張表。

注意：刪除訂單不會把當初扣掉的原料加回庫存（依要求不動庫存數量）。
"""

import argparse
import os
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

# ---------------------------------------------------------------------------
# 刪除界線。寫死在這裡是刻意的：一次性腳本，不給參數調整以免誤用。
# 時間帶明確的 +08:00，不依賴連線的 session timezone，換台機器跑結果一樣。
# ---------------------------------------------------------------------------
ORDER_CUTOFF = "2026-10-16 18:59:00+08:00"  # placed_at 早於這個時刻的訂單
HISTORY_CUTOFF = "2026-10-16"  # business_date 早於這一天的歷史資料

_PARAMS = {"cutoff": ORDER_CUTOFF, "hist": HISTORY_CUTOFF}

# 盤點用的查詢。order_items 透過 group_id 跟著 order_groups 一起算，
# 不另外用時間條件（明細本身沒有時間欄位）。
_SURVEY = {
    "order_groups": """
        SELECT COUNT(*) AS n,
               MIN(placed_at) AS first_at,
               MAX(placed_at) AS last_at
        FROM order_groups
        WHERE placed_at < CAST(:cutoff AS timestamptz)
    """,
    "order_items": """
        SELECT COUNT(*) AS n, NULL AS first_at, NULL AS last_at
        FROM order_items
        WHERE group_id IN (SELECT id FROM order_groups
                           WHERE placed_at < CAST(:cutoff AS timestamptz))
    """,
    "historical_orders": """
        SELECT COUNT(*) AS n,
               MIN(placed_at) AS first_at,
               MAX(placed_at) AS last_at
        FROM historical_orders
        WHERE business_date < CAST(:hist AS date)
    """,
    "daily_summary": """
        SELECT COUNT(*) AS n,
               MIN(business_date) AS first_at,
               MAX(business_date) AS last_at
        FROM daily_summary
        WHERE business_date < CAST(:hist AS date)
    """,
}

TABLES = list(_SURVEY)

# ---------------------------------------------------------------------------
# 單一營業日模式（--date）。
#
# 營業日的定義和每日結算完全一樣：placed_at::date。連線的 session timezone
# 由 backend.database 設成 APP_TIMEZONE，所以這裡的 ::date 就是台北時間的日期，
# 不會因為機器時區不同而抓錯一天。
# ---------------------------------------------------------------------------
_DAY_IN_GROUPS = (
    "group_id IN (SELECT id FROM order_groups "
    "WHERE placed_at::date = CAST(:day AS date))"
)

_DAY_WHERE = {
    "order_groups": "placed_at::date = CAST(:day AS date)",
    "order_items": _DAY_IN_GROUPS,
    "order_events": _DAY_IN_GROUPS,
    "historical_orders": "business_date = CAST(:day AS date)",
    "daily_summary": "business_date = CAST(:day AS date)",
}

# 刪除順序：先清掉指向 order_groups 的兩張子表，再刪訂單本身。
# order_items 設了 ON DELETE CASCADE、order_events 沒設外鍵，
# 兩張都明確先刪，一來不依賴 cascade，二來 rowcount 才報得出各表刪了幾筆。
_DAY_DELETE_ORDER = [
    "order_items",
    "order_events",
    "order_groups",
    "historical_orders",
    "daily_summary",
]

# 盤點時的顯示順序：訂單在前，結算產物在後
_DAY_TABLES = [
    "order_groups",
    "order_items",
    "order_events",
    "historical_orders",
    "daily_summary",
]


def _day_tables(conn: Connection) -> list[str]:
    """這次要處理哪幾張表。

    order_events 是後來才加的，沒跑過 init_db.py 的資料庫沒有這張表。
    先用 to_regclass 探路把它拿掉，而不是讓 DELETE 去撞 UndefinedTable——
    PostgreSQL 一報錯整個交易就變成 aborted，同一批的其他刪除全部跟著失敗。
    """
    from backend.database import table_exists

    return [
        table
        for table in _DAY_TABLES
        if table != "order_events" or table_exists(conn, "order_events")
    ]


def survey_day(conn: Connection, day) -> dict:
    """盤點指定營業日將被刪除的資料。純查詢，不改任何東西。"""
    tables = _day_tables(conn)
    counts = {
        table: conn.execute(
            text(f"SELECT COUNT(*) FROM {table} WHERE {_DAY_WHERE[table]}"),
            {"day": day},
        ).scalar()
        for table in tables
    }
    span = conn.execute(
        text(
            "SELECT MIN(placed_at) AS first_at, MAX(placed_at) AS last_at "
            "FROM order_groups WHERE placed_at::date = CAST(:day AS date)"
        ),
        {"day": day},
    ).fetchone()
    keep = conn.execute(
        text(
            "SELECT COUNT(*) FROM order_groups "
            "WHERE placed_at::date <> CAST(:day AS date)"
        ),
        {"day": day},
    ).scalar()
    return {
        "tables": tables,
        "counts": counts,
        "first_at": span.first_at,
        "last_at": span.last_at,
        "keep_orders": keep,
    }


def report_day(plan: dict, day) -> int:
    print(f"刪除範圍：營業日 {day} 當天（placed_at::date / business_date）")
    print()
    print(f"{'資料表':<20}{'筆數':>8}")
    print("─" * 30)
    total = 0
    for table in plan["tables"]:
        total += plan["counts"][table]
        print(f"{table:<20}{plan['counts'][table]:>8}")
    print("─" * 30)
    print(f"{'合計':<20}{total:>8}")
    print()
    if plan["counts"]["order_groups"]:
        print(f"當天訂單時間範圍：{_fmt(plan['first_at'])} ～ {_fmt(plan['last_at'])}")
    print(f"保留：其他營業日的 {plan['keep_orders']} 筆訂單，完全不受影響")
    print("不會動到 drinks / ingredients / recipes，也不調整任何庫存數字")
    if "order_events" not in plan["tables"]:
        print("（資料庫還沒有 order_events 表，這一張略過）")
    if plan["counts"]["order_groups"]:
        print(
            "\n提醒：刪除訂單不會把當初扣掉的原料加回庫存（依要求不動庫存數量）。\n"
            "      要校正請在經理後台手動調整。"
        )
    return total


def purge_day(conn: Connection, day, tables: list[str]) -> dict:
    """實際刪除指定營業日的資料。呼叫端負責 commit / rollback。"""
    return {
        table: conn.execute(
            text(f"DELETE FROM {table} WHERE {_DAY_WHERE[table]}"), {"day": day}
        ).rowcount
        for table in _DAY_DELETE_ORDER
        if table in tables
    }


def _fmt(value) -> str:
    """時間統一用 Asia/Taipei 顯示；日期欄位原樣輸出。"""
    if value is None:
        return "—"
    from backend.database import DISPLAY_TZ

    if hasattr(value, "tzinfo") and value.tzinfo is not None:
        return value.astimezone(DISPLAY_TZ).strftime("%Y-%m-%d %H:%M:%S")
    return str(value)


def survey(conn: Connection) -> dict:
    """盤點將被刪除的資料。純查詢，不改任何東西。"""
    plan = {}
    for table, sql in _SURVEY.items():
        row = conn.execute(text(sql), _PARAMS).fetchone()
        plan[table] = {"n": row.n, "first_at": row.first_at, "last_at": row.last_at}

    # 界線附近的資料另外列出來，讓使用者確認切點抓得對
    plan["keep_orders"] = conn.execute(
        text(
            "SELECT COUNT(*) FROM order_groups "
            "WHERE placed_at >= CAST(:cutoff AS timestamptz)"
        ),
        _PARAMS,
    ).scalar()
    plan["history_on_cutoff"] = conn.execute(
        text(
            "SELECT COUNT(*) FROM historical_orders "
            "WHERE business_date = CAST(:hist AS date)"
        ),
        _PARAMS,
    ).scalar()
    plan["summary_on_cutoff"] = conn.execute(
        text(
            "SELECT COUNT(*) FROM daily_summary "
            "WHERE business_date = CAST(:hist AS date)"
        ),
        _PARAMS,
    ).scalar()
    return plan


def report(plan: dict) -> int:
    print("刪除界線")
    print(f"  訂單     placed_at      早於 {ORDER_CUTOFF}")
    print(f"  歷史資料 business_date  早於 {HISTORY_CUTOFF}")
    print()
    print(f"{'資料表':<20}{'筆數':>8}   最早               最晚")
    print("─" * 64)
    total = 0
    for table in TABLES:
        info = plan[table]
        total += info["n"]
        print(
            f"{table:<20}{info['n']:>8}   "
            f"{_fmt(info['first_at']):<18} {_fmt(info['last_at'])}"
        )
    print("─" * 64)
    print(f"{'合計':<20}{total:>8}")
    print()
    print(f"保留：{plan['keep_orders']} 筆訂單（placed_at 在界線之後）")
    print("不會動到 drinks / ingredients / recipes，也不調整任何庫存數字")

    # 界線當天的歷史資料不在範圍內（「2026-10-16 以前」取不含當天）。
    # 有資料的話講出來，免得以為一起刪掉了。
    on_cutoff = plan["history_on_cutoff"] + plan["summary_on_cutoff"]
    if on_cutoff:
        print(
            f"\n提醒：{HISTORY_CUTOFF} 當天還有 historical_orders "
            f"{plan['history_on_cutoff']} 筆、daily_summary "
            f"{plan['summary_on_cutoff']} 筆，依界線定義**不在刪除範圍內**。\n"
            "      若這些也是測試資料，把 HISTORY_CUTOFF 改成 2026-10-17 再跑一次。"
        )
    if plan["order_groups"]["n"]:
        print(
            "\n提醒：刪除訂單不會把當初扣掉的原料加回庫存（依要求不動庫存數量）。\n"
            "      要校正請在經理後台手動調整。"
        )
    return total


def purge(conn: Connection) -> dict:
    """實際刪除。呼叫端負責 commit / rollback。

    順序：先刪 order_items 再刪 order_groups。
    order_items.group_id 雖然設了 ON DELETE CASCADE，但明確先刪明細，
    一來不依賴 cascade 的行為，二來 rowcount 才報得出各表實際刪了幾筆。
    """
    deleted = {}
    deleted["order_items"] = conn.execute(
        text(
            "DELETE FROM order_items WHERE group_id IN "
            "(SELECT id FROM order_groups "
            " WHERE placed_at < CAST(:cutoff AS timestamptz))"
        ),
        _PARAMS,
    ).rowcount
    deleted["order_groups"] = conn.execute(
        text(
            "DELETE FROM order_groups "
            "WHERE placed_at < CAST(:cutoff AS timestamptz)"
        ),
        _PARAMS,
    ).rowcount
    deleted["historical_orders"] = conn.execute(
        text(
            "DELETE FROM historical_orders "
            "WHERE business_date < CAST(:hist AS date)"
        ),
        _PARAMS,
    ).rowcount
    deleted["daily_summary"] = conn.execute(
        text("DELETE FROM daily_summary WHERE business_date < CAST(:hist AS date)"),
        _PARAMS,
    ).rowcount
    return deleted


def remaining(conn: Connection, tables: list[str] | None = None) -> dict:
    return {
        table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        for table in (tables if tables is not None else TABLES)
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清理測試訂單（預設只盤點，不刪除）"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真的執行刪除；不加這個參數只會印出將刪除的筆數",
    )
    parser.add_argument(
        "--date",
        metavar="YYYY-MM-DD",
        help="只清除這一個營業日的資料；不加這個參數則沿用原本的時間界線模式",
    )
    args = parser.parse_args()

    day = None
    if args.date is not None:
        try:
            day = date.fromisoformat(args.date)
        except ValueError:
            print("✗ --date 的格式應為 YYYY-MM-DD", file=sys.stderr)
            return 1

    # 連線字串只從環境變數讀，不印出來也不寫進任何檔案
    if not os.getenv("DATABASE_URL"):
        print("✗ 缺少環境變數 DATABASE_URL", file=sys.stderr)
        print("  cmd:        set DATABASE_URL=...", file=sys.stderr)
        print('  PowerShell: $env:DATABASE_URL = "..."', file=sys.stderr)
        print("  bash:       export DATABASE_URL=...", file=sys.stderr)
        return 1

    from backend.database import engine  # 這一行才會建立連線池

    conn = engine.connect()
    try:
        if day is None:
            tables = TABLES
            plan = survey(conn)
            total = report(plan)
            rerun = "python cleanup_test_orders.py --execute"
        else:
            plan = survey_day(conn, day)
            tables = plan["tables"]
            total = report_day(plan, day)
            rerun = f"python cleanup_test_orders.py --date {day} --execute"

        if not args.execute:
            conn.rollback()
            print("\n這是盤點模式，沒有刪除任何資料。")
            if total:
                print("確認上面的數字無誤後，重跑一次並加上 --execute：")
                print(f"    {rerun}")
            return 0

        if not total:
            conn.rollback()
            print("\n沒有符合條件的資料，不需要刪除。")
            return 0

        deleted = purge(conn) if day is None else purge_day(conn, day, tables)
        left = remaining(conn, tables)
        conn.commit()  # 到這裡才寫進去；上面任一步出錯都會整個回復
    except Exception as exc:
        conn.rollback()
        print(
            f"\n✗ 發生錯誤，已全部回復，資料庫沒有任何變動：{exc}",
            file=sys.stderr,
        )
        return 1
    finally:
        conn.close()

    print("\n✓ 已刪除（單一 transaction）")
    print(f"{'資料表':<20}{'已刪除':>8}{'剩餘':>10}")
    print("─" * 40)
    for table in tables:
        print(f"{table:<20}{deleted[table]:>8}{left[table]:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
