# 泳道 A（P0）：数据层 as-of 通道修复 + 缺口分诊

**执行纪律：**
- 基线：`final-dav336` @ `b26d040`。开工前 `git fetch target && git checkout -B fix/ta-audit-a target/codex/dav-4-p2a-trunk`（以远端主干为准）。
- 只改本泳道文件：`tradingagents/dataflows/interface.py`、`tradingagents/graph/data_collector.py`、`tradingagents/dataflows/providers/cn_akshare_provider.py`（如需）、新增测试文件。禁改 api/main.py、evidence_verifier.py、report_service.py、prompts/zh.py（其他泳道领地）。
- 环境铁律：宿主 `.venv310`，跑测试用 `env -u PYTHONPATH .venv310/bin/python -m pytest`；数据源直连需完整 no_proxy 清单。
- 金标准夹具已入仓：`tests/golden/audit_20260823/`（三份 `*_result_data.json` 为 DB 全量直导，含 14-18 条 data_gaps 原文）。

## A1. 定位 as-of 拒绝根因
8 项报表类接口（balance_sheet/income_statement/cashflow/fundamentals/earnings_forecast/shareholder_count/fund_flow_individual/restricted_release）在三轮分析中报"未返回可验证数据日期"——接口有数据但被 as-of 验证闸安全丢弃。

1. 读 `tradingagents/dataflows/interface.py:184`（_extract_as_of）与 `:255`（_as_of_refusal）、`tradingagents/graph/data_collector.py:619` 附近的 actual_as_of 提取键。
2. 用真实调用复现：`env -u PYTHONPATH .venv310/bin/python` 下对 600900.SH 调 `get_balance_sheet(ticker, curr_date="2026-08-21")`（走 cn_akshare 的 `_financial_report_sina`，cn_akshare_provider.py:1115），观察原始返回体里有没有报告期日期列、格式是什么。
3. 根因三选一并留证（贴原始返回样本到 PR）：①返回体无日期列 → 需 provider 自建 as_of；②有日期但解析正则缺格式 → 修 _extract_as_of；③data_collector 提取键名不匹配 → 修提取逻辑。

## A2. 最小修复（TDD）
1. 先写失败测试 `tests/test_financial_as_of.py`：构造含报告期日期的真实样本，参数化 8 项接口，断言提取出 ≤ curr_date 的 actual_as_of。
2. 实现最小修复（优先修解析/提取层，不改抓取逻辑）。若根因是新浪源无日期列：在 provider 内用返回体的报告期列自建 as_of，PR 说明；**严禁把发布日当报告期**（防前视），不可行时标 known-gap 并升级，不得静默降级。
3. 冒烟：600900/000333/600276 三标的 × 8 项接口，断言每项返回带 actual_as_of ≤ 2026-08-21。

## A4. 5 项 "provider call failed" 分诊
fund_flow_board / insider_transactions / hot_stocks / share_pledge / northbound_flow 五项：
1. northbound_flow（单日个股北向净流向，披露已停止）→ gap 文案改【数据不可用：披露停止】known-gap 类别。
2. 板块资金即时快照仅支持当日 → 【数据不可用：仅支持当日快照】。
3. share_pledge / insider_transactions / hot_stocks / fund_flow_board 历史日期：先查现有 provider 链（含 fuyao/tushare 线）有无历史能力，有则修 as-of 对齐；确认无源则落 known-gap 文案并在 PR 记录。
4. earnings_forecast/restricted_release 同样定性（无源则 known-gap 注明）。

**验收标准（全部满足才算完成）：**
1. `tests/test_financial_as_of.py` 全绿 + 宿主树既有相关测试不回归；
2. 三标的冒烟输出贴 PR（每项 actual_as_of 可见）；
3. 分支推送远端并回报精确 SHA；
4. 不碰其他泳道文件，`git diff --name-only` 干净可审。

完成后：推分支 → issue 评论附 SHA+测试输出 → 等 Hermes 精确 SHA 复审，禁止自行合主干。
