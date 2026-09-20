RED 已有效（4 failures），GREEN 已在系统 Python 3.14 通过；交付前必须使用宿主 `.venv310/bin/python -m pytest tests/test_verdict_extraction.py` 复跑。检查是否改了既有 golden expected_note：只允许更新因新契约必然变化的断言，不能把本来含 probability 的 000333 误标。随后运行相关 report_service tests、diff-check、compileall并推远端。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
