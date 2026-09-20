# 独立审核：Confirmation Gate claim 生命周期（卡A）

**只读。** 等实现卡贴出完整 40 字符候选 SHA 后再审。

## 基线

- 父 tip：`c72dd7b6098297efbec80931dda8bf509c8d8709`
- brief：`work/2026-09-04-confirmation-gate-lifecycle-dispatch.md`

## 审核要点

1. 是否仍把「rejected + deterministic reject」的非核心 claim 扫进 `global_fatal`（回归 = 打回）。
2. focus/adopted fatal 是否仍 WAIT；rejected+adopt 是否 ABSTAIN/NO_TRADE。
3. 有无按多空立场写死「对手方不阻断」。
4. 测试是否覆盖美的 fixture + bull/bear 对称 + 歌尔类未确认核心。
5. 单关注点；未部署；未开加权。

书面给出 ✅通过 / ⚠️有条件通过 / ❌打回（含路径与行号）。勿改实现分支。
