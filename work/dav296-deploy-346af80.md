# DAV-296 部署 346af80（RAG 词表外置 + 交易日 fixture）

主干已核验：`target/codex/dav-4-p2a-trunk@346af8049ddd2a0d8c9c69f3147732de0537ec0a`
父链：`cf0932e` → `f209d85`（DAV-288 词表外置，终审 PASS）→ `346af80`（DAV-288 fixture，DAV-295 终审 PASS）。组合树全量回归 **1604 passed / 0 failed / 1 skipped**。

## 保护

禁止 reset --hard / clean -fd；禁止改 `.env`、providers、role_bindings、持久轮次。WIP 先保护再 FF。

## 部署

1. 确认远端主干仍为 `346af804…`。
2. active reports（pending/running）= 0。
3. 宿主 FF 到该 SHA。
4. kill 旧 PID，lsof :8000 清空。
5. 完整 no_proxy + DATABASE_URL + `.venv310` 重启 uvicorn。
6. healthz 必须 = `346af804…`。

## 冒烟

- 宿主树：`pytest tests/test_sina_historical_fund_flow.py -q` 必须 18 passed；`pytest tests/test_knowledge_rag.py -q` 必须全绿。
- `tradingagents/knowledge/rag_vocab.json` 存在且可加载（缺失时应 warning 回退，不得崩溃）。
- 持久配置仍 3/1；models 列表拉取正常；historical_cases 表可查询。

不得 @项目调度助手。立即执行。
