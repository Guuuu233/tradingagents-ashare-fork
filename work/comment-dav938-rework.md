## DAV-978 复审结论：打回，返修要求

代码审核员2 对候选 `cda447a519acafe836a0252f5c600ab7e0d851de` 同 SHA 只读复审结果为 **打回**，须返修后重新交付。

### 必须修复（🟡）

`frontend/src/pages/Dashboard.tsx:37-41`：`analysis_status` 仅交给 `parseDecisionAction` 解析，`PARTIAL` 拿不到 action，随后落到 `trade_action`/`decision`，导致：

- `analysis_status=PARTIAL, trade_action=BUY, decision=BUY` → 实际输出 `增持`（红色）
- `PARTIAL + HOLD` → 显示 `持有`

`PARTIAL` 是非完整、不可校准的分析状态（`tradingagents/agents/utils/decision_status.py:72-82`）。现有页面已统一按观望处理：`frontend/src/pages/Reports.tsx:46-47` 优先显示观望，`frontend/src/components/DecisionCard.tsx:48-55` 优先解析为 `watch`。控制台把同一份部分失败报告显示成方向性/持有动作，违反跨页面语义一致性，可能误导交易解释。

### 返修边界

1. 在状态优先级中将 `PARTIAL` 明确映射为 `watch`（或抽取共享解析能力复用，不得新建第二套映射表）。
2. 补 `PARTIAL + BUY` 与 `PARTIAL + HOLD` 的回归断言。
3. **不得改写历史报告中的方向/动作值**；只改展示层。
4. 白名单不变：`frontend/src/pages/Dashboard.tsx`、`frontend/src/pages/Dashboard.test.tsx`。
5. 直接父必须是交付时的远端主线 tip（当前为 `b95a9b88c81e87a4da121f9945aedbe844fa04e0`，若期间主线前移则以新 tip 为准）。

### 已通过、无需重做的部分

`npm test` 18 文件 / 178 用例通过；`npm run build` 成功；白名单定向 ESLint 0 error；红队 RT-1/RT-2/RT-3/RT-4 全部通过（BUY/SELL 正确着色，WAIT/NO_TRADE/ABSTAIN 未被映射成买卖方向，null/空串/未知值无 `undefined`，既有中文值不二次翻译）。返修后须重跑上述测试与构建并贴精确数字。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
