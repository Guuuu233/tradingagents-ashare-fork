# H1b 真实攒样阻塞：修复 research_manager double_count_guard 类型健壮性

## 背景

当前生产服务运行在 `/private/tmp/ta-serve-6ee1486`，`/healthz` 精确回读 SHA `6ee148699339efefc2f7f7548eb286be485524e1`。Tushare/LLM 已恢复，真实 H1b 试点 `600036.SH / 2026-09-11` 已完成数据采集、7 个分析师和 bull/bear 辩论，但在研究经理确定性护栏失败：

```text
AttributeError: 'str' object has no attribute 'get'
tradingagents/agents/managers/research_manager.py:689
apply_manager_double_count_guard()
```

具体路径：`metrics["double_count_guard_applied"]` 已为真时，代码把 `manager_verdict["excluded_evidence"]` 转成 list 后，对每个既有元素无条件执行 `e.get("claim_id")`。真实输入允许既有 `excluded_evidence` 元素是字符串；因此新 v2 分析无法完成，不能批量攒 H1b 样本。

## 基线与范围

- 精确基线：`6ee148699339efefc2f7f7548eb286be485524e1`
- 只修改 `tradingagents/agents/managers/research_manager.py` 与其现有定向测试文件（如确有必要）。
- 不改用户个人配置、providers、模型绑定、API Key、辩论轮次、生产库，不部署。
- 必须从当前基线创建新分支/提交并推送；若基线在远端不可达，明确报告 blocked，不用其他 SHA 冒充。

## 修复要求

1. `apply_manager_double_count_guard()` 在幂等重入路径遇到 `excluded_evidence` 中的字符串、mapping、空值或其他合法历史形态时不抛异常。
2. 保留原有字符串证据，不把它静默丢弃；对 mapping 仍按 `claim_id` 去重。
3. 对同一 `excluded_claim_ids` 重入仍保持幂等，不重复追加结构化记录。
4. 不放宽 double-count guard 语义，不把字符串当成可采纳 claim，不绕过 E-04 护栏。
5. 增加最小回归测试覆盖：
   - 已有字符串 + 新 mapping；
   - 已有 mapping（相同/不同 claim_id）；
   - 空/异常历史项不导致异常；
   - 二次调用结果稳定。

## 验收命令

使用项目 `.venv310`，所有命令 `env -u PYTHONPATH`：

- 现有 research_manager 相关定向测试（先发现真实测试文件，不猜路径）；
- 新增/修改用例全部通过；
- `python -m compileall` 覆盖修改文件；
- `git diff --check` 必须通过；
- 报告精确 passed/failed、diff stat、完整新 SHA。

## 交付纪律

完成后把精确远端 branch/SHA、测试数字、未合入/未部署状态写在本卡；最后一行使用精确 Markdown mention 唤醒项目调度助手。交付后停止继续修改，等待同 SHA“代码审核员”只读复审。