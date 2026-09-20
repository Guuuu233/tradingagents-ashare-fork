# C-09-2：`daily_basic` 读取接线（不注入资金流归一）

**基线：** `git fetch` 后 `origin/codex/dav-4-p2a-trunk` = `5e254acae668bc0b391f62624a8f3057c732e683`（若 tip 已变，停下来问）。  
**一个关注点：** 用现有 `CnAkshareProvider._tushare_transport_post` / `_tushare_post` 拉取**单日** `daily_basic`，按列名取数，失败分类与 PIT 截断。  
**不要做：** 资金流 `scale_metrics` 注入（那是 C-09-3）；C-04 复权；C-05 旁证；改默认 URL；改 token / `role_bindings`；打官方 `api.tushare.pro` 真网；打印 token；FF / 部署。

依据 `work/2026-09-05-c09-daily-basic-eval.md`。评估稿里的 `async def` **不要照抄**——本仓库 Tushare 路径是同步的。

## 允许改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`（扩展 `_TUSHARE_REQUEST_FIELDS`，新增 `_fetch_tushare_daily_basic`；复用既有 post/decode/error，禁止新开并行 HTTP 客户端）
- `tests/test_tushare_daily_basic.py`（新建）

禁止改 `data_collector.py`、巨潮、资金流证据对象。

## 契约

1. 入参 `symbol` + `trade_date`/`as_of`。`trade_date > as_of` 必须拒绝，不得请求网关。
2. 取列按名字：至少 `ts_code,trade_date,close,turnover_rate,turnover_rate_f,volume_ratio,free_share,circ_mv,total_mv,amount`（网关缺列进 `missing`，禁止 `iloc`）。
3. 失败类型沿用 `tushare.daily_basic:<category>`：`token_missing` / `permission_denied` / `no_rows` / `missing_field` / `json_shape` / transport。空表 ≠ 用最新截面回填。
4. 走 `TUSHARE_API_URL` / `TUSHARE_BASE_URL`；**不要改**缺省落到官方站的现有行为。
5. 单测 mock HTTP：**正常一行、token 缺失、空表、缺列、日期越界**。禁止真 token、禁止测里打网关。

一个 commit，push 功能分支，40 位 SHA。禁止 push 主干、禁止 FF。
