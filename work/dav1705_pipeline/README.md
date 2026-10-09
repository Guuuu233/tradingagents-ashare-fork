# DAV-1705 影子组合端到端试跑（驱动脚本说明）

> 管线试跑，非成绩 — 本目录只放可复现的驱动脚本与说明，**不放任何数据产物**。
> 产物一律写到 gitignored 的本机目录（见下），不入库。

## 文件清单（白名单：`work/dav1705_pipeline/` 下 4 文件 + `tests/` 下 1 测试）

- `build_ledger_chunked.py` — 分块流式账本封存（复用生产
  `scripts/phase2/daily_snapshot_ledger.py` 封存语义：`build_record`/hash 链/
  HEAD 锚/state；默认 `snapshot_date` 直接复用生产侧同一函数
  `cn_today_str()`，Asia/Shanghai 口径，F-01）。
- `run_shadow_trial.py` — 影子组合串联驱动（M2-A 成交规则 + M2-B 三套策略，
  另含内存合成对照 `synthetic_smoke/synthetic_edge`）。
- `finalize_trial.py` — 产物「非成绩」标注固化（picks JSON 顶层字段 + m2 报告
  首行抬头；固定字符串注入，不破坏逐字节复现）。
- `test_build_ledger_chunked.py` → 已移至 `tests/test_dav1705_pipeline_drivers.py`
  （pytest `testpaths=["tests"]` 只收 `tests/`，放 `work/` 会被静默跳过；
  内容不变，仅 `sys.path` 指向 `work/dav1705_pipeline` 取被测驱动）。
- `README.md` — 本文件。

## 产物落点与 gitignore 依据

- 默认产物目录：`work/analysis_runs/dav1705/`（账本 `ledger/`、影子结果
  `shadow_trial-*.json`、报告、日历文本均落此处）。
- 忽略依据：`.gitignore` 中 `work/analysis_runs/`（“机器生成的分析运行产物”），
  该目录整体不入库。复现后 `git status` 应保持干净即为证明。

## 交易日历（运行时三级来源，不再冻结文本入库）

`run_shadow_trial.resolve_calendar()`：显式 `--calendar` 文件 >
Tushare `trade_cal` 在线（复用生产侧同一请求函数 `_query_tushare_api`，
无 token 自动降级）> 本机缓存 `<phase2>/api_cache/trade_cal_*.pkl`
取覆盖最广的一份。三级全缺则 fail-close（绝不退化成自然日口径）。
注意：本机缓存行情与日历止于 2025-12-31；2026 窗口的报告需 Tushare
在线日历（配 `TUSHARE_TOKEN`），无 token 环境用缓存覆盖内的 `--as-of`
（如 2025-12-31）验证同套代码路径。

## 复现命令（解释器须为项目锁定 `.venv310`，且 `env -u PYTHONPATH`）

```bash
V="env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python"
OUT=work/analysis_runs/dav1705
# 0) 账本封存（分块流式；ledger/ 为单次快照：sealed_at 取运行时钟，
#    两次封存 chain_hash 必然不同，属 by design；下游为账本纯函数）
$V work/dav1705_pipeline/build_ledger_chunked.py \
    --db /private/tmp/ta-snapshot/current.db --ledger-dir $OUT/ledger --force
$V scripts/phase2/daily_snapshot_ledger.py verify --ledger-dir $OUT/ledger
# 1) 名单（m2_sampler 为主干生产脚本，不在本目录）
$V scripts/phase2/m2_sampler.py --date 20251231 --n 20 --seed 42 \
    --out $OUT/trial-nonresult_picks-20251231-a.json
# 2) 影子组合（日历自动解析；--write-calendar 落盘供 m2 报告复用）
$V work/dav1705_pipeline/run_shadow_trial.py a --ledger $OUT/ledger/forward_ledger.jsonl \
    --out-dir $OUT --write-calendar $OUT/calendar.txt
# 3) 日报/月报（主干生产脚本）+ 标注固化
$V scripts/phase2/m2_periodic_report.py run --period both --ledger $OUT/ledger/forward_ledger.jsonl \
    --calendar $OUT/calendar.txt --as-of 2026-09-30 \
    --daily-out $OUT/trial-nonresult_m2_daily_2026-09-30.md \
    --monthly-out $OUT/trial-nonresult_m2_monthly_2026-09.md
$V work/dav1705_pipeline/finalize_trial.py $OUT/trial-nonresult_picks-20251231-a.json \
    $OUT/trial-nonresult_m2_daily_2026-09-30.md $OUT/trial-nonresult_m2_monthly_2026-09.md
# 4) 逐字节复现：上式各跑两次（a/b），cmp 即证
```

## 已知数据侧阻断（非本卡可修，I-01/I-02）

- I-01：phase2 行情缓存止于 2025-12-31，2026-09+ 信号无 bar 可成交
  （成交顺延耗尽归因 `suspension`/`data_missing`，属 M2-A R3 口径）。
- I-02：账本 `input_pit_status` 100% UNVERIFIED，正式栏（F0+VERIFIED）恒为 0。
- 内存根因（M-05）：快照 1600 个 completed 报告 result_data 合计约 5.46GB，
  主干脚本一次 `fetchall()` 常驻即越 6GiB；分块驱动 peak footprint 约 0.37GiB。
