# Social B2/B3/B4 当前主线重放计划（仅计划，不合入）

## 当前基线

- trunk：`8e49a809333868da73f98bd44bb070f0831bf23e`
- B2：`92cf2abdf1a970535de78fadc5b78fbac869defc`，基线旧、非 trunk 后代
- B3：`e85fc31184645145083101f852f0e360ecb9fe16`，B2 后续、非 trunk 后代
- B4：`aed3b9727910220be562b7f6d05778d3580ba9b5`，与 B3 共享 importer 文件、非 trunk 后代

## 串行顺序

1. B2 contracts：从最新 trunk 重放，白名单为 contracts/archive_schema/social init + B2 tests；完成后代码审核与专项测试。
2. B3 importer：以 B2 新 SHA 为父，从最新 trunk+B2 重放；处理 importer 与 contracts 依赖；完成后代码审核与专项测试。
3. B4 entity resolver：以 B3 新 SHA 为父，从最新 trunk+B2+B3 重放；处理 importer/init 文件交集；完成后代码审核与专项测试。
4. 三者组合树专项 social regression；确认与 H1b 两个白名单文件无交集后，才进入主线合入决策。

## 禁止项

- 不直接 cherry-pick 旧 SHA 到 trunk。
- 不让 B3/B4 同时写 `mediacrawler_importer.py`。
- 不与 H1b `research_manager.py` / `test_expectation_revision_contract.py` 混合提交。
- 不使用 Cookie、不启动真实采集、不启用 active。
- 不把专项 162 passed 写成真实外部采集成功；它只证明代码/fixture 契约。
