# P0-3b 返修：不要把每股收益当金额做 H1−Q1

## 结论

Cursor 独立复审 `5e360af95d3cd1c1a5ad88b35ce7fbd325b197fa`：**不准予合入。**

相关套件在隔离 worktree `/tmp/ta-p03b-5e360af` 复跑 **68 passed**。Sina / backup 两处已接线，H1 header 仍 `not_derived`，无 Q1 时生产路径有 `reason=missing_q1`。这些不够。独立审核员 PASS、项目主管建议合入，都不够（D-010）。

## High

`INCOME_DERIVATION_WHITELIST` 含 `基本每股收益`、`稀释每股收益`，并对它们做 `H1 − Q1`。

复现（该 SHA，隔离树）：

```text
净利润 250/100 → 150（流量，可减）
基本每股收益 2.50/1.00 → 1.5  （写入 values，当 Q2 EPS）
稀释每股收益 2.40/0.90 → 1.5
```

H1 EPS − Q1 EPS **不是** Q2 单季 EPS。股本加权、送转、增发都会让这个差值变成假数。派生走的是过滤后的全表 `filtered`，不是 `_shrink_table` 之后的可见列，所以即使用户没在压缩表里看到 EPS，注入块仍可能写出假 Q2 EPS。

来源：DAV-473 白名单从 `utils.py` 的 **展示/压缩** 字段表抄来，那张表本来就含 EPS。展示字段 ≠ 可减金额。本卡纠正该合同。不要再把「DAV-473 原文写了 EPS」当成完成。

## 基线

- 继续分支 `agent/dev2/p0-3b-q2-derived`（父提交必须是 `5e360af95d3cd1c1a5ad88b35ce7fbd325b197fa`）
- 主干仍是 `codex/dav-4-p2a-trunk` @ `ba47284e3284fbed70fc104901fbe354907c7bee`
- 不要快进主干、不要部署、不要 `git add -A`
- 脏文件勿动：`AGENTS.md`、`frontend/src/services/api.ts`、`work/h1b_gates_report.json`

## 行为

在原路径 `derive_q2_from_h1_q1`：

1. 从 **可减** 白名单去掉 `基本每股收益`、`稀释每股收益`。
2. 防御：列名含 `每股` 的字段一律不减，进 `missing`（或等价 skip），即使以后有人加回白名单。
3. 净利润 / 营收等金额字段的成功派生保持不变。
4. 不要改 H1 `classify` / cutoff header 的 `not_derived`。不要做 Q3/Q4。不要改 `_shrink_table`。

`utils.py` 的展示字段表 **不要改**（不在白名单）。EPS 仍可出现在压缩后的原始表里；只禁止出现在 **派生块 `values` / 注入金额**。

## 允许修改

- `tradingagents/dataflows/financial_announce.py`
- `tests/test_financial_period_kind.py`
- `tests/test_financial_announce_cutoff.py`（只追加断言；不要改坏 `_three_tables` 既有 cutoff / `2024-08-20` H1 header）

不要改 `cn_akshare_provider.py`，除非独立复审后证明接线被这次改动弄断（当前不需要动接线）。

## 测试（先红后绿）

在 `5e360af` 上先写会失败的测试：

1. 利润表同时有 `净利润` 与 `基本每股收益`/`稀释每股收益`：`period_kind=single_quarter_derived`，净利润差值正确，**两列 EPS 都不在 `values`**。
2. 生产路径新夹具：`get_income_statement` 在可减 H1+Q1 下派生块含净利润类差值，**不得**出现 `基本每股收益=` 或把 EPS 差值写成 Q2 单季。用不易与净利润撞车的 EPS 数字（例如 3.21 与 1.07）。
3. 既有 68 条期望保持：无 Q1 → `missing_q1`；H1 header `not_derived`；balance 无 `single_quarter_derived` 金额。

```bash
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_financial_period_kind.py \
  tests/test_financial_announce_cutoff.py \
  -q --tb=short
```

## 交付

- 同一分支新 SHA，已 push
- `git diff --stat` 相对 `5e360af95d3cd1c1a5ad88b35ce7fbd325b197fa`
- 精确 pytest 数字
- 不要写「彻底修复」；不要自行合主干
- 合入必须等 Cursor 对新 SHA 写出完整 40 位 +「准予合入」
- **不准予部署**
- 不要 @项目调度助手催工
