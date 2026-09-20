# DAV-148 DAV-119 最终组合树验证

## 固定输入

目标主干：`codex/dav-4-p2a-trunk @ f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`。

只读验证。不得修改仓库、配置、数据库、用户设置、providers、模型绑定或凭据；不得推送临时树；不得重启服务。

## 物化内容

在 detached 临时树中，从目标 trunk 物化并组合这些已精确复审的独立提交：

- `92dc1f408cab4824b692f3a2c87372a5faf2237f`（DAV-135）
- `35603c1e5be4d26857231eeb8ba3d684de05bc63`（DAV-136）
- `7812eb682a40edc2a8da4c89ae42825b0c1570a3`（DAV-139）
- `f5a2f83e0d122212ecc1bdf05cff698bb84d096c`（DAV-141）
- `d39bac29b23a676585590d4110748836ec982b00`（DAV-143）
- `00b62f51f09689d8f7daa6c15d0b081e3ea0efdb`（DAV-144，测试契约修复；若 SHA 不存在，以远端实际核验为准，不猜）
- DAV-145 的远端精确 SHA（先用 git ls-remote 核验，不能凭标题）

若存在同文件重叠，必须显式记录冲突及选择理由；不得静默覆盖。

## 验证门

使用宿主 `.venv310` Python 3.10.20；执行与 DAV-140 对齐的定向组合测试、全量回归、changed-module compileall、`git diff --check`。对每个失败分类为：组合新增、目标 trunk 已有、环境/网络阻塞、测试契约冲突。

真实 provider 固定探针：`002167.SZ`、`600396.SH`，requested as-of `2026-08-14`；记录实际 `source`、`algorithm_group`、`status`、`as_of`、字段、单位、窗口、`attempted_sources`、`fallback_errors`、`em_typed_gap`、`final_source`。Sina historical/legacy 只能作为降级证据，不能满足 EM/THS 新算法可比性。

只有以下条件全部满足才可建议 parent 收口：组合定向无新增失败、全量新增失败均解释、真实 provider metadata 可审计、且 EM/THS 同标的/日期/窗口/字段/单位可比证据存在。否则保持 DAV-119/DAV-140 blocked。
