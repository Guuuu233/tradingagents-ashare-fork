# P0：分析师深度质量闸抗空话升级

**基线：`1eb280b35f436c2ff1ece00a448ad7483c86eff9`。独立分支，不合主干。不得改 Prompt（DAV-320 正在改 zh/en）。**

## 已复现缺陷

`report_quality_gate.check_report_keywords` 只检查“传导”与“联动/外溢/时滞”子串。一句“宏观政策传导与市场联动值得关注”即可假通过，不能证明四层联想、量化关系、时滞和真实数据缺口。

## 契约

1. 新增角色感知的确定性深度评分器，至少覆盖 macro、fundamentals、news、volume_price；不能只看关键词。
2. macro：需要有效实体/数字（指数点位或百分比）、至少一条因果/箭头式传导链、方向/量级、时滞或明确数据缺失；外盘失败仍禁止平滑。
3. fundamentals：需财务数字、产业链/议价权、至少一条敏感性关系（成本/销量变动→毛利/利润影响）或明确【数据缺失】；不能用一句口号替代。
4. news：需事件事实/来源或缺失、直接影响、间接上下游/同行/国际传导、时滞/验证节点。
5. volume_price：需量价数字/日期、确认或异常、供需/阶段判断、后续验证条件；并检查是否利用 Phase1 上下文产生至少一处跨维度引用或明确无可用上下文。
6. 质量结果必须结构化写入 `data_failure_ledger`：role、score、failed_dimensions、reason；幂等，不重复。
7. 合理部分数据缺失应 fail-closed 记账但不伪造；不要因单一缺源把 completed 改 failed。若已有一次有限重试框架可安全复用则用；否则保持非阻断记账，明确测试。
8. 负向测试：空话“传导联动值得关注”必须失败；堆砌数字无因果链失败；只写因果无证据/缺失标记失败。
9. 正向测试：真实风格宏观/基本面/新闻/量价样例通过；全部缺失但诚实标注时按契约 partial，不应被判编造。
10. 不改用户配置、分析顺序、轮数、provider。

## 白名单

- `tradingagents/graph/report_quality_gate.py`
- `tradingagents/graph/trading_graph.py`（仅挂钩，如必要）
- `tests/test_report_quality_gate.py`
- 可新增 `tests/test_analyst_depth_quality_gate.py`

## 验收

`.venv310` 定向 pytest、compileall、diff-check。推远端精确SHA。禁止 @项目调度助手。
