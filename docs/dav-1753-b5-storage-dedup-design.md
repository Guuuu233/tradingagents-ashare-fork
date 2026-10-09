# B-5 设计文档：兼容读取层扩展 / 旧行处置 / 逐字相同验收

> 总控 2026-10-09 令 · 依据 DAV-1615 体量普查 + DAV-1710/1715 全量核查实测
> 基线：`codex/dav-4-p2a-trunk` tip `c065209`（本分支 `agent/1/e35f888cd32b` 与主干同步，新增本文档）
> **只写文档；无代码改动；不实施任何下述转换。**

## 0. 目标与依据

DAV-1615 裁定了三条瘦身方向，B-5 为其设计与落地方案（本卡只写设计）：

| 裁定 | 内容 | DAV-1710 实测节省（B 投影基线 2,441,585,943 B / 1,016 份双档报告） |
|---|---|---|
| ① 两档共用一份行情上下文 | `short_term.market_data_context` 与 `medium_term.market_data_context` 全库去重单存 | 实测相等部分 180,272,987 B（7.38%）；若对 634 对不等值也合并则为 356,267,568 B（14.59%，非无损，**待裁定是否纳入**） |
| ② attempts 只存一处 | `ids.attempts` 顶层数组与 `round_messages[].attempts` 展平二选一 | 135,926,700 B（5.57%），口径为「保留消息内、删顶层」 |
| ③ history 读取时拼装 | `ids.history` 不落库，读取时由 `bull_history`/`bear_history`/`round_messages` 现场拼装 | 108,384,887 B（4.44%，仅删 `history`）；删三字段 216,796,446 B（8.88%，另列） |

**边界与待办**：本卡标注「**待全库核实**」——DAV-1752（B-5 全库存储核实，按表按列字节+行数 top10，进行中）结果出来后报总控定最终范围。本设计以已完成的 DAV-1710/1715 全量数据为准；DAV-1752 的按表汇总只影响是否扩大范围（例如把非 `reports` 表一并纳入），不改变下述机制设计。

## 1. 现状架构（B-1 ~ B-4 已就位）

- **唯一物理存储**（D-072 附注裁定）：`reports.result_data` 每档一份权威切片 `short_term` / `medium_term`；`horizons.<h>` 与顶层 `market_data_context` 为**读取层虚拟键**，canonical 行（`storage_schema_version = "result_data.storage.v1"`）不再物理写。
- **兼容层**：`tradingagents/storage/result_data_compat.py`（DAV-1506，B-1）。核心 API：
  - `encode_canonical(result_data)`：旧双别名行 → canonical，录 `storage_compat` 掩码（`horizons_order` / `horizon_key_masks` / `top_market_context` / `top_key_order`）；别名冲突 `StorageCompatConflict` 失败关闭。
  - `expand_compat_view(result_data)`：canonical → 旧视图（深拷贝），按掩码逐字还原键序 / null / 缺失。
  - `result_data_compat_view`：冲突日志 + 保持原样的安全入口（读边界接线点已接：`get_report`、`scheduler/main` 通知装载、`historical_cases`、`daily_snapshot_ledger`、`weekly_report`（DAV-1651））。
  - `strip_compat_view_for_persist`：写边界剥虚拟键，防兼容视图赋回 ORM 后整段写回。
- **写侧**（B-2，DAV-1545）：`canonicalize_for_single_write` 统一漏斗，新写入即 canonical。
- **存量转换**（B-4，DAV-1614）：`scripts/slim_result_data_storage.py` 逐行 `encode_canonical` + 事务内逐字验收 + journal 续跑 + VACUUM 新库；范围仅限**物理带 `horizons` 的行**。
- **B-3**（DAV-1572）：回填脚本只写 `*_term` 权威档，存量物理别名字节保留。

## 2. B-5 三项机制设计

三条裁定对应兼容层三个方向的扩展。总则：**旧行不改写、不删除；读取视图对所有行给出语义正确的结果；冲突一律失败关闭（fail-close）。**

### 2.1 ① 两档共用一份行情上下文（`market_data_context` 去重单存）

**目标物理形态（v2，新写入与可选转换的产物）**：

```
result_data.storage_schema_version = "result_data.storage.v2"
result_data.market_data_context = <单一行情上下文>          # 档间共用，只存一处
result_data.<h>_term.market_data_context 不再物理存         # 档内副本移除
result_data.storage_compat.shared_market_context = {"order": ["short","medium"], ...}
```

- 顶层 `market_data_context` 从「虚拟键」升级为 v2 行的**唯一权威存储位**；档内副本成为虚拟键，由读取层还原到 `short_term.market_data_context` 与 `medium_term.market_data_context`。
- **不一致值处置**：DAV-1710 实测 894 对双边 mdc 中 260 对相等、634 对不等。相等的 260 份可无损合并；不等的 634 份**必须保留两档各自的 mdc**（不能选一边丢另一边）。方案：v2 行允许 `storage_compat.shared_market_context.kind = "per_horizon_keep"`，此时档内 mdc 仍是物理权威（等同 v1），读取视图原样返回；只有 `kind = "shared"` 时才走单存还原。**合并范围裁定属总控**（见 §4 待裁定项）。

**读取视图（对旧行/新行分别给出的视图）**：

| 行形态 | `storage_schema_version` | 读取视图行为 |
|---|---|---|
| A 旧行（物理 `horizons` + 顶层 mdc + 档内 mdc） | 无 / 其他 | B-1 语义：`expand_compat_view` 原样返回（`is_canonical_storage` 判否），不做任何改动。 |
| v1 canonical 行（B-4 转换产物：无 `horizons`、无顶层 mdc、档内各有 mdc） | `result_data.storage.v1` | 走现有 B-1 路径：`horizons.<h>` 由 `horizon_key_masks` 从 `*_term` 重建；顶层 `market_data_context` 按 `top_market_context`（`per_horizon`/`slice`/`null`/`absent`）重建。档内 mdc 各自独立，视图与物理一致。 |
| **v2 行（B-5 新写入）** | `result_data.storage.v2` | 新增分支：档内 `market_data_context` 视为虚拟键，`expand` 时把顶层单一 mdc **深拷贝**注入 `short_term` 与 `medium_term` 两个切片；`horizons.<h>` 与顶层键序仍按 `storage_compat` 掩码还原。两档视图中的 mdc **逐字节相同**（同一来源）。 |
| v2 + 保留档内不一致 mdc（`shared_market_context.kind = "per_horizon_keep"`） | `result_data.storage.v2` | 档内 mdc 为权威，视图原样透出；顶层 mdc 按掩码重建（若为 `absent` 则不补）。 |

`is_canonical_storage` 判定扩为 `storage_schema_version in {v1, v2}`；v1 逻辑完全冻结，v2 仅追加「档内 mdc 虚拟化」一条还原规则。

### 2.2 ② attempts 只存一处（`ids.attempts` 顶层删除，读取时从 `round_messages` 展平）

**实测事实**（DAV-1710 全量）：顶层 `ids.attempts` 与该档 `round_messages[].attempts` 展平数组**同文**——short 728/728、medium 527/527 组全等，7,988/7,988 条顶层记录与消息内匹配。但存在 **5 个 short 切片有消息内 attempts 共 36 条、而无顶层 attempts**；消息内总条数 8,024 > 顶层 7,988。

**口径裁定**：DAV-1710 的测算口径是「**保留消息内 `round_messages[].attempts`、删除 `ids.attempts` 顶层数组**」。本设计采用同一口径（消息内为权威，顶层为派生）——理由：消息内记录与发言消息天然同址，且全库中存在「只有消息内、没有顶层」的行（反向会丢数据）。

**目标物理形态（v2）**：`investment_debate_state.attempts` 不再物理写入；`round_messages[].attempts` 保持原样（已是唯一副本）。

**读取视图**：

| 行形态 | 读取视图行为 |
|---|---|
| A 旧行 / v1 行（`ids.attempts` 物理存在） | 原样返回，不展平（物理键优先于虚拟键）。 |
| **v2 行（顶层 `attempts` 缺失）** | `expand` 时按 `round_messages` 数组顺序、再按各 `attempts` 数组顺序**展平**消息内记录，重建成顶层 `attempts` 数组（深拷贝注入视图）。重建键序与元素序 = 消息内顺序（DAV-1710 已证二者同文同序）。 |
| v2 行且「无顶层 attempts 却有消息内 attempts」 | 展平结果注入视图顶层（补回虚拟键），与旧行为一致——旧行顶层缺失误差恰好也在消息内。 |

冲突检测：v2 行若物理顶层 `attempts` 仍存在，必须与展平结果 `_values_equal`，不等则 `StorageCompatConflict`（同 B-1 对物理别名的处理）。

### 2.3 ③ history 读取时拼装（`ids.history` 不落库）

**写侧现状**（`debate_utils.py::update_debate_state_with_payload`）：`history` 与 `bull_history`/`bear_history` 同步追加——`"history": _append_history(state.get("history",""), argument)` 与 `history_key` 行同式；`_append_history` 语义 = `existing + "\n" + argument`（首条无前缀）。每档 `history` = 该档全部 `"{speaker}: {cleaned_response}"` 按 `round_messages` 序串接。

**DAV-1710 实测警示**：`history = bull_history || bear_history` 的直接文本相等仅 short 1/743、medium 0/527——**不能**用 `bull + bear` 拼接还原；必须从 `round_messages` 按序取 `speaker_label: cleaned_prose` 拼装（与 `_append_history` 的写入语义同构）。此外，DAV-1710 仅测了字段删除字节，**未验证现场拼装可逐字节还原**——本设计把「可还原性」列为验收硬指标（§3），不预设已成立。

**目标物理形态（v2）**：`investment_debate_state.history` 不落库（`bull_history`/`bear_history` 保留——DAV-1710 另列的「删三字段」是范围选择，默认只删 `history`）。

**读取视图**：

| 行形态 | 读取视图行为 |
|---|---|
| A 旧行 / v1 行（`ids.history` 物理存在） | 原样返回。 |
| **v2 行（`ids.history` 缺失）** | `expand` 时对该档 `investment_debate_state`，按 `round_messages` 序取各 `"{speaker_label}: {cleaned_prose}"` 用 `"\n"` 连接，拼装 `history` 注入视图（深拷贝）。拼装算法与 `_append_history` 同构；`round_messages` 为空时 `history = ""`（与 `_append_history` 空基线一致）。 |
| v2 行 `ids.history` 仍存在（异常） | 与拼装结果 `_values_equal` 比对，不等 → `StorageCompatConflict`。 |

**⚠️ 待验证项**：拼装还原要求每条 `round_messages[i]` 的 `speaker`/`cleaned_prose` 字段足以唯一还原写入时的 `argument`。`debate_utils.py` 中 `argument = f"{speaker_label}: {cleaned_response}"`，而 `cleaned_prose` 字段经 `sanitize_debate_response`/`_quarantine_rejected_machine_blocks` 清洗——需确认 `round_messages[].cleaned_prose` 落库值与写入 `history` 时的 `cleaned_response` **逐字节一致**（无二次清洗）。若存在差异，`history` 拼装需改为「保留 `round_messages[].raw_response` 重跑清洗」或接受近似还原——**属待验证，不预设**。

## 3. 「逐字相同」验收方案（对照面与比对方法）

延续 B-1/B-4 的验收范式：所有转换均为**读取层投影 + 可选离线迁移**，验收核心是「同一报告在新旧口径下的读取视图逐字节一致」。

### 3.1 对照面（哪些输出必须逐字相同）

| # | 对照面 | 说明 | 既有先例 |
|---|---|---|---|
| A | **兼容视图展开结果** | 对同一物理行，`expand_compat_view` 在 B-5 前后的输出 `result_data` 字典逐字节相同（`json.dumps(..., sort_keys=True)` 或对既有 JSON 文本做 `length(CAST AS BLOB)` 比对）。 | B-1 验收：505 份全别名行 expand 全等价；B-4 逐行事务内验收 |
| B | **`get_report` API 输出** | 固定 `report_id`，B-5 前后 `GET /api/v1/reports/{id}` 响应体逐字节相同（含虚拟键 `horizons` / 顶层 `market_data_context` / `ids.history` / `ids.attempts`）。 | B-1 `get_report` detached shadow |
| C | **下游读取脚本产物** | `weekly_report` / `daily_snapshot_ledger` / `historical_cases` / `shadow_credit` 对同一批报告的产物 JSON 逐字节相同。 | B-1 的 H1b 门槛两 cohort 产物逐字节相同 |
| D | **`encode_canonical`/`encode_v2` 往返** | `expand(encode(legacy_row))` 对样本行逐字节还原（键序、null、缺失按 `storage_compat` 掩码）。 | B-1 的 22 例往返测试、B-4 逐行验收 |
| E | **写路径产物** | 同一输入下，B-5 写漏斗产出的 canonical v2 行，经 `expand` 后与旧漏斗产出的 A 形状行视图逐字节一致。 | B-2 的 canonicalize 漏斗一致性 |

### 3.2 比对方法（如何证明逐字相同）

1. **提取值级**：`json_extract` 后 `length(CAST(v AS BLOB))` 字节相等 + `json(v1) IS json(v2)` 值相等（SQLite minify 后语义等）。适用：全库快速全量核对（DAV-1710 口径）。
2. **视图级（更强口径）**：`json.dumps(expand(row), sort_keys=True, ensure_ascii=False)` 的 UTF-8 字节序列相等。适用：抽样 + 全部转换行的迁移验收。**注意**：SQLite `json_extract` 会压缩非字符串空白，「提取值相等 ≠ 原 JSON 片段连空白逐字节相同」——DAV-1710 复审已明确该边界，B-5 验收须按 DAV-1715 🟢 建议写明基线是「minified B 投影」，避免误抄原始 `result_data` 口径（两者差恒定 115,768,828 B 空白）。
3. **逐行迁移验收**（若做存量转换）：B-4 机制复用——keyset 分批、事务内 `expand(encode(row)) == row` 逐字节核对后再提交；journal 记录 `report_id`/前后 sha256，可中断续跑。
4. **拼装正确性验收（history 专属）**：对全部 `ids.history` 存在的行，离线验证「`round_messages` 拼装串」与已存 `history` 逐字节相等；不等者列入例外清单，**未全量验证前不得开启 v2 写**（fail-close）。

### 3.3 验收门槛（参考 B 链既有门禁）

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
| B-5 落地后的新写入 | 写漏斗直接产 v2（档内 mdc 单存顶层、顶层 `ids.attempts` 不写、`ids.history` 不写）；`storage_schema_version` 升 `v2`，`storage_compat` 掩码扩录 `shared_market_context` / `history_source` / `attempts_source`。 |
| 冲突/不可还原行 | fail-close：保持存储形态不动、记日志入例外清单（同 B-1 `result_data_compat_view` 语义）。 |

**迁移可选性**：若总控裁定做存量瘦身，只对 mdc 相等行 / attempts 同文行 / history 可拼装行做 v1→v2；每行迁移前后 `expand` 视图逐字节验收（§3.2-3）。估算无损收益上限 ≈ 180.3 MB（mdc 相等部分）+ 135.9 MB（attempts）+ 108.4 MB（history）≈ **424.6 MB**（占 B 投影基线 17.4%），与 B-4 的库文件级瘦身正交（B-4 只省文件物理空间，B-5 省 `result_data` JSON 字节）。

## 5. 待裁定与待验证清单（报总控定范围）

1. **【待全库核实】** DAV-1752 结果未出——全库按表/按列字节 top10（重点 `llm_call_logs` 等非报告表）出来后，由总控定 B-5 是否扩范围到非 `reports` 表，或仍限于 `result_data` 内部。
2. **mdc 合并范围**：只对 260 对相等行单存（无损 180.3 MB），还是对 634 对不等行也合并（多 176 MB 但有损、需保留双份）？本设计默认**只合相等行**，不等行 `per_horizon_keep`。
3. **history 拼装可还原性**：未验证 `round_messages` 能否逐字节还原 `history`（`cleaned_prose` vs 写入时 `cleaned_response` 的二次清洗差）。验证不通过则 ③ 降级为「history 照存 + 只删冗余字段」。
4. **是否做存量 v1→v2 迁移**：本设计默认只做读取层扩展 + 新写入 v2；存量迁移收益 424.6 MB vs 迁移风险，由总控定。
5. **`round_messages[].attempts` 与顶层 `ids.attempts` 反向行**：5 个 short 切片只有消息内无顶层——新写入应以消息内为唯一权威，读取视图补顶层虚拟键（§2.2 已定，此处仅登记）。

## 6. 本卡交付边界

- 仅 `docs/dav-1753-b5-storage-dedup-design.md` 一个文件，落在 `agent/1/e35f888cd32b` 分支（基线 `c065209`），单提交。
- 未触碰任何代码、测试、主干；未调模型；未访问数据库。
- 遗留：DAV-1752 全库核实、history 可还原性验证、范围裁定、实现卡（B-5 落地）均未执行，由总控/调度后续安排。
