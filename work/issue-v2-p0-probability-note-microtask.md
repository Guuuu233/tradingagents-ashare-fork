## 精确基线与生产复现

- 基线：`target/codex/dav-4-p2a-trunk@23e09e5ed2cc8623b88bcbda94d701df5d6b2150`
- 真实报告：`af8f029a1ab842eca7ba80d43ec882d8`，BUY，confidence=68，target=36.8，stop=33.0，正式文本没有 probability/胜率。
- probability=NULL 语义允许；缺陷是持久化 `result_data` 没有 `extraction_note`，形成静默缺失。
- DAV-356 旧 workdir/改动全部作废，不读取、不复用、不 cherry-pick。

## 唯一允许范围

只允许修改：
- `api/services/report_service.py`
- `tests/test_verdict_extraction.py`

禁止修改 nested `result_data['structured']`、数据库 schema、API main、data_collector、其他测试、golden JSON、配置、用户模型/providers/role bindings/API Key、主干和服务。

## 严格 TDD

1. 在 `tests/test_verdict_extraction.py` 新增一个最小生产可达测试：BUY；VERDICT confidence=68；文本目标/止损可提取；所有正式文本无 probability。
2. 先用宿主 Python 3.10 运行该测试，确认 RED：`probability is None`、`extraction_warning is None`，但 `extraction_note` 当前为 None。
3. 最小 GREEN：在 `resolve_report_fields` 中，当 `probability is None` 且不是“confidence+probability同时缺失”的 warning 情形时，顶层 `extraction_note` 增加稳定文案 `概率未提供/未提取`；若已有 HOLD note，用中文分号连接。不得从 confidence 映射 probability，不填 0。
4. `create_report` 现有逻辑已经会把 resolved note 写到顶层 canonical result_data；只测试该生产可达顶层持久化，不新增/同步任何 nested `structured` 字典。
5. 现有三份 golden replay：只允许将确实没有 probability 的 600900/600276 note 期望按新契约更新；000333 已有 probability=0.65，必须保持 note=None。不得改 fixture 文件。
6. 宿主 `.venv310` 跑：新增测试、完整 `tests/test_verdict_extraction.py`、`tests/test_confidence_extraction.py`、`tests/test_report_recovery.py`；compileall、git diff-check。
7. 推送 fresh remote branch/SHA，明确未合入/未重启/未上线。

changed files 必须恰好两个。不要 mention 项目调度助手。