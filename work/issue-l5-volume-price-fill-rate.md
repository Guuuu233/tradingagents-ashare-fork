# L5：量价输入完整度与填充率提升

## 基线

- 远端主干：`codex/dav-4-p2a-trunk`
- 开工基线必须重新 `git ls-remote`，当前核验值为完整 SHA：`41b77dc7a0871a849744b8013db3290700ccc883`
- 当前主工作树脏且处于旧 agent 分支，禁止把它当开发树；必须使用从该 SHA 建立的隔离 worktree。

## 目标

按 `work/2026-09-12-dispatch-sequence.md` 的 L5，找出并修复一个可复现的量价输入完整度缺口，使有效 OHLCV / 量价分析输入的填充率在固定、可重复的 fixture 或 mock provider 对照中实际提升。

这里的“填充率提升”必须是输入数据覆盖改善，不得用默认值、空表、LLM 文本或改分母制造提升。缺失、provider failure、empty、unavailable 必须保留各自语义；`0` / `Decimal(0)` 是合法值，不得被当成缺失。

## 白名单

- `tradingagents/dataflows/providers/`
- `tradingagents/graph/data_collector.py`
- 与上述改动直接对应的测试文件

不得改 `api/main.py`、`prompts/`、研究经理、辩论轮次、加权开关、数据库 schema、社交 Gate、部署脚本或无关 provider。

## 验收钉子

1. 先给出候选缺口的基线测量：固定 symbol / trade date / provider 返回，明确分子、分母、字段覆盖率和失败分类。
2. 修复后用同一输入重跑，覆盖率确实提升；不得只报“非空”。
3. 缺数据仍显式进入既有 failure / gap / provenance 语义；不得静默回退或伪造价格、成交量、技术指标。
4. 严格遵守请求日期截断、列名解析、重复日期冲突拒收以及现有 `price_basis` 语义；不得用 `.iloc` 行位置切片掩盖日历或缺行问题。
5. 至少覆盖以下红队场景：
   - provider failure 与空结果不得变成 confirmed empty 或有效数据；
   - 缺 `volume` / 缺必要 OHLCV 列必须显式不可用；
   - 合法 `volume=0` 或 `Decimal(0)` 不得丢失；
   - 日期超出 cutoff、未来行和重复日期冲突不得进入量价输入；
   - provider fallback 不能跨越显式 `price_basis` 约束；
   - 所有派生指标在原始输入不足时必须保持 insufficient / missing 语义。
6. 交付报告须给出完整 40 位候选 SHA、第一父、改动文件、`git diff --check`、精确 pytest 数字、基线/修复后填充率和未解决风险，并把卡置为 `in_review`。

## 禁止事项

- 不访问真实外部行情接口，不采集真实数据，不写生产库。
- 不部署、重启服务、FF 主干或修改当前脏工作树。
- 不自建或指派“独立代码审核员”。候选交付后，代码审查统一派给“代码审核员”（必要时“代码审核员2”）；审查必须针对同一完整 SHA，含红队与 RT-FULL。
