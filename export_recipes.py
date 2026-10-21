#!/usr/bin/env python
"""唯讀匯出：把酒單、酒譜、原料匯成三個 CSV，給 Excel 直接開。

用法（在專案根目錄，需先設定環境變數 DATABASE_URL）:
    python export_recipes.py                  # 匯出到 ./exports/
    python export_recipes.py --out-dir 路徑   # 換一個輸出資料夾
    python export_recipes.py --help

輸出（UTF-8 with BOM，Excel 打開不會變亂碼）:
    ingredients.csv            原料表
    recipes.csv                酒譜，一列 = 一支飲品的一個原料
    drinks_without_recipe.csv  酒單上有、但還沒建任何酒譜的飲品

這支腳本只有 SELECT：沒有 INSERT／UPDATE／DELETE，也沒有任何 DDL。
連線前還會把交易設成 READ ONLY，就算以後有人改壞了，資料庫也會擋下寫入。

連線字串只從環境變數 DATABASE_URL 讀，不印出來也不寫進任何檔案。
匯出的內容只有酒單／酒譜／原料，不含訂單、客人、員工或任何人名。
"""

import argparse
import csv
import os
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.engine import Connection

DEFAULT_OUT_DIR = "exports"

# 欄位以 backend/schema.sql 的實際欄位為準，沒有的欄位就不會出現在這裡：
# ingredients 沒有「類別」，也沒有「是否啟用」——後者是 drinks.is_available。
_INGREDIENTS = """
    SELECT id, name, unit, current_stock, reorder_point, is_unlimited
    FROM ingredients
    ORDER BY name
"""
_INGREDIENTS_HEADER = [
    "原料id", "原料名稱", "單位", "目前庫存", "補貨點", "無限制供應",
]

# LEFT JOIN：沒有酒譜的飲品也要出現，原料相關欄位留空
_RECIPES = """
    SELECT d.id AS drink_id, d.name AS drink_name, d.category, d.price,
           i.name AS ingredient_name, r.quantity_needed, i.unit, r.note
    FROM drinks d
    LEFT JOIN recipes r ON r.drink_id = d.id
    LEFT JOIN ingredients i ON i.id = r.ingredient_id
    ORDER BY d.category, d.name, i.name
"""
_RECIPES_HEADER = [
    "飲品id", "飲品名稱", "類別", "價格",
    "原料名稱", "每杯用量", "單位", "用量備註",
]

_NO_RECIPE = """
    SELECT d.id, d.name, d.category, d.is_available
    FROM drinks d
    WHERE NOT EXISTS (SELECT 1 FROM recipes r WHERE r.drink_id = d.id)
    ORDER BY d.category, d.name
"""
_NO_RECIPE_HEADER = ["飲品id", "飲品名稱", "類別", "目前販售中"]


def _cell(value):
    """布林值寫成看得懂的字，None 留空白格。"""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    return value


def write_csv(path: Path, header: list[str], rows) -> int:
    """寫一個 CSV，回傳資料列數（不含標題列）。

    encoding 用 utf-8-sig：開頭多一個 BOM，Excel 才認得出是 UTF-8。
    newline="" 是 csv 模組的要求，少了它在 Windows 會多出空白列。
    """
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(header)
        for row in rows:
            writer.writerow([_cell(v) for v in row])
            count += 1
    return count


def export(conn: Connection, out_dir: Path) -> dict:
    # 這一句要是交易裡的第一個指令，之後任何寫入都會被資料庫拒絕
    conn.execute(text("SET TRANSACTION READ ONLY"))

    counts = {}
    counts["ingredients.csv"] = write_csv(
        out_dir / "ingredients.csv",
        _INGREDIENTS_HEADER,
        conn.execute(text(_INGREDIENTS)).fetchall(),
    )
    counts["recipes.csv"] = write_csv(
        out_dir / "recipes.csv",
        _RECIPES_HEADER,
        conn.execute(text(_RECIPES)).fetchall(),
    )
    counts["drinks_without_recipe.csv"] = write_csv(
        out_dir / "drinks_without_recipe.csv",
        _NO_RECIPE_HEADER,
        conn.execute(text(_NO_RECIPE)).fetchall(),
    )

    counts["_drinks"] = conn.execute(text("SELECT COUNT(*) FROM drinks")).scalar()
    counts["_with_recipe"] = conn.execute(
        text("SELECT COUNT(DISTINCT drink_id) FROM recipes")
    ).scalar()
    counts["_ingredients"] = conn.execute(
        text("SELECT COUNT(*) FROM ingredients")
    ).scalar()
    return counts


def main() -> int:
    parser = argparse.ArgumentParser(
        description="唯讀匯出酒單／酒譜／原料成三個 CSV（UTF-8 with BOM，Excel 可直接開）",
        epilog="只做 SELECT，不會修改資料庫。連線字串從環境變數 DATABASE_URL 讀取。",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"輸出資料夾，預設 ./{DEFAULT_OUT_DIR}/（不存在會自動建立）",
    )
    args = parser.parse_args()

    if not os.getenv("DATABASE_URL"):
        print("✗ 缺少環境變數 DATABASE_URL", file=sys.stderr)
        print("  cmd:        set DATABASE_URL=...", file=sys.stderr)
        print('  PowerShell: $env:DATABASE_URL = "..."', file=sys.stderr)
        print("  bash:       export DATABASE_URL=...", file=sys.stderr)
        return 1

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    from backend.database import engine  # 這一行才會建立連線池

    conn = engine.connect()
    try:
        counts = export(conn, out_dir)
    except Exception as exc:
        print(f"\n✗ 匯出失敗：{exc}", file=sys.stderr)
        return 1
    finally:
        conn.rollback()  # 唯讀交易，收尾一律回復，不留任何痕跡
        conn.close()

    print(f"✓ 已匯出到 {out_dir.resolve()}")
    print()
    for name in ("ingredients.csv", "recipes.csv", "drinks_without_recipe.csv"):
        print(f"  {name:<28}{counts[name]:>5} 列")
    print()
    print(f"  飲品總數      {counts['_drinks']:>5}")
    print(f"  有酒譜的飲品  {counts['_with_recipe']:>5}")
    print(f"  原料總數      {counts['_ingredients']:>5}")
    missing = counts["_drinks"] - counts["_with_recipe"]
    if missing:
        print(f"\n  其中 {missing} 支飲品還沒有酒譜，清單見 drinks_without_recipe.csv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
