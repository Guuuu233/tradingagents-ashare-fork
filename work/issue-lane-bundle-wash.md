# P0 补丁：信息增量闸门「捆绑洗白」废除（per-claim 拒收）

**执行纪律：**
- 基线：远端 `target/codex/dav-4-p2a-trunk`（本地 HEAD 可能是 59f7253/DE；开工前 `git fetch target && git checkout -B fix/ta-audit-bundle-wash target/codex/dav-4-p2a-trunk`）。
- **只改** `tradingagents/agents/utils/debate_utils.py` + 对应测试。禁改 `zh.py`、`evidence_verifier.py`、`api/main.py`、`report_service.py`、`setup.py`、researcher 节点。
- C 泳道 (`fix/ta-audit-c` @ 64f9167) **未碰**本文件，无冲突。
- 环境：宿主 `.venv310`，`env -u PYTHONPATH .venv310/bin/python -m pytest`。
- 金标准：`tests/golden/audit_20260823/3c09051e7e364d859dfbe5f1af7cc2c9_result_data.json`（000333 INV-9 证据 vs INV-1 相似度 0.986/1.000）。

## 缺陷（已 DB+函数重放证实）

`debate_utils.py` Check D（约 844-889 行）：逐条 claim 判重后，**仅当全部 new_claims 都是 duplicate 才拒收整条消息**。

000333 第 5 次发言重放：
- INV-9：claim_sim 0.616，new_ev=0，all evidence ≥0.82 → `is_dup=True`
- INV-10：claim_sim 0.4，new_ev=2 → `is_dup=False`
- `valid_new_claims` 非空 → 整条消息放行，克隆的 INV-9 入库

`duplicate_claim_ids` 在 round_messages 中实测为 None/空（代码 936 默认 `[]`，1143/1178 有回写路径但入库未生效）。

## 修复（TDD，闸门只加不减）

1. 失败测试 `tests/test_debate_bundle_wash.py`（或扩现有 `test_debate_e2e_protocol_repair.py`）：
   - 夹具：同侧历史含 INV-1 证据；新消息含 INV-9（证据逐字/≥0.82 相似）+ INV-10（轻度改写、有新证据）。
   - **现状期望（修前 RED）**：整条消息 accepted，INV-9 进入 claims。
   - **修后期望**：消息可以 accepted（因为 INV-10 有效），但 INV-9 **不得**进入 `claims`；`duplicate_claim_ids` 含 INV-9（或等价标记 `recycled=true`）；INV-10 仍入库。
   - 负例：全部 new_claims 都是 duplicate → 整条消息仍 `invalid_protocol`（现有整包拒收路径保留）。
   - 既有协议测试全绿（5 次越界拦截语义不回归）。

2. 实现：per-claim 判重后，duplicate 的 claim **拒收入库**（从即将写入的 new_claims 中剔除），并把 id 写入 `duplicate_claim_ids`；不要再用「整条消息全重复才拒」给克隆开后门。
3. **不要**在本卡改 Check B/C 的 `message_index>=2`（那是 F1 分相，另卡）。不要改 prompt。

## 验收

1. 上述测试全绿 + `tests/test_debate_e2e_protocol_repair.py` 等既有辩论闸测试全绿。
2. 用 000333 金标准 claims 离线重放：INV-9 类克隆不得入库。
3. 分支推远端，issue 评论附精确 SHA + 测试输出。等 Hermes 精确 SHA 复审，禁止自行合主干。
