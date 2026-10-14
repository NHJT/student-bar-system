"""把 ingredients 與 recipes 更新成 seed_data 裡的最新菜單，不動訂單。

用法（在專案根目錄，需先設定 DATABASE_URL）:
    python -m backend.update_recipes --dry-run   # 只印出會做什麼，不寫入
    python -m backend.update_recipes             # 實際更新

和 seed_data 的差別
-------------------
seed_data 是「重置」：清空 drinks／ingredients／recipes，連訂單一起砍。
這支是「更新」：只改原料與配方，drinks、order_groups、order_items、
daily_summary、historical_orders 一律保留，所以營運中也能跑。

可重複執行。單位換算只在原料還是「瓶／罐」時才做一次，
已經是 ml 的原料不會被重複乘 700。
"""

import argparse

from sqlalchemy import text
from sqlalchemy.engine import Connection

from backend.database import engine, init_db
from backend.seed_data import BOTTLE_ML, CAN_ML, INGREDIENTS, MENU

# 原料更名：(舊名, 新名)。目標名稱已存在時會合併（配方改指新的，庫存相加）。
RENAMES = [
    ("蘇打水", "氣泡水"),  # 統一叫氣泡水，Gin Fizz／Highball 自動跟著改
    ("萊姆汁", "酸"),  # 「酸」＝萊姆汁＋檸檬汁混合液，沿用同一條庫存
    ("金巴利", "Campari"),  # 同一支酒，統一用菜單上的寫法
]

# 舊單位 → 一單位等於幾 ml
UNIT_TO_ML = {"瓶": BOTTLE_ML, "罐": CAN_ML}

TARGET = {name: (unit, stock, reorder) for name, unit, stock, reorder in INGREDIENTS}


def _rename_ingredients(conn: Connection) -> list[str]:
    notes = []
    for old, new in RENAMES:
        old_id = conn.execute(
            text("SELECT id FROM ingredients WHERE name = :n"), {"n": old}
        ).scalar()
        if old_id is None:
            continue  # 已經改過了
        new_id = conn.execute(
            text("SELECT id FROM ingredients WHERE name = :n"), {"n": new}
        ).scalar()
        if new_id is None:
            conn.execute(
                text("UPDATE ingredients SET name = :new WHERE id = :id"),
                {"new": new, "id": old_id},
            )
            notes.append(f"原料更名：{old} → {new}")
            continue
        # 兩個名字都在：把舊的配方與庫存併進新的，再刪掉舊的
        conn.execute(
            text(
                "UPDATE recipes SET ingredient_id = :new WHERE ingredient_id = :old "
                "AND drink_id NOT IN (SELECT drink_id FROM recipes "
                "                     WHERE ingredient_id = :new)"
            ),
            {"new": new_id, "old": old_id},
        )
        conn.execute(
            text(
                "UPDATE ingredients SET current_stock = current_stock + "
                "(SELECT current_stock FROM ingredients WHERE id = :old) WHERE id = :new"
            ),
            {"old": old_id, "new": new_id},
        )
        conn.execute(text("DELETE FROM ingredients WHERE id = :id"), {"id": old_id})
        notes.append(f"原料合併：{old} 併入既有的 {new}（庫存相加）")
    return notes


def _convert_units(conn: Connection) -> list[str]:
    """瓶／罐 → ml，庫存與補貨點一起按比例換算。已是 ml 的跳過。"""
    notes = []
    rows = conn.execute(
        text("SELECT id, name, unit, current_stock, reorder_point FROM ingredients")
    ).fetchall()
    for row in rows:
        target_unit = TARGET.get(row.name, (None,))[0]
        factor = UNIT_TO_ML.get(row.unit)
        if target_unit != "ml" or factor is None:
            continue
        conn.execute(
            text(
                "UPDATE ingredients SET unit = 'ml', "
                "current_stock = current_stock * :f, reorder_point = reorder_point * :f "
                "WHERE id = :id"
            ),
            {"f": factor, "id": row.id},
        )
        notes.append(
            f"單位換算：{row.name} {row.current_stock:g} {row.unit} "
            f"→ {row.current_stock * factor:g} ml（1 {row.unit} = {factor} ml）"
        )
    return notes


def _add_missing_ingredients(conn: Connection) -> list[str]:
    notes = []
    for name, unit, stock, reorder in INGREDIENTS:
        inserted = conn.execute(
            text(
                "INSERT INTO ingredients (name, unit, current_stock, reorder_point) "
                "VALUES (:name, :unit, :stock, :reorder) "
                "ON CONFLICT (name) DO NOTHING RETURNING id"
            ),
            {"name": name, "unit": unit, "stock": stock, "reorder": reorder},
        ).scalar()
        if inserted is not None:
            notes.append(f"新增原料：{name}（{stock:g} {unit}，補貨點 {reorder:g}）")
    return notes


def _replace_recipes(conn: Connection) -> tuple[list[str], int]:
    """重寫 MENU 裡每一杯的配方。不在 MENU 的飲品（經理自己加的）不動。"""
    notes = []
    ingredient_ids = dict(
        conn.execute(text("SELECT name, id FROM ingredients")).fetchall()
    )
    written = 0
    for drink_name, _category, _price, items in MENU:
        drink_id = conn.execute(
            text("SELECT id FROM drinks WHERE name = :n"), {"n": drink_name}
        ).scalar()
        if drink_id is None:
            notes.append(f"⚠ 酒單上找不到「{drink_name}」，配方未寫入")
            continue
        missing = [n for n, _q in items if n not in ingredient_ids]
        if missing:
            notes.append(f"⚠ 「{drink_name}」用到未登記的原料 {missing}，配方未寫入")
            continue
        conn.execute(
            text("DELETE FROM recipes WHERE drink_id = :id"), {"id": drink_id}
        )
        conn.execute(
            text(
                "INSERT INTO recipes (drink_id, ingredient_id, quantity_needed) "
                "VALUES (:drink_id, :ingredient_id, :quantity_needed)"
            ),
            [
                {
                    "drink_id": drink_id,
                    "ingredient_id": ingredient_ids[name],
                    "quantity_needed": qty,
                }
                for name, qty in items
            ],
        )
        written += len(items)
    return notes, written


def _unused_ingredients(conn: Connection) -> list[str]:
    rows = conn.execute(
        text(
            "SELECT name FROM ingredients WHERE id NOT IN "
            "(SELECT ingredient_id FROM recipes) ORDER BY name"
        )
    ).fetchall()
    return [row.name for row in rows]


def update(dry_run: bool = False) -> None:
    init_db()
    with engine.connect() as conn:
        notes = _rename_ingredients(conn)
        notes += _convert_units(conn)
        notes += _add_missing_ingredients(conn)
        recipe_notes, written = _replace_recipes(conn)
        notes += recipe_notes
        unused = _unused_ingredients(conn)

        if dry_run:
            conn.rollback()
        else:
            conn.commit()

    for note in notes:
        print(f"  · {note}")
    if not notes:
        print("  · 沒有需要變更的原料")
    print(f"\n配方   {len(MENU)} 杯 / {written} 列")
    if unused:
        # 配方改版後有些原料不再被任何飲品用到，留著不刪，由經理自行判斷
        print(f"目前沒有任何配方用到：{'、'.join(unused)}")
    print("（已回滾，沒有實際寫入）" if dry_run else "✓ 已更新")


def main() -> int:
    parser = argparse.ArgumentParser(description="更新原料與配方（保留訂單）")
    parser.add_argument(
        "--dry-run", action="store_true", help="只印出會做的變更，不寫入資料庫"
    )
    args = parser.parse_args()
    update(dry_run=args.dry_run)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
