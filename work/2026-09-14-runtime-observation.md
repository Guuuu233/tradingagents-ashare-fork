# 2026-09-14 运行态只读观察

## 范围

本记录只保存运行态和外部能力的只读核验结果；没有启动分析、调用模型、写入生产库、启动 Compose、切换社交模式或改动用户配置。

## 核验结果

- 目标主线：`origin/codex/dav-4-p2a-trunk` 回读为 `4a41663a5f6ef25232408479da8887cf5110d434`；合入工作树 detached 且 clean。
- 线上服务：PID `59324`，工作目录 `/private/tmp/ta-release-p2-55b-f094d6a-20260914`；`/healthz` HTTP 200，运行 SHA 为 `f094d6a78bc699fc6224e57164d38455c2ad55a9`。
- Provider health：7 个 provider 返回正常，其中 `cn_stub` 明确为 placeholder；social 状态仍为 disabled。
- 固定回归标的只读行情：`600396.SH` 在 `2026-09-08`、`2026-09-09` 各返回一根 K 线；`002167.SZ` 同两日各返回一根 K 线。该结果只证明行情接口可读，不证明真实分析链路或收益结论。
- 生产库：`/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db`，SHA-256 为 `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`；只读 `quick_check=ok`、`integrity_check=ok`；reports 为 `completed=793`、`failed=616`。本次观察没有写库。
- 看板：逐页读取 10 页（最后一页 24 条），合计 `924` 张；`840 done`、`84 cancelled`、`0` 非终态。空闲 agent 未产生新的施工 run。
- Compose：本机 Docker Desktop 上下文为 `desktop-linux`，但 Docker daemon socket 不存在，`docker info` 无法连接；因此本轮不能把静态 Compose 文件核验升级为容器级挂载证据，也没有启动 Docker 作为替代。

## 当前可执行边界

现阶段剩余项目均已有明确边界：生产 trace/report/readback 需要数据写入授权，V-03a 等待 forward OOS，社交 Gate 0–4 等待外部账号/Cookie 和逐级授权，真实 Fuyao 天梯探测等待 key，Compose 挂载核验等待 Docker daemon。无新的安全代码施工项可从空看板凭空派发。
