# 历史分析报告只显示股票代码：名称 fallback 修复

## 用户现象

历史报告列表和详情又只显示股票代码，不显示中文股票名称。

## 已核验事实

- 当前服务 PID：`41593`，数据库路径为 `data/tradingagents.db`。
- `/v1/reports` 的列表路径使用 `_get_reverse_stock_map_cached_only()`；冷缓存直接返回 `{}`，因此响应把 `name` 回退为代码。
- 独立 `.venv310` 探针在清冷缓存时调用 `_load_cn_stock_map()`，AkShare 当前失败于深交所名称表：`SSLError/SSLEOFError`。
- 独立探针随后成功加载 5543 股票 + 26488 ETF/基金，`601398.SH → 工商银行`，说明数据本身可恢复，但当前服务内存缓存仍需重启或成功刷新。
- 已有 `tests/test_cn_stock_map_retry.py`：当前基线 `6 passed`，但它只证明失败重试，不证明报告 API 在冷缓存/名称源失败时仍能返回名称。

## 固定基线

- target trunk：`codex/dav-4-p2a-trunk@f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- 允许修改：`api/main.py`、已有 API 测试文件；如需前端只改现有报告展示测试/代码，先说明。
- 不修改数据库 schema、报告历史原文、用户配置、providers、API Key、资金流逻辑。

## 任务边界

1. 先写回归测试：名称缓存冷/源失败时，`/v1/reports` 或其名称解析 seam 不能把可恢复名称永久冻结为空；成功加载后列表/详情应返回 `name`。
2. 选择成熟的最小方案：复用现有本地/缓存能力或在名称源失败后短退避；不能每次列表请求阻塞 100 秒，也不能把失败 `{}` 当 7 天有效缓存。
3. 列表和详情必须行为一致：不能列表有名字、详情又只有代码，或反过来。
4. 旧报告必须保持可读，不能只修新报告。
5. 运行 `.venv310` 定向测试、compileall、`git diff --check`，推送完整远端 SHA。

## 交付协议

评论必须包含根因、改动文件、完整远端 branch/SHA、测试结果和未合入/未重启事项。最后使用：[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

禁止修改个人模型/provider/API Key/主干/服务。
