独立核验通过：

- 宿主 HEAD / `origin/codex/dav-4-p2a-trunk` = `52353ed8d28d0dcee50be370b01ec42059df9836`
- `/healthz.commit_sha` = `52353ed8d28d0dcee50be370b01ec42059df9836`
- `pytest tests/test_evaluation_contracts.py tests/test_recalculate_weekly_metrics.py tests/test_h1b_gates.py -q` → **51 passed**
- `credit_weighting_enabled` 未动；辩论轮次仍为 3/1

P3-H2.4 部署完成，可关 DAV-449。
