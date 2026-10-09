# B-5 设计文档：兼容读取层扩展 / 旧行处置 / 逐字相同验收

> 总控 2026-10-09 令 · 依据 DAV-1615 体量普查 + DAV-1710/1715 全量核查实测
> **候选基线（DAV-1757 复审修正）**：本文档依赖的 B-1 ~ B-4 / DAV-1651 均**未合入主干**——B-1（DAV-1506）/B-2（DAV-1545）/B-3（DAV-1572）位于 `origin/staging/b-chain-20261009` tip `082be0476c1b68dcc9cd33048d3588ff6346ac96`（相对主干 `c065209b97ea36f32d83ec61f8c2ae1aea97d7dd` 领先 7 个提交，依次为 `17fae5c` B-1、`fac996d`/`e845fb6`/`f0108af`/`d440e8a` B-1 修订、`9853fa2` B-2、`082be04` B-3）；B-4（DAV-1614 `scripts/slim_result_data_storage.py`）位于 `origin/agent/2/8f6e0d518330`（`39acc39`，摘入 B 链，未进入 staging）；`weekly_report` 兼容层接线（DAV-1651，`e7ad845`/`d940051`）位于 `origin/agent/agent/360b1e1b472a`。三者均非 `c065209` 的祖先（`git merge-base --is-ancestor` 判定 NO）。
> **B-5 实现卡须以 B 链 staging tip（含 B-4 摘入，即 `082be04` + `39acc39` 的合成基线）为父，不得直接以主干 `c065209` 为父**——否则第一步就是 `ImportError`（主干上 `tradingagents/storage/result_data_compat.py`、`scripts/slim_result_data_storage.py`、`canonicalize_for_single_write`/`encode_canonical`/`expand_compat_view`/`StorageCompatConflict`/`is_canonical_storage`/`storage_schema_version` 全部不存在，主干 `git grep` 零命中）。
> **只写文档；无代码改动；不实施任何下述转换。**

## 术语表

- **`ids`**：`investment_debate_state`（`result_data` 内嵌的辩论状态子文档）；`risk_debate_state` / `risk_feedback_state` 另见各节范围说明（默认**不在** ②③ 范围内，见 §2.2/§2.3）。
- **mdc**：`market_data_context` 行情上下文对象。
- **A 行 / v1 行 / v2 行**：A 行 = 无 `storage_schema_version` 或带物理 `horizons` 别名的旧行；v1 行 = `result_data.storage.v1` canonical 行；v2 行 = 本文档新定义的 `result_data.storage.v2` 行。
- **权威存储位**：物理上真实存在、读取视图以其为准还原虚拟键的字段。

## 0. 目标与依据

DAV-1615 裁定了三条瘦身方向，B-5 为其设计与落地方案（本卡只写设计）：

| 裁定 | 内容 | DAV-1710 实测节省（B 投影基线 2,441,585,943 B / 1,016 份双档报告） |
|---|---|---|
| ① 两档共用一份行情上下文 | `short_term.market_data_context` 与 `medium_term.market_data_context` 全库去重单存 | 实测相等部分 180,272,987 B（7.38%）；若对 634 对不等值也合并则为 356,267,568 B（14.59%，非无损，**待裁定是否纳入**） |
| ② attempts 只存一处 | `ids.attempts` 顶层数组与 `round_messages[].attempts` 展平二选一 | **上限 135,926,700 B（5.57%，待核）**——该数字出自「保留消息内、删顶层」口径，DAV-1757 复审实测表明顶层 `attempts` 承载消息内无对应项的合成记录（`raw_response` 唯一副本），真实去重收益上限待 §2.2 分层统计后核定 |
| ③ history 读取时拼装 | `ids.history` 不落库，读取时由 `bull_history`/`bear_history`/`round_messages` 现场拼装 | 108,384,887 B（4.44%，仅删 `history`）；删三字段 216,796,446 B（8.88%，另列） |

**边界与待办**：本卡标注「**待全库核实**」——DAV-1752（B-5 全库存储核实，按表按列字节+行数 top10，进行中）结果出来后报总控定最终范围。本设计以已完成的 DAV-1710/1715 全量数据为准；DAV-1752 的按表汇总只影响是否扩大范围（例如把非 `reports` 表一并纳入），不改变下述机制设计。

## 1. 现状架构（B-1 ~ B-4 已就位——均不在主干上，位置如下）

> ⚠️ 以下各组件在主干 `c065209` 上**全部不存在**（`git cat-file -e` / `git grep` 实测零命中）。"已就位"指已在下列分支落地并通过各自验收。

- **唯一物理存储**（D-072 附注裁定）：`reports.result_data` 每档一份权威切片 `short_term` / `medium_term`；`horizons.<h>` 与顶层 `market_data_context` 为**读取层虚拟键**，canonical 行（`storage_schema_version = "result_data.storage.v1"`）不再物理写。
- **兼容层**：`tradingagents/storage/result_data_compat.py`（B-1，DAV-1506；位于 `staging/b-chain-20261009`，`17fae5c` + 修订 `fac996d`/`e845fb6`/`f0108af`/`d440e8a`，tip `082be04`）。核心 API：
  - `encode_canonical(result_data)`：旧双别名行 → canonical，录 `storage_compat` 掩码（`horizons_order` / `horizon_key_masks` / `top_market_context` / `top_key_order`）；别名冲突 `StorageCompatConflict` 失败关闭。
  - `expand_compat_view(result_data)`：canonical → 旧视图（深拷贝），按掩码逐字还原键序 / null / 缺失。
  - `result_data_compat_view`：冲突日志 + 保持原样的安全入口。读边界接线点已接：`get_report`、`scheduler/main` 通知装载、`historical_cases`、`daily_snapshot_ledger`（均在 `staging/b-chain-20261009` 上）；`weekly_report`（DAV-1651）位于 `agent/agent/360b1e1b472a`（`e7ad845`/`d940051`）——**不在主干，也不在 staging**。
  - `strip_compat_view_for_persist`：写边界剥虚拟键，防兼容视图赋回 ORM 后整段写回。
- **写侧**（B-2，DAV-1545）：`canonicalize_for_single_write` 统一漏斗，新写入即 canonical。位于 `staging/b-chain-20261009`（`9853fa2`）。
- **存量转换**（B-4，DAV-1614）：`scripts/slim_result_data_storage.py` 逐行 `encode_canonical` + 事务内逐字验收 + journal 续跑 + VACUUM 新库；范围仅限**物理带 `horizons` 的行**。位于 `agent/2/8f6e0d518330`（`e6a9e74`，摘入提交 `39acc39`）——**主干与 staging 均无此文件**。
- **B-3**（DAV-1572，`staging/b-chain-20261009` tip `082be04`）：回填脚本只写 `*_term` 权威档，存量物理别名字节保留。

## 2. B-5 三项机制设计

三条裁定对应兼容层三个方向的扩展。总则：**旧行不改写、不删除；读取视图对所有行给出语义正确的结果；冲突一律失败关闭（fail-close）。**

### 2.1 ① 两档共用一份行情上下文（`market_data_context` 去重单存）

**目标物理形态（v2，新写入与可选转换的产物）**：

```
result_data.storage_schema_version = "result_data.storage.v2"
result_data.market_data_context = <单一行情上下文>          # 档间共用，只存一处
result_data.<h>_term.market_data_context 不再物理存         # 档内副本移除
result_data.storage_compat.top_market_context = {"kind": "shared", "horizons": ["short","medium"], "order": [...]}
```

- 顶层 `market_data_context` 在 v2 下从「虚拟键」升级为**唯一权威存储位**；档内副本成为虚拟键，由读取层还原到 `short_term.market_data_context` 与 `medium_term.market_data_context`。
- **不一致值处置**：DAV-1710 实测 894 对双边 mdc 中 260 对相等、634 对不等。相等的 260 份可无损合并；不等的 634 份**必须保留两档各自的 mdc**（不能选一边丢另一边）。方案：v2 行允许 `storage_compat.shared_market_context.kind = "per_horizon_keep"`（或等价地 `top_market_context.kind = "per_horizon"` 加档内 mdc 权威标志），此时档内 mdc 仍是物理权威（等同 v1 语义），读取视图原样返回；只有共享态（`kind = "shared"`）时才走单存还原。**合并范围裁定属总控**（见 §5 待裁定项）。

**读取视图（对旧行/新行分别给出的视图）**：

| 行形态 | `storage_schema_version` | 读取视图行为 |
|---|---|---|
| A 旧行（物理 `horizons` + 顶层 mdc + 档内 mdc） | 无 / 其他 | B-1 语义：`expand_compat_view` 原样返回（`is_canonical_storage` 判否），不做任何改动。 |
| v1 canonical 行（B-4 转换产物：无 `horizons`、无顶层 mdc、档内各有 mdc） | `result_data.storage.v1` | 走现有 B-1 路径：`horizons.<h>` 由 `horizon_key_masks` 从 `*_term` 重建；顶层 `market_data_context` 按 `top_market_context`（`per_horizon`/`slice`/`null`/`absent`）重建。档内 mdc 各自独立，视图与物理一致。 |
| **v2 行（B-5 新写入，`kind = "shared"`）** | `result_data.storage.v2` | 新增分支：档内 `market_data_context` 视为虚拟键，`expand` 时把顶层单一 mdc **深拷贝**注入 `short_term` 与 `medium_term` 两个切片；`horizons.<h>` 与顶层键序仍按 `storage_compat` 掩码还原。两档视图中的 mdc **逐字节相同**（同一来源）。 |
| v2 行且保留档内不一致 mdc（`kind = "per_horizon_keep"`） | `result_data.storage.v2` | 档内 mdc 为权威，视图原样透出；顶层 mdc 按掩码重建（若为 `absent` 则不补）。 |

`is_canonical_storage` 判定扩为 `storage_schema_version in {v1, v2}`；v1 逻辑完全冻结，v2 仅追加「档内 mdc 虚拟化」一条还原规则。

#### 2.1.1 与 B-1/B-2 既有掩码/fail-close 契约的兼容矩阵（DAV-1757 🟡-2 要求）

v2 语义必须与 B-1/B-2 三处 `is_canonical_storage` 分支的既有行为共存，不撞 fail-close。以下为待实现的取值约定（**设计口径，实现卡按此编码**）：

| 维度 | v1 现状（`staging/b-chain-20261009` 实测） | v2 `shared` 行为 | v2 `per_horizon_keep` 行为 |
|---|---|---|---|
| `_VIRTUAL_TOP_KEYS` | `("horizons", "market_data_context")`（`:77`）——写侧 `strip_compat_view_for_persist` 对 canonical 行**无条件剥**两键（`:687`，仅 `_top_mdc_kept` 豁免 mdc） | `market_data_context` 在 v2 下是**权威物理键**，必须**从剥离名单中豁免**（掩码 `top_market_context.kind = "shared"` 即该豁免标志）；`horizons` 仍为虚拟键照常剥 | 档内 mdc 权威、顶层 mdc 若物理存在视为旧形态残留，按现有 `_top_mdc_shape`/`_top_mdc_kept` 规则处理（不匹配则 fail-close） |
| `top_market_context.kind` 取值 | `per_horizon` / `slice` / `null` / `absent` 四态 | 新增第五态 **`shared`**：`{"kind": "shared", "horizons": ["short","medium"], "order": <顶层键序还原位>}`，expand 时把顶层 mdc 注入各档 `market_data_context` | 沿用 `per_horizon`（掩码表明顶层是按档重建的虚拟键）；档内 mdc 保留为权威，**不**进 `shared` 分支 |
| `detect_alias_conflicts`（`:216-220`） | 顶层 mdc 匹配不到任何档内 mdc → `unreconstructible` 冲突 | v2 `shared` 行：顶层 mdc 是权威源，**跳过**「顶层必须匹配档内」检测；反向要求 `shared` 掩码存在且 `horizons` 覆盖全部实体档，否则冲突 | 同 v1：顶层 mdc（若存在）必须能还原到某档 mdc，否则 `unreconstructible` |
| `canonicalize_for_single_write`（`:411` 起，`is_canonical_storage` 分支） | v1 行直接 `strip_compat_view_for_persist` | v2 行：剥 `horizons` 与档内 `market_data_context` 虚拟副本，**保留**顶层 `market_data_context` 权威位；新写入由写漏斗直接产 v2 形态 | 同 v1 处理档内 mdc 权威行 |
| `expand_compat_view`（`:541` 起，`not is_canonical_storage` 分支） | 非 canonical → 原样返回 | v2 行：先 `detect_alias_conflicts`（含 `shared` 语义），再把顶层 mdc 深拷贝注入各档；`top_key_order` 还原顶层键序 | 档内 mdc 原样透出；顶层按 `per_horizon`/`absent` 掩码还原 |
| `strip_compat_view_for_persist`（`:673` 起） | canonical 行剥 `_VIRTUAL_TOP_KEYS` | v2 行：`horizons` 照剥；`market_data_context` 仅在 `top_market_context.kind != "shared"` 时剥（`shared` 即新版 `_top_mdc_kept` 等价豁免位） | 同 v1 |

**要点**：`shared` 行的顶层 mdc 是数据本体，不是重建视图——`strip` 与 `canonicalize` 分支必须以 `top_market_context.kind == "shared"` 作为「此 mdc 是权威物理位」的判别，等同于对现有 `_top_mdc_kept` 豁免（`kind == "absent"` + 键物理存在）的 v2 对应物。实现卡须在 `detect_alias_conflicts`、`canonicalize_for_single_write`、`expand_compat_view`、`strip_compat_view_for_persist` 四处同步接入 `shared`/`per_horizon_keep` 分支，缺一即撞 fail-close 或剥掉权威数据。

### 2.2 ② attempts 只存一处——**方向改为「保留顶层 `ids.attempts` 为权威」，且落地前须完成分层统计（DAV-1757 🔴-2）**

**实测事实**（DAV-1710 全量）：顶层 `ids.attempts` 与该档 `round_messages[].attempts` 展平数组**同文**——short 728/728、medium 527/527 组全等，7,988/7,988 条顶层记录与消息内匹配；另存在 5 个 short 切片只有消息内 attempts（36 条）而无顶层 attempts；消息内总条数 8,024 > 顶层 7,988。

**DAV-1757 复审代码实测推翻「消息内为权威」口径**（`debate_utils.py` `update_debate_state_with_payload`，主干 `:2007-2035`）：

- `sanitized_attempts` 为空（一次通过、无重试）时，`round_msg` **不写 `attempts` 键**（`if sanitized_attempts:` 才写），但同一分支的 `else:` 会向 `state_attempts` **追加一条全新合成记录** `{"attempt_index":1, "message_index", "debate_round", "speaker", "parse_status":"valid", "accepted":True, "error_detail":"", "raw_response": raw_response}`，并以 `"attempts": state_attempts` 落库。**即每条一次通过的发言都在顶层产生一条含 `raw_response` 的记录，而对应 `round_messages[i]` 没有 `attempts` 键**。
- risk 域更严重：`risk_mgmt/{aggressive,conservative,neutral}_debator.py:67` 调用时不传 `attempts=`，`sanitized_attempts` 恒为空——`risk_debate_state.attempts` 的**全部**记录都是含 `raw_response` 的合成记录，`round_messages` 里一条对应项都没有。
- 这些 `raw_response` **不是冗余副本**：`api/services/report_service.py:396-419` `_is_persisted_debate_attempt_path` 明确 `attempts[*].raw_response` 是 "the only places production carries a model block verbatim"，DAV-1432 置信度降级豁免正建立在顶层这条路径上。

**由此修订的口径（设计裁定）**：

- **权威位定为顶层 `ids.attempts`**（承载 `raw_response` 唯一副本与合成记录）；`round_messages[].attempts` 是按消息分组的重试记录副本，为派生冗余。
- 原「删顶层、留消息内」口径**作废**——按主干代码删除顶层 `attempts` 会不可逆删除 `raw_response` 全库唯一副本并使 DAV-1432 置信度降级链路失效，不是去重。
- ② 的物理形态降级为 **待 §2.2.1 统计完成 + 总控裁定**，与 ③ 同级对待。默认建议形态（供裁定参考）：v2 行保留顶层 `ids.attempts` 为唯一权威，**不写** `round_messages[].attempts`（消息内不再存副本）；读取视图对 v2 行把顶层 attempts 按 `(message_index, attempt_index)` 回填到对应 `round_messages[i].attempts` 以还原旧视图。
- §0 的 135,926,700 B 与 §4 的 424.6 MB **标注为上限待核**：其中有多少字节属于 `raw_response` 唯一副本（不可删）须由下方统计分离。

#### 2.2.1 落地前置统计（必做，B-5 实现卡或独立核实卡执行）

对全库分层统计以下口径，按 domain 分 `investment` / `risk` 两列：

1. 顶层 `attempts` 中、在 `round_messages` 内找不到对应项的记录数及其 `raw_response` 字节占比（按代码判定必非零；risk 域应为全部）。
2. `round_messages[].attempts` 中、与顶层记录同文的字节量（真去重收益）。
3. 两方向不对称行清单（只有顶层无消息内 / 只有消息内无顶层）。

统计结果出来前，② 不得进入实现；结果若证实「删消息内副本」无损，方按上面默认建议形态写 v2。**DAV-1710 的匹配口径（按 `(attempt_index, message_index)` 配对还是逐字段相等、是否只限 `investment_debate_state`）须由总控回查 DAV-1710 脚本产物后再定**——本仓库内该脚本不在候选分支也不在主干，无法本地核实（DAV-1757 已标存疑）。

**读取视图（按修订口径，权威 = 顶层）**：

| 行形态 | 读取视图行为 |
|---|---|
| A 旧行 / v1 行（顶层 `attempts` 与消息内 `attempts` 均物理存在） | 原样返回，不重建（物理键优先）。 |
| **v2 行（只存顶层 `attempts`，消息内无 `attempts`）** | `expand` 时按 `round_messages` 序遍历、以顶层 `attempts` 的 `(message_index, attempt_index)` 归属把各条**深拷贝**注入 `round_messages[i].attempts`；顶层数组原样保留。 |
| v2 行且消息内仍物理存 `attempts`（异常） | 与顶层对应记录 `_values_equal` 比对，不等 → `StorageCompatConflict`（fail-close）。 |
| 只有消息内无顶层的存量行（DAV-1710 实测 5 个 short 切片） | 属 v1/A 行形态，原样透出；v2 写侧不再产生此形态。 |

### 2.3 ③ history 读取时拼装（`ids.history` 不落库）

**写侧现状**（`debate_utils.py::update_debate_state_with_payload`）：`history` 与 `bull_history`/`bear_history` 同步追加——`"history": _append_history(state.get("history",""), argument)` 与 `history_key` 行同式（`:2068-2069`）。

**`_append_history` 精确语义**（`:2177-2181`，DAV-1757 复核）：`existing = str(history or "").strip()`；**existing 为空时直接返回 `argument`（不 strip）**，非空才返回 `existing.strip() + "\n" + argument`。即最终串 = `arg1`（原样保留首尾空白）+ `"\n" + arg2` + …，与简单 `"\n".join(...)` 在 `arg1` 含首尾空白时逐字节不等——拼装算法必须显式复刻「首条原样、后续追加」语义。

**拼装源过滤（DAV-1757 🟡-1，本卡修正为设计约束而非待验证）**：`_record_unstructured_response`（`:1485`）在 `domain == "investment"` 分支（`:1528-1570`）只写 `round_messages` 与 `attempts`，**`updates` 里没有 `history`/`history_key`**（非 investment 分支 `:1599-1600` 才有）。即 `investment_debate_state` 中**解析失败的发言进了 `round_messages` 但不进 `history`**。拼装时**必须过滤这些条目**：判别条件为 `round_messages[i].accepted is True` 且 `parse_status == "valid"`（有效轮标记；`_record_unstructured_response` investment 分支写 `accepted: False`）。实现侧建议 v2 写入掩码 `storage_compat.history_source = {"kind": "round_messages_accepted", "domain": "investment"}` 显式记录拼装规则。

**同源字段已确认**（DAV-1757 复核结论，从待验证项移除）：成功路径 `cleaned_response = strip_tagged_json(raw_response, marker)`（`:1803`）→ `"cleaned_prose": cleaned_response`（`:1983`）→ `argument = f"{speaker_label}: {cleaned_response}"`（`:2033`）——**三者是同一变量**，`round_messages[].cleaned_prose` 与写入 `history` 的 `cleaned_response` 逐字节必然一致，不存在二次清洗差（失败路径 `_quarantine_rejected_machine_blocks` 同样作用于同一变量）。

**范围**：本节 `ids` 仅指 `investment_debate_state`。`risk_debate_state`（`build_empty_risk_debate_state` `:2208` 起同样有 `history` + `aggressive_history`/`conservative_history`/`neutral_history` 四个字段）与 `risk_feedback_state` **默认不在 ③ 范围**——其写入路径与 investment 不同（`_record_unstructured_response` 非 investment 分支写 `history`，过滤规则相反），若纳入须另测拼装过滤口径。**是否在范围由总控裁定**。

**DAV-1710 实测警示**：`history = bull_history || bear_history` 直接文本相等仅 short 1/743、medium 0/527——**不能**用 `bull + bear` 拼接还原；必须从 `round_messages` 按序取 `speaker_label: cleaned_prose` 拼装（与 `_append_history` 的写入语义同构）。此外 DAV-1710 仅测字段删除字节，**未验证现场拼装可逐字节还原**——可还原性列为 §3 验收硬指标，不预设已成立。

**目标物理形态（v2）**：`investment_debate_state.history` 不落库（`bull_history`/`bear_history` 保留——DAV-1710 另列的「删三字段」是范围选择，默认只删 `history`）。

**读取视图**：

| 行形态 | 读取视图行为 |
|---|---|
| A 旧行 / v1 行（`ids.history` 物理存在） | 原样返回。 |
| **v2 行（`ids.history` 缺失）** | `expand` 时按 `round_messages` 序**只取 `accepted is True` 且 `parse_status == "valid"` 的条目**，各取 `f"{speaker}: {cleaned_prose}"`，首条原样、其后各条以 `"\n"` 前缀追加（复刻 `_append_history` 空基线语义：`existing.strip()` 为空则返回 argument 原样），拼装 `history` 注入视图（深拷贝）。`round_messages` 为空时 `history = ""`。 |
| v2 行 `ids.history` 仍存在（异常） | 与拼装结果 `_values_equal` 比对，不等 → `StorageCompatConflict`。 |

**⚠️ 待验证项**（收敛后仅剩一条）：`round_messages` 拼装过滤规则（`accepted`/`parse_status`）能否在全库样本上**逐字节复现**已存 `history`——尤其是存在失败发言的档。验证方式见 §3.2-4，**未全量验证前不得开启 v2 写**。

## 3. 「逐字相同」验收方案（对照面与比对方法）

延续 B-1/B-4 的验收范式：所有转换均为**读取层投影 + 可选离线迁移**，验收核心是「同一报告在新旧口径下的读取视图逐字节一致」。

### 3.1 对照面（哪些输出必须逐字相同）

| # | 对照面 | 说明 | 既有先例 |
|---|---|---|---|
| A | **兼容视图展开结果** | 对同一物理行，`expand_compat_view` 在 B-5 前后的输出 `result_data` 视图做 `json.dumps(view, ensure_ascii=False)` **不带 `sort_keys`** 的 UTF-8 字节序列相等比对（与 B-4 `_view_bytes` 口径对齐：去掉 `storage_compat`/`storage_schema_version` marker 键后比对）。**`sort_keys=True` 只作值等价辅助口径，不构成逐字验收判据**——它会抹掉键序，无法检出键序回退，而 B-1 的全部价值正是按掩码还原键序/null/缺失。 | B-1 验收：505 份全别名行 expand 全等价；B-4 `_view_bytes` 逐行事务内验收 |
| B | **`get_report` API 输出** | 固定 `report_id`，B-5 前后 `GET /api/v1/reports/{id}` 响应体逐字节相同（含虚拟键 `horizons` / 顶层 `market_data_context` / `ids.history` / `ids.attempts`）。 | B-1 `get_report` detached shadow |
| C | **下游读取脚本产物** | `weekly_report` / `daily_snapshot_ledger` / `historical_cases` / `shadow_credit` 对同一批报告的产物 JSON 逐字节相同。 | B-1 的 H1b 门槛两 cohort 产物逐字节相同 |
| D | **`encode_canonical`/`encode_v2` 往返** | `expand(encode(legacy_row))` 对样本行逐字节还原（不带 `sort_keys` 的字节相等；键序、null、缺失按 `storage_compat` 掩码）。 | B-1 的 22 例往返测试、B-4 逐行验收 |
| E | **写路径产物** | 同一输入下，B-5 写漏斗产出的 canonical v2 行，经 `expand` 后与旧漏斗产出的 A 形状行视图逐字节一致。 | B-2 的 canonicalize 漏斗一致性 |

### 3.2 比对方法（如何证明逐字相同）

1. **提取值级**：`json_extract` 后 `length(CAST(v AS BLOB))` 字节相等 + `json(v1) IS json(v2)` 值相等（SQLite minify 后语义等）。适用：全库快速全量核对（DAV-1710 口径）。
2. **视图级（更强口径）**：`json.dumps(expand(row), ensure_ascii=False)` **不带 `sort_keys`** 的 UTF-8 字节序列相等（marker 键按 B-4 `_view_bytes` 剔除）。适用：抽样 + 全部转换行的迁移验收。`sort_keys` 版仅作「值等价」辅助口径并须标注不构成逐字判据。**注意**：SQLite `json_extract` 会压缩非字符串空白，「提取值相等 ≠ 原 JSON 片段连空白逐字节相同」——DAV-1710 复审已明确该边界，B-5 验收须按 DAV-1715 🟢 建议写明基线是「minified B 投影」，避免误抄原始 `result_data` 口径（两者差恒定 115,768,828 B 空白）。
3. **逐行迁移验收**（若做存量转换）：B-4 机制复用——keyset 分批、事务内 `expand(encode(row)) == row` 逐字节核对后再提交；journal 记录 `report_id`/前后 sha256，可中断续跑。
4. **拼装正确性验收（history 专属）**：对全部 `ids.history` 存在的行，离线验证「按 §2.3 过滤规则（`accepted`/`parse_status` + 首条原样语义）从 `round_messages` 拼装」与已存 `history` 逐字节相等；不等者列入例外清单，**未全量验证前不得开启 v2 写**（fail-close）。

### 3.3 验收门槛（参考 B 链既有门禁）

> 本节门槛针对 **B-5 实现卡**（代码改动卡），本卡为纯文档（0 tests）不适用。

- 单进程 peak footprint ≤ 4 GiB（`/usr/bin/time -l`）；全库扫描用 `json_extract`/`json_each`，不整份反序列化。
- RT-FULL：`-q -p no:randomly`，`DATABASE_URL` 指向隔离临时库，追加 `--deselect tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback::test_single_horizon_report_persists_and_reads_all_scale_fields`（DAV-979 主干既有死锁，非候选引入）。
- 解释器：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`（Python 3.10.20，`env -u PYTHONPATH`）。
- 转换/回退演练参照 DAV-1650（10-12 全流程 + 回退实测）在临时目录完成，不碰生产库。

## 4. 旧行处置（不改写原则下）

**原则：存量行不改写、不删除、不强制迁移。** B-5 默认只做**读取层扩展**——v1/A 行在读取视图下语义保持不变，v2 只约束**新写入**。是否做存量 v1→v2 迁移属范围裁定（总控定），不是默认动作。

| 行类别 | 处置 |
|---|---|
| A 旧行（物理 `horizons` + 双别名） | **不动**。读取视图原样透出（B-1 已保证）。B-4 已把带 `horizons` 的行转为 v1；B-5 不要求再转 v2。 |
| v1 canonical 行（B-2 新写 + B-4 转换） | **不动**。档内各自 mdc、顶层 attempts、history 均物理存在，读取视图已正确。若要吃到去重收益需另立存量迁移卡，且只能对 260 对 mdc 相等行无损合并；634 对不等行迁移时必须保留双份（`per_horizon_keep`）。 |
| B-5 落地后的新写入 | 写漏斗直接产 v2：档内 mdc 单存顶层（`shared`）或保留（`per_horizon_keep`）；`ids.attempts` 方向按 §2.2 修订口径（顶层权威，消息内不写副本，**前提：§2.2.1 统计证实无损**）；`ids.history` 不写（前提：§3.2-4 拼装验证通过）。`storage_schema_version` 升 `v2`，`storage_compat` 掩码扩录 `shared_market_context`/`top_market_context.kind = "shared"` / `history_source` / `attempts_source`。 |
| 冲突/不可还原行 | fail-close：保持存储形态不动、记日志入例外清单（同 B-1 `result_data_compat_view` 语义）。 |

**迁移可选性**：若总控裁定做存量瘦身，只对 mdc 相等行 / attempts 同文行（限消息内副本方向）/ history 可拼装行做 v1→v2；每行迁移前后 `expand` 视图逐字节验收（§3.2-3）。估算无损收益**上限待核** ≈ 180.3 MB（mdc 相等部分）+ ≤135.9 MB（attempts，须扣 `raw_response` 唯一副本字节，§2.2.1 统计后核定）+ 108.4 MB（history）≈ **≤424.6 MB**（占 B 投影基线 ≤17.4%），与 B-4 的库文件级瘦身正交（B-4 只省文件物理空间，B-5 省 `result_data` JSON 字节）。attempts 分项在 §2.2.1 统计完成前不得作为裁定依据。

## 5. 待裁定与待验证清单（报总控定范围）

1. **【待全库核实】** DAV-1752 结果未出——全库按表/按列字节 top10（重点 `llm_call_logs` 等非报告表）出来后，由总控定 B-5 是否扩范围到非 `reports` 表，或仍限于 `result_data` 内部。
2. **mdc 合并范围**：只对 260 对相等行单存（无损 180.3 MB），还是对 634 对不等行也合并（多 176 MB 但有损、需保留双份）？本设计默认**只合相等行**，不等行 `per_horizon_keep`。
3. **② attempts 口径裁定**（DAV-1757 🔴-2 修订）：顶层 `ids.attempts` 为 `raw_response` 唯一副本载体，**必须保留为权威**；待 §2.2.1 分层统计（顶层独有记录数与 `raw_response` 字节占比，按 investment/risk 分）完成后由总控定「删消息内副本」是否落地；DAV-1710 匹配口径须总控回查。
4. **history 拼装可还原性**：已收敛为「过滤规则全库逐字节验证」一项（`cleaned_prose` 与 `cleaned_response` 同源已由代码确认，DAV-1757）；验证不通过则 ③ 降级为「history 照存 + 只删冗余字段」。`risk_debate_state`/`risk_feedback_state` 是否纳入由总控裁定。
5. **是否做存量 v1→v2 迁移**：本设计默认只做读取层扩展 + 新写入 v2；存量迁移收益 ≤424.6 MB（上限待核）vs 迁移风险，由总控定。
6. **`round_messages[].attempts` 与顶层 `ids.attempts` 反向行**：5 个 short 切片只有消息内无顶层——该形态在修订口径下属存量异常行，原样透出、不迁移；新写入以顶层为唯一权威（§2.2 已定，此处仅登记）。

## 6. 本卡交付边界

- 仅 `docs/dav-1753-b5-storage-dedup-design.md` 一个文件；上一候选 `agent/1/e35f888cd32b` @ `a4af9a0`（父 `c065209`）经 DAV-1757 复审打回，本卡为修订版（2🔴+4🟡+3🟢 全部处理），新分支/SHA 见交付评论。
- 未触碰任何代码、测试、主干；未调模型；未访问数据库。
- 遗留：DAV-1752 全库核实、§2.2.1 attempts 分层统计、history 拼装过滤全库验证、范围裁定、实现卡（B-5 落地，父须为 B 链 staging tip 含 B-4 摘入）均未执行，由总控/调度后续安排。
