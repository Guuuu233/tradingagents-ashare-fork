# DAV-256 阶段二补齐 3 个缺失行业动态产业链

基线：`target/codex/dav-4-p2a-trunk@0b10041f9e68b5d0116b76c36cd629acb365f10d`

《项目加强方案》阶段二要求 5 行业动态采集。当前 `tradingagents/dataflows/industry_linkage.py` 的 `INDUSTRY_LINKAGE_MAP` **只有**：

- 消费电子
- 新能源车

静态知识库 `tradingagents/knowledge/industry_linkage.py` 已有 27 行业，但动态采集未铺开。必须补：

1. 半导体（映射至少 `688981.SH` 中芯国际、`603501.SH` 韦尔股份）
2. 石油化工（`601857.SH` 中国石油、`600309.SH` 万华化学）
3. 金融地产/银行（`600036.SH` 招商银行、`000002.SZ` 万科A）

## 允许修改

- `tradingagents/dataflows/industry_linkage.py`
- `tradingagents/dataflows/providers/industry_linkage_provider.py`
- `tradingagents/graph/data_collector.py` 的行业映射（若已有函数则原地扩展，禁止 `_v2`）
- `tests/test_industry_linkage.py` 或现有等价测试（追加，不整文件替换）

禁止改 Prompt 大段、`.env`、providers 表、用户配置、主干、部署、真实 LLM。

## 验收

1. 独立 worktree，父提交必须是 `0b10041`
2. 5 个行业都能 `get_industry_linkage` 返回非空结构；缺指标显式「数据缺失」，禁止臆造数值
3. DataCollector 能把上述 6 只股票映射到正确行业
4. 免费源（Yahoo/LME 等）失败必须 typed gap，不得假成功
5. `.venv310` 定向测试 + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check
6. 推送独立分支精确 SHA，不合主干

两阶段分析拓扑（DAV-195）不在本卡。不得 @项目调度助手。
