# DAV-268 精确 SHA 独立终审 b1fa020（256+258 叠在 2e9e674 上）

只读。禁止改代码、`.env`、主干、部署。

## 候选（不要审旧树 520f871）

- `target/integration/dav256-dav258-on-2e9e674@b1fa020a0b511be9266651cd69dd2eb091d84cc8`
- 父链必须是：
  `2e9e674ceff062b7b35ae010a954b61f7575c47e`
  → `9852c7085e95c0cb7175062e91ca875801aa0bbf`（DAV-258 知识库）
  → `b1fa020a0b511be9266651cd69dd2eb091d84cc8`（DAV-256 三行业）
- DAV-265 审的是旧树 `520f871`（基于 `0b10041`），**不能**当作本 SHA 的 PASS。

## 必须独立 checkout 该 SHA

1. `git merge-base --is-ancestor 2e9e674 b1fa020` 成立；ahead 2 / behind 0
2. 相对 `2e9e674` 的 diff 只有产业链/知识库相关文件，不得含 `api/main.py`
3. blob 与源提交 `a3f9d91` / `0111b1a`（或 098f8bf/520f871 对应文件）一致
4. INDUSTRY_LINKAGE_MAP 含消费电子、新能源车、半导体、石化、金融地产；6 只映射股票正确
5. 600519 基本面知识库注入测试在本树上通过
6. `.venv310`：定向知识库+产业链+models_fetch + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check

PASS 或打回，列精确证据。不得 @项目调度助手。
