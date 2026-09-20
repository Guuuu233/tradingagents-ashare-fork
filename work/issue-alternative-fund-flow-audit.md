# DAV-178 免费历史主力资金替代源事实审计

## 用户提供候选

1. `efinance.stock.get_history_bill`
2. 东财 `push2his ... /fflow/daykline|get`
3. 聚宽 JQData `get_money_flow`
4. 通达信本地文件 + mootdx/easy_tdx

## 已核验初步事实

- efinance PyPI 自述明确：`base on eastmoney`；源码 `efinance/common/getter.py` 的 `get_history_bill` 直接请求 `push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`。因此 efinance 不是独立数据源，只是同一东财接口的 SDK 包装，不能作为网络/WAF 故障时的独立 fallback。
- efinance 源码字段映射为：`f51 日期, f52 主力净流入, f53 小单, f54 中单, f55 大单, f56 超大单, f57-f61 各净占比, f62 收盘价, f63 涨跌幅`。用户示例将 `f51-f65` 15 列映射为 15 个列名，但维护库实际只映射 `f51-f63` 13 字段；示例字段数量/尾部字段存在风险，不能直接接入。
- 本机当前 DNS 已恢复到公开 IP，但路由仍经 `utun1024 -> 198.18.0.1`；`curl --noproxy '*'` 对 push2his 仍发生 LibreSSL SSL_read bad decrypt、空 body。故“国内 Mac 本地必然顺畅”在当前主机不成立。
- JQData SDK 源码确认存在 `get_money_flow`/`get_money_flow_pro`，但公开源码没有证明“永久免费每天 100 万条”；官网文档当前地区访问受限。配额/试用政策必须登录官方账号核实，不能作为无需凭据的生产方案。
- mootdx 公开定位是通达信数据读取；需要进一步证明本地离线文件实际包含主力/大单资金分类字段。`.day` 标准日线本身通常只有 OHLCV/amount，不应把客户端界面指标自动等同为可离线解析的资金流历史。

## 任务

只读审计，不修改代码/配置/数据库/用户设置，不安装依赖，不调用凭据接口：

1. 精确核对 efinance、qstock、mootdx/easy_tdx 的底层来源和资金流字段；区分“独立源”与“同一东财接口包装”。
2. 对 push2his 的 path、fields 数量、字段映射、HTTP/HTTPS、客户端/路由限制出具事实表；不得凭 README 猜字段。
3. 核对 JQData `get_money_flow` 字段、认证要求、免费/试用/付费政策的可验证证据；无法访问官方政策则明确 unknown。
4. 核对通达信本地 `.day`/其他文件是否真实保存逐日超大单/大单/主力净流入；没有证据则不能建议接入。
5. 给出优先级：A 可立即无凭据接入；B 需要用户注册/授权；C 同源包装无容灾价值；D 不可证明。
6. 输出必须包含 URL/仓库路径/文件行号或命令、限制和结论；0 tests（只读）。评论不要 mention 项目调度助手。
