# 剩余验收独立核验报告（DAV-1024，只读）

核验时间：2026-09-17。核验人：项目评估师。全程只读：未改产品代码、未写生产库、未启动采集、未读 Cookie/密钥。

## 环境基线（实时回读，非沿用快照）

- 远端 trunk `codex/dav-4-p2a-trunk` = `5a0320f0618d203e95e7197e08b110c7850078d4`（`git ls-remote` 实时回读）
- 8000 服务：**无监听，已停机**（`/healthz` 连接失败）；最后部署版 `f094d6a`
- 生产库 `data/tradingagents.db`（只读打开）：reports=1409（completed=793 / failed=616）；`analysis_status` 分布 `NULL=1347 / ABSTAIN=36 / INVALID_RUN=2 / VALID=24`
- `.env` / `.env.local` 中无 `credit_weighting` 与 `TA_SOCIAL_MODE` 键 → 两者均走默认关闭/disabled
- 看板快照（issues.json, 1022 卡）：`in_progress` 1（DAV-979）、`in_review` 9、`blocked` 2（DAV-1003、DAV-990）、`backlog` 6

## 逐项验收核对

### 1. V-03 完整实验（四维消融 + 反泄漏 + OOS）

- **源依据**：`work/2026-09-12-dispatch-sequence.md` 批次 3-1/3-2；`work/v03-freeze-sheet-20260909.md`（冻结口径 + DAV-800 blocker）
- **已完成（代码层）**：V-03a 测量引擎/harness 已合入主干——`tradingagents/eval/v03_return_measure.py`、`scripts/run_v03_return_measure.py` 在 `5a0320f` 树中存在（`git cat-file` 验证）；DAV-861/862/865/866/882 均 done。首批进度基线已有：89 可评测，净 +0.41% vs 沪深300 +0.15%（`work/v03_return_measurement_report.md`，半成品标注）
- **缺口**：四维消融、反泄漏、微观成本完整实验**从未执行**（看板无任何消融实验卡）。阻断链：V-03a 被 DAV-998 阻断级缺陷冻结（`PROJECT_STATE.md` 文首：DAV-998 合入前不得部署、不得继续合入其他候选）；DAV-998 在 `in_review`，整合收口 DAV-1003 `blocked`（Gate 0 护栏验证 FAIL，`work/2026-09-16-gate0-guardrail-fork.md`）；RT-FULL 门禁自身仍依赖 DAV-979（in_progress）
- **授权要求**：跑真实实验属执行动作（需部署/样本生成授权）；收益定性结论额外要求系统输入完整度三项齐备（博弈论已合入、真实舆情未接、量价填充率已合入——第二项仍是缺口）
- **判定**：代码工具就绪，实验本身 0% 完成；不可行条件 = DAV-998 解堵 + 部署 + 授权

### 2. 社交真实 Gate0/1 → shadow → active → legacy 退出

- **源依据**：`docs/social_data/implementation_plan.md:636-720`（Gate 0–4 定义）、`work/2026-09-12-dispatch-sequence.md` 批次 4
- **已完成**：Gate 4（legacy_proxy 删除）已合入主干（DAV-545，`tradingagents/`+`api/` 引用为 0）；Gate 1 离线契约代码存在（`tradingagents/dataflows/social/contracts.py`、`archive_schema.py` 在 `5a0320f` 已验证）；全部契约/接线测试合入
- **缺口**：真实 Gate 0 **未满足**（`work/2026-09-12-l4-gate0-readonly.md`：本机无 MediaCrawler 检出、无 source DB、无 archive 路径、无 Cookie 目录）；生产库 `sqlite_master` 中**无任何 social/archive 表**（实查为空）；Gate 2 shadow 30/10 与 Gate 3 canary 未做；`TA_SOCIAL_MODE` 未设 = `disabled`
- **授权要求**：Gate 0/1 真实采集导入、Gate 2/3 均需 David 单独授权（Cookie、采集、写 archive）
- **判定**：代码交付 ≠ 真实启用；不得宣称舆情已接入

### 3. H1b 样本门槛（R-01）

- **源依据**：`work/2026-09-12-dispatch-sequence.md` S-1；DECISIONS.md D-013 §5
- **现状**：仍为 **FAIL / KEEP_FALSE**。目标台账 793 completed → 131 v2 合格 → 9 份 D-009 合格（cohort 再筛 N=7）。`.env` 无 `credit_weighting` 键，`credit_weighting_enabled` 保持 False；`analysis_status` 有效样本仅 VALID=24
- **缺口**：v2 合格样本量远不达门槛；生产服务停机，无新增样本管道在跑
- **授权要求**：补样本属写生产库动作，需 David 明确数据写入授权；开 `credit_weighting_enabled` 属红线，须 David 单独授权
- **判定**：未达标，维持关闭是正确的；禁止以样本积累名义隐式写库

### 4. 符号碰撞清洗（生产库）

- **源依据**：`work/v03-freeze-sheet-20260909.md` ⛔ 节；`work/2026-09-12-dispatch-sequence.md` 5-2
- **现状**：DAV-800 规范化代码已合入（`5a00c753` 在祖先链）且卡 done，但**生产库数据清洗未执行**——实查 `reports.symbol`：`''` 空值 32 条、非法值 `AGENT`/`AUUSDO` 各 1、裸 6 位残留 4 条（`000001`×2、`600519`×1、`603259`×1），3 组碰撞未合并（`600519` 裸码与 `600519.SH` 739 条并存等）。合计 38 行脏数据仍在生产库
- **缺口**：迁移/清洗脚本未对 `data/tradingagents.db` 实跑
- **授权要求**：写生产库须 David 单独授权 + 先 `.backup()`；「卡 done」不等于「数据已清洗」
- **判定**：代码已合入，数据未清洗；此为 V-03a 实验数据质量的现实缺口（碰撞会把同一股票劈成两份）

## 结论

四项剩余验收均为「代码就绪 / 数据与实验未做」形态：V-03 完整实验未跑（被 DAV-998 → RT-FULL 门禁链阻断）、社交真实 Gate 0–3 未做、H1b 维持 FAIL、生产库符号清洗未执行。无任何一项可宣告完成；所有推进动作均需对应授权（部署/采集/写库/开关），不得由代码合入事实外推。
