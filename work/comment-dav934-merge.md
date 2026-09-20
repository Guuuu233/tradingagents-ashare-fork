## ✅ 已合入主线

`origin/codex/dav-4-p2a-trunk`：`483a1098` → **`28d1adc6a3e89ab344d198d8be564b6892aa3562`**（快进）

- 候选 `28d1adc6`（`origin/agent/support/dav934-on-483a1098`），直接父即合入前 tip
- 审查：DAV-993 ✅ PASS
- 白名单：`tradingagents/dataflows/providers/cn_akshare_provider.py`、`tests/test_akshare_duplicate_handling.py`

### 运维失败集合对照（分文件口径，两侧相同切分，`.venv310`）

| | 基线 `483a1098` | 候选 `28d1adc6` |
|---|---|---|
| OK | 216 | 217 |
| 失败文件 | 8 | **同样这 8 个** |
| 挂死文件 | 1 | **同一个** |

唯一差异为候选自带的 `tests/test_akshare_duplicate_handling.py` → `20 passed`。**零新增失败。**

未部署、未重启、未写生产库；`data/tradingagents.db` SHA256 保持 `94d2f6740db4f206...`。
