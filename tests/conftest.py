"""測試用的資料庫設定。

這些測試需要一個**可以整個清空**的 PostgreSQL 資料庫，用環境變數指定：

    cmd:        set TEST_DATABASE_URL=postgresql://user:pw@localhost:5432/studentbar_test
    PowerShell: $env:TEST_DATABASE_URL = "postgresql://..."
    bash:       export TEST_DATABASE_URL=postgresql://...

    python -m pytest

沒設定就整批跳過，不會去碰 DATABASE_URL 指向的資料庫。

安全鎖：資料庫名稱一定要含有 test。每個測試都會 DROP SCHEMA public CASCADE
再依 backend/schema.sql 重建，誤指到正式資料庫等於全部刪光，所以寧可卡住不跑。
"""

import os
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "backend" / "schema.sql"

_URL = os.getenv("TEST_DATABASE_URL")


def _checked_url() -> str:
    if not _URL:
        pytest.skip("未設定 TEST_DATABASE_URL，略過需要資料庫的測試")
    name = (make_url(_URL).database or "").lower()
    if "test" not in name:
        raise RuntimeError(
            "TEST_DATABASE_URL 的資料庫名稱必須含有 test。"
            "測試會清空整個 schema，這道檢查是為了擋掉誤指正式資料庫。"
        )
    return _URL


@pytest.fixture
def fresh_db():
    """重建一套乾淨的資料表，回傳 backend 正在用的 engine。

    先把 DATABASE_URL 設成測試資料庫再 import backend.database——那個模組是
    在 import 當下就建好 engine 的，所以順序不能顛倒，測試也要等這個 fixture
    跑完才 import backend 的其他東西。

    直接借用 backend 的 engine（而不是自己 create_engine）有兩個好處：
    保證測試和被測程式連的是同一個資料庫，而且連線的 session timezone
    等設定也跟正式環境一致。
    """
    os.environ["DATABASE_URL"] = _checked_url()

    from backend.database import engine

    with engine.connect() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        conn.execute(text(SCHEMA_PATH.read_text(encoding="utf-8")))
        conn.commit()
    return engine
