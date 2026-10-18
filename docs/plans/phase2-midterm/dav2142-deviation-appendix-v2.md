# DAV-2142｜偏差附表 v2

本附表按 DAV-2199（总控 2026-10-17 令）新增，登记 P1 manifest 口径、DAV-2194 证据保全路径与散列、DAV-2195 裁定带来的四项准入规则、事前登记时点，以及 DAV-2198 实现追踪表。**候选文档，待总控对交付的 40 位提交 SHA 签字。** 本附表不覆盖、不修改 `dav2142-phase1-preregistration-v0.2.md`、`dav2142-freeze-register-v0.2.md` 与 `dav2142-deviation-appendix-v1.md`，不改数据、账本、标签、统计公式及既定显著性口径，不授权分析运行。

修订：2026-10-18 按总控 10-17 令增补 §4（已封存行 `custom` 证据来源规则，依据 DAV-2186 裁定、实现卡 DAV-2214），并按总控 10-18 令回填 DAV-2198 / DAV-2207 / DAV-2214 / DAV-2217 的合入 SHA（§5）。初版提交为 `7b1ecb31`。

## 1. P1 manifest 口径

冻结登记 §5 登记的 P1 来源为 DAV-2080 试跑所建 T+10 全市场面板的 `stats.json`（不是 T+40 面板，也不是 P4 自选样本）。**本附表认定：冻结登记中的「P1 manifest」即该 `stats.json`，不是另一个独立的 `manifest.json`。**

| 对象 | SHA256 | 大小 |
|---|---|---|
| `stats.json`（原生成路径 `.../dav-2080-fd1fed5c2507/workdir/out/base_t10/stats.json`） | `15f540140dc1f6956af1d595a31efef6360ad199d6b23320de11e9fcba1dd696` | 5473 B |

该文件的保全副本位于 `~/Documents/TradingAgents-AShare-cache/phase2/p1-base_t10-frozen/stats.json`（DAV-2194 保全目标）；本轮独立只读重算其 SHA256，与上表登记值一致。

该 manifest 为来源登记，不意味着本附表重算分区或基率；未来变更必须记偏差及新 manifest 哈希，沿用冻结登记口径。

依据：冻结登记 `dav2142-freeze-register-v0.2.md` §5 的 P1 来源登记；DAV-2194 交付评论 `01a148b7-e9f6-78a7-82fd-e47a41102b10`；DAV-2199 卡面裁定。

## 2. DAV-2194 证据保全路径与散列

| 证据 | 保全路径 | SHA256 / 大小 / 权限 |
|---|---|---|
| 10-15 数据库压缩快照 | `~/ta-work/evidence/dav2142/tradingagents-2026-10-15.db.zst` | `979adba9f8609859d68f524f948091fad4214271b0ff769bf4718bf4759928ce`；128,352,744 B；0444 |
| P1 基率面板 | `~/Documents/TradingAgents-AShare-cache/phase2/p1-base_t10-frozen/` | 23 文件逐项 SHA256 如下；文件 0444、目录 0555 |

面板下相对路径与 SHA256（23 文件）：

```text
23e42776cbb99c30311d8660e476b9f3d0555ec47ae78e13186ee785b0c459c5  chunks/year=2015/data.parquet
538bb0e15ab8ae4e081ed6124b1873859dbad8631e81ee42e9e892869a143559  chunks/year=2015/stat.json
4824dca03dc38200e2f0430ad3bbc12e414bcdd2729d67c04fe92ebce3f56792  chunks/year=2016/data.parquet
f8d4603a26f88243aba332126e4b6c7c48c7daf7aaadef4446b2f5401b22413f  chunks/year=2016/stat.json
aa8ae8681b772cde87a5c1d3ccf9fa06125b800187cf422fca1e623695cd571d  chunks/year=2017/data.parquet
338dffe95fce7bef666df7087871394ea79ca73f705f7b149127118e46115721  chunks/year=2017/stat.json
a25a13cc5e15a3db2677f7b8b001c12840a32d979ec28eae0c3ee9447ab5fb2f  chunks/year=2018/data.parquet
0c71b2320f6713b58cef224dc68bb707047c1328ce77f099a516cb00ae8e499e  chunks/year=2018/stat.json
7a7a1d01d2f42b11cc7bb0049b106ca1f81727bb80e7773adb45cfaa3a6488f5  chunks/year=2019/data.parquet
557663641d9139cb34c19f112b168bf4bab2078b04531593edcda6501161d256  chunks/year=2019/stat.json
ce5f1306363e591a0831c53d2a4a6ba8a8eff7ecaf0f6e5187807a67f4458d78  chunks/year=2020/data.parquet
8d8c5fc0f643a4bb79f19eea0d386d2c90f4d65fd64f1c443c2ca6808f0c263f  chunks/year=2020/stat.json
6c233b259bfc494301c4761fcc08caf101cb614c3084dfcf4fd6bcb605ce81a8  chunks/year=2021/data.parquet
945c57ef378824e90a6baf5a2261ca96840b23e4f2933191a6097fcadfec5a25  chunks/year=2021/stat.json
6edc17f5157da9d9bf3a8b23f254b3757255ac6edfe1eda55b9bcec5707763e9  chunks/year=2022/data.parquet
a858ccde162b5a64721070ccfb19c37a9bbebc4371b4f013b066c778cf6890fd  chunks/year=2022/stat.json
5c8dd3a40953d2e5e46affa17018b67a88fdaac49cf2654efb1a1b8cae591c1a  chunks/year=2023/data.parquet
cab789444a979dfa7b38bf5c40cb162611d5dddbaa21c8799d0def2a4a5b8e1f  chunks/year=2023/stat.json
8c77f1f489ac50019c6b287894b7bafd567315f912cf5f28e594cdcdae266bb0  chunks/year=2024/data.parquet
8739fe3e44d2b44b3be621b87a2f15c536936aad3d52334c5978acdd8baeb9d4  chunks/year=2024/stat.json
afae02d9691748d4c9b6b878f473cf4fa05a81c0a66131c166a417efc0b18f52  chunks/year=2025/data.parquet
7977550b896dc0be14cfdd3fe7c1ebafd9b1272236796de946a332e8d57a8c76  chunks/year=2025/stat.json
15f540140dc1f6956af1d595a31efef6360ad199d6b23320de11e9fcba1dd696  stats.json
```

目录计数：实际为 **13 个目录**（面板根目录、`chunks`、11 个 `year=` 目录），13/13 为 0555。DAV-2194 交付正文登记为「12/12 目录」，与其自身列举的结构不符；本附表登记实测值 13，不影响 23 文件散列与只读权限结论。

本轮（2026-10-17）只读独立核验：快照 zst 的 SHA256、128,352,744 B 大小及 0444 权限均与上表相符；面板 23/23 文件 SHA256 与 DAV-2194 交付清单逐项一致，23/23 文件 0444；目录 13/13 为 0555；`stats.json` 重算值与本附表 §1 登记值一致。原件未删未移、源目标逐项一致系引用 DAV-2194 执行人历史交付，本轮不冒充重新验证原件。本轮不执行写入探针，不改权限，不解压数据库，不运行数据分析。

## 3. DAV-2195 裁定对应的准入规则

以下四项为 DAV-2195 只读核验（评论 `01a148bb-b231-7c12-a024-06a4c4ce9cf1`）所列口径偏差，经总控 2026-10-17 裁定后登记为正式准入规则；实现卡为 DAV-2198。

1. **标签关联按 `report_id` + 档位**，并核对标签所指记录的散列与账本 `record_sha256` 一致。仅凭股票与信号日不足以建立关联（现有实现以 `(symbol, signal_date)` 建索引，后行覆盖前行，已核有 131 条记录会取到别的 `report_id` / `record_sha256` 的 t10 标签行）。散列不一致的记录排除并记录原因；**具体原因码以 DAV-2198 实现与复审证据为准，本附表不杜撰**。
2. **同键的 `pending` 或 `data_missing` 被后来的 `evaluated_ok` 取代，属正常状态推进**。同一 `(report_id, 档位)` 出现两行 `evaluated_ok` 且收益数值不同，**整份报告排除**，原因码固定为 **`label_conflict`**，并在输出首屏告警；不得后行覆盖前行而静默择一。标签文件保持**只追加、不改写**。
3. **正式队列起点 `2026-10-08` 必须在代码中强制执行**，取值依据为冻结登记；不能只依赖现有数据恰好不含更早的合格样本（现状：`p_rel_t10_baseline_eval.py` 无 start-date 参数或常量，起点靠数据自然成立）。
4. **时点及有效 PIT 判定取「账本 + 补判文件」**：账本 `VERIFIED` / `FAILED` 终态优先且不被补判覆盖；账本 `UNVERIFIED` 才查 `pit_reattest.jsonl`，沿用冻结计划规定的补判顺序（仅 `adjudicated` 参与、同 `report_id` 取最后一条、`no_evidence` 仅进诊断）。**不读取标签行 `input_pit_status` 作为正式准入依据**（现有标签行只抄账本原值，不含补判 overlay：正式窗口 168 条中 144 条须靠补判文件才转为 VERIFIED）。封存时点等其余门限沿用冻结计划，不在 v2 重新定义。

事实边界：DAV-2195 已核，10-08 起的正式窗口标签行数为 0（该窗口最早 T+10 到期日 `2026-10-22`，T+40 到期日 `2026-12-03`），现存 1,099 行标签全部落在正式队列之前；历史标签的关联检验不等于正式到期样本的验证。跨两次生成的 7 行收益类差异（`000776.SZ`、2026-08-19~08-28，均为 t10）与 19 行 `data_missing`（目标日 2026-10-15，t10 14 + t40 5）均在正式队列之外，**仅作为 DAV-2198 的实现测试夹具，不作为本附表的正式读数**。

## 4. DAV-2186 裁定对应的已封存行 custom 证据来源规则

**依据：DAV-2186 裁定**（`user_custom_prompts` 于 2026-10-11 07:31:06 UTC 的整组重建判为记录级重建、内容字节等价、版本键不切换；正式依据为逐报告 `custom_prompt_snapshot`，见 `dav2142-deviation-appendix-v1.md` §1）。哈希一律按冻结登记 §3 的规范化口径计算：`SHA256(UTF-8 JSON, sort_keys=True, ensure_ascii=False, separators=(',', ':'))`。**实现卡：DAV-2214。**

已封存行（账本记录）的 `custom` 证据来源按下列规则判定，逐行打 `custom_basis` 与 `custom_sha256`：

1. **账本行缺 `custom_prompt_snapshot`** 时，**按 `report_id` 从报告快照读 `result_data.custom_prompt_snapshot`**（只读读取共享快照），按冻结登记 §3 的规范化方式计算哈希，**标 `custom_basis=report_snapshot`**；
2. **账本自带的** `custom_prompt_snapshot` 用账本封存值，**标 `custom_basis=ledger`**；两处都有且哈希相同也记 `ledger`（封存值优先）；
3. **两处都缺** → **`custom_basis=unverifiable`**，**不得用登记值（冻结登记 §3 表中的 `b0ababd6…`）填补**；**两处都有但哈希不一致** → **标 `custom_basis=conflict`、原因码 `custom_conflict`，该行整份排除出正式队列，并在输出首屏告警，不静默择一**。

排除在版本选择之前执行（冲突行不因其两个版本本身即争议对象而触发多版本 fail-close）；单侧或缺失证据的行不被排除，按上述 basis 留在漏斗内。

事实边界：本条为总控 10-17 令增补，与 §3 的四项规则同属事前登记（见 §5）。本轮仅落文，未运行该实现、未读取报告快照或数据库、未复算任何 `custom` 哈希。

## 5. 登记时点与实施追踪

**上述 §3 四项规则与 §4 custom 证据规则均已在任何正式样本到期之前登记**：平台登记时间为 DAV-2199 卡面创建 `2026-10-17T10:25:57Z`，§4 增补令评论时间为 `2026-10-17T19:57:36Z`，仓库落文提交时间为 2026-10-17（初版）与 2026-10-18（本增量），均早于 DAV-2195 核验所列首个正式样本 T+10 到期日 `2026-10-22`。该事实指平台裁定登记与仓库落文时点，不等同于本附表已获总控签字、也不等同于实现已合入。

| 事项 | 实现卡 | 合入 SHA（含返修） |
|---|---|---|
| `report_id` + 档位关联、记录散列核对 | DAV-2198 | `4e29907b`（返修 `d046b878`，返修卡 DAV-2207） |
| `pending` / `data_missing` 正常推进、两行 `evaluated_ok` 冲突整份排除及首屏告警 | DAV-2198 | `4e29907b`（返修 `d046b878`，返修卡 DAV-2207） |
| 正式队列起点 `2026-10-08` 代码强制 | DAV-2198 | `4e29907b`（返修 `d046b878`，返修卡 DAV-2207） |
| 账本 + 补判时点判定，忽略标签 PIT 字段 | DAV-2198 | `4e29907b`（返修 `d046b878`，返修卡 DAV-2207） |
| 已封存行 custom 证据来源（`ledger` / `report_snapshot` / `unverifiable`；两处不一致排除 `custom_conflict`） | DAV-2214 | `bc543b25`（返修 `62029ff0`，返修卡 DAV-2217） |

摘入链：`e0fdd6ba` → `4e29907b` → `d046b878` → `bc543b25` → `62029ff0`（两条链完成后的主干 `62029ff0a0acb665199929465efb1dc177cb9f0d`）；其后主干 `60fd8bbd`（DAV-2157）、`2d60ebbb`（DAV-2215）与本附表无关。上述五个提交本轮均以 `git log origin/codex/dav-4-p2a-trunk` 实读核对，在 `codex/dav-4-p2a-trunk` 主干历史中。

补填说明：上述合入 SHA 按总控 2026-10-18 令回填，来源为主干提交历史；不用候选分支 SHA 冒充合入 SHA。若 v2 已经总控签字冻结，后续修订采用经总控确认的追加登记方式，保持已签字版本可追溯。

## 6. 本次核验与待签事项

本次（初版）仅新增本 Markdown 附表；2026-10-18 增量只修改本文件（新增 §4、回填 §5 合入 SHA）。核验范围为：相对主干 `codex/dav-4-p2a-trunk` 基线提交 `43ed66e0` 只新增本文件；`dav2142-deviation-appendix-v1.md` 与两个冻结文件（`dav2142-phase1-preregistration-v0.2.md`、`dav2142-freeze-register-v0.2.md`）的 blob 与 SHA256 相对基线不变；日期 zst 及面板 23 文件的完整 SHA256、大小与只读权限；`git diff --check`。

本次**0 项目 tests、0 SQLite 读取、0 账本写入、0 标签生成/改写、0 收益/IC/归因分析、0 模型或 provider 调用、0 合入、0 部署、0 重启**。本附表不宣称 DAV-2198 的代码已执行上述规则，不解锁正式读数。

待总控对交付的 40 位提交 SHA 签字。复审范围按总控令**只限于本次增补部分**：§4 新增段落与 §5 的合入 SHA 回填；§1–§3 的 manifest 口径、保全散列与四项准入规则文字相对初版 `7b1ecb31` 未改动。未合入、未重启、未上线，未解锁下游分析；不得将本候选文档的提交或已裁规则当作新增分析运行授权。
