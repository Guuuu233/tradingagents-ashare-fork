# 证据核验器缺陷审计与修复方案（2026-09-19）

本文件是只读审计结论与施工方案，不是放行依据。写作时未改动任何产品代码、未建卡、未部署。

---

# 第一部分：发现的问题与原因

## 0. 触发与范围

起因是对生产报告 `9e2dd38b79a04819ae619f0078cefbf5`（`600036.SH` / `2026-09-18`）的核查。该报告
`analysis_status=VALID`、`trade_action=WAIT`、`direction=看多`、`risk_status=BLOCKED`，最终
`reason_codes` 为 `manager_terminal, fatal_core_claims:INV-10, upstream_non_executable, risk_verdict:blocked`。

最初的问题是「研究团队给了偏多，为什么下游是 WAIT」。追查结论：**WAIT 不是风控正常拦截，而是证据核验器
误判被逐级放大的结果**。进一步扫描确认这不是孤例，核验器存在三个**不同根因、可独立复现**的缺陷
（其中 A 与 B' 共享前段提取链，详见第二部分「总原则」）。

审计全程只读：生产库以 `mode=ro` + `PRAGMA query_only=ON` 打开，复现使用离线纯函数，零网络、
零模型调用、零写库。

## 1. 缺陷 A：语义类型折叠导致假 contradicted

### 现象

生产库 979 份 completed 报告中（**661 份 `result_data` 解析后为 JSON 对象**，其余 318 份为
JSON `null`，非解析失败），存在 **77 条**
`status=contradicted` 且 `details` 含「数据冲突」的核验记录，分布在 **63 份报告**。
按报告内 `(claim_id, raw, details)` 去重后仍为 77 条，同报告内完全重复记录为 0 条。

措辞分两支：

| 措辞 | 条数 | 报告数 | 产出版本 |
|---|---|---|---|
| `指标 'X' 数据冲突` | 44 | 37 | **当前 HEAD 仍在产出**（`evidence_verifier.py:866` 唯一冲突文案） |
| `关键词 'X' 数据冲突` | 33 | 26 | **历史落库输出**，当前源码已无此文案 |
| 合计 | **77** | **63** | |

**版本溯源（已核）**：`关键词 'X' 数据冲突` 文案由 `7e7b4d2`（DAV-323）引入，由 `b3ba196`
（2026-09-04，见 §1.3）移除。当前 HEAD `rg "关键词 '"` 对该文件零命中，`数据冲突` 仅 866 行一处。
因此这 33 条**不能**作为「当前存在第二条同名冲突分支」的依据，只能用于说明误判形态。

但当前源码仍在 3.2 / 3.3 用 `_METRIC_CANONICAL_MAP` 做 `common_kw` 关键词交集匹配，
该逻辑与 33 条的成因同源，**测试必须覆盖**。

典型误判：

```
INV-10  证据「折损利息约15.3亿元占净利1%」
        → 判定 指标 '净利率' 冲突: 声称 1%, 报告 2.02%
INV-4   证据「个贷息费折损年化利息约15.3亿元」
INV-12  同上
        → 判定 指标 '营收' 冲突: 声称 15.3亿元, 报告 1781.81亿元
其他报告 关键词 '估值'       1050    vs -20
         关键词 'lpr'        22%     vs -15.55%
         关键词 '大单,超大单'  1.8亿   vs +2.77亿
         关键词 '毛利,毛利率'  2.86%   vs 4.74%
```

`1%` 是「损失占净利润的比例」，`2.02%` 在 `macro_report` 原文是「净利润764.45亿元（同比+2.02%）」，
即**净利润同比增速**，而贴的标签「净利率」是第三个量（净利润/营收）。三个互不相干的量被判为同一指标冲突。
`15.3亿元` 是**分项影响额**，`1781.81亿元` 是**总营收**，二者不构成矛盾。

### 原因

`tradingagents/agents/utils/evidence_verifier.py` 的 `_canonicalize_metric`（约 331 行）：

```python
m = _METRIC_CANONICAL_MAP.get(raw_metric, raw_metric)
if unit == "%":
    if m in ("净利", "净利润"):
        return "净利率"
elif unit in ("元", "股"):
    if m == "净利率":
        return "净利润"
```

该函数假定「指标词 + 单位」可以唯一确定一个量。但 `净利 + %` 至少对应三种语义：净利率、净利润同比增速、
某项影响占净利的比例。`元` 那一支同理，不区分分项影响额与总量。

当前源码中关键词匹配复用同一张 `_METRIC_CANONICAL_MAP`（3.2 的 `common_kw` 交集、
3.3 的逐行关键词闸），因此**不能只修 `_canonicalize_metric`**，词表本身的粒度也在影响面内。

## 1.3 关键前情：本类缺陷已被修过一次且未根治

`b3ba196`（2026-09-04）标题即为 *fix(evidence-verifier): bind numbers to metrics and
prevent pseudo contradiction (Card B)*，改动 `evidence_verifier.py` +329/-43 与
`tests/test_evidence_verifier_fairness.py` +107。提交说明声称已做到：

- 将数值绑定到具体财务指标与时间期间，消除跨值指标混淆；
- 把复合证据句拆成原子语句；
- 阻止同一行内不同指标交叉匹配/交叉绑定；
- 百分比冲突要求指标名相同、单位兼容、期间匹配。

它引入了 `BoundNumber`、`_STRICT_METRICS`、`split_compound_evidence`，并锁定 6 个测试
（`test_reproduce_inv6_pseudo_contradiction_falsified_as_unsupported`、
`test_same_line_multi_metric_no_cross_binding_verified`、
`test_same_line_multi_metric_cross_value_negative_not_verified`、
`test_compound_sentence_atomic_evidence_splitting`、
`test_percentage_conflict_requires_same_metric_unit_period`、
`test_true_conflict_positive_case`）。

**它为什么没能根治，是本轮方案的核心约束：**

1. 它把**比较**做严了（`_is_bound_num_contradicted` 要求两侧同为严格指标、同单位、同期间），
   却没动**命名**。`_canonicalize_metric` 把「占净利 1%」与「净利润同比 +2.02%」都改写成
   `净利率`——两侧于是"同指标"，严格性检查被绕过，照样判冲突。
   **结论：在规范化仍会把不同量折叠成同名的前提下，任何加强"指标名必须相等"的做法都无效。**
2. 它防的是**报告行内**的跨指标绑定，没防**证据句内**的跨指标绑定。
   INV-6 的 `45.40元` 被绑到句尾的 `pb`，正是证据句内跨绑，当前仍可复现。
3. 它没有动 `_is_bound_num_match` 的宽松短路（见 §2「连带缺陷」）。

因此本轮修复**不得**沿用"继续加强指标名相等判定"这条路径，必须先解决规范化的有损折叠与
绑定的就近猜测。新卡必须保留上述 6 个既有测试不退化。

## 2. 缺陷 B'：指标绑定退化，同一事实得出相反结论

### 现象

同一份报告内，两条引用**完全相同两个事实**的证据得到相反结论：

```
INV-6  「基本面报告显示2026H1每股净资产45.40元对应PB仅0.89倍且经营现金流达3046亿元」 → verified
INV-10 「基本面报告显示2026H1每股净资产45.40元且现金流达3046亿元提供极强反脆弱缓冲」 → unsupported
```

两个数都在 `fundamentals_report` 中实际存在：`45.40` 出现 4 次；现金流原文为
「2026H1 经营活动产生的现金流量净额达到 **3,046.11 亿元**」。

### 原因（已离线复现）

复现脚本 `/tmp/ta_repro_inv_20260919.py`，使用 `.venv310`（Python 3.10.20）直接调用
`EvidenceFactualTruthEvaluator.evaluate_single_evidence`，输入为生产库只读取出的七份报告原文：

```
INV-6  keywords: ['pb','现金','现金流']
       45.40元  → metric='pb'        ← 蹭到句尾的 PB
       0.89倍   → metric='pb'
       3046亿元 → metric='现金流'
       ⇒ verified（3.3 多行聚合命中）

INV-10 keywords: ['现金','现金流']
       45.40元  → metric='现金流'     ← 错绑
       3046亿元 → metric='现金流'
       ⇒ unsupported（fall through 到兜底分支）
```

根因：**「每股净资产」在 `evidence_verifier.py` 中完全不存在**（全文件 grep 零命中），
`_METRIC_CANONICAL_MAP` 有 `每股收益→eps`、`市净率→pb`、`资产负债率`、`现金流`，但没有「每股净资产」。

词表缺项时，代码不产出「无法确定指标」，而是把数字绑到**句中碰巧出现的其他已知指标**上：

- INV-6 句尾有「对应PB仅0.89倍」，`45.40元` 被绑成 `pb`。绑定是错的（45.40 是每股净资产不是 PB），
  但报告中那一行恰好同时含 `45.40` 与 `0.89` 且带 pb 关键词，于是 3.3 聚合命中 → verified。
- INV-10 无 PB 锚点，只剩 `现金流`，`45.40元` 被绑成 `现金流`。3.3 要求命中行与证据共享 canonical
  关键词，找不到带现金流关键词且值≈45.40 的行，2 个数只命中 1 个 → 聚合失败 → 915 行兜底 unsupported。

即：**指标绑定取决于句子里碰巧还有什么别的词**。INV-6 的 verified 属于「误绑后蒙对」，同样不可靠。

### 连带缺陷：匹配判据偏斜

仅让未知指标返回 `metric=None` 不足以修复。`_is_bound_num_match`（445-458 行）：

```python
ev_strict = ev_bn.metric if ev_bn.metric in _STRICT_METRICS else None
l_strict  = l_bn.metric  if l_bn.metric  in _STRICT_METRICS else None
if ev_strict and l_strict and ev_strict != l_strict:
    return False
return True
```

只有**两侧都在 `_STRICT_METRICS` 内且不相等**才否决。任一侧落在该集合之外即短路 `return True`，
退化为纯数值比对。因此通配符行为不限于 `metric=None`，而是**全部非严格指标共享**。

反向地，`_is_bound_num_contradicted`（461 行起）要求两侧都必须是严格指标才可能判冲突。

两者合起来，整个匹配器的判定是偏斜的：

```
指标不确定 → match 放行（宽） → 假 verified
指标都严格 → contradicted 生效（严）→ 叠加缺陷 A 的量纲折叠 → 假 contradicted
```

**不确定性一律偏向「通过」，确定性反而偏向「冲突」。** 这解释了为何同一批报告里既有 77 条假冲突，
又有把真实事实判成无支撑的情况——它们是同一套偏斜判据的两端。

`_STRICT_METRICS`（281-286 行，33 项）同时决定「谁能被判冲突」（A 的触发面）与「谁会被当通配符」
（B' 的触发面），同一集合在两个方向上语义相反，需一并审定。

## 3. 缺陷 C：严重度契约失效

`evidence_verifier.py` 产出的核验记录带 `is_fatal` 字段，且该字段是活的（630 行存在
`"is_fatal": True` 的赋值点，用于 source_unavailable 类严重幻觉）。

但 `decision_status.py` 的 372 与 512 行：

```python
if v.get("status") in {"contradicted", "source_unavailable"} or v.get("is_fatal"):
```

OR 的左半边使 `status == contradicted` 单独即可成立，右半边只能加码不能减码。因此
`is_fatal=False` 无法表达「此冲突不致命」。

上述报告中 22 条核验记录 `is_fatal` **全部为 False**，包含那 3 条 contradicted，但
`_is_claim_fatal` 仍将 INV-10 判为 fatal。

对照 `claim_cluster.py:187` 的用法 `if st in {"verified",...} and not is_fatal`，
该模块把 `is_fatal` 当**独立否决位**。同一字段在两个模块语义不同，契约未统一。

## 4. 三条缺陷如何汇合成 WAIT

```
[A] 核验器把「占净利1%」与「净利润同比+2.02%」当作同一指标 → 假 contradicted
      ↓
[提示词 zh.py:274 + 一致性硬闸 evidence_verifier.py:1946]
   提示词要求「存在矛盾冲突证据…坚决予以驳回，记录于 rejected_claim_ids」；
   硬闸则在 adopted/partial 一侧反向设限——采纳 contradicted claim 会触发
   manager_consistency_hard_gate。两者夹击下，模型在正常遵循路径上被**强制趋同**于
   把 INV-10 写入 rejected_claim_ids。这是强约束，不是逻辑上的绝对唯一解：
   `rejected_claim_ids` 仍来自 LLM 输出解析，模型理论上可两边都不列。
      ↓
[C] decision_status.py:369 `counts.contradicted > 0` → _is_claim_fatal = True
    （is_fatal=False 被 OR 绕过）
      ↓
   INV-10 ∈ focus_claim_ids=["INV-9","INV-10"]，即 P0-5b 定义的 core claim
      ↓
   core_fatal 命中 → fatal_core_claims:INV-10 → CONFIRM_UNRESOLVED
      ↓
   upstream_non_executable → trade_action=WAIT
      ↓
   D-009 §5「INVALID/ABSTAIN/NO_TRADE/WAIT 必须排除并计数」→ 不计入 H1b 合格样本
```

需要澄清两点常见误读：

1. **INV-10 不是空头风险 claim。** 它是 Bull Analyst 的多头论据，正文为
   「极端情景压力测试证实0.89倍PB提供坚实底线，监管出清反哺龙头」，那句个贷新规是它的**支持证据**
   （论证损失很小、已被定价）。
2. **「总监驳回」与「确认闸判 fatal」不是两个独立判断。** `rejected_claim_ids` 确实来自模型
   `MANAGER_VERDICT` 输出的解析（`evidence_verifier.py:1792`），不是代码拷贝；但在 contradicted
   分支上模型受提示词与硬闸双向强约束，正常路径下会趋同于复现核验器的 reject 集合。本报告中
   `rejected_claim_ids` 与机械 `decision==reject` 集合逐位相等，实测 `True`。
   因此二者是**同一个错误信号被读了两遍**，不是两层独立判断在冲突。

## 5. 为什么不能先给 core_fatal 加 rejected 豁免

`decision_status.py:397`：

```python
def _get_claim_decision(cid: str) -> str:
    if _is_claim_fatal(cid):
        return "reject"
```

现有豁免判据是 `cid in rejected_ids and _get_claim_decision(cid) == "reject"`。若照搬到 `core_fatal`：

- 任何 fatal claim → `_get_claim_decision` 无条件返回 `"reject"`；
- 同时 contradicted 使其必然进入 `rejected_ids`；
- 豁免条件**恒成立**。

结果是 `fatal_core_claims` 变成永不触发的死代码。这不是放宽，是彻底关闭该闸，真实的核心矛盾
（例如引用不存在数据源的核心 claim）也会一并溜过。

另注：P0-5b 施工卡（`work/issue-p0-5b-confirmation-gate.md:49`）原文是
「存在未决焦点/核心 claim…**或仍有 fatal/contradicted 未裁决**→ UNRESOLVED → WAIT」，
带「未裁决」限定。当前代码里不存在独立于 fatal 的「已裁决」概念，因此该限定无法忠实实现。
要动 `core_fatal`，必须先拆掉上述自指短路。

（补正：D-009 正文全文 14 行 1285 字符，关键词普查 `核心/core/fatal/致命/驳回/rejected/豁免/claim`
均为 0 次，`WAIT` 仅 1 次出现在 §5。core/non-core fatal 细则写在 P0-5b 施工卡，不在 D-009 正文。）

## 6. 附带发现：数据卫生（不阻塞本轮，但污染统计）

对 661 份 `result_data` 为 JSON 对象的 completed 报告扫描（979 份中其余 318 份为 JSON `null`）：

| 项 | 数量 | 说明 |
|---|---|---|
| `reason_codes` 混入整句中文自由文本 | 186 / 661（28%） | 判定口径见下 |
| 其中单句「顺势进攻：主力资金净流入且筹码集中度良好，多头占优，建议跟随」 | 42 / 661 | 含报告 `9e2dd38b`，其 `reason_codes` 实测为 `social_archive_missing` / 该句 / `manager_terminal` / `fatal_core_claims:INV-10` / `upstream_non_executable` / `risk_verdict:blocked` |
| `falsification_conditions` 为空 | 404 | |
| 其中 `analysis_status=VALID` 且证伪条件为空 | **31 / 47** | 三分之二的有效样本没有证伪条件 |

**「自由文本」判定口径**：递归收集 `result_data` 中所有 `reason_codes` 列表项，
若某条目同时满足「含中文全角 `：` 或 `，`」且「长度 > 12 字符」，即判为自由文本。
该口径偏宽，会漏掉不含全角标点的长码，也可能误收个别长机读码；引用该数字时须标注本口径。
复现脚本：`/tmp/ta_audit_final_20260919.py`。

**检索方法注意（三条纪律，互不通用）**

1. `result_data` 以 `\uXXXX` 转义存储中文，SQL `LIKE '%中文%'` 必然零命中
   （实测：原始文本直接含「顺势进攻」为 `False`，含 `\u` 转义为 `True`）。
   所有中文内容统计必须先 `json.loads` 再匹配。本次审计该坑已踩过两次
   （`LIKE '%数据冲突%'` 返回 0，实际为 77 条）。
2. `reason_codes` **必须递归扫描**：它散落在嵌套层，本报告那句自由文本实测位于
   `.analyst_traces[7].reason_codes`，不在顶层列表。只看顶层会严重漏计。
3. `evidence_verification` **必须固定走一条路径**：`result_data` 中存在两份**逐字相同的镜像**
   —— 根级 `evidence_verification` 与 `investment_debate_state.evidence_verification`，
   实测各 315 份报告持有，`9e2dd38b` 两处均为 `len=22` 且内容相同（`True`）。
   递归收集会使计数翻倍。本方案 77 条统计固定走
   `investment_debate_state.evidence_verification`，任选另一条路径结果相同。

`reason_codes` 混入自由文本会污染一切基于该字段的统计。
VALID 样本缺证伪条件对回测校准池是硬伤。

## 7. 本轮确认正常的部分

- **DAV-1086 资金流修复确实生效**：`fund_flow_consensus_guard` 回读 `blocked=False`、
  `data_conflict=False`，选中 `tushare_eastmoney_moneyflow_dc/r0_net=0.40185`，
  THS `netamount=0.1155` 进入独立字段，不再冒充主力口径。
- **DAV-1068 去重裁决态修复生效**：本报告未再出现 `unadjudicated_material_claims_adopt`。
- **数据缺失上报合规**：11 条 `data_gaps` 中 9 条为【数据获取失败】显式上报
  （fund_flow_board、insider_transactions、limit_up_ladder、hot_stocks、share_pledge、
  northbound_flow、scale_metrics、社交归档、板块资金流向），符合 AGENTS.md §3.4，非静默返空。
  但一份建立在 9 个数据源失败之上的 VALID 样本，其口径应在使用时标注。

## 8. 尚未查清（不得据此施工）

- INV-2 与 INV-9 为 `decision=adopt`、证据 2/2 全验证，却未进入 `adopted_claim_ids`；
  `excluded_evidence` 显示 9 条 claim 被同一句 `double_count_guard: 同一事件已被既有预测/事件栏位计入`
  排除。一个事件闸一次踢掉 11 条中的 9 条是否合理，未核查。
- 报告 `8b9fb26d7106443296c7363d4f161616` 的 `symbol` 为**空字符串**且状态 failed，空 symbol 落库路径未查。
- `_STRICT_METRICS` 的准入标准（哪些指标应进入严格集）未定。

---

# 第二部分：修改计划

## 总原则

1. **上游优先。** 先修证据核验器，再动裁决层。在核验器输出可信之前，任何下游豁免都是在掩盖假阳性。
2. **一刀一个关注点，但按真实耦合切。** A 与 B' 是**不同根因、可独立复现**，但共享前段提取链
   （`extract_bound_numbers`、`_METRIC_CANONICAL_MAP`）——B' 的错绑会直接改变 A 后续看到的指标
   （INV-10 的 `45.40元` 绑成 `现金流` 即是实例）。因此二者**合并为一张 matcher 卡**，内部分两个
   关注点、分两个 commit；强行拆成两卡会造成白名单重叠，违反写集互斥。C 独立成卡。
3. **不确定即拒绝。** 所有判据改为白名单式：语义无法确定时返回不可比/unsupported，不得默认放行，
   也不得猜测绑定。
4. **先 RED 再 GREEN。** 每张卡先写能复现缺陷的失败测试，确认其在父版本失败、修复后通过。
5. 本轮**不改** `decision_status.py` 的 `core_fatal`，不改产品阈值，不放宽任何安全闸，不开 H1b。

## 卡 1：匹配器契约（合并 A + B'）

**合并理由**：A 与 B' 共享 `extract_bound_numbers` 与 `_METRIC_CANONICAL_MAP`，拆成两卡必然
白名单重叠。本卡一个实现者串行处理，**两个关注点分两个 commit**，最终候选一次同 SHA 审查。

**白名单**：`tradingagents/agents/utils/evidence_verifier.py` 的
`_canonicalize_metric`、`_METRIC_CANONICAL_MAP`、`_STRICT_METRICS`、
`extract_bound_numbers`、`_extract_metric_keywords`、`_is_bound_num_match`、
`_is_bound_num_contradicted`，以及 `tests/test_evidence_verifier_fairness.py`。
不得改 `decision_status.py`、不得改提示词、不得改硬闸。

### Commit 1 — 语义类型（原缺陷 A）

- 指标规范化不得只依据「词 + 单位」。引入**语义类型**维度，定义为**封闭枚举**，至少含：
  `绝对额` / `分项影响额` / `总量` / `占比` / `同比增速` / `比率` / **`未知`**。
  枚举必须显式包含「未知」态，不得用 `None` 兼作「未抽到」与「抽到但无法归类」两种含义。
- **「分项影响额 vs 总量」不可由指标名与单位推断**（`15.3亿元` 与 `1781.81亿元` 单位相同、
  指标名同为「营收」，仍不可比）。该区分须另有依据（如句法角色、修饰词「折损/影响/贡献」等）；
  依据不足时一律归入 `未知`，走不可比路径。
- `净利 + %` 不得无条件归为 `净利率`；`净利率 + 元` 不得无条件反向归为 `净利润`。
- 语义类型无法确定时返回**不可比**，不得猜测归属。
- **不得**采用「继续加强指标名相等判定」的路径——`b3ba196` 已证明该路径在规范化有损折叠的
  前提下无效（见 §1.3）。
- 不得放宽真实冲突：同指标、同单位、同语义类型、同期间下的数值矛盾仍须判 contradicted。

### Commit 2 — 绑定与匹配判据（原缺陷 B'）

两条缺一不可：

1. **绑定侧**：`extract_bound_numbers` 在无法确定某数字对应指标时，必须产出显式「未绑定」标记，
   **禁止绑到证据句中其他碰巧出现的指标**（`b3ba196` 只防了报告行内跨绑，未防证据句内跨绑）。
   同时补齐「每股净资产」等常用科目。补词表只是缓解，绑定逻辑不改则下一个缺项会以同样方式出错。
2. **匹配侧**：`_is_bound_num_match` 改为白名单式——只有指标、单位、语义类型、期间**四者均兼容**
   才返回 True。未绑定与非严格指标一律不通过，不得维持现行「只要不明确矛盾就通过」。
   仅做第 1 条无效：`45.40元` 变为未绑定后，454-458 行的短路仍会放行。
3. `_STRICT_METRICS` 的准入标准须在本卡明确写出并审定——它同时决定 A 的触发面（谁能被判冲突）
   与 B' 的触发面（谁被当通配符），两个方向语义相反。

### RED 用例（全部取自生产真实样本）

| 用例 | 输入 | 期望 |
|---|---|---|
| A1 | 「折损利息约15.3亿元占净利1%」 vs `净利润764.45亿元（同比+2.02%）` | 不判 contradicted（占比 vs 增速，不可比） |
| A2 | 「个贷息费折损年化利息约15.3亿元」 vs `营收1781.81亿元` | 不判 contradicted（分项影响额 vs 总量） |
| A3 | 构造：同指标同单位同期间的真实矛盾 | **仍**判 contradicted，不得放过 |
| B1 | 生产 INV-10 原句 | verified（两个事实在 `fundamentals_report` 中均真实存在） |
| B2 | 生产 INV-6 原句 | verified，且 `45.40元` 的绑定指标**不得**为 `pb` |
| B3 | 同一句仅增删句尾「对应PB仅0.89倍」 | 两种句式结论**一致**，不得翻转 |
| B4 | 证据数值确实不在任何报告中 | **仍**判 unsupported，不得因放宽绑定而蒙混通过 |
| B5 | 未绑定指标 + 数值巧合相等，且证据**未**命中 3.1 快速路径 | **不得** verified |

B3 是本卡最关键的钉子，同时锁住绑定不确定性与「误绑后蒙对」的假 verified。

> ⚠️ **B5 必须排除 3.1 快速路径**，否则会与合法的精确事实匹配冲突。`evaluate_single_evidence`
> 步骤 3.1 有两个放行分支，**均先于任何指标绑定生效**：
> ```python
> if raw_text in report_body or any(len(p) >= 4 and p in report_body
>                                   for p in raw_text.split("，")):
>     → VERIFIED
> ```
> 第一个分支是证据全文直接出现在报告中；第二个分支更宽——**证据按「，」切分后任意一段
> 长度 ≥4 且出现在报告中即整条 verified**。B5 的构造必须同时避开这两个分支，
> 否则测试会误判合法行为为缺陷。

> ⚠️ **两个 INV-6 不是同一个样本，勿混淆。**
> - **生产 INV-6**（本卡 B2/B3 用）：`600036.SH` 报告 `9e2dd38b` 中的
>   「基本面报告显示2026H1每股净资产45.40元对应PB仅0.89倍且经营现金流达3046亿元」。
> - **既有测试 INV-6**（`test_reproduce_inv6_pseudo_contradiction_falsified_as_unsupported`）：
>   「2026Q2应收账款619.55亿元同比大增17.10%，严重背离营收3.55%的微弱增速」，
>   断言其不得与 `macro_report` 的「概率：25%」判冲突，期望 `STATUS_UNSUPPORTED`。
>
> 两者 claim_id 相同纯属巧合，样本、指标、期望完全不同。实现者不得把新用例写进旧测试函数，
> 也不得因新用例改动旧测试的断言。

### 不得退化的既有测试

`tests/test_evidence_verifier_fairness.py` 当前 17 个用例全部保持通过，其中 `b3ba196` 锁定的 6 个
必须逐项确认未退化：`test_reproduce_inv6_pseudo_contradiction_falsified_as_unsupported`、
`test_same_line_multi_metric_no_cross_binding_verified`、
`test_same_line_multi_metric_cross_value_negative_not_verified`、
`test_compound_sentence_atomic_evidence_splitting`、
`test_percentage_conflict_requires_same_metric_unit_period`、
`test_true_conflict_positive_case`。

## 卡 2（C）：`is_fatal` 严重度契约

**白名单**：`tradingagents/agents/utils/decision_status.py` 的 372、512 行判据，
`evidence_verifier.py` 的 996、1301、1997 行消费点，`claim_cluster.py:187`，
以及上述各处的语义说明与对应测试。

**消费者清单（须逐个审，不得只改 decision_status）**

| 位置 | 当前用法 |
|---|---|
| `decision_status.py:372` | `status in {contradicted, source_unavailable} or is_fatal` — OR，is_fatal 只能加码 |
| `decision_status.py:512` | 同上 |
| `evidence_verifier.py:996` | `status == SOURCE_UNAVAILABLE or is_fatal` |
| `evidence_verifier.py:1301` | 同上 |
| `evidence_verifier.py:1997` | `item.get("is_fatal") or status == SOURCE_UNAVAILABLE` |
| `claim_cluster.py:187` | `st in {verified,...} and not is_fatal` — 当**独立否决位**用 |

`claim_cluster` 与其余五处语义相反，这正是契约未统一的实证。

**测试矩阵**：须补 `status` × `is_fatal` 的四格组合，至少覆盖
`contradicted + is_fatal=False`、`contradicted + is_fatal=True`、
`source_unavailable + is_fatal=False`、`source_unavailable + is_fatal=True`，
并对选定契约逐格断言期望值。

**前置**：本卡是**契约决定**，不是纯实现。施工前须先定：`is_fatal` 是独立严重度位，
还是 `status` 的冗余标记。

- 选「独立严重度位」→ 372/512 两处的 OR 必须拆开，`is_fatal=False` 应能阻止 contradicted 升级为 fatal；
- 选「冗余标记」→ 应删除该字段，避免它挂在输出里制造「校验器说了不致命」的假象。

两者不可并存。当前 `claim_cluster.py:187` 已按前者使用，`decision_status.py` 按后者使用。

**依赖**：本卡应在卡 1 合入后施工。核验器仍在产假 contradicted 时调整严重度契约，会掩盖上游问题。

## 卡 3（数据卫生，可并行，低优先）

**白名单**：`reason_codes` 的产出点，以及 `falsification_conditions` 的产出与落库路径。

- `reason_codes` 必须是机读码，禁止写入整句自由文本（当前 28% 报告被污染）。
  叙述性内容应另置字段。
- VALID 样本缺 `falsification_conditions` 的成因需查清（31/47）。本卡先出诊断，
  是否补齐属产品决定，不在本卡自行决定。

本卡与卡 1/卡 2 无文件重叠，可并行派工，但**不得与证据核验修复混 commit**。

## 不在本轮范围

- `core_fatal` 的 rejected 豁免与生命周期设计——须待卡 2 完成且 `_get_claim_decision`
  的 fatal→reject 短路拆除后单独设计。
- 提示词 `zh.py:273-274` 与一致性硬闸 `evidence_verifier.py:1946` 的强制关系——
  属产品语义，本轮不动。
- 历史报告重判。已落库的 ABSTAIN/WAIT 报告**不得**因修复而改判为 VALID，
  也不得计入前向 cohort。离线重放仅用于诊断。
- H1b 门槛评估与样本计数。修复合入并受控部署、跑出新的受控真实分析后再重算。
  当前这条 WAIT 不计入。

## 验收顺序

1. 卡 1 的两个关注点分别实施，先 RED 后 GREEN，各自独立 commit。
2. 候选与直接父在同一解释器、同一命令下做 RT-FULL 无筛选全量对照，
   只承认**零新增失败**；定向绿不得替代全量。
3. 同 SHA 由代码审核员只读复审。
4. 合入后独立执行部署门（备份、完整性核对、`/healthz` 精确回读、启动恢复证据）。
5. 部署后跑一次受控真实分析，按**四条并列**验收，不得简化为「contradicted/unsupported 消除」
   （那会诱导实现者修成放水）：
   - 目标假冲突消失（A1/A2 形态不再产生 contradicted）；
   - **真实冲突仍被保留**（同指标同单位同语义同期间的矛盾仍判 contradicted）；
   - **真实不存在的证据仍为 unsupported**（不得因放宽绑定而蒙混 verified）；
   - 与直接父版本做 RT-FULL 全量对照，**零新增失败**。
6. 卡 2（`is_fatal` 契约）在卡 1 合入后施工。
7. 全部完成后才重算 H1b。当前那条 WAIT 报告不计入，也不得回头改判。

## 固定基线与边界（写作时实测）

- 远端 origin 主线 `codex/dav-4-p2a-trunk` 与本地 HEAD 均为
  `aa2ccd5707ead6829eb350cd81d9115bf7138179`。
- 线上服务运行中，`/healthz` 精确回读 `commit_sha=aa2ccd5707ead6829eb350cd81d9115bf7138179`。
- 生产库 `data/tradingagents.db`：reports 1740 / completed 979，
  SHA256 `79f1a3dd823e257a10899247381fba3c6b9205f1dc6ac49db9ee3a956d16a0c9`
  （服务在线写入中，该值会随新报告变化）。
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，
  实测 `Python 3.10.20`，所有命令须 `env -u PYTHONPATH`。
- 所有测试 `DATABASE_URL` 指向隔离临时库，禁止触碰生产写入。
- 生产库只读方式：`mode=ro` + `PRAGMA query_only=ON`。
  **禁止**对生产库直接运行 `scripts/verify_h1b_gates.py --db-path`，
  其 `_ensure_report_schema` 会建立可写连接（2026-09-19 01:49 已发生过一次，
  经核对 schema 与计数未变、WAL 为 0 字节，无实际损伤，但属边界越界）。
  正式筛选应对 `.backup()` 副本执行，或直接只读调用纯函数
  `filter_v2_completed_reports` / `filter_reports_by_cohort`。
- 离线复现脚本：`/tmp/ta_repro_inv_20260919.py`，纯函数、零网络、零模型调用，
  可作为卡 1 Commit 2 的 RED 起点。
- **`PROJECT_STATE.md` 已过期**：其首行记录远端 trunk 为 `a290f18`，而实测远端与 `/healthz`
  均为 `aa2ccd5`。该文件工作区零差异（`git status` 为空），属**已提交的过期快照**，
  不是未提交改动。本方案不修改它；引用当前态须以实测为准。
- **存在并行施工**：`work/` 下今日新增 `fund-flow-field-semantics-audit-20260919.md`、
  `issue-fund-flow-r0net-semantic-mismatch-20260919.md`、`h1b-v1-cohort-sample-plan-20260919.md`、
  `h1b_gates_now.json`、`tradingagents.db.bak-20260919-021456-deploy-a290f18` 等非本方案产物。
  经核对当前**无 matcher 相关产品文件的工作区改动**，但派工前仍须重查一次，避免写集冲突。
- 本方案不授权合入、不授权部署、不授权改历史报告、不授权开 `credit_weighting`。
