## Cursor 准予合入 — DAV-566（rebase 后）

**准予合入** tip：`d3daa727c46d5e3a32fe28501348e9761479ee96`
父：`e3683628479b204f9839c0424294fc5bde50ba96`（当前主干 tip）

- 相对 tip 文件白名单与独立审核 DAV-571（旧 tip `748b768…`）一致
- Cursor 隔离复测：industry persistence + schema migration **29 passed**

**FF 排队**：若 DAV-576 Docs 先合入，请先 rebase 到新 tip 再 FF；若主干仍为 `e368362…` 则可线性 FF 本 SHA。
**不准予部署。**
