## 目标

建立本轮全面审计修复的唯一远端 integration SHA，基线主干 `66cd7bf0238fae07d206d028915c7278d93dc466`，禁止直接推主干。

## 已终审候选

1. `7e7b4d234d8424001ac9ecdac8219cfc3b591c31`
   - 父：`9ec4ab9ce91661b7d0d91143ad07b62f497c8a81`
   - 已包含：DAV-320 七报告辩论协议、DAV-321 分析师深度质量闸、DAV-323 研究总监事实核验与裁决硬闸。
   - 终审全量：1723 passed。
2. `2a28d57286db3681963255cbb0bb0248e2dc7b72`
   - 父链：`66cd7bf -> c0a6be4(DAV-322) -> 2a28d57(DAV-326)`
   - 包含：双周期全失败状态修复、无效Token 401、验证码5次作废与频控。
   - 终审 PASS。
3. `9f3abfe7e5e0076c1dfea510ee497ace4e640308`
   - 父：`66cd7bf`
   - 量价四步深度契约、基本面-新闻并发承诺修正。
   - 终审 PASS、全量1680 passed。

## 必须人工语义合并的重叠

- `api/main.py`：7e7b4d2（manager_verdict/evidence持久化）与 2a28d57（dual lifecycle + auth）都改；必须同时保留三者，禁止择一覆盖。
- `tradingagents/prompts/zh.py` / `en.py`：7e7b4d2 的研究总监事实/自洽 Prompt + 72394d3 的七报告辩论占位符 + 9f3abfe 的量价四步与基本面并发承诺修正必须全部保留。
- 其他同名状态/测试文件按契约合并，不能回退 round_messages、manager_verdict、深度质量闸。

## 集成步骤

1. 从精确 `66cd7bf` 新分支。
2. 可先重放/合并 `7e7b4d2` 的完整树，再将 `66cd7bf..2a28d57` 与 `66cd7bf..9f3abfe` 作为语义补丁合并；不得带隐藏祖先。
3. 输出 ancestry/changed-files 矩阵，证明四条成果都存在。
4. 必跑 `.venv310`：
   - debate/manager/analyst-depth/quality/prompt/topology
   - dual-horizon/report lifecycle
   - auth/startup/SSRF/user isolation
   - industry linkage
   - 全量 `pytest tests/ -q`
   - compileall、diff-check
5. 创建远端 `integration/final-audit-20260822`，推精确 SHA；不得主干合入、不得重启、不得修改 `.env`/用户配置。
6. 真实代码符号抽查：round_messages、manager_verdict、EvidenceFactualTruthEvaluator、role depth ledger、Shibor/LPR、all-horizon failed、attempts、volume_price WhatNext 均必须在 integration tree。

## 交付

精确远端 SHA、父链、冲突处理说明、测试数、明确未合入/未重启/未上线。禁止 @项目调度助手。