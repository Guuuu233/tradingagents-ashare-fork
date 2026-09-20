# 数据卫生诊断：reason_codes 自由文本混入 + VALID 缺 falsification_conditions 成因

任务来源：`work/2026-09-19-evidence-verifier-audit-plan.md`「卡 3（数据卫生）」+「§6 附带发现」，
派工约束见 `work/2026-09-19-evidence-verifier-dispatch-brief.md`。若你的 worktree 中无上述文件，
以本卡正文为准。**本卡只出诊断，不改任何产品代码、不改数据、不建子卡。**

## 诊断范围（两项成因，都要落到代码位置）

1. **`reason_codes` 混入整句中文自由文本（实测 186/661，28%）的成因。**
   找出自由文本被写入 `reason_codes` 的产出点（文件:行），说明哪条链路把叙述性内容塞进了
   机读码字段。典型样本：`result_data.analyst_traces[7].reason_codes` 出现整句
   「顺势进攻：主力资金净流入且筹码集中度良好，多头占优，建议跟随」（42/661 份含此句，
   含报告 `9e2dd38b79a04819ae619f0078cefbf5`）。
2. **`analysis_status=VALID` 样本缺 `falsification_conditions`（实测 31/47）的成因。**
   查清该字段的产出与落库路径：是产出端没生成、还是落库端没带上，31/47 的缺口集中在哪一段。

**自由文本判定口径（引用数字时须标注）**：递归收集 `result_data` 中所有 `reason_codes`
列表项，条目同时满足「含中文全角 `：` 或 `，`」且「长度 > 12 字符」即判为自由文本。
口径偏宽：会漏掉不含全角标点的长码，也可能误收个别长机读码。
复现脚本：`/tmp/ta_audit_final_20260919.py`（若不存在，按下述纪律自行写只读统计）。

## 检索纪律（三条，方向互相相反，踩过坑）

| 对象 | 纪律 | 原因 |
|---|---|---|
| 中文内容统计 | 必须先 `json.loads` | `result_data` 以 `\uXXXX` 转义存储，SQL `LIKE '%中文%'` 恒零命中（实测 `LIKE '%数据冲突%'` 返回 0，实际 77 条） |
| `reason_codes` | **必须递归扫描** | 散落嵌套层，实测在 `.analyst_traces[7].reason_codes`，只看顶层严重漏计 |
| `evidence_verification` | **必须固定一条路径** | 根级与 `investment_debate_state` 下是逐字相同的镜像（各 315 份），递归会翻倍；统计固定走 `investment_debate_state.evidence_verification` |

## 硬边界

- **只诊断**：产出诊断报告，定位到文件:行与成因链；不得改产品代码、不得写库、不得改历史数据。
- 生产库只读方式：`mode=ro` + `PRAGMA query_only=ON`，或 `.backup()` 副本后读副本。
- **禁止**对生产库运行 `scripts/verify_h1b_gates.py --db-path`（其 `_ensure_report_schema`
  会建可写连接，2026-09-19 已有一次越界前例）。
- 是否补齐 `falsification_conditions` 属产品决定，不在本卡自行决定，也不要顺手修。
- 与证据核验修复（并行中的 Matcher 卡）**无文件重叠**，也不得与之混 commit。

## 环境铁律

```sh
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
# 实测 Python 3.10.20，报告须贴 -V 输出
```

- 禁用任务工作区 `.venv` 与系统解释器作证据（依赖集不等价）。
- 任何测试/脚本 `DATABASE_URL` 指向隔离临时库。

## 交付

诊断报告写清：①自由文本写入 `reason_codes` 的产出点（文件:行）与触发链路；
②`falsification_conditions` 的产出/落库路径与 31/47 缺口的成因定位；
③复核用统计口径与原始计数；④解释器 `-V` 输出与生产库只读证据。
完成后精确 mention `项目调度助手` 登记（D-032 流程）。
