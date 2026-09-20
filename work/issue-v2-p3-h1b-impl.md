# P3-H1b：信用加权实施（分层隔离 + 门槛校验，默认关）

## 已批准门槛（用户 2026-08-26）

来源：`work/p3-h1b-activation-gates-draft.md` + DAV-432 评估师 Conditional Pass（路径 B）。

| 维 | 批准值 |
|---|---|
| N | ≥60 场；去重标的 ≥20；行业 ≥5；单标的 ≤15% |
| 分侧 | bull/bear 各 ≥25；verified claims 各 ≥100 |
| 时间 | ≥45 自然日且 ≥30 交易日；市场状态覆盖按草案量化定义 |
| T+5 完整率 | ≥95%（在途保持 None；停牌剔分母） |
| 平衡 | 比例 ∈[40%,60%]；\|Nbull−Nbear\|≤10（60 场基线） |
| 偏置冻结 | Δverified ≤18%；challenge 采纳差 ≤25%；克隆率 ≤5%；consistency 触发率 ≤5% |
| 幅度 | 系数 ∈[0.85,1.15] |
| Flag | `credit_weighting_enabled` 默认 **false**；秒关回 shadow，影子数据保留 |

**分层隔离（必做）**：系统级门槛不过 → 全系统不加权。单模型偏置超标 → 仅该模型权重=1.0；异常模型占比 >50% → 全局 Shadow。

## 范围

基线 tip：`11309037de9334820603eec6dd801f291172f6ed`。隔离分支。TDD。禁止改 3/1、模型绑定、Key；禁止自行 FF/部署。

### 必须交付

1. **只读门槛校验**：`scripts/verify_h1b_gates.py`（或 `work/` 等价路径，与仓库惯例一致）  
   - 扫 `reports` 中 v2 + `shadow_credit_metrics`  
   - 输出 7 维 PASS/FAIL 矩阵 + 汇总 JSON  
   - 当前样本预期：**系统级未达标 → 不得建议开 flag**
2. **Feature flag 接线**：读取路径与现有 `feature_flags.credit_weighting_enabled` / `DEFAULT_FEATURE_FLAGS` 对齐；默认 false；关 flag ≡ shadow-only。
3. **分层隔离状态机**：实现系统级 / 模型级判定；元数据写入 `bias_freeze_reason` 等可审计字段。
4. **加权应用点**（仅 flag=true 且系统级 PASS）：只在多个 **verified** claims 之间做相对权重修正；`contradicted`/`unsupported` 永不因信用升格。
5. **测试**（`.venv310`）：  
   - 默认 flag false 时与现有 `tests/test_shadow_credit.py` 硬规则不回归  
   - 门槛不足 → 不加权  
   - 单模型偏置 → 仅该模型 clamp 1.0  
   - 关 flag → 立即平权且不丢影子字段

### 允许改动（优先原路径，禁止平行 `_v2`）

- `tradingagents/agents/utils/shadow_credit.py`（及同目录 tightly related）
- 总监裁决路径中注入权重的**最小必要**调用点（改原函数，勿旁路）
- `tests/test_shadow_credit.py` 扩展；新增 `tests/test_h1b_gates.py` / `tests/test_credit_weighting.py`
- 新增 `scripts/verify_h1b_gates.py`（若仓库脚本目录惯例不同，跟现有 scripts 布局）

### 禁止

- 改持久 3/1、role_bindings、providers、密钥  
- 未过门槛时默认开启加权  
- @项目调度助手  
- 自行 FF / 重启生产

## 验收

1. `.venv310` 相关 pytest 全绿；展示 RED→GREEN 证据  
2. `verify_h1b_gates.py` 在当前库跑出结构化报告  
3. 独立代码审核员只读复审精确 SHA  
4. FF / 部署另开卡（FF 卡禁止重启）

## 参考

- `work/p3-h1b-activation-gates-draft.md`  
- `work/2026-08-26-p2-complete-p3-entry.md`  
- 规格 §11.1–11.4；现有 H1a：`tests/test_shadow_credit.py`
