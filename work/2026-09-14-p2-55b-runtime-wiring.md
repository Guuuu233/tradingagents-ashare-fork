# P2-55b（DAV-922）运行时接线合入证据

## 结论

DAV-922 的最终候选已通过两轮只读复审，并以 `--ff-only` 线性合入
`origin/codex/dav-4-p2a-trunk`。当前目标主线为完整 SHA
`c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`；线上服务仍运行
`79757a6a2dd98f9487bb1fed6466ba71e7e6a31a`，本卡没有部署或重启服务。

这次合入的是离线可验证的运行时接线，不是生产行为证明：没有调用真实模型或真实外部网络，
没有写生产数据库，也没有改变生产配置、凭据、Cookie、社交 active 或信用加权状态。

## 候选、基线与变更边界

- 最终候选：`c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`
- 最终候选直接父：`807464e5784240554efac0f5c3d1110d00a450a1`
- R2 候选远端分支：`origin/agent/1/fc77468944fc`
- 合入前目标主线基线：`51acae375ec104f71e6455559c24ae8fee590063`
- 合入后远端回读：`origin/codex/dav-4-p2a-trunk=c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699`
- 合入 worktree：`/private/tmp/ta-merge-dav922-20260914`
- 合入后 worktree clean；`git diff --check` 通过。

相对基线共 7 个白名单文件，统计为 `1049 insertions(+), 28 deletions(-)`：

1. `api/main.py`
2. `api/services/role_routing_service.py`
3. `tradingagents/graph/trading_graph.py`
4. `tradingagents/llm_clients/validators.py`
5. `tradingagents/llm_clients/__init__.py`
6. `tests/test_llm_model_validation_contract.py`
7. `tests/test_llm_runtime_wiring_contract.py`

没有超出 DAV-922 卡面白名单。

## 实际行为核对

- `_probe_runtime_config`、`_invoke_runtime_warmup` 接入统一的失败分类和日志/响应脱敏；
  401、404、超时、代理路由和未知错误仍保持可区分，原始 API key、Bearer、Cookie、Basic
  Auth 和敏感 URL 参数不回显。
- 角色级 `provider`/`base_url` 使用统一解析：同 provider 可以继承全局地址，异构 provider
  不会隐式继承全局专用地址；角色显式地址即使与全局地址相同也保留，纯空白地址按未配置处理。
- 静态 `VALID_MODELS` 仍是 advisory catalog，没有接成启动或 `get_llm()` 硬门禁；动态
  `/v1/models/fetch` 与运行时策略的边界没有被改成静态列表证明。
- R1 候选的“显式角色地址与全局相同被误当继承”问题已在 R2 修复，并增加了同地址、纯空白、
  异构隐式隔离三类断言。

## 复审与测试

- DAV-923：对 R1 候选 `807464e5784240554efac0f5c3d1110d00a450a1` 的只读复审；该候选后来
  因上述边界问题返修，未作为最终候选合入。
- DAV-924：对最终完整 SHA `c1ce3ab31ac22de1c28931f94fa2e98b0fa2e699` 的只读复审，
  审查人是 `代码审核员`，结论为 `PASS`。审查卡已标记 done。
- 固定 Python 3.10 环境的关联集合：

  `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -m pytest tests/test_llm_runtime_wiring_contract.py tests/test_llm_model_validation_contract.py tests/test_role_llms_user_id.py tests/test_api_smoke.py tests/test_llm_proxy_guard.py`

  在最终候选 worktree 和合入后 worktree 均为 `159 passed, 79 warnings`；合入后耗时
  `31.36s`，失败数和跳过数均为 0。另在代理变量污染环境下复测离线契约，仍通过。
- 本证据只覆盖离线契约和代码路径；没有据此宣称真实模型 warmup、生产 graph 可达、生产报告
  持久化回读或真实业务数据行为已经证明。若要取得这些证据，仍须另走受控业务路径和相应的
  数据写入授权。

## 发布边界

- 本次已完成：候选复审、测试、线性合入、远端 SHA 回读。
- 本次未完成：部署、服务重启、真实模型调用、真实外部网络请求、生产数据库写入、真实社交
  采集或凭据操作。
- 下一次发布必须单独执行备份、预启动、`/healthz` 精确 SHA 回读、只读烟测和数据库回读；
  不能把本卡的离线测试结果当作线上运行证据。
