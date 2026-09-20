目标：同花顺大单与东财平级接入 + 大单/主力口径「参考可信度」（非绝对判断）

基线 tip（必钉）：`31c32f0f877e86fc3c06eb58a34b4dc08a453044`（`origin/codex/dav-4-p2a-trunk`）
分支：从 tip 新建 `codex/dav-fund-flow-lg-credibility`（或等价）；禁止脏 host 工作树。

权威说明（完整）：`work/2026-09-04-fund-flow-lg-credibility-dispatch.md`

用户产品语义（不可违反）：
1. 平台「主力资金」= 统计口径参考（大单代理），不是账户级主力真相。
2. 接入同花顺 `buy_lg_amount` 为与东财 `r0_net` 平级的大单证据；`netamount` 仅总资金旁证。
3. 不得因「r0_net + netamount 并存」整篇清空主力报告；改为参考可信度（同向↑、反向↓，可弱结合价量）。
4. 文案必须强调「参考价值」，禁止断言「就是主力流向」。

允许改：
- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tradingagents/dataflows/fund_flow_evidence.py`
- `tradingagents/agents/analysts/smart_money_analyst.py`
- 相关 tests（含更新被本语义改掉的旧 incomparable 断言）

禁止：部署；改 `credit_weighting_enabled`；自行 merge/FF；碰 `AGENTS.md` / `frontend/src/services/api.ts`；恢复历史 `fund_flow_board` 快照。

TDD：先红后绿。完成后 `in_review` + 完整 40 字符 SHA。D-010：无 Cursor「准予合入」不得合入。
