只读复审前端候选 `0634112364772271119e22e4e7ad8bf961c27c76`，父提交必须为 `79a552f4b28742983c01d3af91fa45bc17f191f0`。重点核验：历史报告从 detail API 的 result_data 渲染，不依赖实时 store；逐轮/attempt/claims/manager/evidence完整；旧报告fallback；fatal/invalid醒目标识；默认折叠和稳定key；不得显示 reasoning_content/密钥；无后端越界。复跑 Vitest、tsc、Vite build，检查bundle与现有10章节未破坏。禁止修改代码。

[@代码审核员2](mention://agent/96016c24-4052-459f-b438-0f70685b5e53)
