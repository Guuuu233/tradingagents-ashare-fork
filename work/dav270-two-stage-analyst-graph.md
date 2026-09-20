# DAV-270 阶段二：两阶段分析师拓扑（DAV-195）

**基线父提交必须是 `b1fa020a0b511be9266651cd69dd2eb091d84cc8`（`integration/dav256-dav258-on-2e9e674`）。禁止基于 `2e9e674` 或旧树 `520f871` 开工。不必等主干 FF。禁止自己推主干。**

对照《项目加强方案》§阶段二：5 行业数据采集之后是**两阶段分析架构**。当前 `tradingagents/graph/setup.py` 仍是 7 分析师全部 `START` 并行，互不感知。

## 产品契约

1. **阶段一并行**：宏观 / 市场 / 情绪（social）从 START 出发。
2. **汇合广播**：三份报告写入共享 state 后，才启动阶段二。
3. **阶段二并行**：基本面 / 新闻 / 主力资金 / 量价，prompt 必须能读到阶段一产物（宏观结论、大盘、情绪），用于四层传导。
4. 禁止 `_v2` 并行图；改 `setup.py` 原路径。
5. 辩论仍在全部分析师 Done 之后进 Bull。
6. 缺数据必须【数据缺失】，禁止为凑拓扑伪造阶段一输出。

## 允许修改

- `tradingagents/graph/setup.py`
- `tradingagents/graph/conditional_logic.py`（仅若需要汇合条件）
- 分析师节点读取上游报告的现有字段（禁止新造平行字段名）
- `tests/` 追加图拓扑/顺序测试

禁止改：`.env`、providers、role_bindings、Prompt 大段、资金流证据模块、主干、部署。

## 验收

独立 worktree，父提交必须是合入后的主干 `b1fa020`（或其后继）。
`.venv310` 定向图测试 + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check。
推送独立分支精确 SHA，不合主干。不得 @项目调度助手。

若 checkout 时主干还不是 `b1fa020` 的后代：停止并报告，不要用旧 SHA 开工。
