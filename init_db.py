#!/usr/bin/env python
"""部署後手動執行的資料表初始化腳本。

用法（在 Railway 上用 `railway run`，或在本機設好 DATABASE_URL 後直接跑）：

    python init_db.py            # 只建立資料表（已存在則跳過）
    python init_db.py --seed     # 建立資料表並寫入初始測試資料

連線字串一律從環境變數 DATABASE_URL 讀取，腳本本身不含任何密碼。
重複執行是安全的：資料表用 CREATE TABLE IF NOT EXISTS，
示範資料用 ON CONFLICT DO NOTHING。
"""

import argparse
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化資料表")
    parser.add_argument(
        "--seed",
        action="store_true",
        help="順便寫入菜單資料（會先清除既有的飲品／原料／配方）",
    )
    parser.add_argument(
        "--force", action="store_true", help="搭配 --seed：連同既有訂單一起清除"
    )
    args = parser.parse_args()

    try:
        from backend.database import APP_TIMEZONE, engine, init_db
    except RuntimeError as exc:  # 缺 DATABASE_URL
        print(f"✗ {exc}", file=sys.stderr)
        return 1

    # 隱藏密碼後印出連到哪裡，方便確認接上的是正確的資料庫
    url = engine.url
    print(f"資料庫: {url.render_as_string(hide_password=True)}")
    print(f"顯示時區: {APP_TIMEZONE}")

    try:
        init_db()
    except Exception as exc:
        print(f"✗ 建立資料表失敗: {exc}", file=sys.stderr)
        return 1
    print("✓ 資料表已建立（已存在的會跳過）")

    if args.seed:
        from backend.seed_data import SeedAborted, seed

        try:
            seed(force=args.force)
        except SeedAborted as exc:
            print(f"✗ {exc}", file=sys.stderr)
            return 1
        except Exception as exc:
            print(f"✗ 寫入菜單資料失敗: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
