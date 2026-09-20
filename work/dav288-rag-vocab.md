# DAV-288 P2：RAG 检索词表外置配置

**基线父提交：`cfc1e22ce8b060a18017acbfa8f4d92144df9cd5`。独立分支，不合主干。**

## 目标（团队终审遗留技术债）

`tradingagents/knowledge/rag.py` 的停用词/同义词/金融专有名词集合（`_KNOWN_FINANCE_EN_CODES` 等）硬编码在源码，无法热更新。

## 契约

1. 词表抽到独立配置文件（如 `tradingagents/knowledge/rag_vocab.json` 或 py 常量模块），启动加载、缺失时回退内置默认（行为不变）。
2. 支持环境变量指向外部文件覆盖（如 `TA_RAG_VOCAB_PATH`），加载失败回退默认并记日志。
3. 检索打分逻辑（BM25/TF-IDF 权重、倒排索引结构）**一行不改**；纯数据外移。
4. 禁止碰 `.env` 实际文件、providers、role_bindings、`INDUSTRY_LINKAGE_MAP`。

## 验收

- `tests/test_knowledge_rag.py` 56 项全绿不改动断言语义；新增词表加载/回退/覆盖测试。
- `.venv310` 定向测试 + `compileall` + `git diff --check`；推送独立分支精确 SHA。
- 不得 @项目调度助手。
