# DAV-199 LLM 401 鉴权链只读审计

## 目标

定位三只真实分析均返回 `401 Invalid API key` 的责任层。只读核验，不修改 `.env`、数据库、providers、role_bindings、model_profiles、用户设置、代码或服务。

## 固定证据

- 运行主干与服务：`c39d975793f907bc21200c7efc5dd2676d2e0833`，PID `97800`
- 失败报告/任务：
  - `ec5aa9c7eaa24abdbb316840dd6fdaf5`
  - `2effc2ac6511471d90ee0f68b0a6c5c1`
  - `a79c973b17eb4bc39665cc8591133d92`
- 报错实际请求：`http://92.119.124.146:8317/v1`，模型报告为 `a6api/claude-opus-4-8`，HTTP 401。

## 必须核验

1. 从三份失败记录、服务日志与 LLM call logs 提取：失败角色、实际模型名、provider 名、base URL、HTTP 状态、时间；禁止打印 Key。
2. 读取宿主 `.env` 只判断 `TA_API_KEY` 是否存在、长度、稳定指纹前8位哈希；不得输出原值。
3. 读取数据库 `providers`、`model_profiles`、`role_bindings`、`user_llm_configs`，仅输出：记录 ID/名称、模型、base_url、key 是否为空、key 的长度与哈希前8位；不得输出原值。
4. 用生产解析函数 `resolve_all_roles` 或真实 API 查询三只任务所属用户各角色最终解析结果：role → provider/model/base_url → key 来源层（全局继承/DB provider/用户配置），不输出 Key。
5. 使用“实际失败角色的同一模型 + 同一解析出的 URL/Key”执行最小 `max_tokens=5` 请求，记录 HTTP 状态与脱敏错误；分别测试：
   - 角色最终解析出的 Key；
   - 宿主 `.env` 的全局 Key（如两者指纹不同）；
   不得循环测试，不得测试无关模型。
6. 分类结论：
   - 全局 Key 也 401 → 上游/全局凭据失效；
   - 全局 Key 200、角色 Key 401 → 数据库 Key 覆盖/级联继承错误；
   - 直接请求200、服务任务401 → 运行进程环境/缓存/解析路径错误；
   - 模型名或 provider 路由不一致 → 角色绑定或 provider 路由问题。
7. 给出最小修复建议，但不执行任何配置修改；列明修改哪一层需用户在设置页操作，或若是代码缺陷再另开开发任务。

## 交付

在 DAV-203 评论发布事实表、哈希对比、直连状态、责任层结论和下一步。0代码改动、0测试提交、未重启、DAV-199/DAV-200继续锁定。
