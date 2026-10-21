-- 學生酒吧點餐與庫存管理系統 資料庫結構（PostgreSQL）

-- 酒單
CREATE TABLE IF NOT EXISTS drinks (
    id           SERIAL  PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    category     TEXT    NOT NULL,
    is_available BOOLEAN NOT NULL DEFAULT TRUE,
    price        INTEGER          -- 售價（NT$，整數）。未定價為 NULL
);

-- 原料
CREATE TABLE IF NOT EXISTS ingredients (
    id            SERIAL           PRIMARY KEY,
    name          TEXT             NOT NULL UNIQUE,
    unit          TEXT             NOT NULL,          -- 例: ml, 顆, 片
    current_stock DOUBLE PRECISION NOT NULL DEFAULT 0,
    reorder_point DOUBLE PRECISION NOT NULL DEFAULT 0, -- 低於此量觸發補貨警示
    -- 當天現買、不會缺貨的原料（柳橙汁、檸檬、薄荷、蛋白…）：
    -- 不扣庫存、不擋下單、不進補貨警示。庫存與補貨點的數字仍然保留著，
    -- 取消勾選後可以接回原本的值（但那段期間沒有記錄，需要重新盤點）
    is_unlimited  BOOLEAN          NOT NULL DEFAULT FALSE
);

-- 配方：一杯飲料需要哪些原料、各多少量
CREATE TABLE IF NOT EXISTS recipes (
    drink_id        INTEGER          NOT NULL REFERENCES drinks (id) ON DELETE CASCADE,
    ingredient_id   INTEGER          NOT NULL REFERENCES ingredients (id) ON DELETE CASCADE,
    quantity_needed DOUBLE PRECISION NOT NULL,
    -- 這個數字的來源。酒譜本來就寫 ml 的留空；由吧匙／dash／drop／top 之類
    -- 非 ml 的寫法換算來的，標成「換算估值（原酒譜：1 吧匙）」，
    -- 日後要校正份量時才知道哪些數字本來就不精確
    note            TEXT,
    PRIMARY KEY (drink_id, ingredient_id)
);

-- 訂單（一張訂單 = 一個 order_group，可包含多個品項）
-- 狀態與時間戳都記在訂單層級；各品項可以單獨切換狀態，
-- 訂單狀態一律取「最落後的品項」，全部做完才算完成。
CREATE TABLE IF NOT EXISTS order_groups (
    id              SERIAL      PRIMARY KEY,
    -- 文字而非數字：除了「1」~「13」，還要能存「吧台」「站位」
    table_number    TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'preparing', 'completed', 'delivered', 'cancelled')),
    payment_status  TEXT        NOT NULL DEFAULT 'unpaid'
                    CHECK (payment_status IN ('unpaid', 'paid')),
    payment_completed_at TIMESTAMPTZ,             -- 標記為已付款的時間
    edit_count      INTEGER     NOT NULL DEFAULT 0,  -- 服務生修改次數（輸入錯誤率量測）
    -- 時間一律以 TIMESTAMPTZ 存 UTC；顯示時再轉成 APP_TIMEZONE
    placed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,  -- 所有品項都開始製作
    completed_at    TIMESTAMPTZ,  -- 所有品項都製作完成
    delivered_at    TIMESTAMPTZ   -- 整張訂單送達桌邊
);

-- 訂單品項
CREATE TABLE IF NOT EXISTS order_items (
    id              SERIAL  PRIMARY KEY,
    group_id        INTEGER NOT NULL REFERENCES order_groups (id) ON DELETE CASCADE,
    drink_id        INTEGER NOT NULL REFERENCES drinks (id),
    quantity        INTEGER NOT NULL DEFAULT 1,
    special_request TEXT,
    -- 下單當下的 drinks.price 快照。之後改售價不影響已成立的訂單；
    -- 這個欄位加進來之前的舊訂單一律是 NULL
    unit_price      INTEGER,
    status          TEXT    NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'preparing', 'completed', 'delivered', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_groups_status ON order_groups (status);
CREATE INDEX IF NOT EXISTS idx_groups_placed_at ON order_groups (placed_at);
CREATE INDEX IF NOT EXISTS idx_items_group ON order_items (group_id);

-- 狀態退回紀錄（誤觸追蹤）。
-- 只記錄「退回上一個狀態」這個動作，用來之後算吧台的誤觸率。
-- 和 order_groups.edit_count 是兩回事：那個算的是服務生輸入錯誤率，
-- 兩者不要混在一起。
-- 這張表不參與每日結算，也不影響庫存。
CREATE TABLE IF NOT EXISTS order_events (
    id                SERIAL      PRIMARY KEY,
    group_id          INTEGER     NOT NULL,  -- order_groups.id（不設外鍵，訂單刪了紀錄仍留著）
    item_id           INTEGER,               -- 單一品項退回時才有；整張退回為 NULL
    event_type        TEXT        NOT NULL DEFAULT 'revert',
    from_status       TEXT        NOT NULL,
    to_status         TEXT        NOT NULL,
    -- 退回前訂單上的時間戳，退回時會被清掉，留在這裡才查得回來
    prev_started_at   TIMESTAMPTZ,
    prev_completed_at TIMESTAMPTZ,
    prev_delivered_at TIMESTAMPTZ,
    -- 系統目前沒有登入機制，分不出是誰按的，只能記下是哪一頁送出的
    actor             TEXT        NOT NULL DEFAULT 'bar',
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_events_group ON order_events (group_id);
CREATE INDEX IF NOT EXISTS idx_events_created ON order_events (created_at);

-- 每日結算：每天 23:59 把當日營運數字存成一筆歷史紀錄
-- 以 business_date 為主鍵，重跑同一天會覆蓋而不是新增
CREATE TABLE IF NOT EXISTS daily_summary (
    business_date    DATE             PRIMARY KEY,
    total_orders     INTEGER          NOT NULL,
    drinks_sold      INTEGER          NOT NULL,
    avg_prep_minutes DOUBLE PRECISION,           -- 當天沒有完成的訂單時為 NULL
    max_wait_minutes DOUBLE PRECISION,           -- 下單到送達的最長時間
    overdue_orders   INTEGER          NOT NULL,  -- 任一站超過 Andon 門檻的訂單數
    edit_rate        DOUBLE PRECISION NOT NULL,  -- 被修改過的訂單占比（%）
    unpaid_orders    INTEGER          NOT NULL,
    top_drinks       JSONB            NOT NULL,  -- [{"name": ..., "qty": n}, ...] 由多到少
    created_at       TIMESTAMPTZ      NOT NULL DEFAULT NOW()
);

-- 每日結算時另存一份當日訂單原始紀錄，供事後自行計算指標
-- （平均／90 百分位等待時間、每小時訂單量等）。
-- 與 daily_summary 同步保留 90 個營業日。
CREATE TABLE IF NOT EXISTS historical_orders (
    business_date        DATE        NOT NULL,
    order_id             INTEGER     NOT NULL,  -- order_groups.id
    table_number         TEXT        NOT NULL,
    items                TEXT        NOT NULL,  -- 例: "Negroni x2 | Gin Tonic x1"
    -- 各品項的備註，例: "Negroni:少冰 | 苡銨特調:偏酸"。沒有備註的品項不列入，
    -- 整張都沒備註就是 NULL。這個欄位加進來之前結算的日子一律是 NULL。
    notes                TEXT,
    item_count           INTEGER     NOT NULL,  -- 品項數
    quantity             INTEGER     NOT NULL,  -- 總杯數
    status               TEXT        NOT NULL,  -- 需要它才能區分已取消與未完成的訂單
    placed_at            TIMESTAMPTZ NOT NULL,
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    delivered_at         TIMESTAMPTZ,
    payment_status       TEXT        NOT NULL,
    payment_completed_at TIMESTAMPTZ,
    is_modified          BOOLEAN     NOT NULL,
    modify_count         INTEGER     NOT NULL,
    PRIMARY KEY (business_date, order_id)
);
