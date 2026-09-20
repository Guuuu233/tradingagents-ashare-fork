# TradingAgents-AShare 全仓体检与代码卫生规划（v1.0 — 2026-08-05）

> 授权来源：David 授予 Hermes 全部权限，代表其指挥 multica「专业团队」。
> 本规划是施工蓝图：系统性检查与测试项目所有文件，优化、清理冗余代码。

---

## 0. 背景与目标

上一轮 M1~M5（DAV-67~71）已竣工并合入主干 `codex/dav-4-p2a-trunk@3c339c5`（全量回归 777 passed / 13 skipped / 2 failed，2 failed 为预置日期炸弹与本次无关）。

David 指令：**系统性检查和测试这个项目的所有文件，优化、清理冗余代码**。

本轮目标（对齐项目竣工三线：诚实校准闭环 / 数据诚实 / 稳定可用）：

1. **全仓文件过一遍**：每个源码/配置/文档文件检查死代码、冗余、重复逻辑、未使用导入、注释掉的代码、遗留 TODO/FIXME/HACK、_v2/_old/_new 并行实现、无用依赖。
2. **测试体系体检**：全量回归基线、skip 原因审查、日期炸弹修复、覆盖缺口分析、测试质量审查。
3. **优化与清理落地**：删死代码（有据可查）、合并重复逻辑、清理未用导入、文档与代码同步。一个改动一个 commit，TDD 守护。
4. **独立复审 + 全量回归**：全部改动经独立审查，最终全量回归 0 新增失败，出具竣工报告。

---

## 1. 现状快照（事实基线）

- 主干：`codex/dav-4-p2a-trunk` HEAD `3c339c5`（M1+M2+M3+M4+M5 全部合入）。
- 上一轮遗留待办池（不阻塞交付，本轮顺带处理）：
  1. **2 个日期炸弹测试**：`tests/test_vendor_chain_semantics.py` 两例硬编码 `2026-08-04`，上海时区过零点后失败 → 改日期相对（动态取今日）。
  2. 观察项：`get_global_news` 参数未生效、VendorFail 同 vendor 重试、Redis 孤儿 job 清理、compose `stop_grace_period`、M2 私有函数耦合/静态标签悬空/密度阈值裕量。
- 运行时：宿主机 `.venv310` + `start.sh`（uvicorn :8000）。
- 测试环境铁律：`env -u PYTHONPATH .venv310/bin/python -m pytest`。

---

## 2. 阶段划分（严格串行，H1 → H2 → H3 → H4）

> 串行原因：上轮经验——squad 并行抢活会导致工作树冲突（skill 已记录）。每阶段仅放行一个，完成并验收后再放行下一阶段。

### H1: 全仓系统性审计（只读，不修改代码）
- [ ] 产出全仓文件清单（源码/测试/配置/文档/脚本分类）
- [ ] 逐文件检查并登记：死代码（未被调用的函数/类/变量/分支）、冗余代码、重复逻辑（可合并处）、未使用导入、注释掉的代码块、遗留 TODO/FIXME/HACK/XXX、_v2/_old/_new/_fixed 并行实现、空文件/空函数、无用依赖（requirements.txt / pyproject）
- [ ] 文档体检：README/CHANGELOG/KNOWN_ISSUES 与代码实际是否一致、过期文档
- [ ] 产出《全仓审计报告》（work/code-audit-report.md）：按严重度分级（P0 必清/P1 应清/P2 建议），每条附文件:行号与理由
- **验收**：报告覆盖全部文件；每条问题可定位、可核查；不修改任何代码

### H2: 测试体系体检
- [ ] 全量回归基线：`env -u PYTHONPATH .venv310/bin/python -m pytest tests/` 记录通过/跳过/失败
- [ ] 修复 2 个日期炸弹（`test_vendor_chain_semantics.py` 硬编码日期 → 动态取今日），TDD：先写失败复现，再修复
- [ ] 审查所有 skip 用例原因是否合理（不该 skip 的恢复）
- [ ] 覆盖缺口分析：核心路径（数据获取、裁决链、任务持久化、校准统计）哪些没有测试
- [ ] 测试质量抽查：断言是否有效、是否测实现细节而非行为
- **验收**：日期炸弹修复有 commit；skip 清单有结论；覆盖缺口报告纳入审计报告

### H3: 优化与清理执行（按 H1/H2 报告落地）
- [ ] 删除死代码（每条删除可回溯到审计报告条目）
- [ ] 合并重复逻辑、清理未用导入、移除注释掉的代码块
- [ ] 清理遗留并行实现（_v2/_old 等，合并到原路径后删除）
- [ ] 依赖体检：移除未使用依赖（有据可查）
- [ ] 文档同步：README/CHANGELOG/KNOWN_ISSUES 与实际一致
- [ ] 纪律：一个 commit 一个关注点；每个清理后跑相关测试；不自行提交主干
- **验收**：每条清理对应审计报告条目；相关测试绿；全量回归 0 新增失败

### H4: 独立复审 + 全量回归 + 竣工
- [ ] 独立代码审核员复审全部改动（diff 与审计报告条目对应）
- [ ] 代码运维测试员全量回归跑绿 + 测试矩阵
- [ ] 出具竣工报告（清理统计：删了多少行/多少文件、修复了什么）
- [ ] 合入主干前提交 Hermes 确认（集成方案 + diff）
- **验收**：复审通过；全量回归 0 新增失败；竣工报告完整

### H5: 接入同花顺金融数据 API（fuyao.aicubes.cn）★ 功能增强（依赖 H1~H4 完成 + API Key）

> 来源：David 推荐的同花顺官方免费金融数据 API（2026-08-05 评估通过）。
> 文档权威定义：https://fuyao.aicubes.cn/llms-full.txt（REST + MCP 全量）。

**定位**：不是替代现有源，而是补强——财务数据主源 + 涨跌停/龙虎榜官方备用源。

- [ ] 配置：`FUYAO_API_KEY` 环境变量（.env.example + start.sh）；SSRF 域名 allowlist 加入 `fuyao.aicubes.cn`
- [ ] 新增 provider `tradingagents/dataflows/providers/cn_fuyao_provider.py`，注册进 registry：
  - 行情快照 `GET /api/a-share/prices/snapshot`（thscodes 批量）
  - 历史K线 `GET /api/a-share/prices/historical`（1d，前/后复权，单标的）
  - 财务报表 `GET /api/a-share/financials`（三大表多期序列）+ 财务指标 `/financials/indicators`
  - 涨跌停 `GET /api/a-share/special-data/limit-up-pool`（+ ladder 连板天梯）
  - 龙虎榜 `GET /api/a-share/special-data/dragon-tiger-list`（all/org/hot_money）
  - 交易日历 `GET /api/a-share/calendar`（近一年，作对照/备用）
- [ ] 统一错误码映射：`0`=成功；`2001/2003`=Key 无效/无权限（显式报告）；`3001/3002`=标的不存在/数据未就绪（VendorEmpty 语义）；`4001`=频率超限（退避重试或切换备用源）；`5001~5003`=服务端错误（VendorFail 语义）
- [ ] route_to_vendor 接线：financials→fuyao 主源（现有弱源降级备用）；limit_up/dragon_tiger→fuyao 备用源（东财主源）；prices→第三备用源
- [ ] 测试：provider mock 测试（信封解析/错误码映射/字段映射）+ route 接线测试 + 有 Key 后真实冒烟
- [ ] 文档：README 数据源章节 + KNOWN_ISSUES 记录
- **依赖**：① H1~H4 全部完成验收；② API Key 已由 David 提供（2026-08-05 实测全部接口通过，存于宿主机 .env）
- **验收**：真实调用冒烟通过；分析师报告能看到财务指标数据；全量回归 0 新增失败

---

## 3. 分工建议

| 角色 | 负责 |
|---|---|
| 项目主管 | 阶段验收、里程碑流转、上报 Hermes |
| 资深开发1/2 | H2 日期炸弹修复、H3 清理执行 |
| 代码复核员 | H1 全仓审计主笔（逐文件过） |
| 高级开发·支援 | 审计辅助、依赖体检 |
| 代码审核员/审核员2 | H3 每批改动质量闸门 |
| 独立代码审核员 | H4 独立复审 |
| 代码运维测试员 | 回归基线、全量回归门卡、测试矩阵 |
| 项目规划与写作助手 | 审计报告整理、文档同步 |
| 主管秘书/调度助手 | 进度跟踪、issue 流转、协调 |

---

## 4. 施工纪律（延续 AGENTS.md + 上轮）

1. **读完再改**：改任何文件前先读全文件 + grep 调用点。
2. **改原路径**：禁止新造 _v2/_new/_fixed；删死代码。
3. **一次一提交**：一个 commit 一个关注点；不自行提交主干（Hermes 确认后）。
4. **TDD**：修复先写复现测试（RED→GREEN）。
5. **环境铁律**：所有 Python 命令必须 `env -u PYTHONPATH`；测试用 `.venv310/bin/python -m pytest`。
6. **不造假**：数据失败显式报告，绝不填 0/空串。
7. **串行纪律**：H1 完成后才能动 H2；H2 完成才能动 H3；每阶段完成在 issue 评论 @项目调度助手，由 Hermes 验收后放行下一阶段。
8. **完成汇报自动触发调度**：每完成一小段，在 issue 评论 @项目调度助手 触发下一轮。

---

## 5. 下一步（第一件事）

**H1 全仓系统性审计**：只读盘点全部文件，产出审计报告，为 H2/H3 提供施工依据。H1 完成验收前，禁止任何代码修改。
