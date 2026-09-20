## 目标
修复普通分析路径基准日不归一化的问题：周末/节假日发起的分析，trade_date 直接用日历日（如周日 2026-08-09），导致资金流等依赖交易日的数据全部获取失败。

## 背景（Hermes 实测，2026-08-09 凌晨）
- 用户手动分析 000657.SZ：报告基准日 2026-08-09（周日，非交易日），报告内"近5日主力资金净流向数据因接口异常暂无有效交互记录"
- 实测：`route_to_vendor('get_individual_fund_flow', '000657.SZ', '2026-08-09')` 失败（东财 ConnectionError + 当日快照 AttributeError）；而 8/5~8/8（交易日）全部成功
- 原因链：
  1. 分析基准日 = 8/9（非交易日）→ `is_historical_analysis_date('2026-08-09')` 为 False → 不走新浪历史备用源（只服务历史日期）
  2. 东财被限 + 当日快照（新浪即时截面）周末拿不到数据 → 失败
- 现有保护：`api/main.py:_resolve_scheduled_trade_date`（:121）调用 `normalize_to_trading_day`（trade_calendar.py:354，回退最近交易日，绝不向前取），但**仅定时任务路径使用**（:184/:5944/:6038）
- 普通分析路径直接透传 `request.trade_date`（:2155/:2632/:2759/:3053），无归一化 → 用户周末/节假日分析必踩坑（数据错位+资金流失败）

## 修复要求
1. **普通分析路径 trade_date 统一归一化**：在请求入口（chat_completions / _run_job / dual-horizon 组装处）对 `request.trade_date` 调用 `normalize_to_trading_day`（回退到最近交易日，语义与定时任务一致：绝不向前取，防未来数据泄漏）
2. 无 trade_date 时（None/空）默认取"最近交易日"（复用 trade_calendar 的现有逻辑，确认默认行为）
3. 保留用户显式指定历史日期的能力（如分析 8/5 的数据，8/5 是交易日就保持 8/5；只有非交易日才回退）
4. 归一化失败（日历不可加载）时保持现有降级行为，不新增异常路径
5. 注意 `_normalize_analysis_horizons` 与 horizons 相关日期逻辑的一致性

## 测试要求（TDD）
- 模拟 trade_date=周末（如 2026-08-09 周日）→ 归一化为最近交易日（2026-08-07 周五）
- trade_date=交易日 → 保持不变
- trade_date=None → 默认最近交易日
- 防前视回归：归一化不向前取（用未来日期验证不被"修正"到未来）
- 现有定时任务路径行为不回归（已有测试）
- 全量回归 0 新增失败

## 验收标准
- 周末发起分析，报告基准日自动为最近交易日，资金流/数据全部正常获取
- 全量回归 0 新增失败；完成后评论汇报等 Hermes 验收

## 环境铁律
- 所有 Python 命令必须 `env -u PYTHONPATH`；测试用 `.venv310/bin/python -m pytest`
- 一个 commit 一个关注点；不自行提交主干
