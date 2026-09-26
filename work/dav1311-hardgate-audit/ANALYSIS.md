# DAV-1311 研究经理自洽硬门精确率审计（误杀率）

- 语料：`work/hist-bench-20260926/audit-corpus-hardgate.txt`，47 档（daily 3 / batch 44），sha256 `88aa5402…`（已核对一致）。
- 数据：生产库只读抽取（`mode=ro`），卷宗 `dossiers/*.json`，逐例判定 `verdicts.json`（人工），汇总 `out/report.md` + `out/summary.json`（`summarize.py` 可复跑）。
- 判定基准（冻结）：结论是否依赖未核实/被否决内容、与已核实证据冲突、或证据跳跃 → 正确拦截；仅消费已核实子事实（与 `evidence_basis` 的 `rejected_subfact` 通道实质等价）却因清单归类被判、或机械词面命中 → 误拦；无法判定不计分母。
- `price_basis_gate_blocked`（5 例）属下游附加码（DAV-1261 `DOWNSTREAM_CODES` 口径），逐例保留但不计入硬门判定。

## 一、总结果（档位级）

**正确拦截 18/47 = 38.3%，Wilson 95% CI [25.8%, 52.6%]；误拦 29/47 = 61.7%；无法判定 0。**

即：多数 ABSTAIN 挡下的裁决在实质内容上没有问题——它们败在账本归类而非证据缺陷。

> 口径说明（供复核参考）：若以「账本不变量被违」即算正确拦截的更严格口径（清单归类错误本身即违规），则 reject_in_partial 与 coverage 两类的误拦全部翻正，档位级正确拦截为 40/47 = 85.1%。本报告主口径按卡内冻结定义（看结论实际依赖）执行。

## 二、命中类型 × 判定（命中级）

| 命中类型 | 命中 | 正确 | 误拦 | 正确率 Wilson95% |
|---|---|---|---|---|
| 部分采纳含 semantic_decision=reject（reject_in_partial） | 46 | 3 | 43 | 7% [2%,18%] |
| E-04 守卫 | 25 | 13 | 12 | 52% [33%,70%] |
| 全额采纳含未核实混合证据（coverage<100%） | 25 | 5 | 20 | 20% [9%,39%] |
| 正文标注 partial claim「证据充分」 | 3 | 3 | 0 | 100% [44%,100%] |
| 部分采纳含覆盖率<67% | 1 | 0 | 1 | 0% |
| 全额采纳 semantic_decision=reject | 3 | 3 | 0 | 100% [44%,100%] |
| 全额采纳 partial_threshold | 2 | 0 | 2 | 0% |
| 正文/机读胜负矛盾（winner_conflict） | 1 | 0 | 1 | 0% |

### 「部分采纳含 reject」（24 档/46 claim 命中）全审结论

- 误拦 43/46：经理将 `reject_with_supported_subset`/`reject_no_supported_subset` 的 claim 放入 `partially_adopted_claims`，但 `evidence_basis` 记录的消费物全部为 `verified_subfacts`——与系统自身允许的 `rejected_subfact` 通道实质完全等价，仅账本归类不同。典型如 `f4f0cac6` INV-5、`21e993fd` INV-4/INV-9。
- 正确拦截 3/46：`28f6a1e0` INV-4/INV-10（裁决 reason「年线双顶假突破」直接采纳被拒命题）、`acbb32d0` INV-11（裁决 target=43.50 即该 claim 断言的「探底 43.5 元」）。
- 边界观察：`21e993fd` INV-4 同时挂在 `partially_adopted_claims` 与 `basis_from_rejected_claim_ids`（双列），属归类瑕疵，未造成结论污染；`8c028552` INV-1 命题级被拒但裁决断言的「分红未定价」原子恰在 eb verified 内，记误拦并留复核标注。

### 「全额采纳含未核实混合证据」（coverage<100%，25 命中）

- 误拦 20：经理已把未核实原子逐一列入 `excluded_evidence`（如 `1312d27a` 的 '0.45'/'0.89'），claim 语义命题全 supported，实质等同部分采纳，仅未换清单。
- 正确拦截 5：未核实原子**未**被剔除即随全额采纳入账——`fb53ab62` INV-3 '18亿'、`18271017-s` INV-3 '27.24元'、`af25901e-s` INV-3 '0.87' 与 INV-11 '48.00'、`31f3b289-s` INV-3 整段市场报告原子。
- **核验器伪原子（补充2，DAV-1184 四形态核查）**：逐例核对 unsupported 原子，确认 1 例伪原子——`fdc9352b` INV-4 的 '200' 由证据串「10EMA/50SMA/**200SMA**死叉」拆出的均线窗口数。其余 unsupported 原子均为真实数值/文本片段（价格、百分比、报告句），非四形态。另有多例 bare 数字（'0.45'、'2.66%' 等）虽原子化但与原始证据文本可对应，不计伪原子。**结论：伪原子在本批中占比极低（1/25 命中），DAV-1184 修复优先级建议维持低**。

### E-04 复核（DAV-1261 再抽样，25 命中全分四类，≥10 例要求已覆盖）

| DAV-1112 类别 | 命中 | 判定 |
|---|---|---|
| 裸断言（应拦） | 13 | 正确 13 |
| 条件/情景推演 | 5 | 误拦 5 |
| 附可回溯依据的定价推断 | 2 | 误拦 2 |
| 明示「状态未知仅作降权」启发式 | 2 | 误拦 2 |
| 否定句机械命中（「无/缺少超预期」） | 2 | 误拦 2 |
| 词面机械命中（「完全定价权」≠priced-in） | 1 | 误拦 1 |

**对 DAV-1261 的复核结论**：priced_in/beat 命中中「裸断言」占 13/25（52%），DAV-1261「绝大多数为真实裸断言」在更细四类口径下**部分成立**——裸断言仍是最大单一类且全部应拦，但另一半命中是条件句、否定句、UNKNOWN 降权与词面误命中，属机械误伤。E-04 的误拦集中在守卫把「提及关键词的句子」一律当断言。

### 其余小类

- 「证据充分」文字冲突 3 例全部正确（`be99f770-s` INV-3、`0c1d9966-m` INV-8/INV-12：正文宣称证据充分，与 partial 核验结果真实冲突）。
- winner_conflict 唯一例 `d93a03ef` 为误拦：正文综合裁决「多头微弱胜出(bull)」与机读块一致，检查把「空头方/空头主张」等词面命中为正文判空。

## 三、误拦模式与确定性规则建议（仅建议，不实施）

1. **reject→partial 归类通道对齐**：硬门在判 `reject_in_partial` 前，先查 `evidence_basis.items[cid].verified_subfacts` 是否 ⊆ claim 的 verified_evidence 且裁决 reason/plan/dispute 文本不含该 claim 的 unsupported 命题锚点；满足则视同 `rejected_subfact` 通道（降级为归类告警），不满足再硬拦。
   - 回归样例：`f4f0cac6:short_term` INV-5（应放行）；`28f6a1e0:medium_term` INV-10（reason 含「双顶假突破」仍须拦）。
2. **coverage 剔除确认**：全额采纳的 claim 存在未核实原子时，若**全部** unsupported_evidence 原子逐字出现于 `excluded_evidence`，判为归类瑕疵（应入 partial）而非结论污染；存在任一未剔除原子才硬拦。
   - 回归样例：`1312d27a:short_term` INV-1/INV-3（应放行）；`fb53ab62` INV-3（'18亿' 未剔除，仍须拦）。
3. **E-04 断言性判定**：命中句先经三层豁免——(a) 含「若/是否/或/候选/失效条件/假设/情景」的条件框架；(b) 含「无/缺乏/缺少/未见」+关键词的否定表述；(c) 明示「未知/UNKNOWN/仅作降权」；另将 priced-in 词表中的「定价」限定为谓词性「已定价/priced-in」，排除「定价权/定价能力」。
   - 回归样例：`7177a072:medium_term`（UNKNOWN 应放行）；`dc1dc116:medium_term`（情景句应放行）；`f4f0cac6:short_term`（裸断言仍须拦）。
4. **winner_conflict 正文锚定**：只匹配显式裁决语（「综合裁决…X胜/裁定 X 胜/winner=X」），不匹配分歧表角色词（空头方/空头主张）。
   - 回归样例：`d93a03ef:short_term`（应放行）。
5. **核验器原子化边界（DAV-1184 低优先级）**：均线窗口 token（`N日SMA/NSMA/NEMA`）拆出的裸整数原子不计入 unsupported。
   - 回归样例：`fdc9352b:medium_term` INV-4 '200'（应放行）。

## 四、复跑方式

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
    work/dav1311-hardgate-audit/extract.py \
    --db /Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db \
    --corpus work/hist-bench-20260926/audit-corpus-hardgate.txt \
    --out work/dav1311-hardgate-audit/dossiers
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
    work/dav1311-hardgate-audit/summarize.py
```

`verdicts.json` 为人工逐例判定产物（审计本体），脚本重跑不覆盖。辅助：`render.py`（卷宗渲染）、`e04_context.py`（E-04 命中句抽取）。
