# 证据核验器 Matcher 契约修复（缺陷 A + B' 合并卡，双 commit）

任务来源：`work/2026-09-19-evidence-verifier-audit-plan.md`「卡 1：匹配器契约」整节 + 「§1.3 关键前情」，
派工约束见 `work/2026-09-19-evidence-verifier-dispatch-brief.md`。若你的 worktree 中无上述文件，
以本卡正文为准——契约与红线已全部内联。

## 背景（已四方定案，不要重新讨论）

证据核验器有三个不同根因、可独立复现的缺陷：A 语义类型折叠（`_canonicalize_metric` 把
「占净利1%」与「净利润同比+2.02%」折叠成同名 `净利率` → 假 contradicted，生产库已见 77 条）、
B' 指标绑定退化（词表缺项时数字绑到句中碰巧出现的其他指标 → 同一事实 INV-6 verified /
INV-10 unsupported）、C 严重度契约（不在本卡）。A 与 B' 共享前段提取链
（`extract_bound_numbers`、`_METRIC_CANONICAL_MAP`），故合并一卡；拆开会白名单重叠。

已定案边界：不得先给 `core_fatal` 加 rejected 豁免（自指闭环，会使该闸变死代码）；
不得重走 `b3ba196`「加强指标名相等判定」的路径（已失败过一次，见下）。

## 白名单（写集互斥，越界即退）

仅允许改动 `tradingagents/agents/utils/evidence_verifier.py` 的
`_canonicalize_metric`、`_METRIC_CANONICAL_MAP`、`_STRICT_METRICS`、`extract_bound_numbers`、
`_extract_metric_keywords`、`_is_bound_num_match`、`_is_bound_num_contradicted`，
以及 `tests/test_evidence_verifier_fairness.py`。
**不得**改 `decision_status.py`、不得改提示词、不得改硬闸（含 `evidence_verifier.py:1946`
一致性硬闸区域）、不得动 `core_fatal` 生命周期。

## 关键前情：本类缺陷已被修过一次且未根治（方案 §1.3 原文）

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
3. 它没有动 `_is_bound_num_match` 的宽松短路（见「连带缺陷」）。

因此本轮修复**不得**沿用"继续加强指标名相等判定"这条路径，必须先解决规范化的有损折叠与
绑定的就近猜测。新卡必须保留上述 6 个既有测试不退化。

## Commit 1 — 语义类型（原缺陷 A）

- 指标规范化不得只依据「词 + 单位」。引入**语义类型**维度，定义为**封闭枚举**，至少含：
  `绝对额` / `分项影响额` / `总量` / `占比` / `同比增速` / `比率` / **`未知`**。
  枚举必须显式包含「未知」态，不得用 `None` 兼作「未抽到」与「抽到但无法归类」两种含义。
- **「分项影响额 vs 总量」不可由指标名与单位推断**（`15.3亿元` 与 `1781.81亿元` 单位相同、
  指标名同为「营收」，仍不可比）。该区分须另有依据（如句法角色、修饰词「折损/影响/贡献」等）；
  依据不足时一律归入 `未知`，走不可比路径。
- `净利 + %` 不得无条件归为 `净利率`；`净利率 + 元` 不得无条件反向归为 `净利润`。
- 语义类型无法确定时返回**不可比**，不得猜测归属。
- **不得**采用「继续加强指标名相等判定」的路径——`b3ba196` 已证明该路径在规范化有损折叠的
  前提下无效。
- 不得放宽真实冲突：同指标、同单位、同语义类型、同期间下的数值矛盾仍须判 contradicted。

## Commit 2 — 绑定与匹配判据（原缺陷 B'）

两条缺一不可：

1. **绑定侧**：`extract_bound_numbers` 在无法确定某数字对应指标时，必须产出显式「未绑定」标记，
   **禁止绑到证据句中其他碰巧出现的指标**（`b3ba196` 只防了报告行内跨绑，未防证据句内跨绑）。
   同时补齐「每股净资产」等常用科目。补词表只是缓解，绑定逻辑不改则下一个缺项会以同样方式出错。
2. **匹配侧**：`_is_bound_num_match` 改为白名单式——只有指标、单位、语义类型、期间**四者均兼容**
   才返回 True。未绑定与非严格指标一律不通过，不得维持现行「只要不明确矛盾就通过」。
   仅做第 1 条无效：`45.40元` 变为未绑定后，454-458 行的短路仍会放行。
3. `_STRICT_METRICS` 的准入标准须在本卡明确写出并审定——它同时决定 A 的触发面（谁能被判冲突）
   与 B' 的触发面（谁被当通配符），两个方向语义相反。

## RED 用例（全部取自生产真实样本）

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

## 不得退化的既有测试

`tests/test_evidence_verifier_fairness.py` 当前 17 个用例全部保持通过，其中 `b3ba196` 锁定的 6 个
必须逐项确认未退化：`test_reproduce_inv6_pseudo_contradiction_falsified_as_unsupported`、
`test_same_line_multi_metric_no_cross_binding_verified`、
`test_same_line_multi_metric_cross_value_negative_not_verified`、
`test_compound_sentence_atomic_evidence_splitting`、
`test_percentage_conflict_requires_same_metric_unit_period`、
`test_true_conflict_positive_case`。

## 证据门（直接约束）

1. **先 RED 再 GREEN。** 每个关注点先写能复现缺陷的失败测试，确认其在**直接父版本**失败。
2. **RT-FULL 无筛选全量对照。** 候选与直接父同解释器、同命令，只承认**零新增失败**。
   定向绿不得替代全量；不得沿用「固定 deselect 即绕过死锁」的失效口径；
   跑全量不得设低于 45 分钟的上限（主线实测约 28 分钟）；
   若见进程高 CPU 长时间不退出，是 baostock EOF 忙循环，立即中止。
3. 候选交付后由 `代码审核员` 做**同 SHA 只读复审**（实施与审查分离，调度会另派）。
4. **交付须含**：完整候选 SHA、直接父、白名单 diff、`git diff --check`、逐场景实测命令与输出、
   全量基线对照、以及明确的「未合入未上线」状态。
5. 复现起点：离线脚本 `work/ta_repro_inv_20260919.py`（纯函数、零网络、零模型调用）
   可直接作为 Commit 2 的 RED 起点；该文件不在产品白名单内，不得改动它。

## 环境铁律

```sh
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
# 实测 Python 3.10.20，报告须贴 -V 输出
```

- 禁用任务工作区 `.venv` 与系统解释器作门禁证据（系统 3.14 依赖集不等价）。
- 所有测试 `DATABASE_URL` 指向隔离临时库。
- 生产库只读方式：`mode=ro` + `PRAGMA query_only=ON`（或 `.backup()` 副本）。
- **禁止**对生产库运行 `scripts/verify_h1b_gates.py --db-path`（会建可写连接，已有越界前例）。

## 红线（本次不含这些授权）

- 不得合入主干、不得部署、不得重启线上服务。
- 不得写生产库、不得改历史报告、不得把已落库的 ABSTAIN/WAIT 改判为 VALID。
- 不得开 `credit_weighting_enabled`、不得开社交 active、不得碰 Cookie 与真实采集。
- 不得改用户配置、模型绑定、提示词正文、3:1 辩论轮次。
- 不得放宽任何安全闸来让测试变绿。

## 完成后

精确 mention `项目调度助手` 报告候选 SHA 与证据包（D-032 流程），由调度派 `代码审核员`
做同 SHA 只读复审。**不要**自行派审查、不要合入、不要部署。
