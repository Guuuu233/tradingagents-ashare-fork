# DAV-274 阶段三：27 行业动态产业链覆盖（方案 DAV-196）

基线必须是线上主干：`target/codex/dav-4-p2a-trunk@8866494f8cae9f648aff0c4c0a2f9f5cf680e8dd`

对照《项目加强方案》§5.1：扩展 `INDUSTRY_LINKAGE_MAP` 到 27 个行业。当前动态 map 只有 5 个（消费电子、新能源车、半导体、石油化工、金融地产）。静态知识库 `tradingagents/knowledge/industry_linkage.py` 已有 27 项 `industry_name`，动态采集未铺开。

## 27 行业（与知识库对齐，禁止另起一套名字）

以 `tradingagents/knowledge/industry_linkage.py` 现有 27 个 `industry_name` 为权威清单，在 `tradingagents/dataflows/industry_linkage.py` 的 `INDUSTRY_LINKAGE_MAP` **原地扩展**（禁止 `_v2`）。已有 5 个不得删、不得改语义。

每个行业必须有：upstream_cost / downstream_demand / international_benchmark / policy_drivers。缺真实 API 的指标：`status=pending_api` 或等价，输出 `【数据缺失】`，禁止臆造数值。

## DataCollector 映射

扩展 `_map_stock_to_industry`：每个新行业至少 1 只 A 股代表（优先沪深主板/创业板/科创已有代码）。已有 6 只映射不得回归。

## 允许修改

- `tradingagents/dataflows/industry_linkage.py`
- `tradingagents/dataflows/providers/industry_linkage_provider.py`
- `tradingagents/graph/data_collector.py`（仅映射函数）
- `tests/test_industry_linkage.py` 及 collector/dataflows 测试（追加）

禁止改：`setup.py` 拓扑、Prompt 大段、`.env`、providers 表、role_bindings、资金流、主干、部署。本卡不做 RAG、不做历史案例闭环。

## 验收

独立 worktree，父提交 `8866494`。
1. `INDUSTRY_LINKAGE_MAP` 恰好覆盖知识库 27 个行业（或 27 键可解析到那 27 个 industry_name）
2. `get_industry_linkage` 对 27 个行业返回非空结构；无 API 的指标显式缺失
3. 原 5 行业 + 6 只股票映射回归
4. `.venv310` 定向产业链测试 + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check
5. 推送独立分支精确 SHA，不合主干

不得 @项目调度助手。
