# 返修卡：DAV-747 拆分为两个单关注点提交

**触发：** D-03-3 独立复审 ❌ 打回（`work/2026-09-08-d03-3-independent-review.md` R-1）。David 裁定 `4a83725` 违反 AGENTS.md 铁律 4。

**执行角色：** Codex（写审分离——本卡由 Claude 复审后产出，返修不得由 Claude 执行；返修后的最终 SHA 由 Claude 独立复审）。

**边界：** 不改 trunk 历史。不 FF、不部署。不改功能语义——本卡是**提交粒度重构**，最终树内容应与 `8f298a8` 等价。不新增 Multica 卡。

## 当前链

```
3496280 (trunk tip)
 └─ 704cd68  DAV-734  实现
     └─ 7f85e2b  DAV-739  封 scale 旁路 + fail-closed
         └─ 4a83725  DAV-747  ❌ 两个关注点
             └─ 8f298a8  DAV-750  展示文本一致性
```

## 拆分依据：两组 hunk 与测试完全不相交

### 关注点 A —— 严格有限数值校验

*失败条件：* 比率值本身非有限 / 不可解析 → 该比率是否可用、status 如何降级。

| 类型 | 位置 |
|---|---|
| 代码 | `smart_money_analyst.py` 顶部 import：`from decimal import Decimal, InvalidOperation`、`import math` |
| 代码 | `_is_valid_num` 整体重写（Decimal 解析 + `is_finite` + 异常收敛到 `InvalidOperation/TypeError/ValueError/OverflowError`） |
| 代码 | `circ_str` / `amt_str` 赋值门控重排（由「text 存在即用」改为「先判值有效」） |
| 测试 | `test_non_finite_values_fail_closed_and_downgrade` |
| 测试 | `test_finite_values_accepted_as_available` |

### 关注点 B —— 资金来源隔离

*失败条件：* 资金流来源标签被**分母来源**冒充 → 展示的 source 是否错误。与数值有效性无关。

| 类型 | 位置 |
|---|---|
| 代码 | docstring 契约第 2 条追加 `selected_source` |
| 代码 | selection 解析块：`raw_source` 严格 `isinstance(str)` + `strip()` 校验 |
| 代码 | **删除** `denominator_source = scale_metrics.get("denominator_source")` |
| 代码 | `source_display = selected_source or denominator_source or "未指定"` → `selected_source if selected_source else "未知/缺少资金流来源"` |
| 测试 | `test_fund_flow_source_isolation_from_denominator_source` |
| 测试 | `test_fund_flow_source_isolation_end_to_end_node` |

**B 组独立性补强：** 被删的 `or denominator_source` 回退链，与 DAV-719 判过的「`or` 吃掉合法值」属同一模式的近亲——此处是让**分母来源冒充资金来源**，属「证据来源不得串味」，与数值有效性无任何共享失败路径。

## 目标链

```
3496280 (trunk tip)
 └─ 704cd68  DAV-734   不变
     └─ 7f85e2b  DAV-739   不变
         └─ <new>  DAV-747a  fix(analyst): 严格有限数值校验（比率不可用即降级）
             └─ <new>  DAV-747b  fix(analyst): 资金来源标签不得回退到分母来源
                 └─ <new>  DAV-750'  展示文本与原始比率一致性校验（rebase）
```

**顺序依据：** DAV-750 整体替换了 A 组改过的 `circ_str`/`amt_str` 块，且**完全不触碰** `source_display`。故 750 依赖 A、不依赖 B；B 与 750 触碰不同行，rebase 无冲突。

## 实现方须自行决定的一点

A 组的 `circ_str`/`amt_str` 门控重排在最终树上**被 DAV-750 整体覆盖**。两种处理：

- **(i)** 747a 保留该重排（忠实拆分，中间提交可独立通过测试，但该段在最终树不可见）
- **(ii)** 747a 只含 `_is_valid_num` 重写 + imports，门控重排并入 750'

复审不代为决定。选 (ii) 须确认 747a 单独提交时 A 组两个测试仍能通过；若不能，只能选 (i)。

## 验收

1. `git log --format=%s 3496280..<新 tip>` 每条**单一关注点**，标题不含并列连词。
2. `git diff 8f298a8 <新 tip>` **应为空**——拆分不得改变最终树内容。若非空，须逐行说明理由。
3. 每个新 commit 单独 checkout 后，其对应测试通过（证明关注点真正可独立）。
4. 定向回归：

```bash
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest -q -p no:randomly \
  tests/test_smart_money_scale_metrics.py tests/test_fund_flow_scale_collector.py \
  tests/test_fund_flow_scale_metrics.py tests/test_tushare_daily_basic.py tests/test_h1b_gates.py
# 基准：303 passed
```

5. 全量对照 `work/2026-09-08-full-suite-baseline.md`：须为 `17 failed / 3388 passed`，失败集逐项相同。
6. 交付写完整 40 位 SHA、第一父、`git diff --stat`、真实 pytest 计数。

## 后续

新最终 SHA → Claude 独立复审 → David 写「准予合入」（D-011 §4.3）。复审方与返修方不得为同一角色。
