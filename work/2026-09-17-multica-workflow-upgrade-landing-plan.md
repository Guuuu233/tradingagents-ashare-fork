# Multica 工作流升级盘点与落地方案（已撤销，仅供历史查证）

> 用户已明确授权全面回退本次流程改革。下文 Phase/GO、禁止成员完成后 mention 调度助手、member-only 复审创建者校验、改革新增 STRICT 双审均不再作为工作要求。恢复改革前成员交付→主动 mention 项目调度助手→调度后续审查/返修的流程；调度助手不得自 mention。原有精确 SHA、写审分离、D-012 测试和 D-013 授权继续有效；当前单独冻结的 DAV-998 重放、最终合入/部署不自动解锁。产品代码及测试成果不回退。工具和证据包保留作历史，不再构成业务前置审批。

日期：2026-09-17

性质：流程设计与实施规格

边界：本文件不构成任何候选合入、部署或生产变更授权

## 0. 执行摘要

当前效率瓶颈不是模型额度，而是调度噪声和证据反复失效：评论会唤醒新 run、同一 issue 可出现多个执行者、规格靠累积评论传递、复审卡可被调度助手自行创建、结论与候选 SHA 绑定不严，以及昂贵全量测试在廉价门禁之前启动。

Devin SWE-2 无限额度适合扩大并行度，但只能并行无写冲突的工作。新的默认模式应为：

1. 一个候选分支同一时刻只有一个写入者；
2. 诊断、实现、复审、回归、合入、部署是分离的门禁；
3. 规格只有一个权威版本，执行者必须显式 ACK；
4. 评论只用于证据归档，不再承担调度；
5. 精确 SHA 冻结后，可并行开展只读复审、回归预检和证据核对；
6. 任何昂贵测试之前先跑秒级的 SHA、工作树、白名单和格式门禁；
7. 结论必须由证据数据生成，禁止硬编码 PASS 文案。

建议按“备份基线 → 角色边界 → 低风险试卡 → 自动化工具 → 真实工作”五步落地。先更新角色边界和投递纪律，再用低风险试卡验证触发语义；验证通过后引入规格版本、候选清单和稳定指纹，最后才用于 DAV-998 重放与卡 C 等真实工作。

## 1. 现状盘点

### 1.1 实时状态（2026-09-17 核验）

以下 issue 均无 running/queued run：

| Issue | 状态 | 说明 |
|---|---|---|
| DAV-995 | `in_review` | 旧离线网络护栏线 |
| DAV-998 | `in_review` | 独立候选 `33bc23d`，仍禁止合入 |
| DAV-1003 | `blocked` | 旧整合线已停工 |
| DAV-1007 | `in_review` | 卡 A，候选 `8589e65` |
| DAV-1009 | `in_review` | 卡 B，A+B 候选 `603a92e` |
| DAV-1011 | `cancelled` | 重复复审卡 |
| DAV-1012 | `done` | 旧 SHA `4cc436a` 的复审 |
| DAV-1013 | `done` | 调度助手自行创建的 `603a92e` 复审，旁证 |
| DAV-1014 | `done` | David 创建的 `603a92e` 正式复审 |

主线、生产服务、数据库和候选的合入状态不因本方案改变。

### 1.2 当前角色与模型

| 角色 | 当前模型 | 适合承担的工作 | 当前主要缺口 |
|---|---|---|---|
| 项目调度助手 | Devin DeepSeek V4.1 Flash | 看板协调、状态机推进、去重 | instructions 明确允许其创建复审卡，与当前治理目标冲突 |
| 资深开发1/2 | Devin SWE-2 | 实现、定向测试、诊断 | 缺少全局 SPEC_VERSION、白名单和禁止 amend 的统一规则 |
| 高级开发·支援 | Devin SWE-2 | 独立补位、窄范围返修 | 与主开发的边界需要靠 issue 规格明确 |
| 代码审核员/2 | Devin SWE-2 | 精确 SHA 的增量只读复审 | 缺少“复审卡必须由主控创建”和统一结论格式 |
| 独立代码审核员 | Devin SWE-2 | 发布前只读审核 | instructions 仍残留已作废的 Cursor 终审措辞 |
| 代码复核员 | Gemini 3.8 Flash High | 异构第二视角、存量复核 | 目前定位偏存量扫描，尚未定义 STRICT 候选第二复审职责 |

平台没有按角色限制 `issue create/assign/status/comment` 的细粒度 ACL。当前约束主要依赖 instructions，因此必须同时设置主控侧 preflight，不能只靠角色自律。

### 1.3 已核实的触发语义

| 动作 | 已知行为 | 新流程要求 |
|---|---|---|
| `issue assign` | 默认启动 run；支持 `--no-start` | 非明确启动时一律带 `--no-start` |
| `issue status` | 默认可能启动 run；支持 `--no-start` | 纯状态流转一律带 `--no-start` |
| `issue update` | 默认可能启动 run；支持 `--no-start` | 更新规格一律带 `--no-start` |
| `issue rerun` | 显式重新入队 | 仅作为一次性启动动作 |
| `steer` | 注入当前 steerable run，不新建 run | owner 正在运行时的唯一纠偏通道 |
| `issue comment add` | 本项目多次实测会产生 comment-triggered run | 仅用于最终交付/裁定归档；发后必须检查重复队列 |
| `issue metadata set` | CLI 支持 KV，但是否触发 run 尚未实测 | 先在试卡验证，未验证前不得作为唯一权威规格源 |

评论触发存在数秒延迟。一次检查为零不能证明后续不会出现 queued run；需要有界的两段式复查。

### 1.4 已发生的结构性问题

1. 同一 SHA 出现多张复审卡：DAV-1013 与 DAV-1014；
2. 连续评论延迟产生多个 queued run，导致重复冷启动；
3. 同一 issue 同时存在实施者和调度助手 run；
4. 规格散落在 description 与多条更正评论中，执行者会读到过期要求；
5. 同一候选在诊断未闭环时多次提前修改生产代码；
6. 廉价格式门禁未先执行，导致昂贵全量跑在无效 SHA 上；
7. 测试脚本曾输出与数据相反的硬编码结论；
8. 定向测试、精确 SHA 复审、完整回归、网络隔离、合入授权、部署授权曾被混为一个“PASS”。

## 2. 目标治理模型

### 2.1 权限与职责

#### 主控（David + 当班 Codex/Claude）

- 创建和更新权威规格；
- 指定实施者、复审者和风险等级；
- 创建复审卡；
- 核准精确 SHA；
- 只有 David 能发出“准予合入”或“准予部署”；
- 负责取消重复 run、处理越权和维护状态机。

#### 项目调度助手

- 只读取看板、识别无 owner/停滞/重复状态；
- 只能建议主控创建复审卡，不能自行创建或指派复审卡；
- 不审代码、不宣布复审通过、不合入、不部署；
- 不在评论中 mention 自己；
- 同一 fingerprint 无变化时保持静默。

#### 实施者（资深开发1/2、高级开发·支援）

- 一个 issue/候选分支同一时刻只允许一个 writer；
- 只修改规格白名单内文件；
- 禁止 amend/rebase/force-push 已冻结提交；
- 开工前必须输出 `ACK_SPEC_<version>`；
- 发现需要扩白名单时停止并上报，不自行扩 scope；
- 交付精确远端分支、完整 SHA、直接父、测试和未完成事项。

#### 第一复审（代码审核员/2，Devin SWE-2）

- 只审主控创建的复审卡；
- 只审 description 指定的完整 40 位 SHA；
- 只读，不修代码；
- PASS 必须写成“复审通过（SHA ...）”，不得写成“可合入/可部署”。

#### STRICT 第二复审（代码复核员，Gemini）

- 作为异构第二视角，只用于 STRICT 任务；
- 重点审结构性风险、测试是否会假绿、全局状态和证据链；
- 不复用第一审核员的结论，不以其 PASS 为输入前提；
- 同样只读且绑定精确 SHA。

这能最大化利用 Devin SWE-2：SWE-2 负责高吞吐实现和第一审，Gemini 只承担高风险任务的异构第二眼，避免所有任务都付出双重复审成本。

### 2.2 复审卡创建者校验

Multica 的 `issue get` 已提供 `creator_id` 和 `creator_type`，不需要依赖标题前缀。

正式复审卡须满足：

```text
creator_type == "member"
creator_id   == "704f6e66-7f28-42b8-9ac9-a93c231349a6"  # David Liu
```

若不满足，审核员只能报告“调度异常”，不得出正式 PASS。DAV-1013 的 `creator_type=agent` 可作为反例；DAV-1014 的 `creator_type=member` 可作为正例。

## 3. 单一权威规格

### 3.1 权威规格块

未完成 metadata 触发验证前，issue description 顶部固定块是唯一权威来源：

```yaml
SPEC_VERSION: 3
RISK: STRICT
PHASE: IMPLEMENTING
OWNER: agent:<exact-id>
BASE_SHA: <40-char SHA>
TARGET_BRANCH: <remote branch>
WHITELIST:
  - path/a.py
  - tests/test_a.py
FORBIDDEN:
  - api/main.py
  - database schema
ACCEPTANCE:
  - ...
TEST_COMMANDS:
  - env -u PYTHONPATH .venv310/bin/python -m pytest ...
```

description 更新必须使用 `--no-start`。更新完成后，主控再进行一次显式启动。

### 3.2 ACK 协议

执行者开始任何读写前，必须在 run 输出首段写：

```text
ACK_SPEC_3
BASE_SHA=<full SHA>
WHITELIST=<paths>
```

ACK 与 description 版本不一致，run 立即停止。评论中的旧规格不再覆盖 description；更正规格必须增加 `SPEC_VERSION` 并更新 description。

### 3.3 Metadata 的位置

`SPEC_VERSION`、`RISK`、`PHASE` 最终应镜像进 issue metadata，便于机器查询。但在低风险试卡证明 `metadata set` 不会意外启动 run 前：

- description 固定块是权威源；
- metadata 只是待验证能力；
- 不给现有在途卡回填 metadata；
- 不把 metadata 当作调度触发器。

## 4. 状态机

### 4.1 细粒度阶段

```text
DRAFT
  → DIAGNOSING
  → SPEC_LOCKED
  → IMPLEMENTING
  → CANDIDATE_FROZEN
  → REVIEW_1
  → REVIEW_2 (STRICT only)
  → REGRESSION
  → AWAITING_MERGE_AUTH
  → MERGED
  → RUNTIME_VERIFIED
```

### 4.2 Multica 粗状态映射

| PHASE | issue status |
|---|---|
| DRAFT | `todo` |
| DIAGNOSING / SPEC_LOCKED / IMPLEMENTING | `in_progress` |
| CANDIDATE_FROZEN / REVIEW_1 / REVIEW_2 / REGRESSION | `in_review` |
| AWAITING_MERGE_AUTH | `blocked` |
| MERGED（未部署） | `in_review` |
| RUNTIME_VERIFIED | `done` |
| 不再采用 | `cancelled` |

所有纯状态更新用 `--no-start`。状态本身不作为证据；run、远端 SHA、测试文件和运行态分别核验。

## 5. 指令投递与去重

### 5.1 决策表

| 当前状态 | 唯一允许动作 |
|---|---|
| 有正确 owner 的 steerable run | 只发 `steer`，不评论、不 rerun |
| 无 run，规格未更新 | `issue update --no-start` 更新 description |
| 无 run，owner 未绑定 | `issue assign --no-start` |
| 规格与 owner 已就绪 | 恰好一次 `issue rerun` 或一次不带 `--no-start` 的启动动作 |
| 需要归档证据 | 评论一次，随后两段式检查 queued |
| 同 fingerprint 已有运行/结果 | 不重派、不重跑 |

### 5.2 评论后的有界复查

每次不得不发评论后：

1. 约 10 秒检查一次 running/queued；
2. 约 30 秒再检查一次；
3. 保留唯一正确 owner run；
4. 取消其余重复/冷启动 run；
5. 不使用无限轮询。

### 5.3 Stage barrier

新建多阶段流水线时，优先使用 parent issue + `--stage` 子卡。每一 stage 的子卡全部结束后，再由平台唤醒 parent owner。实施交付评论不再 mention 项目调度助手。

该机制须先在试卡验证后再用于真实代码工作，避免把未经验证的平台行为直接用于 DAV-998 或卡 C。

## 6. 风险分档与并行策略

### 6.1 FAST

适用：纯文档、注释、测试说明、无产品语义的机械清理。

- 单 writer；
- 定向检查；
- 一名审核员；
- 不跑产品全量；
- 仍需精确 SHA 和白名单。

### 6.2 STANDARD

适用：单模块产品改动、局部 bug 修复、无全局状态/数据库/网络架构变化。

- 单 writer；
- 定向测试 + 相邻模块测试；
- 精确 SHA 独立复审；
- 按项目 D-012，产品代码候选仍需真实全量回归；
- 全量开始前必须通过廉价门禁。

### 6.3 STRICT

适用：网络护栏、provider、全局状态、并发、lifespan、数据库 schema/迁移、数据语义、部署和安全边界。

- 先只读诊断，因果链通过后才实施；
- Devin 第一复审 + Gemini 异构第二复审；
- 红队场景必须由第二双眼睛确认完整性；
- 完整全量回归；
- 涉及网络时加入独立 OS 层监测；
- 合入后必须做运行态和真实业务 smoke；
- David 单独授权合入/部署。

### 6.4 可并行与不可并行

可以并行：

- 冻结 SHA 的只读代码审查；
- 基线失败集合整理；
- 环境/依赖探针；
- 测试日志解析；
- 远端谱系和白名单核对；
- STRICT 的异构第二复审。

不可并行：

- 同一候选分支上的两个 writer；
- 同一文件集的平行修复；
- 未冻结 SHA 时启动正式复审；
- 边跑全量边继续提交；
- 未完成诊断就改生产代码。

## 7. 候选清单与稳定指纹

### 7.1 候选清单必须包含

```text
issue_id
spec_version
risk
base_sha
head_sha
direct_parent_sha
remote_ref_and_readback
worktree_status_before_and_after
changed_files_vs_base
whitelist_result
forbidden_paths_result
diff_check_result
test_commands_and_exact_counts
baseline_failure_set_comparison
evidence_files_and_sha256
unverified_items
review_cards_and_reviewed_sha
merge_authorization
deployment_authorization
```

最终 verdict 必须从这些字段推导。若测试未自然结束、缺汇总、SHA 变化、工作树变脏或必需证据缺失，自动输出 FAIL/INCOMPLETE，禁止继续生成“基线一致”之类结论。

### 7.2 稳定指纹

昂贵测试的 fingerprint：

```text
SHA256(
  full_head_sha
  + interpreter_version
  + dependency_lock_or_pip_freeze_hash
  + exact_test_command
  + relevant_env_summary
  + DB_or_fixture_identity
  + guardrail_mode
)
```

同一 fingerprint 的成功结果直接复用；代码失败不得通过重复运行“洗绿”。基础设施失败单独标记 `INFRA_FAIL`，最多自动重试 2 次；第三次必须人工判断或更换执行环境。

## 8. 廉价门禁必须前置

任何分钟级全量测试前必须全部通过：

1. HEAD 等于锁定完整 SHA；
2. `git status --porcelain --untracked-files=all` 为空；
3. `git diff --check <base>..<head>` 为空；
4. 累计 changed-files 严格符合白名单；
5. 禁改文件为零；
6. 远端 ref 回读同一 SHA；
7. 测试解释器和环境正确；
8. 验收脚本/日志在仓库外，不污染候选；
9. 结论生成逻辑通过负向自测；
10. 无重复的同 fingerprint run。

任一失败立即停止，不启动全量。

## 9. 角色 instructions 的最小更新

### 9.1 项目调度助手

删除当前“默认建立单独只读审查卡”的授权，追加：

```text
不得创建、指派或 steer 任何复审/审查 issue。需要复审时只向主控报告一次。
不得把 PASS 升级为合入/部署授权。
所有 status/assign/update 默认使用 --no-start；仅主控明确要求唤醒时启动一次。
不得在评论中 mention 自己，不得用完成评论形成自触发循环。
同 issue + 同 fingerprint 已有 run 或结果时，不重复派发。
```

### 9.2 实施者三人

追加：

```text
开工先读 description 顶部规格块并输出 ACK_SPEC_<version>。
只改 WHITELIST；需扩围则停止并报告。
不得 amend/rebase/force-push 冻结提交，只能线性追加。
同一 issue 已有另一个 writer 时不得开工。
交付评论不再 mention 项目调度助手；后续由 stage barrier 或主控显式推进。
```

### 9.3 代码审核员/2

追加：

```text
仅接受 creator_type=member 且 creator_id 为 David 的复审卡。
仅审 description 指定的完整 40 位 SHA；实际 HEAD 不一致则拒绝出结论。
不改代码、不合入、不部署。
PASS 固定写“复审通过（SHA <full>）”；禁止写“准予合入/可以 FF/可以上线”。
```

### 9.4 独立代码审核员

- 将 instructions 中已过时的 Cursor 终审措辞改为 D-011 当前口径：David 最终持闸；
- 保留 exact-SHA、白名单、只读和 PASS≠合入；
- 删除互相矛盾的“建议准予合入”模板残留。

### 9.5 代码复核员

追加 STRICT 第二复审职责，但不改变其日常存量复核角色；只有 issue 明确 `RISK=STRICT` 且由 David 指派时，才审未合入冻结 SHA。

## 10. 分阶段落地

### Phase 0：备份与基线

1. 将需修改的 agent JSON 完整导出到仓库外唯一目录；
2. 记录文件路径、时间和 SHA256；
3. 记录当前模型、instructions、max concurrency；
4. 不修改现有 issue，不回填 metadata。

### Phase 1：角色边界与投递纪律

只做：

- 更新项目调度助手、三名实施者、两名代码审核员、独立代码审核员、代码复核员的 instructions；
- 不改模型；
- 不改现有 issue；
- 不建真实代码卡；
- 不启全量测试。

完成后逐 agent 回读，确认更新没有覆盖其原有必要规则。

### Phase 2：低风险试运行

建立一个 workflow pilot parent，下面三张 FAST 试卡按 stage 串行：

1. 纯文档卡：验证 SPEC_VERSION/ACK、单 owner、comment 与 metadata 的触发语义；
2. 测试说明卡：验证候选清单、精确 SHA 复审与 creator 校验；
3. 无产品语义的 lint/typing 卡：验证打回 → 新 SPEC_VERSION → 新 SHA → 新复审。

试运行要额外验证：

- `metadata set` 是否触发 run；
- stage barrier 是否只在整阶段完成后唤醒 parent；
- 评论触发 run 的延迟区间；
- bounded 两段式清队是否足够；
- 审核员是否能拒绝 agent 创建的复审卡；
- rollback 是否能恢复原 instructions。

### Phase 3：自动清单与指纹缓存

在试运行通过后，再实现主控侧工具：

- candidate manifest 生成器；
- stable fingerprint 缓存；
- preflight；
- queued-run 去重；
- evidence hash 清单。

工具必须先做正反测试，且不得进入产品候选提交。

### Phase 4：用于真实工作

第一批真实任务建议：

1. 卡 C（lifespan 全局状态恢复）按 STRICT 流程；
2. DAV-998 在最新主线/指定父提交上重放，不能直接使用旧平行 SHA；
3. 两条线可在不同工作树并行诊断/实现，但最终必须线性整合；
4. 组合候选再跑一次完整门禁，证据不得倒灌成子候选单独授权。

## 11. 回滚

1. Agent instructions：用 Phase 0 的完整 JSON 逐个恢复；
2. Metadata：只删除 pilot 创建的 key；
3. Pilot issue：统一标记 `cancelled`，不删除历史；
4. 主控侧脚本/缓存：删除仓库外工具目录；
5. 若触发行为与预期不符，停止 Phase 2，不把规则推广到现有 issue；
6. 不以回滚流程为由改候选、主线、服务或生产库。

## 12. 已替主控定下的默认选择

为避免再次把五个选择题抛给用户，默认采用：

1. STRICT 异构终审：现有 Gemini“代码复核员”，不新建 agent；
2. 复审卡授权识别：检查 `creator_type=member` + David 的 `creator_id`，不用标题前缀；
3. 状态机：description 固定块为权威，metadata 待试卡验证后只作机器镜像；
4. 现有在途卡：不回填 SPEC_VERSION；
5. `INFRA_FAIL`：同 fingerprint 最多自动重试 2 次；
6. 第一阶段只改 instructions，不改模型、issue、候选或代码。

## 13. 执行授权边界

本文件可以直接交给 Claude/Codex 作为实施规格，但下一步只授权到 Phase 1 时，应使用明确口令：

```text
按 work/2026-09-17-multica-workflow-upgrade-landing-plan.md 执行 Phase 0 和 Phase 1。
只备份并更新角色 instructions；不得修改模型、现有 issue、候选 SHA、主线、服务、数据库；不得创建 pilot 卡。完成后逐 agent 回读并提交变更前后差异与备份 SHA256，等待下一步授权。
```

不要把“执行整份方案”作为一次性授权。Phase 2 必须在 Phase 1 回读验收后另行启动。

## 14. 本轮实际变更

- 新增本流程报告；
- 未修改任何产品代码；
- 未修改任何 Multica agent 配置；
- 未修改任何 issue、run、模型或用户配置；
- 未合入、未部署、未重启、未写生产库。
