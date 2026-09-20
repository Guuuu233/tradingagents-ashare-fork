# DAV-178 Tushare 成熟资金流源接入（DC/THS，fixture-first）

## 背景与决策

当前直接抓取东财/同花顺网页的链路被 TUN/DNS/WAF 卡住：东财 direct HTTP 502，同花顺页面 HTTP 403。不要继续猜私有接口或重复撞网页。

采用成熟的 Tushare Pro 结构化 API 作为新接入层：

- `moneyflow_dc`：东方财富个股资金流，每日盘后更新，历史自 2023-09-11，官方文档明确 `trade_date`、`net_amount`（今日主力净流入额，万元）及超大/大/中/小单字段；
- `moneyflow_ths`：同花顺个股资金流，每日盘后更新，支持 `trade_date/start_date/end_date`，返回 `net_amount`、`net_d5_amount` 和大/中/小单字段；
- API endpoint `https://api.tushare.pro` 当前网络实测 HTTP 200 可达（无 token/非法 body 返回结构化 40101 JSON，而不是 502/403 HTML）。

## 目标

新增一个窄范围、可审计的 Tushare 资金流 provider/fallback，使项目不再依赖网页抓取来获得 DC/THS 的历史结构化结果。没有 token 时 fail-closed typed gap；不得静默回退 legacy 值冒充新算法。

## 施工边界

优先仅允许修改：

- `tradingagents/dataflows/providers/cn_akshare_provider.py` 或现有最合适的资金流 provider 原路径；
- provider registry/路由的最小必要文件；
- 一个既有资金流 provider 测试文件；
- 如项目已有环境变量文档，可仅补变量名 `TUSHARE_TOKEN`，不得写入值。

禁止：

- 修改用户模型/provider/API Key/角色绑定；
- 把 token 写入源码、日志、测试 fixture 或评论；
- 猜测/逆向东财、同花顺、Sina App 私有接口；
- 修改新浪 Web legacy 语义；
- 直接改 target trunk、重启服务或上线。

## 数据语义

1. 记录中必须保留 `transport_provider=tushare`，同时将上游来源分别标成 `source_family=eastmoney` / `source_family=ths`，不能把两个来源都折叠成同一来源。
2. `moneyflow_dc.net_amount` 按文档标为“今日主力净流入额（万元）”；原始单位万元，统一换算亿元时除以 10000。
3. `moneyflow_ths.net_amount` 文档为“资金净流入（万元）”，不能未经证明直接等同 DC 主力净额；`net_d5_amount` 是“5日主力净额（万元）”，周期为 5d，不能与单日 DC 混合。
4. 仅当同股票、同 trade_date、同 period/window、同字段语义和单位可比时进入 consensus；否则分别保存并输出 typed `incomparable_field_semantics`，不得跨字段平均。
5. Tushare 缺 token/权限不足/限流/HTTP/JSON/API code/字段缺失/日期不匹配必须分类记录；只有 `trade_date` 精确匹配 requested as-of 才可用。
6. 新浪 Web 永远是 `legacy_web_algorithm` reference，不进入新算法 consensus。

## 测试与交付

- fixture 覆盖 DC 成功、THS 成功、token 缺失、权限不足、API code 非 0、字段缺失、日期不匹配、单位换算、字段不可比、失败后 legacy reference；
- `.venv310` 定向测试、compileall、git diff-check；
- 推送独立远端 branch/SHA；
- 未配置真实 token 时明确“fixture 通过、live token gate blocked”，不得宣称真实源已上线；
- 完成评论不要 mention 项目调度助手。

官方文档：
- https://tushare.pro/document/2?doc_id=348
- https://tushare.pro/document/2?doc_id=349
- https://tushare.pro/document/2?doc_id=170
