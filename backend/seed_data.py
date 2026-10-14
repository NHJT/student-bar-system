"""初始資料：酒吧真實菜單（飲品、原料、配方）。

執行（在專案根目錄，需先設定 DATABASE_URL）:
    python -m backend.seed_data           # 清除舊的飲品／原料／配方後寫入
    python -m backend.seed_data --force   # 連同既有訂單一起清除
    python init_db.py --seed              # 連資料表一起建立

⚠️ 這個腳本是「重置」而不是「補資料」：它會先清空 recipes、drinks、
   ingredients 再寫入。因為 order_items 參照 drinks，資料庫裡若已經有訂單，
   必須加 --force 一併清除，否則腳本會中止以免誤刪營業資料。

   已經在營運中、只是要更新配方／原料的話，不要跑這支腳本，
   改用 `python -m backend.update_recipes`：它只動 ingredients 與 recipes，
   訂單與歷史紀錄都會保留。

單位政策
--------
液體一律以 **ml** 記錄用量與庫存，不換算成瓶數或罐數。
進貨時用下面兩個換算把瓶／罐換成 ml 填進庫存即可。
"""

import argparse

from sqlalchemy import text

from backend.database import engine, init_db

# 進貨換算（只用來把庫存數字從瓶／罐換成 ml，用量單位本身保持 ml）
BOTTLE_ML = 700  # 一瓶基酒／利口酒／果汁／糖漿
CAN_ML = 330  # 一罐通寧水／氣泡水／可樂／薑汁汽水

# ---------------------------------------------------------------------------
# 原料：(名稱, 單位, 初始庫存, 補貨點)
#
# 補貨點依使用頻率抓：常用的基酒／酸留多一點，只出現在一兩杯的利口酒留少一點。
# 液體的庫存＝瓶數 × 700 或罐數 × 330，換算後單位是 ml。
# ---------------------------------------------------------------------------
INGREDIENTS = [
    # 基酒（6 瓶 / 補貨點 2 瓶；用量少的 3 瓶 / 1 瓶）
    ("琴酒", "ml", 6 * BOTTLE_ML, 2 * BOTTLE_ML),
    ("伏特加", "ml", 6 * BOTTLE_ML, 2 * BOTTLE_ML),
    ("蘭姆酒", "ml", 6 * BOTTLE_ML, 2 * BOTTLE_ML),
    ("龍舌蘭酒", "ml", 6 * BOTTLE_ML, 2 * BOTTLE_ML),
    ("波本威士忌", "ml", 6 * BOTTLE_ML, 2 * BOTTLE_ML),
    ("蘇格蘭威士忌", "ml", 3 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("燒酒", "ml", 3 * BOTTLE_ML, 1 * BOTTLE_ML),
    # 利口酒
    ("橙酒", "ml", 3 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("君度橙酒", "ml", 4 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("黑櫻桃酒", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("紫羅蘭酒", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("白可可利口酒", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("Lillet", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("Campari", "ml", 3 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("甜苦艾酒", "ml", 3 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("水蜜桃利口酒", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("杏仁利口酒", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("黑醋栗利口酒", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    # 糖漿、調味
    ("覆盆子糖漿", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("紅石榴糖漿", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("糖漿", "ml", 6 * BOTTLE_ML, 2 * BOTTLE_ML),
    ("蜂蜜", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("苦精", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),  # Angostura
    ("橘子苦精", "ml", 2 * BOTTLE_ML, 1 * BOTTLE_ML),  # Orange Bitters
    # 果汁（「酸」＝萊姆汁＋檸檬汁的混合液，幾乎每杯都用到，留最多）
    ("酸", "ml", 10 * BOTTLE_ML, 3 * BOTTLE_ML),
    ("檸檬汁", "ml", 3 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("蔓越莓汁", "ml", 4 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("柳橙汁", "ml", 4 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("葡萄汁", "ml", 4 * BOTTLE_ML, 1 * BOTTLE_ML),
    # 汽水（24 罐 / 補貨點 6 罐）
    ("通寧水", "ml", 24 * CAN_ML, 6 * CAN_ML),
    ("氣泡水", "ml", 24 * CAN_ML, 6 * CAN_ML),
    ("薑汁汽水", "ml", 12 * CAN_ML, 4 * CAN_ML),
    ("可樂", "ml", 24 * CAN_ML, 6 * CAN_ML),
    # 生鮮、其他（這些不是液體，維持原本的單位）
    ("薄荷", "束", 5, 2),
    ("紫蘇", "束", 5, 2),
    ("檸檬", "顆", 30, 10),
    ("蛋白", "顆", 24, 8),
    ("冬瓜露", "ml", 3 * BOTTLE_ML, 1 * BOTTLE_ML),
    ("奶蓋粉", "包", 5, 2),
    ("冰塊", "公斤", 30, 10),
]

# ---------------------------------------------------------------------------
# 菜單：(飲品名稱, 分類, 售價, [(原料, 每杯用量), ...])
#
# 用量單位是該原料的 unit（液體皆為 ml）。以下配方為吧台提供的實際數字。
# 售價目前沒有寫進資料庫（drinks 表沒有 price 欄位），先保留在這裡，
# 之後要加價格／營收功能時可直接使用。
#
# 原料名稱對照（吧台寫法 → 系統原料）：
#   金龍舌蘭／龍舌蘭 → 龍舌蘭酒      君度 → 君度橙酒
#   黑櫻桃利口酒     → 黑櫻桃酒      紫羅蘭利口酒 → 紫羅蘭酒
#   Highball 的「威士忌」→ 波本威士忌（要改用蘇格蘭在後台換掉即可）
#
# 標成 TODO 的四杯還沒拿到實際配方，數字是先前的假設值換算成 ml
# （基酒 35、糖漿／利口酒 14、酸 35、果汁 70），拿到配方後再更新。
# ---------------------------------------------------------------------------
MENU = [
    # ---- GIN BASE ----
    ("Gin Fizz", "GIN BASE", 180,
     [("琴酒", 50), ("糖漿", 15), ("酸", 15), ("氣泡水", 60)]),
    ("Gin Tonic", "GIN BASE", 120,
     [("琴酒", 60), ("通寧水", 95)]),
    ("Gimlet", "GIN BASE", 150,
     [("琴酒", 45), ("糖漿", 11), ("酸", 15)]),
    ("White Lady", "GIN BASE", 180,
     [("琴酒", 50), ("君度橙酒", 20), ("酸", 15)]),
    ("Bee's Knee", "GIN BASE", 180,
     [("琴酒", 45), ("檸檬汁", 15), ("蜂蜜", 10)]),
    ("20th Century", "GIN BASE", 200,
     [("琴酒", 45), ("白可可利口酒", 15), ("Lillet", 20), ("酸", 20)]),
    ("Aviation", "GIN BASE", 200,
     [("琴酒", 50), ("黑櫻桃酒", 12), ("紫羅蘭酒", 7), ("酸", 17)]),
    ("Clover Club", "GIN BASE", 200,  # TODO 尚未取得實際配方
     [("琴酒", 35), ("酸", 35), ("覆盆子糖漿", 14), ("蛋白", 1), ("冰塊", 0.1)]),
    ("Negroni", "GIN BASE", 220,
     [("琴酒", 45), ("Campari", 17), ("甜苦艾酒", 23)]),

    # ---- WHISKEY BASE ----
    ("Whiskey Sour", "WHISKEY BASE", 150,
     [("波本威士忌", 60), ("糖漿", 10), ("酸", 15)]),
    ("Boston Sour", "WHISKEY BASE", 180,  # TODO 尚未取得實際配方
     [("波本威士忌", 35), ("糖漿", 14), ("酸", 35), ("蛋白", 1), ("冰塊", 0.1)]),
    ("God Father", "WHISKEY BASE", 200,
     [("蘇格蘭威士忌", 50), ("杏仁利口酒", 15)]),
    # 苦精 7 dash ≒ 2 ml、橘子苦精 3 drop ≒ 1 ml
    ("Old-Fashioned", "WHISKEY BASE", 180,
     [("波本威士忌", 60), ("糖漿", 10), ("苦精", 2), ("橘子苦精", 1)]),
    ("Highball", "WHISKEY BASE", 120,
     [("波本威士忌", 60), ("氣泡水", 60)]),

    # ---- VODKA BASE ----
    ("Vodka Lime", "VODKA BASE", 120,
     [("伏特加", 45), ("酸", 16), ("糖漿", 10)]),
    ("Sex on the Beach", "VODKA BASE", 180,
     [("伏特加", 45), ("水蜜桃利口酒", 10), ("蔓越莓汁", 40), ("柳橙汁", 40)]),
    ("Cosmopolitan", "VODKA BASE", 180,
     [("伏特加", 45), ("君度橙酒", 15), ("酸", 20), ("蔓越莓汁", 40)]),

    # ---- RUM BASE ----
    ("Cuba Libre", "RUM BASE", 150,
     [("蘭姆酒", 60), ("檸檬汁", 15), ("可樂", 120)]),
    ("Daiquiri", "RUM BASE", 150,
     [("蘭姆酒", 45), ("糖漿", 12), ("酸", 15)]),
    ("X.Y.Z", "RUM BASE", 180,
     [("蘭姆酒", 50), ("君度橙酒", 20), ("酸", 15)]),

    # ---- TEQUILA BASE ----
    ("Tequila Sunrise", "TEQUILA BASE", 150,
     [("龍舌蘭酒", 45), ("柳橙汁", 90), ("紅石榴糖漿", 15)]),
    ("Margarita", "TEQUILA BASE", 180,
     [("龍舌蘭酒", 43), ("君度橙酒", 20), ("酸", 16)]),
    ("El Diablo", "TEQUILA BASE", 200,
     [("龍舌蘭酒", 40), ("黑醋栗利口酒", 15), ("酸", 15), ("薑汁汽水", 60)]),

    # ---- OTHERS ----
    ("Long Island Iced Tea", "OTHERS", 250,
     [("伏特加", 15), ("琴酒", 15), ("蘭姆酒", 15), ("龍舌蘭酒", 10),
      ("君度橙酒", 10), ("酸", 15), ("可樂", 80)]),

    # ---- SIGNATURE（本週特調，之後會定期更換）----
    ("雨後夏夜 Summer Night After Rain", "SIGNATURE", 250,  # TODO 尚未取得實際配方
     [("燒酒", 35), ("葡萄汁", 70), ("薄荷", 0.2), ("紫蘇", 0.2),
      ("酸", 35), ("冰塊", 0.1)]),
    ("冬瓜奶蓋 Whitemelon", "SIGNATURE", 250,  # TODO 尚未取得實際配方
     [("波本威士忌", 35), ("冬瓜露", 70), ("奶蓋粉", 0.1), ("冰塊", 0.1)]),
]


class SeedAborted(RuntimeError):
    """資料庫已有訂單、又沒有指定 --force 時中止。"""


def seed(force: bool = False) -> None:
    init_db()
    with engine.connect() as conn:
        order_count = conn.execute(text("SELECT COUNT(*) FROM order_groups")).scalar()
        if order_count and not force:
            raise SeedAborted(
                f"資料庫裡已經有 {order_count} 筆訂單。\n"
                "重置菜單必須連訂單一起清除（order_items 參照 drinks），\n"
                "確定要清掉這些訂單的話請加上 --force。\n"
                "只是要更新配方／原料的話請改用：python -m backend.update_recipes"
            )

        # 清除順序要顧及外鍵：recipes → order_items → order_groups → drinks / ingredients
        conn.execute(text("DELETE FROM recipes"))
        if order_count:
            conn.execute(text("DELETE FROM order_items"))
            conn.execute(text("DELETE FROM order_groups"))
            conn.execute(text("ALTER SEQUENCE order_groups_id_seq RESTART WITH 1"))
            conn.execute(text("ALTER SEQUENCE order_items_id_seq RESTART WITH 1"))
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
