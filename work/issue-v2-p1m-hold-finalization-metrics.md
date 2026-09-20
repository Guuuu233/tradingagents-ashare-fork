## 精确基线与真实生产证据

- 当前目标主干/运行服务：`153fc9bad03c60744407c85a61a7aa52069c84e9`
- 真实报告：`8320e32481b64ea98cb4b33d9c3e8d10`，600406.SH，completed/HOLD，legacy 6条消息与P1-M metadata/flags/utilization已正确落库。
- DB最终result_data：`extraction_note=观望不设目标价`、`extraction_warning=置信度及概率数据全部缺失`；但持久metrics.field_completeness仍是0/4，target未列为legitimate omission。

Hermes宿主3.10精确复现：

```text
resolve_report_fields(result_data无decision)
→ extraction_warning=置信度及概率数据全部缺失
→ extraction_note=None

resolve_report_fields(同数据但decision=HOLD)
→ extraction_note=观望不设目标价
```

根因：API路径先`resolve_report_fields`，后`_apply_structured_report_fields`才确定并写入最终decision；metrics随后基于首次resolved结果刷新。`create_report`落库阶段会再次resolve并补note，但不会重算metrics，造成DB note与metrics矛盾。

## 严格范围

只允许修改：
- `api/main.py`
- `tests/test_debate_state_persistence.py`

禁止其他文件、report_service算法修改、DB schema/配置/用户设置、prompt/graph/provider/frontend、P1-B、主干和服务。

## TDD

1. fresh checkout精确trunk 153fc9b。
2. 在现有测试文件先写RED：
   - result初始无decision，final_trade_decision文本不能可靠被正则识别为HOLD；初次resolve note=None；
   - `_apply_structured_report_fields`最终选择HOLD后，result必须再按现有report_service契约刷新：
     - extraction_note含`观望不设目标价`；
     - extraction_warning保持`置信度及概率数据全部缺失`；
     - metrics刷新后target_price进入legitimate_omissions；
     - probability因存在warning不能冒充合法提供，仍missing；
     - confidence和stop仍missing；因此field completeness应为1/4 partial，不得0/4或4/4。
   - job result与capture_create_report的result_data必须一致；DB canonical二次resolve不得造成note/metrics冲突。
3. 最小GREEN：在`_apply_structured_report_fields`写入最终decision及字段后，复用`report_service.resolve_report_fields`对最终result做一次确定性post-decision resolve，仅刷新`extraction_note`/`extraction_warning`（如需保持现有非空字段，按helper返回值契约）；随后各现有调用点的P1-M helper自然重算metrics。禁止手写第二套HOLD判断和note文案。
4. 验证BUY/SELL、HOLD有目标价、缺confidence、probability合法note场景不回归；不得覆盖已有有效note为错误None。
5. 宿主`.venv310`：精确测试、整个state persistence、P1-M 111项、verdict extraction、replay、compileall、diff-check；不跑全量，最终由Hermes统一全量。
6. changed files恰好2个，推新远端branch/SHA；未合入/未重启/未上线，3/1不变，P1-B锁定。

不要mention项目调度助手。