"""WebSocket 連線管理。

所有角色頁面連到 /ws，伺服器在訂單建立 / 狀態變更 /
庫存警示時廣播 JSON 事件，例如:
    {"event": "order_created", "order": {...}}
    {"event": "order_updated", "order": {...}}
    {"event": "stock_alert", "ingredient": {...}}
"""

import logging

from fastapi import WebSocket
from fastapi.encoders import jsonable_encoder

logger = logging.getLogger("uvicorn.error")


class ConnectionManager:
    def __init__(self) -> None:
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket) -> None:
        if ws in self.active:
            self.active.remove(ws)

    # ⚠ 動 broadcast 之前與之後，請跑 python -m pytest tests/test_live_update.py -v
    #   （全部 passed 才算；規則見 CLAUDE.md）。第一次「要按 F5 才看得到新訂單」
    #   就是這裡造成的：送不出去的例外被吞掉，連線卻沒關，前端不知道要重連。
    async def broadcast(self, message: dict) -> None:
        """把事件推播給所有連線中的頁面。"""
        # 訂單裡有 datetime.date（business_date），json.dumps 處理不了，
        # 先讓 FastAPI 的編碼器轉成字串。整批只轉一次，不必每條連線重做。
        message = jsonable_encoder(message)
        for ws in list(self.active):
            try:
                await ws.send_json(message)
            except Exception:
                # 只從 active 移除、不關閉連線的話，前端的 socket 仍然開著，
                # onclose 不會觸發，就不會重連——畫面從此靜止卻沒有任何徵兆。
                # 所以這裡一定要把連線關掉，讓前端走既有的 5 秒重連。
                logger.exception("WebSocket 推播失敗，關閉這條連線")
                self.disconnect(ws)
                try:
                    await ws.close()
                except Exception:
                    # 連線已經斷了就關不起來，這是正常情況，不用再記一次
                    pass


manager = ConnectionManager()
