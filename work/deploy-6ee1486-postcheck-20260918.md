# 6ee1486 发布后核验

## 版本与服务

- 远端主线：`6ee148699339efefc2f7f7548eb286be485524e1`
- 合入父提交：`e30f312c968c1e0b82cff38df6c456941eb44f86` + `83f011e05548c0da3140623875ec4cc8d09b88bd`
- 8000 `/healthz`：HTTP 200，`commit_sha` 与 `build_identity` 均为 `6ee148699339efefc2f7f7548eb286be485524e1`
- 进程：PID `64769`，工作目录 `/private/tmp/ta-serve-6ee1486`
- `/`：HTTP 200；`/v1/reports?limit=1`：HTTP 200；伪造发布探针：HTTP 404

## 回归与审查

- DAV-1048 实施候选：`83f011e05548c0da3140623875ec4cc8d09b88bd`，直接父 `e30f312`
- DAV-1049：`代码审核员` 同 SHA 只读复审 PASS，无红黄阻塞
- 专项 `tests/test_historical_cases.py`：`62 passed`
- 候选 RT-FULL：`4929 passed`，隔离临时库、规定既有 deselect、无新增失败
- `git diff --check`：干净；产品改动仅在 `tradingagents/knowledge/historical_cases.py`

## 生产库核验

- 部署前备份：`work/tradingagents.db.bak-20260918-041110-deploy-6ee1486`
- 备份 SHA256：`35bd0e07c7d4873f00afd1a73a72bb82fc111ab2412a74280a879065f0b7c971`
- 备份与启动后当前库的 `quick_check` 均为 `ok`
- 报告计数保持：`1410 total / 794 completed / 616 failed`
- `600519.SH` 待回填记录在启动前后保持一致：`actual_change_pct=NULL`、`actual_outcome=【数据缺失】`、`is_error=NULL`、`updated_at=2026-09-17 19:48:54.146306`
- 启动日志仍显示 `scanned=1, backfilled=0, still_missing=1`；评估日尚未收盘，未提前填入收益

## 结论

6ee1486 已上线。DAV-1048 消除了首次 e30f312 发布时发现的“相同暂态拒绝仍刷新 updated_at”问题；本次启动没有产生新的无语义时间戳变化。DAV-998 的实际回填仍须等评估日收盘后再核验，不能把本次机制验证写成实际回填成功。
