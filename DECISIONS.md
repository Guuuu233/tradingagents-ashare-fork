# Decisions

记录跨会话持续有效的决定。临时进度放在 `PROJECT_STATE.md`；实现细节仍以代码、测试和当前 issue 为准。

## 当前有效决定与执行口径（2026-09-24 更新）

本节条款优先于下方 2026-09-14 一节中与之冲突的内容。D-013 的证据清单仍然有效，但签字人按 D-033 改为总控。

### D-033：单一总控制与角色边界（有效）

- 日期：2026-09-23。授权依据为 David 在总控会话中的两句原话：
  - 「我决定任命你以后担任新总控的角色」；
  - 同日对 GPT 建议的确认：「他的建议我总体上是认可的，不过第4个还是交给总工带领团队施工，你负责写明要求并后续签署核实就行」。
- **总控**：Claude（Claude Code 桌面会话）。它是唯一的最终技术裁决者和 D-013 签字者，负责以下事项：
  - 合入与部署的放行；
  - B2+B3 等冻结项的解冻；
  - 路线裁决；
  - 对总工和团队交付的最终签收。
- **主控**：此前由 ChatGPT 会话担任，现在转为独立复核与第二意见。除非 David 另行明确授权，主控不再签发「准予合入」「准予部署」或解冻。
- **总工**：执行统筹，由另一个会话担任。职责是组织团队施工、派工、盯 run、拉同 SHA 复审、汇总证据交总控签收。总工不得自行合入、部署或解冻。
- **Multica 团队 agent**（项目主管、调度助手、开发、审核、运维等）：按卡片执行。卡内文字不构成授权。
- **历史授权**：此前已签发并执行完成的授权继续有效，不追溯撤销；今后的正式授权以总控为准。
- **看板署名**：Multica CLI 的写操作一律显示为 David 的成员账号，因此按前缀区分：
  - 总控的动作以「【总控】」开头；
  - 总工的动作以「【总工】」开头；
  - 没有前缀的，视为 David 本人的普通评论。
- **授权格式**：「准予合入」或「准予部署」必须同时写明三项——完整 40 位 SHA、明确的授权语句、边界（本次授权不包含什么）。
- **治理文档提交**：只改 `DECISIONS.md`、`PROJECT_STATE.md`、`ROADMAP.md` 等治理文档的提交，总控可以直接落到主干，但必须满足三个条件：白名单只含治理文档、`git diff --check` 通过、推送后远端回读一致。凡涉及产品代码或测试的变更，仍须走完整的 D-013。
- **读取顺序**：D-002 的共享上下文读取顺序增补为 `AGENTS.md` → `PROJECT_STATE.md` → `DECISIONS.md` → `ROADMAP.md`。
- **红线不变**：以下事项仍须 David 单独授权——生产库业务写入、社交 active、真实采集与 Cookie、凭据轮换、历史重写、开启信用加权。

### D-034：price-ref 路线与 DAV-1223 B0 定量更正（有效）

- **保留**：DAV-1223 B0 的高层结论成立，即现行 `price_ref.v1` 契约存在确定性 blocker，继续增加 replicate 不会带来信息增量。
- **作废**：B0 备忘中的以下定量拆账和归因：
  - 约 82% 为非价格误抽；
  - pool 中真实价格仅 2/473；
  - cross_basis 的主因是模型把披露价与坐标并置；
  - invalid_conversion 是模型缺少复权因子；
  - 「建议入场区间：35.20 元」必然 unbacked。
- **作废原因**：各快照 `stock_data` 的 CSV 列序不一致——9/10 为 `date,low,close,volume,open,high`，s08 为 `close,low,high,open,date,volume`。按固定列号解析，使 9/10 的价格池为空，下界为 1e9、上界为 0，因而全部被判为「超出价格区间」。更正证据见 DAV-1223 和 DAV-1136 下 2026-09-23 的更正评论。
- **顺序**：
  1. DAV-1225：零 token 纠错，加 W0–W4 分层 what-if；由总工组织施工，签收标准见 DAV-1225 的总控评论；
  2. 总控重写 DAV-1224 的范围；
  3. 实施并做同 SHA 复审；
  4. 对 DAV-1222 的 50 份冻结 state 做零 LLM 重算；
  5. 总控裁决 B2+B3 是否解冻、是否需要最小 Phase B1。
- **五个杠杆同等量化**，不先验排除任何一个：
  - 消除非价格、外币和披露关键词误报；
  - 修正「折合/折算」的转换语义；
  - 给派生估值（derived_estimate）单独的语义角色；
  - 共享的可执行价位解析；
  - 严格 source-backed 的 pool→registry 桥接。
- **严格桥接的定义**：必须有字段级身份才能继承 vendor_qfq，数值相等本身不算。三种情形之一即可：
  - 上下文明确写出 EMA10、VWMA、SMA、BOLL 等指标名，且数值精确匹配对应的冻结指标；
  - 明确的日期加 OHLC 字段；
  - 明确的涨跌停字段。
- 不得把 Stage 4 降为 executable-only；不得为了产出 clean 而关闭或旁路 `price_basis_gate`。
- B2+B3 的 24 条真实重放继续冻结；DAV-1136 维持 blocked。

### D-035：样本口径双视图（有效）

- 凡报告 H1b 或 clean 数，必须同时给出两个视图，并注明数据库快照时间：
  - production view：按线上运行 SHA 的代码口径；
  - trunk candidate view：按远端主干 SHA 的代码口径。
- 2026-09-23 实测（生产库 `.backup()` 副本，完成于 21:55 前后）：
  - production view（`3d9c414`）：clean=15，其中 legacy 6、v1 9；
  - trunk candidate view（`9d03c89`，含 Stage 3.5 HOLD 语义隔离 4 条，与价格口径隔离重叠 1 条）：clean=12，其中 legacy 6、v1 6。
- 「51/51 blocked」的准确含义是：50 份 DAV-1222 冻结重放，加 1 份 contract-era 真实生产 smoke（报告 `7a557f07`）。它不是 51 份线上新报告。

### D-036：定量证据必须可复现（有效）

- 任何进入裁决依据的定量结论，其脚本与输出都必须入库，或随候选分支一起提交；并且能对明确的输入（路径加 SHA256）一键复跑。
- 只存在于临时目录或会话记录里的数字，不得作为裁决依据。
- 起因：DAV-1223 B0 备忘的错误数字来自一段未落盘的一次性脚本，路线裁决据此作出后又被撤回。

### D-037：并行准备线——零模型、零生产写入（有效）

- **现在允许与关键路径并行**：
  - probability 覆盖率与缺失根因的只读诊断（DAV-1226）；
  - 样本供给漏斗与 clean 可达性的只读诊断（DAV-1227）；
  - F1 冻结经验包协议阶段 H 的准备：标签契约与披露季定义草案、数据可得性盘点，不批量采集（DAV-1228）。
- **暂不启动**，等关键路径打通后由总控另行放行：
  - 概率输出与生成链改造；
  - 校准训练；
  - B2+B3 消耗型重放；
  - 任何批量真实分析。
- **约束**：只用生产库的 `.backup()` 副本或 `mode=ro`；不调用 LLM；不写生产库；不修改主干代码。

### D-038：运行态纳入台账（有效）

- 生产服务应从明确的发布 worktree、以精确 SHA 启动。当前的 `3d9c414` 直接运行在主仓库目录（detached HEAD），下一次发布门时迁回独立发布 worktree。
- 每次发布前后，`PROJECT_STATE.md` 头部必须记录：服务 SHA、运行目录、PID，以及关键运行开关（含 `TA_SOCIAL_MODE`）。
- **社交模式现状**：`/v1/social-data/status` 回读为 `disabled`，当前进程未设置 `TA_SOCIAL_MODE`；09-19 台账记录的是 shadow。
- **静默变化的原因（已查明）**：宿主 `.env` 本来就没有 `TA_SOCIAL_*` 三项，09-18 已在 `work/social-shadow-deploy-gate-20260918.md` 记录这一风险。09-20 之后的发布直接从主仓库目录启动，没有经过带社交校验的发布脚本，服务因此回落到默认值 `disabled`。
- **总控决定（2026-09-23）**：暂时保持 `disabled`，不为此单独重启生产。David 原话：「社交数据这个你看着办」。理由有三：
  - 社交归档库只有 09-18 的一次测试采集（关键词「贵州茅台」，xhs 203 条、dy 154 条，快照 357 个），之后没有持续采集，恢复 shadow 几乎没有增量；
  - shadow 模式下社交数据本来就不参与方向判断（`direction_allowed=false`）；
  - 现行关键路径和各项测量都不依赖社交数据。
- **下次发布门的要求**：必须显式设置并回读 `TA_SOCIAL_MODE`（届时为 `disabled`），并写入 `PROJECT_STATE` 头部。
- **将来若要恢复 shadow 或开展持续采集**：先要有明确的研究用途和采集方案（标的范围、频率、登录态的使用方式），由总控提出；真实采集与 Cookie 仍属红线，须 David 单独授权。

### D-039：DAV-1225 签收与 DAV-1224 施工范围（有效，2026-09-24）

- **签收**：DAV-1225 候选 `53df9ad12a5020a64fac6d5db14a7864f3edc701` 签收，并由总控快进并入主干（只含证据文件，产品代码与 `9d03c89` 一致）。
  - 总控独立核验内容：复跑结果逐字节一致；R1–R5 全部落实；W4 的 6 个 pass run 逐条核对全部成立；已知坏锚在各层全部 blocked。
  - 首轮候选 `4d46393` 的同 SHA 复审（DAV-1230）虽给了 PASS，但漏掉了硬标准 #3、#4、派生规则产生的假 clean、提交范围越界四项问题，已按 R1–R5 返修。复审 PASS 不能代替总控核验。
- **what-if 结论**：clean 数 W0–W4 依次为 0 → 0 → 4 → 5 → 6。五个杠杆全部纳入 DAV-1224：
  - typed disclosure 消歧：cross_basis 从 556 降到 24；
  - derived_estimate 语义角色：+4；
  - 共享可执行价位解析：+1；
  - 严格字段级桥接：+1；
  - 抽取假阳性消除：作为前提。
  - D-034 中「pool bridge 非主路线」的旧口径作废。
- **DAV-1224 范围**：以 `work/issue-dav1224-spec-v2-20260924.md` 为准，旧版 v1 存档于 `work/issue-dav1224-spec-v1-20260923.md`。核心验收是用候选 trunk 代码对 50 份冻结 state 做零 LLM 重算，结果与 W4 对齐，任何差异都要逐 run 解释；已知坏锚全部 blocked。
- **已知上限**：五个杠杆全部落地后，冻结语料上也只有约 6/50 通过。剩余的主体是模型自拟、没有来源的真实价位。让生成侧标注价格来源，作为 DAV-1224 之后的独立评估项。

### D-040：公开仓库的数据入库边界（有效，2026-09-24）

- 本仓库为公开仓库。以下内容不得提交，任何分支都不行：
  - 第三方与付费数据供应商的原始记录，包括新闻正文、行情与资金流明细、研报与分析师明细；
  - 完整的运行快照（pickle）。
- **探测类交付**只保留字段名、行数和统计汇总。行级样本必须去除研报标题、人名等内容。
- **分析与审计交付**只提交脚本、统计汇总、逐条判定明细，以及输入文件的 SHA256。输入数据保留在本地，并用 gitignore 排除。
- **已发生的越界**：DAV-1225 旧候选分支 `agent/2/dav-1225-b0-corr` 含 60 个 pickle，由总工在签收后删除该远端分支。DAV-1228 的 `probe-raw/probe2_tushare.json` 并入主干前，须去除 report_rc 的样本行。
- **2026-09-24 补充**：
  - 旧分支 `agent/2/dav-1225-b0-corr` 已删除，经远端核验。
  - 探测类交付一律不保留 vendor 行级样本，免费数据源也一样。只保留字段名、行数、日期范围和布尔结论。
  - DAV-1228 分支 `agent/agent/665f3af82f25` 的问题：头部提交 `15dfbd4` 已剥离 report_rc/index_member 的样本行，但历史提交 `7e237e4` 仍含原始行；probe1/3/4 也仍有样本行。
  - 因此该线只能以单个 squash 提交并入主干，提交里只含已去除样本行的元信息，合入后删除原分支。合入前须有总控签字。

### D-041：DAV-1224 合入、DAV-1233 发布，以及生产运行规范（有效，2026-09-24）

- **合入**：DAV-1224 候选 `a181e4afc763019f24fa258fa7557a84c305bedb` 经总控核验后签「准予合入」，由总工把主干从 `ae89d2b` 快进到 `a181e4a`。
  - 该候选等于 `6043fbc` 加一个只删 4 处 EOF 空行的提交。
  - 核验结果：
    - 50 份冻结 state 的零 LLM 重算为 6/50，与 W4 一致；
    - 已知坏锚全部 blocked；
    - RT-FULL 5488 passed，另有 1 个既有失败；
    - DAV-1232 同 SHA 复审 PASS。
- **发布**：
  - DAV-1233 第一段的 RT-FULL 对照：基线 `3d9c414` 为 5401 passed / 1 failed，候选为 5488 passed / 1 failed，新增失败为 0。
  - 唯一的失败 `test_rt_s4_david_account_clean_population_counts` 在两侧均为 575≠317，原因是读活库与硬编码计数比对，与代码无关。
  - 总控签「准予部署」后，09-24 03:16 切换，生产 = `a181e4a`。
  - 总控独立回读：
    - 进程、环境变量、数据库句柄、社交状态、provider、404、前端 bundle、备份、计数全部通过；
    - 受控 smoke 1 条（4390ddfd），user_id 精确一致，报告计数恰好 +1。
- **生产运行规范**：今后每次发布与重启都适用，完整口径见 DAV-1233 的总控签发评论。
  1. 从独立发布 worktree `/Users/davidliu/Documents/TradingAgents-AShare-releases/<sha>` 启动，该 worktree 为 detached 并加 lock。不再从主 checkout 启动。
  2. 三样被 git 忽略的东西要单独准备：
     - `.env` 用软链；
     - `frontend/dist` 按发布说明复制或构建；
     - DATABASE_URL 用生产库的绝对路径，发布目录下不建 `data/`。
  3. 用 `env -i` 白名单环境启动：HOME、USER、LOGNAME、SHELL、PATH、TMPDIR、DATABASE_URL、TA_SOCIAL_MODE、代理与 no_proxy。不继承 agent 变量。
  4. 启动后回读：commit_sha、`lsof` 看到的数据库路径、社交状态、provider、K 线、404、前端 bundle、报告计数。
  5. 验收失败时回退到上一发布目录（本次为主 checkout 的 `3d9c414`），然后回报。不在现场修改。
- **已接受的技术债**：
  - typed disclosure 的识别不看数值，失败方向是更保守；
  - ST 股 ±5% 涨跌停未单列；
  - `price_ref_source` 键未清理；
  - DAV-1231 的 🟢 项；
  - version 显示为 `dev`；
  - 前端 dist 自 08-28 起未重建；
  - `test_rt_s4` 是读活库的快照测试。
- **上线后首份真实报告的审计**（4390ddfd）：
  - derived_estimate 豁免 3 条，其中 2 条误判；
  - 20 条违规 ref 中有 4 条是抽取误报；
  - 另外 16 条是模型自拟、没有来源的交易价位。
- **顺序**：
  1. **DAV-1235**：price-ref 精度收紧，零 LLM。必须先于任何能提高 clean 产出的杠杆落地，否则误豁免会在其他违规消除后变成假 clean。
  2. **生成侧价格来源标注**：由总控撰写规格。这一项属于 D-037 冻结的生成链改造，由总控单独放行，需要受控真实样本验证。
  3. **B2+B3 解冻裁决**。

### D-013 附注（2026-09-23）

D-013 的证据清单不变，签字人由「总工」改为「总控」（见 D-033）。

## 当前有效决定与执行口径（2026-09-14）

以下条款覆盖本文件中较早的施工快照；旧 SHA、旧服务状态和旧角色名称只作历史记录。

### D-013：合入、部署和状态收口由总工按证据放行（有效）

> 2026-09-23 附注：签字人按 D-033 改为总控；以下证据清单继续有效。

- David 已明确将合入、部署和状态收口的放行权交给总工；这不是降低门禁，也不是允许 agent 自授权。
- 每次放行仍必须核对完整候选 SHA、直接父、白名单、`git diff --check`、同 SHA **代码审核员**复审、RT-FULL、远端回读；部署还必须有 SQLite 备份、完整性/计数核对、`/healthz` 精确回读和启动恢复证据。
- 生产库写入、真实社交采集/Cookie、启用社交 active、凭据轮换、历史重写和信用加权仍是独立红线；本决定不包含这些授权。

### D-014：代码审查统一派给代码审核员（有效）

- 后续代码审查、交付审查和合入前质量审查派给 `代码审核员`（必要时 `代码审核员2`），不得派给「独立代码审核员」。
- 实施者与审查者必须分离；完整 SHA、隔离 worktree、白名单、红队场景和 RT-FULL 门禁不变。

### D-015/D-016：custom_prompt 六项契约及 DAV-808 实施（有效，已完成）

- custom_prompt 纳入 E-02 硬约束：保存/启动/研究经理三层共用确定性判定；命中时保存拒绝，已存或绕过入口的文本使整次任务 `NO_TRADE`，五个可注入角色均不再接收该文本；不自动删库、不静默改写。
- 现有全局提示词保留并记录 hash；DAV-808 已完成审查、合入和部署。该决定不解锁 H1b、信用加权或真实社交采集。

### D-017：V-03a provenance 返修（有效，已合入并部署）

- DAV-866 的 provenance 返修已合入；随后随 E-04/Fuyao 修复部署到 `63d5648bca7c49f57e1211d848cbc1d02ff6b3a5`。
- V-03a 仍只能作为隔离副本上的半成品进度基线，不能写成正式收益结论；详见 `PROJECT_STATE.md` 和 `work/2026-09-14-v03a-readonly-63d.md`。

### D-018：P1-D bounded 七源一手证据摘要（有效，已合入并部署）

- trader 与最终 `risk_manager` 只接收由代码确定性组合的七源证据摘要：固定来源顺序、单源/总长度上限、空/失败状态和来源标签均保留；不把七份报告全文透传给模型，不把分析师数量当作票数。
- 候选完整 SHA `9d702e7522c94bf3ac983cb1ede10943cfca1a4b`，直接父 `51b8b155ef3d47e99dead8ce4960562cd9a77c9e`；DAV-901 已由**代码审核员**对同一 SHA 只读通过，关联集合 160 项通过；对线上 `63d5648` 的 RT-FULL 为双方各 18 failed、候选 4415 passed、基线 4406 passed，失败集合双向差集为 0。
- 上线前已备份并校验生产 SQLite；上线后 `/healthz` 精确回读 `9d702e7`，provider health、只读行情和 social disabled 烟测通过，reports 计数与数据库 SHA 未变。本决定不授权真实分析、生产数据写入、社交采集、信用加权或历史重写。

### D-019：P1-E Fuyao 财务披露日 PIT fail-closed（有效，代码已合入）

- DAV-902 候选完整 SHA 为 `cd7456012fe2e0b03bd33333e6972301c1c76ddc`，直接父为
  `251fd00aa8a9584850cae5d4ab5adfbd3c5d3b94`；白名单严格为
  `tradingagents/dataflows/providers/cn_fuyao_provider.py` 与
  `tests/test_cn_fuyao_provider.py`。
- DAV-903 已由**代码审核员**对同一完整 SHA 只读 PASS。固定 Python 3.10 的专项关联集合为
  `91 passed` 与 `295 passed, 3 deselected`；没有把开发方 Python 3.14 的输出当作证据。
- 同口径 RT-FULL 使用 `env -u PYTHONPATH .../.venv310/bin/python -m pytest -q`：基线
  `9d702e7` 为 `18 failed / 4415 passed`，候选为 `18 failed / 4426 passed`，双方失败集合
  双向差集均为空，新增失败为 0。
- 候选已从 `251fd00` 线性合入 `cd7456`；治理文档随后作为线性后代落账。此决定不把代码合入
  视为部署：线上仍运行 `9d702e7`，发布前必须重新备份 SQLite、核对完整性/计数、受控启动、
  `/healthz` 精确回读和只读烟测。
- 本决定不授权真实分析、生产数据写入、真实采集、Cookie、信用加权或历史重写；P1-E 只改变
  Fuyao provider 的可见性判断和对应测试。

### D-020：P1-E 受控发布（有效，已完成）

- 发布版本为 `026349614a3f1b92a95dc06c0515f10ebec193bc`，其代码父为 P1-E 候选
  `cd7456012fe2e0b03bd33333e6972301c1c76ddc`；发布副本为
  `/private/tmp/ta-release-p1e-0263496-20260914`。
- 旧服务 PID `6509` 已正常停止；新服务 PID `19213` 在 8000 运行，工作目录已核对；
  `/healthz` 的完整 `commit_sha`/`build_identity` 均精确匹配发布 SHA。
- provider health、600519.SH 两日只读 K 线、social disabled 三项烟测通过；未调用真实分析入口。
- 生产 SQLite 仍为 285339648 bytes、SHA-256
  `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010`，`quick_check=ok`，
  reports `1409/793/616`。发布备份 `work/tradingagents.db.bak-20260914-predeploy-0263496` 的
  `quick_check=ok`、reports `1409/793/616`；其 SQLite 一致性备份 SHA 为
  `df628f4293069a820ef69a136a1acbb0e7a517f6638dd24a9faeea30f5b51b9e`。
- 本决定只完成代码发布和只读运行核验，不授权生产业务写入、真实采集、Cookie、信用加权、
  历史重写或将本次发布宣称为生产财务 PIT 业务证据。

### D-021：当前发布的前端 live bundle 验收（有效，已完成）

- 在发布版本 `026349614a3f1b92a95dc06c0515f10ebec193bc` 的实际运行副本中重建前端：
  `npm test -- --run` 为 15 个文件/144 个测试全过，`npm run build` 成功。
- 8001 预启动和 8000 重启后的实际 HTTP 入口均返回网页 `200`，页面标题为
  `TradingAgents Dashboard`，新 JS 资源返回 `200`；API 未知路径仍返回 `404`。
- 8000 当前服务已加载该 bundle；资源 SHA 与构建命令记录在
  `work/2026-09-14-frontend-live-bundle.md`。后续发布不得沿用本次 hash，必须重建并复验。
- 本决定只覆盖构建物和 HTTP 入口，不等于浏览器交互、登录、真实分析或生产数据库写入授权。

### D-022：P1-F 连板天梯只按固定当前窗口接入（有效，已实施合入并受控发布）

- 官方 Fuyao `/api/a-share/special-data/limit-up-ladder` 不接受日期参数，只返回固定近 30
  个交易日矩阵；因此内部能力 `get_limit_up_ladder` 必须把请求基准日期用于本地 PIT 门禁，
  不能把当前窗口伪装成历史快照。
- `cn_fuyao` 是唯一来源；不得从 `get_zt_pool` 合成、不得自动回退到其他 provider、不得
  裁剪未来日期或用 `iloc`/日期回退掩盖窗口不匹配。六个板块、来源、窗口、`seal_nextday`
  的未知值和 typed failure/unavailable 语义必须保留。
- 天梯只进入独立的 `market_attention.limit_up_ladder` 背景字段，不接入交易方向、博弈论
  信号、收益评估、H1b、数据库回填或前端；历史分析遇到该能力时 fail-closed，但不阻断整单。
- 设计和红队清单见 `work/2026-09-14-p1f-limit-up-ladder-design.md`。实施卡交付后必须由
  **代码审核员**对同一完整 SHA 只读审查，再跑与当前发布版本同口径 RT-FULL；本决定不授权
  自动部署、真实分析、生产数据写入或真实社交采集；部署另由 DAV-909 独立发布门执行。
- 实施候选为 `6612aea82e0fb3212d3682c5d09835f529ffec16`，直接父为
  `623c37a71f7a50fdf9945f9158f78cf9f57e5b0a`。DAV-908 的**代码审核员**同 SHA 复审 PASS；
  与线上发布基线 `026349614a3f1b92a95dc06c0515f10ebec193bc` 的 RT-FULL 为基线
  `19 failed / 4425 passed / 1 skipped / 3 deselected`、候选
  `19 failed / 4445 passed / 1 skipped / 3 deselected`，失败集合双向差集为空。候选已线性合入
  `origin/codex/dav-4-p2a-trunk`。随后按独立发布门完成 `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`
  的受控发布：预启动、优雅切换、`/healthz`、provider/行情/social disabled 烟测和数据库回读均通过；
  生产库未写入，未启动真实分析或真实天梯上游请求。

### D-023：生产 Compose 只保留运行所需源码挂载（有效，代码已合入）

- 生产 `docker-compose.yml` 只保留 `./data:/app/data`、`./api:/app/api` 和
  `./tradingagents:/app/tradingagents` 三项运行挂载；移除测试与脚本源码的
  `./tests:/app/tests`、`./scripts:/app/scripts` 挂载，减少生产容器暴露面。
- 本决定不改变 `Dockerfile`、`docker-entrypoint.py`、`docker-compose.split.yml`、端口、环境变量、
  数据库路径或重启策略；不允许借此卡构建镜像、启动/重启/部署、写生产库或采集真实数据。
- DAV-910 候选完整 SHA 为 `2daad463ff004dac97986e39983100be03599521`，直接父为
  `1e634c1f91f8f8b05b6a5754c1549c1d47030059`；**代码审核员**已对同一 SHA 只读 PASS，
  `docker compose config --quiet` 退出码为 `0`，候选已线性合入 `origin/codex/dav-4-p2a-trunk`。
- 本决定只记录代码合入，不等于线上部署；线上当前仍为 P1-F 发布 SHA `6cc4e152...`。P2-54
  前端 `.vade-report` 工件清理另行立卡，不与本决定合并。

### D-024：删除误提交的前端生成工件（有效，代码已合入）

- `frontend/.vade-report` 是一次性生成的报告工件，不是运行时输入；删除它不改变前端依赖、
  组件、构建配置或后端行为。
- DAV-911 候选完整 SHA 为 `80d87b5f1fd9b242b0ce22d430933fca5e07afe2`，直接父为
  `a6dfd86981491cc4452927d671625f42bc47056e`；完整变更清单只有
  `D frontend/.vade-report`。`代码审核员`已对同一完整 SHA 做只读 PASS，`git diff --check`
  通过，未执行测试并已如实记录 0 tests executed。
- 候选已以 `--ff-only` 线性合入 `origin/codex/dav-4-p2a-trunk`。本决定只记录工件清理与代码
  合入，不触发服务重启、部署、生产库写入、真实数据采集或凭据操作；线上继续运行
  `6cc4e152...`。

### D-025：同步 `uv.lock` 与 `pyproject.toml`（有效，代码已合入）

- 当前 `pyproject.toml` 已为项目版本 `0.6.0`，并声明 `python-dotenv` 与 dev 依赖
  `fakeredis`；旧 `uv.lock` 仍记录 `0.2.0`、保留 `langchain-experimental` 且缺少 `fakeredis`，
  `uv lock --check` 实测失败。P1-G 只修复这一锁文件漂移。
- DAV-913 候选完整 SHA 为 `054a76210f799029fe8d390512c763c36f0fb585`，直接父为
  `78d7b09a2e1f664d9d5e44ee581660a16ef734f3`；变更清单严格只有 `M uv.lock`，
  `git diff --check` 通过。**代码审核员**已对同一完整 SHA 做只读 PASS。
- 候选通过 `uv lock --check`（resolved 114 packages）和
  `uv sync --frozen --dry-run`（would install 106 packages），随后以 `--ff-only` 线性合入
  `origin/codex/dav-4-p2a-trunk`。除依赖声明对齐及其孤儿传递依赖清理外，没有无关依赖版本变动。
- 本决定只覆盖锁文件合入，不触发服务重启、部署、生产库写入、真实分析、真实社交采集或凭据操作；
  线上仍运行 `6cc4e152...`。

### D-026：前端历史 direction 只在显示层本地化（有效，代码已合入）

- 历史报告可能保留 `BULLISH`、`LEAN_BEARISH` 等英文方向；ChatCopilotPanel、AgentCollaboration
  和 HistoricalDebateDrawer 的用户可见入口统一复用 canonical `localizeDirection`，当前中文和未知
  值按既有语义保留，空值 fallback 不变。
- DAV-914 候选完整 SHA 为 `8ccecb8d59de31835f9d0f67578123db9a358e7b`，直接父为
  `59ec435306b8c2e49c5ec4a66d433db30df2450c`；实际变更严格为三个组件和三个测试文件。
  **代码审核员**已对同一完整 SHA 只读 PASS；合入后独立验证为 17 个测试文件/171 个测试通过，
  `npm run build` 成功，`git diff --check` 通过。
- 本决定只允许显示文本和显示颜色查找的变更，不改持久化方向值、业务决策、报告内容或 API。候选
  已以 `--ff-only` 线性合入目标主线；本决定本身不触发部署、重启、前端 live bundle 更新、生产库写入、
  真实分析或真实社交采集。随后由独立的 D-027 发布门完成上线。

### D-027：DAV-914 主线受控发布（有效，已完成）

- 发布对象为目标主线完整 SHA `79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`，代码父为 DAV-914 候选
  `8ccecb8d59de31835f9d0f67578123db9a358e7b`；发布前 SQLite 已备份并核对 `quick_check/integrity_check=ok`、
  reports `1409/793/616`，生产库 SHA 未变。
- 8001 预启动的 `/healthz` 精确匹配发布 SHA 后，旧 PID `29528` 优雅停止，新 PID `42225` 在
  `/private/tmp/ta-release-main-79757a6-20260914` 运行；8000 `/healthz`、provider health、只读行情、
  social disabled、Dashboard/新 bundle HTTP 200 和未知 API 404 均通过；8001 已关闭。
- 前端发布副本重建后为 17 个测试文件/171 个测试全过，生产构建成功；Vite 既有配置/包体提示已如实记录。
  本决定只完成代码发布和只读运行核验，不授权真实分析、生产库写入、真实社交采集、Cookie、信用加权或历史重写。

### D-028：LLM 模型名校验先定策略、后接运行链（有效）

- 当前 `validate_model()` 只作为 client 的显式能力检查存在，尚未被 `get_llm()` 或启动
  流程调用。现有设置页允许自由填写模型名，并支持 `/v1/models/fetch` 动态拉取；OpenAI
  兼容服务、OpenRouter、Ollama、DeepSeek 和角色级绑定也不能由一份静态列表完整代表。
- 因此不得直接把 `VALID_MODELS` 接成启动硬门禁，也不得把静态列表当作 provider 实时能力
  证明。先在独立窄卡中定义 advisory、provider discovery 与 warmup failure 的语义，
  再决定是否增加运行时告警或探测；已有合法自定义模型不能因旧列表而无法启动。
- 本决定不改变当前 API key 映射、Anthropic `base_url` 处理或 Google SDK 边界，不触发真实
  模型调用、生产库写入、凭据操作、部署或重启。实施代码仍须由**代码审核员**对同一完整
  SHA 只读审查并按风险执行回归。

### D-029：P2-55 先合入策略层与离线契约，运行接线另立窄卡（有效）

- 日期：2026-09-14
- P2-55 候选 `54bfb621250711571ba5a75b6dc64e0db6dcf645` 基于主线
  `133668c0a9ac39fbb806e1a6e3322824ff60b898`，直接父为
  `223f553c234b6244edd7fa760dc2dc710fd5dc2c`；DAV-921 由**代码审核员**对同一完整 SHA
  只读复审 PASS 后，已线性合入目标主线。
- 本阶段只落地模型校验策略、角色配置隔离、失败分类/凭据脱敏、兼容性修复和离线契约测试。
  固定环境相关测试为 `193 passed`，契约测试为 `62 passed`，代理变量压力复测仍为
  `62 passed`；没有调用真实模型、写生产库、部署或重启服务。
- 静态 `VALID_MODELS` 继续只能作为 advisory catalog；本次不把策略接入 `get_llm()`、启动
  流程或真实 warmup。若要接入，必须另立窄卡定义告警、动态发现、探活和 fail-closed 语义，
  不得把合法的自定义模型误判为不可用，也不得重复派 DAV-916/DAV-918。

### D-030：DAV-922 完成 LLM runtime 接线，但发布与真实 warmup 仍分开（有效）

- 日期：2026-09-14
- DAV-922 最终候选完整 SHA 为 `c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`，直接父为
  `807464e5784240554efac0f5c3d1110d00a450a1`；DAV-924 由**代码审核员**对同一完整 SHA
  只读复审 `PASS` 后，已线性合入 `origin/codex/dav-4-p2a-trunk`。DAV-923 的 R1 候选
  `807464e...` 因显式角色地址边界问题返修，不能作为最终合入依据。
- 运行时 probe/warmup 接入统一失败分类和敏感信息脱敏；角色级 provider/base_url 采用同厂商
  可继承、异构不隐式继承、显式地址（含与全局相同者）保留、纯空白视为未配置的规则。静态
  `VALID_MODELS` 仍不是启动或 `get_llm()` 硬门禁。
- 最终候选和合入后均以固定 Python 3.10 环境通过关联集合 `159 passed, 79 warnings`；工作区
  clean，`git diff --check` 通过。详见 `work/2026-09-14-p2-55b-runtime-wiring.md`。
- 本决定只覆盖代码审查、离线验证和线性合入，不覆盖部署、重启、真实模型/外部网络调用、生产
  数据库写入、真实社交采集、凭据操作或真实业务证据。线上仍运行
  `79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`；后续发布必须另走 D-013 的备份、启动、精确
  `/healthz`、只读烟测和数据库回读门。

### D-031：DAV-922 / P2-55b 受控发布（有效，已完成）

- 日期：2026-09-14。发布对象为目标主线完整 SHA
  `f094d6a78bc699fc6224e57164d38455c2ad55a9`；其代码变更来自 DAV-922 最终候选
  `c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`，中间仅追加回归证据和治理文档。
- 发布前 SQLite 已备份并核对 `quick_check/integrity_check=ok`，reports 为 `1409/793/616`；
  发布后生产库大小和 SHA-256 仍未变化。8001 预启动的 `/healthz` 精确匹配发布 SHA 后，旧
  PID `42225` 优雅停止，新 PID `59324` 在 `/private/tmp/ta-release-p2-55b-f094d6a-20260914`
  运行；8000 `/healthz`、provider health、只读行情、social disabled、Dashboard/bundle
  HTTP 200 和未知 API 404 均通过。
- 预启动期间未用默认密钥绕过安全闸；因新 worktree 没有旧发布副本的 `data` 软链接，发布进程
  显式使用已核对的生产库绝对路径。没有复制、迁移或写生产数据库，也没有更换凭据。
- 本决定只完成受控代码发布和只读运行核验，不授权真实模型 warmup、真实分析、生产报告写入、
  真实社交采集、Cookie、信用加权或历史重写。详见
  `work/2026-09-14-dav922-release-f094d6a.md`。

### D-032：撤销工作流改革，恢复原交付联动

- 日期：2026-09-17
- 状态：有效（编号自工作区 D-018 重编；D-018 已属 P1-D 七源证据摘要，故重编为 D-032）
- 用户明确授权：「行，就回归老流程。全面执行回退。」
- 恢复成员完成后主动精确 mention 项目调度助手，由调度助手继续派发审查、返修和推进依赖；禁止的是调度助手自 mention，不是其他成员唤醒他。
- 撤销改革新增的 Phase/GO 微授权、member-only 复审卡限制、强制 STRICT 双审及证据包前置审批。原有 D-012 真实测试、写审分离和 D-013 总工放行权保留。
- 不回退产品代码、测试成果、主线或生产数据，不恢复过期 Cursor 审批路由。
- 用户单独冻结的 DAV-998 重放、最终整合/合入及部署不因本次流程回退自动解锁。
- 改革试卡 DAV-1015/1016/1017/1018 已取消；已完成复审记录保留。改革工具及压缩包作为历史保留，不再作为施工前置条件。
- 角色恢复依据：`/tmp/multica-phase1-20260917-6H7La5/*-before.json`；此次回退前备份与回读结果：`/Users/davidliu/Documents/Codex/2026-09-17/workflow-rollback-20260917-193339/`。

### 当前不变的原则

- D-009 的状态拆分、PIT、证据独立性和统计排除原则仍有效；D-012 的红队完备性、逐条实跑和 RT-FULL 仍是行为类候选的硬门槛。
- 当前施工主干已包含 P2-55 策略层及 DAV-922 runtime 接线最终候选 `c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`；治理记录已随远端目标主线推送；线上运行代码发布对象为 `f094d6a78bc699fc6224e57164d38455c2ad55a9`（代码变更来自 DAV-922，代码父为 DAV-914 `8ccecb8d59de31835f9d0f67578123db9a358e7b`），并包含 P1-G 候选 `054a76210f799029fe8d390512c763c36f0fb585`、P1-F 候选
  `6612aea82e0fb3212d3682c5d09835f529ffec16`、P2-65 候选
  `2daad463ff004dac97986e39983100be03599521`、P2-54 候选
  `80d87b5f1fd9b242b0ce22d430933fca5e07afe2` 及其治理文档；线上服务运行发布 SHA
  `f094d6a78bc699fc6224e57164d38455c2ad55a9`。完整远端文档 HEAD、运行态、数据库计数和
  剩余工作以 `PROJECT_STATE.md` 及发布后回读为准。

## D-009：决策语义四元拆分优先于继续堆局部闸（已采纳）

- 日期：2026-08-27
- 状态：有效（P0/P1/P2-Gate4 与 Track A5–A12 已在主干 `98fe5d1`；生产未部署）
- 决定：
  1. 采纳 `work/2026-08-27-audit-decision-semantics-plan.md` 为**决策语义 / PIT / 回测污染**权威施工设计；日常派工入口为 `work/2026-08-27-decision-semantics-workflow.md`。
  2. 禁止把「上游失败 / 前视 / 证据冲突 / 方向未确认」坍缩为 Neutral、HOLD 或合格的 `completed` 样本。必须拆分 `analysis_status`、`direction`、`trade_action`、`risk_status`（及 `confirmation_state`）。
  3. 施工顺序：**P0（状态机 + EvidenceRecord + period_kind + 资金语义/cluster + 去人格化）→ P1（事件覆盖 / capitulation / 回测校准隔离 / provider 红灯）→ P2（社交 Task 5–15）**。与 `unified-final-plan` Track A 冲突时以本决定与审计稿为准。
  4. 社交基建可并行，但不得与 P0/P1 混 commit；active / 删 `legacy_proxy` 仍走既有 Gate；未过 Gate 不得宣称社交接入完成。
  5. 回测与校准只接收 `analysis_status=VALID` 且动作语义明确的样本；`INVALID/ABSTAIN/NO_TRADE/WAIT` 必须排除并计数。禁止价格不足时缩短 `hold_days`。
  6. R1/R2/R3 离线 fixture 齐备并通过前，不得声称历史案例“已修复”；只能声称设计可执行。
- 原因：本地核验 `300433.SZ@2026-05-06` 报告 `f8724342` 七分析师全 502 仍落库 `completed/HOLD/25`；`api/main` 仅认 BUY/SELL/HOLD；资金流 guard 写 `direction=中性`；校准只筛 lifecycle `completed`。局部闸无法消除统计污染。
- 影响：P0/P1/P2-Gate4 与 Track A5–A12 已合入至 tip `98fe5d199e8874ae829d2b492882d82339c836f0`（生产未部署）。加权仍保持关闭（`credit_weighting_enabled=False`），不改 3/1 轮次与用户模型绑定。主干合入仍严格执行 D-010 独立审核员 + Cursor「准予合入」流水线；未过 Cursor「准予部署」不得上线。旧 `decision` 字段可兼容，统计主键切新状态。

## D-010：主干合入与部署的最终验收权在 Cursor（已被 D-011/D-013/D-014 取代，仅存档）

> 本节保留历史流水线原文，不得据其中的 Cursor 或「独立代码审核员」名称派发新任务；当前规则见本文件顶部的 D-013/D-014。

- 日期：2026-08-28（补强：2026-08-30）
- 状态：仅存档；隔离分支、单关注点、只读审核和禁止带病 FF 等原则由后续决定继承
- 决定：
  1. David 指定 Cursor 为总控。Multica「项目主管」或「独立代码审核员」单独通过 **不足以** Fast-Forward `codex/dav-4-p2a-trunk` 或生产部署。
  2. **合入前强制流水线（缺一不可）**：
     1. 开发：隔离分支 + 单关注点 commit + 定向 pytest 证据 → `in_review`
     2. **独立代码审核员**（`aa01a41a-c3da-4021-9e45-a592ac77166c`）：对**完整 40 位候选 SHA**只读复审，书面给出 ✅通过 / ⚠️有条件通过 / ❌打回（须含文件路径与行号证据）
     3. **Cursor**：在独立 worktree 对**同一 SHA**复跑测试并做契约/白名单复核；仅当评论同时写出完整 40 位 SHA 与「准予合入」或「准予部署」时，才可开运维 FF/部署卡
  3. 独立审核员 PASS、项目主管「建议合入」、运维 pytest 绿，**均不得**直接 FF。禁止跳过步骤 2 直接由 Cursor「准予合入」代替独立审核员（紧急热修须在评论中显式写「跳过独立审核的理由」并经 David 口头确认——默认不允许）。
  4. 独立审核员与开发者不得互相改对方分支；审核卡只读。打回则开返修卡，禁止带病 FF。
  5. DAV-462 / DAV-464 在 Cursor 复审前已 FF，属过程事故；P2-T5…T11 曾缩成「仅 Cursor 隔离复测」——自本补强起恢复独立审核员闸，不作为免审先例。
- 原因：P0-1 曾在复审前被合入；近几刀社交卡为赶进度跳过独立审核员，削弱第二双眼睛的质量保障。
- 影响：调度助手不得把审核员 PASS 升级成合入。当前 **禁止部署**。下一张编码卡起必须挂独立审核步骤。

## D-008：社交 archive 时间分层与 append-only 快照

- 日期：2026-08-27
- 状态：有效（方案层；产品代码尚未实施）
- 决定：
  1. MediaCrawler `add_ts` 只映射为 `first_seen_at`；`last_modify_ts` 只映射为 `snapshot_at`。二者都是爬虫库务时间，不得解释为平台正文时间。
  2. 平台源时间：小红书用 `time` / `last_update_time`；抖音用 `create_time`。`last_update_time` 的可靠性单独验证，验证通过前不参与历史资格。
  3. 互动指标资格一律 `snapshot_at <= cutoff`。`ingest_at` 只用于导入审计，永不参与资格判断，也不得回填缺失时间。
  4. TradingAgents social archive 必须 append-only snapshot，不继承 MediaCrawler 对工作行的 update-in-place。
- 原因：钉住 SHA `d6f7c5bb` 下，`last_modify_ts` 由爬虫写入、注释写明是 DB 记录更新时间；XHS `update_content` 更新互动数与 `last_update_time` 但不更新 `desc`；DY 对已存在行逐字段覆盖。把库务时间当成源内容时间会把后补抓取和未来互动数带进历史分析。
- 影响：实施方案见 `docs/social_data/implementation_plan.md`。废止「`content_observed_at=add_ts` / `metric_observed_at=last_modify_ts` 当作源时间」的映射。未确认前不派 Multica。

## D-007：信用加权 flag 用户已预批准，但仍受门槛门禁

- 日期：2026-08-26
- 状态：有效
- 决定：用户口头批准开启 `credit_weighting_enabled`；**在 `verify_h1b_gates` 输出 `ELIGIBLE_FOR_ACTIVATION` 之前，生产端 flag 必须保持 `False`**（当前实测 `KEEP_FALSE`）。门槛通过后，无需再次征询即可把 flag 置为 `True` 并部署。
- 原因：D-006 与门槛草案要求系统级门槛全部通过后才允许加权；当前库 689 份报告仍未过 N/分侧/时间/平衡等多维门槛（单标的占比约 45%、行业数 0、多头占比约 87% 等）。
- 影响：批准记入决策账本；不改变默认 flag；继续积累合格周评样本与 `h1b_gate_samples` 注入路径。

## D-006：P3 H1b 激活门槛与分层隔离（已批准）

- 日期：2026-08-26
- 状态：有效
- 决定：采用 `work/p3-h1b-activation-gates-draft.md` 推荐默认值；架构取分层隔离（系统级门槛不过则全员 shadow；单模型偏置仅 clamp 该模型权重为 1.0，异常模型占比 >50% 才全局回 shadow）。`credit_weighting_enabled` 默认 false。
- 量化门槛摘要：N≥60 / 标的≥20 / 行业≥5；bull·bear 各≥25；≥45 自然日且≥30 交易日；T+5 完整率≥95%；多空比例∈[40%,60%]；Δverified≤18%；权重系数∈[0.85,1.15]。
- 原因：规格 §11.1 要求书面批准后方可加权；评估师 Conditional Pass 推荐路径 B 以兼顾可用性与鲁棒性。
- 影响：解锁 H1b 实施卡；未过门槛或关 flag 时不得影响总监裁决。

## D-001：Hermes 保持原位并作为原始历史来源

- 日期：2026-08-24
- 状态：有效
- 决定：不迁移、不覆盖、不清理 `~/.hermes` 中的 memory、session、SQLite 数据库或配置。Cursor 只读取仓库内整理后的共享上下文和脱敏归档。
- 原因：保持 Hermes 原有 session、memory 和 Multica 调度工作流不变，同时降低迁移损坏和隐私泄露风险。

## D-002：共享上下文采用分层读取

- 日期：2026-08-24
- 状态：有效
- 决定：固定读取顺序为 `AGENTS.md` → `PROJECT_STATE.md` → `DECISIONS.md`。历史会话仅在需要追溯时按关键词检索。
- 原因：让 Cursor 获得连续性，同时避免把完整历史放进 every-turn prompt，减少上下文噪声和旧指令污染。

## D-003：项目历史只保存相关、脱敏、可检索的副本

- 日期：2026-08-24
- 状态：有效
- 决定：只归档与 TradingAgents/Multica 工作直接相关的 Hermes 会话；排除 cron 和无关对话。导出必须使用 Hermes 脱敏，并在本地再次扫描常见凭据格式。
- 原因：共享项目证据与私人历史应严格分离。原始完整记录继续由 Hermes 数据和独立备份保存。

## D-004：不在进行中的 Multica 流水线上切换运行时

- 日期：2026-08-24
- 状态：有效
- 决定：本次只确认 Cursor runtime 可用，不改现有 Agent 绑定，不重启 daemon。任何切换必须等相关任务结束后另行明确授权并单独验证。
- 原因：运行时切换可能中断当前 issue 或改变执行环境，不能作为上下文迁移的附带操作。

## D-005：实时状态优先于交接文档

- 日期：2026-08-24
- 状态：有效
- 决定：`PROJECT_STATE.md` 只提供最近核验快照。分支、HEAD、脏工作树、issue、Agent 和 runtime 状态必须在每次开工前重新查询。
- 原因：避免后续 Agent 根据过期状态继续执行或覆盖他人工作。
