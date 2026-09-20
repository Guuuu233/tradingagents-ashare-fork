## Cursor 准予合入 — DAV-567（rebase 后）

**准予合入** tip：`2f96e786e7b80be84254bfe642cb0208eeca4a1b`
父：`e3683628479b204f9839c0424294fc5bde50ba96`

- 相对 tip 仅 fixtures + test；独立审核 DAV-573（旧 `5b63375…`）✅
- Cursor 隔离：**10 passed**

**FF 排队**：须在 Docs(DAV-576) 与 A13(DAV-566) 合入后的 tip 上再 rebase（若父已变），再开运维 FF。当前勿与 Docs/A13 抢同一父并行 FF。
**不准予部署。**
