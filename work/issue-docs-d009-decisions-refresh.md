# Docs：刷新 DECISIONS.md D-009 过期「下一刀」表述

## 背景

`DECISIONS.md` D-009「影响」段仍写下一刀 P2-T12、tip `68ae241`。主干已过 T12–T15/Gate4 与 Track A5–A12，现 tip `98fe5d1`。

## 只做这件事

1. 更新 D-009 **影响**段落：反映 P0/P1/P2-Gate4 与 A5–A12 已在 tip `98fe5d199e8874ae829d2b492882d82339c836f0`；未部署；加权仍关；合入仍走 D-010。
2. **不改** D-009 原则条文本身；不改其它 Dn，除非发现明显过期且同属「影响/下一刀」一句。
3. 单 commit；分支 `agent/planner/d009-decisions-refresh`。

## 明确不做

- 产品代码；部署；扩写长文

## 验收

- push → `in_review`；可走简化审核（仍建议独立审核员扫一眼）；Cursor 准予合入后 FF
