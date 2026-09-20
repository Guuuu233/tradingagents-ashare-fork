# 阶段 B 交接：验证并提交（给终端 Claude Code）

> 读完再动手。**你的任务只有验证和提交，不要改实现。**
> 实现已完成并逐段审查通过，改它等于推翻已定案的决策。
> 本文件在 `.gitignore` 内（`docs/` 整个被忽略，只有 `KNOWN_ISSUES.md` 被跟踪），不会进提交。

---

## 0. 为什么交接给你

上一个实例跑在 Cowork 的沙箱 VM 里：文件读写是宿主机真实的（改动就在工作区，不用重新生成），
但 shell 被隔离，没有 Docker daemon、没有 `/app/.venv/bin/python`。
所以真实容器里的验证做不了，只剩这一步。**你有真 shell，这是交给你的唯一原因。**

---

## 1. 已完成的实现（不要改）

`feat: persist custom analysis prompts server-side` —— 自定义提示词此前只在浏览器
localStorage，分析师/研究员/研究经理从未读到它。本阶段只把存储搬到服务端，**不碰注入**
（注入是阶段 C）。

| 文件 | 改动 |
|---|---|
| `api/database.py` | 新增 `UserCustomPromptDB`（表 `user_custom_prompts`）；`UserDB` 加 `prompt_injection_enabled`（默认 False）+ `_ensure_user_schema()` 里的 ALTER TABLE |
| `api/services/custom_prompt_service.py`（新） | list / replace（整体替换，单事务）/ resolve（单角色 + 全 15 角色）/ migrate（幂等）/ 开关读写 |
| `api/main.py` | 6 个端点 + 对应 pydantic 模型 |
| `frontend/src/types/index.ts` | 4 个新类型 |
| `frontend/src/services/api.ts` | 6 个客户端方法 |
| `frontend/src/pages/Settings.tsx` | 全局提示改为后端优先 + 首次迁移；localStorage 降级为缓存 |
| `tests/test_custom_prompts.py`（新） | 19 个用例，全过 |
| `scripts/smoke_custom_prompts.py`（新） | 真实库冒烟，自带 schema 检查 |

关键设计（已定案，别改）：

- **表形状照抄 `role_bindings`**（`target_type` + `target_key` + 唯一约束），以便复用
  同样的按优先级解析：角色覆盖 > 分组覆盖 > 全局
- **合并语义是追加**：`resolved = global + "\n\n" + override`，让全局约束（置信度上限、
  数据缺口规则）在设了角色提示的 agent 上依然生效
- **长度上限按 resolved 卡（6000）**，不只卡单字段，因为乘以 agent 数变成 token 成本的
  是拼接结果。global 单字段 4000、role/group 单字段 2000
- **角色 key 唯一来源是 `role_routing_service.ALL_ROLES`（15 个，不是 14）**。
  `api/main.py` 的 `ANALYST_AGENT_NAMES` 是 SSE 展示用的另一套短名
  （`aggressive`/`neutral`/`conservative`/`portfolio_manager`），和 `graph/setup.py` 里
  构造 agent 用的 `role_llms` key 对不上。**两套不能混用**，混了风控三方和风控总监会静默错位

### 三处已定案、不要"顺手优化"

1. **6002 边界**：global 和某角色覆盖都顶格（4000+2000）时，加上 `\n\n` 分隔符 resolved 是
   6002，超 6000 上限被拒。**这是刻意的**，不加余量：6000 是"注入文本"的字面上限，加余量后
   这个数字就不再是字面意思。报错已指明角色和两个数字，用户删两个字即可。
2. **主开关放在 `UserDB.prompt_injection_enabled`**，不是 `user_llm_configs`（那张表正在被
   `migrate_legacy_user_llm_config` 拆解，往上加列方向反了），也不是表内 `target_type='switch'`
   （会污染枚举语义、每条 resolve 路径都要额外过滤）。旁边的 `email_report_enabled` /
   `wecom_report_enabled` 同形。
3. **`test_dashboard_tracking.py` 那两个失败与本次改动无关，已定案**。做法：把仓库复制两份到
   `/tmp`，一份用 `git show HEAD:` 还原成改动前，同环境跑同一测试文件 → 改动前后都是
   `2 failed, 2 passed`，失败位置同为 `:199`。**不要修它，不要 stash 重验。**

---

## 2. 你要做的：验证（提交前置条件）

### ⚠️ 最后那条命令不只是测试，它就是那次 schema 迁移

`scripts/smoke_custom_prompts.py` 里的 `init_db()` 会触发
`ALTER TABLE users ADD COLUMN prompt_injection_enabled`，**直接改生产库**
（那个库里有 188 条 completed 报告）。备份不是保险，是前置条件。

### 命令顺序（备份必须是第一条涉及数据库的命令）

**顺序本身是审查过的，不要重排。** 特别是：**不要**在备份之前跑任何
`import api.database` 的命令 —— 包括那条看起来只是读配置的
`python -c "from api.database import DATABASE_URL"`。如果 `api/database.py` 有模块级副作用
（import 时就跑 `create_all()` 或 `_ensure_*_schema()`），那条"只读"命令会在**任何备份存在之前**
把 `ALTER TABLE` 打到生产库上。不需要先去确认有没有副作用，直接把备份放最前面。

```bash
cd ~/Documents/TradingAgents-AShare

# 1. 删掉沙箱残留的锁（上个实例权限不足删不掉）
ps aux | grep -i '[g]it'          # 先确认没有别的 git 进程
rm -f .git/index.lock && git status

# 2. 找服务名
docker compose ps

# 3. 备份 —— 用 shell 直接找文件，不 import 任何应用代码
ls -la data/*.db
cp data/tradingagents.db data/tradingagents.db.bak-2026-07-30
ls -la data/tradingagents.db*     # 确认备份存在且大小非 0

# 4. 备份存在之后，才去确认 DATABASE_URL
docker compose exec <服务名> /app/.venv/bin/python -c "from api.database import DATABASE_URL; print(DATABASE_URL)"
#    ⚠️ 如果它指向的不是第 3 步刚备份的那个文件 → 停下来告诉用户，不要继续

# 5. 单元测试 —— 预期 19 passed
docker compose exec <服务名> /app/.venv/bin/python -m pytest tests/test_custom_prompts.py -v

# 6. 冒烟（真实库；这一步就是 schema 迁移本身）
docker compose exec <服务名> ls scripts/    # 先看 scripts/ 在不在容器里，见下面绊线 1
docker compose exec <服务名> /app/.venv/bin/python scripts/smoke_custom_prompts.py
```

冒烟脚本自带 schema 验证：触发 ALTER TABLE → 查 `PRAGMA table_info(users)` 断言列存在、
`NOT NULL`、默认 0 → 统计 `users` 表里 `IS NULL` 和 `= 1` 的行数（都必须是 0）→ 用 ORM 读一个
**已有**用户断言读出 `False` 而不是 `None`/`True` → 然后才跑 12 项端点检查 → `finally` 删掉
自己建的 2 个临时用户。

### 三条绊线

1. **`scripts/` 可能没被 bind mount。** 交接简报第 8 节写的挂载只有 `api/`、
   `tradingagents/`、`tests/` 三个目录。若 `ls scripts/` 看不到脚本：
   `docker cp scripts/smoke_custom_prompts.py <容器名>:/app/scripts/`
2. **输出里 `PRAGMA table_info` 的默认值若是带引号的 `'0'`，立刻停下。**
   已有部署走 ALTER TABLE 是不带引号的 `0`；带引号说明走了
   `create_all()` 的 `server_default="0"`，即连的是新建库、不是那个装着 188 条报告的库。
3. **副作用**：脚本在真实库建 2 个 `smoke-prompt-*@test.local` 用户并在结束时删除。
   中途崩溃可能残留，清理：
   `DELETE FROM users WHERE email LIKE 'smoke-prompt-%@test.local';`

### 回滚路径（这个 ALTER TABLE 基本不可逆）

SQLite 在 3.35 之前没有 `DROP COLUMN`，**备份是唯一退路**。

> **出现任何异常 —— 绊线触发、断言失败、`database is locked`、或者输出你看不懂 ——
> 立即停下 → `cp` 备份回去 → 把原始输出贴给用户 → 不要向前修。**

"向前修"（发现异常后继续改代码试图绕过）是这里最坏的选择：你在一个已经被部分修改、
状态不明的生产库上操作，每一步都在加深不确定性。退回已知状态再判断。

```bash
# 回滚
cp data/tradingagents.db.bak-2026-07-30 data/tradingagents.db
```

### 关于 `database is locked`

这个库**不是 WAL 模式**（`journal_mode=delete`，`SQLITE_USE_WAL` 默认 false）。
`docker compose exec` 是在 API 进程所在的容器里**再起一个** Python 进程，
两个进程写同一个文件，所以可能看到 `database is locked`。

**跑的时候不要同时操作前端。** 锁会干净失败、不会损坏数据，所以不是灾难 ——
但按上面的规矩，看到它仍然是停下、回滚、贴输出，不要重试着往前撞。

### 贴输出 → 停下等确认

**把第 5、6 步的完整原始输出贴给用户，然后停下等他确认，才能进入下一节的提交。**
（用户要转给审查方，审查方明确要求"真实调用的原始输出，不只是通过"。）

---

## 3. 你要做的：提交

> ### ⛔ 先贴输出，等人工确认，再提交
>
> **不要自行提交。** `AGENTS.md` 铁律 5：不自行提交到主分支，完成后展示完整 diff
> 并说明改了什么、为什么，等确认后再提交。
>
> 用户说过"上面 1-3 做完就提交"，但那句话**以验证通过为条件**。如果绊线 2 触发
> （`PRAGMA` 默认值带引号）、冒烟有任何断言失败、或出现 `database is locked`，
> 这时候提交是错的。**这是第一次对生产库的写操作，闸门留在这里。**
>
> 正确顺序：备份 → 单元测试 → 冒烟 → **贴完整原始输出 → 停下等确认** → 才提交。

拆两个 commit（`AGENTS.md` 第 0 节第 4 条：一次提交一个关注点；
仓库有 `docs:` 单独提交的先例 `fee4c36`、`1aff852`）。

### commit 1 — 功能（8 个路径）

```bash
git add api/database.py api/main.py api/services/custom_prompt_service.py \
        tests/test_custom_prompts.py scripts/smoke_custom_prompts.py \
        frontend/src/pages/Settings.tsx frontend/src/services/api.ts frontend/src/types/index.ts

git status
git diff --cached --stat
```

`--cached --stat` 里**只应有**上面这 8 个路径。**必须不出现**：

```
tests/test_api_smoke.py          ← 长期未提交的脏文件，不要动
tests/test_intent_parser.py      ← 同上
tests/test_portfolio_import.py   ← 同上
work/  work_*.py  work_analysis_collect_probe.json
tradingagents.db-journal
scripts/work_smoke_commit2.py
```

（`tests/test_api_smoke.py::...test_overview_returns_watchlist_...` 本来就是红的，断言
`'600519.SH' == '贵州茅台'`，与本次无关，不要顺手修。）

提交信息：

```
feat: persist custom analysis prompts server-side

自定义分析提示词此前只存在于浏览器 localStorage，分析师/研究员/研究经理
从未读到过它。本提交先把存储搬到服务端，为阶段 C 的注入做准备，本身不改
任何注入行为。

- 新增 user_custom_prompts 表，形状照抄 role_bindings（target_type +
  target_key + 唯一约束），使其能复用同样的按优先级解析模式：
  角色覆盖 > 分组覆盖 > 全局
- 合并语义为追加：resolved = global + "\n\n" + override，让全局约束
  （置信度上限、数据缺口规则等）在设了角色提示的 agent 上依然生效
- 长度上限按 resolved 结果卡（6000），而不是只卡单字段，因为真正乘以
  agent 数变成 token 成本的是拼接结果
- 每行带 prompt_hash，供阶段 C 标识报告用的是哪一版提示词
- 角色 key 唯一来源是 role_routing_service.ALL_ROLES（15 个）。
  api/main.py 的 ANALYST_AGENT_NAMES 是 SSE 展示用的另一套短名，
  两者在风控三方和风控总监上对不上，不能混用
- 用户级总开关 users.prompt_injection_enabled，默认关闭
- PATCH 的删旧插新在单个事务内完成，插入失败回滚，旧提示词不丢
- localStorage 降级为缓存，首次加载时上传一次（仅当后端无 global 记录，
  防止多端/重试把用户已改的新内容覆盖回旧值）
```

### commit 2 — 文档（1 个文件）

```bash
git add docs/KNOWN_ISSUES.md
git diff --cached --stat
```

```
docs: record that custom prompt history is unrecoverable

PATCH 删行重建，用户一改提示词旧文本就永久消失。阶段 E 做 A/B 时，
标着某个 hash 的报告将查不回对应的提示词原文，而"提示词改动如何影响
校准度"正是这项工作要回答的问题。记录解法：阶段 C 注入时把完整
resolved 文本写进报告快照，而不是建历史表。
```

> 注意 `docs/` 在 `.gitignore` 里，只有 `KNOWN_ISSUES.md` 被跟踪。
> `docs/HANDOFF_2026-07-30.md` 也更新过（阶段 C 要点、15 个角色清单、命名陷阱），
> 但它进不了 git。若用户希望它入库需 `git add -f`。

---

## 4. 提交完之后

1. **把 `/v1/dashboard/tracking-board` 在 HEAD 上返回 500 这件事加进 backlog。**
   干净环境（`/tmp`，无挂载问题）里也复现，改动前就存在，交接简报的 backlog 里没有这条。
   **单开会话查，不要混进阶段 B。**
2. 阶段 C 开始前先读 `docs/KNOWN_ISSUES.md` 新增的那节和
   `docs/HANDOFF_2026-07-30.md` 的「阶段 C 要点」，里面有：注入前先读总开关、
   只能通过 `custom_prompt_service.resolve_*()` 取文本、以及必须把完整 resolved 文本
   （不只是 hash）写进报告快照的理由。
3. 提醒用户轮换那个曾在对话里明文粘贴过的本地 `X-API-Key`。

---

## 5. 这个项目最重要的一条（务必读）

**遇到数字对不上、量级差得离谱、会计或物理上说不通的结果，停下来查证，不要给一个解释然后继续。**

已经出过五次「自相矛盾的数字被当成结论往下传」。最近一次就在本阶段：上个实例断言
`test_dashboard_tracking` 的失败是"挂载的 disk I/O error"，搬到 `/tmp` 后 disk I/O error
消失了但测试照样失败（变成 `assert 500 == 200`）—— 两个独立问题叠在一起，第一个把第二个
掩盖了，而它给了个单一原因的解释就往下走了。

前视偏差这类问题的特征恰恰是「看起来完全正常」。

另：**不要写"已彻底修复""完美解决"。没有测试覆盖的修复叫推测。**
