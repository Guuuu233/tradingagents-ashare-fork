# DAV-938 候选 `cda447a5` 只读代码复审

请由 **代码审核员** 对下面这个完整候选 SHA 做同一版本的只读复审。审查不修改、不合入、不部署；不要使用“独立代码审核员”。

## 版本边界

- 实施卡：DAV-938（控制台最近分析统一显示动作标签）
- 候选完整 SHA：`cda447a519acafe836a0252f5c600ab7e0d851de`
- 直接父 SHA：`b95a9b88c81e87a4da121f9945aedbe844fa04e0`
- 远端分支：`origin/agent/1/01a0a2b8-dav-938`
- 目标主线：`origin/codex/dav-4-p2a-trunk`，交付时 HEAD 为上述直接父

## 严格白名单

1. `frontend/src/pages/Dashboard.tsx`
2. `frontend/src/pages/Dashboard.test.tsx`

越出白名单、父提交不一致、候选 SHA 不一致或检出工作树非 clean，直接打回。

## 复审重点

- 控制台“最近分析”不得再把 `report.decision` 原样渲染为英文动作；必须复用既有统一映射（与 DAV-887 / DAV-914 同一 `localizeDirection` 口径），不得新建第二套本地化表。
- 本刀只改用户可见文本与颜色查找，**不得迁移、改写或回填历史报告中的方向/动作值**。
- 未知或缺失动作值必须有确定性兜底展示，不得渲染成 `undefined`、空白或误导性的中性词。
- 生产库现存英文动作（BUY 183 条 / SELL 111 条量级）必须能正确显示；`WAIT`/`NO_TRADE`/`ABSTAIN` 等非方向性状态不得被映射成方向性中文动作。

## 红队场景（须逐条实跑并贴输出）

- RT-1 `decision=BUY/SELL` → 正确中文动作标签与颜色。
- RT-2 `decision=WAIT / NO_TRADE / ABSTAIN` → 非方向性表述，不得显示为买入/卖出。
- RT-3 `decision` 为 null / 空串 / 未知值 → 确定性兜底，无 `undefined`。
- RT-4 已为中文的历史值 → 不重复映射、不出现乱码或二次翻译。

## 须独立复核的证据

- 前端测试：精确文件数与用例数（`npm test`），以及 `npm run build` 生产构建结果。
- 该刀不改后端，可不跑 Python RT-FULL；但须说明理由并确认无后端文件改动。
- 不得改动 bundle 产物、部署或重启服务；live bundle 验收留待发布门另行执行。

请只读检出并复测完整 SHA，给出路径/行号、精确测试数字和明确评级（PASS / FAIL）。
