```yaml
SPEC_VERSION: 2
RISK: STRICT
PHASE: SPEC_LOCKED
OWNER: agent:senior-dev-2
OWNER_ID: 5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc
BASE_SHA: 5a0320f0618d203e95e7197e08b110c7850078d4
TARGET_REF: refs/heads/agent/senior-dev-2/card-c-lifespan-v1   # 当前不存在，唯一交付 ref
WHITELIST:
  - api/main.py
  - tests/test_api_smoke.py
  - tests/test_lifespan_state_restore.py        # 新测试文件（建议）
FORBIDDEN:
  - api/services/report_service.py
  - tradingagents/dataflows/providers/registry.py
  - data/tradingagents.db
OPERATION_BANS:    # 操作禁令（非路径），独立于路径型 FORBIDDEN
  - 不得合入主线 5a0320f 或任何受控基线
  - 不得部署 / 重启服务 / 写生产库
  - 不得 amend/rebase/force-push，只能线性追加提交
  - 不得改动测试隔离护栏（卡A conftest）与拒绝传播语义（卡B interface）
ACCEPTANCE:
  # executor 全局状态：失败路径与正常退出后均恢复原对象
  - 启动期注入异常后：loop.set_default_executor 恢复【进入前原对象】（非 None 非新对象）
  - _default_executor 模块全局引用在失败路径后恢复原值
  - 新建 new_default_executor 已 shutdown（不泄漏存活 ta-asyncio 线程）
  # socket 全局状态：同样恢复
  - 启动期注入异常后：socket.getdefaulttimeout() 精确恢复【进入前原值】
  - 正常 lifespan 退出后：socket.getdefaulttimeout() 精确恢复原值
  # 顺序语义：失败→正常 在同一 loop 成立（非成功→成功自愈假象）
  - 测试构造：同一 event loop 先跑失败 lifespan 再跑正常 lifespan，断言状态恢复
TEST_COMMANDS:
  - env -u PYTHONPATH .venv310/bin/python -m pytest tests/test_lifespan_state_restore.py tests/test_api_smoke.py -q
DIAGNOSIS:
  root_cause: lifespan 启动期异常 → yield 前中断 → 清理段不执行 →
              socket.getdefaulttimeout、new_default_executor、_default_executor 全局泄漏
  evidence_at_base_5a0320f:
    socket.setdefaulttimeout:        api/main.py:339
    executor 挂全局:                  api/main.py:360-370 (global/set_default_executor/_default_executor=)
    可抛启动段:                       api/main.py:375-437 (init_db/store/auth/日历/stock/recovery/backfill)
    yield:                           api/main.py:439
    仅成功路径执行的清理:              api/main.py:440-443
  existing_test_gap: 失败路径零用例（startup_failure/cleans_up/_failing_lifespan 0 命中；
                    现有 test_lifespan_can_restart_on_same_event_loop 仅测成功→成功=自愈假象）
SCHEMA_CHECK:
  - SPEC_VERSION 整数、RISK∈{FAST,STANDARD,STRICT}、PHASE 枚举、OWNER_ID 40位UUID
  - BASE_SHA 40位十六进制、TARGET_REF refs/heads/* 、WHITELIST/FORBIDDEN/OPERATION_BANS/TEST_COMMANDS 均为列表
```
