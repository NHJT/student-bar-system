"""WebSocket 連線管理（第四步實作內容）。

規劃：所有角色頁面連到 /ws，伺服器在訂單建立 / 狀態變更 /
庫存警示時廣播 JSON 事件，例如:
    {"event": "order_created", "order": {...}}
    {"event": "order_updated", "order": {...}}
    {"event": "stock_alert", "ingredient": {...}}
"""

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, message: dict) -> None:
        """把事件推播給所有連線中的頁面。"""
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                self.disconnect(ws)


manager = ConnectionManager()
