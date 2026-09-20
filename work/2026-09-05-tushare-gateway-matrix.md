# 只读卡：Tushare 私有网关能力矩阵与现有调用覆盖

**基线：** `origin/codex/dav-4-p2a-trunk` = `b2f7b77bca19a9b50f0556989f06553c5b15404f`  
**一个关注点：** 把私有网关实测、权限、字段/PIT 风险、代码覆盖冻结成仓库文档。  
**禁止：** 改代码、改 token、改 `role_bindings`/`providers`、重启或改当前 uvicorn、直连官方 `api.tushare.pro`、把 C-04/C-05/C-09 混进本卡实现。

## 运输事实（用户已实测；本卡核验覆盖，不要复述 token）

宿主 `.env` 已有私有兼容网关；运行 worktree 的 `.env` 链到宿主。项目已支持 `TUSHARE_API_URL` / `TUSHARE_BASE_URL`（见 `CnAkshareProvider._tushare_transport_post` 与 `industry_linkage_provider._get_tushare_url`）。缺省仍会落到 `https://api.tushare.pro`——**文档里写明这是配置失误风险，本卡不要改默认值。**

用户冻结的网关能力：

| API | 结论 | 后续建议落点（只写计划，本卡不施工） |
|---|---|---|
| `trade_cal` | 可用 | 日历核验/旁证 |
| `moneyflow_dc` | 可用 | **已接入**资金流 |
| `moneyflow_ths` | 可用 | **已接入**资金流 |
| `adj_factor` | 可用 | C-04：**只能核验或从现在起按日归档**；禁止用当前因子污染历史 PIT |
| `daily_basic` | 可用 | C-09：成交额、自由流通股本、流通市值做规模归一 |
| `dividend` | 可用 | C-04：与 raw daily 评估 PIT/RAW |
| `repurchase` | 可用 | C-05 结构化旁证 |
| `forecast` | 可用 | C-05 结构化旁证 |
| `disclosure_date` | 可用 | C-05 结构化旁证 |
| `anns_d` | **403，需卖家单独授权** | 全量公告主源 **不**走它；主源是巨潮 AKShare |
| `stk_surv` | 空结构，暂不认定可用 | 不接入 |
| MCP | 暂不进生产链 | 记录即可 |

全量公告与 IR **主源**继续用巨潮 AKShare（并行卡 C-05a）。`forecast`/`repurchase`/`disclosure_date` 只作结构化旁证，本卡不接线。

## 现有代码覆盖（必须 grep 后写进文档，带路径）

至少核对这些调用（以主干 checkout 为准，不要凭记忆）：

- `tradingagents/dataflows/providers/cn_akshare_provider.py`：`moneyflow_dc`、`moneyflow_ths`、财报 `balancesheet`/`income`/`cashflow`
- `tradingagents/dataflows/providers/industry_linkage_provider.py`：`fut_daily`、`index_global`、`shibor`、`shibor_lpr` 等
- 相关 tests：`tests/test_fund_flow_*.py`、`tests/test_industry_linkage_provider.py`

文档表格列：`api_name`、是否已调用、文件:函数、失败分类是否区分 auth/403/empty/rate-limit、是否走 `TUSHARE_API_URL`。

## 交付

只新增 **一个** 文件：`work/2026-09-05-tushare-private-gateway-matrix.md`（或同名修订）。不要改 `tradingagents/`、`api/`、`frontend/`、`.env`。

文档必须写：

1. 上表实测结论（可引用本 brief，禁止粘贴 token/URL 查询串里的密钥）
2. 已接入 vs 未接入
3. PIT 风险：`adj_factor` 当前截面不可回填历史
4. 建议下一刀顺序（各一张卡）：C-04 `dividend+raw daily` 评估；C-09 `daily_basic` 归一；C-05 旁证 `forecast/repurchase/disclosure_date`（在巨潮元数据契约稳定后）
5. 明确 **未** 改服务、**未** 开加权、**未** 部署

若做只读探针：只记录 HTTP/业务 code、行数、字段名、失败类别；**禁止**打印 token 或完整 payload。默认以静态 grep + 用户已给实测为准，不必打网关。

## 施工

隔离 worktree 基于 `b2f7b77bca19a9b50f0556989f06553c5b15404f`。一个 commit。`git push -u origin HEAD`。评论写 40 位 SHA。独立审核只读核对：无密钥、无代码改动、覆盖表与 grep 一致。
