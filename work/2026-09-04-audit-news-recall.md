# 只读审计：新闻 / 交易所公告 / 公司 IR·调研召回能力

**禁止改功能代码、禁止 commit、禁止 push、禁止部署。**

## 背景
现有 event coverage 只能证明已取得条目的时间资格，不能证明应查事件已经查到。

需要评估现状是否已有或完全缺失：
- `query_manifest` / `source_manifest` / `recall_gap`
- `canonical_event_id`（同一公告多篇转载算一个底层事件）
- 公告、IR、调研相对资讯转载的优先级
- `published_at`、`first_seen_at` 与 cutoff 资格

## 要回答
1. 新闻、公告、IR 各自的入口文件与数据源。
2. 召回是「搜到什么算什么」还是有应查清单。
3. 去重键是什么（URL？标题？无？）。
4. coverage 字段实际断言了什么。
5. R2 工业富联 news fixture 覆盖了哪一层（新闻切片 vs 公告/IR）。
6. 缺口清单；不要写实现。

对照 SHA：`c72dd7b6098297efbec80931dda8bf509c8d8709`（或当时最新主干，须写明）。
