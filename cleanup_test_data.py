#!/usr/bin/env python
"""一次性清除測試資料。預設只預覽，要加 --confirm 才真的刪。

用法（在專案根目錄，需先設定 DATABASE_URL）:
    python cleanup_test_data.py             # 只列出會刪什麼，不動資料庫
    python cleanup_test_data.py --confirm   # 真的刪除（單一 transaction）

刪除範圍
--------
(a) 任一品項的特殊需求含「TEST」的訂單，連同該訂單的所有品項
(b) 2026-09-13 的 daily_summary 與 historical_orders
(c) 其他日期中，當天每一筆歷史訂單都帶 TEST 標記的，整天的
    historical_orders 與 daily_summary 一起刪

完全不會動到 drinks、ingredients、recipes，也不調整任何庫存數字。

注意：historical_orders 沒有存 special_request（結算時只留飲品與杯數），
所以 (c) 是把 historical_orders.order_id 接回現存的 order_groups 去判斷。
對不到現存訂單的那一天無法判斷，會列出來但不刪。
"""

import argparse
import os
import sys
from datetime import date

from sqlalchemy import text
from sqlalchemy.engine import Connection

# 要清掉的測試標記與日期。這是一次性腳本，寫死在這裡比用參數清楚
TEST_MARKER = "TEST"  # 比對時不分大小寫
TARGET_DATE = date(2026, 9, 13)

_LIKE = {"pat": f"%{TEST_MARKER}%"}


def _print_connection() -> None:
    """印出連到哪個資料庫，密碼會被遮掉。"""
    from backend.database import APP_TIMEZONE, engine

    url = engine.url
    print("連線目標")
    print(f"  主機     {url.host}:{url.port or 5432}")
    print(f"  資料庫   {url.database}")
    print(f"  使用者   {url.username}")
    print(f"  完整字串 {url.render_as_string(hide_password=True)}")
    print(f"  顯示時區 {APP_TIMEZONE}")
    print()


# ---------------------------------------------------------------------------
# 盤點：先把三類要刪的東西全部算出來，再動手。
# (c) 要讀 order_groups，所以一定要在 (a) 把訂單刪掉「之前」就算完，
# 否則被 (a) 刪掉的訂單會讓 (c) 判成「對不到現存訂單」。
# ---------------------------------------------------------------------------
def _test_orders(conn: Connection) -> list[dict]:
    rows = conn.execute(
        text(
            """
            SELECT g.id, g.table_number, g.placed_at, g.status,
                   (SELECT COUNT(*) FROM order_items i WHERE i.group_id = g.id)
                       AS item_count,
                   COALESCE((SELECT string_agg(d.name || ' x' || i.quantity, ' | '
                                               ORDER BY i.id)
                             FROM order_items i JOIN drinks d ON d.id = i.drink_id
                             WHERE i.group_id = g.id), '') AS items,
                   COALESCE((SELECT string_agg(i.special_request, ' / ' ORDER BY i.id)
                             FROM order_items i
                             WHERE i.group_id = g.id
                               AND i.special_request ILIKE :pat), '') AS marks
            FROM order_groups g
            WHERE EXISTS (SELECT 1 FROM order_items i
                          WHERE i.group_id = g.id AND i.special_request ILIKE :pat)
            ORDER BY g.placed_at, g.id
            """
        ),
        _LIKE,
    ).fetchall()
    return [dict(r._mapping) for r in rows]


def _history_on(conn: Connection, business_date: date) -> tuple[int, int]:
    """某一天的 (historical_orders 筆數, daily_summary 筆數)。"""
    orders = conn.execute(
        text("SELECT COUNT(*) FROM historical_orders WHERE business_date = :d"),
        {"d": business_date},
    ).scalar()
    summary = conn.execute(
        text("SELECT COUNT(*) FROM daily_summary WHERE business_date = :d"),
        {"d": business_date},
    ).scalar()
    return orders, summary


def _all_test_dates(conn: Connection) -> tuple[list[dict], list[dict]]:
    """其他日期裡，哪幾天的歷史訂單「全部」都是 TEST。

    回傳 (可以刪的日期, 不能刪的日期與原因)。
    """
    rows = conn.execute(
        text(
            """
            SELECT h.business_date,
                   COUNT(*)                                  AS total,
                   COUNT(g.id)                               AS matched,
                   COUNT(*) FILTER (WHERE t.group_id IS NOT NULL) AS tagged
            FROM historical_orders h
            LEFT JOIN order_groups g ON g.id = h.order_id
            LEFT JOIN (SELECT DISTINCT group_id FROM order_items
                       WHERE special_request ILIKE :pat) t ON t.group_id = h.order_id
            WHERE h.business_date <> :target
            GROUP BY h.business_date
            ORDER BY h.business_date
            """
        ),
        {**_LIKE, "target": TARGET_DATE},
    ).fetchall()

    deletable, skipped = [], []
    for row in rows:
        summary = conn.execute(
            text("SELECT COUNT(*) FROM daily_summary WHERE business_date = :d"),
            {"d": row.business_date},
        ).scalar()
        entry = {
            "business_date": row.business_date,
            "orders": row.total,
            "summary": summary,
            "matched": row.matched,
            "tagged": row.tagged,
        }
        if row.matched < row.total:
            entry["reason"] = (
                f"{row.total} 筆中有 {row.total - row.matched} 筆對不到現存訂單，無法判斷"
            )
            skipped.append(entry)
        elif row.tagged < row.total:
            entry["reason"] = f"{row.total} 筆中有 {row.total - row.tagged} 筆不是 TEST"
            skipped.append(entry)
        else:
            deletable.append(entry)
    return deletable, skipped


def survey(conn: Connection) -> dict:
    orders = _test_orders(conn)
    item_count = sum(o["item_count"] for o in orders)
    target_orders, target_summary = _history_on(conn, TARGET_DATE)
    deletable, skipped = _all_test_dates(conn)

    # (a) 刪掉的訂單裡，有些可能已經被結算進歷史資料，而那天不屬於 (b)/(c)。
    # 依照設定，混合日期的歷史資料整天保留（才不會和 daily_summary 的統計對不起來），
    # 所以這些歷史列會留下來，指向已經不存在的訂單。把筆數列出來讓使用者知道。
    keep_dates = {d["business_date"] for d in deletable} | {TARGET_DATE}
    orphans = 0
    if orders:
        orphans = conn.execute(
            text(
                "SELECT COUNT(*) FROM historical_orders "
                "WHERE order_id = ANY(:ids) AND NOT (business_date = ANY(:dates))"
            ),
            {"ids": [o["id"] for o in orders], "dates": list(keep_dates)},
        ).scalar()

    return {
        "orders": orders,
        "order_items": item_count,
        "target_orders": target_orders,
        "target_summary": target_summary,
        "deletable": deletable,
        "skipped": skipped,
        "orphans": orphans,
    }


def report(plan: dict) -> None:
    orders = plan["orders"]
    print(f"【a】任一品項的特殊需求含「{TEST_MARKER}」的訂單（不分大小寫）")
    if not orders:
        print("     （沒有符合的訂單）")
    for o in orders:
        when = o["placed_at"].strftime("%Y-%m-%d %H:%M")
        seat = o["table_number"]
        seat = f"桌 {seat}" if str(seat).isdigit() else str(seat)  # 吧台／站位不加「桌」
        print(f"     #{o['id']:<5} {seat:<6} {when}  {o['status']:<10} {o['items']}")
        print(f"            特殊需求：{o['marks']}")
    print(f"     小計 order_groups {len(orders)} 筆 / order_items {plan['order_items']} 筆")

    print(f"\n【b】{TARGET_DATE} 的歷史資料")
    print(f"     historical_orders {plan['target_orders']} 筆 / "
          f"daily_summary {plan['target_summary']} 筆")

    print("\n【c】其他日期中，當天歷史訂單全部帶 TEST 的")
    if not plan["deletable"]:
        print("     （沒有符合的日期）")
    for d in plan["deletable"]:
        print(f"     {d['business_date']}  historical_orders {d['orders']} 筆 / "
              f"daily_summary {d['summary']} 筆（{d['orders']} 筆訂單全部是 TEST）")
    for d in plan["skipped"]:
        print(f"     略過 {d['business_date']}：{d['reason']}")

    total_hist = plan["target_orders"] + sum(d["orders"] for d in plan["deletable"])
    total_summary = plan["target_summary"] + sum(d["summary"] for d in plan["deletable"])
    print("\n" + "─" * 46)
    print("  將刪除")
    print(f"    order_groups        {len(orders):>4} 筆")
    print(f"    order_items         {plan['order_items']:>4} 筆")
    print(f"    historical_orders   {total_hist:>4} 筆")
    print(f"    daily_summary       {total_summary:>4} 筆")
    print("  不會動到 drinks / ingredients / recipes，也不調整任何庫存數字")
    print("─" * 46)

    if plan["orphans"]:
        print(
            f"\n提醒：有 {plan['orphans']} 筆歷史訂單屬於被刪掉的 TEST 訂單，"
            f"但那幾天還有正常訂單。\n"
            "      依設定整天保留（否則那天的 daily_summary 統計會對不起來），\n"
            "      這些歷史列會留著並指向已不存在的訂單。"
        )
    if plan["orders"]:
        print(
            "\n提醒：刪除訂單不會把當初扣掉的原料加回庫存（依要求不動庫存）。\n"
            "      要校正的話請在經理後台手動調整。"
        )


def purge(conn: Connection, plan: dict) -> dict:
    """實際刪除。呼叫端負責 commit / rollback。"""
    dates = [TARGET_DATE] + [d["business_date"] for d in plan["deletable"]]
    group_ids = [o["id"] for o in plan["orders"]]

    deleted = {"order_items": 0, "order_groups": 0,
               "historical_orders": 0, "daily_summary": 0}

    if group_ids:
        deleted["order_items"] = conn.execute(
            text("DELETE FROM order_items WHERE group_id = ANY(:ids)"),
            {"ids": group_ids},
        ).rowcount
        deleted["order_groups"] = conn.execute(
            text("DELETE FROM order_groups WHERE id = ANY(:ids)"), {"ids": group_ids}
        ).rowcount

    deleted["historical_orders"] = conn.execute(
        text("DELETE FROM historical_orders WHERE business_date = ANY(:dates)"),
        {"dates": dates},
    ).rowcount
    deleted["daily_summary"] = conn.execute(
        text("DELETE FROM daily_summary WHERE business_date = ANY(:dates)"),
        {"dates": dates},
    ).rowcount
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(
        description="清除測試訂單與測試日期的歷史資料（預設只預覽）"
    )
    parser.add_argument(
        "--confirm", action="store_true", help="真的執行刪除，否則只列出會刪什麼"
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("✗ 缺少環境變數 DATABASE_URL", file=sys.stderr)
        print("  cmd:        set DATABASE_URL=postgresql://使用者:密碼@主機:埠/資料庫",
              file=sys.stderr)
        print("  PowerShell: $env:DATABASE_URL = \"postgresql://...\"", file=sys.stderr)
        print("  bash:       export DATABASE_URL=postgresql://...", file=sys.stderr)
        return 1

    _print_connection()
    from backend.database import engine

    conn = engine.connect()
    try:
        plan = survey(conn)
        report(plan)

        if not args.confirm:
            conn.rollback()
            print("\n這是預覽，沒有刪除任何資料。")
            print("確認上面的清單無誤後，重跑一次並加上 --confirm：")
            print("    python cleanup_test_data.py --confirm")
            return 0

        deleted = purge(conn, plan)
        conn.commit()  # 到這裡才真的寫進去；上面任何一步出錯都會整個回復
    except Exception as exc:
        conn.rollback()
        print(f"\n✗ 發生錯誤，已全部回復，資料庫沒有任何變動：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print("\n✓ 已刪除（單一 transaction）")
    for table, n in deleted.items():
        print(f"    {table:<20} {n:>4} 筆")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
