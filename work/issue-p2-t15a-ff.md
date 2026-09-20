# P2-T15a：线性 FF 主干并回归验证（c1ec33e）

## 授权

Cursor 已在 DAV-523 对完整 SHA **准予合入**：

`c1ec33e7adf95b2889e015038b78dd4ac2233fd5`

父提交：`7db2882d43276c22dd87e259b259dd1f500c6bd0`。
独立审核员 DAV-524：✅通过。
Cursor 隔离：新增 9 / 扩展 176 passed。零产品改动。

## 动作

1. `git fetch origin`
2. 确认 `origin/agent/dev2/p2-t15a-social-e2e-acceptance` tip == `c1ec33e7adf95b2889e015038b78dd4ac2233fd5`
3. 确认 `origin/codex/dav-4-p2a-trunk` 当前仍是 `7db2882d43276c22dd87e259b259dd1f500c6bd0`
4. **线性 Fast-Forward only** 到 `c1ec33e`。禁止 merge。禁止 FF 其它 SHA。
5. `git ls-remote` 回读主干 tip 必须等于 `c1ec33e7adf95b2889e015038b78dd4ac2233fd5`

## 回归

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_e2e_acceptance.py \
  tests/test_social_rollout_modes.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py
```

报告精确数字（Cursor 全社交矩阵：176 passed）。

## 禁止

- **不准予部署**
- 不要删 `legacy_proxy`；不要开 T15b/Gate4
- 不要动脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 不要 @项目调度助手催工

## 交付评论

- 完整 40 位主干 tip + `git ls-remote` + pytest 数字 + 未部署
