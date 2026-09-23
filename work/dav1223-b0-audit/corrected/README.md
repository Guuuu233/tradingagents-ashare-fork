# DAV-1225 corrected/ — B0 定量纠错 + W0–W4 零 token what-if

## 输入（R5：不入库，路径 + SHA256 记录）

DAV-1222 冻结附件的本地副本（10 snapshot pkl + 50 state pkl，约 38 MB）
**不随分支入库**——仓库为公开仓库，第三方内容与二进制不进主干历史。
复跑需将本地副本放到 `work/dav1223-b0-audit/ext/{snapshots,states}/`
（生产检出 `/Users/davidliu/Documents/TradingAgents-AShare/work/dav1223-b0-audit/`
持有原件），或用 `--audit-root <dir>` 指向包含 `ext/`、`snapshot_manifest.json`、
`violations_attr.json` 的目录。输入完整性以 SHA256 记录：snapshot 对
manifest 校验、50 份 state 的 SHA256 写入 `out/run_all.json`。

## 复跑

```bash
# 仓库根目录，项目锁定解释器
env -u PYTHONPATH /path/to/.venv310/bin/python work/dav1223-b0-audit/corrected/run_all.py \
    --audit-root /path/to/dav1223-b0-audit
env -u PYTHONPATH /path/to/.venv310/bin/python work/dav1223-b0-audit/corrected/fixtures.py
```
- 输出：`out/run_all.json`（全量结构化结果）、`out/SUMMARY.md`（汇总）、
  `out/*.jsonl`（refs_all / unspecified_classification / typed_disclosure_review /
  invalid_conversion_review / executable_review / cross_basis_refs 逐条明细）。
- 零 LLM、零外部请求、零生产写库、不改 trunk；W1–W4 补丁全部在 `whatif_impl.py`
  副本内，以 `[W1]`…`[W4]` 注释标注。

## 文件

- `pool.py` — header-based snapshot CSV 解析；10/10 实际表头打印；
  date/open/high/low/close 缺一即 `SnapshotPoolError` fail-closed；
  `selfcheck_missing_column()` 为缺列自测；`named_field_hits()` 输出字段级
  provenance（指标名 / 日期+OHLC / 涨跌停 computed 字段），同值巧合单列。
- `classify.py` — 473 unspecified 六类、111 typed-disclosure 真伪、
  45 invalid_conversion、52 executable-level 的规则化逐条分类。
  [R2] typed 判定收紧为「该值是否该类披露价」（商品语境大宗/头寸增减持/
  票据股本发行/他标的发行价/坐标语境回购价 → false），ambiguous 归 0。
  [R3] derived 判定 token 级：同子句数字前 25 字内估值算术词，弱词不触发。
- `manual_review.json` — [R1] 177 条抽样逐条 manual_verdict 入库，
  SUMMARY 按类报告抽样精度。
- `whatif_impl.py` — trunk `build_price_ref_registry` /
  `evaluate_price_basis_gate` 的逐行副本 + 分层补丁：
  - W1 消除 non-price/foreign/typed-disclosure 伪命中 + conversion 需复权语义；
  - W2 `derived_estimate` 语义角色（非坐标、不作 decision-driving 问责、不可执行）；
  - W3 shared executable parser（gate 价位词并入 registry + 伪价位过滤）；
  - W4 严格 source-backed pool→registry 桥接（仅字段级 provenance）。
- `fixtures.py` — 9 条现状缺陷断言（RED 基线），全部 PASS。
- `run_all.py` — 单命令复跑全部。

## 根因注记

`stock_data` 实际表头两种序：`date,low,close,volume,open,high`（9/10）与
`close,low,high,open,date,volume`（s08）。DAV-1223 B0 按固定列号解析即
列错位 + volume 混入价格池，pool_hits 大量假命中——本目录全部按 header 名取列。
