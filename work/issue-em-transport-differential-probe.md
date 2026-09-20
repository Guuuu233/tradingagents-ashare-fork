# 东财历史资金流传输差异只读探针

## 固定对象

- 代码基线：`codex/dav-4-p2a-trunk@cb9c62e6be92f18aa15cf5a513f9430d0409bf0b`
- 候选 endpoint contract 参考：`agent/2/7eb2100b@935c189476d71f513be324e13c26037e29a38e47`
- 股票：`601398.SH`、`002167.SZ`
- 日期：`2026-08-14`
- 环境：宿主 `.venv310` / Python 3.10.20

## 任务

只读，不修改代码、配置、数据库、用户设置或主干。对比：

1. `curl --noproxy '*'` 与 `.venv310 requests` 对同一固定 URL 的 HTTP/TLS/Content-Type/JSON envelope 差异。
2. `push2his.eastmoney.com` 与 `push2.eastmoney.com` 同路径/参数的差异；只记录状态、rc、schema、行数、最新日期，不输出完整 payload。
3. 服务 PID 70470 的 proxy/no_proxy 实际环境是否覆盖两个域名；不得修改环境。
4. 如 curl 成功而 Python 失败，明确归类 TLS/client fingerprint；如两个域名能力不同，指出可复用 endpoint，但字段语义仍以已审计 f52→r0_net 为限。
5. 给出一个最小、可验证的下一步建议；不得直接接线或提交代码。

输出必须含精确命令、固定输入、结构化结果、blocked/available 结论、`0 tests`（若纯探针）和未合入/未重启/未上线；真实 mention 项目主管。