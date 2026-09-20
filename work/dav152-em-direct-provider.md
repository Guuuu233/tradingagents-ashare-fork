# DAV-152 东财直连接入资金流生产 fallback

## 目标

在当前 target trunk `codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef` 上，新增一个精确、可审计的东方财富直连历史资金流 fallback，使 `get_individual_fund_flow` 在 AkShare `stock_individual_fund_flow` 因 Python TLS/指纹失败时，仍可从东财公开 `push2.eastmoney.com/api/qt/stock/fflow/daykline/get` 取得逐日结构化数据。

## 重要事实

刚刚在宿主直连实测，未打印完整响应，只保留脱敏字段：

- `002167.SZ`，2026-08-14：HTTP 200，`rc=0`，返回 1 条 kline；
- `600396.SH`，2026-08-14：HTTP 200，`rc=0`，返回 1 条 kline；
- 该 endpoint 返回的 11 个逗号字段必须先查明/固定字段定义，不能凭位置猜语义；需要用公开文档/邻近既有映射或交叉核验确认哪些字段是 `r0_net`、大单/特大单等。

## 施工边界

只允许修改：

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- 一个既有 provider 测试文件（优先 `tests/test_cn_akshare_backup_sources.py`）

禁止修改：配置、providers/模型绑定、API Key、数据库、collector、analyst、累计窗口、主干、服务运行配置。禁止修改新浪算法语义；Sina Web 仍只作 legacy reference。

## 生产要求

1. 直连调用必须有 timeout、HTTP/JSON/rc/结构异常分类和失败链记录；不得吞错。
2. 只接受合法交易日、有效日期和有限数值；严格按 `curr_date` 截断，禁止未来行。
3. 必须按明确字段名/已核验字段映射构造结构化 evidence，保留 raw value、raw unit、normalized unit、field semantics、source、algorithm_group、as_of、window、period_kind。
4. 不得把东财总净额/未知字段猜成 `r0_net`。若无法证明字段映射，必须停止并返回 typed gap，不要接线。
5. AkShare EM 失败 → 东财直连成功时，返回新算法组 `source=eastmoney_direct` 并保留 AkShare 失败；东财直连失败后继续既有 Sina fallback，并保留完整 `attempted_sources`、`fallback_errors`、`em_typed_gap`、`final_source`。
6. 东财直连成功但字段无法证明/数据不完整时，必须继续 fallback，不得把 HTTP 200 当成功。

## 测试

至少新增/更新：

- 直连成功的结构化 fixture：日期、字段映射、单位和 `as_of` 断言；
- HTTP/JSON/rc/字段缺失失败后仍 fallback 的回归；
- 东财直连与新浪字段不混淆，`r0_net` 语义不可伪造；
- 失败链包含 AkShare、直连和后续新浪尝试。

使用宿主 `.venv310` 跑指定测试、compileall、diff-check。交付精确远端 SHA 后进入只读审核；不要声称已经形成 EM/THS 共识，真实 THS 可比性仍是独立门。

## 交付

远端 branch/SHA、实际 changed files、定向测试、compileall、diff-check、结构化 metadata 示例（不得含凭据）和未解决限制。不得合入 target trunk、不得重启服务。执行者：资深开发1。使用精确 fresh microtask，不读取 DAV-119 父历史。 