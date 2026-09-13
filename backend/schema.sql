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

-- 訂單
CREATE TABLE IF NOT EXISTS orders (
    id              SERIAL      PRIMARY KEY,
    table_number    INTEGER     NOT NULL,
    drink_id        INTEGER     NOT NULL REFERENCES drinks (id),
    quantity        INTEGER     NOT NULL DEFAULT 1,
    special_request TEXT,
    status          TEXT        NOT NULL DEFAULT 'new'
                    CHECK (status IN ('new', 'preparing', 'completed', 'delivered', 'cancelled')),
    payment_status  TEXT        NOT NULL DEFAULT 'unpaid'
                    CHECK (payment_status IN ('unpaid', 'paid')),
    edit_count      INTEGER     NOT NULL DEFAULT 0,  -- 服務生修改次數（輸入錯誤率量測）
    -- 時間一律以 TIMESTAMPTZ 存 UTC；顯示時再轉成 APP_TIMEZONE
    placed_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,  -- 吧台開始製作
    completed_at    TIMESTAMPTZ,  -- 製作完成
    delivered_at    TIMESTAMPTZ   -- 送達桌邊
);

CREATE INDEX IF NOT EXISTS idx_orders_status ON orders (status);
CREATE INDEX IF NOT EXISTS idx_orders_placed_at ON orders (placed_at);

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
