"""學生酒吧點餐與庫存管理系統 — FastAPI 進入點。

啟動方式（在專案根目錄）:
    uvicorn backend.main:app --reload
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.staticfiles import StaticFiles

from backend.database import init_db
from backend.routers import drinks, ingredients, orders, recipes
from backend.ws import manager

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # 啟動時自動建立資料表
    yield


app = FastAPI(title="學生酒吧點餐與庫存管理系統", lifespan=lifespan)

app.include_router(drinks.router)
app.include_router(ingredients.router)
app.include_router(orders.router)
app.include_router(recipes.router)


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
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
