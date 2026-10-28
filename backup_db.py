#!/usr/bin/env python
"""唯讀備份：把整個資料庫 dump 成一個 .sql 檔，存到 ./backups/。

用法（在專案根目錄，需先設定環境變數 DATABASE_URL）:
    python backup_db.py                    # 備份到 ./backups/
    python backup_db.py --out-dir 路徑     # 換一個資料夾
    python backup_db.py --schema-only      # 只要結構，不含資料
    python backup_db.py --help

這支腳本只是 pg_dump 的包裝，pg_dump 本身只讀不寫，不會改動任何資料。

連線資訊的處理
--------------
連線字串只從環境變數 DATABASE_URL 讀，拆成 PGHOST/PGUSER/PGPASSWORD 等
環境變數交給 pg_dump，**不會出現在命令列參數裡**——不然同一台機器上
任何人 ps 一下就看得到密碼。輸出檔名只有日期時間，不含主機或資料庫名稱，
終端機也不會印出連線字串。

備份檔還原的方式（會覆蓋目標資料庫的內容，請先確認連的是哪一個）:
    psql "目標連線字串" -f backups/backup-20261028-143000.sql
"""

import argparse
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

from sqlalchemy.engine import make_url

DEFAULT_OUT_DIR = "backups"


def _pg_env(url) -> dict:
    """把連線字串拆成 pg_dump 看得懂的環境變數。

    用環境變數而不是 --dbname 參數，密碼才不會留在行程的命令列上。
    """
    env = dict(os.environ)
    env["PGHOST"] = url.host or "localhost"
    env["PGPORT"] = str(url.port or 5432)
    env["PGDATABASE"] = url.database or ""
    if url.username:
        env["PGUSER"] = url.username
    if url.password:
        env["PGPASSWORD"] = url.password
    # 連線字串裡的 sslmode 之類的參數也一併帶過去（Railway 會用到）
    for key, value in (url.query or {}).items():
        if key == "sslmode" and isinstance(value, str):
            env["PGSSLMODE"] = value
    return env


def _scrub(text: str, url) -> str:
    """pg_dump 的錯誤訊息可能帶出主機或使用者，遮掉再印。"""
    for secret in (url.password, url.host, url.username, url.database):
        if secret:
            text = text.replace(str(secret), "***")
    return text


def main() -> int:
    parser = argparse.ArgumentParser(
        description="把整個資料庫備份成一個 .sql 檔（唯讀，不會改動資料庫）",
        epilog="連線字串從環境變數 DATABASE_URL 讀取，不會印出也不會寫進檔案。",
    )
    parser.add_argument(
        "--out-dir",
        default=DEFAULT_OUT_DIR,
        help=f"備份存放資料夾，預設 ./{DEFAULT_OUT_DIR}/（不存在會自動建立）",
    )
    parser.add_argument(
        "--schema-only", action="store_true", help="只備份資料表結構，不含資料"
    )
    args = parser.parse_args()

    raw = os.getenv("DATABASE_URL")
    if not raw:
        print("✗ 缺少環境變數 DATABASE_URL", file=sys.stderr)
        print("  cmd:        set DATABASE_URL=...", file=sys.stderr)
        print('  PowerShell: $env:DATABASE_URL = "..."', file=sys.stderr)
        print("  bash:       export DATABASE_URL=...", file=sys.stderr)
        return 1

    if shutil.which("pg_dump") is None:
        print("✗ 找不到 pg_dump，請先安裝 PostgreSQL 用戶端工具", file=sys.stderr)
        print("  Windows：安裝 PostgreSQL 後把 bin 資料夾加進 PATH", file=sys.stderr)
        print("           例如 C:\\Program Files\\PostgreSQL\\16\\bin", file=sys.stderr)
        print("  macOS：  brew install libpq && brew link --force libpq", file=sys.stderr)
        print("  Linux：  sudo apt install postgresql-client", file=sys.stderr)
        return 1

    url = make_url(raw)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    # 檔名只放日期時間，不含主機或資料庫名稱，萬一被轉傳也不會洩漏連到哪裡
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    suffix = "-schema" if args.schema_only else ""
    target = out_dir / f"backup-{stamp}{suffix}.sql"

    # --no-owner / --no-privileges：還原到別的帳號底下也不會因為角色不存在而失敗
    command = ["pg_dump", "--no-owner", "--no-privileges", "--file", str(target)]
    if args.schema_only:
        command.append("--schema-only")

    print(f"備份中…（{'只有結構' if args.schema_only else '結構與資料'}）")
    result = subprocess.run(
        command, env=_pg_env(url), capture_output=True, text=True
    )
    if result.returncode != 0:
        print("\n✗ 備份失敗：", file=sys.stderr)
        print(_scrub(result.stderr.strip(), url), file=sys.stderr)
        print(
            "\n  版本不符的話（server 比 pg_dump 新），請把本機的 PostgreSQL"
            "\n  用戶端升級到與伺服器相同的大版本。",
            file=sys.stderr,
        )
        if target.exists():
            target.unlink()  # 失敗的半套檔案不要留著誤導人
        return 1

    size_mb = target.stat().st_size / 1024 / 1024
    print(f"✓ 已備份到 {target.resolve()}")
    print(f"  檔案大小 {size_mb:.2f} MB")
    print("\n還原（會覆蓋目標資料庫的內容，請先確認連的是哪一個）：")
    print(f'    psql "目標連線字串" -f {target}')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
