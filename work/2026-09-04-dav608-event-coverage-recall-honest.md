# DAV-608：event_coverage 召回诚实化（禁止把默认五主题当成应查清单）

**父 tip：** `90fcfc435ccc4f4efd32d5ac5c4fbfbfe45975e9`  
**依据：** DAV-598 只读审计；Cursor 已验收「coverage 只证明时间资格；无 query_manifest」。595/601/606 已合入，本卡拆召回诚实化，**不是**接交易所公告。

## 一个关注点

现网 `build_news_event_coverage` 在 `requested_themes` 为空时**编造**五主题 `跨市场/财报/行业政策/公司治理/重大合同`，再对东财最新约 20 条做关键词命中，写成 `suspected_gaps`。`format_event_coverage_summary` 在 gaps 为空时写 **「无明显主题缺失」**。`data_collector.py` 与 `news_analyst.py` 无用户 focus 时同样硬编码这五主题。

这不是召回证明。本卡只停止假装，**不接 CNINFO / 巨潮 / IR、不做 canonical_event_id、不写新 provider、不补 H1b、不部署、不开加权。**

## 语义

`event_coverage` 必须能区分：

| 状态 | 条件 | 允许的说法 |
|---|---|---|
| `recall_status="unknown"` | 调用方未提供应查清单 | 召回完整性未知；`query_manifest=[]`；**禁止**编造五主题；`suspected_gaps` 不得用默认主题冒充缺口 |
| `recall_status="partial_vs_manifest"` | 有显式 `requested_themes` 或 `query_manifest` | 只对清单内主题记 `unverified_or_not_found`；仍不得写成「确认无新闻」 |
| 禁止 | 任何路径 | 「无明显主题缺失」；把时间合格命中当成全市场查全 |

`query_manifest`：原样回显调用方清单（list[str] 即可）。未传则为 `[]`。不要发明交易所公告条目。

显式传入的 `requested_themes`（含测试夹具 R2、用户 `focus_areas`）继续可用；那是调用方声明，不是系统默编。

## 允许改

- `tradingagents/dataflows/news_event_evidence.py`
- `tradingagents/graph/data_collector.py`（只改把五主题硬塞进 `build_news_event_coverage` 的那一处）
- `tradingagents/agents/analysts/news_analyst.py`（只改无 `focus_areas` 时的默认五主题）
- `tests/test_news_event_coverage.py`
- 仅因本改动失败的既有断言（不要扩 R2 成完整 decision_semantics fixture）

原路径改。禁止 `_v2`。魔法值提成具名常量后**删除默认注入**，不要把常量继续当生产缺省清单。

## RED（修复前必须失败）

1. `requested_themes=None`（且不传 query_manifest）时，`requested_themes` / `query_manifest` 不得变成那五个默认主题；`recall_status=="unknown"`。
2. `format_event_coverage_summary` 不得包含「无明显主题缺失」。
3. `news_analyst` 在 `user_intent` 无 `focus_areas` 时不得把五主题写进 coverage 的 `query_manifest`。

修复后上述转绿。显式传入 themes 的既有测试（含 R2 fixture）保持行为：仍可对清单记 gap。

## 禁止

- `tradingagents/dataflows/providers/**` 新接口或编造巨潮/CNINFO API
- `canonical_event_id` 实现（下一张卡）
- `evidence_verifier.py` / `decision_status.py` / `verify_h1b_gates.py` / 回测校准
- 回填旧 121、补样本、自动 FF、部署、`credit_weighting_enabled`

## 验收

一个 commit；隔离 worktree 从父 tip 开分支；完整 pytest 命令/退出码/数量；40 字符 SHA；changed files；`git status`；`git diff --check`。然后独立审核 exact SHA。禁止自称「新闻召回已完成」。
