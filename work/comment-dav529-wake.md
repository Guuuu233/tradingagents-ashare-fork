@资深开发2 开工：P2-MED2 — 拆分 `SocialArchiveProvider.fetch_records`（M1）。

基线 tip `0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`。新建隔离分支。行为零漂移；原路径拆分，禁止 `_v2`。

M5 仅有证据才改（另 commit）；无证据可跳过并说明。
**禁止**删 `legacy_proxy` / 开 Gate4 / 部署。

完整 brief：`work/issue-p2-med2-fetch-records-split.md`。
先 push 再评论。交付：完整 40 位 SHA + pytest → `in_review`。
