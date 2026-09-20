# is_fatal 严重度契约修复（卡 2）— 待 DAV-1088 合入并过同 SHA 复审后派工

**状态：已备待派。** 契约方向已由总工 2026-09-19 拍板（见下），派工前置条件未满足前不得施工。
任务来源：`work/2026-09-19-evidence-verifier-audit-plan.md`「卡 2（C）：is_fatal 严重度契约」+
总工决定；派工约束见 `work/2026-09-19-evidence-verifier-dispatch-brief.md`。

## 总工决定（2026-09-19）：`is_fatal` = 独立严重度位

理由（总工原话要点）：

- 代码已明确把普通 `contradicted` 产出为 `is_fatal=False`；
- `source_unavailable` 才由核验器显式标为 `True`；
- 删除字段会让所有 `contradicted` 都自动升级为 fatal，反而固化当前误杀；
- `claim_cluster.py` 已按独立严重度位消费它。

**语义矩阵（本卡必须逐格实现并断言）：**

| status | is_fatal | 期望 |
|---|---|---|
| `contradicted` | `False` | 证据仍判拒绝/不采纳，但**不得**升级为整条 claim fatal |
| `contradicted` | `True` | fatal |
| `source_unavailable` | `False` | 按显式 `is_fatal` 判定（不升级） |
| `source_unavailable` | `True` | fatal；生产者对真正的不可用源幻觉继续输出 `True` |

补充约束：

- `pit_failed` 仍是**独立、无条件的硬闸**，本卡不得触碰；
- 不在本卡处理 `core_fatal` 的 rejected 生命周期；
- 本卡修正的是「普通事实冲突被错误升级为 fatal」——不会把 INV-10 直接变成 BUY；
  Matcher 合入并重新分析后，再判断剩余 WAIT 是否真实合理。

## 白名单

`tradingagents/agents/utils/decision_status.py` 的 372、512 行判据，
`evidence_verifier.py` 的 996、1301、1997 行消费点，`claim_cluster.py:187`，
以及上述各处的语义说明与对应测试。

## 消费者清单（须逐个审，不得只改 decision_status）

| 位置 | 当前用法 |
|---|---|
| `decision_status.py:372` | `status in {contradicted, source_unavailable} or is_fatal` — OR，is_fatal 只能加码 |
| `decision_status.py:512` | 同上 |
| `evidence_verifier.py:996` | `status == SOURCE_UNAVAILABLE or is_fatal` |
| `evidence_verifier.py:1301` | 同上 |
| `evidence_verifier.py:1997` | `item.get("is_fatal") or status == SOURCE_UNAVAILABLE` |
| `claim_cluster.py:187` | `st in {verified,...} and not is_fatal` — 当**独立否决位**用 |

`claim_cluster` 与其余五处语义相反——按选定契约统一为独立严重度位。

## 测试要求

`status` × `is_fatal` 四格组合全覆盖（`contradicted`/`source_unavailable` × `False`/`True`），
按上表逐格断言；并保留对既有真致命场景（不可用源幻觉）的 fatal 断言不退化。

## 前置依赖与施工顺序

1. **DAV-1088（Matcher 契约卡）合入且过同 SHA 只读复审后**方可派本卡——核验器仍在产假
   contradicted 时调整严重度契约，会掩盖上游问题（方案「卡 2」依赖节）。
2. 施工仍按证据门：先 RED 后 GREEN、RT-FULL 候选与直接父零新增失败、同 SHA 复审由
   `代码审核员` 执行（D-014，不派独立代码审核员）、实施与审查分离。
3. 环境铁律同 DAV-1088：`env -u PYTHONPATH` + `.venv310/bin/python`（3.10.20），
   隔离 `DATABASE_URL`，生产库只读 `mode=ro` + `query_only`。
4. 红线同 DAV-1088：不合入、不部署、不重启、不写生产库、不改历史报告、不动
   `credit_weighting_enabled`、不放宽安全闸、不改提示词与 3:1 轮次。

## 完成后

精确 mention `项目调度助手` 报告候选 SHA 与证据包，由调度派同 SHA 复审。
