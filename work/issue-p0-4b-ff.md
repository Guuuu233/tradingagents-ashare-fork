# P0-4b：线性 FF 主干并回归验证（18e73bd）

## 授权

Cursor 已在 DAV-479 对完整 SHA **准予合入**：

`18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`

父链：`18e73bd` → `cef3bcf` → `12120c705ab6eb69d2ecac0d669b4d465e5b1abb`。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p0-4b-claim-cluster` tip == `18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `12120c705ab6eb69d2ecac0d669b4d465e5b1abb`
4. **线性 Fast-Forward only**：把主干推到 `18e73bd`。禁止 merge commit。禁止 FF 其它 SHA（尤其不要停在 `cef3bcf`）。
5. `git ls-remote` 回读主干 tip 必须等于 `18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_claim_cluster.py \
  tests/test_adjudication_risk_prompts_deep_reasoning.py \
  tests/test_research_manager_run_integrity.py \
  tests/test_fund_flow_evidence.py \
  tests/test_cn_akshare_backup_sources.py \
  tests/test_smart_money_fund_flow_semantics.py \
  tests/test_decision_status.py \
  tests/test_run_integrity.py \
  -k 'not smoke' \
  -q --tb=short
```

可再跑相邻集，但须报告精确数字。

## 禁止

- **不准予部署**；不要重启生产；不要改 `/healthz` 所服务的运行时
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要唤醒 DAV-460
- 不要 @项目调度助手催工（交付评论写完即可）

## 交付评论

- 完整 40 位主干 tip
- `git ls-remote` 证据
- pytest 精确数字
- 明确写：未部署
