# 最新报告主力资金缺口：新算法源恢复

## 用户现象

最近一条真实报告：

- report_id：`6bef7739281a4b57a9920438859fd9e7`
- symbol：`601398.SH`
- trade_date：`2026-08-14`
- status：`completed`

报告不是完全没有资金流文本，但结构化证据显示：

- `fund_flow_individual`：`sina_historical` / `legacy_web_algorithm`，仅旧新浪 Web 参考；
- `consensus.status=data_conflict`；
- `reason_code=no_new_algorithm_source`；
- `direction_allowed=false`；
- `fund_flow_board`：`provider call failed`；
- `smart_money_report` 正确阻断主力增持/减持方向。

宿主 `.venv310` 对当前 target trunk 的真实调用 `CnAkshareProvider().get_individual_fund_flow("601398.SH", "2026-08-14")` 约 5.6 秒返回新浪历史 fallback；不能把该 fallback 冒充新算法组成功。

## 固定基线

- target trunk：`codex/dav-4-p2a-trunk@cb9c62e6be92f18aa15cf5a513f9430d0409bf0b`
- Python：宿主 `.venv310` / 3.10.20
- 回归标的：`601398.SH`、`002167.SZ`
- 回归日期：`2026-08-14`
- 允许代码范围：当前资金流 provider、资金流 evidence/ledger 相关已有测试；禁止改用户设置、providers/API Key、数据库 schema、前端无关代码。

## 任务目标

在当前 trunk 基线上恢复“可验证的新算法资金流证据”，而不是继续把新浪旧 Web 值写成主力结论：

1. 先审阅当前 `get_individual_fund_flow` 与 evidence contract；确认历史日期路径为何在 EM 失败后直接落到 Sina legacy，未形成新算法组来源。
2. 若已有经过语义审计的东方财富 direct 字段映射可复用，接入/修复最小 direct fallback：只在 envelope、rc、字段语义、交易日和实际 as-of 均通过时生成新算法 evidence；不得按数组位置猜字段。
3. 同花顺即时快照不得冒充 `2026-08-14` 历史收盘；没有历史 as-of 能力时必须 typed gap，并保留 attempted_sources/fallback_errors/final_source。
4. 新浪 Web 历史值继续单独标为 `legacy_web_algorithm`，只作参考，不能进入新算法 consensus 或驱动方向。
5. 失败不得返回空白或静默成功；报告必须保留结构化 gap、来源尝试链、requested_as_of、actual as_of、field、unit 和失败类别。

## 必测门禁

- 正常返回：至少覆盖 `601398.SH`、`002167.SZ` 的固定历史日期 fixture/contract。
- HTTP/envelope/rc/字段缺失或日期不匹配：明确失败并继续 fallback。
- 新算法源全部失败：输出数据缺口，`direction_allowed=false`，不得使用 Sina legacy 代替。
- 运行宿主 `.venv310` 定向资金流/日期套件、compileall、`git diff --check`。
- 交付必须推送远端分支并提供精确 branch/SHA、测试命令结果、未合入/未重启/未上线事项；完成评论使用真实 mention。

## 施工纪律

从该精确 trunk 开窄任务，不重放 DAV-119 长上下文，不切换用户模型/provider/API Key，不修改主干；同一代码树只保留一个 coder。真实 provider 在线不可用时，如实报告 blocked，不用新浪 legacy fallback 冒充新算法成功。
