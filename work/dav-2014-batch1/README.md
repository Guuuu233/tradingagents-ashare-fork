# DAV-2014 第一批取样口径（冻结件）

DAV-2179 产出，DAV-2177 空跑同参数复现一致。19:45 前由总控预签。

## batch_spec-batch1.json

sha256 = `0f85f3d0dd7ee50113fe5fed63248969597b568d729cb0888ff1cca2158d6da9`

- 基准日 T = 2026-10-16；批号 b = 1；n = 5
- `seed_base = 20261010` → 实际种子 `sampler_seed = 20261011`
- `expected_model = gemini-3.8-flash-n`（总控 10-17 令唯一修改项）
- sampler：主干 `cb82987b737be2cc3c40c939082b70c4135db7b8` 的
  `scripts/phase2/m2_sampler.py`，脚本 sha256
  `295af94d5578c6cbfda9c81308783a41b67859207202b479d225e739069d5cf9`
- 缓存：`TradingAgents-AShare-cache/phase2/v2-20261016/`；`index_member_all_all.pkl`
  按总控裁定使用主缓存原件 sha256 `d2e276a5b15977bf320675006109adf15bf7691019ce7b2d5009f16d0eb99358`
- 执行器回读：`load_batch_specs()` → (1, 20261016, 5, seed 20261011)；
  `experiment_executor.py plan --batch-spec` 端到端通过（只取样、不跑分析）

注：`expected_model` 写在 `batches[]` 内，执行器 `load_batch_specs()` 目前**只读取**
`batch_index/date/n/seed_base` 四键，`--expected-model` 仍由 CLI 传入；该键为冻结自述，
开跑时须以 `plan/run --expected-model gemini-3.8-flash-n` 与之一致。

## 取样名单（由上列口径实跑产出，与 DAV-2177 空跑逐字节一致）

- `batch-1-picks.json` sha256 `72b360e09fc6461758979b73fa8061dad577c20521e56cff796d51b111fbaa00`
- `batch-1-picks.csv`  sha256 `309a7e5bc03e4be9460b3839a163d1f455ef6714f74d7d56a17a6d70e9c4079c`
- `eligible_digest = 034f5fa64180c8b903679308380c60e59bb34c0303471bde8a57a8b78ec89035`
- pool 800 / eligible 793 / picked 5；剔除 ST 0、上市<1y 5、停牌 2、缺 SW L1 0
- pair_id：`b01-002335.SZ`、`b01-300054.SZ`、`b01-600100.SH`、`b01-600380.SH`、`b01-601336.SH`
