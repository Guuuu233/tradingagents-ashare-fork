# P0：多空辩论轮次信息增量与防复读硬闸

**基线/线上：`7ebd00d847c588f79a0c569f74170cf59fa1e652`。独立分支，不合主干。**

## 真实E2E复现

京东方报告 `c262252e`：6条accepted valid、后5轮均respond+target对方，协议链已成功。但Bear第2轮和第3轮两条new claims逐字完全相同：
- “8/21地量反弹系买盘枯竭，50日均线压制下破位风险极高”
- “铜价上涨侵蚀毛利且海外需求承压，极端情景下杀至4.86元”
同侧最高相似度=1.0，违反每轮必须有新观点、不能复读。

## 契约

1. 在接受 DEBATE_STATE 前，对同一speaker历史claims/上一轮cleaned_prose做确定性信息增量检查。
2. new claim 与同侧历史claim：规范化后完全相同或高相似（字符/词n-gram阈值建议>=0.82）视为duplicate；每条新claim都重复则协议无效。
3. 本轮至少一条真正新增claim，且需提供至少一个历史未出现的证据实体/数值/因果链；仅改写同义词、交换顺序、复用相同证据不算新增。
4. 对手回应允许引用旧claim，但new_claims必须说明新的反驳角度或新增证据；target规则继续保留。
5. 重复协议错误沿用同轮一次重试；重试Prompt列出重复claim和要求的新信息维度。两次失败typed DebateProtocolError，horizon failed。
6. `round_messages`记录 information_gain_score、duplicate_claim_ids/text、new_evidence_count；attempt trace保留。
7. Research Manager前置：同侧跨轮最大相似度不得>=阈值，若存在重复accepted消息直接阻断且LLM调用0。
8. 不使用外部embedding/网络，不新增重依赖；中文英文均可确定性测试。
9. 不改轮数3/1、模型、provider、用户配置。

## 白名单
- debate_utils.py
- bull/bear researcher（重试错误信息）
- research_manager.py（前置增量闸）
- agent_states.py（字段）
- 对应tests，可新增信息增量测试

## 验收
- 完全重复claim红；同义轻改但证据相同红；新增数据/新因果链绿。
- 同轮首次重复、第二次新增→count仅+1。
- 连续重复2次→horizon failed，manager未调用。
- 6轮fixture每轮都有增量，最大同侧相似度<阈值。
- `.venv310`核心+全量、compileall、diff-check；精确SHA。禁止@调度助手。
