## 目标
接入同花顺金融数据 API（fuyao.aicubes.cn）作为新数据源：财务数据主源 + 涨跌停/龙虎榜官方备用源。

## 背景
- David 推荐并已提供 API Key（2026-08-05），**全部接口实测通过**：行情快照（单只+批量）、历史K线（前复权日K）、利润表（年报多期）、财务指标（growth/profitability 等五类）、涨跌停池（137只分页）、龙虎榜（全部/机构/游资）、交易日历
- 规划文档：`work/2026-08-05-code-audit-plan.md` H5 章节（必读）
- **前置依赖：H1~H4 全部完成并验收后由 Hermes 放行**（本 issue 先保持 todo，勿提前开工）
- 文档权威定义（团队可直接抓取）：https://fuyao.aicubes.cn/llms-full.txt
- 施工纪律：见规划第 4 节

## API Key（重要）
- **位置**：宿主机 `/Users/davidliu/Documents/TradingAgents-AShare/.env` 中 `FUYAO_API_KEY`（已写入，gitignore 保护）
- 冒烟测试时读取该文件即可；**严禁把 Key 写入代码、评论、commit、issue 描述**；如 Key 需进 CI/测试配置，改用环境变量引用

## 任务清单
1. 配置：
   - `.env.example` 增加 `FUYAO_API_KEY=` 占位（真实 Key 不入库）
   - `start.sh` 透传 FUYAO_API_KEY（若现有脚本模式如此）
   - SSRF 域名 allowlist 加入 `fuyao.aicubes.cn`（对齐现有模型/数据源 allowlist 机制）
2. 新增 provider `tradingagents/dataflows/providers/cn_fuyao_provider.py`，注册进 registry：
   - 行情快照 `GET /api/a-share/prices/snapshot`（thscodes 逗号分隔批量，不分页）
   - 历史K线 `GET /api/a-share/prices/historical`（单 thscode；interval=1d；adjust=forward/backward/none；窗口≤10年）
   - 利润表/资产负债表/现金流 `GET /api/a-share/financials/{income-statements|balance-sheets|cash-flow-statements}`（period=annual/quarterly；limit=最近N期 或 start+end 区间）
   - 财务指标 `GET /api/a-share/financials/indicators`（report={yyyy}-{1|2|3|4}；五类 abilities：growth/profitability/solvency/operating/cashflow）
   - 涨跌停池 `GET /api/a-share/special-data/limit-up-pool`（分页）+ 连板天梯 `/limit-up-ladder`
   - 龙虎榜 `GET /api/a-share/special-data/dragon-tiger-list`（board_type=all/org/hot_money；date 仅一年内）
   - 交易日历 `GET /api/a-share/calendar/trading-days`（近一年，作对照/备用）
3. 统一错误码映射（对齐数据诚实原则）：
   - `0` 成功；`1001~1004` 参数错误（客户端 bug，显式报错）；`2001/2003` Key 无效/无权限（显式报告，不静默）；`3001` 标的不存在、`3002` 数据未就绪（VendorEmpty 语义）；`4001` 频率超限（退避重试 1~2 次，仍失败则切换备用源）；`5001~5003` 服务端错误（VendorFail 语义）
   - 所有失败走现有 vendor 链显式报告，绝不填 0/空串
4. route_to_vendor 接线（对齐现有供应商注册模式）：
   - 财务数据（financials/indicators）→ fuyao 主源，现有弱源降级备用
   - 涨跌停/龙虎榜 → fuyao 备用源（东财主源，fuyao 在 VendorFail/Refuse 时顶上）
   - 行情快照/历史K线 → fuyao 第三备用源（东财→新浪→fuyao）
   - 交易日历 → 与现有 calendar.json fallback 并存，fuyao 作在线对照
5. 测试：
   - provider mock 测试：响应信封解析、错误码映射、字段映射（snake_case→内部字段）、分页、批量
   - route 接线测试：财务走 fuyao、东财失败时涨跌停/龙虎榜切 fuyao
   - 真实冒烟：读 `.env` 的 Key 调 1~2 个接口验证（结果记录在评论，Key 不回显）
6. 文档：README 数据源章节新增同花顺；KNOWN_ISSUES 记录接入情况

## 验收标准
- 真实调用冒烟通过（财务+涨跌停+龙虎榜各至少一次 code=0）
- 分析流程中分析师能取到财务指标数据（有接线测试证明）
- 全量回归 0 新增失败
- Key 未泄漏（grep 仓库无 Key 明文）
- 完成评论 @项目调度助手，附测试结果摘要，等待 Hermes 验收

## 环境铁律
- 所有 Python 命令必须 `env -u PYTHONPATH`；测试用 `.venv310/bin/python -m pytest`
- 一个 commit 一个关注点；不自行提交主干
