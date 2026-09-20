线性 FF：同花顺大单平级 + 参考可信度（DAV-590）

准予合入 SHA（精确 40 字符，唯一允许）：
`c72dd7b6098297efbec80931dda8bf509c8d8709`

父 tip：
`31c32f0f877e86fc3c06eb58a34b4dc08a453044`

远端分支：
`origin/codex/dav-fund-flow-lg-credibility`

主干：
`codex/dav-4-p2a-trunk`

动作：
1. 核验 `git merge-base --is-ancestor 31c32f0f877e86fc3c06eb58a34b4dc08a453044 c72dd7b6098297efbec80931dda8bf509c8d8709`
2. 核验 `git rev-parse origin/codex/dav-fund-flow-lg-credibility` == 上述 SHA
3. 对 `codex/dav-4-p2a-trunk` 做 **线性 fast-forward only**（禁止 merge commit）
4. 回报 trunk 新 tip 的完整 40 字符 SHA

禁止：部署；改 `credit_weighting_enabled`；合入其他 SHA；reset/force 非必要操作。

关联：DAV-590 实现、DAV-591 独立审核 PASS、Cursor 隔离 101 pytest passed + 「准予合入」。
