Cursor 独立复审 DAV-483 / P0-5b。

候选 SHA（完整 40 位）：`3466e05a6483861cf071d4548b7bee990ac3c774`
父提交：`5e04125c8668adc99abe791a7686299f648de223`（线性，无 merge）
分支：`agent/dev2/p0-5b-confirmation-gate`

隔离 worktree `/tmp/ta-p05b-3466e05` + 宿主 `.venv310`：

```
env -u PYTHONPATH -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY \
  .venv310/bin/python -m pytest \
  tests/test_decision_status.py \
  tests/test_confirmation_gate.py \
  tests/test_research_manager_run_integrity.py \
  tests/test_prompt_depersonification.py \
  -q --tb=short
```

结果：**35 passed in 0.37s**。不采信口头；本次为 Cursor 独立复跑。

相对主干 8 文件、+732/−22。白名单内。未改社交、资金流 selection、cluster、受保护脏文件。

契约核对（含独立探针）：
- 未决焦点 claim → `UNRESOLVED` + `WAIT`；`is_non_executable_status=True`；Trader 不调 LLM
- 部分 verified → `PARTIAL` + `WAIT`
- 全部 verified → `CONFIRMED`，可 BUY
- 嵌套 ABSTAIN / consistency 硬闸优先，不被降成 CONFIRMED
- `winner=tie` + 未确认 → WAIT（不是 Neutral+HOLD）
- 歌尔钉子集成测覆盖 manager→trader 短路
- P0-5a 去人格化测仍绿

残留（不挡合入）：未确认路径用 `analysis_status=VALID` + `WAIT`（不是 ABSTAIN），与本卡「确认态闸」一致；任意非核心 contradicted 也会抬成 UNRESOLVED（偏严，可后续收紧）。

**准予合入** SHA `3466e05a6483861cf071d4548b7bee990ac3c774`。请创建线性 FF 卡，只快进该 SHA 到 `codex/dav-4-p2a-trunk`。

**不准予部署。**

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
