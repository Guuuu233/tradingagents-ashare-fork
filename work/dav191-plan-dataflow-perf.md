# DAV-191 数据层并发与国内/全球指数采集极速优化 (毫秒级与超时压降)

## 一、问题根因定位
用户反馈分析流程卡在“数据就绪，多智能体协作分析启动中...”长时间等待。
经后端探针与性能剖析，定位到两大阻塞瓶颈：
1. **`cn_akshare_provider.py` 中 `get_cn_indices` 逐个请求全历史导致串行超长等待**：
   - 包含 7 个指数 × 3 级重试，其中 `stock_zh_index_daily_tx` 拉取自 1990 年起的全量 8,000+ 条历史，每个耗时高达 14 秒，7 个指数累计阻塞超过 60 秒触发全局采集超时；
2. **`AKSHARE_CALL_LOCK` 大粗粒度全局锁锁死并发**：
   - `get_cn_indices`、`get_global_indices`、`get_major_assets` 内部使用了 `with AKSHARE_CALL_LOCK` 包裹了整个包含多个 HTTP 请求的长循环，把 DataCollector 内部的并发线程池全部打成了串行排队；
3. **缺少轻量内存 TTL 缓存**：
   - 宏观大盘数据对同一交易日全局唯一，无需每次分析都重新拉取 7 个国内指数、9 个全球指数和 6 个大宗商品。

## 二、优化改造方案（严格遵循 AGENTS.md）
1. **轻量内存 TTL 缓存 (5-15 分钟)**：
   - 为 `get_cn_indices`、`get_global_indices`、`get_major_assets` 增加进程内 thread-safe LRU/TTL 缓存。首次获取后后续分析 0 秒返回；
2. **请求瘦身与快速失败**：
   - 国内指数首选 `index_zh_a_hist` 并指定 `start_date/end_date` 近窗范围（默认 90 天），单请求仅几 KB；
   - 避免无边界的全历史拉取；
3. **细化锁颗粒度**：
   - `AKSHARE_CALL_LOCK` 仅包裹单个网络请求本身，严禁包裹整个多指数循环。

## 三、分工与验收
- **资深开发1 负责**：实施 `cn_akshare_provider.py` 优化与内存缓存，将宏观采集耗时从 >60s 压降至 <2s；
- **代码运维测试员 负责**：复核防前视截断与单测通过率。
