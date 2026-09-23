# 研究文档归档

2026-09-23 经 David 同意，总控把仓库外的研究文档复制入库。本仓库是公开仓库，复制前已做凭据、邮箱、IP 与账户 ID 的模式扫描，无命中。原文件仍保留在本机原位置。各文档的「地位」以根目录 `ROADMAP.md` 第 5 节为准。

| 仓库内位置 | 本机原位置 | 日期 | 地位 |
|---|---|---|---|
| `f1-frozen-experience/outputs/TradingAgents-测量与可行性试点协议-v1.0.1-20260923.md` | `~/Documents/Codex/2026-09-22/ni/outputs/` | 09-23 | 现行（方法定稿，未排期） |
| `f1-frozen-experience/outputs/TradingAgents-组织经验实验-文献与统计优化评议-20260923.md` | 同上 | 09-23 | 协议依据 |
| `f1-frozen-experience/outputs/TradingAgents-文献证据台账-20260923.md` | 同上 | 09-23 | 协议依据 |
| `f1-frozen-experience/outputs/TradingAgents-GPT与Opus评价复核补充-20260923.md` | 同上 | 09-23 | 协议依据 |
| `f1-frozen-experience/claude_review/`（验算脚本与结果） | `~/Documents/Codex/2026-09-22/ni/claude_review/` | 09-23 | 协议 §11 的数值出处 |
| `f1-frozen-experience/math-bundle/`（数学复算包，含重跑输出） | `~/Documents/Codex/2026-09-22/ni/work/review_math_bundle/`（同 `outputs/TradingAgents-数学复算包-20260923.zip`） | 09-23 | 协议依据 |
| `f1-frozen-experience/checks/`（统计检查脚本与结果） | `~/Documents/Codex/2026-09-22/ni/work/` | 09-23 | 协议依据 |
| `2026-09-21-learning-review/TradingAgents-首个组织经验实验协议草案-20260921.md` | `~/Downloads/` | 09-21 | 已被 09-23 协议取代 |
| `2026-09-21-learning-review/TradingAgents-Fable-GPT方案评议-20260921.md` | `~/Downloads/` | 09-21 | 已被 09-23 协议吸收 |
| `2026-09-18-next-stage/TradingAgents-下一阶段提升建议-20260918.md` | `~/Downloads/` | 09-18 | 历史（研究建议，未批准） |

## 复现

协议 §11 以 `../claude_review/` 引用验算脚本。仓库内 `outputs/` 与 `claude_review/` 放在同一层级，相对路径保持有效。原注的运行环境是 Python 3.14 + NumPy。

## 未复制的内容（仍在本机原位置）

- 文献原文摘录：`ni/work/literature/*.txt`、`~/Downloads/tradingagents-learning-review-20260921/` 与 `tradingagents-next-stage-research/` 下的 txt。这些是第三方版权文本，不入公开仓库。文献出处见各文档的 Sources 节。
- 本机证据 JSON：`~/Downloads/tradingagents-next-stage-research/*.json`，内容为生产库只读审计明细，体积较大。
- `ni/work/d2_brier_score.html`：scikit-learn 官方文档页面快照。请直接查阅官方文档。
