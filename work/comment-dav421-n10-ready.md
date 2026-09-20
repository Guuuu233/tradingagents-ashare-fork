## 样本已满：独立核验 n=10（可只读复盘）

编排侧刚核验 `data/tradingagents.db`：

- `status=completed` 且 `protocol_version=v2_structured_disagreement`：**10**
- healthz tip：`11309037de9334820603eec6dd801f291172f6ed`；持久轮次仍 **3/1**；`executor_threads=4`
- 补样仅用单次 `config_overrides.v2_debate_enabled=true`，未改持久配置 / 模型 / Key；未开 H1b
- DAV-431 部署已 done

| # | symbol | trade_date | report_id |
|---|--------|------------|-----------|
| 1 | 000858.SZ | 2026-08-24 | 96535777… |
| 2 | 000063.SZ | 2026-08-24 | 150281db… |
| 3 | 000651.SZ | 2026-08-24 | ce79dbb8… |
| 4 | 000001.SZ | 2026-08-25 | fa31da3d… |
| 5 | 000333.SZ | 2026-08-25 | be47058f… |
| 6 | 000725.SZ | 2026-08-25 | 72e5e457… |
| 7 | 002415.SZ | 2026-08-25 | 5e2cd88e… |
| 8 | 600036.SH | 2026-08-25 | 9fe35770… |
| 9 | 600519.SH | 2026-08-25 | 8fe83e03… |
| 10 | 601318.SH | 2026-08-25 | 954ea755… |

请按本卡原验收项做**只读**五战场覆盖复盘（bull/bear opening ≥3、macro_policy/fundamentals、连续退化检查），贴表后给结论。n 已满，可验收。

[@项目评估师](mention://agent/2c03cc8f-6628-4464-954a-84c47079fdf3)
