#!/usr/bin/env python
"""一次性：依飲品名稱寫入售價（drinks.price）。

用法（在專案根目錄，需先設定環境變數 DATABASE_URL）:
    python set_drink_prices.py             # 預設：只列出找到／找不到，不寫入
    python set_drink_prices.py --execute   # 真的寫入（單一 transaction）

只更新 drinks.price 一個欄位，不新增、不刪除飲品，也不碰其他任何資料表。
已成立訂單的 order_items.unit_price 是下單當下的快照，不受這支腳本影響。
"""

import argparse
import os
import sys

from sqlalchemy import text
from sqlalchemy.engine import Connection

# ---------------------------------------------------------------------------
# 價目表。鍵一律使用「資料庫裡實際的飲品名稱」。
#
# 唯一與指定清單不同的是：指定寫「Bee's Knees」，酒單上是 "Bee's Knee"（單數），
# 經確認是同一杯酒，照酒單上的實際名稱寫。
#
# 「苡銨特調」「無酒精飲料」是後來在經理後台新增的，seed_data.py 的 MENU 裡沒有，
# 但線上資料庫有，所以照名稱寫進表裡。比對是執行當下跟資料庫對，
# 真的找不到會列進「找不到」而不會亂寫。
# ---------------------------------------------------------------------------
PRICES = {
    "Gin Fizz": 180,
    "Gin Tonic": 120,
    "Gimlet": 150,
    "White Lady": 180,
    "Bee's Knee": 180,  # 指定清單寫「Bee's Knees」，酒單上是單數
    "20th Century": 200,
    "Aviation": 200,
    "Clover Club": 200,
    "Negroni": 220,
    "Whiskey Sour": 150,
    "Boston Sour": 180,
    "God Father": 200,
    "Old-Fashioned": 180,
    "Highball": 120,
    "Vodka Lime": 120,
    "Sex on the Beach": 180,
    "Cosmopolitan": 180,
    "Cuba Libre": 150,
    "Daiquiri": 150,
    "X.Y.Z": 180,
    "Tequila Sunrise": 150,
    "Margarita": 180,
    "El Diablo": 200,
    "Long Island Iced Tea": 250,
    "苡銨特調": 250,
    "無酒精飲料": 120,
}


def survey(conn: Connection) -> dict:
    """比對價目表與酒單。純查詢，不改任何東西。"""
    rows = conn.execute(text("SELECT id, name, price FROM drinks")).fetchall()
    by_name = {r.name: r for r in rows}

    found, missing = [], []
    for name, price in PRICES.items():
        row = by_name.get(name)
        if row is None:
            missing.append((name, price))
        else:
            found.append(
                {"id": row.id, "name": name, "old": row.price, "new": price}
            )
    # 酒單上有、但價目表沒給價格的飲品
    no_price = sorted(r.name for r in rows if r.name not in PRICES)
    return {"found": found, "missing": missing, "no_price": no_price}


def report(plan: dict) -> None:
    print(f"【找到】{len(plan['found'])} 款，將寫入售價")
    for item in plan["found"]:
        change = (
            "（新設定）"
            if item["old"] is None
            else ("（不變）" if item["old"] == item["new"] else f"（原 {item['old']}）")
        )
        print(f"     {item['name']:<24} NT$ {item['new']:<5}{change}")

    if plan["missing"]:
        print(f"\n【找不到】{len(plan['missing'])} 款，不會寫入")
        for name, price in plan["missing"]:
            print(f"     {name:<24} NT$ {price}   ← 酒單上沒有這個名稱")
    else:
        print("\n【找不到】無，價目表上每一款都對到了")

    if plan["no_price"]:
        print(f"\n【酒單上有、但價目表沒給價】{len(plan['no_price'])} 款，維持原狀")
        for name in plan["no_price"]:
            print(f"     {name}")


def apply_prices(conn: Connection, plan: dict) -> int:
    """只 UPDATE drinks.price，不碰其他欄位或資料表。"""
    changed = 0
    for item in plan["found"]:
        changed += conn.execute(
            text("UPDATE drinks SET price = :price WHERE id = :id"),
            {"price": item["new"], "id": item["id"]},
        ).rowcount
    return changed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="依名稱寫入飲品售價（預設只列出，不寫入）"
    )
    parser.add_argument(
        "--execute", action="store_true", help="真的寫入售價，否則只列出比對結果"
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("✗ 缺少環境變數 DATABASE_URL", file=sys.stderr)
        print("  cmd:        set DATABASE_URL=...", file=sys.stderr)
        print('  PowerShell: $env:DATABASE_URL = "..."', file=sys.stderr)
        print("  bash:       export DATABASE_URL=...", file=sys.stderr)
        return 1

    from backend.database import engine

    conn = engine.connect()
    try:
        plan = survey(conn)
        report(plan)

        if not args.execute:
            conn.rollback()
            print("\n這是預覽，沒有寫入任何資料。")
            print("確認上面的清單無誤後，重跑一次並加上 --execute：")
            print("    python set_drink_prices.py --execute")
            return 0

        changed = apply_prices(conn, plan)
        conn.commit()  # 到這裡才寫進去；上面任一步出錯都會整個回復
    except Exception as exc:
        conn.rollback()
        print(f"\n✗ 發生錯誤，已全部回復，售價沒有任何變動：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    print(f"\n✓ 已更新 {changed} 款飲品的售價（單一 transaction）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
