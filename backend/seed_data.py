"""初始測試資料：飲品、食材、配方。

執行（在專案根目錄，需先設定 DATABASE_URL）:
    python -m backend.seed_data
    python init_db.py --seed      # 連資料表一起建立

可重複執行：已存在的資料會自動跳過，不會重複新增。
"""

from sqlalchemy import text

from backend.database import engine, init_db

# (name, category)
DRINKS = [
    ("莫希托 Mojito", "調酒"),
    ("琴通寧 Gin Tonic", "調酒"),
    ("威士忌可樂", "調酒"),
    ("可樂", "軟飲"),
    ("柳橙汁", "軟飲"),
]

# (name, unit, current_stock, reorder_point)
INGREDIENTS = [
    ("冰塊", "公斤", 10, 3),
    ("檸檬", "顆", 20, 5),
    ("蘭姆酒", "瓶", 3, 1),
    ("琴酒", "瓶", 3, 1),
    ("威士忌", "瓶", 3, 1),
    ("可樂", "罐", 24, 6),
    ("薄荷", "束", 5, 2),
]

# drink_name: [(ingredient_name, quantity_needed), ...]
RECIPES = {
    "莫希托 Mojito": [("蘭姆酒", 0.05), ("冰塊", 0.1), ("檸檬", 1), ("薄荷", 0.2)],
    "琴通寧 Gin Tonic": [("琴酒", 0.05), ("冰塊", 0.1)],
    "威士忌可樂": [("威士忌", 0.05), ("可樂", 1), ("冰塊", 0.1)],
    "可樂": [("可樂", 1), ("冰塊", 0.1)],
    "柳橙汁": [],  # 不需要食材
}


def seed() -> None:
    init_db()
    with engine.connect() as conn:
        drinks_added = conn.execute(
            text(
                "INSERT INTO drinks (name, category) VALUES (:name, :category) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            [{"name": n, "category": c} for n, c in DRINKS],
        ).rowcount

        ingredients_added = conn.execute(
            text(
                "INSERT INTO ingredients (name, unit, current_stock, reorder_point) "
                "VALUES (:name, :unit, :stock, :reorder) "
                "ON CONFLICT (name) DO NOTHING"
            ),
            [
                {"name": n, "unit": u, "stock": s, "reorder": r}
                for n, u, s, r in INGREDIENTS
            ],
        ).rowcount

        recipes_added = 0
        for drink_name, items in RECIPES.items():
            for ingredient_name, qty in items:
                recipes_added += conn.execute(
                    text(
                        """
                        INSERT INTO recipes (drink_id, ingredient_id, quantity_needed)
                        SELECT d.id, i.id, :qty
                        FROM drinks d, ingredients i
                        WHERE d.name = :drink_name AND i.name = :ingredient_name
                        ON CONFLICT (drink_id, ingredient_id) DO NOTHING
                        """
                    ),
                    {
                        "qty": qty,
                        "drink_name": drink_name,
                        "ingredient_name": ingredient_name,
                    },
                ).rowcount

        conn.commit()

    total_recipe_items = sum(len(v) for v in RECIPES.values())
    print(f"飲品   新增 {drinks_added} 筆（跳過 {len(DRINKS) - drinks_added} 筆已存在）")
    print(
        f"食材   新增 {ingredients_added} 筆"
        f"（跳過 {len(INGREDIENTS) - ingredients_added} 筆已存在）"
    )
    print(
        f"配方   新增 {recipes_added} 筆"
        f"（跳過 {total_recipe_items - recipes_added} 筆已存在）"
    )


if __name__ == "__main__":
    seed()
