"""學生酒吧點餐與庫存管理系統 — FastAPI 進入點。

啟動方式（在專案根目錄，需先設定 DATABASE_URL）:
    uvicorn backend.main:app --reload

資料表不會在啟動時自動建立，第一次部署請手動執行:
    python init_db.py --seed
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from backend.database import engine
from backend.routers import drinks, ingredients, orders, recipes, stats
from backend.ws import manager

logger = logging.getLogger("uvicorn.error")

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


class NoCacheStaticFiles(StaticFiles):
    """開發用：要求瀏覽器每次重新驗證 JS/CSS/HTML，
    避免改了前端檔案卻因快取而看不到更新。"""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(app: FastAPI):
    """啟動時只檢查連線與資料表是否就緒，不自動建表（交給 init_db.py）。"""
    try:
        with engine.connect() as conn:
            ready = conn.execute(text("SELECT to_regclass('public.orders')")).scalar()
        if ready:
            logger.info("資料庫連線正常，資料表已就緒")
        else:
            logger.warning("資料庫連線正常，但找不到資料表，請執行: python init_db.py --seed")
    except Exception as exc:
        logger.error("無法連線資料庫: %s", exc)
    yield


app = FastAPI(title="學生酒吧點餐與庫存管理系統", lifespan=lifespan)

app.include_router(drinks.router)
app.include_router(ingredients.router)
app.include_router(orders.router)
app.include_router(recipes.router)
app.include_router(stats.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    """即時更新通道（第四步實作廣播邏輯）。"""
    await manager.connect(ws)
    try:
        while True:
            # 目前只維持連線；訊息一律由伺服器端主動廣播
            await ws.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(ws)


# 前端頁面: /waiter.html、/bar.html、/manager.html
app.mount("/", NoCacheStaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
