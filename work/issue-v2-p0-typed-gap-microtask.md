## 精确基线与单一缺陷

- 仓库：`https://github.com/Guuuu233/1.git`
- 基线：`codex/dav-4-p2a-trunk@23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 真实缺陷：`tradingagents/graph/data_collector.py::_compact_failure_reason('failed')` 固定返回 `provider call failed`，导致报告 `af8f029a1ab842eca7ba80d43ec882d8` 顶层 gap 出现裸英文。
- DAV-357 旧 workdir/分支全部作废，不读取、不复用、不 cherry-pick。

## 严格允许范围

只允许修改两个文件：
- `tradingagents/graph/data_collector.py`
- `tests/test_data_collector.py`

禁止修改 report_service、test_dav37、其他测试、provider、API、数据库、配置、主干、服务和用户个人设置。

## 严格 TDD

1. 在 `tests/test_data_collector.py` 新增一个最小测试，直接调用 `_build_data_failure_ledger`，输入：
   `{'shareholder_count': {'status':'failed','reason':'ConnectionError: upstream unavailable'}}`
2. 断言：
   - entry status 仍是 `failed`；
   - source 为 `shareholder_count`；
   - reason/gap 不包含 `provider call failed`；
   - reason/gap 包含稳定 typed 中文 `数据源调用失败`；
   - 不要求复杂提取/保存原始 exception，不新增 parser/helper。
3. 使用宿主命令运行测试并确认 RED：
   `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_data_collector.py::<test_name> -v`
4. 最小 GREEN：只把 `_compact_failure_reason('failed')` 的固定显示值由英文改为 `数据源调用失败`。不改函数签名、不解析 value、不新增 helper、不改 provenance 其他逻辑。
5. 复跑：新增测试、完整 `tests/test_data_collector.py`、相关既有 data gap tests。若 `test_dav37` 旧断言失败，只报告“旧展示契约需由 integration owner 更新”，本卡禁止修改该文件。
6. compileall、`git diff --check`，推送新远端 branch/SHA。

## 交付

必须给出 RED 原始失败、GREEN 结果、changed files（只能2个）、远端 SHA、明确未合入/未重启/未上线。不要 mention 项目调度助手。