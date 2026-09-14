# PROJECT_STATE

> 最后核验：2026-09-14（Australia/Perth）。这是当前快照，不是历史施工记录的替代品。开工前仍须先 `git fetch origin`，再重新核对远端、服务、数据库和看板。旧计划中的 SHA、服务状态和卡状态不得直接沿用。

## 当前结论

- 远端施工主干 `origin/codex/dav-4-p2a-trunk` 当前为
  `9d702e7522c94bf3ac983cb1ede10943cfca1a4b`，直接父为
  `51b8b155ef3d47e99dead8ce4960562cd9a77c9e`；线上服务已切换到同一业务 SHA。
- P1-D（trader 与最终 risk_manager 的 bounded 七源一手证据摘要）已完成代码审核、同口径全量对照、线性合入并部署；E-04、V-03a provenance 返修、DAV-887 和 Fuyao E2b 窄修均在当前运行副本。
- P1-D 与上线前线上基线 `63d5648` 的同口径全量对照为：基线 18 failed / 4406 passed，候选 18 failed / 4415 passed；失败集合逐项一致，新增失败为 0，候选多通过 9 项。
- V-03a 已在生产库的隔离备份上完成只读进度基线，但不是正式收益实验，也不是“能否盈利”的结论；forward OOS 当前没有可评估样本。
- 生产库未被本轮部署和评估改写；信用加权、真实社交采集、社交 active 和历史重写均未执行。

## 运行态

| 项目 | 当前核验值 |
|---|---|
| 服务 PID | `6509`（uvicorn，父进程 `71186`；旧 PID `98772` 已优雅停止） |
| 服务工作目录 | `/private/tmp/ta-release-9d702e7-20260914` |
| `/healthz` | HTTP 200，`commit_sha=9d702e7522c94bf3ac983cb1ede10943cfca1a4b`，`build_identity` 同 SHA，`executor_queued=0`，`executor_threads=1` |
| 数据库 | `/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db` |
| 数据库完整性 | `PRAGMA quick_check=ok` |
| reports | 总数 1409；`status=completed` 793；`status=failed` 616 |
| 数据库写入保护 | 部署前后 reports 计数、文件大小和 SHA-256 未变；只读评估输入为隔离副本，不是运行中的库 |
| 本次部署备份 | `work/tradingagents.db.bak-20260914-predeploy-9d702e7`，与运行库 SHA-256 完全一致 |
| 部署回退点 | 旧发布 worktree `/private/tmp/ta-release-63d5648-20260914` 保留，供受控回退核验 |

本轮只读运行态烟测：provider health 返回正常；social-data 保持 `disabled`；历史 K 线接口返回数据。未发起分析任务，因此没有新增报告字段可作为生产业务链路证明。

## 已完成的施工线

### E-01 / E-02 / E-03 / E-04 / P1-D

- E-01 证据关系图生产者、E-02 reducer/消费接线和 DAV-808 custom_prompt 三层守卫已合入。
- E-03 多头、空头、证据核验、总监以及普通命题被反驳时恢复 `WAIT` 的修复已合入；PIT/前视失败继续 `NO_TRADE`。
- E-04 预期修正分栏、`cluster_id`、严格日历日期/时间戳和防伪造 hash 的返修已合入并随当前发布上线。
- P1-D 已把七个既有报告字段组合成固定顺序、单源上限和总长度上限的无 LLM 摘要，并接入 trader 与最终 risk_manager；候选 `9d702e7` 已通过 DAV-901 **代码审核员**同 SHA 复审和 RT-FULL 后上线。当前没有触发真实分析，因此尚无生产报告字段/trace/readback 证据。
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

- DAV-887（`4e6266b171b8431c1b979f7d70f0c075370c9b9d`）：TrackingBoard/Portfolio 的 raw direction 显示源代码已接入 `localizeDirection`；隔离副本前端 15 个测试文件/144 个测试通过，生产构建和入口 HTTP smoke 通过；live bundle 尚未部署核验。
- DAV-889（`8d42c466518fae6afa39e4e465886547bca04d84`）：Fuyao fundamentals 缺失 `curr_date` 不再回退到实时报告期。
- DAV-895（`b22e42d9ee3f185c67cc81f9f16fdbf30250d03d`）：Fuyao 龙虎榜首个 4001 不再被当作日期无数据而静默回退。
- DAV-898（`63d5648bca7c49f57e1211d848cbc1d02ff6b3a5`）：交易日历 fallback 优先读取 provider 配置 `fuyao_api_key`，再读环境变量。
- 仍开放：逐票披露日 PIT、`/limit-up-ladder` 能力补齐及其对应的来源/缺失语义；不能把“连板分布”写成已接入连板天梯。

## 当前剩余施工项（按依赖排序）

| 优先级 | 项目 | 当前状态 | 下一步边界 |
|---|---|---|---|
| P1 | 生产图与新字段业务证据 | 证据缺口，不是已确认故障 | 先取得明确的数据写入授权；做受控单次路径，记录 trace、报告和 readback。没有授权则保持只读，不补样本。 |
| P1 | V-03a 正式化 | 只能做进度基线 | 等待真实到期窗口；把 provider 元数据、forward OOS、动态 completeness、purge/embargo 和 prompt/model provenance 一起纳入正式实验。 |
| P1 | 真实社交 Gate 0–4 | 外部条件未满足，功能保持 disabled | 需要独立 MediaCrawler 环境、受控账号/Cookie 和逐级授权；不得用离线 fixture 代替真实采集。 |
| P1 | 裁决者一手证据 | 代码已合入并上线；尚无真实业务链路证据 | 不触发真实分析的前提下，保留代码/回归门禁；若要证明生产图可达，另走明确的数据写入授权和 trace/report/readback 取证。 |
| P1 | 财务披露日 PIT | 已开实施卡，待施工 | 以 `cn_fuyao` 首选路径为对象，按冻结的 verified 可见性、受限 peer 回退和 H1−Q1 派生契约施工；不扩到公告日已有实现、数据库或真实采集。 |
| P1 | 前端产品验收 | 源码、测试、构建和入口 HTTP smoke 已过；live bundle 未核验 | 在不改后端语义的前提下核对实际部署 bundle；不把临时 Vite 服务当成生产前端。 |
| P2 | standalone custom prompt 历史 | 报告 snapshot 已自包含；独立提示词版本仍不保留 | 如需补历史功能，另立卡；不得删除或重写既有报告 snapshot。 |
| P2 | worktree/历史工件清理 | 未授权 | 先只读盘点，再逐项取得清理授权；不得广泛 prune、reset 或删除证据。 |

## H1b / 信用加权红线

- `credit_weighting_enabled` 继续为 `False`（`KEEP_FALSE`）。
- 当前 V-03a 不是 H1b 解锁证据；不补写生产样本、不缩短 T+5、不改门槛比例、不把 `WAIT`/`NO_TRADE` 重新算成合格样本。
- `decision_model_version`、prompt hash、model snapshot 和服务 SHA 必须在正式重算时分层；历史缺失值不能未经授权批量回填。

## 已确认不应重复派工

- DAV-828/829/830/844/846–852/854、DAV-808/856/859、E-04 三轮返修、V-03a provenance 返修和 Fuyao 已列窄修均已有代码、审查或发布证据。
- 旧文档中“DAV-808 尚未决策”“E-04 尚未实现”“P0-B/C/D 仍待编码”“主干仍为 `bdb95f8` / 服务仍为 `a227cdc`”均是历史快照，不能据此新建重复卡。
- “全量无新增失败”只证明对应候选相对基线的测试差异；它不替代部署后的业务烟测、数据库回读或真实数据授权。

## 权威证据

- [全量回归与部署证据](work/2026-09-14-rt-full-63d-e2b.md)
- [P1-D 全量对照证据](work/2026-09-14-rt-full-9d-p1d.md)
- [P1-E 财务披露日 PIT 设计](work/2026-09-14-p1e-financial-pit-design.md)
- [V-03a 只读基线](work/2026-09-14-v03a-readonly-63d.md)
- [前端 DAV-887 验收](work/2026-09-14-frontend-dav887.md)
- [本次计划审计收口](work/2026-09-14-plan-audit-closeout.md)
- 当前决定见 `DECISIONS.md`；已知代码边界见 `docs/KNOWN_ISSUES.md`；实现细节以当前代码和卡内白名单为准。
