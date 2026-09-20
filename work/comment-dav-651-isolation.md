## Cursor 隔离（不准予合入）

**SHA：** `fd5266cba24bab52f547c25b0bb53a5266e73c31`  
**父：** `4fdcf8efa841c2a881d82429febd48578d544c94`  
**pytest：** `tests/test_social_acceptance_plan_guard.py` → 7 passed in 0.25s（数字本身不能当契约已锁）。

**打回点：** `test_historical_snapshot_no_backfill_invariant` 中 `or "严禁当天新采回填"` 未写 `in content`，断言恒真。清单正文门槛（30/10、2–5、AUTH-01..07）看起来未降，但防降级测试对「禁止回填」没有锁住。

返修见独立卡，勿在本卡再叠无关改动。禁止部署。
