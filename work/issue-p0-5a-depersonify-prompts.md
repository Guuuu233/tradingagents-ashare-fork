# P0-5a：smart-money / 量价 prompt 去人格化（VWMA≠主力成本；滞涨=candidate）

## 目标

D-009 / 审计稿 §P0-5 的**第一刀**：改掉生成侧把订单分组与量价统计人格化成「主力意图 / 假摔洗盘 / 主力成本」的 prompt。VWMA 只能是成交量加权价格；高位放量滞涨只能是 `event_candidate`；无席位/账户身份证据不得写机构/公募/外资身份结论。

本卡**不做** confirmation_state → WAIT 硬闸（P0-5b）、**不做** capitulation 特征工程（P1-2）、不是社交、不是部署。不改辩论 3/1，不开 `credit_weighting_enabled`。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`
- **新建**隔离分支，例如 `agent/dev2/p0-5a-depersonify-prompts`
- **不要**在 `agent/dev2/p0-4b-claim-cluster` 或宿主旧分支上继续堆
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §P0-5、§4.1 歌尔、§5「VWMA/主力成本」
- `work/2026-08-27-decision-semantics-workflow.md` P0-5

## 已复现（主干 `18e73bd`）

1. `tradingagents/prompts/zh.py` `smart_money_system_message`（约 610–636）：
   - 明确「主力真实意图」「建仓/洗盘/派发」
   - 「主力净流出 + 急跌 + 缩量/长下影 = 震仓洗盘信号（假摔洗盘）」
   - 「主力成本区间测算」
2. 同文件 `volume_price_system_message`（约 638–733）：把「局内人」当可推断主体；威科夫阶段直接映射买卖行为；「局内人在震仓洗盘」。
3. `research_manager_prompt` 约 282 行仍写「主力真实意图建仓/洗盘/派发」。
4. **回归锁是反的**：`tests/test_analyst_prompts_deep_reasoning.py::test_smart_money_system_message_deep_framework` 仍 `assert` prompt 含 `建仓/派发/洗盘/震仓`——这正是本卡要翻掉的洞，不是保护契约。

## 行为契约

改 **原路径** prompt 文本（禁止 `_v2` 平行模板）。

### smart_money

| 禁止（不得作为数据结论） | 允许 |
|---|---|
| 主力成本区间 / 吸筹价 / 筹码成本区 | VWMA = 成交量加权价格 / 参考带 |
| 假摔洗盘、震仓（当作已证实） | 可观察组合：净额方向 + 换手 + K 线形态 → 标为 `event_candidate` / 待验证假设 |
| 无席位证据时写「公募/外资/机构建仓」 | 大单/超大单 = 订单分组统计；龙虎榜有席位字段时才能写席位名，仍不得外推账户身份 |

默认 `ownership_inference=false`：正文或 VERDICT/机读块中不得把所有权归因写成事实。没有持仓/席位身份证据时，禁止输出主力成本类断言。

### volume_price

| 禁止 | 允许 |
|---|---|
| 「跟随局内人」「局内人是唯一能控制价格的群体」作为操作指令 | 供需/量价可观察描述；威科夫阶段名若保留，必须标明为**教学假设 / 待验证**，不得写成数据字段结论 |
| 高位放量滞涨 → 洗盘/派发（确定） | `high_volume_stagnation_candidate`（或等价中文「高位放量滞涨候选」）+ 构成字段 |
| 无 volume 仍推断吸筹/派发 | 继续 fail-closed（已有条款保留并加强） |

### research_manager

删掉或改写「主力真实意图建仓/洗盘/派发」辅助确认句；可改为「量价与资金流的可观察预期差（仅作解释，不得当作身份结论）」。不要大改五步裁决与 DAV-336 七分析师列表。

### 英文

若 `en.py` 有对等人格化措辞，同步改；没有可不动。

## 不要改

- `decision_status` confirmation → WAIT（P0-5b）
- VPA 指标计算 / capitulation 特征（P1-2）
- 资金流 selection（P0-4a）、claim cluster（P0-4b）
- 财务 period_kind、社交 DAV-460
- 辩论 3/1、`credit_weighting_enabled`
- 受保护脏文件、数据库 schema
- 不要为了去人格化删掉龙虎榜/分单等**可观察**数据要求

## 允许修改

- `tradingagents/prompts/zh.py`（`smart_money_system_message`、`volume_price_system_message`、必要时 `research_manager_prompt` 相关句）
- `tradingagents/prompts/en.py`（仅对等句）
- `tests/test_analyst_prompts_deep_reasoning.py`（**翻转** smart-money 对建仓/洗盘的正向断言）
- 新建 `tests/test_prompt_depersonification.py`（推荐）

## 阅读纪律

1. 完整读 `smart_money_system_message` 与 `volume_price_system_message` 整段，再改。
2. grep `主力成本|假摔|洗盘|局内人|吸筹价|ownership`。
3. 改原路径。不要另起一套 prompt 键名。

## 测试（TDD）

先在 **`18e73bd` 上写会失败的测试**，再改 prompt。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_prompt_depersonification.py \
  tests/test_analyst_prompts_deep_reasoning.py \
  tests/test_adjudication_risk_prompts_deep_reasoning.py \
  -q --tb=short
```

必须覆盖：

1. **smart_money**：正文不得出现「主力成本区间」「假摔洗盘」作为操作/结论模板；必须出现 VWMA 作为「成交量加权价格」类表述（或等价）；`ownership_inference` / 无身份不得归因的约束可见。
2. **volume_price**：不得把「跟随局内人」当操作铁律；高位放量滞涨须是 candidate / 待验证；无 volume fail-closed 仍在。
3. **翻转旧测**：`test_smart_money_system_message_deep_framework` 不得再要求 prompt 必须含「洗盘」等确定语义；可改为要求含龙虎榜/分单等可观察项 + 去人格化约束。
4. **DAV-336**：七位分析师逐一列出仍绿。
5. 不要 `assert result is not None`。

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`
- 精确 pytest 数字（须在无 `pytest-asyncio` 的 `.venv310` 可复现）
- 不要写「彻底修复」；不要自行合主干
- 合入必须等 Cursor 评论同时出现完整 SHA 与「准予合入」
- **不准予部署**
- 不要 @项目调度助手催工
