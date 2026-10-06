# A3 口径拟稿 v4（冻结候选，待评估师复看）

按评估师 11:12 复看两处补正（§4 公式笔误、§3 谓词未排除"部分支持"）逐项修订；其余已认可项原样保留、不回退。冻结通过后即开工改代码；冻结后本文件全文以附件留卡。

## 1. 样本池 = clean 池（完整过滤管线 + 隔离台账）

逐行调用 `filter_v2_completed_reports`（`shadow_credit.py:1045` 起）完整管线，串行经过 Stage 1.0 双档拆包 → Stage 2 结构化资格 → Stage 3 D-009 §5 eligible → Stage 3.5 HOLD 语义隔离 → Stage 4 价格口径隔离。保留 `excluded_counts` + pipeline ledger + `exclusion_reasons`（per-unit `@hz` 键）三份台账；不新增"eligible→clean"改名谓词，分析型 HOLD 留在 clean 分母。

## 2. 场次归属（"≥3 个不同的有效 Opening 战场"）

对每个 clean 档位单元分别判 bull、bear 侧：

- 该单元 `investment_debate_state.claims` 中 `speaker_key/stance` 属该侧（`_is_bull`/`_is_bear` 同口径）且 `stage='opening'` 的论点，取 `battlefield ∈ VALID_BATTLEFIELDS`（`capital_flow`/`sentiment_theme`/`price_volume`/`macro_policy`/`fundamentals`，`debate_utils.py:37–44`）**按战场集合去重**；
- **该侧 Opening 战场覆盖数 ≥ 3**——同一战场多条、空 battlefield、非法 battlefield 均不增加覆盖数（与 `debate_utils.py:1148–1168` 协议硬闸同语义）；
- **且**该侧"产生 Verified Claim"——≥1 条该侧 claim 满足 §3 的唯一 claim 级判据。

满足 → 该侧 +1。双侧 `(1,1)` / 单侧 `(1,0)`/`(0,1)` / 均不达 `(0,0)`；同侧每单元最多贡献 1 场。`winner/tie/trade_action` 不参与侧归属（评估师已认可）。

## 3. claim 级判据（唯一谓词 `is_verified_claim`，场次判定与 ≥100 条两处调用）

对一条 claim（及其同一 `claim_id` 在 `evidence_verification` / `claim_evidence_summary` / claim 自身字段的全部记录）：

1. `evidence_verification` 中该 `claim_id` 至少 1 条 `status='verified'`；
2. 该 `claim_id` 下**无任何** `status='contradicted'` 证据（一票否决）；
3. `is_observation_or_hypothesis_claim(claim)` 为假（`evidence_verifier.py:5070`：观察/假设类不算）；
4. **已判"部分支持"者排除**：该 `claim_id` 在 `claim_evidence_summary[cid].decision` 为 `partial`/`reject` 时不计（正式分类字段即 `summary_map[*].decision`，`evidence_verifier.py:6230–6267`；`partial` = 混合证据部分通过/观察假设核验通过不升级）。**字段缺失时按该 `claim_id` 的 verified/unsupported/contradicted 条目数自行归算**——`verified ≥1 且 contradicted = 0 且 unsupported = 0` 视作可计（等价于"全证据 verified"或"无 summary 但有干净 verified 证据"），`unsupported > 0` 即落入部分支持→不计；`summary` 存在时以 `summary.decision` 为准，不自作主张。

四条同真才计；每条 claim 在同一单元同侧最多贡献 1 次（`claim_id` 去重）。**场次判定（§2）与"各侧 Verified Claims ≥100"复用此同一函数，无第二口径、不在统计处另补过滤。**

**参考列（不判达标）**：另按 `research_manager.py:2729` 宽口径（同 `claim_id` 有 ≥1 条 verified 即算，不查 contradicted/partial/观察假设）并列输出 `*_ref_loose` 一栏，标注"参考，不判达标"。

## 4. 读数与判定项（A3 只判三项）

| 判定项 | 口径 | 阈值 | 读数 |
|---|---|---|---|
| 各侧有效场次 | 累计 clean 单元，分侧 §2 判定 | 各 ≥25 | `bull_sessions`/`bear_sessions` + 差值 |
| 近 50 场多空比例 | 每 cohort 内按 `(created_at, report_id)` 稳定升序取最近 50 个 clean 单元；**`bull_share = N_bull_window / (N_bull_window + N_bear_window)`**（修正 v3 笔误 `N_b/(N_b+N_b)`） | ∈ [40%,60%] | `bull_share_pct` + 窗口内分侧场次；分母 0→N/A，<50→`partial_window` |
| 各侧已核实论点 | §3 谓词按 `claim_id` 去重、分侧累计 | 各 ≥100 | `bull_verified`/`bear_verified` + 差值 |

**cohort 隔离**：所有读数按 DAV-1322 四元键 `dmv:ecv:pbv:hz` 分桶，`short`/`medium` 永不混同一达标池；`legacy_unversioned[:hz]` 单列。

**观察项（不判达标）**：累计 `|N_bull−N_bear|`（按裁定降级为观察，不做窗口/累计两口径展示）；`*_ref_loose` 宽口径列。

## 5. 新增对照样例（测试构造）

按评估师 11:12 列出的反例集合实现，且全部跑过 `filter_v2_completed_reports` 管线后再计：

- 非对称比例：`(N_bull_window, N_bear_window) = (40, 10)` → `bull_share = 80%`（不达标，验证公式不是 `N_b/(N_b+N_b)` 的恒 50% 假绿）；
- 部分支持：某 claim 2 verified + 1 unsupported + 0 contradicted，`claim_evidence_summary.decision='partial'` → 不计 Verified Claim；
- verified+contradicted：同一 claim_id 同时有 verified 与 contradicted → 不计；
- 观察/假设：`is_observation_or_hypothesis_claim` 为真 → 不计；
- 多证据只计 1：同一 claim_id 5 条 verified → `verified_claims +1`（不 +5）；
- cohort 窗口隔离：同 cohort 内取最近 50 单元，跨 `short`/`medium` 不混池；
- 同战场三条不达标：3 条 opening 论点 `battlefield` 相同 → 覆盖数 1 <3，场次不计；
- eligible≠clean：经 Stage 3.5/4 隔离的 eligible 单元不进 clean 池。

## 6. 其余打回条目确认（延续，不回退）

- 交易日历缺失 → 退出码 2，删 `pd.bdate_range` 回退；
- `load_reports` 逐行流式，每行 `json.loads` 后抽取指标字段、即时丢弃原 `result_data`；交付时报 `--all-history` 实测峰值内存（≤4 GB）；
- 档位标签统一 `dmv:ecv:pbv:hz` 四元（`decision_model.v1:evidence_contract.v2:price_basis.vendor_qfq:short|medium`），删 `horizon:short` 写法及文件头旧说明；
- 新增测试：缺日历退 2；A3 构造样本对照 `filter_v2_completed_reports` 管线结果（覆盖 §5 全部反例）。

—— v4 冻结候选稿。请评估师复看；通过后冻结、落实附件并开始改代码。
