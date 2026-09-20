# 设计：decision_model_version 与 H1b cohort 隔离

**状态：** Cursor 已冻结（2026-09-04）。吸收 DAV-600 Conditional Pass 修正。禁止为实现本设计而补 H1b 样本。禁止 `due_count==0` 记 PASS。

**对照主干：** `e10b106df9d3173258b0a3fefc90ba7f3559f109`

## 冻结修正（覆盖下文旧表述）

1. **Cohort 归组主键是三元组**，不是四字段全等：`decision_model_version` + `evidence_contract_version` + `price_basis_version`。
2. `generated_by_commit_sha` 只做溯源。Gate JSON 回显该 cohort 的 SHA 集合摘要。禁止用单 SHA 当 cohort key（否则 N≥60 无法跨 commit 累积）。
3. **旧 121 样本永久 `decision_model.legacy_unversioned`**。禁止回填成 `decision_model.v1`。`e10b106` 之后新生成的样本才能标 `v1`。
4. `verify_h1b_gates.py` 未传 `--cohort` 必须 fail-closed、非零退出；禁止默认全库扫描。
5. 空 cohort 是 FAIL，不是 PASS。
6. 线上 `resolve_claim_credit_weights_for_manager` 遇未标记/混世代必须降级平权 1.0，不得中断交易主链路；这不等于打开 `credit_weighting_enabled`。
7. 本设计不授权 PIT 实现、不授权新闻召回实现、不授权部署。

## 问题

后续施工会改变裁决语义（confirmation lifecycle 已合入；数值匹配、价格口径、证据独立性还在后面）。当前 `scripts/verify_h1b_gates.py` + `evaluate_h1b_system_gates` 对库内 completed 报告做门槛，**没有** `decision_model_version` / `evidence_contract_version` / `price_basis_version` / `generated_by_commit_sha` 过滤。代码库内尚无这些字段名。

现有 121 个 v2 样本必须保留，但不得与新规则样本在未声明 cohort 时混算。在隔离落地前 **暂停 H1b 补样本**。

## 字段（分析完成时写入 result / debate 元数据，只增不改历史语义）

| 字段 | 含义 | 首次约定 |
|---|---|---|
| `decision_model_version` | 四元组 + confirmation + 动作/风控一致性规则世代 | 现网（含 e10b106 confirmation lifecycle）记为 `decision_model.v1`；未打标的历史样本视为 `decision_model.legacy_unversioned` |
| `evidence_contract_version` | 证据匹配、独立性、计票合同 | 现网（595 合入前）`evidence_contract.v0`；595 合入后另立 `v1`，不得 silently 改 v0 样本 |
| `price_basis_version` | 价格口径合同 | 在 PIT 卡落地前：`price_basis.unspecified`（不得假装已是 PIT_ADJUSTED） |
| `generated_by_commit_sha` | 生成该报告的 40 字符 SHA | 新跑样本必填；旧样本可空，空则只能进 `legacy_unversioned` cohort |

价格三口径（未来，不在本设计实现）：`RAW` / `PIT_ADJUSTED` / `TOTAL_RETURN`。PIT 未落地前禁止把前复权序列标成 PIT。

## Gate 合同（实现卡另立，本卡只定规则）

1. `verify_h1b_gates` **必须**接受显式 cohort 参数（CLI 或配置），四元组至少要指定 `decision_model_version`；未指定则 **拒绝运行**（非零退出），不得扫描全库当一次门槛。
2. 一次评价只纳入四字段全部匹配的样本。缺字段的旧样本只能通过 `--cohort=legacy_unversioned` 显式选出。
3. 禁止默认合并 `legacy_unversioned` 与任何 `vN`。
4. `due_count==0` 不得记 PASS；空 cohort 是 FAIL。
5. 报告 JSON 必须回显所用 cohort 与 SHA 集合摘要，避免「121 混世代」被误读为新规则样本。
6. 不改 `credit_weighting_enabled` 默认；隔离本身不能当成 ELIGIBLE。

## 与 D-006 的关系

D-006 的 7 维门槛仍有效，但分母改为 **单一 cohort**。旧 121 的 FAIL/KEEP_FALSE 作为 `legacy_unversioned`（或打标后的 `decision_model.v1` 回填 cohort，若回填必须另卡、只写元数据、不重跑裁决）的历史记录保留。

## 实现边界（后续功能卡，非本设计卡）

- 写入点：分析完成持久化路径（ReportDB result_data），原路径加字段，禁止平行 `_v2` 报告表。
- 读取点：`scripts/verify_h1b_gates.py`、`evaluate_h1b_system_gates` 调用方。
- 测试：未传 cohort 必失败；混世代必排除；空 cohort 非 PASS；旧样本仍可被显式 legacy 选出。
- 禁止：删 121 样本、改 T+5 窗口、编造 probability、部署、开权重。

## 本卡交付

只读审阅本设计：指出与现有 `shadow_credit.py` / `verify_h1b_gates.py` 的冲突。不写功能代码。通过后另立实现卡，parent 为当时主干 tip。
