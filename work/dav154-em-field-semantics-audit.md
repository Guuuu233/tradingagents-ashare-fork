# DAV-154 东财直连资金流字段语义只读审计

## 目标

为正在执行的 DAV-153 提供东方财富公开 `push2.eastmoney.com` 个股资金流 daykline endpoint 的字段语义证据；只读审计，不改代码。

## 固定事实

- target trunk：`codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- 已脱敏直连探针：`002167.SZ`、`600396.SH` 在 `2026-08-14` 均 HTTP 200、`rc=0`、有 kline；完整响应不在任务中传播。
- endpoint 返回日期加 10 个数值字段。当前不能凭数组位置或字段名猜 `r0_net`、总净额、特大单、大单等语义。

## 施工边界

- 只读；禁止修改项目代码、测试、配置、数据库、用户设置、providers、模型绑定、API Key、凭据和主干。
- 不提交、不推送、不重启服务。
- 不逆向新浪 App 私有接口，不抓取或猜测未公开接口。
- 只使用公开文档、公开 endpoint 说明、已存在项目映射或可复核交叉样本；不把“数值看起来像”当字段定义。

## 交付内容

1. endpoint 路径和公开参数的脱敏说明；
2. 逐个字段的已证明语义、证据来源和置信结论；
3. 哪些字段可以安全映射到现有 canonical field，哪些必须保持 unknown；
4. 若无法证明 `r0_net` 或 THS `netamount` 对应关系，明确写“不能接入生产 evidence”，不要给实现建议冒充结论；
5. 给 DAV-153 的最小接入建议：可接字段、不可接字段、必须保留的 raw/unit/as_of/period/window 元数据。

## 验收

不得输出完整响应、凭据、签名参数或 cookie。报告必须明确区分：字段语义已证实、字段语义未证实、可作为 discovery evidence 但不能驱动 consensus。
