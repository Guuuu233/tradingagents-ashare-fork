# DAV-1950 — 第一阶段验收报告生成器说明

总控 10-12 令交付物：`scripts/dav1950_exit_acceptance_report.py`。

## 做什么 / 不做什么

- **只做汇总与格式化**：全部读数来自 `scripts/dav1507_exit_progress.py`
  的同一套扫描与指标函数——生成器在**进程内**以 `--json` 调用其
  `main()`，取回 summary 后按 A1–A4 / B1–B3 逐项渲染「数据来源 /
  起算日 / 样本量 / 门槛 / 是否达标」。不改、不复制任何既有口径
  （clean 管线、A3 冻结谓词、B1/B2/B3 判定全在上游）。
- A1 固定标注「**需总控人工审计**」并附可执行档逐档清单（脚本不判
  假 clean）。
- 只读 `--db`（默认 `/private/tmp/ta-snapshot/current.db` 共享快照，
  `mode=ro`）；不写生产库、不触网、零模型/provider。

## 用法

```bash
env -u PYTHONPATH .venv310/bin/python \
  scripts/dav1950_exit_acceptance_report.py \
  --db /private/tmp/ta-snapshot/current.db --as-of 2026-10-12 \
  [--start 2026-10-08] [--end ...] [--ids ids.json] [--all-history] \
  [--out report.md | --json | --self-test]
```

- `--as-of` 未给时按共享快照目录约定取最新
  `tradingagents-YYYY-MM-DD.db.zst` 名中的日期，再退回 db mtime——
  同一输入下解析结果不变。
- 输出头部含 `db_sha256` 与字节数，便于两次运行确认输入同一文件。

## 确定性

报告是 `(db 内容, --start, --end, --as-of, --calendar-json, --ids,
--all-history)` 的纯函数：正文不嵌生成时刻、不读墙钟；同输入两次
运行 stdout 逐字节相同（实测 sha256 相同，见交付评论）。

## 验收口径（自测实测，2026-10-12 快照）

- 解释器：`.venv310` Python 3.10.20，`env -u PYTHONPATH`。
- `--db /private/tmp/ta-snapshot/current.db --as-of 2026-10-12`
  两次运行 stdout sha256 相同；`/usr/bin/time -l` peak memory
  footprint ≈ 0.87 GiB（≤4 GiB 红线内，余量 ~4.6x；上游扫描
  tracemalloc 峰值 418.5 MB 已含在该包络内）。
- `pytest tests/test_dav1507_exit_progress.py
  tests/test_dav1950_exit_acceptance_report.py -q -p no:randomly`
  → 30 passed（含既有 25 项无回归）。
