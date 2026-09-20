# Phase E：600519 自定义提示词开关 A/B 验证报告

执行时间：2026-07-31 04:36–04:57 AWST  
标的：`600519.SH`（贵州茅台）  
目标用户：`429163f7-50b6-4982-8bdf-96ae99506843`  
正式提示词：`chars=523`，`hash=e8b3a71c826b`  
placement：`after_data`

## 1. 验证范围

按原始 Phase E 计划运行四份真实报告：

| label | trade_date | prompt switch |
|---|---|---:|
| `current_off` | `2026-07-29` | false |
| `current_on` | `2026-07-29` | true |
| `historical_off` | `2026-04-30` | false |
| `historical_on` | `2026-04-30` | true |

每次请求均：

- 使用目标账号 JWT，不使用 server-level `TA_API_KEY`。
- 运行全部 7 个分析师。
- 使用 `horizons=["short"]`。
- 订阅 SSE 并保存完整 `agent.debate` 消息。
- 不保存逐 token 事件。
- 不自动重跑失败任务。

四份任务均为 `completed`。

## 2. 四份报告结果

| label | job_id | final decision | direction | confidence | probability | data_gaps | prompt snapshot |
|---|---|---|---|---:|---:|---|---|
| `current_off` | `2873c7591d0947c898c1d264ff8f6135` | BUY | 偏多 | 75 | null | `[]` | disabled / 3 roles not injected |
| `current_on` | `d7deca255c734554bfadae4539ade111` | BUY | 偏多 | 85 | null | `[]` | enabled / 3 roles injected |
| `historical_off` | `573e92970a7b445ca2cc8e8c6dd496b7` | HOLD | 中性 | 40 | null | `["2025年全年营收负增长的具体驱动因素"]` | disabled / 3 roles not injected |
| `historical_on` | `a8cafe3d314841e3923654d94ba98c0d` | SELL | 看空 | 75 | null | `["2025年全年营收负增长的具体驱动因素在现有数据中未能明确体现"]` | enabled / 3 roles injected |

开启组快照均为：

```text
enabled=true
placement=after_data
bull_researcher: injected=true, resolved_length=523, hash=e8b3a71c826b
bear_researcher: injected=true, resolved_length=523, hash=e8b3a71c826b
research_manager: injected=true, resolved_length=523, hash=e8b3a71c826b
```

关闭组均为：

```text
enabled=false
placement=after_data
三角色 injected=false, resolved_length=0, hash=null
```

因此 A/B 身份、开关和快照分组均正确。

## 3. 置信度与 probability

### 观察结果

- 当日组：提示词开启后，最终 confidence 从 75 上升到 85。
- 历史组：提示词开启后，最终 confidence 从 40 上升到 75。
- 四份结构化 probability 均为 `null`。

### 裁定

正式提示词没有在本轮 A/B 中表现出“数据缺失时降低置信度”的效果。两个日期的开启组置信度都更高。

但是每个格子只运行一次，LLM 输出具有随机性，所以不能据此断言提示词必然造成置信度升高；只能确认本轮没有观察到预期的置信度约束效果。

四份 probability 均为 `null`，符合“主周期、分析基准价或定量依据不明确时不得猜测”的保守语义。

## 4. data_gaps

四份报告的新闻报告和主力资金报告都明确包含重要缺口：

- 新闻数据不可用。
- 主力资金净流向获取失败。
- 龙虎榜无数据。
- VWMA/成交量相关数据调用失败或缺失。

但结构化结果中：

- 两份当日报告的 `data_gaps=[]`。
- 两份历史报告只记录了“2025 年全年营收负增长驱动不明”。
- 没有一份结构化结果记录新闻和主力资金缺失。

这表明当前结构化 `data_gaps` 不是全链路数据缺口汇总。

已知代码路径中，`extract_structured_data()` 主要接收最终交易决策和基本面报告，无法直接看到新闻、主力资金等完整分析师报告。这是本轮缺口漏报的直接结构性原因。

## 5. 主力资金失败后的 Bull 资金流表述

四份主力资金报告均明确写出无法判断资金流入/流出。

当日开启组 Bull 第一轮却写道：

> 这表明市场资金持续流入，多头掌控局面。

它的依据是股价高于 EMA/VWMA，而不是可用的资金流数据。

因此，更一般的验收问题“主力资金数据失败后，Bull 是否仍无依据宣称资金流入”在开启组中命中失败。

原计划更窄的判据是“Bull 是否引用新闻报告中的资金流”。本次四份新闻报告都没有实际资金流数据，所以无法构造“引用新闻资金流”的直接命中样本；不能把不存在的新闻资金流误报为已引用。

## 6. `confidence` 同名字段语义冲突

正式 global 文案规定：

```text
confidence 使用 0–100 的整数
```

但 Bull/Bear 模板的既有机读块要求：

```json
{"new_claims": [{"confidence": 0.72}]}
```

即 `DEBATE_STATE.new_claims[].confidence` 实际是 0–1 的 claim-level confidence。

本次开启组模型仍按既有机读模板输出了 0.75–0.90，解析没有损坏；但全局文案与机器块对同名字段给出了不同单位，属于真实语义冲突。

正式文案需要明确区分：

- 报告级 `StructuredReport.confidence`：0–100 整数。
- `DEBATE_STATE/RISK_STATE.new_claims[].confidence`：沿用既有 0.00–1.00。

## 7. 开启组额外输出字段

`current_on` 的 Bull/Bear 每轮正文都额外输出了：

```text
上涨概率 (probability)：0.70
置信度 (confidence)：85
```

或：

```text
下跌概率 (probability)：0.65
置信度 (confidence)：80
```

两个问题：

1. 模型确实被诱导增加了正文级字段；此前“不会诱导改变既有输出格式”的判断并不成立。
2. Bear 把 `probability` 写成“下跌概率”，违反正式文案中 probability 始终表示“上涨概率”的定义。

机读 `DEBATE_STATE` 仍可解析，所以这是语义/格式偏移，不是 parser 崩溃。

`historical_on` 没有额外输出这些字段，说明该行为也具有模型随机性。

## 8. 决策链观察

研究经理与最终风控结论并不总一致：

- `current_off`：research_manager 为 Hold/偏空，最终风控为 BUY/偏多。
- `current_on`：research_manager 为 Buy/偏多，最终风控为 BUY/偏多。
- `historical_off`：research_manager 为 Hold/中性，最终 HOLD/中性。
- `historical_on`：research_manager 为 Sell/看空，最终 SELL/看空。

当前自定义提示词只注入 Bull、Bear 和 research_manager，不注入 trader 与风险辩论/风险经理。最终 confidence 又由下游最终决策的结构化提取产生，因此 Phase E 的最终 confidence 只能作为整条链路观察值，不能视为三个注入角色对字段语义的直接服从测试。

## 9. 结构性限制

必须继续保留既有声明：

`research_manager` 看不到 `fundamentals_report`、`market_report`、`news_report` 正文；这些正文只用于 memory 检索，不进入它的模板。

本轮还进一步确认：

- research_manager 能直接看到主力资金、量价、市场情绪报告。
- 它能正确说明这些辅助数据不可用。
- 但它无法独立核对 Bull/Bear 对 fundamentals/market/news 一手材料的引用是否准确。

## 10. Phase E 裁定

### 机械验收

通过：

- 4 份真实报告全部完成。
- 日期和开关组合正确。
- JWT 身份正确。
- 开启/关闭快照正确。
- 完整 Bull/Bear/research_manager 辩论消息已保存。
- finally 清理成功。

### 质量验收

未通过：

- 开启提示词没有在本轮降低缺数据场景的最终 confidence。
- 结构化 data_gaps 没有覆盖新闻和主力资金缺失。
- Bull 在主力资金数据失败时仍无依据声称市场资金持续流入。
- report-level confidence 与 claim-level confidence 存在 0–100 / 0–1 冲突。
- Bear 把 probability 写成下跌概率。
- 开启组有时会新增正文级 probability/confidence 字段。

因此 Phase E 应判定为：

```text
执行完成，但质量验收失败；需要单独修复阶段，不能宣称整个自定义提示词项目完成。
```

## 11. 建议的后续修复边界

后续不应直接在 Phase E 产物上临时打补丁。建议拆成至少两个关注点：

1. 提示词语义修复
   - 明确 report confidence 与 claim confidence 的不同单位。
   - probability 永远表示上涨概率，即使当前角色是 Bear。
   - 禁止 Bull/Bear 新增正文级结构化字段。
   - 禁止把价格/均线走势表述为已观测资金净流入。

2. 全链路 data_gaps 汇总
   - 让结构化提取能看到全部相关分析师报告，或在 Python 侧汇总显式失败标记。
   - 不得只根据 fundamentals_report 推断全系统数据完整性。

每个关注点应单独审查、测试和提交。

不建议自动重复同样的四格 A/B；每格单次运行不能做统计因果判断。若修复后需要验证，应先定义更精确的 deterministic 验收条件，再决定是否重跑真实分析。

## 12. 清理状态

驱动程序 finally 回读：

```text
switch=false
cleanup method=API
cleanup ok=true
```

正式 prompt 文本未修改，仍为：

```text
chars=523
hash=e8b3a71c826b
enabled=true（提示词记录本身）
```

注入总开关为 `false`。

