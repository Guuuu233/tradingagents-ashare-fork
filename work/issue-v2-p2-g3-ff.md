# P2-G3 合入主干（线性 FF）

## 独立核验（Cursor）

- 当前 `target/codex/dav-4-p2a-trunk` = `f8f624241d125125a61c69454638982f2423740f`
- 候选：`agent/1/4fdf445fa47a` @ **`50679db31a7fa1908f5ba91d54aa7c39711605a0`**
- `f8f6242` 是该 SHA 祖先；可 fast-forward
- 相对基线仅 4 文件：`financial_announce.py`、`cn_akshare_provider.py`、`test_financial_announce_cutoff.py`、`test_financial_as_of.py`
- `.venv310`（3.10.20）：`pytest tests/test_financial_announce_cutoff.py tests/test_financial_as_of.py` → **50 passed**
- DAV-426 审核员书面 PASS。其 @项目调度助手 已取消，不得由调度助手合入

## 唯一允许动作

远端 `https://github.com/Guuuu233/1.git`：

1. 读回 trunk 仍必须是 `f8f624241d12…`，候选仍必须是 `50679db31a7f…`，否则 BLOCK。
2. `git push <target> 50679db31a7fa1908f5ba91d54aa7c39711605a0:refs/heads/codex/dav-4-p2a-trunk`
3. 读回 trunk 必须等于 `50679db31a7fa1908f5ba91d54aa7c39711605a0`

禁止 force、改代码、重启。完成后 mention 代码运维测试员。不要 mention 项目调度助手。
