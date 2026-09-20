# 修订后 Task 4：确定性个股实体解析

## 规范

唯一规范：`docs/social_data/implementation_plan.md`（已在 Git）§5.3 + Task 4。

## 基线

- 主干 tip：`d7b45713db31216d847b3b48e7aca79360b4b809`（`origin/codex/dav-4-p2a-trunk`）
- 已合入：Task 2 contracts @ `ea3b3a3`，Task 3 importer @ `a25d404`
- 隔离分支建议：`agent/cursor/social-b4-entity-resolver`，从上述 tip 开出
- **禁止**从脏宿主树开工；禁止改 `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`、`data_collector.py`、prompts、`evidence_verifier.py`

## 只做

1. 新增 `tradingagents/dataflows/social/entity_resolver.py`
2. 新增 `tests/test_social_entity_resolver.py`（先写可失败测试）
3. 最小改动让 importer 写入 `social_entity_mentions`（若当前未写）
4. 可最小改 `contracts.py` / `__init__.py` 导出；不要顺手做 Task 5 provider

## 行为要求（§5.3）

- 完整代码置信度 1.00；标准名 1.00；唯一别名 0.95；专属关键词无冲突 0.90
- 行业/概念词只打 topic，**不绑个股**
- 多股票同文分别映射
- 全角/半角 NFKC 归一
- **禁止**从 `api.main` 反向导入
- 确定性、可单测；词典可内置小样本，勿拉用户配置/DB

## 验收

```bash
env -u PYTHONPATH .venv310/bin/python -m pytest -q --tb=short \
  tests/test_social_entity_resolver.py \
  tests/test_mediacrawler_importer.py \
  tests/test_social_contracts.py
```

## 交付

- 精确 pathspec 单关注点 commit：`feat(social): add deterministic equity entity resolver`
- 推远端隔离分支；issue 回帖：**精确 SHA**、文件列表、pytest 摘要
- **不要自行 FF 主干**；等独立审核 + 主管放行

## 边界

- MediaCrawler 仍是外置采集器；本任务不启动爬虫
- archive 只追加 mentions 行；禁止 UPDATE/DELETE snapshots
- 昵称/Cookie/token 不得进入 archive 或日志
