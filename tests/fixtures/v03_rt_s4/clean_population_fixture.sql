-- RT-S4 clean-population fixture (DAV-1250)
-- 全合成数据，不含任何生产库内容（D-040）。
-- 目标账号 = DEFAULT_TARGET_USER_ID (429163f7-50b6-4982-8bdf-96ae99506843)
-- 截止口径 = DEFAULT_HISTORICAL_CUTOFF_DATE (2026-09-08)
--
-- 设计计数（target user + trade_date <= 2026-09-08）：
--   total=10, completed=8, failed=1（另含 1 条 pending 计入 total）
--   load(completed, cutoff) = 8 行；trade_date ∈ [2025-12-30, 2026-09-08]
--   原始 direction 非空候选 = 6（f004 NULL / f005 '' 不计）
--   measure_dataset 分区：DEV=1 (f008) / HISTORICAL_OOS=5 / FORWARD_OOS=0 / REGRESSION=2 (f006,f007)
--   f011 (2026-09-09) 超出 cutoff 被排除；f012 属其他账号被排除。

CREATE TABLE reports (
    id TEXT,
    user_id TEXT,
    symbol TEXT,
    trade_date TEXT,
    status TEXT,
    decision TEXT,
    direction TEXT,
    confidence INT,
    target_price REAL,
    stop_loss_price REAL,
    result_data TEXT,
    created_at TEXT
);

INSERT INTO reports VALUES
-- completed, HISTORICAL_OOS, 普通在池标的（direction 非空 → 候选）
('f001', '429163f7-50b6-4982-8bdf-96ae99506843', '600519.SH', '2026-03-02', 'completed', 'BUY',  '偏多', 80, 1500.0, 1350.0, '{}', '2026-03-02 15:00:00'),
('f002', '429163f7-50b6-4982-8bdf-96ae99506843', '600519.SH', '2026-03-03', 'completed', 'BUY',  '偏多', 75, 1450.0, 1320.0, '{}', '2026-03-03 15:00:00'),
('f003', '429163f7-50b6-4982-8bdf-96ae99506843', '000001.SZ', '2026-03-04', 'completed', 'SELL', '偏空', 70,   10.0,   12.0, '{}', '2026-03-04 15:00:00'),
-- completed, direction 缺失/为空 → 计入 completed 总数但不算评估候选
('f004', '429163f7-50b6-4982-8bdf-96ae99506843', '600006.SH', '2026-03-05', 'completed', 'BUY',  NULL,  60,   8.0,    7.0, '{}', '2026-03-05 15:00:00'),
('f005', '429163f7-50b6-4982-8bdf-96ae99506843', '600000.SH', '2026-03-06', 'completed', 'HOLD', '',    55, NULL,   NULL,  '{}', '2026-03-06 15:00:00'),
-- completed, 六个黄金回归标的中的两个 → 永久隔离进 REGRESSION 分区
('f006', '429163f7-50b6-4982-8bdf-96ae99506843', '000333.SZ', '2026-03-09', 'completed', 'BUY',  '偏多', 78,   60.0,   52.0, '{}', '2026-03-09 15:00:00'),
('f007', '429163f7-50b6-4982-8bdf-96ae99506843', '601012.SH', '2026-09-08', 'completed', 'BUY',  '偏多', 65,   20.0,   17.0, '{}', '2026-09-08 15:00:00'),
-- completed, DEV 段（<= 2025-12-31）
('f008', '429163f7-50b6-4982-8bdf-96ae99506843', '600519.SH', '2025-12-30', 'completed', 'BUY',  '偏多', 72, 1450.0, 1300.0, '{}', '2025-12-30 15:00:00'),
-- 非 completed：failed 计入 failed；pending 计入 total 但不进 completed/failed
('f009', '429163f7-50b6-4982-8bdf-96ae99506843', '600519.SH', '2026-03-10', 'failed',    'BUY',  '偏多', 50, 1500.0, 1350.0, '{}', '2026-03-10 15:00:00'),
('f010', '429163f7-50b6-4982-8bdf-96ae99506843', '600519.SH', '2026-03-11', 'pending',   'BUY',  '偏多', 50, 1500.0, 1350.0, '{}', '2026-03-11 15:00:00'),
-- 超出 historical cutoff（2026-09-09，FORWARD_OOS 段）→ counts/load 均被 cutoff 排除
('f011', '429163f7-50b6-4982-8bdf-96ae99506843', '600519.SH', '2026-09-09', 'completed', 'BUY',  '偏多', 70, 1500.0, 1350.0, '{}', '2026-09-09 15:00:00'),
-- 其他账号 → 被 target_user_id 过滤
('f012', 'synthetic-other-user-0001',            '600519.SH', '2026-03-02', 'completed', 'BUY',  '偏多', 80, 1500.0, 1350.0, '{}', '2026-03-02 15:00:00');
