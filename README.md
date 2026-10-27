# 學生酒吧點餐與庫存管理系統

作者：Jeff Lin

Python + FastAPI + PostgreSQL 後端，原生 HTML/CSS/JavaScript 前端，WebSocket 即時更新。
部署於 Railway。

## 專案結構

```
Procfile           # Railway/Heroku 啟動指令
railway.toml       # Railway 部署設定
init_db.py         # 部署後手動執行的資料表初始化腳本
cleanup_test_orders.py   # 一次性：清除測試訂單與歷史資料（預設只盤點，--execute 才刪）
backfill_history_notes.py # 一次性：把已結算日子的備註補回 historical_orders.notes
set_drink_prices.py      # 一次性：批次設定酒單價格
backend/
  main.py          # FastAPI 進入點（含 WebSocket 端點、靜態檔掛載）
  database.py      # SQLAlchemy 連線（讀 DATABASE_URL）與資料表初始化
  schema.sql       # 資料表結構（PostgreSQL）
  models.py        # Pydantic 模型
  seed_data.py     # 酒吧真實菜單（飲品/原料/配方），會先清空再寫入
  update_recipes.py # 只更新原料與配方，保留訂單（營運中改配方用這支）
  inventory.py     # 配方庫存連動（下單扣料/取消退料/補貨警示）
  summary.py       # 每日結算：彙總寫進 daily_summary，原始訂單存 historical_orders
  scheduler.py     # 每天 23:59 觸發結算的排程
  ws.py            # WebSocket 連線管理
  routers/
    drinks.py      # 飲料 API
    ingredients.py # 原料庫存 API
    orders.py      # 訂單 API
    recipes.py     # 配方 API
    stats.py       # 經理儀表板統計 API
    history.py     # 歷史數據 API
frontend/
  index.html       # 角色選擇首頁
  waiter.html      # 服務生：待送達佇列（置頂）、購物車式點餐、標記付款、修改／取消
  bar.html         # 吧台：新訂單／製作中兩欄佇列（可展開分項處理）、Andon 超時
  manager.html     # 經理：今日 KPI、熱門品項、超時訂單、訂單總覽、
                   #       庫存警示，以及飲品／原料／配方管理
  history.html     # 歷史數據：每個營業日一列，點開看明細並下載原始 CSV
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

Windows 的 `set` 和 bash 的 `export` 不一樣，環境變數要這樣設（和後面的
指令要在**同一個視窗**執行，關掉視窗就失效）：

```bat
REM 命令提示字元 (cmd)
set DATABASE_URL=postgresql://postgres@127.0.0.1:5432/studentbar
```

```powershell
# PowerShell
$env:DATABASE_URL = "postgresql://postgres@127.0.0.1:5432/studentbar"
```

cmd 的 `set` 前後不要有空白、不要加引號，密碼裡若有 `%` 要寫成 `%%`。

### 從自己的電腦連 Railway 的資料庫

要對線上資料庫跑 `init_db.py` 或 `update_recipes` 時，`DATABASE_URL` 要填
Railway Postgres 服務的**公開**連線字串 `DATABASE_PUBLIC_URL`（主機長得像
`xxx.proxy.rlwy.net`）。`postgres.railway.internal` 只有在 Railway 的容器
內部才解析得到，從自己的電腦連會失敗。

打開 http://localhost:8000 選擇角色頁面。API 文件在 http://localhost:8000/docs。

### 跑測試

測試需要一個**可以整個清空**的資料庫，用另一個環境變數指定，和
`DATABASE_URL` 分開，避免誤刪平常在用的資料。資料庫名稱一定要含有 `test`，
不然測試會直接拒絕執行。沒設定這個變數就整批跳過。

```bash
pip install -r requirements-dev.txt
createdb studentbar_test
export TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/studentbar_test
python -m pytest
```

```bat
REM 命令提示字元 (cmd)
set TEST_DATABASE_URL=postgresql://postgres@127.0.0.1:5432/studentbar_test
python -m pytest
```

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
WHISKEY／VODKA／RUM／TEQUILA BASE、OTHERS、SIGNATURE）、41 項原料、95 筆配方。

```bash
python -m backend.seed_data           # 重置菜單
python -m backend.seed_data --force   # 連同既有訂單一起清除
```

⚠️ 這是**重置**而非補資料：執行時會先清空 `recipes`、`drinks`、`ingredients`
再寫入。因為 `order_items` 參照 `drinks`，資料庫裡若已有訂單，腳本會中止並要求加
`--force`，避免不小心刪掉營業資料。

### 營運中更新配方

已經在收單、只是要改配方或原料時**不要**跑 `seed_data`，改用：

```bash
python -m backend.update_recipes --dry-run   # 先看會做哪些變更
python -m backend.update_recipes             # 實際更新
```

它只動 `ingredients` 與 `recipes`，訂單與歷史紀錄都保留，而且可以重複執行。

### 單位

液體一律以 **ml** 記錄用量與庫存，不換算成瓶數。進貨時用
`1 瓶 = 700 ml`、`1 罐 = 330 ml`、`1 顆蛋白 = 30 ml`（`seed_data.py` 的
`BOTTLE_ML` / `CAN_ML` / `EGG_WHITE_ML`）把數量換成 ml 填進庫存即可。
非液體維持原本單位：薄荷／紫蘇以「束」、檸檬以「顆」、奶蓋粉以「包」、
冰塊以「公斤」。

「顆」沒有通用換算（檸檬 1 顆不是 30 ml），所以 `update_recipes.py` 的
`PER_INGREDIENT_ML` 是逐項指定的，不要把它改成「顆 → ml」的通用規則。

### 換算估值

酒譜寫「1 吧匙」「7 dash」「3 drop」「top」這類非 ml 的量法時，存進資料庫的
ml 只是估值。這種列在 `MENU` 裡用 `est("1 吧匙")` 標記，會寫進
`recipes.note`，經理後台的配方管理有一欄「備註」顯示它。日後要校正份量，
先看有這個註記的幾列就好。

經理在後台改動某一列的用量後，那列的註記會自動清掉（數字已經不是換算來的了）；
沒動到的列註記保留。

配方數字來自吧台實際出杯量。其中 Boston Sour 與兩款 SIGNATURE 特調還沒拿到
實際配方，`MENU` 裡標了 `TODO`，數字是舊的假設值換算成 ml，
拿到配方後直接改 `MENU` 再跑一次 `update_recipes` 即可。

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
| daily_summary | business_date, total_orders, drinks_sold, avg_prep_minutes, max_wait_minutes, overdue_orders, edit_rate, unpaid_orders, top_drinks |
| historical_orders | business_date, order_id, table_number, items, item_count, quantity, status, placed_at, started_at, completed_at, delivered_at, payment_status, payment_completed_at, is_modified, modify_count |
| order_groups | id, table_number, status, payment_status, payment_completed_at, edit_count, placed_at, started_at, completed_at, delivered_at |
| order_items | id, group_id, drink_id, quantity, special_request, status |

`edit_count` 記錄服務生修改該筆訂單的次數，作為輸入錯誤率的量測依據。
新增欄位時要同時登記到 `database.py` 的 `_MIGRATIONS`，既有的資料庫執行
`python init_db.py` 才會一起升級。

## 一張訂單多品項

一張訂單（`order_groups`）可以包含多個品項（`order_items`），同一桌點不同飲料
只要送出一張訂單。

- **服務生**：選好桌號後**先選基酒分類、再選該分類的飲料**（GIN BASE → Gin Tonic），
  逐項「加入訂單」，清單裡可改數量、改特殊需求或移除，
  確認後整張送出。同一款飲料且特殊需求相同會自動合併數量。
  分類來自 `drinks.category`，顯示順序定在 `common.js` 的 `CATEGORY_ORDER`
  （API 是字母序，點單時依基酒排比較好找）；不在清單裡的分類照字母接在後面，
  所以經理新增分類也不會漏掉。
  數量用 −／＋ 加減器（最小 1），「送出訂單」要車內有品項才可點，
  且與「加入訂單」分置上下兩端、顏色不同，降低誤觸。
  版面是左邊點餐、右欄上為**待送達佇列**（送酒是服務生的工作）、下為本場訂單／付款；
  手機單欄時待送達排最前面。吧台一標記完成，待送達就即時出現，送到桌邊後按
  「已送出」切成已送達，標題徽章顯示待送達張數。
  本場訂單／付款以收款為重點：未付款排最上面並有藍色標示條，已付款且已送達的
  縮成一行，點一下才展開品項。
- **吧台**：兩欄佇列（新訂單、製作中），一張訂單一張卡片，卡片內列出所有品項。
  點按鈕推進整張訂單；需要分開做時展開卡片，對個別品項單獨切換狀態。
  吧台做到「完成製作」為止，之後就離開吧台畫面。
- **狀態與時間戳記在訂單層級**。訂單狀態一律取**最落後的品項**：三杯裡有一杯
  還沒開始做，整張訂單就還是新單，全部做完才算完成。這樣 Andon 計時不會虛報。
- 庫存以整張訂單為單位結算：任一品項的原料不足就整張拒收，不會只扣一半。

### 桌號

桌號在資料庫裡是**文字**不是數字，因為除了「1」~「13」還要能存
「吧台」「站位」「外帶」（三者依序排在桌 1 之前）。顯示時用 `common.js` 的
`tableLabel()`：純數字加「桌」字，其餘原樣輸出。要增減選項改 `common.js` 的
`TABLE_COUNT` 與 `NON_TABLE_SEATS` 即可，後端只檢查非空與長度，不寫死允許清單。

`daily_summary` 沒有桌號欄位（只存當日彙總），不受影響。

⚠️ 程式更新後**一定要跑 `python init_db.py`**：資料庫的 `table_number` 還停在
INTEGER 時，送出文字桌號會在 INSERT 時拋 `InvalidTextRepresentation` 變成 500。
沒跑遷移的話，服務啟動時會在 log 印出「資料庫結構待升級」警告。

訂單狀態流：`new → preparing → completed → delivered`（可 `cancelled`）；付款：`unpaid → paid`。
訂單只有在 `new` 階段可以修改或取消，取消時食材會退回庫存。

## 每日結算與歷史數據

每天 **23:59**（`APP_TIMEZONE` 當地時間）排程會自動結算當日營運數字，寫進
`daily_summary`。當天沒有任何訂單就跳過不存，不會留下空白紀錄。

儲存的欄位：日期、總訂單數、總售出杯數、平均製作時間、最長等待時間（下單到
送達）、超時訂單數、訂單修改率、未付款數、各飲品售出數量。最多保留 **90 個
營業日**，寫入新紀錄後會把超出的最舊紀錄刪掉。

結算不會刪除任何訂單，原始紀錄完整保留。儀表板的「訂單總覽」與 KPI 只統計
**今天**的訂單（`GET /api/orders?today=true`），所以跨過午夜後畫面自然歸零。
吧台與服務生頁面不加這個條件，跨夜還沒做完的訂單才不會從佇列消失。

### 原始訂單紀錄

彙總統計之外，結算時也會把當日**每一筆訂單**另存一份到 `historical_orders`，
供事後自行計算彙總統計沒有涵蓋的指標（90 百分位等待時間、每小時訂單量等）。
欄位：日期、訂單 id、桌號、品項清單（`items`，例如 `Negroni x2 | Gin Tonic x1`）、
品項數、總杯數、狀態、四個階段時間戳、付款狀態與付款時間、是否曾被修改、修改次數。
一張訂單一列，品項寫在同一筆記錄的 `items` 欄位裡。

`status` 不在原始需求裡，但少了它就分不出「已取消」和「結算當下還沒做完」，
算平均等待時間時會把取消的單算進去，所以一併保留。

`historical_orders` 與 `daily_summary` 保留同樣的 90 個營業日，裁切時同步刪除，
不會出現有原始紀錄卻沒有彙總的孤兒資料。

在歷史數據頁展開任一天，點「下載 CSV」即可取得該日原始紀錄。CSV 的時間欄位是
`APP_TIMEZONE` 當地時間的 `YYYY-MM-DD HH:MM:SS`，並帶 UTF-8 BOM，Excel 開啟
中文品名不會亂碼，pandas 也能直接解析。

相關 API：

```
GET  /api/history                    所有歷史營業日（新的在前）
GET  /api/history/{date}             單一營業日
GET  /api/history/{date}/orders.csv  該日原始訂單紀錄（CSV 下載）
POST /api/history/settle             立刻結算今天（重複執行會覆蓋同一天）
```

經理儀表板右上角的「查看歷史數據」進入 `/history.html`，點任一營業日可展開
當日明細與熱門飲品前三名。

服務若在 23:59 前後重啟就會錯過排程，因此**啟動時會自動補結算**兩種情況的過去日期：

- 有訂單但沒有彙總紀錄（錯過排程）
- 有彙總但沒有原始訂單紀錄（那天是加入 `historical_orders` 之前結算的）

原始訂單一直留在 `order_groups` / `order_items` 裡，所以重跑一次就能補齊。
若該日的訂單已不在資料庫（例如當初就沒有留存），則無法還原，歷史頁會標示那天不提供 CSV 下載。

## 主檔管理（經理儀表板）

飲品、原料、配方都可以在經理儀表板直接新增／編輯／刪除，改動會透過 WebSocket
即時同步到其他頁面（例如新增飲品後，服務生的點餐選單會自動更新）。

刪除的連帶影響：

- 刪除飲品或原料時，`recipes` 設了 `ON DELETE CASCADE`，關聯的配方會一併移除。
- **已有訂單紀錄的飲品無法刪除**（回 409），以免破壞既有訂單；請改用「停售」下架。

### 無限制供應的原料

當天現買、不會缺貨的原料（柳橙汁、檸檬、薄荷、蛋白…）可以在原料管理勾選
**無限制供應**（`ingredients.is_unlimited`）。勾選後：

- 下單**不扣庫存**，也不會因為庫存不足擋下訂單
- 不進庫存警示，不會讓任何飲品變成缺料
- 配方照舊保留，`recipes` 的資料完全不動
- 原料列表顯示「無限制」徽章，庫存與補貨點顯示「—」，並集中放在列表下方

實作上只在 `inventory.py` 的 `_RECIPE_SQL` 加了 `AND NOT i.is_unlimited`：
扣料、退料、缺料檢查三件事都走那條 SQL，排除一次就全部生效。

`current_stock` / `reorder_point` 的數字**仍然留在資料庫裡**，編輯時只是把輸入框
停用。取消勾選就接回原本的值，並提示「無限制期間沒有記錄扣庫存，請重新盤點」
——那段期間的消耗沒有被記錄，數字一定是舊的。

之後做補貨點／EOQ 分析時**預設排除** `is_unlimited` 的原料（它們沒有有意義的
消耗紀錄）。`GET /api/ingredients` 是 `SELECT *`，回傳已經包含這個欄位。
歷史結算與 CSV 匯出只處理訂單、不輸出原料，格式不受影響。

## Andon 超時門檻

| 狀態 | 計時起點 | 預設門檻 | 在哪一頁警示 |
|---|---|---|---|
| 新訂單（未開始製作） | `placed_at` | 20 分鐘 | 吧台 |
| 製作中 | `started_at` | 10 分鐘 | 吧台 |
| 待送達（完成未送出） | `completed_at` | 10 分鐘 | 服務生 |

警示跟著負責的人走：做酒的兩段在吧台頁，送酒的那一段在服務生頁。

超時的卡片會變紅、跳出視窗並播放警示音；經理儀表板的訂單總覽也會把該列整列標紅。
要調整門檻請同時改 `frontend/js/common.js` 的 `ANDON_THRESHOLDS` 與
`backend/routers/stats.py` 的 `ANDON_THRESHOLDS`（前者管前端標示，後者管
經理儀表板的超時清單與 KPI）。

## 統計口徑變更

會讓報表數字前後不可直接比較的改動記在這裡。**歷史資料一律不修改**：
`historical_orders`、`daily_summary` 與 CSV 欄位都維持原樣，
變更只影響生效日之後新建立的資料。

| 生效日 | 變更內容 | 提出者 | 影響的統計口徑 |
|---|---|---|---|
| 部署日（待補） | 服務生端移除數量選擇，**一杯酒就是一張訂單**，新單的 `quantity` 恆為 1；同一桌點多杯會建立多張單 | 現場工作人員 | `total_orders` 變多；`drinks_sold`（總杯數）不變；`avg_prep_minutes`、`max_wait_minutes`、`edit_rate` 的分母變大，與 10/16 之前的資料不可直接比較 |
| 2026-10-27 | 吧台／外帶／站位的訂單可由吧台一鍵「完成並送出」 | 現場工作人員 | 這類訂單的 `completed_at` 與 `delivered_at` 幾乎同時；CSV 新增推導欄 `delivered_by`（不是資料表欄位） |
| 2026-10-21 | 只改桌號不再計入修改次數（`edit_count`） | 店主 | `edit_rate` 會略為下降，因為換位子不再算服務生輸入錯誤 |
| 2026-10-21 | 結算時把各品項備註存進 `historical_orders.notes` | 店主 | 新欄位，這天之前結算的日子一律是 `NULL`；其餘欄位與數字沒有變動 |
| 2026-10-16 | Andon 門檻調整為待接單 20 分、製作中 10 分、待送達 10 分 | 店主 | `overdue_orders` 的認定標準改變，門檻放寬後超時筆數會下降 |

**未修改歷史資料**：以上每一項都沒有回頭改寫任何已結算的資料。
10/16 當天及更早的訂單保持原樣（可能是一張單多品項、`quantity` 大於 1），
系統照常讀取、顯示與匯出。唯一一支會寫入歷史表的腳本是
`backfill_history_notes.py`，它只填補原本就是 `NULL` 的 `notes` 欄，
不改動任何既有數值。

## 開發順序

1. 資料庫結構 + FastAPI 骨架
2. 基本 CRUD API
3. 三個角色的前端頁面
4. WebSocket 即時更新
5. Andon 超時邏輯（吧台佇列超時變紅）
6. 配方庫存連動 + Reorder Point 警示
7. 經理儀表板
