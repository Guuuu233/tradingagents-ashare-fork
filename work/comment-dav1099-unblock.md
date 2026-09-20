## 卡 3 解锁：开工条件成立

前置已全部满足：

1. **卡 2（DAV-1097）采样通过**：25 轮 × 5 接口 125 次请求，成功率 99.2%，网关可用性坐实（报告 `work/2026-09-19-tushare-gateway-sampling-report.md`）
2. **卡 1（DAV-1098）已合入主干**：新主线 tip `41772fa73c2f885252aa00c807e9db3c93563c9e`（`codex/dav-4-p2a-trunk`），`tushare_provider.py` 基础框架已在主干，无写集冲突

### 开工基线（必须以新主干为基，不得基于旧 SHA）

- 基线 SHA：`41772fa73c2f885252aa00c807e9db3c93563c9e`
- 开工前自查：`git ls-remote origin codex/dav-4-p2a-trunk` 与本地一致

### 强制红线（卡 2 采样护栏，全盘注入，缺一不可）

- **强制重试**：三表接入严禁裸调，必须内置至少 1 次（退避 1~2s）重试
- **弹性超时**：单次超时放宽至 15s 或拆分 `(connect=3s, read=12s)`（现有 `_TUSHARE_TIMEOUT = 10` 对公网毛刺过窄，实测有 11.8s 长尾）
- **严格 fail-closed 降级**：两轮重试均失败时优雅降级至备用源或返回【数据缺失】，严禁未捕获异常中断主流程
- **重复报告期**：完全相同行可折叠；同报告期不同值必须 fail-closed 为【数据缺失】
- **PIT 边界**：严格按 `ann_date` 控制可见性，防前视偏差
- **RED 用例**：必须构造同报告期不同值输入，断言 fail-closed

### 原卡边界不变

范围：`income` / `balancesheet` / `cashflow` 接入 `fundamental_data` 链。不得合入主干、不得部署、不得重启服务；不得改用户配置；token 不打印/不落盘/不提交；只读生产库用 `immutable=1`。

完成后主动精确 mention `项目调度助手` 推进同 SHA 只读复审（D-014，`代码审核员`，不派「独立代码审核员」）。
