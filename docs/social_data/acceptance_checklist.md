# TradingAgents Social Data Real Ingestion & Rollout Acceptance Checklist

> **Target:** Track B-3 真实采集 / shadow / canary 验收方案与检查清单（执行不启用）  
> **Reference:** `docs/social_data/implementation_plan.md` §3, §4, §5, §10, §11, §12; D-008, D-009, D-010; DAV-648, DAV-649, DAV-650, DAV-651  
> **Baseline Commit:** `origin/codex/dav-4-p2a-trunk` @ `4fdcf8efa841c2a881d82429febd48578d544c94`  
> **Strict Operational Boundary:** **本卡只出方案与检查清单，不启动爬虫、不切 active、不部署、不改账号。**

---

## 1. 核心门槛与铁律（承接原计划，不降级）

根据 `docs/social_data/implementation_plan.md` 与 D-009 决策规范，真实上线必须逐级通过 Gate 0 至 Gate 4，原计划门槛一律保持，严禁任何降级：

1. **Gate 0 门槛**：
   - 必须钉死 MediaCrawler commit SHA: `d6f7c5bb906b6dac40ddf343ef9e26438a3de092`。
   - 强制使用 SQLite 作为 MediaCrawler 工作库（`save_data_option=sqlite`），强制环回地址 `127.0.0.1`，严格校验工作库表与列，任何非 SQLite 或未知格式一律非零退出，禁止 JSONL 空转。
   - 真实环境下小红书（xhs）与抖音（dy）各完成至少一轮真实采集并导入 archive，确认 archive 行数增加且工作库的原地更新绝不 `UPDATE` 已有快照行。
   - 凭据安全：Cookie、Token、手机号、密码绝对不入库、不入日志、不入 Git。
2. **Gate 1 门槛**：
   - 离线契约与时间五层分立：`published_at`（平台源发布时间）、`source_updated_at`（平台源修改时间）、`first_seen_at`（爬虫首次发现 `add_ts`）、`snapshot_at`（爬虫最后修改 `last_modify_ts`）、`ingest_at`（归档审计时间）。
   - 资格截断：正文资格严格由 `published_at` 与 `first_seen_at <= cutoff` 判定；互动数资格严格由 `snapshot_at <= cutoff` 判定；`ingest_at` 仅作归档审计，**绝不参与任何资格判定**。
   - XHS `last_update_time` 可靠性验证：遵循 `docs/social_data/xhs_last_update_time_verification.md` 结论，未明确标记为 trusted 前，资格判断一律忽略 `source_updated_at`。
3. **Gate 2 门槛（Shadow 灰度）**：
   - 环境变量 `TA_SOCIAL_MODE=shadow`。
   - 计算真实 `SentimentBundleV1` 并落入 `result_data.social_data_context`，保证全链路可追溯。
   - 强制锁定 `direction_allowed=false`：总监（`research_manager`）、证据核验（`evidence_verifier`）、质量门禁（`report_quality_gate`）严格拦截，**绝不得把社交分数当成多空方向证据**；最终交易结论与关闭社交时严格一致。
   - **人工覆盖抽检：必须覆盖 10 只股票（不同行业/市值/流动性特征）、至少 30 份真实分析报告，人工核验无方向漂移且缺口诚实，签署审核归档。**
4. **Gate 3 门槛（Active Canary 灰度，执行需另授权）**：
   - 环境变量 `TA_SOCIAL_MODE=active`，且必须通过 `TA_SOCIAL_CANARY_SYMBOLS` 限制在 **2–5 只白名单股票**。
   - 提示词隔离：`social_media_analyst` 与 `news_analyst` 双向彻底切断，NEWS/SOCIAL sentinel 互不泄漏；采用严格四段式结构。
   - 覆盖不足或无数据时正文明确输出“社交方向不可判断”，trace `direction_allowed=false`。
   - 同一输入（symbol/date）必须保证确定性 `bundle_id` 与分数。
5. **历史无当时快照防前视铁律**：
   - 历史回测或查询时，若 `cutoff` 前无可用快照，必须明确标为缺失（`REASON_SOCIAL_NO_HISTORICAL_SNAPSHOT`），**绝对禁止用当天或事后新采集的数据回填历史快照**。
6. **不可用 ≠ 市场冷淡（诚实缺口语义）**：
   - 社交数据缺失或基础设施不可用时，属于数据接入通道缺口，绝不允许被分析师或总监解读为“市场冷淡”、“无人关注”或“中性偏空”。
7. **D-009 铁律**：
   - **未逐级通过 Gate 0–3 前，绝对不得宣称社交接入完成。DAV-545 只证明 Gate 4 独立删除项在代码层面已提前就绪，绝不代表真实业务验收通过。**

---

## 2. 门禁完成态对照表（代码已交付 vs 真实未做）

| 门禁 | 验收细项 | 门槛标准 | 代码已交付状态（Code Delivered） | 真实未做状态 / 阻断点（Real-world Pending） | 验证依据 / 自动化套件 |
|---|---|---|---|---|---|
| **Gate 0** | MediaCrawler 钉 SHA | 锁定 `d6f7c5bb906b6dac40ddf343ef9e26438a3de092` | **已完成**：`run_social_ingestion.py`、`import_mediacrawler_social.py` 强制校验该 SHA；`runbook.md` 固化 CLI 参数映射 | **未做**：生产宿主机未拉取真实 MediaCrawler 仓库及安装 Python 3.11/uv 环境 | `tests/test_run_social_ingestion_guards.py`<br>`tests/test_import_mediacrawler_social_cli.py` |
| **Gate 0** | SQLite 强制校验与隔离 | 强制 `save_data_option=sqlite`；控制接口强制 `127.0.0.1` | **已完成**：参数校验器拒绝非 sqlite（如 jsonl）、拒绝非 loopback 地址，校验表与列白名单 | **未做**：生产真实运行生成真实物理 SQLite 工作库 | `tests/test_run_social_ingestion_guards.py` |
| **Gate 0** | 双平台真实导入首轮验证 | xhs/dy 各至少一轮；archive 行增加；旧快照不被 UPDATE | **已完成**：`MediaCrawlerImporter` 实现 4 表导入、空正文处理、非法时间拒收、哈希去重与 append-only；合成测试全过 | **未做（待授权）**：真实账号/Cookie 驱动真实爬虫抓取小红书与抖音，物理导入生产 `social_archive.db` 并核对行数 | `tests/test_mediacrawler_importer.py`<br>`tests/test_social_e2e_acceptance.py` |
| **Gate 0** | 凭据与敏感数据治理 | Cookie/Token 绝不落库、不入日志、不入代码 | **已完成**：`author_id_hash` 单向脱敏，过滤 `xsec_token`、nicknames 等；代码与日志不打印敏感信息 | **未做（待授权）**：测试账号 Cookie 注入外部受控安全目录（`~/.mediacrawler/cookies/`） | `tests/test_social_e2e_acceptance.py` |
| **Gate 1** | 时间字段五层分立 | `published_at` / `source_updated_at` / `first_seen_at` / `snapshot_at` / `ingest_at` | **已完成**：契约、表结构与资格函数全部实现；`ingest_at` 永不参与资格判定 | **已完成（离线代码）**：离线测试全量闭环；待真实数据入库后执行数据审计 | `tests/test_social_contracts.py`<br>`tests/test_social_as_of_guard.py` |
| **Gate 1** | XHS `last_update_time` 验证 | 未验证前忽略 `source_updated_at` 资格 | **已完成**：产出结论文档；代码默认未 trusted 时忽略该字段，防止正文未改互动变化时误判资格 | **未做**：生产大样本长期追踪真实内容变动与更新时间戳的相关性 | `docs/social_data/xhs_last_update_time_verification.md`<br>`tests/test_social_as_of_guard.py` |
| **Gate 2** | Shadow 模式链路追溯 | `TA_SOCIAL_MODE=shadow`；生成 bundle 并持久化 | **已完成**：`collector.py`、`analyst_adapter.py` 支持 shadow；`social_data_context` 贯穿 State/API/Report | **未做（待授权）**：生产环境配置 `TA_SOCIAL_MODE=shadow` 并重启应用服务 | `tests/test_social_rollout_modes.py`<br>`tests/test_report_social_context.py` |
| **Gate 2** | Shadow 方向锁定拦截 | `direction_allowed=false` 不得当多空证据 | **已完成**：适配层与总监层严格门禁，多空结论与关社交时完全一致；报告质量门禁严格把关 | **未做**：在线生产流量下的报告决策无偏移实际观测 | `tests/test_social_downstream_gates.py`<br>`tests/test_social_analyst_separation.py` |
| **Gate 2** | **人工覆盖抽检（硬门槛）** | **30 份分析报告 / 10 只股票** | **已完成（工具/框架）**：离线测试夹具已覆盖多 status 场景；状态 API 支持四分立诚实指标 | **未做（待授权）**：在真实 Shadow 运行下，人工抽检 10 只代表性股票、共 30 份报告，核验无方向污染并签署报告 | 人工核验表格签署与归档 |
| **Gate 3** | **Active Canary 灰度（待授权）** | **2–5 只白名单股票**；`TA_SOCIAL_MODE=active` | **已完成**：白名单过滤逻辑全覆盖，白名单外自动降级 disabled/not_applicable，绝不越权 | **未做（待授权）**：生产环境授权配置 `TA_SOCIAL_MODE=active` 与 `TA_SOCIAL_CANARY_SYMBOLS` | `tests/test_social_rollout_modes.py`<br>`tests/test_social_data_collector.py` |
| **Gate 3** | 提示词隔离与确定性 | 严格四段式；无新闻泄漏；bundle 确定性 | **已完成**：提示词重构完成，sentinel 双向物理隔离，覆盖不足标不可判断；bundle_id 确定性生成 | **未做**：真实生产在线模型生成抽检与稳定性监控 | `tests/test_social_analyst_separation.py`<br>`tests/test_analyst_prompts_deep_reasoning.py` |
| **通用** | 历史无快照防前视 | 缺失标缺失，**严禁当天新采回填** | **已完成**：PIT 资格函数筛选 `snapshot_at <= cutoff`，无快照返回 `no_historical_snapshot` 且不填数据 | **未做**：历史长周期批量回测中的实际快照完整性核查 | `tests/test_social_as_of_guard.py`<br>`tests/test_social_report_gap_regression.py` |
| **通用** | 诚实缺口语义 | 不可用 ≠ 市场冷淡 | **已完成**：缺口格式化与质量门禁锁定，不可用视为基础设施缺口，禁止推导多空倾向 | **未做**：生产偶发爬虫中断时的报告表现抽检 | `tests/test_social_report_gap_regression.py`<br>`tests/test_social_downstream_gates.py` |
| **Gate 4** | Legacy 清理与终态宣布 | 删除 `legacy_proxy`；D-009 守卫 | **代码已交付**：代码层在 DAV-545 已完成清理，disabled 返回 `not_applicable` | **未做（终态门禁）**：必须待 Gate 0、1、2、3 真实物理验收全过，方可宣布社交轨道全量完成 | 最终端到端验收签署 |

---

## 3. 待授权操作清单（需人工明确授权，禁止自主执行）

为确保生产稳定、网络合规与账号安全，下列 7 项物理操作属于**外部依赖与受控执行项**。当前开发阶段（Track B-3 / DAV-651）**绝对禁止自主执行**，必须在获得指定角色书面/工单明确授权后逐步推进：

| 授权编号 | 待授权操作项 | 涉及环境 / 资源 | 前置准入条件 | 审批授权角色 | 当前状态 |
|---|---|---|---|---|---|
| **AUTH-01** | **MediaCrawler 宿主机运行环境初始化** | 生产宿主机独立 Python 3.11 虚拟环境、Playwright/Chromium 依赖安装，检出钉住 commit `d6f7c5bb906b6dac40ddf343ef9e26438a3de092` | 仓库只读审计完成 | 运维主管 / 架构师 | 待授权（BLOCKED） |
| **AUTH-02** | **社交平台采集账号与 Cookie 注入** | 小红书与抖音受控测试账号，Cookie 注入 `~/.mediacrawler/cookies/`（非 Git 目录） | AUTH-01 完成，非商业合规确认 | 安全员 / 账号负责人 | 待授权（BLOCKED） |
| **AUTH-03** | **首轮真实网络小样本采集与归档导入** | 运行 `run_social_ingestion.py` 执行小样本受控抓取，并执行 `import_mediacrawler_social.py` 导入真实 `social_archive.db` | AUTH-02 完成，目标测试池确定 | 调度师 / 技术主管 | 待授权（BLOCKED） |
| **AUTH-04** | **生产服务环境变量切换为 Shadow 模式** | 设置生产环境 `TA_SOCIAL_MODE=shadow`，重启应用服务启动影子跟踪 | AUTH-03 验证 archive 行增加且旧快照无变更 | 运维主管 / 技术主管 | 待授权（BLOCKED） |
| **AUTH-05** | **Shadow 模式 30 份报告 / 10 只股票人工审计** | 业务与算法团队对 Shadow 产出的 30 份报告（涵盖 10 只代表性股票）进行人工全量核验并签署报告 | AUTH-04 连续平稳运行 3 天以上 | 业务负责人 / 审查员 | 待授权（BLOCKED） |
| **AUTH-06** | **生产服务开启 Active Canary 灰度模式** | 配置 `TA_SOCIAL_MODE=active` 及 `TA_SOCIAL_CANARY_SYMBOLS`（指定 2–5 只股票） | AUTH-05 人工审计全票通过 | 决策委员会 / 项目调度助手 | 待授权（BLOCKED） |
| **AUTH-07** | **全量 Active 上线与 Track B 正式验收收官** | 清空 Canary 白名单全量启用，关闭 Track B 父卡（DAV-648）并宣布上线 | AUTH-06 灰度运行无故障 7 天 | 项目调度助手 / 团队主管 | 待授权（BLOCKED） |

---

## 4. 人工覆盖抽检（Gate 2 验收操作指引）

当 **AUTH-04**（Shadow 模式开启）获得批准并生效后，执行人员与审查员必须依照下表执行 30 份报告人工审计：

### 4.1 样本股票池选取规则（10 只股票）
必须涵盖不同市值梯队、行业板块及社交关注度类型：
1. **高关注权重股（2 只）**：如 贵州茅台（600519.SH）、宁德时代（300750.SZ）
2. **科技成长题材股（3 只）**：如 寒武纪（688256.SH）、中芯国际（688981.SH）、中科曙光（603019.SH）
3. **传统周期/消费股（2 只）**：如 招商银行（600036.SH）、五粮液（000858.SZ）
4. **低关注度/冷门中小盘股（3 只）**：用于核验 `insufficient` 或 `empty` 缺口诚实性，确保无假情绪注入

### 4.2 每只股票审计 3 个不同日期/批次（共 30 份报告）
每份报告逐项核对以下 5 条铁律：
- [ ] **核验点 1（追溯完整）**：报告 `result_data.social_data_context` 字段存在，`bundle_id` 格式正确且与 `social_archive.db` 记录完全一致。
- [ ] **核验点 2（方向锁死）**：`social_data_context.direction_allowed` 为 `false`；报告正文未将社交数据作为多空判断依据，最终多空方向结论与关闭社交时完全一致。
- [ ] **核验点 3（缺口诚实）**：冷门股或未采集股票时，报告明示“社交数据暂无/不足”，严禁出现“市场情绪冷淡”、“资金观望中性”等主观臆测词句。
- [ ] **核验点 4（数据隔离）**：`sentiment_report` 中无新闻类 sentinel 或正文泄漏；`news_report` 中无小红书/抖音原生内容。
- [ ] **核验点 5（防前视核对）**：历史回测报告中，所有社交指标仅使用 `snapshot_at <= cutoff` 的快照，不存在未来指标污染。

---

## 5. 验收结论与状态声明

截至本卡（DAV-651 / Commit B-3）交付时点：
- **代码交付状态**：**100% 交付且全量自动化回归通过**（涵盖 Contracts、Archive、Importer、Provider、Aggregator、Collector、Adapters、Separation、Quality Gates、Downstream Guards 与 Status APIs 共 220+ 项定向与集成测试）。
- **真实运行状态**：**严格保持待授权状态**。爬虫进程未启动，生产模式未切换（保持默认 `disabled`），生产部署未执行，账号未配置。
- **后续调度流转**：本验收方案与检查清单已完全就绪，后续物理执行由项目调度助手根据 AUTH-01 ~ AUTH-07 授权清单按部就班推进。
