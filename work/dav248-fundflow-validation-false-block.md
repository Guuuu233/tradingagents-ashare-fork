# DAV-248 资金流模型校验误阻断（单源 r0_net 被 netamount 误解析）

## 精确基线
- `target/codex/dav-4-p2a-trunk@c956e43db0fea1fea88b85103d10218734b2b1c8`
- 生产报告 `cc3b55a8a35b433fb908f8a78af96f66`（600036.SH / 2026-08-20 / 正确账户）

## 已核实事实（不要再做环境考古）

Tushare-东财已经选出合法单源：

```text
selection.status=selected
selected_source=tushare_eastmoney_moneyflow_dc
selected_field=r0_net
selected_value=2.211971
selected_window_days=1
selected_as_of=2026-08-20
direction_allowed=true
hard_guard.blocked=false
```

同日 THS `netamount=-1.928246` 是总净额旁证，产品规则禁止跨字段平均，也不得因此否决东财 r0_net。

但 `smart_money_analyst` 在 LLM 正文后调用 `validate_model_summary`，落库：

```text
validation.status=blocked
unverifiable_fields=["netamount","netamount"]
model.r0_net=2.21 / 2.211971
model.netamount=2.21 / 2.211971
mismatches=[]
```

随后 `consensus_blocked=True`，把 `smart_money_report` 改写成：

```text
资金流来源选择不可用或结构化累计存在冲突；已阻断增持、减持、吸筹方向摘要。
```

并把 `fund_flow_consensus_guard.blocked=true`。研究经理/交易员/风险法官看到该 guard 后短路，用户看起来像“拿不到主力资金、进不了辩论”。

根因：`extract_model_daily_values` / `extract_model_totals` 把“主力资金净额 +2.21亿”同时解析成 `r0_net` 和 `netamount`；结构化 records 只有 r0_net，没有 netamount → unverifiable → `validate_model_summary` 在 `combined_model and unverifiable` 时直接 `blocked`。

## 允许修改
- `tradingagents/dataflows/fund_flow_evidence.py`
- `tests/test_fund_flow_evidence.py`
- 如确有必要：`tradingagents/agents/analysts/smart_money_analyst.py`（不得改产品单源优先规则）

禁止改 `.env`、providers、用户配置、主干、部署、真实 LLM。

## TDD 必须先红后绿

1. 选中字段为 `r0_net`、结构化仅有 r0_net=2.211971、模型正文含“主力资金净额 +2.21亿 / 单日主力净流入 2.21 亿元”时：
   - 不得把同一句解析为 netamount；
   - `validate_model_summary` 不得因 extra/unverifiable netamount 阻断；
   - 状态应为 `matched` 或至少非 `blocked`（无 mismatches）。
2. 模型写“总净流入 2.21 亿”而结构化没有 netamount：不得把总净额冒充主力；也不得仅因旁证字段缺失就否决已选 r0_net。
3. 模型 r0_net 与结构化差超过 0.01 亿：仍 mismatch/blocked。
4. 选中字段为 netamount 时，正文写“主力吸筹/增持”的既有语义阻断保持不变。

## 命令
独立 worktree，宿主 `.venv310`：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_fund_flow_evidence.py tests/test_smart_money_fund_flow_semantics.py -q
TUSHARE_TOKEN='' env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests -q
python -m compileall -q tradingagents/dataflows/fund_flow_evidence.py
git diff --check
```

推送独立分支，给出精确 SHA、父链、RED/GREEN、全量结果。禁止合入主干。
