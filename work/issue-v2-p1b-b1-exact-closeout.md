## 固定输入

- fresh基线：`target/codex/dav-4-p2a-trunk@ccc4c53a4985f8db32353586e6cb4317aa34f8cd`
- 原DAV-391未提交现场补丁：`/Users/davidliu/Documents/TradingAgents-AShare/work/dav391-uncommitted.patch`
- 补丁SHA-256：`70892f6b7a8df85d625f38a7fa5080c98c81913ff48eb2235939c838b61ccf53`
- 原Opening测试副本：`/Users/davidliu/Documents/TradingAgents-AShare/work/dav391-test_debate_opening_protocol.py`
- 测试文件SHA-256：`9d7e511a9aa5b5a45bd160e9023bbbe06e468a4f05e4cee429b1704f06a92d2a`
- 原DAV-391已BLOCK；无远端候选。禁止修改宿主树或复用原worktree。

## 操作顺序

1. fresh checkout精确ccc4c53。
2. 校验两个输入hash；`git apply --check`后应用补丁；将测试副本复制为`tests/test_debate_opening_protocol.py`。
3. 先运行py_compile和Opening测试，记录当前真实状态；不要采信父卡的移动worktree测试摘要。
4. 只修下列四项，不重写O1/O2/O3整体。

## 四个阻断

### C1 Opening必须恰好3条claim

当前补丁仍允许`2 <= len(new_claims) <= 3`。改为v2 Opening `len(new_claims)==3`；1/2/4条均invalid_protocol，3条且3个不同合法battlefield才通过。更新新测试文件的测试名/文案，新增两条claim即使覆盖两个合法战场也拒绝。Legacy不变。

### C2 Bear成功后stage切challenge

- Bull message1成功：claim/round_message自身stage=opening，返回state.protocol_stage仍opening。
- Bear message2成功：claim/round_message自身stage=opening，但返回authoritative state.protocol_stage=`challenge`，供下一动作使用。
- invalid/missing attempt：count和protocol_stage均不推进，claims/history不污染。
- B1不实现challenge payload或路由，`conditional_logic.py`冻结。

### C3 静态模板本身不得泄漏

当前`zh.py/en.py` Bear模板硬编码INV-1和“第2次必须回应/target多头”，即使动态state清空也泄漏。必须参数化同一`bull_prompt`/`bear_prompt`，禁止新增`*_v2/_new/_fixed`并行key。

推荐可审计方案：在同一模板中引入明确stage契约参数或成对marker区段：
- legacy最终prompt保留现有三轮框架、原机器块示例、INV示例和respond/target规则；既有legacy断言不删除、不弱化。
- v2 Opening最终prompt移除全部legacy-only区段，并注入Opening契约：恰好3条claim、3个不同合法battlefield、responded/target/resolved为空；机器块示例不含任何INV ID或反驳指令。
- 禁止对散落文本做不可审计的多次字符串猜测替换；必须有统一helper/marker/placeholder和独立测试。

恢复并保持Opening最终Bear prompt断言：INV-1/INV-2/INV-3、Bull独特正文、claim文本、summary/current_response均0命中。静态示例ID同样算泄漏。

### C4 动态隔离与retry

Opening：history/current_response/claims/focus/unresolved/round_summary/past_memory隔离；memory n_matches=0或不调用；authoritative state不清空。Attempt2 retry不得泄漏对手ID或要求respond/target对手，必须要求3条/3战场/空responded-target-resolved。七报告原始字段和manifest保持一致。

## 严格范围

只允许原补丁6文件 + `tradingagents/prompts/zh.py`、`en.py` + `tests/test_debate_opening_protocol.py`。禁止api/main.py、conditional_logic.py、setup.py、manager/challenge/tiebreak、DB/配置/前端/provider/主干/服务。

## TDD与验收

- 使用普通同步test + `asyncio.run`，禁止pytest-asyncio插件/mark。
- 必须记录有效RED：静态INV泄漏、两条claim误通过、Bear后stage未切换。
- GREEN：Opening专项全绿。
- 稳定冻结编辑后运行：原111项legacy矩阵、prompt/custom prompt相关矩阵、DAV-346、e2e protocol、state persistence、P1-M矩阵。
- `env -u PYTHONPATH .../.venv310/bin/python tests/golden/audit_20260823/replay_verifier.py`
- compileall、diff-check；不跑全量。
- changed files仅上述范围；新远端branch/SHA，直接父ccc4c53；明确未合入/未重启/未上线，3/1不变。

不要mention项目调度助手。