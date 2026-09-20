# DAV-938 候选 6659146715278b50ea7310be721287980cda1066 只读代码复审

请由 **代码审核员2** 对下面这个完整候选 SHA 做同一版本的只读复审。不修改、不合入、不部署。

## 版本边界（运维已用 `git ls-remote` 预先核实，仍请自行回读）

- 实施卡：DAV-938
- 候选完整 SHA：`6659146715278b50ea7310be721287980cda1066`
- 精确远端 ref：`origin/agent/1/01a0a2b8-dav-938-r2`
- 直接父：`b95a9b88c81e87a4da121f9945aedbe844fa04e0`（= 交付时远端主线 tip）
- 目标主线：`origin/codex/dav-4-p2a-trunk`

开工前须 `git ls-remote origin` 回读上述 ref 与 SHA，并核 `merge-base`。**注意**：本卡给出的 ref 拼写已核实为远端真实存在；若回读与本卡不符，请在评论指出具体差异，不要据此重写代码。

## 严格白名单（仅 2 个文件）

1. `frontend/src/pages/Dashboard.tsx`
2. `frontend/src/pages/Dashboard.test.tsx`

越出白名单、父提交不一致、SHA 不一致或工作树非 clean → 打回。

## 本次修复的背景（上一版为何被打回）

上一候选 `cda447a5` 在 `Dashboard.tsx:37-41` 处，当 `analysis_status=PARTIAL` 且拿不到 action 时会回落到 `trade_action`/`decision`，导致 **`PARTIAL + BUY` 被显示成「增持」**——把一次未完成的分析呈现为可执行的买入建议，属误导性 UI。本候选应使 `PARTIAL` 优先映射为「观望」。

## 复审重点

- `PARTIAL` 的判定是否**优先于** `trade_action`/`decision` 回落链，且不因大小写、空白、`null`/`undefined` 而失效。
- 是否与既有实现对齐一致：`frontend/src/pages/Reports.tsx:46-47`、`frontend/src/components/DecisionCard.tsx:48-55`。三处语义必须一致，否则同一份数据在不同页面显示不同动作。
- 是否误伤正常路径：`COMPLETED + BUY` 仍须显示「增持」，不得被新分支吞掉。
- 是否存在其他未覆盖的 `analysis_status` 取值（如 `FAILED`、`RUNNING`、未知值）落入错误分支。
- 新增断言是否真正覆盖回归点，而非仅做快照。

## 红队场景（须逐条实跑并贴实际输出）

- RT-1 `PARTIAL + BUY` → 必须「观望」，不得「增持」
- RT-2 `PARTIAL + HOLD` / `PARTIAL + SELL` → 必须「观望」
- RT-3 `PARTIAL` 且 action 字段缺失 / 为 `null` → 必须「观望」，不得崩溃或显示空白
- RT-4 `COMPLETED + BUY` → 仍须「增持」（防误伤）
- RT-5 未知或异常 `analysis_status`（如 `FAILED`、小写 `partial`、空字符串）→ 说明落入哪个分支、是否安全
- RT-6 与 `Reports.tsx` / `DecisionCard.tsx` 对同一组输入的显示是否一致

每条须含：输入、预期、实际、执行命令、候选 SHA、结论。

## 回归要求（纯前端候选）

- `npm test`：贴文件数与用例数精确数字及退出码。
- `npm run build`：贴是否成功、modules transformed 数与退出码。
- 确认 diff 中**无任何后端 / Python 文件**，据此说明为何不跑 Python RT-FULL。
- 交付方自报为「18 个文件、180/180 通过，build 成功 2785 modules，退出码 0」，请独立复现并比对，不得直接引用其数字。

## 禁止项

不修改代码、不合入、不部署、不重启服务、不写生产库、不改个人配置。

## 交付格式

路径/行号证据、精确测试数字、明确评级：**PASS / 有条件通过 / 打回 / 审核无效**。任一红队场景未实跑 → 不得 PASS。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
