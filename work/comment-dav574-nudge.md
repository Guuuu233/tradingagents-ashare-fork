## Cursor 催办 — DAV-574

运维仍为 `working`，但主干 tip 仍是 `98fe5d1…`，未见 FF 完成评论。

请立即执行：

```bash
git fetch origin
# 确认 origin/codex/dav-4-p2a-trunk == 98fe5d199e8874ae829d2b492882d82339c836f0
# 确认 origin/agent/dev2/a0-frontend-v2-override == e3683628479b204f9839c0424294fc5bde50ba96
git push origin e3683628479b204f9839c0424294fc5bde50ba96:codex/dav-4-p2a-trunk
```

（或等价线性 FF。）完成后评论新旧 tip，卡 → `done`。禁止 merge / force trunk。**不准予部署。**
