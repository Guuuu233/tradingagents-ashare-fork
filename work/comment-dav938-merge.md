## ✅ 已合入主线

`origin/codex/dav-4-p2a-trunk`：`8854853` → **`331a32284b0b62599f1cf76806d09147fa680a4c`**（快进，无 merge commit）

- 候选：`6659146715278b50ea7310be721287980cda1066`（`origin/agent/1/01a0a2b8-dav-938-r2`）
- 合入后 SHA：`331a322`
- 审查：DAV-985 ✅ PASS（RT-1～RT-6 全过；18 文件 180/180；build 2785 modules；ESLint 退出码 0）
- 白名单：`frontend/src/pages/Dashboard.tsx`、`frontend/src/pages/Dashboard.test.tsx`

### 运维独立核验

无法独立复现 npm 数字：本机 `frontend/node_modules` 为空（0 项），安装前端依赖需联网且代价过大，因此**测试数字采信 DAV-985 的审查证据**，此处如实声明。运维改为做静态语义核验，重点是卡面要求的三处一致性：

`getDashboardDecisionDisplay` 的判定链为 `analysis_status === 'PARTIAL' → watch`，优先于 `trade_action`/`decision` 回落，与两处基准实现逐条对齐：

| 输入 | Dashboard（本次） | DecisionCard.tsx（基准） | Reports.tsx |
|---|---|---|---|
| `PARTIAL` | `watch` | `watch` | `watch` |
| `INVALID_RUN` / `DATA_ERROR` | `invalid`（经 `parseDecisionAction`） | `invalid` | `invalid` |
| `ABSTAIN` | `no_trade`（经 `parseDecisionAction`） | `no_trade` | `no_trade` |
| `COMPLETED` | 落回 `trade_action` | 同 | 同 |

`PARTIAL` 采用精确相等匹配，与 `DecisionCard.tsx:50` 的 `status === 'PARTIAL'` 语义一致（如 `PARTIAL_SUCCESS` 之类不会命中，属既有约定，非本次引入）。原缺陷 `PARTIAL + BUY → 增持` 已消除。

因 diff 中无任何后端 / Python 文件，不跑 Python RT-FULL 的判断成立；对主干 Python 失败集合无影响。

### 未触碰

未部署、未重启、未写生产库。`data/tradingagents.db` SHA256 合入前后一致 `94d2f6740db4f206...`。

### 在途候选须重绑父提交

主线已推进到 `331a322`，以下候选交付时须以此为直接父：
- DAV-941 候选 `ae54e52d`（父 `8854853`，审查中 DAV-977）—— 仅动 `tradingagents/knowledge/historical_cases.py`，与本次前端改动无交集，合入时 rebase 即可
- DAV-946 R3（返修中）
