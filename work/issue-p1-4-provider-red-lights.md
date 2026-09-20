# P1-4：provider 红灯最小修复（THS/OOR 回归钉 + 三股票 as_of smoke 离线 fixture 化）

## 目标

D-009 / 审计稿 §P1-4：把已重现的 provider 红灯收成**可离线复现的绿测**，禁止把供应商临时故障混进产品回归。

本卡范围：

1. **历史财报 THS**：Sina 失败且无合格公告日备用资格时，拒绝 THS 当日摘要（既有 `test_provider_historical_refuses_ths_fallback` 必须保持绿；若主干仍有旁路，堵死）。
2. **资金流 OOR**：请求日早于覆盖区间 → 明确 OOR/fail-closed，不得返回别日并标成请求日（既有 `test_fund_flow_requires_curr_date_and_oor_message` 必须保持绿）。
3. **三股票八接口 as_of smoke → 离线 fixture**：`tests/test_financial_as_of.py::test_smoke_three_tickers_eight_interfaces_as_of[...]` 当前在主干 tip 上 **3 failed**（`balance_sheet` `as_of is None`；实网/SOCKS 依赖）。本卡必须把它改成**冻结 payload/as_of fixture**；真实网络 smoke 单独 mark 隔离（默认不跑）。

不做社交、不做回测、不部署、不宣称蓝思案例已修。

## 基线

- 主干：`codex/dav-4-p2a-trunk` @ `6a799d460318acd9865584e80d9ad07b8e71df25`
- **新建**隔离分支，例如 `agent/dev2/p1-4-provider-red-lights`
- origin: `https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 不要 FF、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 权威

- `work/2026-08-27-audit-decision-semantics-plan.md` §2 五红灯、§7 P1-4
- `work/2026-08-27-decision-semantics-workflow.md` P1-4：定向集 0 fail

## 已复现（主干 `6a799d4`，Cursor 独立）

```text
FAILED test_smoke_three_tickers_eight_interfaces_as_of[600900.SH]
FAILED test_smoke_three_tickers_eight_interfaces_as_of[000333.SZ]
FAILED test_smoke_three_tickers_eight_interfaces_as_of[600276.SH]
# balance_sheet returned None as_of；日志：sina financial table … Missing dependencies for SOCKS support
```

同时：`test_provider_historical_refuses_ths_fallback`、`test_fund_flow_requires_curr_date_and_oor_message` 在离线 fixture 下已绿——本卡要**保持**，并补齐 smoke 离线化。

## 行为契约

改**原路径**；禁止 `_v2`。

### A. 三股票 smoke 离线化（主交付）

- 为 `600900.SH` / `000333.SZ` / `600276.SH`（或测试参数化清单）提供冻结 fixture（JSON/CSV），覆盖 smoke 断言所需的八接口中至少导致失败的 `balance_sheet`（及其它被断言的接口）。
- 测试默认路径：**不访问实网**；通过 monkeypatch provider/`_ak`/route 注入 fixture。
- 每个成功接口必须有可验证 `as_of`（非 None），且 `as_of <= requested curr_date`。
- Sina 失败 + 无合格 announce → 该接口应返回明确失败文本/`as_of` 语义符合既有 PIT 合同，而不是静默 None 还当成功。
- 原网络 smoke：加 `@pytest.mark.network`（或项目既有 mark），默认 deselected；文档一句说明如何开启。

### B. THS / OOR 回归钉

- 保持并（必要时加强）`tests/test_financial_announce_cutoff.py` 中：
  - `test_provider_historical_refuses_ths_fallback`
  - `test_fund_flow_requires_curr_date_and_oor_message`
- 若实现旁路仍能喂 THS 无公告日数据进历史分析，必须在 provider 原路径堵住。

## 允许修改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`（及直接相关的 announce/as_of 辅助，若已有模块）
- `tradingagents/dataflows/financial_announce.py`（仅当 THS 拒绝路径必须动）
- `tests/test_financial_as_of.py`
- `tests/test_financial_announce_cutoff.py`（仅追加/加固，不破坏既有绿断言）
- `tests/fixtures/...` 新建离线 fixture（财务 as_of / announce）
- 必要时 `pytest.ini` / `pyproject.toml` 注册 `network` mark（若仓库尚无）

## 禁止

- 实网作为默认 CI/定向回归依赖
- 社交、回测、VPA、confirmation、新闻 event_coverage
- 改 3/1、开加权、部署、碰脏文件
- `@pytest.mark.asyncio`

## 测试（TDD）

先在基线确认三 smoke 红，再 fixture 化至绿。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_financial_as_of.py \
  tests/test_financial_announce_cutoff.py::test_provider_historical_refuses_ths_fallback \
  tests/test_financial_announce_cutoff.py::test_fund_flow_requires_curr_date_and_oor_message \
  -q --tb=short
```

验收：定向集 **0 failed**；默认跑不得触网。

## 交付

1. tip 完整 40 位 SHA；父提交必须是 `6a799d4…`
2. 定向 pytest 精确数字
3. `git diff --stat` 白名单内
4. 未部署；未宣称案例已修
5. 卡置 `in_review`

## 不可违反

D-006/007/008/009/010；AGENTS.md 铁律。
