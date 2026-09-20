## 固定审核对象

- trunk父SHA：`153fc9bad03c60744407c85a61a7aa52069c84e9`
- 远端分支：`agent/2/e9e0a4bdc213`
- 精确SHA：`ccc4c53a4985f8db32353586e6cb4317aa34f8cd`
- 直接父必须为153fc9b；相对父恰好2文件：api/main.py、tests/test_debate_state_persistence.py。
- 只读复审，0 changes；禁止主干/服务/DB/配置/P1-B和全量测试。

## 必审

1. 远端SHA/父链/两文件/diff-check；宿主3.10 RED 4失败→GREEN32、P1-M 70/92、相关56/85、compileall。
2. `_apply_structured_report_fields`内post-decision调用`report_service.resolve_report_fields`：
   - 不造成递归（resolve不再调用_apply）；
   - 只刷新note/warning，不改变已确定decision/confidence/probability/target/stop；
   - HOLD无目标价→观望不设目标价；HOLD有目标价不产生该note；BUY无目标价仍missing；
   - probability=None且warning存在时不能算合法probability omission；只有无warning的明确note才合法。
3. 旧有效extraction_note场景：post-resolve不应错误清除属于当前最终result的合法note；核`概率未提供/未提取`与HOLD组合。
4. 三个生产路径均通过同一`_apply_structured_report_fields`，随后P1-M helper刷新；create_report二次resolve后note/metrics不矛盾。
5. 宿主`.venv310`窄测state persistence、verdict extraction、dav37 regression；独立复跑真实HOLD纯函数探针，期望HOLD/note/warning/1-of-4。
6. legacy 6消息、3/1、protocol/flags/utilization不受影响；无LLM/网络/DB调用新增。
7. 输出PASS/BLOCK、证据、0 changes；未合入/未重启/未上线，P1-B锁定。

不要mention项目调度助手。