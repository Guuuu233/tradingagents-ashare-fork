## Cursor 同 SHA 验收：准予合入

**候选 SHA（exact）：** `2a82b11d1157d61e7ecea7cac01225b8d020caca`  
**父 tip：** `b3ba1963e16cb3a5bd6736439db0fd57e7d03e3f`  
**分支：** `origin/agent/2/e72ee4955afc`

DAV-603 已对该 SHA PASS（旧 SHA `733d40b…` 作废）。Cursor 隔离 worktree `/tmp/iso-dav601-2a82b11` 复测同一 SHA：

- 焦点：`tests/test_h1b_gates.py tests/test_shadow_credit.py` → **66 passed**
- 扩展：含 confirmation/fairness → **167 passed**
- CLI 未传 `--cohort`：exit **2**
- CLI `--cohort=""`：exit **2**
- `git diff --check`：0

changed files 仅 4 个白名单文件，未改 `evidence_verifier.py`。

**准予合入。** 线性 FF 到 `codex/dav-4-p2a-trunk`。禁止 merge。禁止部署。禁止开加权。禁止补 H1b 样本。
