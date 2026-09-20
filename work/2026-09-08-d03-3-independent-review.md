# 独立复审结论：D-03-3 / D-04 资金面分析消费 scale_metrics

**结论：❌ 打回**（2026-09-08 由 David 裁定，取代复审初判的 ⚠️）

> **裁定变更记录：** 复审初判为 ⚠️ 有条件通过，倾向认为 `4a83725` 的两项改动是同一修复的两个侧面。**David 裁定该倾向不成立**：数值校验与来源隔离的失败条件、契约语义、测试均可独立分离，构成两个关注点，违反 AGENTS.md 铁律 4。单关注点门禁 ❌，`8f298a8e565d51bd3bb9b4ffa00179fe450259a8` 不得直接 FF。返修方案见 `work/2026-09-08-dav747-split-rework-card.md`。

- 复审角色：Claude（**非本候选实现者**，实现者为资深开发1 / multica-agent，写审分离成立，符合 D-011 §3）
- 复审性质：只读。未改功能代码、未 commit、未 push、未 FF、未部署。
- **本结论不等于放行。** 按 D-011 §4.3，FF 须由 David 写出完整 40 位 SHA 与「准予合入」。

## 候选

- **审核 tip：** `8f298a8e565d51bd3bb9b4ffa00179fe450259a8`
- **第一父：** `4a837253ddbb538a8cc3ec491ad6fbeed4d1b2fa`
- **merge-base：** `3496280753139aca9b4567f74da625ed48bdfc1a` = 当前 origin trunk tip ✅
- **分支：** `origin/agent/1/a03cac37099b`，trunk 之上线性 4 刀
- 关联卡：DAV-734 / DAV-739 / DAV-747 / DAV-750

链：`704cd68`(DAV-734 实现) → `7f85e2b`(DAV-739 封 scale 旁路 + fail-closed) → `4a83725`(DAV-747 数值校验 + 来源隔离) → `8f298a8`(DAV-750 展示文本一致性)

## 通过项（附证据）

| # | 检查项 | 证据 |
|---|---|---|
| 1 | 基线合法 | merge-base 即 trunk tip，非陈旧基线，无 merge commit |
| 2 | 白名单 | `git diff --name-only 3496280..8f298a8` 仅 2 项：`tradingagents/agents/analysts/smart_money_analyst.py`、新建 `tests/test_smart_money_scale_metrics.py` |
| 3 | 禁改文件零改动 | `fund_flow_evidence.py`（纯计算）、`data_collector.py`、`cn_akshare_provider.py`、`frontend/` 全部未触碰；未新增 fetcher |
| 4 | **真实接线** | `smart_money_analyst.py:476` `scale_metrics_prompt = format_fund_flow_scale_metrics_prompt(scale_metrics, selection)`，在 `create_smart_money_analyst` 体内。区别于 E-01 的零引用 |
| 5 | 未引入评分/权重/排名 | `fallback_rank`(:711)、`credibility_score`(:716) 经 diff 比对为**既有代码**，本链未新增。纪律文本明写严禁跨股排名与新评分 |
| 6 | unavailable 纪律强制 | `smart_money_analyst.py` 三处降级 + 契约矛盾记录：available 但两比率不可用→降级；partial 但无可用比率→降级；unavailable 携带数值→不予呈现。输出固定含「不得据绝对净额替代」 |
| 7 | **净额比率 0 当合法值** | 探针：`net_to_circ_mv=0` + `net_to_amount=0.0` → 状态 `available (完整可用)`。直接承接 DAV-719「0 不得当缺失」的裁决 |
| 8 | 输入不就地修改 | 探针 deepcopy 前后比对：`scale_metrics` 与 `selection` 均未被改，与 docstring 契约一致 |
| 9 | 池写回非本刀引入 | `fund_flow_evidence["selection"] = selection` 在 trunk `3496280` 的 `smart_money_analyst.py:124`、`:249` 已存在。本刀新增的 `evidence_for_display = dict(...)` + `pop("scale_metrics")` 反而是防御性浅拷贝，避免 scale_metrics 在 prompt 中重复呈现 |
| 10 | reference_only 不回退 | 仅接受严格 bool；缺失/非 bool → 显示「未知/缺少 selection，按 reference_only 纪律处理」，不默认 False。测试覆盖 str/int/bool/缺失四类 |

## 测试证据

```
工作树：/private/tmp/ta-review-8f298a8（隔离，detached @ 8f298a8）
解释器：/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
命令：  env -u PYTHONPATH ... -m pytest -q -p no:randomly
```

**定向：** `tests/test_smart_money_scale_metrics.py` + `test_fund_flow_scale_collector.py` + `test_fund_flow_scale_metrics.py` + `test_tushare_daily_basic.py` + `test_h1b_gates.py` → **303 passed in 24.28s**

**全量对照基线：**

| | 基线（trunk+E-01） | 本候选 `8f298a8` | 判定 |
|---|---|---|---|
| failed | 17 | **17** | 失败集**逐项相同**，零新增回归 |
| passed | 3214 | **3388** | +174（新测试文件） |
| skipped | 1 | 1 | — |

基线明细见 `work/2026-09-08-full-suite-baseline.md`。17 项均为既有失败，与本候选无因果关系。

## 打回项

**R-1｜`4a83725`(DAV-747) 单 commit 含两个独立关注点 —— 违反 AGENTS.md 铁律 4**

裁定人：David。复审初判倾向合并成立，**该倾向被推翻**。

分离证据（复审补充定位，供返修执行）：两组 hunk 与测试完全不相交，见 `work/2026-09-08-dav747-split-rework-card.md`。

功能正确性与测试证据（下列全部十项通过 + 303 定向 + 全量零回归）**不受影响**，但门禁不通过，故不得 FF。返修后须对新的最终 SHA 重做独立复审。

## 未覆盖范围（诚实声明）

- **未做 UI / 真实服务 / 部署验收。** 本次仅离线测试与静态复核。
- 未验证 LLM 实际消费 prompt 后的行为（需真实运行授权，本轮无）。
- 未独立在 trunk `3496280` 复跑全量以证明 17 项失败在 trunk 亦存在——依据是 E-01 commit 为纯新增文件的推断，非实测。
- `smart_money_analyst.py` 既有的池写回（:124/:249）本次判定为**范围外**，未评估其是否污染共享池。若需评估须另开只读卡。

## 后续

按 D-011 §4.3，下一步是 David 决定是否写「准予合入」。复审方不开 FF 卡、不指派、不推进合入。

复审人：Claude ｜ 日期：2026-09-08 ｜ 依据：D-011（取代 D-010 中 Cursor 条款）
