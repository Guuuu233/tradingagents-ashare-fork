@资深开发2 **Gate4 / T15b 已授权**（用户明确「开 Gate4」）。

brief：`work/issue-p2-t15b-gate4-remove-legacy.md`

**父 tip**：优先等 DAV-542 把主干 FF 到 A5 `d2f8aa05579d0520abe942972889a82060ea65e7` 后再从主干拉分支；若你先开工，可直接以该 tip 为父建 `agent/dev2/p2-t15b-gate4-remove-legacy`。

独立单 commit 删 `legacy_proxy`；disabled → `not_applicable`；更新断言 legacy 的测试。禁止部署 / 开加权。先 push 再 `in_review`。
