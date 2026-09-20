# C-04 切片 1（只读）：dividend + raw daily 的 PIT 风险与落点

**基线：** `git fetch origin` 后 `origin/codex/dav-4-p2a-trunk` 必须是 `c83881809da88686c30f097b1c3872187a5733ca`（或其线性后代）。  
**一个关注点：** 冻结「如何用私有网关 `dividend` + raw daily 做 PIT/RAW，以及为何不能用当前截面 `adj_factor` 回填历史」。  
**禁止：** 改 `tradingagents/`、`api/`、`frontend/`、`tests/`；改 token / `.env` / `role_bindings` / `providers`；重启服务；实现复权引擎；接线 `dividend`/`adj_factor`/`daily`；混 C-09-3 / C-05；直连官方 `api.tushare.pro`；打印 token；FF/部署；push 主干。本卡评论**禁止** @独立代码审核员。

C-05 采集/覆盖度诚实路径已在主干。网关矩阵见 `work/2026-09-05-tushare-gateway-matrix.md`（仓库里没有 `tushare-private-gateway-matrix.md` 这个文件名）。现网 `_TUSHARE_REQUEST_FIELDS` 含资金流、`daily_basic`、`forecast`/`repurchase`/`disclosure_date`，**不含** `dividend` / `adj_factor` / raw `daily`。DAV-606 只把回测缺省口径标成 `vendor_qfq`，不是 PIT 复权引擎。

## 允许改

只新增一个文档：`work/2026-09-05-c04-pit-raw-dividend-eval.md`

必须写清（grep 主干证据，带路径）：

1. 现网行情是前复权（`adjust="qfq"` / `price_basis=vendor_qfq`）vs 真正不复权 raw 收盘价通道尚未接入。
2. `adj_factor`：矩阵记为网关可用；当前代码未进 `_TUSHARE_REQUEST_FIELDS`。即使用当前因子也**禁止**回填历史 PIT；只可核验或自今日起按日归档。
3. `dividend`：作除权事件旁证的 PIT 日期字段该用哪一列（按列名，禁止位置切片）；不得把空表写成确认无分红。
4. 与 DAV-606 `price_basis` 标签如何对接：未接入 raw 时不得声称 `raw` / `PIT_ADJUSTED`。
5. 建议下一刀（只写计划）：是否单独开「只读探针」还是「只接线 dividend 旁证」；本卡不施工。
6. 明确未改服务、未开加权、未部署。若做只读探针：只记 HTTP/业务 code、行数、字段名、失败类别；默认以静态 grep + 矩阵为准，不必打网关。

一个 commit，push 功能分支，评论 40 位 SHA。不要自建审核卡、不要 @独立代码审核员。
