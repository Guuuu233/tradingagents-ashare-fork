# DAV-1388：Tushare 新闻缓存移出发布目录

## 背景与边界

父提交：`aeebb5225bddd995cf57ddcff9f3f1063e94bf68`（取自远端 `codex/dav-4-p2a-trunk`）。

d17b0fe5 上线核对发现：`data_cache_dir` 默认 `tradingagents/dataflows/data_cache`，落在
`releases/<sha>/` 内，每次部署目录清零/重建 → 全市场新闻冷启动重新拉取（实测单次冷
启动 131s，当天首份约 81s）。本次只处理**缓存目录跨发布共享**这一项卡面要求（总控
派工口径：新闻缓存移出发布目录，放到发布目录之外的固定缓存路径）；enrich 缩短的进一
步方案按卡面先测耗时构成再定，本次在日志中补充分段耗时观测（见下）。

## 变更

- `tradingagents/default_config.py`：新增 `_default_data_cache_dir()`，
  `data_cache_dir` 默认值改为 `${XDG_STATE_HOME}/tradingagents/data_cache`
  （未设 XDG_STATE_HOME 时为 `~/.local/state/tradingagents/data_cache`）。
  路径与当前目录、SHA、worktree 和 `releases/<sha>` 无关，跨版本保留。
  与 DAV-1412 的 stock-map 存档共用同一约定（`${XDG_STATE_HOME}/tradingagents/`）。
- 可选环境变量 `TA_DATA_CACHE_DIR` 覆盖：必须是绝对路径，且解析后不得落在本工作树
  或任何 `releases` 目录内（与 `TA_STOCK_MAP_ARCHIVE_PATH` 同一约束）。
- `tradingagents/graph/trading_graph.py`：建目录用 `config["data_cache_dir"]`
  替代写死的 `project_dir/dataflows/data_cache`，两者始终一致。
- `tradingagents/dataflows/tushare_global_news.py`：
  - `_write` 原子化加固：tmp 文件带 `pid`/`thread ident`（多进程并发写不再共用
    同一 `*.tmp` 名），`flush+fsync` 后 `os.replace`，失败清理 tmp；
  - enrich 阶段新增构成日志：`tushare_news enrich: N 个回补段耗时 Xs`——卡面要求
    “先测出 enrich 各环节的耗时（回补多少条、并发多少）”，该日志给出段数与总耗时；
    每条 `client.query` 已受 `_GATEWAY_SEM` 并发约束，请求数即段分页数（可在网关
    侧核对）。
  - 文档串与注释同步新路径语义；D-058 过时数字更新（`_GLOBAL_NEWS_BUDGET_S`、
    `_GATEWAY_MAX_CONCURRENCY`、registry `~350s` 改为实测口径）。

## 兼容性

- 生产（多进程 uvicorn）：各进程同机同用户，`~/.local/state/tradingagents/data_cache`
  天然共享；首批写入时并发安全（原子改名 + 独立 tmp 名）。旧 `releases/<sha>` 内
  已有缓存不迁移——下次部署自动从新目录冷启动一次后即稳定；如需立即复用可由发布门
  将旧目录 `cp -r` 到新路径（本卡不含部署动作）。
- 测试/沙盒：`TushareNewsCache(str(tmp_path))` 显式 `base_dir` 不变；
  未传 `base_dir` 的调用方走 config，路径可用 `TA_DATA_CACHE_DIR` 钉到 tmp。
- 其它使用 `data_cache_dir` 的路径（y_finance CSV、stockstats）随之迁到稳定目录；
  均为可再生缓存，无持久化语义破坏。

## 测试

- 新增 `TestCacheDirRelocation`（6 用例）：默认路径在仓库/发布目录之外、
  `XDG_STATE_HOME` 生效、`TA_DATA_CACHE_DIR` 相对路径/发布目录内拒绝 + 合法路径
  接受、不传 `base_dir` 走 config、跨“发布目录”共享、并发写无 tmp 残留/无损坏。
- `tests/test_tushare_global_news.py`：35 passed（锁定解释器 Python 3.10.20，
  `env -u PYTHONPATH`，`DATABASE_URL` 指向隔离临时库）。
- `py_compile` 全部改动文件通过。
