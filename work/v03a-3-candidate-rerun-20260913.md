# V-03a 候选提交后只读复跑记录

日期：2026-09-13

## 固定对象

- 候选：`c36fbc525355a880835db8c43d65fc445b6bdc95`
- 直接父提交：`7cd1a8523e5f879712739576d7b6f101f2c1ff2c`
- 解释器：`env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20）
- 命令：`scripts/run_v03_return_measure.py`
- 性质：只读离线复放；不调用真实大模型，不写生产库，不部署，不重启服务。

## 运行结果

- 生产库运行前后 SHA256：`94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`，字节级一致。
- 生产库 `quick_check=ok`、`integrity_check=ok`。
- cutoff `2026-09-08`：`total=317`、`completed=231`、`failed=86`；当前 live 口径为 `318/232/86`，后者未进入 cutoff 测量。
- 副本 SHA256：`d5ecd2137fc049062aa43b1d1f5e59b467ca9cea8b7a1680b65f289e3875cc3d`。
- 231 条报告进入离线测量；DEV=0、HISTORICAL_OOS=210、FORWARD_OOS=0；回归标的隔离 21 条；7 个消融变体共享同一快照哈希。
- 25 字段审计表生成 231 行；报告继续标记“半成品基线,非定性判断”。

## 产物指纹

- `work/v03_snapshot_manifest.json`：`3687e644de58e86361de539a383065f8e2251f5366ac99c7be29f2be55074802`
- `work/v03_audit_records.json`：`c8219c259708a417f790cc45ff6c7d5c6a40afa73679ba298c4831b238a05622`
- `work/v03_ablation_summary.json`：`0c0b0c6e05f7d95f2a4ecff45514fd9f9e181f6875c0c3c670b644aed2c2065a`
- `work/v03_return_measurement_report.md`：`b2fe647f58d2edf2a23265bb4c94a37ea32a8468c8e26684ca023095591a49c1`
- `work/v03_return_measurement_report.json`：`c72dea585c88b99e9dfef1c6bcf3f000fb40d56b7b11119f078dd757e290a368`

## 待审查的 provenance 差异

- 复跑产物中的 `code_sha` 已为候选完整 SHA `c36fbc525355a880835db8c43d65fc445b6bdc95`。
- 产物中的 `running_service_sha` 仍由代码常量填为 `a6d4540feaa8043ff36b0607a31c1d2d5f004149`。
- 同时现场 `GET http://127.0.0.1:8000/healthz` 返回运行服务 `a227cdc3bb466edf2e910419cb6013cfc021d309`。
- 该差异未在红队复核中被列为缺陷，现转交代码审核员判定：它是冻结基线字段的有意语义，还是需要修复的来源追溯错误。在裁定前，不把这份报告宣称为“运行服务版本已正确回读”。
