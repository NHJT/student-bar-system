# 學生酒吧點餐與庫存管理系統

作者：Jeff Lin

Python + FastAPI + PostgreSQL 後端，原生 HTML/CSS/JavaScript 前端，WebSocket 即時更新。
部署於 Railway。

## 專案結構

```
Procfile           # Railway/Heroku 啟動指令
railway.toml       # Railway 部署設定
init_db.py         # 部署後手動執行的資料表初始化腳本
backend/
  main.py          # FastAPI 進入點（含 WebSocket 端點、靜態檔掛載）
  database.py      # SQLAlchemy 連線（讀 DATABASE_URL）與資料表初始化
  schema.sql       # 四張資料表結構（PostgreSQL）
  models.py        # Pydantic 模型
  seed_data.py     # 酒吧真實菜單（飲品/原料/配方），會先清空再寫入
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

## 環境變數

所有設定都從環境變數讀取，程式碼裡不含任何密碼或連線字串。

| 變數 | 必填 | 說明 |
|---|---|---|
| `DATABASE_URL` | ✅ | PostgreSQL 連線字串。Railway 的 Postgres 服務會自動提供 |
| `PORT` | — | 由 Railway 注入；本機不設定時 uvicorn 用預設埠 |
| `APP_TIMEZONE` | — | 顯示用時區，預設 `Asia/Taipei` |
| `SQL_ECHO` | — | 設為 `1` 會把實際送出的 SQL 印到 log，除錯用 |

## 本機開發

需要一個本機或遠端的 PostgreSQL（不再支援 SQLite）。

```bash
pip install -r requirements.txt
createdb studentbar
export DATABASE_URL=postgresql://postgres@127.0.0.1:5432/studentbar
python init_db.py --seed        # 建立資料表並寫入菜單資料
uvicorn backend.main:app --reload
```

打開 http://localhost:8000 選擇角色頁面。API 文件在 http://localhost:8000/docs。

## 部署到 Railway

1. 在 Railway 專案裡新增一個 **PostgreSQL** 服務。
2. 在應用服務的 Variables 加上 `DATABASE_URL`，值填 `${{Postgres.DATABASE_URL}}`
   （參照 Postgres 服務，不要自己貼連線字串）。需要別的時區再加 `APP_TIMEZONE`。
3. 推上 GitHub 後 Railway 會依 `railway.toml` / `Procfile` 建置並啟動。
4. **第一次部署後手動初始化資料表**（資料表不會在啟動時自動建立）：

   ```bash
   railway run python init_db.py --seed   # 只要建表不寫菜單就拿掉 --seed
   ```

   也可以在 Railway 的 Shell 直接執行同一行指令。

## 菜單資料

`backend/seed_data.py` 存的是酒吧的真實菜單：26 款調酒（依基酒分成 GIN／
WHISKEY／VODKA／RUM／TEQUILA BASE、OTHERS、SIGNATURE）、38 項原料、116 筆配方。

```bash
python -m backend.seed_data           # 重置菜單
python -m backend.seed_data --force   # 連同既有訂單一起清除
```

⚠️ 這是**重置**而非補資料：執行時會先清空 `recipes`、`drinks`、`ingredients`
再寫入。因為 `orders` 參照 `drinks`，資料庫裡若已有訂單，腳本會中止並要求加
`--force`，避免不小心刪掉營業資料。

每杯用量是合理的起始值（基酒 0.05 瓶、利口酒 0.02 瓶、萊姆汁 0.05 瓶、
果汁 0.1 瓶、汽水 1 罐、香草 0.2 束、冰塊 0.1 公斤、苦精 0.005 瓶），
實際份量請在經理後台的配方管理逐項調整。

售價寫在 `MENU` 裡但**尚未進資料庫**（`drinks` 表沒有 `price` 欄位），
之後要加價格／營收功能時可直接取用。

店內桌號為 1–13，設定在 `frontend/js/common.js` 的 `TABLE_COUNT`。

部署注意事項：

- `railway.toml` 固定 `numReplicas = 1`。WebSocket 的連線清單放在單一程序的記憶體中，
  開多副本會讓推播只送到其中一台；要水平擴充得改用 Redis pub/sub 之類的外部通道。
- 健康檢查指向 `/api/health`。
- 時間欄位在資料庫一律以 `TIMESTAMPTZ` 存 UTC，API 回傳帶時區位移的 ISO-8601
  字串（例如 `2026-08-14T20:05:00+08:00`），前端據此計算 Andon 超時，
  因此瀏覽器在哪個時區都不會算錯。

## 資料表

| 資料表 | 欄位 |
|---|---|
| drinks | id, name, category, is_available |
| ingredients | id, name, unit, current_stock, reorder_point |
| recipes | drink_id, ingredient_id, quantity_needed |
| orders | id, table_number, drink_id, quantity, special_request, status, payment_status, edit_count, placed_at, started_at, completed_at, delivered_at |

`edit_count` 記錄服務生修改該筆訂單的次數，作為輸入錯誤率的量測依據。
新增欄位時要同時登記到 `database.py` 的 `_MIGRATIONS`，既有的資料庫執行
`python init_db.py` 才會一起升級。

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
