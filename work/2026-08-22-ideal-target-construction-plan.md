# TradingAgents-AShare 达理想目标施工方案（2026-08-22）

基线：主干/线上 `ab3cda62bc676562c9421f809c0f1fa95d62f34e`  
对照：《项目加强方案》四层传导 + §4.5/§5.4/§6；独立审计与京东方 `81f63cad` 真实验收。

持久配置 **辩论 3 / 风控 1** 为用户设置，本轮**禁止改** `user_llm_configs` / `role_bindings` / `.env`。代码默认风控已是 3，验收可用单次 `config_overrides`，事后必须回读仍为 3/1。

---

## 0. 已达标（不再派工）

| 目标 | 证据 |
|---|---|
| Prompt 强制联想 / 三层传导 | zh.py；京东方宏观传导×16、时滞×10 |
| 辩论 3 轮 | 代码默认 3；`81f63cad` inv count=6 |
| 两阶段拓扑 | `8866494` 起上线 |
| 27 行业图谱 | INDUSTRY_LINKAGE_MAP=27 |
| 本地 RAG | `dcc871` / 词表外置 |
| 历史案例落库+回填代码 | `historical_cases` 3 行；eval_date=2026-08-24 待周一实值 |
| 设置页 URL→providers 级联 | `ab3cda6`；15 角色解析 Tailscale |
| 外盘指数主路径 | 恒生/日经/KOSPI/DAX/富时/CAC 真值已进宏观并落库 |

---

## 1. 剩余缺口（距「理想预期」）

### P1-A 美股三大指数仍【数据缺失】（阻碍四层第一层）

- 京东方 `81f63cad` 宏观写明「标普/纳指/道指点位【数据缺失】」。
- 新浪回退用了 `gb_inx/gb_ixic/gb_dji`，本机可用的是 `int_dji,int_nasdaq,int_sp500`。
- 东财 ulist 有 SPX/DJIA/NDX，但 `as_of > trade_date`（美股日历/时区）会被整段丢掉。
- 5 日/20 日因走 session snapshot，契约允许【数据缺失】；美股 **1 日真值必须有**。

### P1-B 产业链注入 fail-open（§4.5 验证2）

- `format_industry_linkage_for_prompt(None)` 返回 `""`，宏观/基本面不插入「【产业链联想数据】」。
- `81f63cad` 宏观 0 次该标记。LLM 无法区分「没映射」与「采集失败」。
- 国际对标仍大量 yfinance 限流 → 必须显式【数据缺失】，禁止静默省略。

### P1-C §6 后处理关键词闸（方案原文未做）

- 仅靠 Prompt，无落库后检查。若宏观缺「传导/联动/时滞」或外盘失败被改写成「外围平稳」，当前无机器拦截。

### P2 不阻塞本轮并行开工

| 项 | 处理 |
|---|---|
| 案例 T+1 实值 | 代码已上线，8-24 收盘后自动回填；不开新实现卡 |
| 风控持久 1 轮 | **不改用户配置** |
| 碳酸锂等「待接入」 | **错误叙事**：Tushare 已付费且 `fut_daily LC.GFE` 有真值；见 DAV-305 |
| `except Exception` 吞细节 | 随 P1-A 日志补强，不单独大改 |

---

## 2. 理想验收（全部满足才算达预期）

1. `route_to_vendor('get_global_indices','2026-08-21')`：**标普500、纳斯达克（标明综合或100口径）、道琼斯** 均有 latest_close + 1d%；港/日/韩/欧保持现网真值。
2. 正确账户京东方新报告宏观必须同时出现：美股点位或【数据缺失】分项（不得整包没有美股）、恒生/日经/KOSPI、【产业链联想数据】段落（无数据也要该标题+缺失说明）。
3. `market_data_context.global_indices` status≠failed（partial 可接受，须列出缺失项）。
4. 宏观正文含「传导」且不得把失败改写成「外围平稳」；质量闸把违规记入 ledger（可一次重试，禁止编造）。
5. 持久 3/1、providers Tailscale、role_bindings 不变。

---

## 3. 并行泳道（文件不冲突）

| 卡 | Owner | 文件 | 依赖 |
|---|---|---|---|
| DAV-302 美股指数 1d 真值 | 资深开发1 | `cn_akshare_provider.py`、`tests/test_global_indices_fallback.py` | 无 |
| DAV-303 产业链 fail-closed 注入 | 资深开发2 | `industry_linkage.py`、宏观/基本面 analyst、对应测试 | 无 |
| DAV-305 Tushare 优先接碳酸锂等 | 资深开发2（接 302 SHA） | industry_linkage.py / industry_linkage_provider.py | 父提交用 `49a4003`（302 交付），勿与 301 抢 cn_akshare |

合入顺序：三卡各自独立终审 PASS → 组合树回归 → FF `ab3cda6` 后代 → 部署 → 京东方真实账户 smoke。

施工铁律：独立分支、父提交必须 `ab3cda62`、禁止 `_v2`、禁止改 `.env`/providers 数据/role_bindings/持久轮次、不得 @项目调度助手。
