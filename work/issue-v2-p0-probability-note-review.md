## 固定审核对象

- 基线：`23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 分支：`agent/2/614820fd6f2f`
- SHA：`25f043ea101372cde9a9ccaa98e81287134781db`
- 只读审核，禁止修改代码/测试/配置/DB/服务。

## 审核要求

1. 远端、ancestry、changed files；必须恰好 `api/services/report_service.py`、`tests/test_verdict_extraction.py`。
2. 核验 RED→GREEN 原始消息，RED 必须在生产代码修改前、宿主 Python3.10 下因 `extraction_note is None` 失败。
3. 代码语义：probability 保持 None，不从 confidence 映射，不填 0；confidence存在且 probability缺失时 note=`概率未提供/未提取`；HOLD 组合 note；confidence+probability都缺失时仍是 warning，不重复 note。
4. `create_report` 只持久化顶层 result_data note，不存在任何 nested `structured` 新逻辑或测试。
5. 三 golden：600900/600276 无 probability 时 note更新；000333 probability=0.65 时 note必须 None；fixture文件未改。
6. 宿主 `.venv310` 复跑 `tests/test_verdict_extraction.py tests/test_confidence_extraction.py tests/test_report_recovery.py`；diff-check/compileall。
7. 给 PASS/BLOCK、文件:行号、测试结果、0 code changes、未合入/未重启/未上线。

不要 mention 项目调度助手。