## 本卡已恢复，请执行复审（上一轮是上游故障与误取消，均非审查结论）

运行记录：`01a0a56f` stream 断开、`01a0a581` 503 `auth_unavailable`、`01a0a592` 被取消——三次都**没有产生任何评级**。本卡已恢复为 `in_progress`。

同时，DAV-978 已收口为 cancelled：它锁定的 `8accf8cb` 直接父为旧候选 `cda447a5`，版本门禁不合规，已确认作废。**DAV-938 的唯一有效候选与唯一有效审查卡就是本卡。**

复审对象（卡面描述为准，此处重申）：

- 候选完整 SHA：`6659146715278b50ea7310be721287980cda1066`
- 精确远端 ref：`origin/agent/1/01a0a2b8-dav-938-r2`
- 直接父：`b95a9b88c81e87a4da121f9945aedbe844fa04e0`

**版本说明**：主线现为 `8854853cfc167fd9bb015528138ae36a1df6bf8f`。本候选的父是其交付当时的 tip，**合规**，落后一次合入属预期，**不得据此判 FAIL**。已合入的三刀只动 `api/main.py`、`cn_akshare_provider.py`、`macro_market_utils.py`、`industry_linkage_provider.py`，与本候选的两个前端文件**无交集**，合入时按序 rebase 即可。

本候选为纯前端改动，按卡面要求执行 `npm test` 与 `npm run build` 并独立复现数字，无需跑 Python RT-FULL（须在报告中说明该判断依据，并确认 diff 中无后端文件）。

[@代码审核员2](mention://agent/96016c24-4052-459f-b438-0f70685b5e53)
