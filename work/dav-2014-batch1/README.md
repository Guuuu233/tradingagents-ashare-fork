# DAV-2014 第一批取样口径（冻结件）

DAV-2179 产出，DAV-2177 空跑同参数复现一致。19:45 前由总控预签。

## batch_spec-batch1.json

sha256 = `0d555fcfca6adfd57036aeaa9a9aa3f4dae0622373d61340fb4355b4295c0b02`

- 基准日 T = 2026-10-16；批号 b = 1；n = 5
- `seed_base = 20261010` → 实际种子 `sampler_seed = 20261011`
- `expected_model = gemini-3.8-flash-n`（总控 10-17 令唯一修改项）
- sampler：主干 `cb82987b737be2cc3c40c939082b70c4135db7b8` 的
  `scripts/phase2/m2_sampler.py`，脚本 sha256
  `295af94d5578c6cbfda9c81308783a41b67859207202b479d225e739069d5cf9`
- 执行器回读：`load_batch_specs()` → (1, 20261016, 5, seed 20261011)；
  `experiment_executor.py plan --batch-spec` 端到端通过（只取样、不跑分析）

## 缓存目录（DAV-2187 打回 🟡-1 订正）

- **实际取数目录（live）**：`TradingAgents-AShare-cache/phase2/v2-20261016/overlay_cache/`
  —— 这才是 `PHASE2_CACHE_DIR` / `--cache-dir` 必须指向的目录。
- **不能**指向 `…/v2-20261016/` 根目录：该目录下没有 `index_weight_000300.SH.pkl` 等取数文件，
  实测直接 `FileNotFoundError: …/v2-20261016/index_weight_000300.SH.pkl`。
  根目录只是本批次的产出目录（`manifest_v2_dav2165.json`、`out_v2/`）加一个 `overlay_cache/`。
- `overlay_cache/` 为 live 目录，其中 3 个文件是指向主缓存原件的**软链**（软链本身随环境而变，
  内容摘要与主缓存原件一致）：
  `index_classify.pkl` → `phase2/index_classify.pkl`、
  `stock_basic.pkl` → `phase2/stock_basic.pkl`、
  `index_member_all_all.pkl` → `phase2/index_member_all_all.pkl`。
  其中 `index_member_all_all.pkl` 按总控裁定使用主缓存原件，sha256
  `d2e276a5b15977bf320675006109adf15bf7691019ce7b2d5009f16d0eb99358`。
- 本批实际读到的输入件 sha256（同时记入 spec 的 `sampler.input_sha256`）：

  | 输入件 | sha256 |
  | --- | --- |
  | `index_weight_000300.SH.pkl` | `c73a05fa32ca1799d02c2972bfdf91c9b5603b50bcb11f2dbedaa49fbcaf957e` |
  | `index_weight_000905.SH.pkl` | `53adaf617d721d0a98969e5c0d63ca35f96d29527932699870f0b1325037b15b` |
  | `namechange.pkl` | `57ff7a79bed3a52eeb01727dd984c50db5e361831a11ca6cc5680d2319e17dd8` |
  | `stock_basic.pkl`（软链目标） | `b67b06e834a51ef79316a314916c8f085c4844a4b60c6a5afa5bc206941fc6dc` |
  | `index_member_all_all.pkl`（软链目标） | `d2e276a5b15977bf320675006109adf15bf7691019ce7b2d5009f16d0eb99358` |
  | `daily_by_day/2026-10-16.pkl` | `0e61f70863d1455a671c2dd7cbeda420bd9ae8e2c8426cabc71c42e6bb954f8f` |

  `sw_daily.pkl`、`daily_basic_by_day/` 不被 `m2_sampler.py` 读取，不属冻结输入。

### 可复制的实际开跑命令（取样复现）

固定解释器 `~/Documents/TradingAgents-AShare/.venv310/bin/python`（3.10.20），必须
`env -u PYTHONPATH`（宿主 PYTHONPATH 会污染 venv）：

```bash
env -u PYTHONPATH \
  PHASE2_CACHE_DIR="$HOME/Documents/TradingAgents-AShare-cache/phase2/v2-20261016/overlay_cache" \
  ~/Documents/TradingAgents-AShare/.venv310/bin/python \
  scripts/phase2/m2_sampler.py \
  --date 20261016 --n 5 --seed 20261011 \
  --out /tmp/rep-picks.json --csv /tmp/rep-picks.csv

cmp /tmp/rep-picks.json work/dav-2014-batch1/batch-1-picks.json
cmp /tmp/rep-picks.csv  work/dav-2014-batch1/batch-1-picks.csv
```

等价的 `--cache-dir` 写法（覆盖 `PHASE2_CACHE_DIR`）：
`… python scripts/phase2/m2_sampler.py --date 20261016 --n 5 --seed 20261011 \
--cache-dir "$HOME/Documents/TradingAgents-AShare-cache/phase2/v2-20261016/overlay_cache" --out …`

实测输出：`pool=800 eligible=793 picked=5/5 excl(st=0 young=5 susp=2 noL1=0) digest=034f5fa64180`，
与冻结的 `batch-1-picks.json` / `.csv` 逐字节一致（`cmp` 无差异）。

## expected_model 对账口径（DAV-2187 打回 🟡-2 订正）

`expected_model` 写在 `batches[]` 内，但执行器 `load_batch_specs()` 目前**只读取**
`batch_index` / `date` / `n` / `seed_base` 四键；`--expected-model` 仍由 CLI 传入，
并由 `fetch_batch_pairs()` 直接赋给每个 `PairSpec.expected_model`。

**执行器当前不校验文件内 `expected_model` 与 CLI `--expected-model` 是否一致。**
文件里的该键是冻结自述，只有人工对账兜底；代码层守卫（`load_batch_specs` 增加冲突校验 +
run_report 落 `batch_spec_path`/`sha`/`expected_model`）已另开小卡，不在本件范围。

### 开跑前对账

1. 以 `plan` 输出为准，确认 CLI 与文件一致：

```bash
env -u PYTHONPATH \
  PHASE2_CACHE_DIR="$HOME/Documents/TradingAgents-AShare-cache/phase2/v2-20261016/overlay_cache" \
  DATABASE_URL="sqlite:////tmp/dav2196-empty.db" \
  ~/Documents/TradingAgents-AShare/.venv310/bin/python \
  scripts/phase2/experiment_executor.py plan \
  --batch-spec work/dav-2014-batch1/batch_spec-batch1.json \
  --expected-model gemini-3.8-flash-n \
  --out /tmp/plan-batch1.json
```

2. 校验 plan 输出的每一对：
   `jq -r '.batches[0].pairs[] | "\(.pair_id)\t\(.expected_model)"' /tmp/plan-batch1.json`
   必须 5 行全部为 `gemini-3.8-flash-n`，且与 spec 文件里的 `expected_model` 一致；
   任何一行不一致即停跑，回调度确认。

### 收批后对账

用 `--state-dir` 下的 `ledger.jsonl`：每次尝试一行 JSONL（`append_attempt()`），
逐行读 `served_models`（由 provider 响应体 `result_data.served_models` 采集，见
`flatten_served_models()`）与 plan 里的 `expected_model` 比对：

```bash
jq -r 'select(.served_models | length > 0) | "\(.pair_id)\t\(.served_models|join(","))"' \
  <state-dir>/ledger.jsonl
```

任何 `served_models` 出现非 `gemini-3.8-flash-n` 的值即按「非冻结模型」处理；
`state.json` 的 `pairs[].served_models` 是同一份数据的最新状态副本，可交叉核对。
另见 `run_report.json` 的 `batches[].outcomes` 与 `stop_reason`。

## 取样名单（由上列口径实跑产出）

- `batch-1-picks.json` sha256 `72b360e09fc6461758979b73fa8061dad577c20521e56cff796d51b111fbaa00`
- `batch-1-picks.csv`  sha256 `309a7e5bc03e4be9460b3839a163d1f455ef6714f74d7d56a17a6d70e9c4079c`
- `eligible_digest = 034f5fa64180c8b903679308380c60e59bb34c0303471bde8a57a8b78ec89035`
- pool 800 / eligible 793 / picked 5；剔除 ST 0、上市<1y 5、停牌 2、缺 SW L1 0
- pair_id：`b01-002335.SZ`、`b01-300054.SZ`、`b01-600100.SH`、`b01-600380.SH`、`b01-601336.SH`

一致性口径（🟢-1 建议的收口）：本件以上述 sha256 为准；DAV-2177 空跑侧的 picks 摘要未在本件
留存副本，claim 只保证「本件在 overlay_cache 上复现逐字节一致」，需要跨卡逐字节比对时以
DAV-2177 侧自身记录的 sha256 为准，不要以本件数值代替。