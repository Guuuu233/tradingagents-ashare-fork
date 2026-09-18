# Social 舆情线 / H1b 实时对账（2026-09-18）

## 当前运行基线

- 远端主线：`codex/dav-4-p2a-trunk@8e49a809333868da73f98bd44bb070f0831bf23e`
- 8000 `/healthz`：`commit_sha=8e49a809333868da73f98bd44bb070f0831bf23e`
- H1b 候选：`4b540b0c9b08d77a12ce7d08cfdf0095288cd350`
- H1b 候选直接父：`8e49a809333868da73f98bd44bb070f0831bf23e`
- H1b 候选尚未合入，未因本对账重启服务、未切换生产代码。

## Social 主线已落地内容

远端 trunk 的 social 历史已包含：

- MediaCrawler 受控 ingestion / shadow / canary / report gate 等 P2-T7～T15a 链路；这些旧 dev2 分支均已是 trunk 的祖先，不得重复合并。
- `08f4017`：MediaCrawler SQLite 工作路径绑定修复。
- `c2a52a9`：Gate 0 sandbox evidence。
- `8e49a80`：Gate 2 preflight evidence。

当前 trunk 上针对 social 合同、导入、实体解析、聚合、archive provider、collector、分析师隔离、toolnode、防前视、report gate、rollout 的专项测试：

- **162 passed / 0 failed / 31.76s**
- 测试在精确 trunk `8e49a80` 的独立 worktree、`env -u PYTHONPATH`、Python 3.10.20 下执行。

## 仍在远端但未进入 trunk 的 Cursor 分支

| 分支 | SHA | 状态 | 处理规则 |
|---|---|---|---|
| `agent/cursor/social-b2-contracts` | `92cf2abd` | 非 trunk 后代，基线很旧 | 不直接合并；从当前 trunk 重放/重审 |
| `agent/cursor/social-b3-importer` | `e85fc311` | B2 的后续，但仍非 trunk 后代 | 先处理 B2 依赖，再从当前 trunk 重放 |
| `agent/cursor/social-b4-entity-resolver` | `aed3b972` | 与 B3 共享 `mediacrawler_importer.py`，且基线不同 | 不与 B3 并行合并；串行重放并复审 |

B2/B3/B4 的文件交集已确认：

- `tradingagents/dataflows/social/__init__.py`
- B2/B3 还共享 contracts/archive schema/tests；B3/B4 共享 `mediacrawler_importer.py`。
- 它们不触及 H1b 的两个文件：`research_manager.py`、`test_expectation_revision_contract.py`。

## H1b 当前状态

- `4b540b0` 已由“代码审核员”同 SHA PASS。
- 定向测试：**112 passed**；compileall、diff-check、历史 `excluded_evidence` 类型/幂等复现均通过。
- 以当前 trunk `8e49a80` 为基线的聚合回归：候选与基线均为 **17 failed / 4959 passed / 1 skipped / 5 deselected**，失败集合相同，**无新增失败**。
- 17 项是当前 trunk 既有失败，不能写成 H1b 引入；但全量不是 0 failed，因此 H1b 仍保持“已复审、待主控合入”，不能写成全量绿。
- H1b 与已审 social 分支无文件交集，允许独立保留；合入仍需单独授权。

## 外部边界

- 未使用 Cookie。
- 未启动真实社交采集、未启用 active。
- Social Gate0–3 仍需受控账号/沙箱/授权；当前完成的是代码与 Gate 证据，不是线上真实采集证明。
- H1b `credit_weighting_enabled` 继续关闭；不因样本攒取或 social 进度自动解锁。

## 对账纪律

1. 只认 `git ls-remote origin codex/dav-4-p2a-trunk` 的 trunk SHA。
2. 远端分支不是 trunk 后代时，标记为“候选/待重放”，不称为已合入。
3. social 同树交集严格串行：B2 → B3 → B4；只读审查、专项测试可并行。
4. H1b 与 social 分开验收；H1b 只改两个白名单文件，social 不得借合入顺手改 H1b 文件。
5. 每次 trunk 前移后重新核对候选父链、文件交集和测试失败集合；不沿用过期 SHA 的复审结论。
