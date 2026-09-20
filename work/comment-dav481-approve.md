Cursor 独立复审 DAV-481 / P0-5a。

候选 SHA（完整 40 位）：`5e04125c8668adc99abe791a7686299f648de223`
父提交：`18e73bdde4dcc8493f3e81290cc21c762d3b9aaf`（线性，无 merge）
分支：`agent/dev2/p0-5a-depersonify-prompts`

隔离 worktree `/tmp/ta-p05a-5e04125` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_prompt_depersonification.py \
  tests/test_analyst_prompts_deep_reasoning.py \
  tests/test_adjudication_risk_prompts_deep_reasoning.py \
  -q --tb=short
```

结果：**54 passed in 31.13s**。不采信口头；本次为 Cursor 独立复跑。

相对主干 4 文件、+221/−96。白名单内。未改 confirmation gate、资金流、cluster、社交、受保护脏文件。

契约核对：
- `假摔洗盘` / `主力成本区间` 已从 smart_money 结论模板移除；`ownership_inference=false`、VWMA=成交量加权价格、`event_candidate` 在
- volume_price 已去「跟随局内人」铁律；滞涨为 candidate；威科夫标为教学/待验证；无 volume fail-closed 仍在
- research_manager 已去掉「主力真实意图建仓/洗盘/派发」；DAV-336 七分析师与五步裁决仍绿
- 旧测已翻转，不再要求 prompt 含「洗盘」

残留（不挡合入）：`MANAGER_VERDICT` 示例 dispute_map 仍用「机构吸筹 / 主力派发」作多空解读对照，且写明不得单独支撑方向。后续可再洗示例文案。

**准予合入** SHA `5e04125c8668adc99abe791a7686299f648de223`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。

**不准予部署。**

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
