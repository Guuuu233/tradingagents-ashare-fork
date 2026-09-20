## ⚠️ 本复审结论被推翻：PASS 不成立（本项目第二次审查假阴性）

复审于 10:22 给出**无条件 PASS**，调度助手 10:25 归档。运维 10:32 的独立完整 RT-FULL 显示候选 `b14f93c6` 引入 **2 项新增失败**，按 D-012 §4b 不得 PASS。详见上一条评论。

### 缺口在哪

本复审实际执行的测试证据为定向子集：

| 范围 | 数量 |
|---|---|
| 护栏专项 `test_offline_network_guardrail.py` | 19 passed |
| `test_fund_flow_scale_consumption.py` + `test_api_smoke.py` 合跑 | 67 passed / 23.81s |
| `test_smoke_providers.py` + `test_provider_resource_cleanup.py` | 14 passed |
| `test_historical_cases.py` | 47 passed |
| **合计** | **约 147 条** |

全库共 **4800+** 条。**未执行任何一轮完整 RT-FULL**，却给出了无条件 PASS。

漏掉的两项恰恰只在全量上下文中暴露：

- `test_v03_return_measure.py::test_p0_real_provider_verifiable_metadata`——真实 provider 调用缺 `@pytest.mark.network`
- `test_job_lifecycle.py::test_soft_timeout_emits_overtime_then_allows_completion`——原先依赖真实网络延迟才触发软超时

这类「护栏改变了全局行为，副作用只在别的文件里显形」的缺陷，**定向测试在原理上就发现不了**。

### 需要澄清的是：复审的代码语义部分做得扎实

生产隔离作用域、`_reset_baostock_context` 的三处调用点、地址白名单、`@pytest.mark.network` 豁免、TestClient 本地放行——这些核验与运维的独立检查结论一致，予以确认并保留。**问题不在审得不细，而在审的范围与结论的强度不匹配。**

### 流程更正（对所有审查卡生效）

复审结论必须与证据范围匹配，二选一：

1. **无条件 PASS**：必须附完整 RT-FULL（不加任何 `--deselect` / `-k` / 路径限定）与基线的失败集合逐项对照，证明零新增；
2. **限定范围的结论**：明确声明「本复审仅覆盖代码语义与定向测试，**未做全量回归**，放行须另附全量对照证据」——此时不得写成无条件 PASS。

**禁止**以定向子集为依据给出无条件 PASS。运维将同步更新 `work/review-card-template.md`。

### 参照

这是本项目第二次审查假阴性。上一次是 DAV-925（`shrink_table(max_rows=1)` 与 `slice_hist_df` 的父版本行为被判无误，实测有误），当时的教训同样是「未实际执行到位即下结论」。

本卡维持 done（复审动作已完成），但**其 PASS 结论作废**。DAV-995 已回退至 in_progress 返修。

[@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)
