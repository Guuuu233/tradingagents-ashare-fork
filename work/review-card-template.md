# 审查卡模板（只读代码复审）

> 用法：复制本文件，替换 `<>` 占位符后作为 `multica issue create --description-file` 的内容。
> 审查一律派 **代码审核员**（必要时 `代码审核员2`）；**不得**派「独立代码审核员」（D-014）。

---

# <DAV-xxx> 候选 `<短SHA>` 只读代码复审

请由 **代码审核员** 对下面这个完整候选 SHA 做同一版本的只读复审。审查不修改、不合入、不部署。

## 版本边界

- 实施卡：<DAV-xxx>
- 候选完整 SHA：`<40位>`
- 直接父 SHA：`<40位，须等于交付时的远端主线 tip>`
- 远端分支：`origin/<分支>`
- 目标主线：`origin/codex/dav-4-p2a-trunk`

开工前须 `git ls-remote origin <分支>` + `git cat-file -t <sha>` 回读确认存在；回读失败即视为未交付，不进审查（D-012 §6）。

## 严格白名单

1. `<文件1>`
2. `<文件2>`

越出白名单、父提交不一致、候选 SHA 不一致或检出工作树非 clean，直接打回。

## ⚠️ 强制运行环境（不满足即结论无效）

**必须使用项目锁定解释器，绝对路径：**

```
/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
```

- 必须 `env -u PYTHONPATH`（宿主 `PYTHONPATH` 会污染 venv，导致 numpy/pydantic「二进制不兼容」假象）。
- 报告中**必须贴出** `<解释器> -V` 的实际输出，须为 `Python 3.10.20`。
- **不得**使用系统 `python3` / `python3.14` / 任务运行时自带 `.venv`。原因：系统 3.14 装的是另一套依赖（**pandas 3.0.5 vs 项目 2.3.0**，跨大版本；字符串列 dtype `object→str`、copy-on-write `False→True`），且生产服务运行在 `.venv310`。异构环境的绿灯既可能假绿（那边过、生产挂）也可能假红，且系统 site-packages 不受 `uv.lock` 锁定、结论不可复现。
- 若因环境限制只能在其他解释器上运行：结论只能记为「旁证」，必须在报告中显式标注解释器版本与依赖差异，**并明确声明未取得发布门禁证据**；不得写成 PASS。

若需要跑外部依赖测试：`unset http_proxy/https_proxy/all_proxy`，或设 `http_proxy=http://127.0.0.1:9` 使外连快速失败；否则请求会走 Clash `7897` 挂死。

## 复审重点

- <契约要点 1>
- <契约要点 2>

## 红队场景（D-012，须逐条实跑并贴实际输出）

- RT-1 正常路径：<预期>
- RT-2 边界输入：<预期>
- RT-3 空值 / 旧格式 / 回退路径：<预期>
- RT-4 异常或数据截断：<预期>
- RT-5 状态冲突：<预期>
- RT-6 与相邻模块交互：<预期>

每条须含：固定输入或 fixture、预期输出、实际输出、执行命令、候选完整 SHA、解释器、结论。静态阅读或测试全绿不能替代实跑。

## RT-FULL 全量回归（改产品代码时不可省，D-012 §4b）

```
env -u PYTHONPATH PYTHONPATH=<候选worktree> \
  DATABASE_URL="sqlite:////private/tmp/<隔离库>.db" \
  /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly \
  --deselect "tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields"
```

- **必须 deselect 上面这条**：它是主干既有死锁（约 38% 处 `%CPU=0` 挂死，不会自行结束），非候选引入，详见 **DAV-979**。报告中须显式写明该 deselect 及原因。
- `DATABASE_URL` 必须指向隔离临时库；**禁止**让测试写到 `data/tradingagents.db`。
- 须给出 collected / passed / failed / skipped / deselected 精确数字，并与目标主线基线做**失败集合逐项对照**；有任何**新增**失败即不得 PASS。
- 纯前端候选可不跑 Python RT-FULL，但须说明理由并确认无后端文件改动，同时给出 `npm test` 与 `npm run build` 的精确结果。

## 禁止项

不修改代码、不合入、不部署、不重启服务、不写生产库、不调用真实模型或真实供应商、不读取 Cookie、不改 `credit_weighting_enabled`、不改个人配置（`role_bindings` / `providers` / API Key）。

## 交付格式

给出路径/行号证据、精确测试数字、解释器版本输出，以及明确评级：**PASS / 有条件通过 / 打回 / 审核无效**。任一红队场景未执行或无法复现 → 不得为 PASS。

最后一行须为可触发调度的 mention：
[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

---

## 【强制】结论强度必须与证据范围匹配（2026-09-16 新增，因 DAV-1000 假阴性）

复审结论二选一，不得混用：

**A. 无条件 PASS** —— 必须同时满足：
- 附**完整 RT-FULL**：不加任何 `--deselect` / `-k` / 路径限定 / `--ignore`
- 附与基线的**失败集合逐项对照**（建议 `comm` 比对排序后的 FAILED 清单），证明**零新增**
- 两侧同解释器、同口径、同 `-p no:randomly`

**B. 限定范围结论** —— 若未做全量回归，必须原文声明：
> 本复审仅覆盖代码语义与定向测试，**未做全量回归**，放行须另附全量对照证据。

此时**不得**写成无条件 PASS。

### 为什么

DAV-1000 审 DAV-995 护栏候选时，跑了约 147 条定向测试（护栏专项 19 + 两文件合跑 67 + provider 14 + historical_cases 47）后给出无条件 PASS；运维独立完整 RT-FULL 实测 **2 项新增失败**（`test_v03_return_measure.py::test_p0_real_provider_verifiable_metadata`、`test_job_lifecycle.py::test_soft_timeout_emits_overtime_then_allows_completion`）。

该候选修改的是**全局 socket 行为**，副作用必然出现在**其他文件**里——定向测试在原理上就发现不了。凡涉及 conftest / 全局 monkeypatch / 资源生命周期 / 供应商选路的改动，**一律适用 A**。

全量基线（主线 `28d1adc6`）：`20 failed, 4777 passed, 1 skipped, 3 deselected in 1700.10s (28:20)`。护栏落地后预计降至 10 分钟内，全量成本已不构成豁免理由。
