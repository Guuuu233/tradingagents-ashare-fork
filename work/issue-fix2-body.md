## 目标
修复 David 实测反馈的两个用户侧 bug（2026-08-06 上线后立即发现，P1 优先）：
- **Bug A：中文简称识别失败**——"分析京东方"报"无法识别股票标的"，必须输全名/代码
- **Bug B：历史记录卡"正在分析"**——分析结束后前端仍锁定输入，要等很久才能发第二条

## 背景
- 主干：`codex/dav-4-p2a-trunk` HEAD `a403e78`（H1~H5 已竣工）
- 修复纪律：TDD（先写复现测试 RED→GREEN）、改原路径、一次一提交、不自行提交主干
- Hermes 已完成根因定位（如下），团队按定位修复 + 补测试，也可独立复核

## Bug A 根因（已定位）
1. **全角/半角不匹配**：`_load_cn_stock_map()` 的名称含全角字符（如京东方Ａ 是全角"Ａ"）。`_search_cn_stock_by_name`（api/main.py:509）未做归一化：LLM 提取"京东方A"（半角）→ 与"京东方Ａ"互不包含 → 匹配失败。实测：`_search_cn_stock_by_name('京东方')` 能命中 000725.SZ，但 `'京东方A'` 返回 None。
2. **原文兜底整句匹配**：LLM 挂掉/未返回名字时，`_ai_extract_symbol_and_date_streaming`（api/main.py:3719）拿整句（"分析京东方"）去 `_search_cn_stock_by_name`，整句不是任何名称的子串 → 失败。
3. LLM 返回非标准名（如"京东方科技"）同样失败。

**修复建议**：
- `_search_cn_stock_by_name` 内对 query 和名称做 NFKC 归一化（`unicodedata.normalize('NFKC', ...)`，全角→半角），精确/子串匹配都基于归一化后文本
- 原文兜底路径：剥离常见意图词（分析/帮我/看看/一下/怎么样/如何/今天/现在 等）后再匹配；或对原文做"名称是原文子串"的反向匹配（取 stock_map 中 name 出现在原文里的候选，长度优先）
- 补充测试：半角"京东方A"命中 000725.SZ；全角"京东方Ａ"命中；"分析京东方"整句兜底命中；"京东方科技"类扩展名（如能实现则实现，否则登记为已知限制）

## Bug B 根因（已定位）
前端 `ChatCopilotPanel.tsx` 的恢复轮询 `recoverInterruptedJob`：SSE 中断后每 3 秒调 `GET /v1/jobs/{id}`（**内存** job store，重启即丢失）查状态，轮询上限 `RECOVERY_POLL_MAX_ATTEMPTS = 2*60*60/3`（**2 小时**）。若任务中断（服务重启/线程挂起）且内存 job 丢失/状态停滞 running，前端 `isAnalyzing=true` 锁输入框，用户要等很久（直至 2h 上限或 1800s 软超时）。而**数据库 reports 表是持久化的**（completed/failed 已落库），恢复轮询没查它。

**修复建议**：
- 恢复轮询增加"DB 报告状态"回查：job 404 或 running 时，调报告查询接口（如 `/api/reports?symbol=...` 或新增按 job_id 查报告端点），报告 completed/failed 即解锁（`isAnalyzing=false`）
- 缩短恢复轮询上限：`RECOVERY_POLL_MAX_ATTEMPTS` 从 2 小时降至 ~5 分钟（100 次），到上限时给用户明确提示"任务状态异常，请刷新查看历史报告"
- 前端页面加载时若 store 中有 currentJobId，先查一次状态再决定是否恢复轮询（避免刷新后无谓等待）
- 后端补充（如需要）：`GET /v1/jobs/{job_id}` 404 时错误信息可携带"报告已落库，请查历史"提示
- 补充测试：jobLifecycle 单测（轮询上限、报告 completed 解锁）；后端若新增报告查询端点则补 API 测试

## 验收标准
- Bug A：`_search_cn_stock_by_name('京东方A')` 返回 000725.SZ（新增测试）；"分析京东方"整句兜底命中；相关单测全绿
- Bug B：恢复轮询不再无谓等待 2 小时（上限 5 分钟内 + 报告状态解锁）；相关单测全绿
- 全量回归 0 新增失败
- 完成评论 @项目调度助手，附修复 diff 摘要 + 测试结果，等 Hermes 验收

## 环境铁律
- 所有 Python 命令必须 `env -u PYTHONPATH`；测试用 `.venv310/bin/python -m pytest`
- 前端测试用 `cd frontend && npm test`（如适用）
- 一个 commit 一个关注点；不自行提交主干
