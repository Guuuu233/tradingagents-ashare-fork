@资深开发2 开工：P2-T14 shadow/canary 守卫。

基线 tip `7876f1cd5c798382e03cf42210f6dc7c0d3bf565`。新建隔离分支。重点：修 M6（shadow 的 `source_mode` → `legacy_proxy`）+ 新建 `tests/test_social_rollout_modes.py` 锁四条契约。

完整 brief：`work/issue-p2-t14-social-rollout-gates.md`。
**先 push 再评论**（T13 教训）。交付：完整 40 位 SHA + pytest 数字 → `in_review`。不自行 FF / 不删 legacy / 不部署 / 不 @调度助手合入。
