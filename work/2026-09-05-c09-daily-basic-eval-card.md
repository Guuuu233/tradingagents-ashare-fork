# C-09 切片 1（只读）：daily_basic 规模归一落点

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` 必须已含 C-05d `b42bb50893ec135c9f365f98f12255a18728f696` 与 C-04 文档（cherry-pick 后的 tip）。开工前 `git rev-parse origin/codex/dav-4-p2a-trunk`。  
**一个关注点：** 冻结如何用私有网关 `daily_basic` 做成交额 / 自由流通股本 / 流通市值的规模归一，以及 PIT 字段时点。  
**禁止：** 改 `tradingagents/`、改 token、改 providers、接线生产、混 C-04 复权引擎、混 C-05。

依据 `work/2026-09-05-tushare-private-gateway-matrix.md` 与 `work/2026-09-05-c04-pit-raw-dividend-eval.md`。只新增 `work/2026-09-05-c09-daily-basic-eval.md`。

必须写清：哪些字段可作规模归一；`trade_date` 必须 ≤ 分析截止日；缺列上报；禁止用最新截面回填历史市值。禁止打印 token。一个 commit，push，40 位 SHA。禁止 FF/部署。
