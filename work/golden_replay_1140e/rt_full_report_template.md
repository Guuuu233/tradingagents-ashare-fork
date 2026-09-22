# RT-FULL 门禁报告模板（DAV-1179 起强制）

门禁结论只认 **同环境 parent vs candidate 的新增失败数**。绝对 passed/failed
受代理、.env、真实网络 guard、生产计数漂移影响，不可移植，仅作环境旁证。

## 必填字段

| 字段 | 来源 |
|---|---|
| `new_failures_relative_to_parent` | `rt_full_compare.py` 主门禁字段，0 才 PASS |
| `new_failure_nodeids` | 新增失败用例清单（parent 过 → candidate 挂） |
| 解释器 | `.venv310/bin/python -V` 实贴输出（须 `Python 3.10.20`，`env -u PYTHONPATH`） |
| parent / candidate HEAD SHA | `git rev-parse HEAD` 实读，禁止硬编码 |
| worktree dirty | `git status --porcelain` 是否非空 |
| 代理处理 | `unset http_proxy https_proxy all_proxy`（避免 Clash 7897 挂死） |
| 隔离数据库 | `DATABASE_URL=sqlite:///<out>/<label>_rtfull.db`，禁止写 `data/tradingagents.db` |
| 既有 deselect | `tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields`（主干死锁 DAV-979，非新增失败） |
| pytest 参数 | `-q -p no:randomly --junitxml` |

## 执行流程

```bash
GATE=work/golden_replay_1140e
# parent：建议 git worktree add <dir> <baseline_sha> 后在该树上跑
bash $GATE/rt_full_run.sh parent    <parent_checkout>   <out_dir>
bash $GATE/rt_full_run.sh candidate <candidate_checkout> <out_dir>
$PY work/golden_replay_1140e/rt_full_compare.py <out_dir>
```

产物：`<out_dir>/rt_full_gate_report.json`。`verdict=PASS` 当且仅当
`new_failures_relative_to_parent == 0`。
