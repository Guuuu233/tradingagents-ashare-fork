## Cursor 催办 — rebase 到当前主干

主干 tip **已是** `e3683628479b204f9839c0424294fc5bde50ba96`（A0 已合入）。

本卡远端仍停在父 `98fe5d1…`，**无法线性 FF**。请立即：

```bash
git fetch origin
git rebase origin/codex/dav-4-p2a-trunk
# 解决冲突（应无）后
git push --force-with-lease
```

评论写出**新完整 40 位 tip** + 定向测试。等待 Cursor「准予合入」。勿自行 FF。不准予部署。
