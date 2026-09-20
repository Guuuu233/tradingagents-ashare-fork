# DAV-189 阶段二：DataCollector 全球宏观大盘接入与知识库上下文挂载

## 一、任务目标
承接 DAV-188 阶段一交付成果，完成全球宏观数据、大宗商品、国内核心大盘指数在 `DataCollector` 中的统一安全聚合，并将阶段一构建的 27 个行业产业链知识库与 19 类宏观事件情景图谱安全注入至宏观、基本面等分析师节点。

## 二、执行铁律与规范（严格遵循 AGENTS.md）
1. **防前视偏差（绝对铁律）**：所有新增全球指数、国内大盘、大宗商品历史切片必须通过 `_normalize_daily_frame` 或等价逻辑，严格按 `<= trade_date` 截断，禁止读取未来时间的数据。
2. **按列名取数**：严格按列名解析 DataFrame，禁止位置切片 (`.iloc`)；
3. **失败显式上报**：数据获取异常必须进入 `data_failure_ledger`，并返回结构化 `【数据获取失败】...`，禁止返回空串或假数据；
4. **单任务单关注点**：分工明确，测试全覆盖，经由代码审核员与测试员双重复核后方可合入。

## 三、施工拆解与分工
1. **T6（资深开发1 负责）**：
   - 扩展 `tradingagents/dataflows/providers/` 与 `tradingagents/dataflows/interface.py`，注册全球核心指数（标普500、纳指、恒生等）、大类资产（黄金、原油、美债10年期、美元指数）及国内核心指数（沪深300、创业板指等）的安全历史获取方法；
   - 在 `tradingagents/graph/data_collector.py` 的 `pool` 中安全集成 `global_indices`、`major_assets`、`cn_indices` 视图，并登记到失败台账与 provenance。
2. **T7（资深开发2 负责）**：
   - 在 `tradingagents/agents/analysts/macro_analyst.py` 与 `fundamentals_analyst.py` 等节点中，安全挂载 `tradingagents.knowledge` 导出的 `format_industry_deep_context` 与 `format_macro_event_context`，并接入大盘与全球市场视图；
   - 保持无 collector 时的异步回退路径行为一致。
3. **T8（代码运维测试员与代码审核员 负责）**：
   - 编写针对性单元测试与端到端历史回测无泄漏验证；
   - 独立代码审核，确保零前视偏差与全量回归测试通过。
