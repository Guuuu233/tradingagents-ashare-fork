# DAV-293 集成：a2a9733 + 3fed8d5 线性组合树并 FF 合入

**当前主干：`cfc1e22ce8b060a18017acbfa8f4d92144df9cd5`。两个候选均为其直接子提交（兄弟分支），禁止 merge commit 进主干。**

## 候选（独立终审均已 PASS）

| 候选 | 内容 | 审核 |
|---|---|---|
| `a2a9733f254e8dfc995fe5a6ee7e1f4de121b3c8` | DAV-290 TA_API_KEY 泄漏修复（P0） | DAV-292 PASS |
| `3fed8d58e8e7e224ffdba70c7b1252e6a072914c` | DAV-287 案例回填 | DAV-291 PASS |

## 步骤

1. 在你的 checkout（非宿主）从 `cfc1e22` 建线性组合分支：先重放安全修复（保持原提交信息），再在其上重放回填提交。两候选都改 `api/main.py` 但区域不同，如冲突按各自白名单语义最小解决。
2. 推送组合分支到 `target`（Guuuu233/1）。
3. 组合树头跑全量回归：`PYTHONPATH=. TUSHARE_TOKEN='' .venv310/bin/python -m pytest tests/ -q`。
   - 允许的已知失败**仅限** `tests/test_sina_historical_fund_flow.py` 的 8 个 current_day 用例（非交易日假阳性，DAV-289 待修）。
   - 出现任何其他失败即停止、贴日志、不得合入。
4. 全绿后 fast-forward only 推送 `codex/dav-4-p2a-trunk` 到组合树头；GitHub API 回读 HEAD 确认。
5. 回报：组合链两个 SHA、回归统计（passed/failed 明细）、远端 HEAD。

禁止强推、禁止 merge commit、禁止碰 `.env`/providers/role_bindings。不得 @项目调度助手。
