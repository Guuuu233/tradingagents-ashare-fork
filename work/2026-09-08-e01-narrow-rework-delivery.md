# E-01 窄返修交付：证据关系契约三处缺陷

**依据：** E-01 实施卡（`.../outputs/2026-09-08-e01-implementation.md`）+ 控制层复审推翻原 PASS。
**执行：** Claude。**边界：** 未 FF、未部署、未改历史、未新增 Multica 卡。原 `d41c6ca` 链原样保留。

## 链

- **基线（第一父）：** `d41c6cae90c55f75b239b08ff31709285d110c1e`（E-01 DAV-744）
- **分支：** `agent/claude/e01-narrow-fix`
- **白名单：** 仅 `tradingagents/agents/utils/evidence_relations.py` + `tests/test_evidence_relations.py`（与实施卡唯一白名单一致，其余 tracked 文件 0 修改）

| SHA | 关注点 |
|---|---|
| `eaf0137cb3418aa9f41967886c14d86fec4c46f4` | 冻结 metadata 不得被误判为非 JSON 安全 |
| `2d90418b5b9f02a3d59706b635163caf4ed466e6` | `datetime` 型 `baseline_date` 须 fail-closed 而非抛异常 |
| `d3d176310e6fea1c42bd288c1ddcb23c7607e531` | 时间字段存在但为空须 fail-closed，不得跨语义顶替 |

**按 AGENTS.md 铁律 4 一刀一个关注点**——这是 DAV-747 被打回后的直接教训。

## 三处缺陷与修法

### 1｜`eaf0137` 冻结 metadata 无法回流构造

*现象：* `EvidenceRelation("c", SUPPORTS, "d", existing.metadata)` 抛
`TypeError: metadata must be JSON-safe: Object of type mappingproxy is not JSON serializable`。
`from_dict` 收到冻结映射同样失败。数据本身完全 JSON 安全，**报错信息本身是误导**。

*根因：* `__post_init__` 第 4 步对**入参**做 `json.dumps`，第 5 步才深冻结。冻结产物
`MappingProxyType`/`tuple` 不是 json 可序列化类型，于是「已冻结的合法 metadata」被误判。

*修法：* 先 `_deep_thaw_mapping` 归一成纯 dict/list，再 `allow_nan=False` 校验，最后冻结归一结果。
NaN/Infinity 与不可序列化对象的拒绝行为不变，含嵌套层（新测试断言）。

*契约影响：* 实施卡要求「`to_dict/from_dict` 无损往返」。修前该要求在组合场景下不成立。

### 2｜`2d90418` `datetime` baseline 抛异常穿透

*现象：* `validate_relation(..., baseline_date=datetime(...))` 抛
`TypeError: can't compare datetime.datetime to datetime.date`。

*根因：* `datetime` 是 `date` 子类。baseline 分支先判 `isinstance(baseline_date, date)`，
datetime 落入该分支且**不归一**；而 ctx 时间戳分支先判 `datetime` 并调 `.date()`。
同一文件两处判型顺序相反，末尾 `dt > b_dt` 拿 date 与 datetime 相比。

*修法：* baseline 分支判型顺序改为与 ctx 分支一致——先 `datetime` 归一到 `date`，再判 `date`。

*契约影响：* 实施卡要求「PIT 前视与畸形日期 **fail closed**」。**异常穿透不是 fail closed**——
调用方拿到的是崩溃而非 `ValidationResult`。这是直接违反卡内硬要求，不是风格问题。

### 3｜`d3d1763` `or` 链跨时间语义顶替

*现象：* `published_at=""` 时静默回落到 `trade_date`，校验返回 `valid=True`。

*根因：*
```python
ts = ctx.get("published_at") or ctx.get("ann_date") or ctx.get("timestamp") or ctx.get("trade_date")
```
`or` 把 present-but-falsy 当成缺失。后果是**交易日冒充发布时间**通过前视校验。

*定性：* 与 DAV-719 判过的「mapping 净额 0 被 or 吃掉」是同一模式；跨语义顶替另触
D-008「不同时间类不得互换」。

*修法：* 按声明顺序逐键显式判定——键存在即表示该 ctx 已声明这一种时间语义，
present-but-empty 记 `MALFORMED_TIMESTAMP` 并 fail closed，不再换用下一个键；
一个时间键都没有才是 `MISSING_TIMESTAMP`。前视消息补上实际采用的字段名，使所用时间语义可追溯。

## 测试证据

```
工作树：/private/tmp/ta-e01-fix（隔离）
解释器：/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python
命令：  env -u PYTHONPATH ... -m pytest -q -p no:randomly
```

- 实施卡点名命令 `tests/test_evidence_relations.py + test_claim_cluster.py + test_decision_status.py` → **53 passed in 0.41s**
- `tests/test_evidence_relations.py` 单独 → **27 passed**（原 24，本返修 +3 个测试函数覆盖三处缺陷）
- **逐 commit 独立性**（在独立 worktree 验证，避免 checkout 竞争）：

| commit | 独立 checkout 后 |
|---|---|
| `eaf0137` | 24 passed |
| `2d90418` | 25 passed |
| `d3d1763` | 27 passed |

递增且无回退，证明每刀只加自己的测试且不破坏前刀。

- `git diff --check d41c6ca..HEAD`：无空白错误
- 工作树：clean
- 全量对照基线：见下方「全量」节

## 未做事项（诚实声明）

- **未接线。** `evidence_relations.py` 在生产链仍是零引用——这是实施卡明令（「不接入现有生产者/消费者」），非遗漏。接线是独立后续包。
- 未做 UI / 真实服务 / 部署验收。
- 未修改 `CollateralRecord`（卡内明令保持 `canonical_event_id=None`）。
- 未评估 `_deep_thaw_mapping` 对超深嵌套的递归深度上限——现有测试未覆盖病态深度。
- 未 push 到 origin。

## 后续

按 D-011 §3，本链由 Claude 实现，**复审须由另一角色**执行。按 D-011 §4.3，FF 须 David 写出完整 40 位 SHA 与「准予合入」。
