# CLS 财联社新闻源：历史游标、快照清单与时点防前视

## 定位

这是 DAV-118 的独立新闻源后续任务，不得混入当前 DAV-119 资金流三文件返工，也不阻塞资金流验收。财联社当前可复用的是官网网页 endpoint，不应表述为面向个人开发者、带 SLA/Key 的正式开放 API；接入仅限内部研究用途，公开再分发前另行核对授权。

## 已核验的接口事实

- 最新快速路径：`https://www.cls.cn/api/cache?app=CailianpressWeb&name=telegraph&os=web&sv=8.7.9`，当前可返回 `roll_data`。
- 历史主路径：`https://www.cls.cn/v1/roll/get_roll_list`，网页请求需要当前签名参数；可用 `last_time` 游标向更早时间翻页。
- 旧 `nodeapi/telegraphList`、`nodeapi/updateTelegraphList` 当前探针返回 404；`api/cache?name=telegraphList` 的历史语义本轮未能稳定复现，只能标记“当前条件下未复现/不稳定”，不能写成绝对不支持历史。
- 详情页 `detail/<id>` 不能作为所有电报的默认补全文路径；不得假设电报 `id` 一定等于文章详情对象 ID。

## 实现要求

1. **双路径**：Latest 使用 `/api/cache?name=telegraph`；Historical 使用 `/v1/roll/get_roll_list` + sign + `rn=50` + 游标分页。缓存路径只能作为首批/近实时快速路径，不能单独宣称具有完整历史能力。
2. **游标防漏**：每页取最老 `ctime`，下一页使用 `next_cursor = min_ctime + 1` 做 1 秒重叠；允许跨页重复，最终按 `cls_id`/电报 `id` 去重。不得只用 `min_ctime` 而不做边界处理。
3. **同秒边界测试**：构造或录制同一 `ctime` 有多条记录且恰好跨页的 fixture，验证使用 +1 重叠不会漏掉同秒记录；记录重复 ID 数量和去重后数量。
4. **Crawl manifest**：每次历史抓取必须保存机器可审计清单，至少包括：`started_at`、`finished_at`、`analysis_as_of`、`pages_requested`、`records_received`、`unique_ids`、`min_ctime`、`max_ctime`、`duplicate_ids`、`request_errors`、`cursor_sequence`、`coverage_complete`、`stop_reason`。禁止只在日志里声称“抓了 N 页”。
5. **原始快照**：保存每页原始 JSON 或不可变哈希/路径，并保留 `source`、请求 URL（脱敏 query）、`retrieved_at`、游标和页序，旧快照不可被新运行覆盖。
6. **时间**：原始保存 `ctime`（Unix 秒）；统一用 `datetime.fromtimestamp(ctime, tz=ZoneInfo("Asia/Shanghai"))` 生成 ISO `published_at`，禁止依赖服务器本地时区或 `datetime.fromtimestamp(ts)` 无时区调用。
7. **正文与链接**：正文优先 `content`，缺失时用 `brief`，再缺失才标记缺口；只有接口明确返回 `shareurl`/`source_url`/详情 URL 才使用。禁止拼接 `https://www.cls.cn/detail/{id}` 作为统一规则，禁止强抓详情页补全所有电报。
8. **Point-in-time guard**：只允许 `ctime <= analysis_time` 的记录进入历史报告；分页结束、覆盖范围不足、签名失败、游标不再递减、请求错误或无法确认完整覆盖时，必须写 typed gap/`coverage_complete=false`，不能把部分实时集合冒充完整历史集合。
9. **边界与成本**：设置最大页数、超时、重试和最早可回溯时间；到达服务端历史边界时明确 stop_reason，不声称覆盖全部历史。
10. **统一字段**：`cls_id`、`ctime`、`published_at`、`title`、`brief`、`content`、`level`、`subjects`、`stock_list`、`source_url/shareurl`、`source`、`requested_as_of`、`retrieved_at`。

## 测试与交付

- 使用 `.venv310` 跑定向测试；补充同秒分页、游标单调递减、重复去重、时间区/前视、content/brief/link 优先级、manifest 完整性和失败 gap 测试。
- 真实探针只输出脱敏结果，不输出 token/凭据；保存一次可复核 crawl manifest。
- 交付 branch/SHA、实际测试结果、manifest 示例和未覆盖的历史范围；未有远端 SHA 不得宣称完成。
- 不修改用户模型绑定、providers、API Key、个人设置或当前资金流任务代码，除非另行明确授权。

## 当前结论

财联社适合做近期事件/电报新闻源；历史回测可行性必须以 v1 游标分页 + 原始快照 + manifest + `ctime <= analysis_time` 证据链为准，而不是只凭接口存在或一次连续抓取的口头报告。
