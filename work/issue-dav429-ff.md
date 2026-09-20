# P2-10.4 线性 FF 主干（data_gaps 结构性分类）

## 独立核验（Cursor）

- 当前 `target/codex/dav-4-p2a-trunk` = `50679db31a7fa1908f5ba91d54aa7c39711605a0`
- 候选：`agent/2/672b8f4758de` @ **`11309037de9334820603eec6dd801f291172f6ed`**
- `50679db` 是该 SHA 祖先；可 fast-forward
- 相对基线仅 4 文件：
  - `tradingagents/graph/data_collector.py`
  - `api/services/report_service.py`
  - `tests/test_data_gap_classification.py`
  - `tests/test_report_data_gaps.py`
- `.venv310`（编排侧）：`pytest tests/test_data_gap_classification.py tests/test_report_data_gaps.py` → **11 passed**；`tests/test_data_collector.py` → **23 passed**
- DAV-429 独立代码审核员书面 **PASS**（候选 SHA 同上）。其 @项目调度助手 已取消，不得由调度助手合入

## 唯一允许动作

远端 `https://github.com/Guuuu233/1.git`：

1. 读回 trunk 仍必须是 `50679db31a7fa1908f5ba91d54aa7c39711605a0`，候选仍必须是 `11309037de9334820603eec6dd801f291172f6ed`，否则 BLOCK。
2. `git push <target> 11309037de9334820603eec6dd801f291172f6ed:refs/heads/codex/dav-4-p2a-trunk`
3. 读回 trunk 必须等于 `11309037de9334820603eec6dd801f291172f6ed`

禁止 force、改代码、重启。完成后 mention 编排侧/项目主管开部署卡。不要 mention 项目调度助手。
