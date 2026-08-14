# 學生酒吧點餐與庫存管理系統

作者：Jeff Lin

Python + FastAPI + SQLite 後端，原生 HTML/CSS/JavaScript 前端，WebSocket 即時更新。

## 專案結構

```
backend/
  main.py          # FastAPI 進入點（含 WebSocket 端點、靜態檔掛載）
  database.py      # SQLite 連線與初始化
  schema.sql       # 四張資料表結構
  models.py        # Pydantic 模型
  seed_data.py     # 初始測試資料（飲品/食材/配方）
  inventory.py     # 配方庫存連動（下單扣料/取消退料/補貨警示）
  ws.py            # WebSocket 連線管理
  routers/
    drinks.py      # 飲料 API
    ingredients.py # 原料庫存 API
    orders.py      # 訂單 API
    recipes.py     # 配方 API
    stats.py       # 經理儀表板統計 API
frontend/
  index.html       # 角色選擇首頁
  waiter.html      # 服務生：點餐、桌號、標記付款、修改／取消未製作的訂單
  bar.html         # 吧台：訂單佇列、狀態切換、Andon 超時（變紅＋跳窗＋警示音）
  manager.html     # 經理：今日 KPI、熱門品項、超時訂單、訂單總覽、
                   #       庫存警示，以及飲品／原料／配方管理
  css/style.css
  js/common.js     # 共用 API / WebSocket 工具
  js/waiter.js  js/bar.js  js/manager.js
```

## 快速開始

```bash
pip install -r requirements.txt
python -m backend.seed_data     # 寫入初始測試資料（可重複執行，已存在會跳過）
uvicorn backend.main:app --reload
```

打開 http://localhost:8000 選擇角色頁面。API 文件在 http://localhost:8000/docs。

## 資料表

| 資料表 | 欄位 |
|---|---|
| drinks | id, name, category, is_available |
| ingredients | id, name, unit, current_stock, reorder_point |
| recipes | drink_id, ingredient_id, quantity_needed |
| orders | id, table_number, drink_id, quantity, special_request, status, payment_status, edit_count, placed_at, started_at, completed_at, delivered_at |

`edit_count` 記錄服務生修改該筆訂單的次數，作為輸入錯誤率的量測依據。
新增欄位時要同時登記到 `database.py` 的 `_MIGRATIONS`，既有的 `bar.db` 才會一起升級。

訂單狀態流：`new → preparing → completed → delivered`（可 `cancelled`）；付款：`unpaid → paid`。
訂單只有在 `new` 階段可以修改或取消，取消時食材會退回庫存。

## 主檔管理（經理儀表板）

飲品、原料、配方都可以在經理儀表板直接新增／編輯／刪除，改動會透過 WebSocket
即時同步到其他頁面（例如新增飲品後，服務生的點餐選單會自動更新）。

刪除的連帶影響：

- 刪除飲品或原料時，`recipes` 設了 `ON DELETE CASCADE`，關聯的配方會一併移除。
- **已有訂單紀錄的飲品無法刪除**（回 409），以免破壞既有訂單；請改用「停售」下架。

## Andon 超時門檻

| 狀態 | 計時起點 | 預設門檻 |
|---|---|---|
| 新訂單（未開始製作） | `placed_at` | 5 分鐘 |
| 製作中 | `started_at` | 10 分鐘 |
| 待送達（完成未送出） | `completed_at` | 3 分鐘 |

超時的卡片會變紅、跳出視窗並播放警示音；經理儀表板的訂單總覽也會把該列整列標紅。
要調整門檻請同時改 `frontend/js/common.js` 的 `ANDON_THRESHOLDS` 與
`backend/routers/stats.py` 的 `ANDON_THRESHOLDS`（前者管前端標示，後者管
經理儀表板的超時清單與 KPI）。

## 開發順序

1. 資料庫結構 + FastAPI 骨架
2. 基本 CRUD API
3. 三個角色的前端頁面
4. WebSocket 即時更新
5. Andon 超時邏輯（吧台佇列超時變紅）
6. 配方庫存連動 + Reorder Point 警示
7. 經理儀表板
