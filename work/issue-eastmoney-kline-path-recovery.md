# DAV-181 东财免费历史资金流 kline 路径恢复

## 新环境事实

用户已完全退出 Clash。当前：

- DNS 为公开 IP；路由 `en0 -> 192.168.31.1`；proxy env 为空。
- `push2his`/`push2` 的 `/fflow/daykline/get` 在 HTTP/HTTPS、curl/requests 下均 empty reply / RemoteDisconnected。
- 同一主机、同一参数下，`/fflow/kline/get` 在 HTTP/HTTPS、curl/requests 下均 HTTP 200、JSON、`rc=0`。
- 固定实测 `push2his ... /fflow/kline/get`：
  - `601398.SH`：120 rows，2026-02-24 至 2026-08-17，包含 2026-08-14；
  - `002167.SZ`：同样 120 rows并包含 2026-08-14；
  - 每行实际为 6 字段（请求 f51-f63 不代表服务一定返回 13 字段）；f51 日期，f52 已审计为 `r0_net`，f53-f56 仅保留 raw/discovery。
- 当前生产常量仍是 `/fflow/daykline/get`，解析器要求 `field_count >= 11`，所以即使切换路径也会错误拒绝真实 6 字段响应。

## 目标

在当前 target `codex/dav-4-p2a-trunk@21f58832b270917e540afa157ffd2daffa5b6e3f` 上做极窄恢复：使用已实测可用的 `/fflow/kline/get` 历史日级路径，并按实际最小契约解析，不猜未知字段。

## 允许修改

- `tradingagents/dataflows/providers/cn_akshare_provider.py`
- `tests/test_cn_akshare_backup_sources.py`（或当前同一功能的既有 provider 测试文件，仅一个）

禁止修改 collector、evidence consensus、analyst、API、数据库、配置、用户模型/provider/API Key、前端。

## 实现约束

1. 将 direct URL 切到经过固定实测的 `push2his.eastmoney.com/api/qt/stock/fflow/kline/get`；保留 klt=101、lmt、secid、timeout、headers、rc/schema/date 校验。
2. 最小接受字段为 f51 日期 + f52 主力净流入；真实响应 6 字段时成功解析。f53-f56 可按 raw/discovery 保存；f57+ 缺失不得伪造为空值语义或导致失败。
3. 仅 f52 映射 canonical `r0_net`；不得采用用户示例中未经证明的 f64/f65 或猜测 total-net。
4. 严格 `returned_date <= requested_as_of`；历史请求必须包含/截断到 requested date；未来行忽略，非法/重复日期、非有限 f52、rc/schema 错误 fail-closed。
5. 保留 attempted_sources/fallback_errors/failure_categories/final_source、actual/requested as-of、field/unit/algorithm_group。
6. 不修改 Sina legacy 规则；东财 direct 成功时属于 new algorithm source，但单源是否允许方向仍由既有 consensus guard 决定。

## 测试与交付

- 先写 6 字段真实形状 fixture：成功、requested date、未来行、非法 f52、字段少于 2、HTTP/JSON/rc/schema 失败和 fallback chain。
- `.venv310` 跑 provider 定向测试及相关 fund-flow evidence/collector 最小集；compileall、git diff-check。
- 做一次固定 live probe：`601398.SH`、`002167.SZ` / `2026-08-14`，仅输出脱敏 source/algorithm_group/as-of/field/unit/status/failure category。
- 推送新远端 branch/SHA，交独立复审；不合入、不重启、不上线。
- 评论不要 mention 项目调度助手。
