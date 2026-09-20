## 更正与补充调查：`ce9e7a59` 的 ABSTAIN 已查明，与本轮修复无关（附实测证据）

### 更正（独立核验指认，属实）

验收汇报把 `ce9e7a59` 表述为「completed、4/4 分析师真实产出」的干净成功分析，**漏掉了决策层仍弃权的事实**。实测生产库（含 WAL 只读副本）：

```
id=ce9e7a59  status=completed  analysis_status=ABSTAIN  decision=NO_TRADE
error=NULL
```

### ABSTAIN 根因（实测定位）

`final_trade_decision` 原文：

> 资金流来源选择 guard 已阻断：风险计划不得批准增持、减持或吸筹方向。

即审计报告 §1 早已定案的**资金流守卫短路**（`smart_money_report` 为空可佐证）。该问题在交接说明 §3.5 中被明确列为「**不在本轮范围**，与数据源无关，另行处理」。

**结论：ABSTAIN 是预期行为，与全球指数修复（DAV-1098）无关、与财报接入（DAV-1099）无关。** 宏观报告（`macro_report`，4702 字节）中全球指数逐条渲染、来源标注、逐指数基准日全部正常——四项验收的取证对象是数据层产物，不依赖最终决策状态，验收结论成立。

### 主干推进 `ddbf0c1` 核验

合入后主干新增 12 个提交（全部为 docs / gitignore / AGENTS.md 增补，作者 David Liu），`git diff --stat 4297715..ddbf0c1 -- tradingagents/ api/ tests/` **输出为空——零代码改动**。服务跑在 `4297715` 与当前主干代码完全一致，部署后验收四项证据不受影响，**无需重新部署、无需重跑验收**。

### 遗留清单（明确不结案项）

- 值域防线与新鲜度闸的**拒绝分支至今只有单元测试覆盖，无真实数据命中**——第三轮如实记「未覆盖」，本条保留在遗留清单，不随四项结案。
- 资金流守卫短路是独立的已知问题（不在本轮范围），建议另行建卡处理。
- 启动脚本已固化为 `scripts/start_service.sh`（含 Tailscale 网段、no_proxy 全清单、`env -u PYTHONPATH`、DATABASE_URL、自动停旧进程与 healthz 等待），今后重启一律走脚本，不再手工补环境。
