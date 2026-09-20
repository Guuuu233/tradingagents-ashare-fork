# P2-T12 续做：rebase 到 H1 tip 并重新交付审核

## 背景

1. 回顾审计 HIGH **H1**（lookback 空窗误标 `refused`）已修并 FF 到主干：
   - tip：`0d21d1950350d42f65a7e3cb42040c05552eb3e0`
   - 父：`68ae241bdf9c148654f551fb67b7e5f2ec56dba4`
2. 现有候选分支 `agent/dev2/p2-t12-social-report-gates` tip：
   - `d051ced4afaf6d9a31b28c43b39790f80be0ea23`
   - **基于 68ae241，不含 H1**，不得合入。
3. `d051ced` 已含此前质量闸返修（`evaluate_social_depth` legacy/disabled/shadow 不误杀；`apply_report_quality_gate` 接入 `sentiment_report`）。续做**不要重写功能**，只 rebase + 回归。

旧卡 DAV-507 / DAV-509 已过期基线，本卡取代它们。

## 任务（唯一关注点）

1. `git fetch origin`
2. 以 `origin/codex/dav-4-p2a-trunk`（= `0d21d19…`）为基，把 `d051ced` 的 T12 改动 **rebase** 到新 tip（或 cherry-pick 单 commit）。
3. 冲突时优先保留：
   - H1：`provider.py` 空窗 → `empty` / `social_empty`（无 failure ledger）
   - T12：quality gate / report / status API / direction_allowed 行为
4. 本地 pytest（精确数字，禁止 proxy）：
   ```bash
   env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
     .venv310/bin/python -m pytest \
     tests/test_social_downstream_gates.py \
     tests/test_social_data_api.py \
     tests/test_report_data_gaps.py \
     tests/test_social_as_of_guard.py \
     tests/test_social_data_collector.py \
     -q
   ```
   另跑相关 social 套件若时间允许。
5. push 同分支 `agent/dev2/p2-t12-social-report-gates`（允许 force-with-lease，因仅 rebase）。
6. 评论交付：完整 **40 位 SHA**、父提交、diffstat、pytest 数字 → `in_review`。

## 禁止

- 不自行 FF / 不部署 / 不删 `legacy_proxy` / 不改辩论轮次
- 不扩 MED M1–M7
- 不 @调度助手 合入
- 独立审核 PASS ≠ 准予合入；等 Cursor 同 SHA 隔离复测 +「准予合入」后由运维线性 FF

## 验收

- tip 祖先含 `0d21d19…`
- 功能范围仍为 T12（报告 ledger / direction_allowed / quality gate / `GET /v1/social-data/status`）
- 返修契约测试仍在：disabled/shadow 无「不可判断」可 pass；active 不足无标记 fail；`apply_report_quality_gate` 评 `sentiment_report`
