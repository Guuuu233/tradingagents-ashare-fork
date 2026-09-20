# P2 收口 / P3 入口（2026-08-26 ~00:25 UTC+8）

以本文件 + 实时 Git / Multica / `/healthz` / DB 为准。旧交接 `work/2026-08-25-p2-handoff-continue.md` 已过期。

## 一句话

**P2 已收口。** tip=`11309037de9334820603eec6dd801f291172f6ed` 已部署；v2 样本 n=10；五战场监控 DAV-421 **PASS**；data_gaps 分类 DAV-429/430/431 **done**。下一步是 **P3（H1b 前置定义 + H2 周评）**，H1b 加权未批准前保持 shadow-only。

## 实时核验

| 项 | 值 |
|---|---|
| 分支 / HEAD | `codex/dav-4-p2a-trunk` @ `11309037de9334820603eec6dd801f291172f6ed` |
| `target/codex/dav-4-p2a-trunk` | 同上 |
| `/healthz.commit_sha` | 同上；`executor_threads=4` |
| 持久轮次 | **3/1**（未改） |
| v2 completed（`protocol_version=v2_structured_disagreement`） | **10** |
| Multica P2 相关卡 | DAV-421/422/423/424/425/426/427/428/429/430/431 均为 **done** |
| 在途分析 | 0 |
| 样本补齐 / 5m loop | 已停（目标达成） |

## P2 验收摘要

| 规格 | 卡 | 结果 |
|---|---|---|
| §10.1 五战场监控 | DAV-421 | PASS：10/10 双方 opening≥3；宏观 60%、基本面 80%；无连续窄化 |
| §10.2 G1 同档告警 | DAV-422..424 | done |
| §10.3 G2 供弹盘点 | DAV-425 | done（只读） |
| §10.4 gaps + 财报备用 | DAV-426..431 | done；新报告 provenance 含 `gap_class` |

## P3 入口（规格 §11）

**不得直接开加权。** §11.1 要求先书面定义并经用户批准：

1. 最小样本量 N  
2. 时间跨度  
3. T+5 完整率门槛  
4. bull/bear 样本平衡  
5. 模型×方向偏置阈值  

未满足任一项 → **继续 H1a shadow-only**（P1-S / DAV-415/417 已落地）。

可并行准备（仍不加权）：

- **H2 周度评测脚手架**：指标采集、去重、可复算看板（§11.3）  
- **H1b feature flag 设计**：默认关；关闭后回 shadow，数据不丢（§11.4）

## 铁律（仍有效）

- 不改用户模型绑定 / Key / 持久 3/1  
- 不自行 FF；审核 PASS ≠ 已部署  
- Multica 从仓库根跑  
- Host 脏树：禁止 reset / clean  
- 证据用 `.venv310`  
- 调度助手只编排，禁止施工  

## 建议下一步（待你点头）

1. 开 P3 规划卡：起草 H1b 激活门槛草案 → 评估师审 → **你批准数字**  
2. 可选：开 H2 只读评测脚手架实施卡（零加权）  
3. 暂缓任何写入裁决路径的信用加权代码  
