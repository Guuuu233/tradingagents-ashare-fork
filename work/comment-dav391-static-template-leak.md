O3有效RED已证明：即使动态history/claims清空，`zh.py`/`en.py` Bear静态模板仍含`INV-1`和“第2次必须回应多头/target对手”，构成真实双盲泄漏。你刚把O3测试中的`INV-1/2/3`零命中断言删除，这是弱化验收，禁止。

## 必须修正

1. 恢复Bear Opening最终prompt断言：`INV-1`、`INV-2`、`INV-3`均0命中；Bull独特正文、claim文本、summary/current_response也0命中。静态示例ID同样算泄漏，不能以“不是本场动态数据”为由放行。
2. 在允许范围`zh.py`/`en.py`内参数化**同一个**bull/bear prompt的stage契约，不新增`*_v2/_new`并行prompt键：
   - v1调用时注入现有legacy三轮框架、原机器块示例和respond/target规则，使最终legacy prompt语义与现有断言保持；
   - v2 Opening调用时注入双盲Opening框架：恰好3条claim、3个不同合法battlefield、responded/target/resolved为空；机器块示例不得出现任何INV ID或反驳指令。
   - 不能仅对最终字符串做脆弱replace；用明确format placeholder或同等可审计参数化方案。
3. researcher按`is_v2 && protocol_stage=='opening'`选择stage契约；动态隔离视图和stage专属retry继续保留。custom prompt可保留，但测试使用空custom prompt。
4. 恢复/新增v1最终prompt断言，证明flag关闭时原legacy示例与“第2至第6次回应/target对手”仍存在；不要删除/改写既有legacy测试来适配v2。
5. O2仍未落地：当前实现仍允许2-3条，必须改为**恰好3条**并增加2条claim明确RED/GREEN。
6. stage切换仍需断言：Bear opening成功后authoritative state `protocol_stage='challenge'`；两条round_message自身stage仍opening；失败attempt不推进。
7. 修复后先跑Opening专项，随后稳定重跑完整111项legacy矩阵；运行测试期间冻结编辑。

暂不提交；禁止conditional_logic/api main/setup.py/主干/服务。