# 全面终审 泳道一：API 与后端路由层审计 (api/)
【严格只读，不修改代码，不合入，不重启】

重点审计范围：
1. `api/main.py`
2. `api/database.py`
3. `api/job_store.py`
4. `api/services/`（auth_service, role_routing_service, custom_prompt_service, report_service, calibration_service 等）

审查要求：
- 逐行检查异步与多线程安全、数据库连接泄露、死锁风险；
- 检查大模型 API 路由在单厂商/多厂商、有无 token 时的鉴权与异常处理；
- 检查作业状态流转、取消、中断恢复逻辑是否有遗漏；
- 检查是否存在死代码、未用导入、未捕获异常；
- 产出详细缺陷清单（文件、行号、问题现象、风险等级、优化建议）；
- 评论末尾不要 mention 项目调度助手。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
