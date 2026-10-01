# DAV-1412：StockMap 持久存档与备用源

## 实现与边界

父提交：`8e4f09ac6c7f726b120ab4011b2e3bea9a2fa082`（取自远端 `codex/dav-4-p2a-trunk`，不用废弃的 main）。

加载顺序：AkShare 沪深京名称表 → 新浪独立完整 A 股名称表 → 本地存档。ETF/基金仍为 AkShare 可选补充；只有基金数据不算股票名称表加载成功。

- 线上源成功：启动原有 7 天内存 TTL，并原子写入存档。
- 两个线上源失败：读取存档，日志记录存档时间，保留默认 1800 秒重试退避。存档不能伪装成一次线上加载成功，不能因此延后重试 7 天。
- 失败且没有有效存档：返回空名称映射，报告列表仍返回股票代码。已有的前端 `report.name || report.symbol` 无需修改。
- 已暖内存过期不直接删除；存档也不可用时仍保留已有名称，避免刷新失败导致名称消失。
- 只更改名称表加载链路。未改前端、用户配置、CPA、凭据、数据库 schema 或业务写库逻辑；没有合入、部署、重启或运行真实模型。

## 稳定存档路径与格式

默认位置：`${XDG_STATE_HOME}/tradingagents/stock-map.json`；未指定 XDG_STATE_HOME 时为 `~/.local/state/tradingagents/stock-map.json`。路径与当前目录、SHA、worktree 和 `releases/<sha>` 无关，因此无需改现有发布环境便能跨版本保留。

可选环境变量 `TA_STOCK_MAP_ARCHIVE_PATH` 必须是绝对路径，且解析后不得落在本工作树或任何 `releases` 目录内。若发布门希望与数据库同级存放，可在后续获准部署时指定稳定目录内的绝对路径；本次没有修改生产环境变量。

存档是 UTF-8 JSON：

```json
{
  "schema_version": 1,
  "saved_at": "2026-10-01T10:28:41.854969+00:00",
  "stock_count": 2,
  "fund_count": 0,
  "name_to_code": {
    "贵州茅台": "600519.SH",
    "纬达光电": "920001.BJ"
  }
}
```

上例仅说明格式，不是提交的股票全名单。读入校验版本、带时区的时间、非空名称、六位代码及 SH/SZ/BJ 后缀、计数与映射大小。损坏/缺失/权限失败均记录日志并安全回退。写入采用同目录临时文件 + flush/fsync + `os.replace`；写失败不清空旧存档，也不丢弃已取得的线上名称。

## 新浪接口与完整性实测

参考最新 AkShare 一手实现：`https://raw.githubusercontent.com/akfamily/akshare/master/akshare/stock/stock_zh_a_sina.py`，使用新浪公开 `Market_Center.getHQNodeStockCount` 与 `Market_Center.getHQNodeData`，节点 `hs_a`。

本次锁定解释器实际输出：**Python 3.10.20**。所有项目 Python 命令均 `env -u PYTHONPATH`。

先于接入的独立实测：5571 行、5571 唯一代码、5571 唯一名称；沪 2319、深 2904、京 348，56 页，总耗时 43.49 秒。接入后的实际产品适配器于 `2026-10-01T10:28:41.854969+00:00` 再测：**5571 名称、5571 唯一代码，沪 2319 / 深 2904 / 京 348，37.82 秒**。例如：贵州茅台 `600519.SH`、京东方Ａ `000725.SZ`、纬达光电 `920001.BJ`。

完整性保护：服务端实测会把 `num=1000` 截为 100，因此固定每页 100，并核对每页应有行数、总数、重复名称/代码与沪深京覆盖；不接收部分页面、不把截断结果存档。范围检查为 4000–20000 只。仅消费代码和名称，不消费行情价字段。名称按新浪原文保留，可能带当日 XD 等标识。

请求独立 Session，不读取环境代理/认证；连接/读取超时为 5/10 秒，每请求一次有界重试，分页总预算 120 秒（最后一次已发出的请求可超过预算边界）。实测亦出现过一次第 7 页超时，后续同一实现再次完整取到全量；备用源并非永远可达，本地存档仍是必要兜底。

## 测试与证据

数据库均通过 `DATABASE_URL` 指向隔离临时库，清除代理变量，未使用生产数据库。外网实测只调用任务授权的公开名单接口，不启动 API lifespan。

- RED：新增测试在没有适配/存档模块的父版本上 17 failed（缺失模块）。
- 名称表专项：覆盖源失败读存档、所有来源缺失后报告列表显示代码、成功持久化与重启回读、备用源独立启动、损坏存档、原子写失败保留旧文件、跨发布 cwd 路径、退避与线上 TTL、并发及快路径。
- 名称表专项：41 passed（其中本次新增 22 个夹具用例）。扩展专项：290 passed，1 deselected，2 warnings（名称表 + socket guard/SSRF 等既有测试）。
- 父版本 RT-FULL：6306 passed，1 skipped，6 deselected，205 warnings，3 subtests passed。
- 最终候选 RT-FULL：**6328 passed，1 skipped，6 deselected，205 warnings，3 subtests passed**；最终观测零新增失败。
- `py_compile` 与 `git diff --check` 通过。

RT-FULL 使用：

```sh
env -u PYTHONPATH -u http_proxy -u https_proxy -u all_proxy \
  -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  DATABASE_URL="sqlite:///$ISOLATED_TEMP_DIR/rt.db" \
  /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest \
  -q -p no:randomly \
  --deselect 'tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields'
```

上述显式 deselect 是 DAV-979 已知主干死锁，不计候选新增失败；其余 deselect 来自仓库现有默认门禁配置。

保留中间结果，不隐瞒失败：首轮候选的测试隔离 fixture 依赖 monkeypatch，改变 socket guard 的拆卸顺序，产生 10 个 teardown errors；已改为独立环境变量保存/恢复，扩展专项确认无错误。此后一次全量出现既有 `test_route_timeout_retries_then_falls_back` 的 10ms 线程池时序断言失败；**不修改/不 deselect 该测试，也不修改产品树，完整原命令再跑后为以上 6328 passed**。另一次名称表+provider 组合专项同样出现这一时序断言失败。随后在父 SHA 的全新 detached worktree，只跑未修改的 `tests/test_provider_resource_cleanup.py`：前 4 次各 6 passed，第 5 次复现同一断言 `None != 'fast'`（1 failed / 5 passed），证实主线已有不稳定性，而非本次新增失败。只证明最终全量跑次全绿，不宣称已消除该时序风险。

交付评论附 RED、专项、父/候选 RT-FULL 日志及 XML、实际备用源结果与完整 diff，供同 SHA 只读复审。

## 发布门验收（本次未执行，不能当作线上恢复证据）

1. 同 SHA 只读复审、总控终签后，由发布门合入/部署；本分支不得直接部署。
2. 生产启动日志出现 `[StockMap] Loaded`（线上来源或存档），核对数量及源；确认 `[StockMap] Saved archive` 写入的是稳定路径。
3. 前端报告列表恢复中文名称；中文名称搜索可查到对应代码。
4. 跨下一次发布确认同一存档仍存在；线上源失败时日志包含存档 `saved_at`，默认 30 分钟重试仍生效。
5. 如存档目录只读，修复服务账号的目录权限，不改用户个人配置；没有存档且备用源也失效时仍只能显示代码，不能据此声称已恢复。
