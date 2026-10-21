#!/usr/bin/env python
"""一次性：把已結算日子的備註補回 historical_orders.notes。

用法（在專案根目錄，需先設定環境變數 DATABASE_URL）:
    python backfill_history_notes.py                 # 預設：只盤點，不寫入
    python backfill_history_notes.py --execute       # 實際寫入
    python backfill_history_notes.py --date 2026-10-16          # 指定單一日期
    python backfill_history_notes.py --date 2026-10-16 --execute

為什麼補得回來
--------------
每日結算（summary.settle_day）只會 INSERT 歷史表，從來不刪 order_groups /
order_items，所以結算過的日子，原始的 special_request 通常還留在資料庫裡。
這支腳本就是拿那些原始資料，用和結算完全相同的規則重組 notes 再寫回去。

組字串的規則與 summary.py 的 _SNAPSHOT_ORDERS 一致：
    「飲品名:備註 | 飲品名:備註」，沒寫備註的品項略過，整張都沒有就維持 NULL。

只 UPDATE historical_orders.notes 一個欄位，不新增、不刪除任何列，
也不碰 order_groups、order_items、daily_summary 或其他欄位。
已經有 notes 的列不會被覆蓋。
"""

import argparse
import os
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

DEFAULT_DATE = "2026-10-16"

# 和結算時組 notes 的規則完全相同
_NOTES_EXPR = """
    (SELECT string_agg(d.name || ':' || i.special_request, ' | ' ORDER BY i.id)
     FROM order_items i JOIN drinks d ON d.id = i.drink_id
     WHERE i.group_id = h.order_id
       AND i.special_request IS NOT NULL
       AND btrim(i.special_request) <> '')
"""


def survey(conn: Connection, day) -> dict:
    """盤點：哪些歷史訂單補得回來、哪些原始資料已經不在了。"""
    where_day = "h.business_date = :d" if day else "TRUE"
    params = {"d": day} if day else {}

    rows = conn.execute(
        text(
            f"""
            SELECT h.business_date, h.order_id, h.items, h.notes,
                   (SELECT COUNT(*) FROM order_groups g WHERE g.id = h.order_id)
                       AS live_group,
                   {_NOTES_EXPR} AS rebuilt
            FROM historical_orders h
            WHERE {where_day}
            ORDER BY h.business_date, h.order_id
            """
        ),
        params,
    ).fetchall()

    fillable, already, no_notes, gone = [], [], [], []
    for r in rows:
        if r.notes:
            already.append(r)
        elif not r.live_group:
            gone.append(r)          # 原始訂單已被刪（例如跑過清理腳本）
        elif r.rebuilt:
            fillable.append(r)
        else:
            no_notes.append(r)      # 原始資料還在，但當初就沒寫備註
    return {"rows": rows, "fillable": fillable, "already": already,
            "no_notes": no_notes, "gone": gone}


def report(plan: dict, day) -> int:
    scope = f"{day}" if day else "全部已結算日期"
    print(f"盤點範圍：{scope}　歷史訂單共 {len(plan['rows'])} 筆\n")

    print(f"【可補回】{len(plan['fillable'])} 筆")
    for r in plan["fillable"]:
        print(f"     {r.business_date} #{r.order_id:<4} {r.items}")
        print(f"            → notes: {r.rebuilt}")

    if plan["no_notes"]:
        print(f"\n【原始訂單還在，但當初就沒寫備註】{len(plan['no_notes'])} 筆，不需要補")
    if plan["already"]:
        print(f"\n【已經有 notes】{len(plan['already'])} 筆，不覆蓋")
    if plan["gone"]:
        print(f"\n【補不回來】{len(plan['gone'])} 筆：原始的 order_items 已經不在資料庫裡")
        for r in plan["gone"]:
            print(f"     {r.business_date} #{r.order_id:<4} {r.items}")
        print("     （可能是跑過 cleanup_test_orders.py 之類的清理腳本刪掉了）")
    return len(plan["fillable"])


def backfill(conn: Connection, day) -> int:
    """只更新 notes 還是空的那些列。"""
    where_day = "h.business_date = :d" if day else "TRUE"
    params = {"d": day} if day else {}
    return conn.execute(
        text(
            f"""
            UPDATE historical_orders h
            SET notes = {_NOTES_EXPR}
            WHERE {where_day}
              AND h.notes IS NULL
              AND {_NOTES_EXPR} IS NOT NULL
            """
        ),
        params,
    ).rowcount


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把已結算日子的備註補回 historical_orders.notes（預設只盤點）"
    )
    parser.add_argument("--execute", action="store_true", help="真的寫入，否則只盤點")
    parser.add_argument(
        "--date",
        default=DEFAULT_DATE,
        help=f"要補的營業日 YYYY-MM-DD，預設 {DEFAULT_DATE}；用 all 表示全部",
    )
    args = parser.parse_args()

    day = None
    if args.date != "all":
        try:
            day = date.fromisoformat(args.date)
        except ValueError:
            print("✗ 日期格式應為 YYYY-MM-DD（或 all）", file=sys.stderr)
            return 1

    if not os.getenv("DATABASE_URL"):
        print("✗ 缺少環境變數 DATABASE_URL", file=sys.stderr)
        print("  cmd:        set DATABASE_URL=...", file=sys.stderr)
        print('  PowerShell: $env:DATABASE_URL = "..."', file=sys.stderr)
        print("  bash:       export DATABASE_URL=...", file=sys.stderr)
        return 1

    from backend.database import engine

    conn = engine.connect()
    try:
        plan = survey(conn, day)
        count = report(plan, day)

        if not args.execute:
            conn.rollback()
            print("\n這是盤點模式，沒有寫入任何資料。")
            if count:
                print("確認無誤後，重跑一次並加上 --execute：")
                print(f"    python backfill_history_notes.py --date {args.date} --execute")
            return 0

        if not count:
            conn.rollback()
            print("\n沒有需要補的資料。")
            return 0

        changed = backfill(conn, day)
        conn.commit()  # 到這裡才寫入；上面任一步出錯都會整個回復
    except Exception as exc:
        conn.rollback()
        print(f"\n✗ 發生錯誤，已全部回復，資料庫沒有任何變動：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"\n✓ 已補上 {changed} 筆的 notes（單一 transaction）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
