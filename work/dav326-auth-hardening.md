# P1：显式无效Token拒绝 + 验证码错误上限/IP频控

**前置：DAV-322精确SHA复审通过后，从最新主干/集成SHA开工；当前todo。**

## 已核安全缺陷
1. `RequireUser`：请求显式携带损坏/伪造 Authorization 时，JWT异常被except pass，可能降级为 local-default-user；破坏鉴权边界。
2. 登录验证码：6位码10分钟有效，错误验证未累计attempts/作废，缺少验证端IP频控。

## 契约
- 显式 Authorization：格式错误、JWT无效/过期、API token无效均401；只有完全无Authorization时，且配置允许本地匿名模式，才可local-default-user。
- Web-only端点继续拒绝API token，不放宽。
- EmailVerificationCodeDB增加attempts（迁移向后兼容）；单码错误5次立即consumed/失效。成功验证清理/消费。
- request-code已有频控需保留；verify-code新增按IP+email滑窗频控，超限429，响应不泄漏邮箱是否存在/剩余尝试数。
- production绝不回显dev_code；development现有流程测试保持。
- JWT默认开发secret警告可在不影响既有加密数据的前提修复；若会破坏解密则仅新增启动告警，不擅自轮换。
- 不改用户数据、provider、模型。

白名单：api/main.py、api/services/auth_service.py、api/database.py及认证测试/迁移测试。
验收：无header匿名允许；bad bearer 401；expired 401；wrong code 1-4拒绝、5作废、正确码后也失效；频控429；用户隔离。`.venv310`测试/compileall/diff-check。禁止@调度助手。
