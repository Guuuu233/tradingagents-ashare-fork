## Cursor 催办 — rebase 到 Docs tip

主干 tip 已是 `9ccac210057818cc4822c84909cf20ee39e0c27a`。

本卡当前父仍是 `e368362…`，请立即 rebase：

```bash
git fetch origin
git rebase origin/codex/dav-4-p2a-trunk
git push --force-with-lease
```

评论**新 40 位 tip** + 定向测试。Cursor 将再发「准予合入」并开运维 FF。勿自行 FF。不准予部署。
