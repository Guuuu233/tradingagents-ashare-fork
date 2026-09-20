# ABSTAIN 只读复核：根因独立确认 + 红队覆盖评估（DAV-1066）

只读诊断证据，非候选代码 PASS。0 产品/测试修改。代码审核员（DAV-1066）产出，供唯一实现者与项目调度助手使用。

## 证据环境

- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -V` → `Python 3.10.20`，全程 `env -u PYTHONPATH`。
- 代码：复现脚本 import 自 `/private/tmp/ta-serve-4b540b0`（`git rev-parse HEAD` = `4b540b0c9b08d77a12ce7d08cfdf0095288cd350`，生产精确版本）。
- 库：`data/tradingagents.db` SQLite `mode=ro` + `PRAGMA query_only=ON`，零写入。
- 命令：`env -u PYTHONPATH DATABASE_URL=sqlite:////tmp/isolated-abstain-readonly-1066.db .../python /tmp/ta_abstain_repro_20260918.py`。

## 缺陷 A 独立复现确认

LIFECYCLE_MINIMAL 输出：去重前 `CONFIRMED`，`apply_manager_double_count_guard` 将 INV-2 从 `adopted_claim_ids` 剥离到 `excluded_evidence` 后 → `UNRESOLVED` + `unadjudicated_material_claims_adopt:INV-2`。断言 `REPRO_CONFIRMED` 通过。

根因代码级确认（`decision_status.py` 约 427-447 行）：`decided_cids = adopted ∪ partially_adopted ∪ rejected`，而 `known_debate_cids` 由 `claims ∪ summary_map ∪ ver_by_cid ∪ core` 构成——被剥离的 INV-2 仍在 `known_debate_cids`，确定性 decision=adopt → 必然命中 unadjudicated。注意剥离发生在 guard 内、确认评估在其后，不存在“manager 未裁决”——这是**生命周期丢状态**，不是真漏裁决。

真实批次逐条核验（`created_at >= 2026-09-18 09:49:00`，v2 过滤后 24 条）：实测 **8 条**报告含 `unadjudicated_material_claims_adopt:`（42882a59、ba9ff1e5、f5cb5316、318f418c、146b5937、075363c8、373d28d2、00c1fbec），**全部** flagged ⊆ double_count_guard 结构化排除集，0 例外。⚠️ 修复卡写“7 条”——实测 8 条，疑为快照时点漂移（批内可能新增 completed），请实现者以修复时点快照重新核数，不要照抄 7。另有 23 条 ABSTAIN 中仅这 8 条由该缺陷导致，修复 A 不会把整批 ABSTAIN 清零，其余 ABSTAIN 另有原因码，预期管理需注意。

## 缺陷 B 独立复现确认

E04_MINIMAL 四句全部 `accepted=false`：“利好尚未充分定价。”“目前无法确认利好已定价。”“The benefit is not fully priced in.”（三句**误拦**）；“利好已充分定价。”（正确拦截）。E04_REAL：报告 `2d7f66ddf185495f9099ec488916116f` 去告警尾后唯一命中“但未充分定价低估值…避险虹吸效应”→ 误拦。

根因代码级确认（`research_manager.py` 约 471-489 行）两处独立缺陷：
1. `pi_patterns_zh` 子串列表含 `充分定价`/`完全定价`/`已被充分消化` 等**裸子串无否定回看**——“尚未充分定价”命中“充分定价”即判断言。而同一函数下方的 regex `已定价` 分支却带了 `(?<!未)(?<!尚未)(?<!不能确定)(?<!无法确认)` 回看——同一守卫两种标准，子串分支是漏网根因。
2. 英文 regex `(?<!not\s)(?<!un)...priced in` 只看紧邻前词——“not **fully** priced in”中 priced 前是 fully，lookbehind 失效；“not yet priced in / isn't priced in”同理漏判。

## 批次 E-04 命中逐条分类（11/24 命中，勿以偏概全）

| 报告 | 命中语境 | 判定 |
|---|---|---|
| 2d7f66dd | “但未充分定价低估值…” | 误拦（否定句） |
| f8700023 | “增量政策尚未充分定价”“未被充分定价” | 误拦（否定句） |
| ce0fc412 | “属已定价历史数据”“机构减仓属于已定价事实” | **真拦**（肯定断言） |
| 834fb1d1 | “已定价超4个交易日”“已充分定价且Q2净利下滑” | **真拦** |
| b5b1243d | “属已定价事实”“历史已定价” | **真拦** |
| b9e0a30e | “中报利空已充分定价” | **真拦** |
| 6a2ead77 | “已被市场充分反映（已定价）” | **真拦** |
| 9edf48e6 | “属于已定价信息，不计独立票” | 存疑（肯定断言但是自守语境，倾向真拦） |
| b923eef9 | “已充分定价”“属于已充分定价旧闻” | **真拦** |
| a48d3726 | “预亏已充分定价” | **真拦** |
| db874b9b | “空头拿已定价的历史财报压制短线被成功阻击” | **B3 归属场景**：是陈述对方论点被驳，非经理自己断言——修复后仍属灰区，需 B3 显式裁决 |

结论：11 条命中里至多 2 条明确误拦（2d7f66dd、f8700023），8-9 条为真实肯定断言。B 修若用过宽放行会把 8 条真拦误放——这是本批最大回归风险，实现者必须逐条给出修复前后对照。

## 红队场景覆盖评估（A1-A4 / B1-B4）

总体覆盖方向正确，以下缺口/加固点需补：

### A 侧
- ✅ A2 覆盖伪造 excluded ID 后门；加固要求：放行依据必须绑定 `double_count_guard_audit.excluded_claim_ids`（结构化审计）+ claim 真实存在于 debate claims，**不得**信任 manager 自报 `excluded_evidence` 任意字符串/外来 claim_id——否则等于开后门绕过裁决闸。
- ⚠️ 缺口：被排除 claim 本身是 fatal（contradicted/PIT_failed/source_unavailable）的场景未列。正确语义是“已裁决 adopt 但零额外贡献”；若实现把排除项计入 decided 而不复检其确定性 decision=adopt，fatal claim 会被排除路径静默洗白（它不再出现在 adopted，跳过 Row-1 fatal 检查）。应新增场景：fatal/PIT claim 不得被排除机制救活。
- ⚠️ 缺口：重复 claim 落在 `partially_adopted_claims`/`rejected_claim_ids` 的交互（guard 目前只剥 adopted）。修复若引入“excluded ⇒ decided”映射，需确认与 partial/reject 行不冲突。
- ✅ A3 已含首次/重入一致性（`double_count_guard_applied` 幂等分支存在，重入时按 audit.excluded_claim_ids 再剥）。
- ✅ A4 覆盖两独立事件不合并 + 字符串/None 历史排除项保留（`4b540b0` 的 DAV-1064 修复正是非 mapping 容忍，回归须保住）。

### B 侧
- ⚠️ 最关键约束：修复必须是**逐命中点（per-occurrence）否定判定**，不能“文本含未/不→整体跳过”。b5b1243d 同段既有否定又有肯定命中；naive 全文放行会误放 8 条真拦。B2“同段另有肯定断言仍拒绝”隐含此意，建议写明实现约束。
- ⚠️ 否定词表需扩展：现有 regex 只看 `未/尚未/不能确定/无法确认`；批次实测还出现 `未被充分定价`、`并未`、`没有`、`未必`，英文 `not yet`、`isn't`、`far from priced in` 均不在回看内。B1 三句只是最小集，不是完备集。
- ⚠️ 子串列表与 regex 双标问题：修复应对 `pi_patterns_zh` 每个裸子串统一加否定语境判定，而不是只补 regex 分支。
- ✅ B3（引述对方观点）是真实场景：db874b9b 命中即“空头拿已定价…被阻击”的转述句，修复需决定是否放行——建议保守仍拦或按归属判定，逐条写明结论。
- ✅ B4 离线重放仅诊断，禁止改旧报告状态/纳入前向 cohort——边界正确。

### 层语义确认（卡片特别检查点）
- ✅ verified（核验状态）≠ adopted（裁决结果）：`unadjudicated_material_claims_adopt` 的 adopt 指确定性证据层 decision=adopt，不是“经理采纳未核实 claim”。修复不得把 verified 当裁决。
- ✅ excluded≠不存在的 claim：合法排除 = 有来源已裁决 adopt 且零额外贡献；不是把 decided 列表补满强行 VALID，A2 后门约束必须保住。

## 总体评价

两个缺陷根因均独立复现并在 `4b540b0` 源码定位；红队卡方向正确但需补 3 个硬场景：①fatal/PIT claim 不得经排除洗白；②excluded 放行必须绑定结构化审计来源；③B 修必须 per-occurrence 且覆盖扩展否定词表，并以“11 条命中逐条前后对照”作为回归证据。计数差异（卡片 7 条 vs 实测 8 条 unadjudicated）需实现者按修复时点快照重核。本复核不替代候选 SHA 正式审查；候选交付后按常规门禁（同 SHA、RT-FULL 基线对照）重审。
