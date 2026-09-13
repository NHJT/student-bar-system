"""每日結算排程。

每天 23:59（APP_TIMEZONE 當地時間）自動跑一次結算。
railway.toml 固定單一副本，所以只會有一個排程器在跑；
就算日後開多副本，結算是以 business_date 覆寫的，重複執行也不會產生重複資料。
"""

import asyncio
import logging
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from backend import summary
from backend.database import APP_TIMEZONE
from backend.ws import manager

logger = logging.getLogger("uvicorn.error")

SETTLE_HOUR = 23
SETTLE_MINUTE = 59

_scheduler: AsyncIOScheduler | None = None


async def run_daily_settlement() -> None:
    """排程進入點。資料庫操作丟到執行緒，避免卡住事件迴圈。"""
    try:
        result = await asyncio.to_thread(summary.settle_today)
    except Exception:
        logger.exception("每日結算失敗")
        return
    if result:
        # 讓開著儀表板的頁面知道要刷新
        await manager.broadcast(
            {"event": "day_settled", "business_date": result["business_date"]}
        )


def start() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone=ZoneInfo(APP_TIMEZONE))
    _scheduler.add_job(
        run_daily_settlement,
        CronTrigger(hour=SETTLE_HOUR, minute=SETTLE_MINUTE),
        id="daily-settlement",
        replace_existing=True,
        misfire_grace_time=3600,  # 服務稍晚才起來也補跑
    )
    _scheduler.start()
    logger.info(
        "每日結算排程已啟動：每天 %02d:%02d（%s）",
        SETTLE_HOUR,
        SETTLE_MINUTE,
        APP_TIMEZONE,
    )


def shutdown() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
