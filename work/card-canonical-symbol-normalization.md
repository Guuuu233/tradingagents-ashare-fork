**实施卡（V-03a 前置 blocker）。** David 2026-09-09 定：symbol 格式统一须独立成卡，非顺手修；**V-03a 依赖本卡完成后再执行**。前置 = 当前 trunk `fab99d9`。

## 背景（实测，reports 1408 条）

`symbol` 列格式不一，会污染 V-03 几乎所有下游指标（join / groupby / 去重 / 收益对齐 / 股票池过滤 / 预测计数 / agent credit / 校准统计）。危险在**不报错，只给"数学正常、事实错误"的结果**：
- 空值 `''` **32 条**；非法值 **2 种**（`AGENT`、`AUUSDO`）
- **3 处 collision**（同股两写法）：`000001`/`000001.SZ`、`600519`/`600519.SH`、`603259`/`603259.SH`

## 唯一关注点

在 V-03 任何 join/groupby/return alignment **之前**，把证券代码统一为 canonical 形式；空/无法映射 symbol **显式隔离并计数**，禁止静默 join、禁止静默 drop；加 collision/duplicate 检查。

## Canonical 规则

- 目标格式：6 位数字 + 交易所后缀 `.SZ/.SH`。前缀映射（确定性）：
  - `000/001/002/003/300/301` → `.SZ`
  - `600/601/603/605/688/689` → `.SH`
  - `8xx/4xx/920`（北交所）→ `.BJ`，按股票池规则**排除**（不静默丢，标记计数）
- 裸码补后缀（`000001`→`000001.SZ`）；已 canonical 的不变（幂等）。
- 空值、非 6 位数字开头（`AGENT`/`AUUSDO`）、前缀无法映射 → `unmappable`，隔离并计数，附原因。

## 允许改

- 新增 `tradingagents/.../symbol_canonical.py`（规范化 + 隔离 + collision 检查）
- 新增 `tests/test_symbol_canonical.py`
- 迁移脚本（**只在副本库**跑，不写生产库）：产出映射报告 + 隔离清单，供 David 审阅后再决定是否落库

## red_team_scenarios（D-012 §5b，须第二双眼睛复核覆盖面）

| # | 场景 | 预期 |
|---|---|---|
| RT-1 | collision 核心 `000001` 与 `000001.SZ` 并存 | 归一为单一 canonical，groupby 计数合并正确，不劈成两份 |
| RT-2 | 空值 `''`（32 条） | 标 `unmappable` 隔离+计数，**不静默 join，不静默 drop** |
| RT-3 | 非法值 `AGENT`/`AUUSDO` | 标 `unmappable`+原因，隔离计数 |
| RT-4 | 已 canonical `000001.SZ` | 幂等不变 |
| RT-5 | 前缀映射：`300xxx→.SZ`、`688xxx→.SH`、`600519→.SH` | 后缀推断正确 |
| RT-6 | 北交所 `8xxxxx` | 标 `.BJ` 并按池规则排除（标记计数，非静默丢） |
| RT-7 | collision/duplicate 检查 | 主动surface冲突计数，不静默 |
| RT-FULL | 候选 SHA 真全量 `pytest -q -p no:randomly` | 对照 trunk fab99d9 基线，零新增失败 |

## 硬约束

- **只读生产库**；规范化/迁移只在 `sqlite3 .backup()` 副本上验证，路径写进交付；**禁止写生产库**（是否落库由 David 定）。
- 禁止任何静默 join/drop——所有隔离必须可计数、可审计。
- 交付前 `git push` + `git ls-remote origin <分支>` 回读贴输出。
- 禁止 FF、部署、重启、写生产库、开加权。

## 交付

完整 40 位 SHA、第一父（须 fab99d9）、diff --stat、diff --check、7 个 RT + RT-FULL 实测输出（含 32 空值/2 非法/3 collision 的隔离与合并计数）、副本库路径、工作树状态。状态置 in_review。
