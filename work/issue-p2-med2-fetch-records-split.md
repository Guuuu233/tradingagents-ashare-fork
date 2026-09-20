# P2-MED2：社交残留 M1（拆分 fetch_records）+ 可选 M5

## 目标

消化回顾审计剩余 Medium：

- **M1**（本卡主项）：`SocialArchiveProvider.fetch_records` 过长（~300 行）→ **原路径内拆分**为具名 helper，行为不变
- **M5**（可选、有证据才改）：社交路径上过宽 `except Exception` / 错误码误标；能定点则改，禁止无证据的大扫除

**禁止** Gate 4 / 删 `legacy_proxy` / 部署 / 改默认 `TA_SOCIAL_MODE`。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `0d7a67e48e21a465b8672c975ec8f268f9ef0aeb`
- **新建**隔离分支，例如 `agent/dev2/p2-med2-fetch-records-split`
- 禁止 FF / 部署 / `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/audit-retro-p2-t5-t11.md` M1 / M5
- AGENTS.md：修改原路径、删死代码、一次 commit 一个关注点
- D-008 / D-010

## 文件白名单

1. `tradingagents/dataflows/social/provider.py`（M1 必做；M5 仅当同文件有明确误标）
2. 相关测试：`tests/test_social_archive_provider.py`、`tests/test_social_as_of_guard.py`、`tests/test_social_e2e_acceptance.py`（行为锁，必要时补拆分后的定向测）
3. M5 若触及：`collector.py` / `mediacrawler_importer.py` — **仅**有复现测的点；另 commit

## M1 要求

1. **行为零漂移**：同一输入 → 同一 `SocialFetchResult`（status / reason_codes / records / ledger 语义）
2. 在 `SocialArchiveProvider` 内把 `fetch_records` 拆成清晰步骤，例如（名称可调）：
   - 解析 as_of / cutoff
   - 打开只读连接与 pragma
   - 查询候选行
   - 资格过滤与 snapshot 选择
   - 组装 records / provenance
3. **禁止**新建并行 provider、禁止 `_v2` 后缀绕过、禁止复制粘贴留旧实现
4. 函数尽量 <60 行；嵌套 ≤3
5. 单独 commit：`refactor(social): split archive fetch_records into helpers (M1)`

## M5 要求（可选）

- 先用测例钉住「误标 / 静默吞错」再改
- 捕获要具体并打日志；禁止 `except Exception: pass`（解析多格式的循环除外，但须有最终失败路径）
- 单独 commit：`fix(social): narrow exception handling for … (M5)`
- 若找不到可证伪误标：在交付评论写「M5 本卡跳过 + 理由」，不要空改

## 测试

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest -q \
  tests/test_social_archive_provider.py \
  tests/test_social_as_of_guard.py \
  tests/test_social_data_collector.py \
  tests/test_social_e2e_acceptance.py \
  tests/test_social_aggregator.py
```

M1 前后关键路径测必须仍绿；若拆分引入缺口，补测锁行为而非锁行数。

## 交付

- **先 push**；`git ls-remote` 可见 tip
- 评论：每个 commit 完整 40 位 SHA + MED ID + pytest 精确数字
- tip 祖先含 `0d7a67e…` → `in_review`
- 不自行 FF；不 @调度助手合入；不删 legacy

## 不做

- Gate 4 / T15b
- MED 已合入的 M2/M3/M4/M6/M7 再改一遍
