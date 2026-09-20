# P1-M 指标语义单问题返修

## 精确基线

- 父候选：`target/agent/1/01a03077-dav367@33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b`
- DAV-371 只读审计：BLOCK，报告已给出真实 Golden 复现。
- DAV-370 正在另一分支只修改 `propagation.py` / `trading_graph.py` / protocol test，本卡与其文件范围不得重叠。

必须使用 `multica repo checkout https://github.com/Guuuu233/1.git --ref 33c6e6bf7a9a14ef1381c2ae98b9ea9b70a5d98b` 建立 fresh 独立 checkout。禁止进入或修改 `/Users/davidliu/Documents/TradingAgents-AShare` 宿主树。

## 严格允许范围

只允许修改：
- `tradingagents/agents/utils/debate_metrics.py`
- `tests/test_debate_metrics.py`
- `tradingagents/agents/utils/offline_ab_harness.py`（仅输出标签，不改评估算法）
- `tests/test_offline_ab_harness.py`（仅标签契约测试）

禁止修改 agent_states、propagation、trading_graph、api/main、researcher、conditional_logic、manager、prompt、DB schema、前端、provider、配置、用户设置。

## 严格 TDD 垂直切片

### S1：真实轮次字段

先写 RED：
- 输入真实形状 `round_messages`，只含 `debate_round`、`message_index` 和 evidence/claims；
- 000333 Golden 的后续轮次 denominator 必须 >0，不能返回 zero_denominator；
- 优先级：显式 `debate_round` → `round_index`（兼容夹具）→ 根据 `message_index` 推导；非法/缺失时 typed no_data，不猜任意轮次。

确认 RED 后最小 GREEN。不得只改测试。

### S2：数字事实去污染

先写 RED，至少覆盖：
- `600900.SH`、`000333.SZ` 不得产出 600900/333；
- `2026-08-23`、`2026年8月21日` 不得拆成 2026/8/23/21；
- `INV-1`、`CH-2` 不得产出 1/2；
- `45.0-50.0元` 必须作为一个区间事实或明确排除，不能拆成两个独立事实；
- 正常 `15.5倍`、`100亿元`、`+30.5%` 保留。

最小 GREEN：前置清洗+明确正则契约。禁止通过 float 转换抹除前导零。需要复用项目已有数值规范化 helper时先完整读调用点，不另造第二套口径。

### S3：字段完整率的合法空值

先写 RED：
- probability=None 且 extraction_note 含 `概率未提供/未提取` → `legitimate_empty`，计入业务契约合规分子；
- HOLD target=None 且 note含 `观望不设目标价` → `legitimate_empty`，计入合规分子；
- 无 note 的 None 仍是 missing；
- 输出 details 明确 `legitimate_omissions`，numerator/denominator/rate数学一致。

本阶段指标定义为“字段契约完整率”，不是物理非空率；note 仅接受明确白名单文案，不接受任意文本洗白。

### S4：A/B输出标签

同一 legacy result_data只覆盖协议版本时，输出必须带：
- `comparison_mode=structural_compatibility_baseline`
- note说明 delta=0 不代表真实 v2 质量等同

不得改变 metrics 计算或伪造候选数据。

## 验收

- 每个切片真实 RED→GREEN原始输出；
- 原 `tests/test_debate_metrics.py` 与 `tests/test_offline_ab_harness.py` 全绿；
- 3份 Golden 只读回放，打印清洗前后 denominator 的可复算摘要；
- DAV-371 三个复现全部修复；
- compileall、diff-check；
- 相关 P1-M tests和 replay全绿；
- 不需要单独跑全量（最终 sibling 组合树统一跑全量）；
- 推送新远端 branch/SHA，明确未合入/未重启/未上线。

变更文件必须不超过上述4个。不要 mention 项目调度助手。