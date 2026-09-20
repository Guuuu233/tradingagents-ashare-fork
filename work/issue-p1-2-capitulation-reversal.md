# P1-2：capitulation / reversal 候选特征 + 分层入场（不偷看 T+1）

## 目标

D-009 / 审计稿 §P1-2：在量价路径用**确定性特征**标出

```text
normal | high_volume_stagnation_candidate | capitulation_candidate
reversal_unconfirmed | reversal_confirmed | insufficient_data
```

并落实 `WAIT → confirmation → staged entry`：

- `capitulation_candidate`：**只观察/风险**，不得 BUY
- `reversal_confirmed` 且有独立方向证据：允许**小仓试探**（非满仓）
- 确认不足：维持 `WAIT`，**禁止**写成永久 `NO_TRADE`

本卡不做 P1-3 回测、不做新闻、不做社交、不部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `aa0742a5cbcdf79a46dbc24dd7d96e2186f0a714`
- **新建**隔离分支，例如 `agent/dev2/p1-2-capitulation-reversal`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要 FF 主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §7 P1-2、§4.1 歌尔滞涨/假摔
- `work/2026-08-27-decision-semantics-workflow.md` P1-2：candidate≠买入；确认后小仓试探
- 已合入 P0-5a：滞涨=candidate、禁止假摔洗盘确定性结论——本卡补**可机读特征**，不要把 prompt 人格化加回去

## 已复现（主干 `aa0742a5`）

1. `_compute_vpa_indicators`（`tradingagents/graph/data_collector.py`）主要吐文本指标串，**没有** `capitulation_candidate` / `reversal_*` 结构化标签。
2. P0-5a 只约束 prompt 措辞；执行层不会因「候选」自动 `WAIT`。
3. 若用「后续确认」却偷看 cutoff 之后 bars，会前视。

## 行为契约（锁死）

改**原路径**；禁止 `_v2`。

### 1. 确定性特征（优先扩 `_compute_vpa_indicators` 或同文件纯函数）

输入：cutoff 日及之前的 OHLCV（及可选换手）。至少算：

- 成交量 z-score（相对 lookback）
- 振幅 / 收盘位置（相对当日高低）
- 连续下跌天数（若有）
- follow-through：**只能用 cutoff 当日及之前已发生的 bars**；禁止用 T+1

输出结构化 dict（可附在 VPA 文本旁或 `market_data_context` / state 键），含：

| 字段 | 取值 |
|---|---|
| `volume_regime` | `normal` / `high_volume_stagnation_candidate` / `capitulation_candidate` / `insufficient_data` |
| `reversal_state` | `reversal_unconfirmed` / `reversal_confirmed` / `insufficient_data` /（无候选时可不写或 `none`） |
| `features` | 数值特征摘要（z-score 等），供测断言 |
| `as_of` / `cutoff` | 明确日期 |

阈值用具名常量；缺列/样本不足 → `insufficient_data`，**禁止**填默认「正常」。

### 2. 与决策状态接线（最小）

- `capitulation_candidate` 且未 `reversal_confirmed` → 不得产出可执行 BUY；优先映射到既有 `trade_action=WAIT` / 非执行短路（复用 P0-5b `is_non_executable_status` / confirmation，**不要**平行再写一套 trader）。
- `reversal_confirmed`：允许小仓试探语义（可用 `position_pct` 上限常量，例如 ≤10%，写进测）；不得自动满仓 BUY。
- 「严格条件未满足」→ `WAIT`，**不是**永久 `NO_TRADE`（除非上游已是 INVALID/资金闸）。

接线点建议（白名单内选最小集）：`research_manager` 或 `decision_status` 读 state/上下文中的 VPA 标签；Trader 已有 WAIT 短路则复用。

### 3. Prompt

volume_price / research_manager **最多一句**：候选≠买入；`reversal_confirmed` 前不得当反转结论。禁止恢复「假摔洗盘」确定性话术。

## 允许修改

- `tradingagents/graph/data_collector.py`（`_compute_vpa_indicators` 及调用处）
- 必要时新建同目录纯函数模块**仅当**拆函数必要——优先原文件内拆；禁止 `_v2` 文件名
- `tradingagents/agents/utils/decision_status.py` 和/或 `research_manager.py` / `trader.py`（最小接线）
- `tradingagents/agents/analysts/volume_price_analyst.py`（若需读结构化标签）
- `tradingagents/prompts/zh.py` / `en.py`（最多一句）
- `tests/test_capitulation_reversal.py`（新建）
- 可选 `tests/fixtures/vpa/` 离线 OHLCV JSON

## 禁止

- 社交、新闻 event_coverage、回测/校准、资金流 selection、cluster、confirmation 算法大改
- 实网 pytest；偷看 T+1 bars
- 改 3/1、开加权、部署、碰脏文件
- `@pytest.mark.asyncio`（用 `asyncio.run`）

## 测试（TDD）

先在基线 SHA 写失败测，再实现。

`tests/test_capitulation_reversal.py` 至少：

1. 样本不足 → `insufficient_data`
2. 构造高量滞涨序列 → `high_volume_stagnation_candidate`（不是买入）
3. 构造极端放量下跌 → `capitulation_candidate`；接决策后不得 BUY
4. cutoff 后才出现的 follow-through **不得**把状态抬成 `reversal_confirmed`（前视钉）
5. cutoff 前已有确认 bars → 可 `reversal_confirmed`；小仓上限断言
6. 条件不足 → `WAIT`，不是永久 `NO_TRADE`（相对上游 VALID 路径）

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest tests/test_capitulation_reversal.py -q --tb=short
```

## 交付

1. 分支 tip 完整 40 位 SHA；父提交必须是 `aa0742a5…`
2. 定向 pytest 精确数字
3. `git diff --stat` 白名单内
4. 未部署；未宣称歌尔/蓝思案例已修
5. 卡置 `in_review`

## 不可违反

D-006/007/008/009/010；AGENTS.md 铁律。
