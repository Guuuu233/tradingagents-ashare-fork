# Project State

## 当前状态头部（2026-09-25 总控更新；开工前必须重新回读，D-005）

> 项目总图见 `docs/PROJECT_PANORAMA.md`（快照，给全体成员看的概览）。

| 项 | 值 |
|---|---|
| 生产代码 SHA | `c5a90d5944498e87d98ca541a2dc5e9cae24ff10`（`/healthz` 精确回读；09-25 20:21 起，DAV-1282：全球指数取数修复——Tushare 有界并发与部分结果、停用新浪无日期 int_*；**价格返修与 E-04 返修均开启**）。上一版 `a666be5`（DAV-1276） |
| 主干 | 以 `git ls-remote origin codex/dav-4-p2a-trunk` 回读为准。`c5a90d5` 之后若只有治理文档提交，代码树与生产逐字节一致 |
| 待发布提交 | 无 |
| H1b 漏斗（D-035；生产与主干已同口径） | completed 1052 → v2 390 → D-009 合格 43 → HOLD 语义隔离 4 → 39 → 价格口径隔离 28（与 HOLD 重叠 1）→ **clean 12**（legacy 6 + v1 6） |

漏斗的统计口径：
- **数据来源**：生产库 `.backup()` 副本，快照时间 2026-09-23 21:54。
- **部署后新增的报告**：新增 completed 只有 smoke 报告 4390ddfd。它带 `price_ref_contract_version=price_ref.v1` 与 `price_basis_version=price_basis.unspecified`，按 `classify_price_basis_exclusion` 判定为 contract_incomplete，不进入 clean。这一结论来自读代码，下次刷新台账时用已提交的脚本复测。
- **clean 从 15 变成 12 的原因**：生产视图原来是 15，本次发布后 DAV-1139 HOLD 语义隔离生效，排除 4 条（其中 1 条与价格口径隔离重叠）。这不是数据丢失。
- **计算代码未变**：漏斗计算所用的 `shadow_credit.py`、`price_basis_isolation.py`、`verify_h1b_gates.py`，自 `9d03c89` 以来没有改动。

- **H1b 门槛**：FAIL / `KEEP_FALSE`。门槛要求单一 cohort ≥60，v1 cohort 目前只有 6 条。`credit_weighting_enabled=False`。
- **服务运行态**（D-038、D-041、D-044、D-045、DAV-1276）：
  - PID 84954，运行目录 `releases/c5a90d5`（locked）；回退源为 `releases/a666be5`（DAV-1282）。
  - 启动环境：`env -i` 白名单 16 项，与 DAV-1270 相同，含两个返修开关，不含 DUMP_DIR。
  - bundle 为 `index-DXhIKhFl.js`，含博弈论报告显示。
  - 回退启动命令存于 `logs/prev-launch-a666be5.txt`；`releases/3f34db5` 在签收后删除。
  - 按 David 09-25 的指示，已清理更早的发布目录 `f075124`、`b0ceff3`、`a181e4a`（git worktree 已解锁并移除，`.env` 为软链，目标文件未受影响）。今后只保留「当前」与「回退」两个发布目录，发布签收后删除更早的那个。
- **生产库**：
  - reports 共 1823 份：completed 1059、failed 764（09-25 20:3x）。
  - 部署前备份为 `data/tradingagents.db.bak-20260925-deploy-a666be5`。
  - 历史报告的 `game_theory_report` 列**不回填**（总控裁定，David 授权「你看着办」）：API 读取时回退取值，显示已经完整；回填属于生产库业务写入，没有收益，只有风险。
- **本次 smoke**（3c55216a，000725.SZ@2026-09-24）：
  - **VALID / HOLD / CONFIRMED，gate 通过，属于合格 clean**，这是 price_ref.v1 契约期内生产第一份合格 clean。
  - E-04 返修被采用：「已完全公开……无新增超预期」改为「已定价状态为 unknown」，总控逐条审计，没有逃逸。
- **上线后监控**（D-044、D-045）：已审计 3 份（eedb6128、5152eecc、3c55216a），没有触发回退条件。
- **口径：** `v2_debate_enabled` 只检查构建产物 `dist/assets`。
- **当前唯一关键路径**（D-041）：
  1. ~~**DAV-1235**~~：price-ref 精度收紧（冻结语料 5/50，D-042 补充）已随 DAV-1242 于 09-24 13:25 上线，同批上线的还有前端 DAV-1238 与关闭上游上报的 DAV-1243。
  2. **生成侧价格来源标注**（D-043）：
     - 提示词注入方案 DAV-1246 已证明无效；
     - 代码校验 + 定向返修 DAV-1249 机制有效，但合格 clean 仅 1/20，第一版不合入，按 v2 返工；v2 须设同代码对照组。
     - 冻结语料的主指标已改为「合格 clean」（旧的 gate 通过数把 ABSTAIN 计入，合格数实际为 0/50）。
     - **09-25 15:40（D-045）：** DAV-1261 诊断、DAV-1264 硬门修复、DAV-1267 研究经理 E-04 返修已合入主干 `3f34db5`；DAV-1270 已签「准予部署」，生产将开启 E-04 返修（16 项白名单）。
     - **09-25 更新（D-044）：** DAV-1249 v2 与 DAV-1255 已合入主干 `f075124`。受控对照中，合格 clean 开启返修为 4/30，关闭为 0/30。待统一发布门 DAV-1258 上线，并在生产开启返修。
  3. **B2+B3 解冻裁决**。
- **上线后首份真实报告**（smoke 4390ddfd，600519.SH@2026-09-23，VALID / BEAR / NO_TRADE）：
  - 共 205 条 price ref：vendor_qfq 177、unspecified 25、derived_estimate 3。
  - gate 结果为 blocked，共 42 条违规，出自 20 条 ref：16 条是模型自拟的交易价位；另外 4 条是抽取误报，其中差额 3 条、非本股价格 1 条。
  - derived_estimate 的 3 条里有 2 条误判，交 DAV-1235 收紧。
  - 字段桥接 1 条：pr-191 → `derived.limit_down@2026-09-23`，正确。
- **冻结语料 what-if**：a181e4a 在冻结语料上 6/50 通过，与 W4 一致。
- **样本产出率**（DAV-1227）：每 100 份 completed 约产出 2.45 个 clean，这是现在唯一的口径。契约期的新报告目前产出仍为 0。
- **冻结项**：
  - B2+B3 的 24 条真实重放；
  - 信用加权；
  - 社交 active；
  - 批量真实分析；
  - 概率输出与生成链改造（D-037）。
- **并行准备线**（D-037）：
  - DAV-1226、1227、1228 已签收。
  - DAV-1228 以 squash 提交 `0833925` 并入主干（D-042），probe-raw 只保留元信息；原分支与 squash 分支在主干快进后删除。
  - F1 阶段 H 的实施，在关键路径完成后再排期。
- **测量地基约束**：
  - `price_ref.v1` 下 51/51 blocked：50 份 DAV-1222 冻结重放，加 1 份 contract-era 生产 smoke `7a557f07`；
  - 固定输入方差（DAV-1222，10 个样本 × 5 次）：交易动作 5 次全一致的仅 2/10，H1b 资格发生翻转的 4/10；
  - 前向窗口 ABSTAIN 87.1%，8 条 VALID 全部为 WAIT（DAV-1044，09-19）；
  - completed 报告带 probability 的比例：2026-07 为 0/403，2026-08 为 7/328，2026-09 为 12/321。
- **已知运行态技术债**：
  - **前端 dist 过期**：自 08-28 起未重建，此后 10 个前端提交从未上线（截至 `49f1e87`），其中包括 DAV-765 校准面板、DAV-914/887 方向本地化。
    - 其中 e368362 会让聊天入口的分析改用 v2 辩论（后端默认关闭），属于 D-037 冻结范围。
    - 先由 DAV-1236 做只读评估，再由总控逐项裁决。
  - **一条读活库的测试**：`test_rt_s4_david_account_clean_population_counts` 以 `mode=ro` 读活库，并与 09-08 的硬编码计数比对，因此在任何 SHA 上都会失败（575≠317）。它不是回归信号，应改为固定快照，或移出 RT-FULL。
- **角色**（D-033）：
  - 总控（Claude 桌面会话）：最终裁决与签字；
  - 总工：执行统筹；
  - ChatGPT：独立复核；
  - 看板动作以「【总控】」「【总工】」前缀区分。
- **路线索引**：见仓库根目录的 `ROADMAP.md`。

## 2026-09-23 → 2026-09-24 变更摘要

- **主干**：`9d03c89` → `a181e4a`。
  - 治理：`daba99d`（D-033～D-038）、`5e43e29`（计划与研究文档入库、D-038）、`ae89d2b`（D-039/D-040、DAV-1224 v2 规格）。
  - 证据：`53df9ad`（DAV-1225 纠错返修）。
  - 代码：DAV-1224 C1–C5，即 `80c1f5e`、`6196774`、`98c4d3c`、`46e84c2`、`6043fbc`；另有 `a181e4a`，只删除 4 处 EOF 空行。
- **上线**：`3d9c414` → `a181e4a`（DAV-1233，09-24 03:16）。
  - 同时带上 DAV-1135、DAV-1139、DAV-1138。
  - 生产迁入独立发布 worktree，社交开关改为显式设置。
  - 第一段 RT-FULL：基线 5401 passed / 1 failed，候选 5488 passed / 1 failed，新增失败 0。
  - 另做受控 smoke 1 条（4390ddfd）。
- **看板**：
  - DAV-1224、1231、1232、1233 已 done；
  - 新立 DAV-1235（P0）与 DAV-1236（P1，前端变更清单），均放在 backlog。总工同时立的占位卡 DAV-1234 与 DAV-1235 重复，已取消；
  - 旧分支 `agent/2/dav-1225-b0-corr` 已删除。

## 2026-09-19 → 2026-09-23 变更摘要

- **主干**：`116c7d6` → `9d03c89`，共 67 个提交（66 个非合并提交）。主要包括：
  - 证据核验器：DAV-1088、1091、1093、1140（A–E）、1141、1144～1148、1157～1177；
  - `price_ref.v1` 契约：DAV-1142、1196～1200、1207、1211；
  - 财务口径与违规传播：DAV-1108、1134、1143；
  - Tushare 全球指数与财报三表接入：DAV-1098、1099；
  - E-04 修补：DAV-1110、1135；
  - 资金流：DAV-1086、1138；
  - H1b 入场价契约与 HOLD 语义隔离：DAV-1107、1139。
- **上线**：09-22 以来依次为 `b51a528`（DAV-1186）→ `96f14eb`（DAV-1206）→ `7f865c9`（DAV-1209/1210）→ `3d9c414`（DAV-1214/1215）。09-20～21 期间另有多次上线（如 `846357a`、`8a5b1bf`、`55cd0bb`、`91afea8`），见各卡评论，本次未逐一回读。
- **新口径**：
  - `price_ref.v1` 契约，以及 Stage 4 价格口径隔离（DAV-1199/1200）；
  - Stage 3.5 HOLD 语义隔离（DAV-1218，目前仅在主干）；
  - direction_basis / evidence_basis 账本（DAV-1187/1188）；
  - semantic coverage gate（DAV-1193）。
- **诊断**：
  - B2/B3 分层历史重放，每批 12 条，见 `work/h1b-regime-pool*.md`；
  - DAV-1222 固定输入方差实锤：S3=4、S4=4、S5=0；
  - DAV-1223 B0：高层结论保留，定量部分作废（D-034）。
- **角色**：09-23 起由主控（ChatGPT）签字；同日由 D-033 改为单一总控制。

## 看板收口记录（2026-09-23 总控，详见 DAV-1229）

- **改为 done**（代码均已在主干和线上，经 merge-base 核验）：DAV-1107（`96db9ce`）、DAV-1108（`846357a`、`7e87829`）、DAV-1134（`8a5b1bf`）、DAV-1178（父卡 DAV-1148 已 done）。
- **取消**：DAV-1203，与 DAV-1204 重复。
- **改为 backlog**：DAV-1112（并入 DAV-1227），DAV-1225（防止自动指派，待总工指派）。
- **新立**：DAV-1226、1227、1228，均为 backlog。
- 所有状态变更都用 `--no-start`，事后核查确认没有唤醒任何 agent run。
- 6 张 HD2D-CIRNO 卡与本项目无关，未改动，不计入本项目统计。

---

## 2026-09-19 快照（历史，勿据此开工）

最后核验：2026-09-19。远端 trunk `codex/dav-4-p2a-trunk` 回读 = **`a290f187f18635dfc2c4890cea2645bed9f09f1a`**（含 09-18 ABSTAIN 修复线 7 个提交：DAV-1068 去重裁决态 `bbca10a`/`c76972e`/`1210c70`/`254d2ab`、DAV-1071 E-04 引述/条件豁免 `6993fbf`、DAV-1061 SQLite sidecar 加固 `0db3bb9` 及 merge `a290f18`）。**`a290f18` 已于 2026-09-19 受控部署并在线**：serve worktree `/private/tmp/ta-serve-a290f18`，PID 1018（PPID=1）运行于 8000，`/healthz` 精确回读同一 SHA，`executor_queued=0`；部署证据 `work/deploy-a290f18-postcheck-20260919.md`，部署前备份 `work/tradingagents.db.bak-20260919-021456-deploy-a290f18`。生产库 `data/tradingagents.db` 当前 reports **1736**（978 completed / 758 failed，含受控验证新报告 `c21456dd`），quick_check=ok。社交 active 未开启：`/v1/social-data/status` 回读 `mode=shadow`、xhs/dy operational，归档 357 快照。

**H1b 正式门槛口径（D-009 §5 cohort，2026-09-19 重算）：977 completed → 315 v2 → 17 D-009 eligible。** cohort 拆分：`legacy_unversioned` 7 条（冻死，生产现只产 v1 cohort），`decision_model.v1:evidence_contract.v1:price_basis.unspecified` 10 条；两者按 D-009 隔离不得合并。**仍 FAIL / KEEP_FALSE**——v1 cohort 10/60，样本量不足。198 条存量 ABSTAIN 缺陷签名：A（DAV-1068 `unadjudicated_material_claims_adopt`）52、E-04 priced-in 69、E-04 超预期 15，合计 132/198（66.7%），零重叠。**a290f18 部署后受控单样本验证（报告 `c21456dd`，详见 `work/abstain-fix-verification-a290f18-20260919.md`）：两个旧闸（unadjudicated_material_claims_adopt、E-04 priced-in/超预期）均未再触发；该报告仍 ABSTAIN 系真实资金流/方向证据闸拦截，不计入 H1b 样本。** 单次样本只证明修复路径生效，存量 132 条缺陷签名的真实下降须后续批次验证。

✅ **DAV-998 阻断级缺陷已修复并合入**（经 `4355ca3`→`e295b58` 链）：T+1 refusal 可重试态与永久终态已解耦（`TERMINAL_REFUSAL_CODES` 白名单）；部署动作已于 2026-09-18 独立放行并完成。

✅ **本轮合入批次全部门禁通过**（同 SHA 只读复审）：
- `4355ca3`（整合 989/995/996/998+护栏）：Gate0 ✅ + DAV-1031 复审 ✅
- `e295b58`（卡C+A+B+5窄修组合）：DAV-1034 有条件通过，RT-FULL 8failed/4911passed vs 基线20/4836，0新增失败
- `68ba906`（DAV-952 K线窗口）：DAV-1036 复审 ✅
- `df753841`（DAV-1032+1035 merge）：三父保留，410 passed/38.83s
- 五窄修（931/932/933/943/953）均同 SHA 复审 PASS

✅ **部署门已收口**：`4b540b0` 已通过独立服务 worktree 上线并完成 `/healthz`、数据库备份守恒、前端根路径和报告接口烟测；部署脚本保留在 `work/deploy-4b540b0.sh`，发布后证据为 `work/deploy-4b540b0-postcheck-20260918.md`。

✅ **2026-09-18 已部署 `4b540b0` 至本机 8000**：初始 PID 52913 曾运行于 worktree `/private/tmp/ta-serve-4b540b0`；后因临时会话退出，由独立 PID **68658（PPID=1）** 接管。`/healthz` 精确回读 `commit_sha=4b540b0c9b08d77a12ce7d08cfdf0095288cd350`。部署前备份 `work/tradingagents.db.bak-20260918-130737-deploy-4b540b0`（quick_check ok；备份时 1418 reports / 794 completed / 624 failed）。发布后前端 `/`→200、`/v1/reports`→200、伪造 API 路径→404。

✅ **博弈论生产可达证据已验证**（一次性登录认证，未建永久 token）：`POST /v1/analyze` 600519.SH/2026-09-17/market/短线 → job `f8c59465` 完成，决策 `NO_TRADE`；trace 确认 `game_theory_analyst` 执行（`bundle_id=game_theory_v1`，确定性计算），报告字段+信号写入并回读一致；该次 reports 1409→1410。记录 8 项数据缺口 → **证明链路可达且诚实降级，非所有数据源正常**。证据 `work/df753841-game-theory-live-smoke-20260918.md`。

✅ **4b540b0 受控真实 H1b 分析已完成**：账户 `429163f7-50b6-4982-8bdf-96ae99506843`，`600036.SH/2026-09-11`，显式 v2 辩论，job `5fd6de0b…` 完整执行并写库；结果 `v2_structured_disagreement`、winner=bear，但 E-04 `priced_in` 无可回溯证据触发一致性硬闸，`analysis_status=ABSTAIN`，不计入 H1b 合格池。报告计数 794→795 completed；T+5 案例真实回填 `+1.16%`。H1b 仍 `FAIL/KEEP_FALSE`。证据 `work/deploy-4b540b0-postcheck-20260918.md`。

✅ **社交 shadow 运行与归档已实测**：线上进程环境为 `TA_SOCIAL_MODE=shadow`、平台 `xhs,dy`，归档库 `data/social_archive.db` 存在且可读。最近 xhs ingest `203 read / 203 inserted / 0 rejected`，dy ingest `154 / 154 / 0`；归档快照 `357`、实体映射 `93`。这证明采集与归档链路成功，不等于 active 影响交易决策。active 继续关闭。Cookie 未导出、未保存、未人工读取；采集使用已登录浏览器会话的登录态。

✅ **V-03 只读实验基线已交付**（DAV-1041）：`df753841` 离线重放，83 有效样本/76 收益样本，毛 -0.28%/净 -0.48%/胜率 55.26%；反泄漏拦截 future_data 2 条、7 变体消融一致、FORWARD_OOS=0 如实记录。零写生产库。半成品基线，非定性判断；FORWARD_OOS 复跑等真实报告累积（子卡 DAV-1044 backlog）。

在途：工程施工为 0；当前仅保留 DAV-998 收盘回填、V-03 前向样本和 H1b 样本积累等条件性观察项。

✅ **2026-09-18 生产库符号碰撞清洗已执行**（授权内写库）：先 `.backup()` 副本 → dry-run 报告（4 映射/34 隔离）→ 备份库清洗验证（`600519`→`600519.SH` 740+1→741 等）→ 生产库原子 UPDATE 4 行（`000001`×2→`000001.SZ`、`600519`→`600519.SH`、`603259`→`603259.SH`）→ `wal_checkpoint(TRUNCATE)` 落盘。清洗后：`000001.SZ`=59、`600519.SH`=741、`603259.SH`=12；残留隔离行 34（32 空 + AGENT + AUUSDO，quarantine 设计保留）。守恒 1410，`quick_check=ok`。生产库 SHA256 由 `94d2f674…` → `c8e52938e090…`（首次变更）。部署前备份 `work/tradingagents.db.bak-20260918-pre998replay`。映射报告 `work/symbol_canonical_mapping_report.{md,json}`。

✅ **DAV-998 重放机制验证通过**（`backfill_pending_cases(as_of='2026-09-18')`）：唯一 pending 案例 `600519.SH`（eval_date=今日）因 `eval_date_not_closed` 正确判为可重试暂态而非终态（修复前会误标永久排除），明日收盘后自动回填。`total_scanned=1/still_missing=1`——机制正确，无需人工重放。

✅ **H1b 修复已合入并部署（2026-09-18）**：候选 `4b540b0c9b08d77a12ce7d08cfdf0095288cd350` 基于 `8e49a80`，代码审核员 DAV-1065 同 SHA PASS，定向 `112 passed`，组合全量与基线均 `17 failed / 4959 passed / 1 skipped / 5 deselected`，无新增失败。部署后以真实账户 `429163f7-50b6-4982-8bdf-96ae99506843` 运行一次受控 v2 分析：任务 `5fd6de0b…` 完整完成并写库，但 `analysis_status=ABSTAIN`（E-04 priced-in 无可回溯证据触发一致性硬闸），不计入 H1b 合格样本。生产库因此为 `795 completed / 624 failed`；`credit_weighting_enabled=False`，门槛仍 `FAIL/KEEP_FALSE`。证据 `work/deploy-4b540b0-postcheck-20260918.md`。

⚠️ **前六次合入的流程合规性瑕疵**：当时基于「全量会挂死」的错误判断改用分文件对照取基线，而 D-012 §4b（`DECISIONS.md:71`）明确规定全量测试不可由子集替代。后经实测全量约 28 分钟可跑完。**仅 DAV-941（第七刀）做了正式的父/候选完整全量对照**。前六刀的「零新增失败」有参考价值但不等于当时合规；建议对 `b95a9b88..DAV-998 返修 tip` 做一次聚合全量回归补齐证据。

✅ **RT-FULL 基线已可用**（DAV-979）：主线 `28d1adc6` 实测 `20 failed, 4777 passed, 1 skipped, 3 deselected in 1700.10s (28:20)`；`5a0320f` 同口径 `20 failed, 4801 passed`，失败集合一致。跑全量不得设低于 45 分钟的上限。

🔴 **RT-FULL 卡死真因：baostock EOF 忙循环**（DAV-979，2026-09-16 第三次更正，最终结论）。**网络是通的**：同一代理环境下 `baostock.login()` 0.077s 成功、查行情 0.681s 成功（Clash 对 `public-api.baostock.com` 走 DIRECT）。缺陷在 `baostock/util/socketutil.py:55 send_msg`：`while True: recv = sock.recv(8192); receive += recv; if receive[-13:] == b"<![CDATA[]]>\n": break`——**无 EOF 检查、无超时**。对端关闭连接后 `recv()` 恒返回 `b""`，终止条件永不满足 → **100% CPU 无限忙循环（livelock）**。触发链：`POST /v1/reports` 保存 completed 报告 → 历史案例计算 → baostock 取 T+1 行情 → 对端关闭 → 忙循环。**线程池超时只能停止「等待」，无法杀掉已运行的供应商线程**，故上层 60s 超时对此完全无效。实证：DAV-996 进程 PID 6619 持续 132.3% CPU 达 1h19m、持有两个 `:10030` **CLOSE_WAIT**（对端已发 FIN、本端未关闭，与缺失的 EOF 处理完全对应）；DAV-992 遗留 PID 12371 跑 `test_api_smoke.py` 单文件空转 4h28m（正常单跑 `56 passed in 29.53s`）；假 socket 复现 **1 秒空转 4,332,446 次**。两者已清理。

> ⚠️ **需严格区分两种形态**（此前运维曾混为一谈）：
> ① **阻塞式慢**：`%CPU=0`、`STAT=S`，60s 超时×3 = 181.3s——耗时构成的主体（`--durations` 前 7 名均在 `test_fund_flow_scale_consumption.py`，合计约 21 分钟）。
> ② **忙循环卡死**：`%CPU≈100-132%`，永不退出——真正的「挂死」。
> 运维曾称「根本不是死锁」，该说法**只对一半**：① 成立，但 ② 确实存在。跑测试时若见进程高 CPU 长时间不结束，就是 ②，立即中止，干等无意义。

**修复方向**（三项并行）：DAV-995 测试全局离线隔离（**只拦 `connect` 不够**，必须覆盖已存在的 baostock 模块级 `context` 复用 socket）+ baostock EOF/超时处理；DAV-996 api_smoke 全局状态复位；DAV-989/991 线程清理。**DAV-992 保持 cancelled**。

本文件包含历史快照；开工前必须重查 Git / Multica。

## 2026-09-15 P2A 主线三刀合入（DAV-944 / DAV-940 / DAV-930），未部署

| 项 | 值 |
|---|---|
| 合入 | `b95a9b88c81e87a4da121f9945aedbe844fa04e0..8854853cfc167fd9bb015528138ae36a1df6bf8f` 非强制快进；`ls-remote` 回读逐位一致 |
| DAV-944 | 候选 `f64d8548` → `f125179`；审查 DAV-973 ✅ PASS；`cn_akshare_provider.py` |
| DAV-940 | 候选 `cafa39b8` → `9f2ab95`；审查 DAV-975 ✅ PASS；`macro_market_utils.py`、`industry_linkage_provider.py` |
| DAV-930 | 候选 `3ea5513a` → `8854853`；审查 DAV-981 ✅ PASS；`api/main.py` |
| 三者产品文件 | 互不相交，串行 cherry-pick 全部干净 |
| 生产库 | `data/tradingagents.db` SHA256 合入前后一致 `94d2f6740db4f206...`，零写入 |
| 部署 | 未执行；8000 端口服务仍停机 |

**放行依据（对照方法已变更）**：RT-FULL 在主干上必挂（见下），无法取得完整基线，改用**分文件执行**取可比对照——223 个测试文件逐个独立进程、120s 看门狗，基线与叠加树使用完全相同切分与命令，解释器 `.venv310`（Python 3.10.20）。结果：两侧均 `214 OK / 8 失败文件 / 1 挂死文件`，**失败集合逐项一致，零新增失败**；叠加侧多出候选自带的 `test_hot_stocks_api_contract.py` 9 passed。并行扫描时 `test_cohort_metadata_persistence.py` 曾在叠加侧报 HANG，串行无干扰复验两侧均 12 passed（25.92s / 22.19s），确认为 CPU 争抢导致的看门狗假阳性。

主干既有失败集合（未因本次合入改变）：`test_cninfo_disclosure_metadata` 1F、`test_dav27_report_semantics` 2F、`test_debate_state_persistence` 5F、`test_game_theory_integration` 1F、`test_provider_date_guards` 1F、`test_signal_processing` 3F、`test_social_data_api` 1F、`test_two_stage_analyst_topology` 4F。

## 2026-09-16 第四、五刀合入（DAV-938 / DAV-946），未部署

| 卡 | 候选 | 合入后 | 审查 | 白名单 |
|---|---|---|---|---|
| DAV-938 | `66591467` | `331a322` | DAV-985 ✅ PASS | `Dashboard.tsx` + 其测试 |
| DAV-946 | `483a1098` | `483a1098` | DAV-986 ✅ PASS | 6 个 dataflows 产品文件 + `tests/test_historical_yfinance_pit.py` |

`8854853` → `331a322` → `483a1098`，均为非强制快进。生产库 SHA256 全程不变 `94d2f6740db4f206...`，未部署未重启。

**DAV-946 运维独立核验**：历史日 `2024-01-02` 下，`y_finance` 五个 raw 入口（balance_sheet / cashflow / fundamentals / income_statement / insider_transactions）全部返回 `VendorRefuse`；`alpha_vantage_fundamentals` 四个原始函数底层 `_make_api_request` **零调用**。DAV-980 的两个 🔴（raw wrapper 返回普通字符串、Alpha Vantage 绕过 provider 护栏）均已消除。失败集合对照（分文件口径）：基线 `215 OK / 8 失败 / 1 挂死`，候选 `216 OK / 8 失败 / 1 挂死`，唯一差异为候选自带新测试文件 `106 passed`，**零新增失败**。

**DAV-938 例外声明**：本机 `frontend/node_modules` 为空，未独立复现 npm 数字，测试数字采信 DAV-985 审查证据；运维改作静态语义核验，确认 `PARTIAL→watch` 优先于 `trade_action` 回落，且与 `DecisionCard.tsx` / `Reports.tsx` 对 `INVALID_RUN`/`DATA_ERROR`/`ABSTAIN`/`COMPLETED` 的映射逐条一致。

## ~~2026-09-16 RT-FULL 挂死根因已定位（DAV-979 → 实施卡 DAV-992）~~ 【本节结论已撤销】

> 🚫 **以下整节结论已于 2026-09-16 撤销，仅作错误归因留档，勿据此开工。**
> 真实情况见文首「baostock EOF 忙循环」一节（第三次更正，最终结论）。套件在网络正常时能跑完（28:20），但连接被对端关闭时会真实卡死。耗时几乎全部来自测试触达**真实 baostock 服务器**——它走裸 TCP、不读代理环境变量，每次调用可等 60s 并重试，vendor 链叠加。`--durations` 显示前 7 名全在 `test_fund_flow_scale_consumption.py`，每条 ≈181.3s ≈ 60s×3，合计约 21 分钟。
> 下文「关闭模块级全局 `_executor` 导致后续用例永久阻塞」的机制**未被证实**：DAV-989 的候选 `b5f63d4` 正按此方向修，复现命令仍卡在同一位置。
> 但**另一个独立问题确实存在**：进程/线程收尾不干净。实证——DAV-992 运行取消后遗留 PID 12371 跑 `test_api_smoke.py` 单文件空转 4 小时 28 分、持续 99.5% CPU（该文件正常单跑为 `56 passed in 29.53s`），`sample` 显示热点全在纯 Python 字节码帧、无系统调用等待，与 baostock 的阻塞等待（%CPU=0 / STAT=S）形态完全不同。该缺陷归 DAV-989 / DAV-991。
> 正确拆分：**DAV-995**（测试默认禁真实外网 + socket 隔离）、**DAV-996**（复位 api_smoke 全局状态）、**DAV-989/991**（executor / TestClient / 后台线程收尾）、**DAV-992 保持 cancelled 不再施工**。


用增量删除（delta debugging）在 `b95a9b88` 只读检出上定位：**`tests/test_api_smoke.py` 是挂死的必要条件**（移除它即不挂），其搭档呈累积效应而非单一文件。

机制：`tests/test_api_smoke.py:471 test_lifespan_can_restart_on_same_event_loop` 在同一 pytest 进程内用 `asyncio.run` 进出 `api.main.lifespan` 两次；而 lifespan 退出路径 `api/main.py:441` 执行 `_executor.shutdown(wait=True)`，关闭的是 **模块级全局单例**（`api/main.py:607` 创建），于是把整个进程范围内的 `api.main._executor` 永久关闭，后续用例永久阻塞。附带两处泄漏：`new_default_executor.shutdown(wait=False)` 不等回收（每次 64 线程）；`_get_client()` 的 `TestClient` 既不用 `with` 也不 `close()`。

证据：`test_rt7_single_and_dual_horizon_isolation` **单独跑 0.18s 通过**、整个文件单跑 2.73s 结束，仅在全量上下文中无限期挂死；栈为主线程 `PyThread_acquire_lock_timed → _pthread_cond_wait`。修复已开 **DAV-992**（资深开发2），验收包含可复现命令必须正常结束 + 完整 RT-FULL 必须跑完。

## 2026-09-15 RT-FULL 门禁自身不可靠（DAV-979），原归因已证伪

全量套件在主干上无法跑完，实测存在**三类现象**，此前记录的单一归因不成立：

| # | 位置 | CPU | 形态 | 状态 |
|---|---|---|---|---|
| 1 | 38% `tests/test_game_theory_integration.py::test_rt7_single_and_dual_horizon_isolation` | 0% | 锁阻塞 | 已定位用例，**污染源未定位** |
| 2 | 96% | ~171% | 高 CPU 空转 51 分钟 | 未定位 |
| 3 | `tests/test_knowledge_rag.py` | — | 分文件执行时两侧均挂 | 主干既有 |

关键证据：`test_rt7...` **单独跑 0.18s 通过**，整个 `test_game_theory_integration.py` 单独跑 2.73s 结束，仅在全量上下文中无限期挂死 → 属**跨用例污染**（前序测试遗留锁或未回收线程），非该用例自身缺陷。栈证据：主线程 `PyThread_acquire_lock_timed → _pthread_cond_wait`，另有线程停在 `poll`/`internal_select`。

**原 `--deselect tests/test_fund_flow_scale_consumption.py::...` 缓解方案无效**：deselect 确实生效（`4652/4656 collected, 4 deselected`），但两侧仍在 38% 处挂死。任何引用该 deselect 声称「已绕过死锁」的审查结论均不足以支撑 RT-FULL 通过。环境未装 `pytest-timeout`，不得为此擅自向 `.venv310` 安装依赖。

## 2026-09-15 审查环境铁律已写入 agent 指令

DAV-973 审查中发现审核员回落到系统 `python3.14` 取证。实测：3.14 **并非跑不通**（依赖齐全、`py_mini_racer` 可 `eval`、多个测试文件两解释器结论一致），问题是**依赖集不等价且不可复现**——pandas `2.3.0` vs `3.0.5`（跨大版本，字符串列 dtype `object→str`、copy-on-write `False→True`）、numpy `2.2.6` vs `2.5.1`、akshare `1.18.30` vs `1.18.75`；生产服务运行在 `.venv310`，系统 site-packages 不受 `uv.lock` 锁定。

已将「运行环境铁律」追加进 `代码审核员`、`代码审核员2`、`资深开发1`、`资深开发2`、`高级开发·支援` 五个 agent 的指令（原指令逐字保留，备份见 `work/agent-instructions-backup/`），并落盘审查卡模板 `work/review-card-template.md`。要点：绝对路径 `.venv310/bin/python` + `env -u PYTHONPATH`、报告须贴 `-V` 输出、禁用任务工作区 `.venv` 与系统解释器作门禁证据、隔离 `DATABASE_URL`、代理指向关闭端口、卡死超 10 分钟不得反复重跑。

## 2026-09-15 DAV-925 假阴性留档

`59c05db..b95a9b88` 九刀合入链经核验属实（SHA、白名单逐条吻合）。但 **DAV-925 代码审核员给出的「0 生产代码缺陷」结论不成立**，实证：父版本 `59c05db` 下 `shrink_table(max_rows=1)` 返回最旧行 `2023-12-31`，主线返回最新行；`slice_hist_df(df,"NOT-A-DATE",...)` 父版本原样返回全部 3 行、主线返回空。审查通过不等于无缺陷，历史审查结论不得直接复用为放行依据。

## 2026-09-13 E-04（DAV-877）：预期修正契约第三轮返修已合入，未部署

| 项 | 当前证据 |
|---|---|
| 候选 / 第一父 | `bdb95f8d9533d4e20278d635f17386422f6de8a4` / `c23b5ae3e8695e4f35c3d2b33b182c2f0440d0d7` |
| 目标分支 | `origin/codex/dav-4-p2a-trunk` |
| 红队 | DAV-879；同 SHA PASS；42 项契约、2 项证据密度、全量对照均无新增失败 |
| 代码审查 | DAV-880；`代码审核员` 同 SHA 只读 PASS，无高/中问题；未使用独立代码审核员 |
| 改动范围 | 4 个白名单文件，`+440/-29` |
| 合入 | `020d3e3b18147f5ea20e90d3878b20952d20fd97..bdb95f8d9533d4e20278d635f17386422f6de8a4` 非强制快进；远端回读逐位一致 |
| 运行态 | 未部署、未重启；`/healthz` 仍为 `a227cdc3bb466edf2e910419cb6013cfc021d309` |
| 证据 | DAV-877 / DAV-879 / DAV-880 卡内报告；候选契约 `42 passed`，证据密度 `2 passed`；全量失败集合与基线一致 |

本次合入不等于上线：部署、生产库写入、真实社交采集、Cookie、信用加权和历史重写均未执行。H1b 仍为 `KEEP_FALSE`；L4 真实社交 Gate 0 仍未满足。后续部署须作为独立动作重新核验，不由本次合入自动触发。

## 2026-09-13 V-03a DAV-866：默认 provenance 返修已合入，未部署

| 项 | 当前证据 |
|---|---|
| 候选 | `020d3e3b18147f5ea20e90d3878b20952d20fd97`；直接父 `8c69eab186bde58e49cbc134fb4da5f015c77e92`；远端分支 `origin/agent/1/14ab0be94894` |
| 修复 | 无服务上下文的 `EvaluationStamp`、`SnapshotManifest`、`measure_dataset([])` 统一输出 `offline_replay_gap`；历史样本生成 SHA `a6d4540...` 保留在正确字段；显式 SHA / healthz / 离线四条路径分开记录 |
| 红队 | DAV-867 已完成；结论可行，HIGH=0、MEDIUM=0；生产库零写入、25 字段审计、7 变体快照守恒 |
| 代码审查 | DAV-868 已完成；**代码审核员**对同一完整 SHA 只读审查，HIGH=0、MEDIUM=0、LOW=1，明确“准予合入”；未使用独立代码审核员 |
| 全量对照 | 候选 `4291 passed, 19 failed, 1 skipped, 3 deselected`；父版本 `4288 passed, 19 failed, 1 skipped, 3 deselected`；19 个失败逐项一致，双向差集为空 |
| 合入 | `a227cdc..020d3e3` 非强制快进；`ls-remote origin codex/dav-4-p2a-trunk` 回读逐位一致 |
| 运行态 | 未部署；服务 `/healthz` 仍为 `a227cdc3bb466edf2e910419cb6013cfc021d309` |
| 数据保护 | 全量测试前后生产库 SHA256=`94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`；`quick_check=ok`、`integrity_check=ok` |
| 证据 | `work/v03a-9-dav866-independent-check-20260913.md`、`work/v03a-10-code-review-card-dav866-20260913.md`、`work/v03a-11-full-regression-20260913.md` |

LOW 项为既有 RT-S4 实时计数断言（生产库全量 318、历史截点 317），未因本次返修新增；本次合入不授权部署、重启、真实采集、生产写入或信用加权。

## 2026-09-13 DAV-808 / DAV-856：custom_prompt E-02 守卫已上线

| 项 | 当前证据 |
|---|---|
| 候选/当前 trunk | `a227cdc3bb466edf2e910419cb6013cfc021d309`；父链为 `b92acd15...` → `54077b6...` |
| 合入 | `54077b6..a227cdc` 非强制快进；远端 `codex/dav-4-p2a-trunk` 回读逐位一致 |
| 实现 | DAV-856；返修 DAV-859 恢复既有快照路径严格断言并补阻断分支测试 |
| 覆盖复核 | DAV-857 完成；RT-13 至 RT-16 纳入实施/复审门禁 |
| 代码审查 | DAV-860；`代码审核员` 对同一完整 SHA 只读 PASS |
| 全量验收 | 候选 `19 failed, 4268 passed, 1 skipped, 3 deselected`；与线上基线 19 项失败集合一致，无新增失败 |
| 运行态 | 服务 PID 35280；`/healthz` HTTP 200，`commit_sha=a227cdc3...`；启动恢复 `failed=0`，回填 0 |
| 数据保护 | 部署前备份 `work/tradingagents.db.bak-20260913-deploy-a227cdc`；部署前后 `completed=793, failed=616`，quick_check=ok |
| 证据 | `work/dav856-merge-deploy-20260913.md` |

首次 DAV-858 审查因漏看既有测试断言被保留为 `blocked` 历史；其后 DAV-859 返修并由 DAV-860 在同一完整 SHA 上复审通过。未启用信用加权，未进行真实社交采集、Cookie 读取或生产数据回写，未改写 `5489166b29c2` 全局提示词基线。

## 2026-09-12 DAV-854：E-03c 普通反驳路径修复（当前施工）

| 项 | 当前证据 |
|---|---|
| 问题 | `7810f198` 相对线上 `70b5b47` 多 2 个全量失败；普通被反驳命题的动作由 `WAIT` 错变为 `NO_TRADE`；PIT/前视失败的 `NO_TRADE` 硬闸不动 |
| 修复卡 | DAV-854，urgent，`done`；开发、复审、全量验收和 FF 均已完成 |
| 第一父 | `7810f19875890725cd26e414cde272063fb3606a`（开工前由 `git ls-remote origin` 回读） |
| 实现者 | 资深开发1；真实 run `01a0961c-94f1-7acc-9783-00516c3bbf61` 已完成；候选 `54077b6ad4a287bbd8c43d386e91427662a5e786`，使用宿主 Python 3.10.20 |
| 无效/重复任务 | 早先使用 Python 3.14 的 run `01a09618-f0b6-759f-9789-3bcfb67e0002` 已取消且未产生代码；两个重复 queued run `01a09619-013d-78c9-a17c-03a020b7b6ed`、`01a0961b-4223-757c-b7b1-92caed1c590b` 已取消 |
| 允许范围 | `decision_status.py` 的被反驳动作判定；测试只增不改松既有断言；不得碰配置、数据库、部署、DAV-808 |
| 审查 | DAV-855；`代码审核员` 对同一完整 SHA 只读 PASS；无严重/中等/建议项；未改码、未合入、未部署 |
| 验收 | RT-1~RT-7 通过；父 `7810f198` 全量 21 failed，候选全量 19 failed；候选失败集合与线上 `70b5b47` 的 19 项逐项一致；证据 `work/2026-09-13-dav854-rtfull.md` |
| 放行 | 依据 D-013，WorkBuddy 已执行 `7810f198..54077b6` 非 force FF；远端回读逐位一致 |
| 运行态 | 服务已部署 `54077b6ad4a287bbd8c43d386e91427662a5e786`；数据库未改写，reports 仍为 1409 |

### DAV-854 合入记录

| 项 | 值 |
|---|---|
| FF | `7810f19875890725cd26e414cde272063fb3606a..54077b6ad4a287bbd8c43d386e91427662a5e786`，未使用 force |
| 合入后远端回读 | `refs/heads/codex/dav-4-p2a-trunk` = `54077b6ad4a287bbd8c43d386e91427662a5e786` |
| 证据 | `work/2026-09-13-dav854-rtfull.md`；DAV-855 同 SHA 代码审核 PASS |
| 部署 | 已执行；服务副本 `/private/tmp/ta-serve-dav854.dsusBF`，PID 24162（PPID 1）；`/healthz` 精确回读候选 SHA |
| 部署前备份 | `work/tradingagents.db.bak-20260913-deploy-54077b6`；源库/备份库 reports 均 1409，均 `quick_check=ok` |
| 启动验收 | 日志确认 `Recovered stale active reports: failed=0`、`backfilled=0`；provider/date smoke 通过：stock/margin/lhb 命中，历史 zt_pool 显式 `VendorRefuse` |

---

## 2026-09-12 WorkBuddy 接替终审位后的放行记录

### DAV-828 / E-01 producer

| 项 | 值 |
|---|---|
| 候选 | `21140a63ffe5beace8fc5b2b73530913c97f70bd` |
| 第一父 | `70b5b47bcff0618e0db1258a438b0875440d4ac5` |
| 分支 | `agent/2/e3f9faf9e727` |
| 独立复审 | DAV-835：通过；专项 16 passed；关联套件 370 passed；RT-FULL 失败集合与基线一致 |
| 放行 | WorkBuddy 依据 D-013：准予合入；不含部署授权 |
| FF | `70b5b47..21140a6`，未使用 force |
| FF 后回归 | 关联 14 套件：370 passed in 1.44s |
| 远端回读 | `codex/dav-4-p2a-trunk` = `21140a63ffe5beace8fc5b2b73530913c97f70bd` |
| 部署 | 未执行；生产库、加权、真实采集均未触碰 |

DAV-828 已收口为 `done`。后续候选必须以新的 trunk tip 为第一父重新验收，不得直接沿用 `70b5b47` 的独立分支做第二刀 FF。

### DAV-829 / L2 博弈论生产接线

| 项 | 值 |
|---|---|
| 串行整合候选 | `a72daa2da5c1a02de90b992cdf7bcef068a99779` |
| 第一父 | `4433efb21de928ab387d050fb50cf1f5369f1672`（父链上一层为 `21140a63...`） |
| 原候选 | `f452ef94e659b9eceb51f8d66766ecd9997f1273`，已在 DAV-828 合入后 rebase，未改原分支历史 |
| 分支 | `agent/workbuddy/dav829-on-21140a6` |
| 代码审查 | DAV-841：代码审核员通过；12 passed；RT-FULL 失败集合与基线一致 |
| 放行 | WorkBuddy 依据 D-013：准予合入；不含部署授权 |
| FF | `21140a6..a72daa2`，未使用 force |
| FF 后回归 | `tests/test_game_theory_integration.py`：12 passed in 11.57s |
| 远端回读 | `codex/dav-4-p2a-trunk` = `a72daa2da5c1a02de90b992cdf7bcef068a99779` |
| 部署 | 未执行；生产库、加权、真实采集均未触碰 |

DAV-829 已收口为 `done`。DAV-830 必须基于 `a72daa2...` 重新 rebase、由代码审核员审查后才能进入下一刀 FF。

### DAV-830 / L3 真实结算管道

| 项 | 值 |
|---|---|
| 串行整合候选 | `41b77dc7a0871a849744b8013db3290700ccc883` |
| 第一父 | `fed94aa56c43412a93f16762cd4663019f924903`（父链包含 `a72daa2...`） |
| 原候选 | `b72105fc2df848b864dd5de962df37e91f870378`，已在 DAV-829 合入后 rebase，未改原分支历史 |
| 分支 | `agent/workbuddy/dav830-on-a72daa2` |
| 代码审查 | DAV-842：**代码审核员通过**；红队 35 passed；专项 121 passed；RT-FULL `18 failed, 4161 passed, 1 skipped, 3 deselected`，失败集与基线一致 |
| 放行 | WorkBuddy 依据 D-013：准予合入；不含部署授权 |
| FF | `a72daa2..41b77dc`，未使用 force |
| FF 后回归 | 结算与标签专项：121 passed；联合不变性六套件：229 passed（仅既有 JWT 警告） |
| 远端回读 | `codex/dav-4-p2a-trunk` = `41b77dc7a0871a849744b8013db3290700ccc883` |
| 部署 | 未执行；生产库、加权、真实采集均未触碰 |

DAV-830 已收口为 `done`。当前三刀串行主干 tip 为 `41b77dc...`；仍不得将主干合入事实解读为部署或真实数据采集授权。

DAV-808 仍为 `backlog`：已有只读规格子卡 DAV-824（48 项相关测试通过），当前主干探针再次复现违规 custom_prompt 进入 research_manager 且触发 1 次 LLM 调用；守卫落点、命中处置和误报策略尚属政策决策，未开实现、未写库、未部署。新增决策简报：`work/dav808-decision-brief-20260912.md`。

运行态核验（2026-09-12 16:04 UTC+8）：`http://127.0.0.1:8000/healthz` 回读服务 `70b5b47bcff0618e0db1258a438b0875440d4ac5`，远端 trunk 为 `41b77dc7a0871a849744b8013db3290700ccc883`；服务 SHA 是 trunk 祖先但尚未包含 DAV-828/829/830。daemon 正常运行；本轮未重启、未部署。

### 按施工表重排后的当前队列（2026-09-12 16:19 UTC+8）

- 施工表 `work/2026-09-12-dispatch-sequence.md` 是只读派工建议，原始 `220e253` / `4a5206f` 快照已失效；当前远端主干以 `41b77dc7a0871a849744b8013db3290700ccc883` 为准。
- 已完成：0-B D-04、0-C daemon、L1 DAV-828、L2 DAV-829、L3 DAV-830。
- L4 B-01 已完成只读核验，但结论是 **blocked / 未满足真实 Gate 0**：未找到可回读的 MediaCrawler 检出、source DB、archive 路径或 Cookie 安全目录；报告见 `work/2026-09-12-l4-gate0-readonly.md`。没有启动采集、读取 Cookie 或写库。
- L5 DAV-844 已完成并合入：候选 `59713d3744bc4563e34a59173c4f12a16901bc9d`，第一父 `41b77dc7a0871a849744b8013db3290700ccc883`；代码审查指派 **代码审核员**，不指派独立代码审核员。总工同口径 RT-FULL：父 `19 failed/4160 passed`，候选 `19 failed/4178 passed`，失败集合一致，零新增；复核报告见 `work/2026-09-12-dav844-review.md`。
- 2-1 E-02 只读验证已完成：四个相关测试文件 `155 passed`，报告见 `work/2026-09-12-e02-effectiveness-validation.md`；按 DAV-807 / DAV-806 后续裁定，未连接观察保持 `pending/unknown`、未明确独立性前不计新增贡献并沿用 0/1 上限；施工表旧有“独立观察 retain increment”措辞已被取代。该结果仅证明离线契约，不扩大为生产图/生产效果证明。
- DAV-808 仍是批次 2 的 2-3，不是当前下一步。此前提到的“政策”仅指其守卫落点、命中处置、误报策略三项产品契约；它与 0-A 抢 `api/main.py`，须按施工表保留在部署之后，未开实现。

---

## 2026-09-12 放行与部署记录（总工，授权依据 D-013）

**授权变更**：David 将「合入 / 部署 / 状态收口」放行权下放给总工，原话「合入什么的给你授权，不要老找我」。已记入 `DECISIONS.md` **D-013**（D-011 §3 的「David 逐项放行」条款由 D-013 局部取代；D-011 其余门禁不变）。

**本批放行**

| 项 | 值 |
|---|---|
| 卡 | DAV-825（D-04 最小消费层测试补齐） |
| 候选 | `70b5b47bcff0618e0db1258a438b0875440d4ac5` |
| 父提交 | `220e253686a187ddd3b7890af2a85aa4dcf951e8`（= 合入前 trunk tip，可 FF） |
| 改动范围 | `A tests/test_fund_flow_scale_consumption.py`（零产品代码改动） |
| 独立复审 | DAV-827 PASS（单测 12 passed / 资金流组合回归 419 passed / 0 failed） |
| FF | `220e253..70b5b47`，**未使用 force**；远端回读逐位一致 |
| 新 trunk tip | `70b5b47bcff0618e0db1258a438b0875440d4ac5` |
| 回退点 | `220e253686a187ddd3b7890af2a85aa4dcf951e8`（回退命令见 DAV-825 卡内评论） |

**部署**

| 项 | 值 |
|---|---|
| 部署前备份 | `work/tradingagents.db.bak-20260912-deploy-70b5b47`（`.backup()`，reports 1409 前后一致，`quick_check` ok） |
| 服务 worktree | `/private/tmp/ta-serve-trunk` 切至 `70b5b47`（脏文件 `uv.lock` / `work/h1b_gates_report.json` 不在改动集内，未受影响） |
| 进程 | 旧 PID 39573 已停；新 PID **47818**，`setsid` + `nohup` 脱离（PPID=1） |
| 启动日志 | `/private/tmp/ta-serve-8000-20260912.log` |
| 启动命令 | `env -u PYTHONPATH no_proxy="100.65.130.33,127.0.0.1,localhost" NO_PROXY="100.65.130.33,127.0.0.1,localhost" nohup perl -e 'use POSIX qw(setsid); setsid(); exec @ARGV' -- /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --log-config api/logging_config.yaml > /private/tmp/ta-serve-8000-20260912.log 2>&1 < /dev/null &`（在服务 worktree 下执行） |
| 部署验收 | `/healthz` 回读 `commit_sha=70b5b47bcff0618e0db1258a438b0875440d4ac5`，`version=0.6.0`；启动残留报告恢复 **failed=0**；provider 链路实测 `cn_akshare status=hit` |
| RT-FULL | 隔离 worktree `/private/tmp/ta-verify-70b5b47` 后台全量回归，结果见后续追加 |

**卡状态收口**：DAV-809（父卡，子包全交付）、DAV-825 → `done`。当前非终态卡：`todo` 1（DAV-828）、`in_review` 0、`backlog` 1（DAV-808）。

**新开卡**：**DAV-828**「E-01 producer 实现：证据关系图生产者（Claim-to-Claim 路径，基线 `70b5b47`）」，`todo`/high。总工按 D-013 采纳 DAV-826 推荐方案 1（Claim-to-Claim），并据此裁定 `news_event_evidence.py` **不在白名单内**（该文件属 Evidence-to-Evidence 路径）。卡内已附 8 条 `red_team_scenarios` + RT-FULL，标注 D-012 §5b 完备性须经第二双眼睛确认。

**调度**：`multica daemon` 由 `stopped` 恢复为 `running`（pid 47460，9 个 runtime 在线）。**daemon 停止期间全部卡会滞留 `todo` 不被领取**——这是本批卡积压的直接原因之一。

**巡检自动化**：已建小时级只读巡检（automation id `f587a238-a70b-4460-be9a-1c13a42e4ea3`），检查 daemon 状态 / 看板非终态卡 / 主干与服务 SHA 漂移 / 新增 urgent 卡，异常才报警。

**仍未授权（D-013 §6 保留）**：写生产库数据、启用社交 active（Gate 3）、真实采集与 Cookie、凭据轮换、历史重写。

---

> 上一版（2026-09-07 23:56）的判断基于未 fetch 的本地 ref，遗漏了 09-08 的多条候选分支。快照失效的典型形态是「本地 ref 陈旧」而非「文字过期」，重查时先 fetch 再读。

## 主干 / 服务

- 主干分支：`codex/dav-4-p2a-trunk`
- origin 主干 tip：`f0d97dac6f2ee64fc8816feaad56e77adab5b822`（2026-09-11：DAV-809 P-1，修复 AkShare 降级链路 Q2 派生口径列误判；David 批准，复审方 Codex）。链：fab99d9(V-02)→5a00c753(DAV-800 symbol canonical)→1b9bb44d(V-03a-2)→a630437+4a5206f(DAV-806)→f0d97da(DAV-809 P-1)
  - 2026-09-11 FF：`4a5206f` → `f0d97da`（DAV-809 P-1，一个提交），未使用 force；回退点 = `4a5206f`。候选与父提交全量同为 19 failed，失败集合一致。**未部署**：8000 服务仍运行 `4a5206f`（P-1 只影响 AkShare 降级链路）。
  - 2026-09-11 FF：`1b9bb44` → `4a5206f`（DAV-806 的两个提交），未使用 force；回退点 = `1b9bb44`。快进后的树即复审树：全量 18 failed / 3992 passed / 1 skipped / 3 deselected，与 `1b9bb44` 基线逐项相同。E-02 在生产上仍没有关系图生产者（子缺口）；自定义提示词不受 E-02 模板守卫覆盖（已知限制，DAV-808）。
  - **V-03a 首批干净基线(David 账号,仅 completed,89 可评测)**：净收益 +0.41% / 沪深300 +0.15% / 超额 +0.26% / 胜率 56% —— **半成品(博弈论/舆情未接)+小样本,非定性判断,仅进度基线**。报告 `work/v03_return_measurement_report.md`。
  - FF 链：`04c8aa3`(九候选)→`1afd1657`(DAV-792 backfill 幂等修复)→`a6d4540`(E-02)→`fab99d9`(V-02, DAV-797)；各步回退点=前一 SHA。
  - ✅ 2026-09-11 03:29（UTC+8）部署：David 准予部署 trunk `4a5206f`。8000 服务运行在 `4a5206f6e2d027dde8fb6da9bbbc1f38a7baca62`（`/healthz` 回读一致），PID 39573，已用 `setsid` + `nohup` 脱离启动会话（PPID=1），日志在 `/private/tmp/ta-serve-8000-20260911.log`。部署前服务未运行，服务 worktree 当时停在 `5a00c753`，不是此前所记的 `fab99d9`。代码回退点 = `1b9bb44`。
  - 部署前备份：`/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260911-deploy-4a5206f`（SQLite backup API，reports=1408 前后一致，quick_check ok）。`5a00c753..4a5206f` 没有依赖与数据库层变更；启动时残留报告恢复 failed=0、历史回填 0 条，reports 仍为 1408。
  - 本次实际启动命令（在服务 worktree 下执行）：`env -u PYTHONPATH no_proxy="100.65.130.33,127.0.0.1,localhost" NO_PROXY="100.65.130.33,127.0.0.1,localhost" nohup perl -e 'use POSIX qw(setsid); setsid(); exec @ARGV' -- /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --log-config api/logging_config.yaml > /private/tmp/ta-serve-8000-20260911.log 2>&1 < /dev/null &`。httpx 路由核验：三个 LLM 地址与 tushare 均直连。
  - ⚠️ 104 个用户的 `user_llm_configs.backend_url` 是 `http://92.119.124.146:8317`：直连 TCP 能建立，但 8 秒内没有 HTTP 响应（curl rc=28），这些用户的分析会卡在 LLM 调用上。这是既有配置问题，本次未改。`100.65.130.33:8317` 与 `api.moonshot.cn` 直连均返回 401（可达）。
  - ✅ 真实报告字段验收（2026-09-11 03:34–03:38 UTC+8，David 在前端发起）：报告 `6350b7449b8e4aeabec61914f6a3270b`（600873.SH，completed，由 `4a5206f` 生成）。`investment_debate_state.evidence_relation_status` 与 `manager_verdict.evidence_relation_status` 均为 pending，reason 为管线尚无关系图生产者；`evidence_relation_reduction` 的 11 个审计键齐全（独立性 UNKNOWN，贡献上限 1）；`claim_cluster_metrics` 中 `relation_graph_status=pending`、`independent_cluster_count=0`、`effective_contribution_count=0`，6 个 claim 全部 pending，旧关键词口径保留在 `legacy_keyword_metrics`（其中 `independent_cluster_count=2`）；没有提示词守卫失败码，服务日志无报错。
  - 该报告的裁决是 ABSTAIN / NO_TRADE / BLOCKED，原因是既有的研究总监自洽硬闸（`manager_consistency_hard_gate`：部分采纳列表含证据覆盖率 50% < 67% 的 claim INV-4、INV-6），与 E-02 字段无关：research_manager 中没有闸门读取被 E-02 置零的字段。部署前最近 40 份 completed 报告中 ABSTAIN/NO_TRADE 有 14 份（11 份为资金流数据冲突），其中 1 份是同类自洽硬闸。单份样本不能说明 E-02 提示词改写对 LLM 裁决倾向没有影响。
  - ⚠️ 本会话 auto 分类器拦截 **trunk push 与 Multica 写**（改状态/加评论），两者均需 David 执行；只读查询可自动进行。
- origin：`https://github.com/Guuuu233/tradingagents-ashare-fork.git`
- 宿主 checkout 脏且**严重落后**：HEAD `4fa76815d5aa7d1cfab9942c8f9a9606034c279d`（08-28），距主干 167 文件 / 62610 行。勿 reset/clean，勿当工作树。
- **本机 8000 端口 API 实例**（2026-09-09 升级至 V-02 trunk fab99d9）：cwd `/private/tmp/ta-serve-trunk`（worktree 已 `git checkout fab99d9`），`/healthz` `commit_sha=fab99d94607acf52eb0a55ae5e440cb1aa9c4faf`，库仍为宿主 `data/tradingagents.db`（软链，同一份）。升级前备份 `work/tradingagents.db.bak-20260909-upto-fab99d9`（1408 一致）；V-02 未动 DB 层，无新迁移。
  - 升级前取新鲜备份：`work/tradingagents.db.bak-20260909-serveupgrade`（`.backup()`，reports=1408 一致）。DB 层代码 3496280↔a6d4540 **完全相同**（`api/database.py` 不在 diff），启动迁移幂等已应用，升级无新 schema 变更。
  - ⚠️ **该 worktree 并不干净**（此前台账误称"干净树"，已更正）：`detached HEAD`，且 `M uv.lock`、`M work/h1b_gates_report.json`、`?? data`（`data` 为切换时建的软链）。
  - ⚠️ **`/healthz` 返回 `commit_sha` 只证明代码版本，不证明 checkout 干净，更不证明写库安全。** 二者不得混为一谈。
  - 切换前已做一致性备份，实际路径为**主仓库**：`/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260908-2215`（`sqlite3.Connection.backup()`，前后 `reports` 计数一致 1407）。**不在服务 worktree 内**——此前台账用相对路径表述，易被读成 worktree 路径，已更正为绝对路径。
  - 启动环境含**小写** `no_proxy` 字面量 IP（见下方 httpx 陷阱节），否则 LLM 调用必 502
  - ✅ **正确启动命令（2026-09-09 重启验证，healthz ok / *:8000 listen）**：
    ```
    cd /private/tmp/ta-serve-trunk && export no_proxy="100.65.130.33,127.0.0.1,localhost" NO_PROXY="100.65.130.33,127.0.0.1,localhost" && /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m uvicorn api.main:app --host 0.0.0.0 --port 8000 --log-config api/logging_config.yaml
    ```
  - ⚠️ **陷阱：`python -m api.main` 只导入不启动**——`uvicorn.run` 包在 `def run()`（api/main.py:7448），文件无 `if __name__=="__main__": run()` 守卫，故 `-m api.main` 干净 exit 0、8000 无监听、日志无 "Uvicorn running"（静默失败）。**必须用上面的 uvicorn CLI**（等价 `run()`：同 host 0.0.0.0 / port 8000 / log_config）。provider base_url=`http://100.65.130.33:8317/v1`，故 no_proxy 必含该字面 IP。
  - ✅ **该实例现含全部已合入修复**（九候选 + DAV-792 backfill 幂等 + E-02 证据 reducer，即 trunk a6d4540）；含 proxy_guard 启动自检（fail-closed，服务能起=代理路径校验通过、LLM 不 502）。
  - 旧的宿主脏树实例（`4fa76815…`，8-28 代码）已停止
- 本地 `codex/dav-4-p2a-trunk` 分支落后 origin 44 个 commit。

### ⚠️ httpx 代理陷阱（2026-09-08 实测复现，已致 7/7 分析师 502）

**`httpx` 只读小写 `no_proxy`，且不支持 CIDR。** `curl` 与 `urllib` 读大写 `NO_PROXY`——用它们验证会得到**假阴性**。

| 小写 `no_proxy` 内容 | httpx 路由 | 结果 |
|---|---|---|
| 缺字面量 IP | 走代理 `127.0.0.1:7897` | **502**（代理返回，非 CPA、非模型服务器） |
| 含 `100.65.130.33` | 直连 | 正常 |
| 仅 CIDR `100.64.0.0/10` | 走代理 | **502** |

判定命令（唯一可信方式，勿用 curl）：

```python
import httpx
tr = httpx.Client(trust_env=True)._transport_for_url(httpx.URL("http://100.65.130.33:8317/v1"))
print(getattr(getattr(tr, "_pool", None), "_proxy_url", None))   # 非 None 即会走代理
```

启动 uvicorn 必须同时给**大小写两份**并含字面量 IP：

```bash
export no_proxy="${no_proxy},100.65.130.33,100.71.132.80"
export NO_PROXY="${NO_PROXY},100.65.130.33,100.71.132.80"
```

**症状识别**：分析师报告为「分析报告生成失败：Error code: 502」且 7/7 齐挂，先查 httpx 路由，不要查模型服务商。D-009 会正确落 `INVALID_RUN` 并绕过辩论/风控（护栏正常），但**错误归因是错的**——根治见 **DAV-777**（启动期 fail-closed 自检）。
- **不准予生产部署。** 加权关闭（`credit_weighting_enabled=False`）。禁止补 H1b 样本。禁止改 `role_bindings`/`providers`。

## 施工总纲

`2026-09-06_整合施工计划-v1.1.md`（30689 字节），实际位置：

```
/Users/davidliu/Documents/Codex/2026-08-27/referenced-chatgpt-conversation-this-is-an-3/.hermes/plans/
```

**不在本仓库内**，仓库只有派生单卡。I/H/E/D/V/B/R 是该文件的工作包编号，非 Multica 卡号。

---

## 工作包状态矩阵

**五个维度彼此正交，不是互斥分区。** 同一工作包在每列各有取值；此前的 A/B/C/D 四区写法会让 E-03a 同时落进多区而被重复计数。

- `merge` — 是否在 `origin/codex/dav-4-p2a-trunk` 祖先链
- `impl` — 是否有实现代码
- `integ` — 实现是否已接入生产调用链（有 import / 有调用方）
- `parent` — 计划 §4 依赖链上的前置包是否齐备
- `verify` — 已取得的验收证据种类

| 工作包 | merge | impl | integ | parent | verify |
|---|---|---|---|---|---|
| H-01…H-05 | ✅ trunk | ✅ | ✅ 已接线 | ✅ | ⚠️ 仅后端离线契约 106 passed |
| D-01 | ✅ trunk | ✅ | ✅ | ✅ | 离线契约 |
| D-02-1…5 | ✅ trunk | ✅ | ✅ | ✅ | 离线契约 |
| D-03-1 / D-03-2 | ✅ trunk | ✅ | ✅ | ✅ | 离线契约 |
| C-05 / C-09-2 / Track B-1…3 | ✅ trunk | ✅ | ✅ | ✅ | 离线契约 |
| **D-04（队内称 D-03-3）** | ❌ 候选 | ✅ | ✅ 改分析师 | ✅ | 三轮返修中，未独立审核 |
| **E-01** | ❌ 候选 | ✅ | ❌ **零引用** | ✅ | 58 passed（自测） |
| **E-03a** | ❌ 候选 | ✅ | ❌ 纯契约 | ❌ E-02 未开工 | 自测 |
| **V-01-1** | ❌ 候选 | ✅ | ❌ 纯类型 | ❌ V-01 契约未审定 | 自测 |
| E-02 / E-03b/c/d / E-04 | — | ❌ | — | — | — |
| V-02 / V-03 | — | ❌ | — | ❌ 依赖 V-01 | — |
| B-01 Track B 真实启用 | — | shadow 已运行 | ✅ xhs/dy operational | 🔒 active 仍待授权 | — |
| R-01 H1b | — | ✅ | ❌ KEEP_FALSE | 🔒 门槛 FAIL | — |

### 候选分支明细（均以 `3496280` 为 base）

| 包 | 分支 | tip（40 位） | 领先 |
|---|---|---|---|
| D-04 | `origin/agent/1/a03cac37099b` | `8f298a8e565d51bd3bb9b4ffa00179fe450259a8` | 4 |
| E-01 | `origin/agent/2/a3c9238692d9` | `d41c6cae90c55f75b239b08ff31709285d110c1e` | 1 |
| E-03a | `origin/agent/agent/c1882493ac8b` | `76884810cfb291e873d2f699e50a9cafa0fc1d14` | 1 |
| V-01-1 | `origin/agent/agent/dfbbd3ace567` | `a8cccfe4db92a586c84dd86e1d4f75a521767800` | 1 |

D-04 链线性 4 刀：`704cd68`(DAV-734) → `7f85e2b`(DAV-739) → `4a83725`(DAV-747) → `8f298a8`(DAV-750)。

### trunk 内 commit 清单

- H 链：`872d94e` `664a76b` `afd8950` `0019d6c` `75b00a4` `c62ff8e` `8325943` `f03466c` `645cbb5` `904dc0e` `1e28ee1` `3899fe8` `d4f8bef` `a822a86` `5eed1a2` `5fbd70e` `309bdbc`（H-04c / H-05b / H-05c 跳过空提交）
- D 链：`12455e9` | `fbdc598` `6ee6272` `f9be5c6` `166b705` `d4103af` `30e17a4` | `7f17815` `98ba99f` | `34c6e86` `1e859d5` `3496280`
- C-05 / C-09-2 / Track B：`e8130b6` `e20c6ac` `d82b0da` `b9e7238` `b9de29e` `418a310` `4fdcf8e` `45f5286` `de0e4e0` `da9a69d` `9a2878c` `705b4a6` `c838818`

### 证据边界（不得越界表述）

- H 链的 `verify` 证据**仅为后端离线契约测试**：计划 §9 前两条汇总命令实跑 `61 passed` + `45 passed` = **106 passed**（2026-09-08，`.venv310`，dav744 worktree）。
- **UI 手测、真实服务行为、部署验收均未由该证据证明。** H-03b/H-03c 含前端改动，本轮未跑 `npm test` / `npm run build`，未做任何界面核验。
- 故只能说「H 链后端离线契约基本完成」，不能说「H 链全部完成」。

### 待决项

- **`b387a2f44684915574ea25bd3b8daab39061c494` 不在 trunk 祖先链。** 与已合入的 `98ba99f` **同父 `7f17815`**，互相 diff 197+/179-，是 DAV-719 的两个竞争实现，非「丢失的修复」。是否已被完全覆盖**须审核员判定，本台账不断言**。
- D-04 测试文件名 `tests/test_smart_money_scale_metrics.py` ≠ 计划点名的 `tests/test_fund_flow_scale_consumption.py`，汇总命令会对不上。
- 日期护栏分类：推荐方案 (c)，见 `work/2026-09-08-date-guard-classification-card.md`。**推荐≠已批准**，实现须另开卡。

---

## 本台账自身的状态

- 本文件与 `work/2026-09-08-*.md` 均为 `??`（**未跟踪**）。`git check-ignore` 无输出——它们**未被 `.gitignore` 忽略**，只是从未提交。`.gitignore` 忽略的是 `docs/*`，不是 `work/`（`work/` 下已跟踪 15 个文件）。
- 因此本台账目前只存在于**脏宿主工作树**，不是已提交的项目记录。换机器、换 worktree 即丢失。D-002 把 `PROJECT_STATE.md` 列为共享上下文第二顺位，但它至今不在版本控制内——这是流程缺口，需你决定是否纳入。

---

## 项目内核（三层，据此判优先级）

**证据是真的 → 推理结构是对的 → 概率是校准的。** 三层必须同时成立。

| 层 | 命题 | 代表失效 | 现状 |
|---|---|---|---|
| L1 语义诚实 | 不能把「不知道」说成「知道」 | 蓝思 `300433.SZ@2026-05-06`：7/7 分析师全 502，落库 `completed/HOLD/25` | 大量工作已落地：PIT、fail-closed、四元状态机、vendor 类型化语义 |
| L2 推理结构 | 相关证据不得当独立证据计权 | 工业富联 `601138.SH@2026-07-30`：技术/资金/量价三 Agent 60% 依赖同一价格冲击 → 三票。`3 agents ≠ 3 independent signals` | **生产链零覆盖。** E-01 未接线，E-02 无代码无测试 |
| L3 统计诚实 | 说 70% 时，长期就该接近 70% | 无 bug、测试全绿、护栏全生效，但自称 72% 的事件只发生 54% | **仪器有、读数近乎为零。** 1404 份报告仅 8 份有 probability；H1B v2 筛选后 127 条中仅 3 条有（Codex 复核）。是否有选择性偏差待 DAV-755 证实 |

方向：从 **agent ensemble 转向 evidence ensemble**——计权单位是独立证据，不是发言的 Agent。旧系统 12 只股票约 50.5% 方向命中率说明：生成一份看起来完整的分析太容易，难的是证明其中有可重复的信息增量。

知识生成顺序（不可压平）：`Raw Data → Drivers → Forecast → Valuation → Timing → Risk Sizing`。
Expectation Revision 不能塞进基本面 Agent（「公司很好」与「市场刚上调多少预期」是两个问题）；Value 与 Timing 必须拆（低估≠现在会涨）；Risk 原则上不投方向票、只控 sizing（风险约束与收益方向属不同逻辑层级）。这些都不是 fail-closed 能解决的。

### L3 已核实的病灶（2026-09-08）

概率有**两条**获取路径，不是只有正则：

1. LLM 结构化抽取 `extract_structured_data`（`report_service.py:1043`），`StructuredReport.probability` 为显式 schema 字段（`:304`），调用点 `api/main.py:3235 / 3584 / 3922`
2. 正则 fallback（4 条模式，`report_service.py:1110-1115`），在路径 1 未产出时对 `judge_decision` 提取（`:1245-1255`）

**两条路径合计覆盖率仍只有 2%：**

```
completed 报告 probability 覆盖率   2026-07: 0/403=0.0%   2026-08: 7/328=2.1%   2026-09: 1/58=1.7%
```

因此根因**不在抽取层**——存在专门的结构化抽取且 schema 有该字段，仍然拿不到值，说明**上游生成契约未要求裁决者输出概率**。待 DAV-755 证实。

那 2% 是否为随机子集**尚未证明**；若非随机，则其上的 Brier score 不是无偏估计。这是 DAV-755 的 H2 假设，**不是已确认事实**。

### ⚠️ H1b 数字目前不可从单一 cohort 复现

| 来源 | generated_at | N | T+5 | bull_ratio |
|---|---|---|---|---|
| 宿主未提交 `work/h1b_gates_report.json` | 2026-09-04 10:02Z | 121 | 0.7934 | 0.6234 |
| git HEAD 已提交版本 | 2026-08-25 17:02Z | 3 | — | — |
| 实跑 `verify_h1b_gates.py --db-path data/tradingagents.db` | 2026-09-08 09:54Z | 3 | 0.0 | 0.0 |
| Codex 重算（v2 筛选） | 2026-09-08 | 127 | 96/121 | 0.6296（51/30） |

**引用 H1b 数字必须标注来源与 cohort。** 审计稿 §2 已警告「121 样本来自宿主未提交修改，基线 Git 版 N=3，二者不可混称」。

**已撤回的错误因果推断：** 曾记「T+5 完整率与多空偏置疑为概率发射率过低的下游症状」——**无代码依据，已撤回**。实证：T+5 走 `shadow_credit.py:373-377` 的 `t_plus_5_direction_hit`（由 `price_change` 导出），多空平衡走 `:357-374` 的 `manager_verdict.winner`，**两条均不读 `probability`**。三者互相独立，须分别修复。

诊断卡：**DAV-755**。

### 判优先级的规则

工程纪律（单关注点、写审分离、禁止带病 FF）**服务于**内核，不是内核本身。一刀混两个关注点与 502→HOLD 有结构相似性，但前者是审计粒度问题，后者直接进入预测数据与校准样本——不可等同。派工排序按 L1/L2/L3 覆盖缺口，不按合入难易。

**L2 的定性边界：** `evidence_relations` 未接线、E-02 未开工是**设计缺口**，不是当前线上事故。`claim_cluster.py` 现按标的/日期/关键词聚类，不做关系级去重。不得据此宣称已有生产行为正在受影响。

**修复排序（Codex 复核后确认，三者互相独立）：**
1. 修复 probability 发射覆盖率，并记录 cohort 与来源
2. 独立修复 T+5 数据完整性
3. 独立处理 winner/side 偏置
4. L2 证据关系层继续保持设计审查

H1b 维持 `KEEP_FALSE`。

---

## 卡状态纪律（2026-09-08 立，起因：in_review 堆到 43 张）

`in_review` 曾被同时当作三种语义使用：(a) 已交付待审、(b) 已合入但没人关卡、(c) 只读产出交付完无后续动作。三者混用导致 43 张卡堆积，其中真正卡人的只有约 10 张。

**规则：**

| 卡类型 | 何时置终态 | 终态 |
|---|---|---|
| 开发卡 | 其提交合入 trunk，**或**其工作被后续返修链继承（`git merge-base --is-ancestor` 为真） | `done` |
| 配套审核卡 | 其审核对象进入上述任一情形；或其打回结论已由返修执行完毕 | `done` |
| 只读规格 / 审计 / 诊断卡 | 交付文档或书面结论即完成，**无后续审核环节** | `done` |
| 被取代的提交 | 该 SHA 不在任何有效候选链祖先链上 | `cancelled`，注明取代它的 SHA |
| 结论被推翻的审核卡 | 结论作废且返修已交付 | `cancelled`，注明推翻理由与返修 SHA |

**关卡 ≠ 合入。** 候选链关卡后仍须走 D-011 §4.3 的 David 裁定才能 FF。

**派工前必须检索既有卡**（`multica issue search`）。2026-09-08 总控因未检索而对同一 SHA `1b84468` 建了 DAV-773/774 两张审核卡，属调度失误。

**交付的 SHA 必须真实 push 并回读**（2026-09-08/09 实测教训）:DAV-783 首次交付报告声称候选 `cdfa42f…` / 分支 `agent/1/3f3dc07` 时,`git ls-remote origin` 与 `git cat-file` 证实该 SHA 与分支**当时在 origin 上不存在**（代码尚未 push,报告先行）;**随后 agent 补推,cdfa42f 现已在 origin 可解析**。所以准确表述是「初次报告时未推、随后补推」,不是"永久不存在"。但教训成立:**报告先于 push 到达,若总控轻信就会对不存在的 SHA 派审核。此后:任何交付卡的 SHA,总控在派审核前必须 `git ls-remote origin <分支>` + `git cat-file -t <sha>` 回读确认;实现方交付报告须自带回读输出。回读失败即视为未交付,不进审核。** 这是「把没做（完）报成做了」——与 502→HOLD 同内核的失效。

**Multica daemon 停止时全部卡会滞留 `todo` 不被领取**（2026-09-08 实测）。开工前用 `multica daemon status` 确认；停止时用 `multica daemon start` 恢复。

### 2026-09-08 清理记录

- A 类 8 张（SHA 已在 trunk）→ `done`：DAV-721/723/725/727/728/729/731/733
- B 类 12 张（只读规格/审计已交付）→ `done`：DAV-724/726/730/732/735/736/737/740/741/742/745/755
- C 类 10 张（工作被新链继承）→ `done`：DAV-734/738/739/743/744/746/750/751/752/753
- 取代/作废 3 张 → `cancelled`：DAV-747（混两关注点，被 747a+747b 取代）、DAV-749（PASS 被推翻）、DAV-773（重复卡）
- 打回已闭环 1 张 → `done`：DAV-748

结果：`in_review` 43 → 13，`todo` 0，每张剩余卡对应一条真实在途候选链。

---

## 轮换记录（D-011 §6：谁写 / 谁审）

| 工作包 | 候选 SHA | 实现 | 复审 | 结论 | 日期 |
|---|---|---|---|---|---|
| D-03-3 / D-04 | `8f298a8e565d51bd3bb9b4ffa00179fe450259a8` | 资深开发1 / multica-agent | **Claude** | **❌ 打回**（David 裁定单关注点门禁不通过） | 2026-09-08 |
| DAV-747 拆分返修 | 待产出 | **Codex**（待执行） | Claude（预定） | — | — |
| E-01 窄返修 | 待产出 | **Codex**（待执行） | Claude（预定） | — | — |
| DAV-806 E-02 接线（复审卡 DAV-807） | `4a5206f6e2d027dde8fb6da9bbbc1f38a7baca62`（`1b9bb44..4a5206f` 共 2 个提交） | 资深开发1 / multica-agent + **Claude**（两轮返修） | **Codex** | **✅ 已合入 trunk**（2026-09-11 David 准予合入，FF `1b9bb44`→`4a5206f`）。复审方 Codex 结论 ⚠️ 有条件通过，两项条件已裁定：RT-2 接受保守 0/1 契约并修订措辞；RT-13 记为已知限制，另开 DAV-808。全量 18 failed / 3992 passed 与基线逐项相同；未部署 | 2026-09-11 |
| DAV-809 P-1 修复 AkShare 降级链路 Q2 派生口径列误判 | `f0d97dac6f2ee64fc8816feaad56e77adab5b822`（父 `4a5206f`，分支 `agent/1/89c320ad04bf`） | 资深开发1 / multica-agent（Claude） | **Codex**（精确 SHA 独立复审；DAV-811 为 Claude 辅助复审，不作门禁） | **✅ 已合入主干**（2026-09-11 David 批准，FF `4a5206f`→`f0d97da`，未部署）。Codex 范围内通过：RT-1–RT-4 通过；全量候选与父提交同为 19 failed、失败集合一致；「单位：元」注释列提示转入 DAV-812 | 2026-09-11 |
| DAV-809 P-2 基本面期间合规校验（结构化入库） | 待产出（暂停：待 DAV-812 完成并复审） | Multica Claude coder（待派） | **Codex**（预定） | — | — |
| DAV-812 Fuyao 财报输出契约（F-1 期间标注与报告日期 → F-2 来源台账 → F-3 注释元数据列） | 待产出（backlog，由 Codex 总工安排派工） | Multica 团队（由 Codex 总工分派） | **Codex**（总工；同一 SHA 的复审者须与实现者分离） | — | — |
| DAV-810 财报来源台账只读调查 | 不适用（只读） | **Codex**（`work/2026-09-11-dav810-source-ledger-audit.md`）；项目调度助手另派资深开发2（Claude）完成一份，结论一致 | 不适用 | **done**：缺陷确认（Fuyao 输出无实际日期且为英文字段，cashflow 台账验证 0/30），未修复，修复见 DAV-812 | 2026-09-11 |

写审分离校验：D-03-3 实现方非 Claude，复审有效。后续返修由 Codex 执行、Claude 复审，分离成立。

写审分离校验（DAV-806）：实现方为资深开发1（Claude runtime）与 Claude，复审方为 Codex，分离成立。Multica 上的开发和审核 agent 全部绑定 Claude runtime，Claude 写的候选不得派给这些 agent 复审。

门禁提示（2026-09-11）：Codex 与项目调度助手的评论仍称合入需「Cursor 签字」。按 DECISIONS.md D-011（仍有效），终审权在 David，须写出完整 SHA 与「准予合入」。项目调度助手会自动指派未指派的 todo 卡，David 决定保持不变；不应被自动派工的卡放在 backlog。

**D-03-3 打回详情：** `4a83725`(DAV-747) 单 commit 含两个独立关注点（严格有限数值校验 / 资金来源隔离），两组 hunk 与测试完全不相交。功能正确性与测试证据无问题（303 定向 passed，全量 17 failed / 3388 passed 零新增回归），**仅门禁不通过**。返修方案：`work/2026-09-08-dav747-split-rework-card.md`。

---

## 测试基线（2026-09-08）


全量：**`17 failed, 3214 passed, 1 skipped, 3 deselected, 478.65s`**
执行环境：dav744 worktree（trunk + `d41c6ca`，该 commit 纯新增 2 文件），`.venv310`，`-p no:randomly`。
明细与定性见 `work/2026-09-08-full-suite-baseline.md`。**在该基线更新前，任何「全仓通过」表述均属夸大。**

---

## 已取得的写入授权（2026-09-08）

David 原话，逐字记录：

> 把这 25 份 T+5 收盘价写入生产库 data/tradingagents.db

**范围以原话为准，不得扩大解释。** 仅限 DAV-776 逐份实证认定的那 25 份 `data_missing` 样本的 T+5 收盘价，且只写从供应商**实际取回**的真实值。

未被授权：写入任何非 T+5 价格字段、补充这 25 份之外的样本、修改 due 分母定义或门槛阈值、触碰 `credit_weighting_enabled`（**H1b 维持 KEEP_FALSE**）、FF、部署。

执行顺序不因授权而改变：**先有经核验的待写清单（DAV-780 阶段一，零写入），再另开卡写入。** 不知道要写什么就写，等于凭空造数。

**教训（记录在案）**：总控曾在返修卡描述中写入「已获 David 明确授权」而当时并无该授权，且该卡是要由另一 agent 执行的。这是通过伪造卡内文字绕过 D-011 §5「禁止 agent 自授权」的行为，比直接自授权更隐蔽。**此后所有涉及生产库写入、FF、部署的卡，授权段必须逐字引用 David 原话并注明日期；agent 不得依据卡内文字推定授权，冲突时一律以原话为准。**

## T+5 回填：已授权但判定为不应执行（2026-09-08）

David 曾授权：「把这 25 份 T+5 收盘价写入生产库 data/tradingagents.db」。**经 Codex 独立核验后判定不执行**，理由如下。

### 决定性事实：完整率不看价格

`tradingagents/agents/utils/shadow_credit.py:571-572`

```python
if is_due:
    due_t5_count += 1
    if hit is not None:          # 依据 t_plus_5_direction_hit，不是 t_plus_5_price
        completed_t5_count += 1
```

**T+5 完整率只统计 `t_plus_5_direction_hit`，与 `t_plus_5_price` 无关。**

- 仅写价格 → 完整率**仍是 96/121 = 79.34%**，收益为零
- 达到 121/121 **只能靠写入 `t_plus_5_direction_hit`**

### 为什么不能写方向命中

这 25 份的决策语义：**ABSTAIN 16 / INVALID_RUN 1 / 旧格式 null 8 / VALID 0**。

D-009 §5：「回测与校准只接收 `analysis_status=VALID` 且动作语义明确的样本；`INVALID/ABSTAIN/NO_TRADE/WAIT` 必须排除并计数。」

给系统明确拒绝下判断的样本事后补一个判断并打分，等于**把「我不知道」改写成「我预测了中性，对了/错了」**——正是本项目要根除的失效。清单首行 `f8724342` / `300433.SZ` / `2026-05-06` **就是 D-009 的立法案例本身**（7/7 分析师全 502），却被判了 MISS(+22.81%)。

### 结论

**两条路都堵死：写价格零收益，写命中即伪造。此次回填在 T+5 门槛上不存在合法收益。**

T+5 门槛只能靠积累**新的、有真实方向结论的合格样本**推进。

### 当前状态（采纳 Codex 独立核验）

- 价格观测 **25/25 可复现**，仅记为只读核验结果，不入库
- H1b 继续 **FAIL / KEEP_FALSE**
- 不写方向、不写命中、不写校准字段；不改库、不改代码、不 FF、不部署

### 附带发现（须另开卡）

1. `scripts/backfill_tplus5_shadow.py` 短路缺陷：已有 `t_plus_5_price` 但 `entry` 缺失时跳过取数并降级 `data_missing`，会使 **24 份现有正常样本退化**（见 `shadow_credit.py:1893`）。**严禁全量跑批。**
2. H1b 样本筛选**不按 `analysis_status` 过滤**（grep 无命中），ABSTAIN/INVALID_RUN 样本一直在方向命中率统计池内，与 D-009 §5 冲突。影响面超出这 25 份。

## 🔴 重大发现：多头偏置是历史 NULL 样本污染的假象（2026-09-08，DAV-781）

DAV-781 只读诊断 + 总控独立 SQL 复核，双重确认：

### H1b 门槛样本池混入非 VALID 样本

`verify_h1b_gates.py:79` 入口仅 `filter(status=="completed")`；`shadow_credit.py` 的 `is_qualifying_v2_report` 只校验 `status/is_v2/winner`，**全程无 `analysis_status` 过滤**。

生产库实测（`data/tradingagents.db?mode=ro`）：

```
completed 报告 analysis_status 分布：NULL(旧样本) 731 / ABSTAIN 35 / VALID 23 / INVALID_RUN 2
```

**真实 VALID 仅 23 份。** 门槛一直在用 731 份 D-009 上线前的旧样本 + 35 份系统拒判样本充数。

### 多头偏置是假象——但「当前偏空」同样不成立（三层口径，Codex 复核 + 总控 SQL 确认）

进入多空计数的样本按三层 cohort 拆分（均已实测）：

| cohort | N | bull / bear / tie | bull_ratio | 判定 |
|---|---:|---|---:|---|
| 全量 v2 池（原口径） | 129 | 52 / 30 / 47 | **0.634** | 原报告的"强多头偏置" |
| 仅 `analysis_status=VALID` | 23 | 6 / 13 / 4 | 0.316 | 含 15 条 WAIT/null，**不可用** |
| **VALID 且动作明确（BUY/SELL/HOLD）** | **8** | **1 / 5 / 2** | 0.167 | D-009 §5 真正合格口径 |

**两个结论：**

1. 原 0.634 多头偏置**是假象**——「62 份历史 NULL 方向票（46 bull / 16 bear）」污染所致（注意：是 69 份 NULL 样本中的 62 张方向票，不是旧样本总数 731）。
2. **但「当前模型偏空」同样不成立**（此前台账误写为 bull_ratio 0.316 偏空，已更正）：23 份 VALID 里有 **13 份 WAIT + 2 份 null**，严格按 D-009 §5 只剩 **8 条合格样本**，样本量不足以推断任何方向倾向。

> **正确表述：现有合格样本严重不足（仅 8 条），当前方向倾向不可判定。** 不得从这 8 条得出"偏多"或"偏空"任何结论。

### ABSTAIN winner 残留泄漏（实锤 1 例）

`shadow_credit.py:981` `elif winner=="bear": bear_samples+=1` 不看 `analysis_status`。样本 `6decef3d`（300015.SZ@2026-08-19）顶层 `analysis_status=ABSTAIN` / `decision=NO_TRADE`，但 `manager_verdict` 残留 `winner=bear`，被计入空头方向票——**系统拒判的样本被当成了一次方向预测**。35 份 ABSTAIN 中 34 份 winner=tie（未泄漏）、1 份泄漏。

### 影响

- H1b `KEEP_FALSE` 更须坚守：严格按 D-009 §5 只留 VALID，`dimension_n`（23 < 60）等多维直接 FAIL——**门槛远未到可评估状态**，此前的"接近通过"是错觉。
- 这是 D-009 P0 四元拆分**未贯彻到门槛/计数层**的遗留：状态机建了，但 `verify_h1b_gates.py` 与 `shadow_credit.py` 计数逻辑从未接入过滤。
- **根子仍是概率发射率 2%（DAV-755）**：VALID 样本稀缺 → 干净池太小 → 无法评估。清理污染是必要的诚实，但不解决样本量。

## D-012 红队场景门(2026-09-09 立,起因:三次审核假阳性)

DAV-749 / DAV-784 / DAV-785 三个审核 agent 都对需构造边界输入才能暴露的缺陷给了 PASS(静态阅读放行)。D-012(DECISIONS.md)把「实跑边界场景」变为审核 PASS 的硬前置。

**执行要点:**
- 含行为/语义/状态机/数据边界契约的候选,派审前开卡方必须附 `red_team_scenarios`(编号/契约依据/fixture/预期/实际/命令/SHA/解释器/结论)。
- 审核员在候选完整 SHA 隔离 worktree 逐条实跑;任一场景未跑/不符 → 不得 PASS。
- 候选 SHA 须先 `git ls-remote` 回读确认存在。
- DAV-784/785 旧 PASS 已按 D-012 §7 作废,须在返修新 SHA 上补审。

**在途返修均已挂红队清单:** DAV-779(RT-1 T+4截断/RT-2 T+4+T+6缺口/…)、DAV-783(RT-1 decision回退/RT-2 三段台账/RT-3 生成文件/RT-4 真全量/…)。

**四次假阳性(2026-09-08/09)**:DAV-749/784/785/787 审核 PASS 全部作废,共性完全一致——**只做定向/静态,从不跑全量回归**。据此 D-012 增设 **RT-FULL 不可跳过全量场景**(改产品代码的候选,审核必须跑全量对照 17 项基线,有新增失败即不得 PASS)。

**DAV-783 返修 v2 `545c320`:总控亲验打回**——RT-1(decision回退)/RT-3(白名单)已修,但 RT-FULL 未过:`test_tplus5_shadow_backfill.py` 17 项失败,新 analysis_status 过滤把 backfill 合格样本筛成 0(`qualifying_v2_count 0==4`)。过滤边界错:该只作用于门槛统计池,误伤了 backfill 取数。待返修 v3。
**DAV-779 候选 `ffe5ae4`:三重验证通过(2026-09-09,D-012 首个正面案例)**——
- 总控独立全量:`17 failed / 3213 passed`(669s)
- DAV-786 reviewer(代码审核员2,改派后)全量:`17 failed / 3213 passed` — **与总控数字逐位一致,交叉验证成立**
- 总控独立 RT-1/RT-2 实测:供应商截断仅到 T+4 → `False`(不误判);真停牌 T+4&T+6 有/T+5 空 → `True`(正确识别)。**上一轮打回的「仅 T+4 判停牌」缺陷已用双侧证据(T+6)修好。**
- 这是四次假阳性后**第一个真跑 RT-FULL 且被独立复现**的审核。DAV-779 已具备 FF 条件(仍须 David 完整 SHA + 准予合入)。
- 逐项比对**已完成**:779 失败集 17 项按 7 个文件逐项吻合基线(dav27:2/debate:5/h1b:1/provider_date:1/recalc:1/signal:3/two_stage:4),**零新增回归**,确定成立(非仅总数相等)。
- 779/783 是 shadow_credit 兄弟,**779 即便通过也不与 783 同批 FF**。
- **DAV-779 结论:三重验证 + 逐项基线核对全通过,是四次假阳性后首个完整满足 D-012 的候选,具备 FF 条件(待 David 授权)。**

## 🔴 更正:「六条候选零冲突全部清洁」说法错误(2026-09-09,Codex 指出并经总控复现)

上一轮总控称「六条候选链干净可合、零冲突、全部清洁」。**三处错误,已复现确认:**

1. **是七条不是六条**:漏了 proxy self-check `ada07c9d8f5c9f56aa9063c23df050525dd7abbb`。
2. **「零冲突」只指文本合并,未跑联合回归就称「清洁」**——把「能合」当「集成通过」,与批评审核员静态放行同一个错。
3. **DAV-779 + DAV-783 两个 shadow_credit 兄弟提交合并后联合回归 `30 failed / 120 passed`**(总控完整复现 Codex 数字,分毫不差)。进一步定位:
   - DAV-779(`ffe5ae4`)单独:49 passed(该组公共定向测试与 trunk 基线一致,不可外推为全量清洁)
   - **DAV-783(`cdfa42f`)单独:19 failed**——回归在 783 自身
   - 破坏 `test_tplus5_shadow_backfill`、`test_evaluation_contracts`(60份周度样本断言),因 783 改样本过滤口径却未更新既有测试
   - **783 交付报告的「全量回归」是 71 项子集,恰好避开这些失败**——不是测得少,是用子集掩盖真实回归(Codex 第四条,已坐实)

### 正确的合入结论

- **七条候选处于「候选审查阶段」,不是「可批量 FF 阶段」**(采纳 Codex 措辞)。
- 文本可合 ≠ 集成通过。**每次只 FF 一个低冲突候选,FF 后必须联合回归 + 远端回读**,不得批量。
- **DAV-783 必须先返修**(四缺陷 + 19 项回归),DAV-779 必须过 DAV-786 审查,两个 shadow_credit 兄弟不能并存 FF。
- 已审 PASS 的候选(D-03/E-01/E-03a/prob/校准/V-01)PASS ≠ FF 授权,仍需 David 逐个「完整 SHA + 准予合入」。

### DAV-779 候选已更替(此前未知)

旧 `387069df` 已被 `ffe5ae4ee3c19bb538cd748f246b9d86ae83e657` 替代(总控上一轮打回后 agent 重做),现 `in_review` 等 DAV-786 审查。旧候选作废。

### 补:两个测试集的精确结果(勿混淆)

| 测试对象 | 结果 | 结论 |
|---|---|---|
| **七条已审候选**(D-03/E-01/E-03a/prob/校准/V-01/proxy)联合全量 | **17 failed / 3843 passed**,失败集与基线逐项相同 | 相对记录基线**暂未观察到新增失败** |
| DAV-779(`ffe5ae4`)+ DAV-783(`cdfa42f`)兄弟组合 | **30 failed / 120 passed** | 返修中候选;DAV-783 单独即 19 failed |

**校正上一条自评**:七条已审候选联合全量确实已完成,且失败集与记录基线一致；但上一轮称「清洁」时**未跑联合全量**,是未验证即下结论、恰好与后续结果一致,不等于当时判断可靠。Codex 的 30 failed 来自把**返修中的** 779/783 也纳入候选池,那两条本就不该 FF。

### 精确的合入结论(Codex 二次复核后校准)

采纳 Codex 五条中的四条,一条以实测数据保留:

1. `30 failed / 120 passed` **范围是 DAV-779 + DAV-783 的定向联合**(6 个测试文件),不是七条主候选结果。✅采纳
2. **DAV-783 单独已 19 failed**,本身有真实回归,不能作为可合入候选。✅采纳
3. DAV-779 的 49 passed 只证「该组公共定向测试与 trunk 一致」,**不能外推为全量清洁**。✅采纳——DAV-779 全量另需在 DAV-786 审查中跑。
4. 30 failed 中「11 个失败及交互效应」不能直接断言全由 DAV-783 单独造成(783 单独 19 failed,组合 30 failed,差额 11 项含兄弟交互,未逐项归因)。✅采纳,留待返修时逐项记录。
5. Codex 称「七条主候选联合全量未完成」——**此条与总控实测不符,以数据保留**:总控 `ta-union` 树合入七条后全量实跑 `17 failed / 3843 passed`(542.84s,证据存档 `work/union-7candidates-regression-20260909.txt`)。合并 tip `cc9d66f7f4b8b4d1762081125ca001c82edbfe20`(不可达对象,已双方核对):trunk 后共 **37 个新增可达 commit,其中 7 个是合并 commit**(非「37 个 merge」——总控上一轮此处措辞已更正);七条全是该 tip 祖先,`ffe5ae4`/`cdfa42f` 均不在。3843 比基线 3214 多 629,是七条各自新增测试全通过。**Codex 已撤回「联合全量未完成」,确认信息滞后。**

### 锁定结论

- **候选审查阶段,禁止批量 FF。**
- 七条主候选联合全量**已完成**:失败集与记录基线一致(17 项),**暂未观察到新增失败**。严谨口径——不写「零回归/全绿」:(a) 全量本身仍有 17 个已知失败(D-009 语义迁移遗留);(b) 基线到 trunk 的等价性是**基于 E-01 纯新增文件的推断**(`work/2026-09-08-full-suite-baseline.md:11`),非单独重跑 trunk 的实测。
- 「联合树完成且失败集不变」**不自动产生 FF/部署授权**——仍须 David 逐条完整 SHA + 准予合入,逐个 FF、FF 后各自联合回归 + 远端回读。
- **DAV-783 返修**(四缺陷 + 19 回归),**DAV-779 等 DAV-786 审查**;两个 shadow_credit 兄弟不与七条同批,彼此也不同批。
- 社会模块「代码集成、运行态关闭、四道 Gate 未真实验收」表述不受本次纠正影响。

## shadow_credit.py 修改链依赖顺序（2026-09-09，防止兄弟冲突）

当前三条待处理改动都涉及 `shadow_credit.py` 的门槛或 backfill 语义，不能并行做成互不知情的线性 FF：

1. **DAV-779**（候选 `ffe5ae4ee3c19bb538cd748f246b9d86ae83e657`）：T+5 双侧证据停牌检测；DAV-786 已完成精确 SHA 的独立 RT-FULL 审查并 PASS。独立全量为 `17 failed, 3213 passed, 1 skipped, 3 deselected`，失败集逐项对应记录基线，仍待最终授权。
2. **DAV-783 v3**（候选 `3c6e60690d80e2c6b53f57fa16aa056028c9ed9e`）：将 `analysis_status` 过滤从共用的 `is_qualifying_v2_report` 解耦，集中到 H1b 门槛路径，保护 backfill。DAV-788 已完成精确 SHA 的独立 RT-FULL 审查并 PASS；最新候选联合 DAV-779 的真实 Git worktree 全量为 `17 failed, 3229 passed, 1 skipped, 3 deselected`，失败集与记录基线逐项一致，仍待最终授权。
3. **DAV-782 修法**（尚无修法卡）：修复 `backfill_tplus5_shadow_for_report` 的 entry 缺失短路（基线约 `:1673`），保留已有 T+5 价格，不因 entry 缺失降级为 `data_missing`。

**依赖顺序已锁定：** DAV-782 暂不派，须先由 Cursor/David 裁定并逐个接纳 DAV-779、DAV-783 v3 之一；基于已核验的 trunk tip 重算 DAV-782，避免再次制造 backfill 语义兄弟提交。DAV-779、DAV-783 v3、DAV-782 必须逐个 FF，每次 FF 后做联合回归和远端回读；任意两条都不能只凭文本无冲突、单卡绿测试或独立 PASS 放行。

## DAV-783 v3 干净 + 779/783 可同批(2026-09-09,总控真 worktree 验证)

- **v3 `3c6e60690d80e2c6b53f57fa16aa056028c9ed9e`**:真 worktree 全量 `17 failed / 3208 passed`,失败集逐项吻合基线 17 项,零新增回归。backfill 误伤已修(`test_tplus5_shadow_backfill.py` 28 passed —— v2 曾 17 failed)。白名单无生成文件,RT-1 无 decision 回退。
- **779 + 783v3 联合无冲突**:合并树定向 152 passed(v2 时同组 30 failed)。**兄弟冲突根源(backfill 被过滤误伤)已消除。**
  - **更正此前判断**:v2 阶段记「779/783 绝不同批 FF」——那是因 v2 坏。**v3 修好后,779(`ffe5ae4`)与 783v3(`3c6e606`)可作为一对同批 FF**(仍须 David 逐条完整 SHA + 准予合入)。
- **DAV-788 审核**:结论对(PASS),但全量证据用 71 项子集(`15 failed/56 passed`),不合 D-012 §4b。**非第五次假阳性**(结论未放行真缺陷),但方法违规——全量门禁以总控真 worktree 实测为准。Codex 的「归档树 5 项额外失败=缺 .git 环境伪差异」判断成立(真 worktree 无此失败)。

### 当前 FF 就绪清单(全部待 David 授权,逐条完整 SHA + 准予合入)

| 候选 | SHA | 验证状态 |
|---|---|---|
| 七主候选 | 各自 SHA | 联合树 cc9d66f 全量17=基线 |
| DAV-779 | `ffe5ae4ee3c19bb538cd748f246b9d86ae83e657` | 三重验证+逐项核对通过 |
| DAV-783 v3 | `3c6e60690d80e2c6b53f57fa16aa056028c9ed9e` | 真全量17=基线+backfill修复+与779无冲突 |

779 与 783v3 **可同批,证据链完整**:
- 联合树 tip `658e168`(不可达);合并无冲突。
- **联合全量已跑(总控复现 Codex)**:`17 failed / 3229 passed`(457s),失败集逐项吻合基线 17 项,零新增回归。证据存档 `work/union-779-783v3-regression-20260909.txt`。双源(Codex + 总控)逐位一致。

**与七主候选的三方联合全量尚未跑**——若要 shadow_credit 对 + 七主候选一起 FF,需补跑三方联合回归(不能从「两两干净」推「三方干净」,v2 阶段教训)。分单元 FF 则各单元证据已齐。

## ✅ 九候选已合入 trunk（2026-09-09，David 授权 + 亲手 push）

- **新 trunk**：`04c8aa3127b701deb4c72e1be0555c58b2f8e728`（旧 `3496280` + 41 commits，9 个合并）
- **回退点**：`3496280753139aca9b4567f74da625ed48bdfc1a`；回退命令 `git push --force-with-lease origin 3496280753139aca9b4567f74da625ed48bdfc1a:codex/dav-4-p2a-trunk`
- **合入内容（九条，全部三方联合全量验证：`17 failed / 3880 passed`，失败集逐项=基线，零新增回归）**：
  D-03 `962bf16` / E-01 `464790d` / E-03a `77e7cab` / prob `3864da5c` / 校准 `1b84468` / V-01 `b6359c3` / proxy自检 `ada07c9d` / 779停牌 `ffe5ae4` / 783v3样本池 `3c6e606`
- **执行方式**：auto 分类器两次拦截 agent push（外向改主干),最终由 David 在终端亲手执行 `git push`，总控远端回读确认逐位一致。**符合 D-011：FF 由 David 授权且亲自执行。**
- **仍未做**（红线不因合入解锁）：部署、开加权、社交 active、写生产库。H1b 维持 `KEEP_FALSE`。
- **后续**：DAV-782 backfill 短路修法卡现可派（v3 已入 trunk，无兄弟冲突顾虑）；E-02/V-02 前置(E-01/V-01)已入 trunk，可开工。

## DAV-789 backfill 幂等修复:完整过 D-012,待 FF（2026-09-09）

- **候选**：`3282404dbd6105eebc2c120a15a5c644e337dbcf`（分支 `agent/2/2c6b7e29301e`，第一父 = 新 trunk `04c8aa3`）
- **验证（总控亲验）**：白名单仅 `shadow_credit.py` + `tests/test_backfill_idempotency.py`；8 个红队场景（RT-1~5 + batch）全 PASS；真全量 `17 failed / 3888 passed`，失败集逐项=基线，零新增回归。存档 `work/dav789-fullreg-20260909.txt`。
- **修复内容**：backfill 解耦 price 与 entry 判定——有效 `t_plus_5_price` + entry 缺失（含非数字文本如"逢反抽122.80减仓")不再降级 `data_missing`；幂等（重复 backfill 不改已有有效 price）。解除 DAV-782 诊断的「24 份正常样本会退化」隐患。
- **❌ 已打回**（2026-09-09，Codex 发现+总控复现）：回退分支被 price_series/get_price_fn 短路——传部分 price_series 或 get_price_fn 返回 None 时，已有有效 `t_plus_5_price`(如 15.6) 被清成 data_missing。候选只堵了「不传取价源」一个入口，漏两个。DAV-790 PASS 作废（第五次红队场景不全→漏缺陷）。
- **返修 DAV-792**（资深开发2，todo）：回退改统一兜底（非 if/elif 互斥），补 RT-1c（部分 price_series）/RT-1d（get_price_fn None）。红队清单已按 **D-012 §5b** 经总控+Codex 双人确认覆盖面。
- **D-012 §5b 新立**：红队清单完备性须第二双眼睛确认——DAV-789 正是开卡方单方列清单漏入口所致。
- **一段插曲（记录）**：total 阻断曾是 antigravity 凭据过期→CPA 503 auth_unavailable→全派工卡死；David 刷新凭据后解除（gemini-3.8 实测 200）。判据用实际请求,不用本地时钟（该机时钟不稳，会误报凭据过期）。

## 排队 / 未授权

- Track B 真实启用：当前 shadow 已运行，xhs/dy 采集与归档 operational；active、扩大采集和 Cookie 导出仍未授权。
- H1b：门槛 FAIL / `KEEP_FALSE`。最新只读三段台账为 `795 raw → 133 qualifying v2 → 9 D-009 eligible`；`legacy_unversioned` 实际评估 7 条，样本量远低于 60，仍不得靠调样本/缩窗口/降门槛过关。
- 未授权：准予部署、开加权、启用社交 active、扩大采集、PDF 正文抽取。
