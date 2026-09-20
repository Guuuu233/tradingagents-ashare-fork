# DAV-159 项目调度助手事件链故障审计

## 目标

只读审计“任务完成/阻塞 → 精确 mention → 项目调度助手接收 → 后续调度 run”链路，确认当前是提示词执行问题、Multica 事件/评论触发问题、模型/provider 路由问题，还是上下文过长导致的编排失败。

## 已核验事故样本

1. DAV-157 环境 preflight：精确 SHA `c9bbe52022805befbce14298cea174280c7eebd2` 缺 `.venv310/bin/python`，评论使用真实 mention 后，曾返回 queued 并产生后续调度 run；说明 mention 触发链在部分场景可用。
2. DAV-158 mention 协议核验：项目调度助手为 6 张卡补发评论，补发评论 `trigger_outcomes` 为 queued；但同一协议任务随后出现 `API Error: 400 unknown provider for model codex/gpt-5.6-luna`，说明后续调度执行存在运行时/provider 路由失败。
3. DAV-126：项目调度助手 run `24a6e648-aaff-41ea-9e16-dc26ffa65b4d` 因 `Prompt is too long · automatic compaction failed` 失败；不能归类为代码失败。
4. 多个项目调度助手完成评论仍未带真实 mention，尽管 agent instructions 已加入强制交付协议；说明“提示词要求”目前不是平台硬性验收门。
5. 评论触发有 `queued`、`coalesced` 两种结果；需要确认 coalesced 是否会丢失必要的后续调度，还是只合并重复事件。

## 审计范围

只读检查：

- 精确 issue/run/comment 时间线：DAV-153、DAV-157、DAV-158、DAV-126；
- run 的 agent、model、provider、runtime_id、error、trigger_comment_id、delivered_comment_ids、coalesced_comment_ids；
- 评论 `trigger_outcomes` 是否真实 queued/coalesced；
- 项目调度助手的 agent instructions、当前 model/provider 解析和 max_concurrent_tasks；
- 15 分钟看板哨兵与 Multica autopilot 是否为同一机制，是否存在重复/错配/暂停；
- 完工后是否真的创建下一任务，还是只发布“已收到/无需动作”评论。

## 必须回答

1. 是否存在可复现的调度平台故障？按“确认 / 未确认 / 证据不足”分类。
2. `unknown provider for model codex/gpt-5.6-luna` 的责任层：模型配置、agent runtime、provider 解析、还是一次性网关错误；不得修改用户模型/provider 设置。
3. `Prompt too long` 是否由父任务历史注入造成；给出避免长上下文的最小 handoff 方案。
4. `coalesced` 事件是否仍保证至少一个有效 scheduler run；若不能，记录为事件丢失风险。
5. 普通 `@项目调度助手` 与 `mention://agent/...` 的触发差异；确认硬门应放在 agent prompt、CLI comment wrapper 还是 scheduler 侧。
6. 给出最小修复建议：不改业务代码、不改个人模型/provider/API key、不重复启动同树 coder。

## 交付证据

- 时间线表：issue、run、comment、mention、trigger_outcomes、后续 run、结果；
- 原始错误分类，不得把 400/429/provider error 当代码失败；
- 明确哪些动作已实际完成、哪些未完成；
- 审计完成后在 issue 评论末尾使用真实 mention：[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)。

## 边界

只读；禁止修改仓库业务代码、测试、数据库、用户模型绑定、providers、API Key、凭据、主干或服务。不得重启服务，不得宣称 DAV-119 或任何下游门已解锁。

执行者：首席调度官；项目调度助手只作为被审计对象，不作为唯一审计者。
