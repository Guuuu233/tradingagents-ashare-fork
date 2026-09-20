# 最新报告主力资金：EM/THS 能力只读探针

## 目标

对 target trunk `codex/dav-4-p2a-trunk@cb9c62e6be92f18aa15cf5a513f9430d0409bf0b` 做只读能力核验，不修改代码。

固定输入：

- `601398.SH`、`002167.SZ`
- `2026-08-14`
- 宿主 `.venv310` / Python 3.10.20

## 必须回答

1. 东方财富 direct endpoint 当前是否可达：HTTP 状态、JSON envelope、rc、schema、请求日期、实际数据日期、可验证字段语义；不得输出凭据或完整原始响应。
2. 同花顺当前公开接口是否有可验证历史 `as_of=2026-08-14` 能力；即时快照不得冒充历史收盘。
3. 对每个源给出 `source`、`source_family`、`algorithm_group`、field、unit、requested_as_of、actual as_of、status、failure category。
4. 明确新浪 Web 历史 fallback 只能归类 `legacy_web_algorithm`，不能算新算法组。
5. 若没有真实 EM/THS 新算法可比证据，明确 blocked，不建议把 legacy 值塞入 consensus。

## 验收

- 只读，不改代码/配置/数据库/用户设置。
- 固定参数、脱敏输出、宿主 `.venv310` 命令结果。
- 报告精确 target SHA、探针命令、结果和限制；最后使用真实项目调度助手 mention。
