# DAV-155 同花顺资金流公开能力只读探针

## 目标

与 DAV-153 并行，独立核验同花顺公开/现有适配器资金流能力，判断是否能产生带真实日期、字段、单位和来源的结构化证据；只读，不改代码。

## 固定输入

- target trunk：`codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- integration branch：`integration/dav119@5f1d197ce59c9e16e5666534db903cae2ee6d2a3`
- 标的：`002167.SZ`、`600396.SH`
- 历史验收日期：`2026-08-14`
- 使用宿主 `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`；若不可访问，立即报告环境阻塞，不用系统 Python 冒充。

## 探针范围

1. 通过现有公开适配器/API 测试 `stock_fund_flow_individual(symbol="即时")` 的真实返回结构；
2. 记录是否有来源日期、查询日期、`净额`、单位、窗口、可验证 `as_of`；
3. 分别尝试当前/历史调用语义时，严格遵守反前视规则：历史 `2026-08-14` 不得用当前快照伪装；
4. 只读检查是否存在公开、日期可验证的 THS 历史资金流入口；不猜测私有 endpoint，不逆向 App。

## 交付

每个标的输出脱敏字段：`source`、`algorithm_group`、`status`、`as_of`、field、unit、window、period_kind、失败类型。若无真实日期或只能返回即时快照，明确判为不能满足历史 EM/THS comparability gate；不能把 THS `净额`写成新浪 `r0_net`。

## 边界

不修改任何项目文件、配置、数据库、用户设置、providers、模型绑定或凭据；不提交、不推送、不重启。只提供独立证据给 DAV-153/DAV-151，不宣称 consensus。
