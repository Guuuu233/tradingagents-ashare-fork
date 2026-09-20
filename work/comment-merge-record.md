## ✅ 已合入主线（运维执行）

`origin/codex/dav-4-p2a-trunk`：`b95a9b88` → **`8854853cfc167fd9bb015528138ae36a1df6bf8f`**（快进，无 merge commit，无历史重写）

本次合入三刀：

| 卡 | 候选 SHA | 合入后 SHA | 审查卡 |
|---|---|---|---|
| DAV-944 | `f64d8548` | `f125179` | DAV-973 ✅ PASS |
| DAV-940 | `cafa39b8` | `9f2ab95` | DAV-975 ✅ PASS |
| DAV-930 | `3ea5513a` | `8854853` | DAV-981 ✅ PASS |

三者产品文件互不相交（`cn_akshare_provider.py` / `macro_market_utils.py`+`industry_linkage_provider.py` / `api/main.py`），串行 cherry-pick 全部干净。

### 放行依据：基线 vs 叠加树失败集合对照

由于 RT-FULL 在主干上会挂死（详见 DAV-979 的更正说明，原 deselect 方案无效），改用**分文件执行**取对照：223 个测试文件逐个独立进程执行，120s 看门狗，基线与叠加树使用**完全相同的切分与命令**，解释器为 `.venv310`（Python 3.10.20）。

| | 基线 `b95a9b88` | 叠加树（+3 候选） |
|---|---|---|
| OK | 214 | 214 |
| 失败文件 | 8 | **同样这 8 个** |
| 挂死文件 | `test_knowledge_rag.py` | **同一个** |
| 新增文件 | — | `test_hot_stocks_api_contract.py` 9 passed |

**零新增失败。** 主干既有失败集合（未因本次合入改变）：

```
test_cninfo_disclosure_metadata.py      1 failed, 22 passed
test_dav27_report_semantics.py          2 failed, 11 passed
test_debate_state_persistence.py        5 failed, 27 passed
test_game_theory_integration.py         1 failed, 17 passed
test_provider_date_guards.py            1 failed, 4 passed, 1 skipped
test_signal_processing.py               3 failed, 5 passed
test_social_data_api.py                 1 failed, 5 passed
test_two_stage_analyst_topology.py      4 failed, 9 passed
```

一处假阳性已排除：并行扫描时 `test_cohort_metadata_persistence.py` 在叠加侧报 HANG，串行无干扰复验为 base `12 passed in 25.92s` / stack `12 passed in 22.19s`，系 CPU 争抢导致的看门狗超时，非回归。

### 未触碰

未部署、未重启服务、未写生产库。生产库 `data/tradingagents.db` SHA256 合入前后一致：`94d2f6740db4f206...`。

### 对在途候选的影响

以下候选的直接父仍为 `b95a9b88`，**交付时合规**，但合入前需 rebase 到新 tip：

- DAV-938 候选 `66591467`（审查中，DAV-985）—— 纯前端，与本次三刀无文件交集
- DAV-941 返修中
- DAV-946 R3 返修中

两个在跑的开发运行已通过 steer 中途告知新 tip，无需重启运行。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
