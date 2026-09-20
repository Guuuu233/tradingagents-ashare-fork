# Track A7：报告写路径持久化 industry（无 schema 列）

来源：Gate4 后 D-007 门槛诚实性。A6 已能从嵌套 JSON 提取 industry；实测门槛常 **行业数=0**，因 completed 写路径未把可验证行业写入 `result_data`。

## 基线

主干 tip：`503aa1606161918ba25e77dad40ec2e8df652461`（开工前 `git ls-remote`）。  
分支建议：`agent/dev2/a7-persist-report-industry`。  
**单关注点 commit。**

## 只做这件事

1. 在报告落库 / 更新 completed 路径（`api/services/report_service.py` 等既有写路径），调用已有 `extract_report_industry` / collector 侧行业映射，把可验证行业写入 `result_data` 既有槽位（优先 `instrument_context.industry` / 与 A6 提取顺序一致的字段）。
2. 缺失 → 不写或显式 `null`；**禁止**硬编「未知行业」或默认行业。
3. 可选：离线 `--dry-run` 回填脚本，幂等更新历史 completed v2 行的同一字段（不改其它字段）。
4. `verify_h1b_gates` / `extract_report_industry` 对 fixture 能稳定数出 ≥1 个真实行业（测试夹具，不依赖生产库编造过关）。

## 明确不做

- **不加** `ReportDB.industry` 数据库列 / migration（改 schema 须 David 另批；本卡禁止）
- 开 `credit_weighting_enabled` / 部署 / Gate4 以外社交大改
- 脏文件：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- `/v1/calibration` 大改（另卡）

## 验收

- 定向 pytest：写入后 `extract_report_industry` 非空；缺失保持 None
- 门槛夹具：多标的不同行业时 `unique_industries` 随真实数据增加，不靠硬编
- 先 push；40 位 tip → `in_review`；D-010

## 权威

`work/2026-08-27-unified-final-plan.md` A6 后续；D-006/D-007；A6 `extract_report_industry`。
