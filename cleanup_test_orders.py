#!/usr/bin/env python
"""一次性清理：營業前清掉測試訂單，保留飲品／原料／配方的設定。

用法（在專案根目錄，需先設定環境變數 DATABASE_URL）:
    python cleanup_test_orders.py             # 預設：只盤點，不刪任何東西
    python cleanup_test_orders.py --execute   # 真的刪除（單一 transaction）

資料表名稱與欄位都對照 backend/schema.sql 與 backend/models.py 確認過：

    order_groups       一張訂單。id / table_number / status / payment_status /
                       payment_completed_at / edit_count / placed_at /
                       started_at / completed_at / delivered_at
    order_items        訂單明細（不是 order_details）。id / group_id →
                       order_groups.id / drink_id / quantity /
                       special_request / status
    historical_orders  每日結算存的原始訂單。PK (business_date, order_id)
    daily_summary      每日彙總。PK business_date

刪除範圍
--------
1. placed_at 早於 2026-10-16 18:59（Asia/Taipei）的 order_groups，
   以及這些訂單底下的 order_items
2. historical_orders 與 daily_summary 中 business_date 早於 2026-10-16 的資料
   （含 9/13 那批測試資料）

完全不會碰 drinks、ingredients、recipes，也不調整任何庫存數字——
這支腳本的 SQL 只出現上面那四張表。

注意：刪除訂單不會把當初扣掉的原料加回庫存（依要求不動庫存數量）。
"""

import argparse
import os
import sys

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


def remaining(conn: Connection) -> dict:
    return {
        table: conn.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
        for table in TABLES
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清理營業前的測試訂單（預設只盤點，不刪除）"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="真的執行刪除；不加這個參數只會印出將刪除的筆數",
    )
    args = parser.parse_args()

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
        plan = survey(conn)
        total = report(plan)

        if not args.execute:
            conn.rollback()
            print("\n這是盤點模式，沒有刪除任何資料。")
            if total:
                print("確認上面的數字無誤後，重跑一次並加上 --execute：")
                print("    python cleanup_test_orders.py --execute")
            return 0

        if not total:
            conn.rollback()
            print("\n沒有符合條件的資料，不需要刪除。")
            return 0

        deleted = purge(conn)
        left = remaining(conn)
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
    for table in TABLES:
        print(f"{table:<20}{deleted[table]:>8}{left[table]:>10}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
