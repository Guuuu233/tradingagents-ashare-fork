# 生产缺陷返修：拒绝的机读块污染辩论 history

## 固定基线与证据

- 当前远端/宿主：`target/codex/dav-4-p2a-trunk@2d7bc5b8a0b45b52fb61a71d9cd6a7649e3578bd`
- 正确账户3/3报告：`50d1f2f94bf2489db7897eab4c00928e`
- 真实失败：完整推理后 finalize 失败：`DEBATE_STATE machine block contains invalid JSON`
- 生产日志在20:57:33先出现：`[debate_utils] DEBATE_STATE parse warning (invalid_or_trailing_prose): machine block not accepted`
- DAV-205开始将debate state持久化后，history里的原始坏注释被report_service递归扫描并拒绝。

## 根因

`tradingagents/agents/utils/debate_utils.py`：
- `_parse_tagged_json()`正确拒绝坏/尾随机读块；
- `update_debate_state_with_payload()`进入`_record_unstructured_response()`；
- `_record_unstructured_response()`调用`strip_tagged_json()`；
- 但`strip_tagged_json()`对解析失败块原样返回，于是坏`<!-- DEBATE_STATE ... -->`进入history；
- `report_service.validate_report_machine_blocks()`在落库时正确fail-closed。

## 施工目标（单一关注点）

仅修复“被拒绝的DEBATE_STATE/RISK_STATE机读注释不得写入结构化history”。不得放宽report_service校验。

### TDD要求

先在现有debate utils/semantics测试文件中新增失败测试，至少覆盖：

1. 合法正文 + malformed JSON块：正文保留，标签完整移除，count+1，claims不变，最终`validate_report_machine_blocks()`通过；
2. 合法JSON块后有尾随正文（触发`invalid_or_trailing_prose`）：正文与尾随正文保留，机读注释删除，结构化payload不采纳；
3. 重复同标签块：全部同标签注释隔离，不进入history；
4. 标签缺冒号；
5. 截断块（无`-->`）：从标签起至末尾隔离，标签前正文保留；
6. `RISK_STATE`同样处理；
7. 合法机读块现有路径不回归：正常解析claims/responded/resolved，history不含注释；
8. 直接报告正文包含坏块时，`report_service.validate_report_machine_blocks()`仍必须抛错（fail-closed不变）。

## 实现约束

- 修改原路径，不新增_v2/new/fixed；
- 优先在`debate_utils.py`增加私有、窄用途的“history quarantine”辅助函数，仅由`_record_unstructured_response()`调用；
- 不改变公共`strip_tagged_json()`对解析失败块原样保留的现有安全语义；
- 不修改prompt、轮次、数据库schema、用户配置、report_service、provider/model/Key；
- 不重跑真实LLM报告。

## 验证

- 精确新增/相关测试；
- `tests/test_prompt_semantics.py`、`tests/test_dav27_report_semantics.py`、`tests/test_dav37_stage16_regressions.py`、`tests/test_debate_state_persistence.py`；
- 全量`pytest tests/ -q`；
- compileall、git diff --check。

## 交付

推送独立远端分支和精确SHA，列出RED→GREEN证据、变更文件、测试；不得合入主干、不得部署。DAV-209/DAV-199保持blocked，DAV-200保持backlog。
