## 精确基线

- 目标主干/宿主/运行服务：`ccc4c53a4985f8db32353586e6cb4317aa34f8cd`
- P1-M已闭环：最终全量`1899 passed, 1 skipped, 0 failed`；真实报告protocol/flags/metrics、legacy 6消息、日志关联、3/1均通过。
- 顶层施工规格：`/Users/davidliu/Downloads/TradingAgents-AShare-v2-完整详细施工实施规格-2026-08-24.md` 第7节。

## 阶段目标

实现默认关闭、请求级可启用的v2三段式后端协议，不修改setup.py图拓扑：
1. opening：Bull/Bear双盲独立立论，每方2-3 claims、至少3战场；
2. challenge：只攻击对手claim，new_claims为空，challenge证据过统一verifier；
3. tiebreak：仅必要时每方一次，否则`tiebreak_skipped=true`并进总监；
4. 总监输出分歧地图并逐条处置challenge；未验证fatal不得否决claim；
5. legacy flag关闭时6条流程逐字兼容；持久配置仍3/1。

## 串行子阶段

- B1：请求级启用、stage schema/记录、opening协议与双盲输入隔离、battlefield。
- B2：challenge结构、校验、存储与证据核验。
- B3：tiebreak路由、总监参数化、dispute map、belief trajectory/degenerate。
- B4：精确SHA组合、独立复审、全量、合入部署、三只全新标的3/3验收。

同一代码树仅一个writer。B1/B2/B3必须基于上一阶段远端精确SHA；子阶段候选不单独合入或部署。前端P1-FE与shadow P1-S暂不启动。

## 红线

禁止修改持久3/1、用户模型/绑定/providers/backend URL/API Key；禁止自动换模；禁止弱化协议闸、证据闸、自洽闸；禁止DB扩表、前端、数据provider；禁止setup.py拓扑变化。所有真实启用只用单次request `config_overrides`，结束后核DB仍3/1。

## 最终硬闸

Bear opening泄漏Bull本场claim=0；opening双方合法率100%；challenge new_claims非空=0；challenge evidence status=100%；未验证fatal否决=0；tiebreak最多一次；legacy回归0；宿主全量0失败；服务SHA=trunk；三只新标的结构硬闸全部通过。
