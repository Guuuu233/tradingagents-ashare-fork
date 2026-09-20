# DAV-214：RISK_STATE 截断块污染 current_*_response

## 生产证据

- 主干/宿主：`7ef89f63662ce01bacdcda9dd0996060ce903c83`
- 正确账户3/3复验报告：`dfcf75d53e8343e7bcbdeca8b43ff80d`
- 失败：`RISK_STATE machine block is truncated`
- 日志：
  - 23:42:36 `invalid_or_trailing_prose`
  - 23:43:39 `truncated: closing marker is missing`
- DAV-210已清理history，但三个风险节点仍执行：
  - `clean_response = strip_tagged_json(full_content, "RISK_STATE")`
  - 解析失败时公共strip按设计原样返回
  - 随后写入`current_aggressive_response/current_conservative_response/current_neutral_response`
  - result_data递归校验发现截断块并fail-closed。

## TDD目标

从当前主干新分支施工。先写失败测试，证明风险节点遇到 malformed/trailing/duplicate/缺冒号/截断RISK_STATE时：

1. history与对应`current_*_response`均不含`RISK_STATE`标签；
2. 合法自然语言正文保留；
3. count正常递增，坏payload不更新claims；
4. 合法RISK_STATE仍正常解析claims并清除标签；
5. 三个风险节点行为一致；
6. 最终`report_service.validate_report_machine_blocks({"risk_debate_state": state})`通过；
7. 直接报告正文坏块仍由report_service拒绝。

## 实现约束

- 修改原路径；不改公共`strip_tagged_json()`失败块保留语义；
- 优先让`update_debate_state_with_payload`返回/暴露已隔离正文，或新增公共窄用途安全正文函数供risk节点使用；避免三个节点复制正则；
- 仅允许修改`debate_utils.py`、三个risk debator文件及相关现有测试；
- 不改prompt/轮次/DB/配置/provider/model/Key；不跑真实LLM；不合入部署。

## 验证

专项节点测试、`test_debate_state_persistence.py`、相关语义/报告测试、全量tests、compileall、diff-check。推送远端分支与精确SHA，给RED→GREEN证据。
