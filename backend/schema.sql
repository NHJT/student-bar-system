-- 學生酒吧點餐與庫存管理系統 資料庫結構（PostgreSQL）

-- 酒單
CREATE TABLE IF NOT EXISTS drinks (
    id           SERIAL  PRIMARY KEY,
    name         TEXT    NOT NULL UNIQUE,
    category     TEXT    NOT NULL,
    is_available BOOLEAN NOT NULL DEFAULT TRUE
);

-- 原料
CREATE TABLE IF NOT EXISTS ingredients (
    id            SERIAL           PRIMARY KEY,
    name          TEXT             NOT NULL UNIQUE,
    unit          TEXT             NOT NULL,          -- 例: ml, 顆, 片
    current_stock DOUBLE PRECISION NOT NULL DEFAULT 0,
    reorder_point DOUBLE PRECISION NOT NULL DEFAULT 0 -- 低於此量觸發補貨警示
);

-- 配方：一杯飲料需要哪些原料、各多少量
CREATE TABLE IF NOT EXISTS recipes (
    drink_id        INTEGER          NOT NULL REFERENCES drinks (id) ON DELETE CASCADE,
    ingredient_id   INTEGER          NOT NULL REFERENCES ingredients (id) ON DELETE CASCADE,
    quantity_needed DOUBLE PRECISION NOT NULL,
    PRIMARY KEY (drink_id, ingredient_id)
);

-- 訂單（一張訂單 = 一個 order_group，可包含多個品項）
-- 狀態與時間戳都記在訂單層級；各品項可以單獨切換狀態，
-- 訂單狀態一律取「最落後的品項」，全部做完才算完成。
CREATE TABLE IF NOT EXISTS order_groups (
    id              SERIAL      PRIMARY KEY,
    table_number    INTEGER     NOT NULL,
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
    status          TEXT    NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'preparing', 'completed', 'delivered', 'cancelled'))
);

CREATE INDEX IF NOT EXISTS idx_groups_status ON order_groups (status);
CREATE INDEX IF NOT EXISTS idx_groups_placed_at ON order_groups (placed_at);
CREATE INDEX IF NOT EXISTS idx_items_group ON order_items (group_id);

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
    table_number         INTEGER     NOT NULL,
    items                TEXT        NOT NULL,  -- 例: "Negroni x2 | Gin Tonic x1"
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
