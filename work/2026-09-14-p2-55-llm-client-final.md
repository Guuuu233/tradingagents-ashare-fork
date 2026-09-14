# P2-55 LLM client 策略层最终合入证据

> 日期：2026-09-14（Australia/Perth）

## 候选与复审

- 目标主线：`origin/codex/dav-4-p2a-trunk`
- 合入候选：`54bfb621250711571ba5a75b6dc64e0db6dcf645`
- 直接父：`223f553c234b6244edd7fa760dc2dc710fd5dc2c`
- 候选基线：`133668c0a9ac39fbb806e1a6e3322824ff60b898`
- 候选分支：`origin/agent/1/78d1aa149255`
- 复审卡：DAV-921（`[Review R3] DAV-918 候选 SHA 54bfb621 只读代码复审`）
- 复审结论：**代码审核员对同一完整 SHA PASS**；复审为只读，未改代码、未合入、未部署、未写生产库、未重启服务。
- 合入动作：在干净 detached worktree 中由 `133668c` 执行 `git merge --ff-only 54bfb621...`，随后推送目标主线；远端回读为 `54bfb621250711571ba5a75b6dc64e0db6dcf645`。

## 变更边界

相对候选基线，实际变更严格为 8 个白名单文件：

```text
A tests/test_llm_model_validation_contract.py
M tradingagents/llm_clients/TODO.md
M tradingagents/llm_clients/__init__.py
M tradingagents/llm_clients/anthropic_client.py
M tradingagents/llm_clients/base_client.py
M tradingagents/llm_clients/google_client.py
M tradingagents/llm_clients/openai_client.py
M tradingagents/llm_clients/validators.py
```

本阶段落地：

1. advisory / custom endpoint / permissive provider / discovery 的模型策略状态；
2. 角色级 provider 与 `base_url` 继承隔离；
3. `classify_llm_failure` 的类型化失败归类和凭据脱敏；
4. Google 标准 endpoint 识别、统一导出和旧 `validate_model()` 未知 provider 空模型兼容；
5. 离线构造、fake transport、代理环境隔离与回归契约。

静态 `VALID_MODELS` 没有接成启动硬门禁；本阶段也没有把策略接入 `get_llm()`、启动流程或真实 warmup。运行链接线若要施工，必须另立窄卡，不能把静态 catalog 当作实时 provider 能力证明。

## 独立验证

固定 Python 3.10.20 环境：`/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`。

- 相关测试：**193 passed in 4.74s**；
- 契约测试：**62 passed in 0.40s**（代理变量注入后）；
- 独立安全/兼容性探针：`compatibility_and_redaction_probe_passed`；覆盖短 Bearer、含 `+` `/` `=` 的 Bearer、JWT Bearer、URL 凭据/query 敏感参数和未知 provider 空模型兼容；
- 候选工作区：clean；`git diff --check`：通过。

## 运行态边界

- 合入后未部署；线上 `/healthz` 仍为代码 SHA `79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`，服务未重启。
- 未调用真实模型、未发起真实 LLM 网络请求、未写生产数据库、未操作真实凭据。
- 因此本证据只证明策略层代码已审查、测试通过并合入，不证明生产运行链已经执行模型校验或 warmup。

## 后续施工边界

若继续推进，下一张卡只能围绕运行时接线的明确契约展开：告警/动态发现/warmup 的触发点、fail-closed 范围、未知模型和自定义 endpoint 的用户体验，以及相应的真实运行证据。不得重复派 DAV-916、DAV-918 或 DAV-921，也不得以本次离线测试替代真实模型或生产业务证据。
