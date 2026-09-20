# DAV-147 py-mini-racer 环境修复（宿主运维）

## 目标

只修宿主 `.venv310` 的 py-mini-racer/V8 ABI 运行环境，使 `MiniRacer()` 构造与 `ctx.eval("1+1")` 可用；不修改项目代码、主干、数据库、用户设置、providers、模型绑定或凭据。

## 当前事实

- 项目目录：`/Users/davidliu/Documents/TradingAgents-AShare`
- `.venv310`：Python 3.10.20
- `py-mini-racer`：0.6.0
- `MiniRacer()`：失败，`dlsym(..., mr_eval_context): symbol not found`
- `.venv310/bin/python` 无 pip 模块；不得直接改 system Python。

## 施工边界

- 这是宿主环境修复，不是仓库代码任务；不得写入 git tracked/untracked 文件，不得改 `pyproject.toml`/`requirements.txt`，不得提交/推送。
- 必须先备份当前 `.venv310` 中 py-mini-racer 相关包/二进制及版本信息到 `/tmp` 或工作区外；不得删除唯一副本。
- 优先使用现有 `uv`/`python3.10 -m venv`/已安装 wheel 的安全路径；新增/更换依赖前记录精确版本和来源。
- 只接受与当前 macOS/Python 3.10 兼容、能真实构造 MiniRacer 的版本；若网络或包不可用，停止并报告，不猜测。

## 验收

必须输出真实命令结果：

1. `env -u PYTHONPATH .venv310/bin/python --version`；
2. `env -u PYTHONPATH .venv310/bin/python -c "from py_mini_racer import py_mini_racer; c=py_mini_racer.MiniRacer(); print(c.eval('1+1'))"` 返回 `2`；
3. 记录 `py-mini-racer` 实际版本；
4. 说明未修改项目代码/配置/数据库/用户设置；
5. 通过后再通知 DAV-146 重新做一次固定 provider 探针，不能直接宣称 EM/THS consensus。

若无法安全修复，保持 DAV-146 blocked；不要反复运行组合回归。
