独立核验通过：

- 宿主 `HEAD` = `origin/codex/dav-4-p2a-trunk` = `d50b0dc3a7b3721aba16ac4474530c1565be79de`
- `/healthz.commit_sha` = `d50b0dc3a7b3721aba16ac4474530c1565be79de`
- uvicorn PID 25955，监听 `127.0.0.1:8000`
- `pytest tests/test_evaluation_contracts.py -q` → **13 passed**
- `credit_weighting_enabled` 未动；辩论轮次仍为 3/1

P3-H2.0 部署完成，可关 DAV-444。
