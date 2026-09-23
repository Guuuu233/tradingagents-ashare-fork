# 仓库外计划文档归档

2026-09-23 经 David 同意，总控把此前只存在于本机 Codex 目录的计划文档复制入库。原文件仍保留在本机原位置。各文档的「地位」以根目录 `ROADMAP.md` 第 5 节为准。

| 仓库内位置 | 本机原位置 | 地位 |
|---|---|---|
| `2026-09-06-integrated-construction-plan/2026-09-06_整合施工计划-v1.1.md` | `~/Documents/Codex/2026-08-27/referenced-chatgpt-conversation-this-is-an-3/.hermes/plans/` | 主体已交付（09-14 审计） |
| `2026-09-06-integrated-construction-plan/2026-09-06_整合施工计划-v1.md` | 同上 | 已被 v1.1 取代 |
| `2026-09-17-remaining-work-audit/plan-inventory.md` 等 3 份 | `~/Documents/Codex/2026-09-17/remaining-work-audit/` | 已取代。前三阶段已完成，其余由 `ROADMAP.md` 接续。文中「D-018 回归老流程」实为 D-032 |

## 未复制的内容

- 09-17 审计的 `issues.json`：看板全量快照，3.5 MB。
- 09-17 审计的其余 JSON 附件。

## 空白规范化

为通过 `git diff --check`，只改了 7 行空白，正文未改：
- `plan-inventory.md` 第 3–8 行：行尾双空格硬换行改为等价的反斜杠硬换行；
- `整合施工计划-v1.1.md`：一行纯空白行改为空行。
