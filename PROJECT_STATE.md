# PROJECT_STATE

> 最后核验：2026-09-14（Australia/Perth）。这是当前快照，不是历史施工记录的替代品。开工前仍须先 `git fetch origin`，再重新核对远端、服务、数据库和看板。旧计划中的 SHA、服务状态和卡状态不得直接沿用。

## 当前结论

- 当前远端目标主线与治理记录已推送；线上运行发布对象为 `f094d6a78bc699fc6224e57164d38455c2ad55a9`。`f094d6a...` 的代码变更来自 DAV-922 最终候选 `c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`，其后只追加治理文档。P2-55 的策略层与运行时接线已受控发布，尚未调用真实模型或执行真实分析。生产库未被写入；当前远端完整 SHA 以开工时 `git fetch origin` 后回读为准。
- 2026-09-14 后续只读观察已记录于 `work/2026-09-14-runtime-observation.md`：目标主线回读为 `a24c7d7a68ed0ac5f3784965aa8f43ad463cc19b`，线上仍为 `f094d6a...`；两个固定回归标的各返回两日 K 线，生产库计数和完整性未变。Docker Desktop 上下文存在但 daemon socket 不可用，因此 Compose 容器级挂载核验仍未执行。
- 看板本次逐页核对为 924 总 / 840 done / 84 cancelled / 0 非终态；此前 4 张旧 blocked 卡已按替代关系取消，详见 `work/2026-09-14-board-stale-cards-closeout.md`。
- 施工主干已包含 P1-F 连板天梯候选 `6612aea82e0fb3212d3682c5d09835f529ffec16`（直接父
  `623c37a71f7a50fdf9945f9158f78cf9f57e5b0a`，根设计基线
  `d816a8c7c57c850ff2e1d57d852d3ad7f6e0d477`）及此前 P1-E 的治理文档和发布代码；此前线上
  发布版本为 `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`，已由本轮 `79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`
  受控发布替换。P1-F 已审查、RT-FULL、线性合入并受控发布；完整远端文档 HEAD 以 push 后回读为准。
- P1-D（trader 与最终 risk_manager 的 bounded 七源一手证据摘要）已完成代码审核、同口径全量对照、线性合入并部署；E-04、V-03a provenance 返修、DAV-887 和 Fuyao E2b 窄修均在当前运行副本。
- P1-D 与上线前线上基线 `63d5648` 的同口径全量对照为：基线 18 failed / 4406 passed，候选 18 failed / 4415 passed；失败集合逐项一致，新增失败为 0，候选多通过 9 项。
- P1-E 与线上基线 `9d702e7` 的同口径全量对照为：基线 18 failed / 4415 passed，候选 18 failed / 4426 passed；失败集合逐项一致，新增失败为 0，候选多通过 11 项。
- V-03a 已在生产库的隔离备份上完成只读进度基线，但不是正式收益实验，也不是“能否盈利”的结论；forward OOS 当前没有可评估样本。
- 生产库未被本轮部署和评估改写；信用加权、真实社交采集、社交 active 和历史重写均未执行。
- `/limit-up-ladder` 已按 D-022 完成实施、返修、**代码审核员**同 SHA 复审、RT-FULL、线性合入和受控发布；代码候选为
  `6612aea82e0fb3212d3682c5d09835f529ffec16`，此前发布版本为 `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`，现已随 `79757a6a2dd98f9487bb1fed6466ba71e7e6a31a` 继续运行。已完成只读运行烟测，但尚未调用真实天梯上游或真实分析入口。
- P2-65（DAV-910）已完成：只移除生产 `docker-compose.yml` 的 `./tests:/app/tests` 与
  `./scripts:/app/scripts` 两项源码挂载，保留 `data/api/tradingagents` 三项运行挂载；候选
  `2daad463ff004dac97986e39983100be03599521` 经**代码审核员**同 SHA 只读 PASS、Compose 配置
  校验通过后已线性合入目标主线，并进入当前发布代码树；本次服务使用 uvicorn 发布副本而非
  Compose 容器，挂载效果尚未做容器级运行核验；详见 `work/2026-09-14-p2-65-compose-mounts.md`。
- P2-54（DAV-911）已完成：只删除误提交的 `frontend/.vade-report` 一次性工件；候选
  `80d87b5f1fd9b242b0ce22d430933fca5e07afe2` 经**代码审核员**同 SHA 只读 PASS 后，已从
  `a6dfd86981491cc4452927d671625f42bc47056e` 线性合入目标主线。它没有运行时引用，不需要
  单独重启或部署；详见 `work/2026-09-14-p2-54-vade-report.md`。
- P1-G（DAV-913）已完成：`uv.lock` 已与当前 `pyproject.toml` 的项目版本和依赖声明对齐；候选
  `054a76210f799029fe8d390512c763c36f0fb585`（直接父 `78d7b09a2e1f664d9d5e44ee581660a16ef734f3`）
  仅修改 `uv.lock`，经**代码审核员**同 SHA 只读 PASS、`uv lock --check` 和
  `uv sync --frozen --dry-run` 通过后已线性合入。锁文件不改变当前运行代码，不单独部署；详见
  `work/2026-09-14-p1-g-uv-lock.md`。
- DAV-914 已完成：三个前端 secondary direction 展示入口统一复用 `localizeDirection`；候选
  `8ccecb8d59de31835f9d0f67578123db9a358e7b`（直接父 `59ec435306b8c2e49c5ec4a66d433db30df2450c`）
  只改 6 个前端组件/测试文件，经**代码审核员**同 SHA 只读 PASS、合入后前端 17 个测试文件
  /171 个测试和生产构建通过后已线性合入目标主线，并随 `79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`
  受控发布。详见 `work/2026-09-14-p2-direction-localization.md` 与
  `work/2026-09-14-dav914-release-79757a6.md`。

## 运行态

| 项目 | 当前核验值 |
|---|---|
| 服务 PID | `59324`（uvicorn，父进程 `71186`；旧 PID `42225` 已优雅停止） |
| 服务工作目录 | `/private/tmp/ta-release-p2-55b-f094d6a-20260914` |
| `/healthz` | HTTP 200，`commit_sha=f094d6a78bc699fc6224e57164d38455c2ad55a9`，`build_identity` 同 SHA，`executor_queued=0`，`executor_threads=1` |
| 数据库 | `/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db` |
| 数据库完整性 | `PRAGMA quick_check=ok` |
| reports | 总数 1409；`status=completed` 793；`status=failed` 616 |
| 数据库写入保护 | 部署前后 reports 计数、文件大小和 SHA-256 未变；只读评估输入为隔离副本，不是运行中的库 |
| 本次部署备份 | `work/tradingagents.db.bak-20260914-predeploy-f094d6a`，SQLite `quick_check/integrity_check=ok`，reports `1409/793/616`；备份 SHA-256 为 `94d2f6740db4f2065100479dd5cb3ccf5d8a504447a55fa8f19635927ce83010` |
| 部署回退点 | 旧发布 worktree `/private/tmp/ta-release-p1f-6cc4e-20260914` 保留，供受控回退核验 |

本轮只读运行态烟测：provider health 返回正常；social-data 保持 `disabled`；历史 K 线接口返回数据；前端 Dashboard 与新 bundle HTTP 200。未发起分析任务，因此没有新增报告字段可作为生产业务链路证明。

## 已完成的施工线

### E-01 / E-02 / E-03 / E-04 / P1-D

- E-01 证据关系图生产者、E-02 reducer/消费接线和 DAV-808 custom_prompt 三层守卫已合入。
- E-03 多头、空头、证据核验、总监以及普通命题被反驳时恢复 `WAIT` 的修复已合入；PIT/前视失败继续 `NO_TRADE`。
- E-04 预期修正分栏、`cluster_id`、严格日历日期/时间戳和防伪造 hash 的返修已合入并随当前发布上线。
- P1-D 已把七个既有报告字段组合成固定顺序、单源上限和总长度上限的无 LLM 摘要，并接入 trader 与最终 risk_manager；候选 `9d702e7` 已通过 DAV-901 **代码审核员**同 SHA 复审和 RT-FULL 后上线。当前没有触发真实分析，因此尚无生产报告字段/trace/readback 证据。
- P1-E DAV-902 已由**代码审核员**同 SHA 审查、关联回归和 RT-FULL 通过后合入，并随发布版本
  `026349614a3f1b92a95dc06c0515f10ebec193bc` 上线；发布后只做只读烟测，尚无生产财务报告的
  披露日 PIT trace/report/readback 证据。
- E-04 代码的全量无新增失败已由上述同口径 RT-FULL 证明；但仍需单独积累真实业务报告的字段/trace/readback 证据，不能把离线回归当作生产行为证明。

### P0-B/C/D 审计缺口

这些不是当前待重新施工的代码项：

- P0-B 博弈论接线硬化已在 DAV-881（`dd42437b90dc61ab29527d118004faf639988abc`）完成；接线失败不再只是 warning 后静默返回，已有真实 graph builder 与失败路径测试。
- P0-C/D V-03a 股票池元数据、ST/新股 unknown 处理、显式 OOS 上界和动态 completeness 已在 DAV-882（`565be982cae17ec6b14765d56015a79945ec068b`、`4f1a1aa3b643c960cfb8b56408c4c2a993bf8fc3`）完成，并通过同 SHA 审查和全量对照。
- 仍未闭环的是运行证据：生产历史中 `game_theory_report` / `game_theory_signals` 曾全部为空；要证明新部署后的生产图可达，必须有受控业务路径的 trace、报告字段、持久化回读三者一致证据。没有数据写入授权前不得用生产分析补样本。

### V-03a 评估器

- provenance 返修已合入并随 `63d5648` 部署；评估器和 runner 版本与服务 SHA 可追溯。
- 本轮隔离副本结果：232 份 completed 报告输入；方向候选 228；进入样本池 217；评估 83；typed missing 10；收益样本 76；覆盖率 38.25%；可评估率 100%；历史 OOS 210 条（评估 76）；forward OOS 0 条；回归标的 21。
- 统计读数：mean net return `-0.004822`（约 -0.48%），mean excess `-0.005439`（约 -0.54%），win rate `0.5526`。这些只是半成品进度基线，不能作投资或盈利判断。
- 七种消融共享 snapshot hash `65e0fe6274d2c287f6dffe926113011449046ccb9b6a00c8166f54168d181640`，均未产生 forward OOS；不能据此声称机制差异已被识别。

### Fuyao / D 线窄修

- DAV-887（`4e6266b171b8431c1b979f7d70f0c075370c9b9d`）：TrackingBoard/Portfolio 的 raw direction 显示源代码已接入 `localizeDirection`；发布副本前端 15 个测试文件/144 个测试通过，生产构建和 live bundle 入口 HTTP smoke 通过。当前 bundle 指纹见 `work/2026-09-14-frontend-live-bundle.md`；后续发布必须重建并复验。
- DAV-889（`8d42c466518fae6afa39e4e465886547bca04d84`）：Fuyao fundamentals 缺失 `curr_date` 不再回退到实时报告期。
- DAV-895（`b22e42d9ee3f185c67cc81f9f16fdbf30250d03d`）：Fuyao 龙虎榜首个 4001 不再被当作日期无数据而静默回退。
- DAV-898（`63d5648bca7c49f57e1211d848cbc1d02ff6b3a5`）：交易日历 fallback 优先读取 provider 配置 `fuyao_api_key`，再读环境变量。
- DAV-902（`cd7456012fe2e0b03bd33333e6972301c1c76ddc`）：Fuyao 财务链已按披露日和报告期做 fail-closed 可见性约束；DAV-903 已由**代码审核员**同 SHA 通过，RT-FULL 相对 `9d702e7` 零新增失败。代码已合入并随 `026349614a3f1b92a95dc06c0515f10ebec193bc` 受控发布。
- DAV-905/DAV-907（`6612aea82e0fb3212d3682c5d09835f529ffec16`）：Fuyao 连板天梯已按固定当前窗口、单一来源、fail-closed 和独立背景字段实现；DAV-908 **代码审核员**同 SHA 复审 PASS，RT-FULL 相对线上发布版本新增失败为 0，已合入主线并随 `6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1` 受控发布。
- 仍开放：生产财务报告的披露日 PIT 业务证据、P1-F 的真实天梯上游/业务链路证据，以及其他真实业务链路证据；不能把只读烟测写成真实分析路径已调用。

P1-F 真实上游的隔离只读探测已于 2026-09-14 执行：因发布环境没有 `fuyao_api_key` 或
`FUYAO_API_KEY`，provider 在 HTTP 请求前明确拒绝，本次请求计数为 0；未写生产库、未跑真实
分析。详见 `work/2026-09-14-p1f-live-probe.md`。补齐受控 key 后仍需重新探测，不能把模拟红队
和 RT-FULL 当作真实上游证据。

### P2 前端 direction 展示

- DAV-887 已覆盖 TrackingBoardPanel 与 Portfolio；DAV-914 覆盖 ChatCopilotPanel 的完成/恢复/通知、AgentCollaboration 的 verdict 标签和 HistoricalDebateDrawer 的总监裁决方向。
- DAV-914 候选已由**代码审核员**对同一完整 SHA `8ccecb8d59de31835f9d0f67578123db9a358e7b` 只读 PASS，合入后独立前端全量为 17 个测试文件/171 个测试通过，构建成功；Vite 既有配置/包体提示已如实保留。
- 该项只改变用户可见文本与颜色查找，不迁移或改写历史方向值；已随 `79757a6...` 发布并完成 live bundle HTTP 200/资源 hash 核验。后续发布仍须重建并复验 bundle，不能沿用本次 hash。

### P2-55 LLM client 遗留 TODO 复核与策略层合入

- `tradingagents/llm_clients/TODO.md` 的四条旧描述已完成核对；P2-55 候选
  `54bfb621250711571ba5a75b6dc64e0db6dcf645` 已线性合入目标主线，并由**代码审核员**在
  DAV-921 对同一完整 SHA 只读复审 PASS。候选基于 `133668c0a9ac39fbb806e1a6e3322824ff60b898`，
  直接父为 `223f553c234b6244edd7fa760dc2dc710fd5dc2c`，变更严格限于卡面 8 个白名单文件。
- 本次落地的是策略层和离线契约：`evaluate_model_policy`、角色级配置隔离、provider
  catalog/custom endpoint/discovery 状态、失败分类与统一凭据脱敏，以及旧
  `validate_model()` 对未知 provider 空模型的兼容行为。固定 Python 3.10 环境下相关测试
  `193 passed`，契约测试 `62 passed`，注入代理变量后契约测试仍为 `62 passed`；另有独立
  短 Bearer、Base64 字符集和未知 provider 空模型探针通过。
- `VALID_MODELS` 仍不得充当启动硬门禁；本次没有把策略接入 `get_llm()`、启动流程或真实
  warmup，没有调用真实模型、写生产库或部署。若要接入运行链，必须另立窄卡定义告警/探活
  时机和失败语义，不得重复派 DAV-916/DAV-918。

### P2-55b DAV-922 LLM runtime 校验接线与失败语义收口

- 最终候选 `c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`（直接父
  `807464e5784240554efac0f5c3d1110d00a450a1`）已由**代码审核员**在 DAV-924 对同一完整 SHA
  只读复审 `PASS`，并以 `--ff-only` 线性合入目标主线；R1 的 DAV-923 候选因显式角色地址
  边界问题返修，未作为最终版本合入。
- 变更严格限于 7 个白名单文件。运行时 probe/warmup 现在统一使用失败分类和凭据/敏感参数
  脱敏；角色级 provider 地址遵守同厂商继承、异构隔离、显式同地址保留和纯空白未配置规则。
- 固定 Python 3.10 环境的关联集合在最终候选与合入后均为 `159 passed, 79 warnings`，合入后
  耗时 `31.36s`；工作区 clean、`git diff --check` 通过。详见
  `work/2026-09-14-p2-55b-runtime-wiring.md`。
- 代码合入、离线证据和受控发布均已完成；线上目前为 `f094d6a...`。发布过程没有调用真实模型
  或执行真实分析，仍没有真实模型 warmup 或生产业务报告证据。

## 当前剩余施工项（按依赖排序）

| 优先级 | 项目 | 当前状态 | 下一步边界 |
|---|---|---|---|
| P1 | 生产图与新字段业务证据 | 证据缺口，不是已确认故障 | 先取得明确的数据写入授权；做受控单次路径，记录 trace、报告和 readback。没有授权则保持只读，不补样本。 |
| P1 | V-03a 正式化 | 只能做进度基线 | 等待真实到期窗口；把 provider 元数据、forward OOS、动态 completeness、purge/embargo 和 prompt/model provenance 一起纳入正式实验。 |
| P1 | 真实社交 Gate 0–4 | 外部条件未满足，功能保持 disabled | 需要独立 MediaCrawler 环境、受控账号/Cookie 和逐级授权；不得用离线 fixture 代替真实采集。 |
| P1 | 裁决者一手证据 | 代码已合入并上线；尚无真实业务链路证据 | 不触发真实分析的前提下，保留代码/回归门禁；若要证明生产图可达，另走明确的数据写入授权和 trace/report/readback 取证。 |
| P1 | 财务披露日 PIT | 代码已审查、RT-FULL 通过、已合入并发布；尚无生产业务证据 | 发布版本 `0263496` 已通过备份、健康检查、只读烟测和数据库回读；若要证明生产图/报告行为，另走明确的数据写入授权，不把只读烟测当成业务样本。 |
| P1 | 前端产品验收 | 当前发布副本的源码、测试、构建和 live bundle HTTP smoke 已过 | 后续版本发布时重建并复验 bundle；当前证据不覆盖浏览器交互、登录或真实分析业务。 |
| P1 | `/limit-up-ladder` 能力 | 代码已合入并受控发布；尚无真实业务样本 | 候选 `6612aea...` 已通过 DAV-908 **代码审核员**同 SHA 复审、RT-FULL，并随 `6cc4e15...` 发布；后续真实上游调用仍须按设计的日期/来源/失败语义取证，不改 `get_zt_pool` 或交易信号。 |
| P2 | standalone custom prompt 历史 | 报告 snapshot 已自包含；独立提示词版本仍不保留 | 如需补历史功能，另立卡；不得删除或重写既有报告 snapshot。 |
| P2 | LLM client 模型校验策略与运行接线 | 策略层与 DAV-922 运行时接线已合入并随 `f094d6a...` 受控发布；同口径 RT-FULL 相对线上基线失败集合差集为 0，`get_llm()`/启动硬门禁仍未启用，也没有真实 warmup 证据 | 发布门已完成；后续只做受控只读核验，若要取得真实 warmup/业务报告证据需另按数据写入边界执行。不得把静态白名单变成硬门禁，也不得重复派 DAV-916/DAV-918/DAV-921/DAV-922。 |
| P2 | 生产 Compose 测试/脚本源码挂载 | DAV-910 已合入并进入当前发布代码树；本次服务不是 Compose 容器 | 若实际使用 Compose，另做容器级 config/挂载核验；当前 Docker daemon 不可用，未启动容器；不把 uvicorn 发布当作 Compose 运行证据。 |
| P2 | worktree/历史工件清理 | 未授权 | 先只读盘点，再逐项取得清理授权；不得广泛 prune、reset 或删除证据。 |

2026-09-14 只读盘点：Git 共登记 198 个 worktree，其中 135 个元数据标记为 `prunable`，63 个
仍为非 prunable；另发现 90 个 unreachable commit、206 个 unreachable tree、116 个 unreachable
blob 及 1 个临时 garbage object。没有执行清理；详见
`work/2026-09-14-worktree-artifact-inventory.md`。

## H1b / 信用加权红线

- `credit_weighting_enabled` 继续为 `False`（`KEEP_FALSE`）。
- 当前 V-03a 不是 H1b 解锁证据；不补写生产样本、不缩短 T+5、不改门槛比例、不把 `WAIT`/`NO_TRADE` 重新算成合格样本。
- `decision_model_version`、prompt hash、model snapshot 和服务 SHA 必须在正式重算时分层；历史缺失值不能未经授权批量回填。

## 已确认不应重复派工

- DAV-828/829/830/844/846–852/854、DAV-808/856/859、E-04 三轮返修、V-03a provenance 返修和 Fuyao 已列窄修均已有代码、审查或发布证据；DAV-902/903 已完成代码、回归和受控发布门禁。
- DAV-910/P2-65 已完成单文件 Compose 清理、**代码审核员**同 SHA 审查和线性合入；不得重复派工。变更已进入当前发布代码树，但本次服务不是 Compose 容器，挂载效果仍未做容器级运行核验。
- DAV-911/P2-54 已完成单文件前端工件清理、**代码审核员**同 SHA 审查和线性合入；不得重复派工。它不改变线上服务行为，也没有单独部署动作。
- DAV-913/P1-G 已完成单文件 `uv.lock` 对齐、**代码审核员**同 SHA 审查和线性合入；不得重复派工。它不改变运行时代码，不需要单独部署。
- DAV-914 已完成三个剩余前端 direction 展示入口的本地化、**代码审核员**同 SHA 审查、合入后前端测试与构建，并已随 `79757a6...` 受控发布；不得重复派工。后续发布需按发布门重建/复验前端 bundle。
- DAV-916/DAV-918/DAV-921 已完成 P2-55 策略层实施、两轮兼容/安全返修、**代码审核员**对最终 SHA `54bfb621250711571ba5a75b6dc64e0db6dcf645` 的同 SHA PASS、相关回归和线性合入；不得重复派工。运行链接线仍是独立后续边界。
- DAV-922/DAV-923/DAV-924 已完成 P2-55b 运行时接线、R1/R2 复审、RT-FULL 和最终 SHA `c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699` 的线性合入；最终复审由**代码审核员**完成，R1 候选已被 R2 修复版取代。随后已按 D-031 随主线 tip `f094d6a...` 受控发布；不得重复派工。真实 warmup/生产业务证据仍未取得。
- DAV-892/DAV-894、DAV-906、DAV-917 是旧候选流程残卡，已确认无运行任务并转为 `cancelled`；对应有效交付分别以 DAV-895、DAV-908、DAV-918/DAV-921 为准，不得从旧卡重派。
- 旧文档中“DAV-808 尚未决策”“E-04 尚未实现”“P0-B/C/D 仍待编码”“主干仍为 `bdb95f8` / 服务仍为 `a227cdc`”均是历史快照，不能据此新建重复卡。
- “全量无新增失败”只证明对应候选相对基线的测试差异；它不替代部署后的业务烟测、数据库回读或真实数据授权。

## 权威证据

- [全量回归与部署证据](work/2026-09-14-rt-full-63d-e2b.md)
- [P1-D 全量对照证据](work/2026-09-14-rt-full-9d-p1d.md)
- [P1-E 财务披露日 PIT 设计](work/2026-09-14-p1e-financial-pit-design.md)
- [P1-E 同口径全量回归](work/2026-09-14-rt-full-p1e-cd7456.md)
- [V-03a 只读基线](work/2026-09-14-v03a-readonly-63d.md)
- [前端 DAV-887 验收](work/2026-09-14-frontend-dav887.md)
- [前端 live bundle 验收](work/2026-09-14-frontend-live-bundle.md)
- [本次计划审计收口](work/2026-09-14-plan-audit-closeout.md)
- [P1-F 连板天梯接入设计](work/2026-09-14-p1f-limit-up-ladder-design.md)
- [P1-F 实施、复审与 RT-FULL 收口](work/2026-09-14-p1f-merge-rt-full-6612aea.md)
- [P1-F 受控发布收口](work/2026-09-14-p1f-release-6cc4e.md)
- [P2-65 Compose 挂载清理与合入证据](work/2026-09-14-p2-65-compose-mounts.md)
- [P2-54 前端工件清理与合入证据](work/2026-09-14-p2-54-vade-report.md)
- [P1-G `uv.lock` 对齐与合入证据](work/2026-09-14-p1-g-uv-lock.md)
- [DAV-914 前端 direction 展示本地化与合入证据](work/2026-09-14-p2-direction-localization.md)
- [DAV-914 受控发布与 live bundle 证据](work/2026-09-14-dav914-release-79757a6.md)
- [P2-55 LLM client 遗留 TODO 复核](work/2026-09-14-p2-55-llm-client-audit.md)
- [P2-55 LLM client 策略层最终合入证据](work/2026-09-14-p2-55-llm-client-final.md)
- [P2-55b DAV-922 runtime 接线合入证据](work/2026-09-14-p2-55b-runtime-wiring.md)
- [DAV-922 全量回归对照证据](work/2026-09-14-rt-full-p2-55b.md)
- [DAV-922 受控发布收口](work/2026-09-14-dav922-release-f094d6a.md)
- [看板旧 blocked 卡收口](work/2026-09-14-board-stale-cards-closeout.md)
- [运行态只读观察](work/2026-09-14-runtime-observation.md)
- 当前决定见 `DECISIONS.md`；已知代码边界见 `docs/KNOWN_ISSUES.md`；实现细节以当前代码和卡内白名单为准。
