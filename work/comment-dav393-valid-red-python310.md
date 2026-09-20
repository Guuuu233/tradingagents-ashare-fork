当前`test_debate_challenge_protocol.py`已写，但刚才15个失败来自裸pytest/Python3.14，不能计TDD RED；文件里还使用`@pytest.mark.asyncio`，而宿主`.venv310`没有pytest-asyncio，沿用会产生执行层错误。

在修改任何生产代码前必须：

1. 把所有async测试改为普通同步`def test...`，内部定义`async def _run()`并使用`asyncio.run(_run())`；禁止安装插件、改pytest配置。
2. 打印并保存：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python --version`，必须为3.10.20。
3. 用同一解释器运行：
   `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_debate_challenge_protocol.py -vv`
4. 有效RED必须是断言失败/ImportError明确指向缺失challenge功能（challenges/self_win_prob未知字段、协议仍走legacy Check C、无evaluate_challenges、stage未进tiebreak等），不能是async plugin、语法、fixture或环境错误。
5. 先贴/记录有效3.10 RED终态，再开始C1生产代码。此前禁止修改生产文件。
6. 后续所有GREEN、矩阵、replay、compileall继续显式`env -u PYTHONPATH .../.venv310/bin/python`，不得再用裸pytest/python3。

继续当前唯一a2b7515 worktree，不提交、不派第二writer。不要mention项目调度助手。