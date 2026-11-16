# 工作規則

## 改動範圍
- 只做我要求的改動，不順手重構或改其他邏輯。
- 不改既有的下單、付款、計時、警告門檻、庫存、每日結算邏輯，除非我明講。

## 流程
- 不要 push。改完給 diff 摘要：檔案、行數、風險。
- 完成後只 commit。push 只推到我指定的分支，預設不 push。
- 涉及資料庫變更時：只新增可為空的欄位；migration 放進 `init_db.py` 且可重複執行；先說明風險再動手。

## 畫面即時更新（「要按 F5」的復發防護）
- 凡是修改連線、輪詢、畫面更新相關程式，改完必須跑 `python -m pytest tests/test_live_update.py -v`，全部 passed 才能 commit；`skipped` 不算通過。
- 範圍：`frontend/js/common.js` 的 `guardedRefresh`／`wsStartPolling`／`wsScheduleReconnect`／`wsWireVisibility`／`connectWebSocket`、`bar.js` 與 `waiter.js` 的 `connectWebSocket(...)` 呼叫及其 refresh、`backend/ws.py`、`backend/main.py` 的 `/ws`。
- commit message 要註明已跑，例如結尾加一行「已跑 tests/test_live_update.py：9 passed」。
- 測試跑不起來（沒有測試資料庫或瀏覽器）就停下來告訴我，不要跳過，也不要為了讓它過而放寬測試裡的時限。

## 腳本
- 一次性腳本預設 dry-run，加 `--execute` 才真的寫入。

## 機密
- 不得在輸出、檔案、commit 中出現連線字串、token、密碼。連線一律從環境變數 `DATABASE_URL` 讀。

## 部署
- 營業時段（週五 19:00–22:00）不部署。

## 回覆格式
- 先寫結論與要我決定的事，整則不超過 15 行。
- 只列：改了哪些檔案、風險、需要我決定的事。不要貼程式碼。
- 細節放在我問時再說。
