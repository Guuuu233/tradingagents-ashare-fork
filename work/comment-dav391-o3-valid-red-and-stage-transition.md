当前O3两项失败不是功能RED，而是测试执行错误：项目没有pytest-asyncio，`@pytest.mark.asyncio`导致“async def functions are not natively supported”。必须先修测试执行方式，再确认真实RED。

## 必须立即纠正

1. 去掉`@pytest.mark.asyncio`与async test定义，按项目既有模式使用普通`def test...`，内部`asyncio.run(run_case())`。不得安装新插件或改pytest配置。
2. 修正O2实现与测试：当前`debate_utils.py`仍是`2 <= len(new_claims_list) <= 3`，必须改为`len(new_claims_list) == 3`；错误文案和测试名同步改为“恰好3条”。新增2条claim（两个不同合法battlefield）明确invalid_protocol测试。
3. stage权威契约：
   - Bull/Bear opening成功写入的round_message/claim `stage='opening'`；
   - Bear message2成功后，返回authoritative state的`protocol_stage`必须切换为`challenge`，作为下一动作stage；
   - Bull message1成功后state仍opening；
   - invalid/missing attempt不得推进count或protocol_stage。
   新增相应断言。B1不实现challenge payload，但必须正确推进state。
4. O3修正测试执行后，先在生产代码尚未隔离时运行，失败必须明确来自：Bear prompt包含Bull独特句子/INV IDs/summary/current_response、memory n_matches非0，或retry要求respond/target对手；只有这样的失败才是有效RED。
5. 然后最小GREEN：Opening只构造只读隔离视图，不清空authoritative state；retry同样不得泄漏。
6. O2/O3冻结后重跑完整111项legacy矩阵。旧fixture不改；运行期间不得继续编辑，避免移动worktree假失败。

继续当前唯一worktree，暂不提交；禁止conditional_logic/api main/setup.py/主干/服务。