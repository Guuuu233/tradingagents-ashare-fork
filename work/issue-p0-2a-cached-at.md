# P0-2a：禁止把 cached_at 当成数据 as_of

## 目标

D-009 P0-2 的最小可落地切片：堵住 `tradingagents/graph/data_collector.py` 里 `_extract_source_as_of` 把 **缓存时间** 当成 **数据有效日** 的洞。不是全量 EvidenceRecord 迁移，不是社交，不是部署。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `4fa76815d5aa7d1cfab9942c8f9a9606034c279d`
- 从该 SHA 开隔离分支，例如 `agent/<you>/p0-2a-cached-at`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要快进主干、不要部署、不要 `git add -A`

## 已复现的产品问题

`_extract_source_as_of` 在显式 `as_of` 缺失时，用 `cached_at`（int timestamp 或字符串里的日期）当作 `actual_as_of`。`cached_at` 是缓存/请求时间，不是行情或财报有效日。这违反审计稿 P0-2：「`cached_at`、ingest time、当前 API 返回时间不能自动成为有效 as-of」，也违反 AGENTS.md 3.5（解析失败禁止填默认时间）。同函数对 `fromtimestamp` 使用 `except Exception: pass`。

行业联动抽取函数注释已写「Never use … cached_at as the real data date」，股票/通用路径没有遵守。

## 行为契约

对 dict payload：

| 输入 | 必须输出 |
|---|---|
| 有可解析且 `<= requested_as_of` 的 `as_of` / `actual_as_of` / `quote_as_of` / `data_as_of` | 用该日期 |
| 只有 `cached_at` / `retrieved_at` / ingest / now，没有显式数据日 | 返回 `None`，不得用缓存日冒充 |
| 显式日期无法解析 | 返回 `None`，记录日志，不得 `pass` 后填今天或 cached_at |
| 显式日期 `> requested_as_of` | 返回 `None`（future 拒绝） |

`_build_source_provenance` 在 as_of 为 None 时走现有 unavailable / available_unverified_as_of 路径，不要新造平行函数。不要新增无人调用的 `EvidenceRecord` 文件。

## 允许修改

- `tradingagents/graph/data_collector.py`（原函数，禁止 `_v2`）
- `tests/test_data_collector.py`

若必须改 `tests/test_industry_linkage_dataflows.py` 里依赖 cached_at 冒充 as_of 的夹具，只改夹具使它提供显式 `as_of`，并在交付里写明。

## 禁止修改

- 社交 `tradingagents/dataflows/social/`、DAV-460
- P0-3/P0-4/P0-5、前端、schema 大迁移
- `AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`
- 主干快进、部署

## 测试（TDD）

先加会在 `4fa7681` 上失败的测试，再改产品代码。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest tests/test_data_collector.py -q --tb=short
```

必须断言：

1. payload 仅有 `cached_at`（epoch 或 ISO）时 `_extract_source_as_of` 为 `None`
2. 同时有 `as_of=2026-08-20` 与更新的 `cached_at` 时，返回 `2026-08-20`
3. `as_of=2026-08-22` 且 `requested_as_of=2026-08-21` 时返回 `None`
4. 既有 `test_data_collector.py` 绿

## 交付

- 分支名 + 完整 40 位 SHA，已 push
- `git diff --stat` 相对 `4fa7681`
- 精确 pytest 输出
- 不要写「彻底修复」；不要自行合主干
- 完工后只 @项目调度助手 一次，并写明 SHA。合入等待 Cursor「准予合入」
