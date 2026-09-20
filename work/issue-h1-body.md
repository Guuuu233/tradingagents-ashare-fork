## 目标
全仓系统性审计（只读）：盘点项目所有文件，登记死代码/冗余/重复逻辑/未用导入/注释代码/遗留 TODO/并行实现/无用依赖，产出《全仓审计报告》。

## 背景
- 主干：`codex/dav-4-p2a-trunk` HEAD `3c339c5`（M1~M5 已竣工）
- 规划文档：`work/2026-08-05-code-audit-plan.md`（必读）
- 施工纪律：见规划第 4 节；本阶段**只读，不修改任何代码**

## 任务清单
1. 产出全仓文件清单（源码/测试/配置/文档/脚本分类）
2. 逐文件检查并登记：死代码（未被调用的函数/类/变量/分支）、冗余代码、重复逻辑（可合并处）、未使用导入、注释掉的代码块、遗留 TODO/FIXME/HACK/XXX、_v2/_old/_new/_fixed 并行实现、空文件/空函数、无用依赖（requirements.txt / pyproject）
3. 文档体检：README/CHANGELOG/KNOWN_ISSUES 与代码实际是否一致、过期文档
4. 产出《全仓审计报告》`work/code-audit-report.md`：按严重度分级（P0 必清/P1 应清/P2 建议），每条附文件:行号与理由

## 验收标准
- 报告覆盖全部文件；每条问题可定位、可核查
- **不修改任何代码**（git status 必须干净，仅新增审计报告文档）
- 完成评论 @项目调度助手，等待 Hermes 验收后放行 H2

## 环境铁律
- 所有 Python 命令必须 `env -u PYTHONPATH`；测试用 `.venv310/bin/python -m pytest`
- 本阶段不需要跑全量测试；如需运行静态检查工具（如 `python -m pyflakes` / `vulture`）可用，但结果须人工复核后再登记
