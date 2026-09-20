# DAV-262 精确 SHA 独立终审 2e9e674 用户自有 URL 拉模型列表

只读。禁止改代码、`.env`、providers、role_bindings、主干、部署。

## 候选

- `target/agent/work2/dav-261-models-fetch@2e9e674ceff062b7b35ae010a954b61f7575c47e`
- 父提交必须是 `0b10041f9e68b5d0116b76c36cd629acb365f10d`
- 允许文件：`api/main.py`、`tests/test_models_fetch_ssrf.py`（若还有 allowlist 测试文件须在 diff 内并说明）

## 必须独立 checkout 该 SHA 后核对

1. 已登录请求体 `base_url=http://100.65.130.33:8317/v1` 不因 allowlist 缺失失败（单测/代码路径）
2. 输入框 URL 优先于库内 URL
3. Key 回退读 `api_key_encrypted` / `backend_url`，不再读不存在的 `custom_base_url` / `llm_api_key_ciphertext` / `api_key_ciphertext`
4. 云元数据仍拒绝；未登录非回环仍 401
5. 未改用户配置数据
6. `.venv310` 复跑 `tests/test_models_fetch_ssrf.py` + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check

PASS 或打回，列精确证据。不得 @项目调度助手。
