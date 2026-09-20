# DAV-140 环境变化触发验证门

只读验证，不修改代码。

固定目标：target `codex/dav-4-p2a-trunk` @ `f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`。

仅在以下任一外部环境信号发生变化后执行：
- `py_mini_racer` 的 `mr_eval_context` 可用；
- 东方财富 EM 请求出现可复核的成功结构化返回；
- 同花顺 THS 请求出现可复核的成功结构化返回。

若环境信号没有变化，立即报告 blocked，不重复运行组合回归。

验证时固定使用宿主 `.venv310`，标的 `002167.SZ`、`600396.SH`，requested as-of `2026-08-14`，记录实际 `source`、`algorithm_group`、`as_of`、字段、单位、尝试链和失败原因。

只有出现 EM/THS 同标的、同日期、同窗口、同字段/单位的可比记录，才继续 DAV-140 组合验证；否则保持 blocked。禁止合入、重启、解锁后续。
