"""初始資料：酒吧真實菜單（飲品、原料、配方）。

執行（在專案根目錄，需先設定 DATABASE_URL）:
    python -m backend.seed_data           # 清除舊的飲品／原料／配方後寫入
    python -m backend.seed_data --force   # 連同既有訂單一起清除
    python init_db.py --seed              # 連資料表一起建立

⚠️ 這個腳本是「重置」而不是「補資料」：它會先清空 recipes、drinks、
   ingredients 再寫入。因為 orders 參照 drinks，資料庫裡若已經有訂單，
   必須加 --force 一併清除，否則腳本會中止以免誤刪營業資料。
"""

import argparse

from sqlalchemy import text

from backend.database import engine, init_db

# ---------------------------------------------------------------------------
# 每杯用量的預設值（單位見原料表）。這些只是起始值，經理可在後台逐項調整。
# ---------------------------------------------------------------------------
BASE = 0.05  # 基酒（瓶）
LIQUEUR = 0.02  # 利口酒、香甜酒（瓶）
SYRUP = 0.02  # 糖漿、蜂蜜（瓶）
BITTERS = 0.005  # 苦精（瓶），一次只用幾 dash
LIME = 0.05  # 萊姆汁（瓶），調味用量，和基酒同級
JUICE = 0.1  # 果汁（瓶），當主體的柳橙／蔓越莓／葡萄汁
SODA = 1  # 汽水（罐）
HERB = 0.2  # 薄荷、紫蘇（束）
LEMON = 0.2  # 檸檬（顆），切角裝飾
EGG_WHITE = 1  # 蛋白（顆）
WINTERMELON = 0.1  # 冬瓜露（瓶）
MILK_CAP = 0.1  # 奶蓋粉（包）
ICE = 0.1  # 冰塊（公斤）

# ---------------------------------------------------------------------------
# 原料：(名稱, 單位, 初始庫存, 補貨點)
# 補貨點依使用頻率抓：常用的基酒／萊姆汁留多一點，只出現在一兩杯的利口酒留少一點。
# ---------------------------------------------------------------------------
INGREDIENTS = [
    # 基酒
    ("琴酒", "瓶", 6, 2),
    ("伏特加", "瓶", 6, 2),
    ("蘭姆酒", "瓶", 6, 2),
    ("龍舌蘭酒", "瓶", 6, 2),
    ("波本威士忌", "瓶", 6, 2),
    ("蘇格蘭威士忌", "瓶", 3, 1),
    ("燒酒", "瓶", 3, 1),
    # 利口酒
    ("橙酒", "瓶", 3, 1),
    ("君度橙酒", "瓶", 4, 1),
    ("黑櫻桃酒", "瓶", 2, 1),
    ("紫羅蘭酒", "瓶", 2, 1),
    ("白可可利口酒", "瓶", 2, 1),
    ("Lillet", "瓶", 2, 1),
    ("金巴利", "瓶", 3, 1),
    ("甜苦艾酒", "瓶", 3, 1),
    ("水蜜桃利口酒", "瓶", 2, 1),
    ("杏仁利口酒", "瓶", 2, 1),
    ("黑醋栗利口酒", "瓶", 2, 1),
    # 糖漿、調味
    ("覆盆子糖漿", "瓶", 2, 1),
    ("紅石榴糖漿", "瓶", 2, 1),
    ("糖漿", "瓶", 6, 2),
    ("蜂蜜", "瓶", 2, 1),
    ("苦精", "瓶", 2, 1),
    # 果汁
    ("萊姆汁", "瓶", 10, 3),
    ("蔓越莓汁", "瓶", 4, 1),
    ("柳橙汁", "瓶", 4, 1),
    ("葡萄汁", "瓶", 4, 1),
    # 汽水
    ("通寧水", "罐", 24, 6),
    ("蘇打水", "罐", 24, 6),
    ("薑汁汽水", "罐", 12, 4),
    ("可樂", "罐", 24, 6),
    # 生鮮、其他
    ("薄荷", "束", 5, 2),
    ("紫蘇", "束", 5, 2),
    ("檸檬", "顆", 30, 10),
    ("蛋白", "顆", 24, 8),
    ("冬瓜露", "瓶", 3, 1),
    ("奶蓋粉", "包", 5, 2),
    ("冰塊", "公斤", 30, 10),
]

# ---------------------------------------------------------------------------
# 菜單：(飲品名稱, 分類, 售價, [(原料, 每杯用量), ...])
#
# 售價目前沒有寫進資料庫（drinks 表沒有 price 欄位），先保留在這裡，
# 之後要加價格／營收功能時可直接使用。
# ---------------------------------------------------------------------------
MENU = [
    # ---- GIN BASE ----
    ("Gin Fizz", "GIN BASE", 180,
     [("琴酒", BASE), ("萊姆汁", LIME), ("糖漿", SYRUP), ("蘇打水", SODA), ("冰塊", ICE)]),
    ("Gin Tonic", "GIN BASE", 120,
     [("琴酒", BASE), ("通寧水", SODA), ("檸檬", LEMON), ("冰塊", ICE)]),
    ("Gimlet", "GIN BASE", 150,
     [("琴酒", BASE), ("萊姆汁", LIME), ("糖漿", SYRUP), ("冰塊", ICE)]),
    ("White Lady", "GIN BASE", 180,
     [("琴酒", BASE), ("橙酒", LIQUEUR), ("萊姆汁", LIME), ("冰塊", ICE)]),
    ("Bee's Knee", "GIN BASE", 180,
     [("琴酒", BASE), ("萊姆汁", LIME), ("蜂蜜", SYRUP), ("冰塊", ICE)]),
    ("20th Century", "GIN BASE", 200,
     [("琴酒", BASE), ("白可可利口酒", LIQUEUR), ("Lillet", LIQUEUR),
      ("萊姆汁", LIME), ("冰塊", ICE)]),
    ("Aviation", "GIN BASE", 200,
     [("琴酒", BASE), ("黑櫻桃酒", LIQUEUR), ("萊姆汁", LIME),
      ("紫羅蘭酒", LIQUEUR), ("冰塊", ICE)]),
    ("Clover Club", "GIN BASE", 200,
     [("琴酒", BASE), ("萊姆汁", LIME), ("覆盆子糖漿", SYRUP),
      ("蛋白", EGG_WHITE), ("冰塊", ICE)]),
    ("Negroni", "GIN BASE", 220,
     [("琴酒", BASE), ("金巴利", LIQUEUR), ("甜苦艾酒", LIQUEUR), ("冰塊", ICE)]),

    # ---- WHISKEY BASE ----
    ("Whiskey Sour", "WHISKEY BASE", 150,
     [("波本威士忌", BASE), ("糖漿", SYRUP), ("萊姆汁", LIME), ("冰塊", ICE)]),
    ("Boston Sour", "WHISKEY BASE", 180,
     [("波本威士忌", BASE), ("糖漿", SYRUP), ("萊姆汁", LIME),
      ("蛋白", EGG_WHITE), ("冰塊", ICE)]),
    ("God Father", "WHISKEY BASE", 200,
     [("蘇格蘭威士忌", BASE), ("杏仁利口酒", LIQUEUR), ("冰塊", ICE)]),
    ("Old-Fashioned", "WHISKEY BASE", 180,
     [("波本威士忌", BASE), ("糖漿", SYRUP), ("苦精", BITTERS), ("冰塊", ICE)]),
    # 菜單只寫「威士忌」，這裡先對應到波本；要改用蘇格蘭在後台換掉即可
    ("Highball", "WHISKEY BASE", 120,
     [("波本威士忌", BASE), ("蘇打水", SODA), ("冰塊", ICE)]),

    # ---- VODKA BASE ----
    ("Vodka Lime", "VODKA BASE", 120,
     [("伏特加", BASE), ("萊姆汁", LIME), ("糖漿", SYRUP), ("冰塊", ICE)]),
    ("Sex on the Beach", "VODKA BASE", 180,
     [("伏特加", BASE), ("水蜜桃利口酒", LIQUEUR), ("萊姆汁", LIME),
      ("蔓越莓汁", JUICE), ("冰塊", ICE)]),
    ("Cosmopolitan", "VODKA BASE", 180,
     [("伏特加", BASE), ("君度橙酒", LIQUEUR), ("萊姆汁", LIME),
      ("蔓越莓汁", JUICE), ("冰塊", ICE)]),

    # ---- RUM BASE ----
    ("Cuba Libre", "RUM BASE", 150,
     [("蘭姆酒", BASE), ("可樂", SODA), ("萊姆汁", LIME), ("冰塊", ICE)]),
    ("Daiquiri", "RUM BASE", 150,
     [("蘭姆酒", BASE), ("萊姆汁", LIME), ("糖漿", SYRUP), ("冰塊", ICE)]),
    ("X.Y.Z", "RUM BASE", 180,
     [("蘭姆酒", BASE), ("君度橙酒", LIQUEUR), ("萊姆汁", LIME), ("冰塊", ICE)]),

    # ---- TEQUILA BASE ----
    ("Tequila Sunrise", "TEQUILA BASE", 150,
     [("龍舌蘭酒", BASE), ("柳橙汁", JUICE), ("紅石榴糖漿", SYRUP), ("冰塊", ICE)]),
    ("Margarita", "TEQUILA BASE", 180,
     [("龍舌蘭酒", BASE), ("君度橙酒", LIQUEUR), ("萊姆汁", LIME), ("冰塊", ICE)]),
    ("El Diablo", "TEQUILA BASE", 200,
     [("龍舌蘭酒", BASE), ("黑醋栗利口酒", LIQUEUR), ("萊姆汁", LIME),
      ("薑汁汽水", SODA), ("冰塊", ICE)]),

    # ---- OTHERS ----
    ("Long Island Iced Tea", "OTHERS", 250,
     [("琴酒", BASE), ("伏特加", BASE), ("蘭姆酒", BASE), ("龍舌蘭酒", BASE),
      ("君度橙酒", LIQUEUR), ("萊姆汁", LIME), ("可樂", SODA), ("冰塊", ICE)]),

    # ---- SIGNATURE（本週特調，之後會定期更換）----
    ("雨後夏夜 Summer Night After Rain", "SIGNATURE", 250,
     [("燒酒", BASE), ("葡萄汁", JUICE), ("薄荷", HERB), ("紫蘇", HERB),
      ("萊姆汁", LIME), ("冰塊", ICE)]),
    ("冬瓜奶蓋 Whitemelon", "SIGNATURE", 250,
     [("波本威士忌", BASE), ("冬瓜露", WINTERMELON), ("奶蓋粉", MILK_CAP),
      ("冰塊", ICE)]),
]


class SeedAborted(RuntimeError):
    """資料庫已有訂單、又沒有指定 --force 時中止。"""


def seed(force: bool = False) -> None:
    init_db()
    with engine.connect() as conn:
        order_count = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar()
        if order_count and not force:
            raise SeedAborted(
                f"資料庫裡已經有 {order_count} 筆訂單。\n"
                "重置菜單必須連訂單一起清除（orders 參照 drinks），\n"
                "確定要清掉這些訂單的話請加上 --force。"
            )

        # 清除順序要顧及外鍵：recipes → orders → drinks / ingredients
        conn.execute(text("DELETE FROM recipes"))
        if order_count:
            conn.execute(text("DELETE FROM orders"))
            conn.execute(text("ALTER SEQUENCE orders_id_seq RESTART WITH 1"))
        conn.execute(text("DELETE FROM drinks"))
        conn.execute(text("DELETE FROM ingredients"))
        conn.execute(text("ALTER SEQUENCE drinks_id_seq RESTART WITH 1"))
        conn.execute(text("ALTER SEQUENCE ingredients_id_seq RESTART WITH 1"))

        ingredient_ids = {}
        for name, unit, stock, reorder in INGREDIENTS:
            ingredient_ids[name] = conn.execute(
                text(
                    "INSERT INTO ingredients (name, unit, current_stock, reorder_point) "
                    "VALUES (:name, :unit, :stock, :reorder) RETURNING id"
                ),
                {"name": name, "unit": unit, "stock": stock, "reorder": reorder},
            ).scalar_one()

        recipe_rows = []
        for drink_name, category, _price, items in MENU:
            drink_id = conn.execute(
                text(
                    "INSERT INTO drinks (name, category) "
                    "VALUES (:name, :category) RETURNING id"
                ),
                {"name": drink_name, "category": category},
            ).scalar_one()
            for ingredient_name, qty in items:
                if ingredient_name not in ingredient_ids:
                    raise SeedAborted(
                        f"「{drink_name}」的配方用到未登記的原料：{ingredient_name}"
                    )
                recipe_rows.append(
                    {
                        "drink_id": drink_id,
                        "ingredient_id": ingredient_ids[ingredient_name],
                        "quantity_needed": qty,
                    }
                )

        conn.execute(
            text(
                "INSERT INTO recipes (drink_id, ingredient_id, quantity_needed) "
                "VALUES (:drink_id, :ingredient_id, :quantity_needed)"
            ),
            recipe_rows,
        )
        conn.commit()

    if order_count:
        print(f"已清除既有的 {order_count} 筆訂單")
    print(f"飲品   {len(MENU)} 筆")
    print(f"原料   {len(INGREDIENTS)} 筆")
    print(f"配方   {len(recipe_rows)} 筆")


def main() -> int:
    parser = argparse.ArgumentParser(description="重置並寫入酒吧真實菜單")
    parser.add_argument(
        "--force", action="store_true", help="連同既有訂單一起清除"
    )
    args = parser.parse_args()
    try:
        seed(force=args.force)
    except SeedAborted as exc:
        print(f"✗ {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
