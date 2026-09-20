# v2 P1-FE：前端辩论查看器适配（总卡）

## 精确基线

- 目标主干：`target/codex/dav-4-p2a-trunk` @ `0554216305b3c860cbe893681335b6b1a29e17ef`
- P1-B 已独立核验并部署；服务 `/healthz` 与主干 SHA 一致。
- 施工规格：`/Users/davidliu/Downloads/TradingAgents-AShare-v2-完整详细施工实施规格-2026-08-24.md` 第 8 节。
- 本卡与 P1-S 可并行，**禁止**改对抗核心文件与 `api/main.py`。

## 目标

用户能看懂 v2 结构（协议徽标、Opening、Challenge、Tiebreak、分歧地图、退化标志），旧 v1 六条消息抽屉不回归。

## 红线

禁止改用户 3/1、模型/绑定/Key；禁止 FF 主干、禁止重启服务、禁止改 `tradingagents/`。候选推到独立远端 branch，等独立精确 SHA 复审后再合入。

完成后按统一交付格式评论，并 mention 独立代码审核员，不要 mention 项目调度助手。
