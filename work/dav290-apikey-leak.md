# DAV-290 P0：models/fetch 匿名请求泄漏宿主 TA_API_KEY（安全修复）

**基线父提交：`cfc1e22ce8b060a18017acbfa8f4d92144df9cd5`。独立分支，不合主干。最高优先级，先于 DAV-288。**

## 已坐实的缺陷（Hermes 终审在宿主树全量回归发现）

在有 `.env` 的宿主环境跑 `pytest tests/test_models_fetch_ssrf.py` 出现 2 个失败：

- `test_anonymous_fetch_from_loopback_allowed`：期望转发 `api_key=""`，实际捕获 `'sk-ta-…'`
- `test_authenticated_fetch_from_non_loopback_uses_user_url_policy`：同上

根因链：`api/main.py` 导入时 `load_dotenv()` 注入 `TA_API_KEY` → `/v1/models/fetch` 处理链对未显式携带 Key 的请求把该环境变量 Key 附到**调用方指定的任意 base_url** 上。后果：本机任意进程可借 8000 端口把宿主 Key 外送到自建收集器；服务一旦绑定非回环地址即升级为远程凭据泄露。团队 checkout 无 `.env`，因此既有套件从未暴露此路径。

## 契约

1. 复现：checkout 后在目录放 `.env`（内容仅 `TA_API_KEY=sk-repro-leak`），跑上述两个测试确认红。
2. 定位 `api/` 内 models fetch 链路的环境变量 Key 回退点并移除该回退：fetch 端点的出站 Key 只允许来自「显式请求参数」或「账户已保存凭据」，任何环境变量兜底一律禁止。
3. 全仓 grep `TA_API_KEY`：逐处判定是否会把该值发往用户可控 URL；同类问题一并修。
4. 新增回归测试：monkeypatch `os.environ["TA_API_KEY"]="sk-test-leak"` 后断言两个场景捕获的 `api_key==""`（保证无 `.env` 的 CI 也能锁住该行为）。
5. 不改变合法流程：已保存 URL 的已登录用户 fetch、回环默认账号可用性等现有 passing 用例必须保持绿。

## 禁止

禁止动 `.env` 文件本身；禁止改 SSRF 校验策略语义（allowlist/is_global 逻辑）；禁止 `_v2`；禁止碰 providers/role_bindings 数据。

## 验收

- 带 `.env` 复现环境：`tests/test_models_fetch_ssrf.py` 全绿。
- 干净环境：定向套件 + `compileall` + `git diff --check` 全绿。
- 附 grep 证据清单（每处 `TA_API_KEY` 的处置说明）。推送独立分支精确 SHA。
- 不得 @项目调度助手。
