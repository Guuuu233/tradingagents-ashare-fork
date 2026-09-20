# DAV-259 已登录用户自有 URL 可拉模型列表

基线：`target/codex/dav-4-p2a-trunk@0b10041f9e68b5d0116b76c36cd629acb365f10d`

用户授权方案 2：设置页自己换 Base URL 后点「获取模型列表」必须能打到该地址，**不再依赖** `TA_MODELS_FETCH_ALLOWLIST` 是否包含该主机。换下一个 Tailscale / 本机 / 公网地址都不必再改 `.env`。

## 产品契约

`POST /v1/models/fetch`（必须已登录，非 `local-default-user` 的非回环来源仍 401）：

1. **优先用请求体 `base_url`**（设置页输入框，不必先保存）。
2. 请求体为空时才回退库内 `user_llm_configs.backend_url`，再回退选中 `providers.base_url`。
3. 上述「用户自有 URL」允许代发 `GET {base}/v1/models`，包括：
   - `http://100.65.130.33:8317/v1`（CGNAT / Tailscale `100.64.0.0/10`）
   - `http://localhost:8317/v1` / `http://127.0.0.1:8317/v1`
   - 公网 IP/域名
4. 仍必须拒绝：
   - 云元数据 `169.254.169.254` / `fd00:ec2::254`
   - URL 内嵌用户名密码、query、fragment、非 http(s)
   - 未登录且非回环来源
5. **不要**再把「不在 allowlist」当成已登录用户自有 URL 的硬失败。allowlist 只留给未带用户 URL 的默认/匿名路径（若仍保留默认 URL）。
6. 默认 `_MODELS_FETCH_DEFAULT_URL` 不要再用解析不了的 `host.docker.internal`；已登录且无 URL 时用 `http://localhost:8317/v1`。

## Key 回退（同卡必改，否则空输入框仍 401）

当前 `api/main.py` `fetch_available_models` 读了不存在的字段：

- 错：`user_cfg.custom_base_url` / `user_cfg.llm_api_key_ciphertext`
- 对：`user_cfg.backend_url` / `user_cfg.api_key_encrypted`
- provider 错：`api_key_ciphertext`；对：`api_key_encrypted`

请求 `api_key` 为空时按序：provider 解密 Key → `user_llm_configs.api_key_encrypted` → 环境 `TA_API_KEY`（只读，不写库）。禁止打印 Key。

## 允许修改

- `api/main.py`（仅 models fetch 相关函数/`fetch_available_models`）
- `tests/test_models_fetch_ssrf.py`（追加/改写与本契约冲突的旧 allowlist 用例，必须说明）
- 如有 `tests/test_models_fetch_ssrf_allowlist.py` 一并改

禁止改：`.env`、providers 表数据、role_bindings、用户 Key、Prompt、资金流、主干、部署。

## 测试（`.venv310` / Python 3.10）

至少覆盖：

- 已登录 + `base_url=http://100.65.130.33:8317/v1`：host 闸门通过（mock 连接），不因 allowlist 缺失失败
- 已登录 + 输入框 URL 与库内 URL 不同：用输入框
- 云元数据仍拒绝
- 字段回退读 `backend_url` / `api_key_encrypted`
- 未登录非回环仍 401
- 旧 allowlist 单测若断言「无名单即全拒绝」，改为「无用户 URL 才拒绝 / 有用户 URL 放行」

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_models_fetch_ssrf.py tests/test_models_fetch_ssrf_allowlist.py -q
TUSHARE_TOKEN='' env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests -q
python -m compileall -q api tests
git diff --check
```

## 交付

独立 worktree，父提交 `0b10041`。推送分支精确 SHA。不合主干、不重启。不得 @项目调度助手。
