"""開發用示範資料。執行:  python -m backend.seed"""

import sqlite3

from backend.database import DB_PATH, init_db

DRINKS = [
    ("經典調酒 Highball", "調酒", 1),
    ("莫希托 Mojito", "調酒", 1),
    ("生啤酒", "啤酒", 1),
    ("可樂", "無酒精", 1),
]

INGREDIENTS = [
    ("威士忌", "ml", 2000, 500),
    ("蘭姆酒", "ml", 1500, 500),
    ("蘇打水", "ml", 5000, 1000),
    ("薄荷葉", "片", 100, 30),
    ("萊姆", "顆", 20, 5),
    ("生啤", "ml", 10000, 3000),
    ("可樂", "ml", 6000, 2000),
]

# (drink_name, ingredient_name, quantity_needed)
RECIPES = [
    ("經典調酒 Highball", "威士忌", 45),
    ("經典調酒 Highball", "蘇打水", 120),
    ("莫希托 Mojito", "蘭姆酒", 45),
    ("莫希托 Mojito", "蘇打水", 90),
    ("莫希托 Mojito", "薄荷葉", 8),
    ("莫希托 Mojito", "萊姆", 0.5),
    ("生啤酒", "生啤", 400),
    ("可樂", "可樂", 350),
]


def seed() -> None:
    init_db()
    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        conn.executemany(
            "INSERT OR IGNORE INTO drinks (name, category, is_available) VALUES (?, ?, ?)",
            DRINKS,
        )
        conn.executemany(
            "INSERT OR IGNORE INTO ingredients (name, unit, current_stock, reorder_point) "
            "VALUES (?, ?, ?, ?)",
            INGREDIENTS,
        )
        for drink_name, ingredient_name, qty in RECIPES:
            conn.execute(
                """
                INSERT OR IGNORE INTO recipes (drink_id, ingredient_id, quantity_needed)
                SELECT d.id, i.id, ?
                FROM drinks d, ingredients i
                WHERE d.name = ? AND i.name = ?
                """,
                (qty, drink_name, ingredient_name),
            )
        conn.commit()
        print(f"示範資料已寫入 {DB_PATH}")
    finally:
        conn.close()


if __name__ == "__main__":
    seed()
