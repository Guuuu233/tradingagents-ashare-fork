项目规划：work/2026-08-04-development-plan.md 的 M3 里程碑（对齐竣工定义②③：数据诚实 + 稳定）。

## 任务1：Vendor chain 语义重构（KNOWN_ISSUES #1）
route_to_vendor 只在异常时回退，普通失败/拒绝/确认空 字符串都算"命中"停止链路。三种结果折叠成两种行为。
修法：引入结果类型（VendorRefuse/VendorFail/VendorEmpty/VendorOk），或等价的异常层级。详见 docs/KNOWN_ISSUES.md 完整设计。

## 任务2：东财受限接口备用源
东财 push2his 对当前 IP 间歇 RemoteDisconnected。个股资金流已加新浪备用源（模式已建立）。推广到其他受影响接口：
- get_fund_flow_board（板块资金流，新浪/同花顺）
- 龙虎榜等 *_em 接口若失败

## 任务3：任务持久化/优雅关停
分析中重启服务 → 任务中断（今天 5 个任务因此 failed）。修法：任务状态落库 + SIGTERM 优雅等待，或启动时恢复 running 任务。

## 验收
- 三类语义正确区分（拒绝不换源/失败换源/确认空停止）
- 受限接口有备用源，实测可用
- 服务重启不再丢失进行中任务（有测试）

完成小段工作后自动 @项目调度助手 触发下一轮。
