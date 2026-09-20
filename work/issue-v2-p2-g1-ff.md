# P2-G1 合入主干（线性 FF）

## 独立核验（Cursor）

- 当前 `target/codex/dav-4-p2a-trunk` = `4fa2e2453de739c0ec10d42a4bac591a6effff53`
- 候选：`agent/1/56553db093b4` @ **`f8f624241d125125a61c69454638982f2423740f`**
- 评论里的 `f8f62424cf3b593673f848030f2ee6d4da5f69c5` **作废**，不得使用
- `4fa2e24` 是该 SHA 祖先；可 fast-forward
- 文件：`model_tier_warning.py`、`test_model_tier_warning.py`、`trading_graph.py`、`report_service.py`（无 `api/main.py`）
- `.venv310`：`pytest tests/test_model_tier_warning.py` → 43 passed
- DAV-422 审核员书面 PASS（其全量 pytest 用了系统 3.14，不以那次 2035 为准）

## 唯一允许动作

远端 `https://github.com/Guuuu233/1.git`：

1. 读回 trunk 仍必须是 `4fa2e24…`，候选仍必须是 `f8f624241d12…`，否则 BLOCK。
2. `git push <target> f8f624241d125125a61c69454638982f2423740f:refs/heads/codex/dav-4-p2a-trunk`
3. 读回 trunk 必须等于 `f8f624241d125125a61c69454638982f2423740f`

禁止 force、改代码、重启。完成后 mention 代码运维测试员。不要 mention 项目调度助手。
