# DAV-269 主干 fast-forward 合入 b1fa020（5 行业动态产业链 + 知识库注入）

Hermes 代表用户授权合入。对照《项目加强方案》阶段二：5 核心行业 INDUSTRY_LINKAGE_MAP。独立终审 DAV-268 PASS。

## 门闩

- 当前主干必须仍是：`target/codex/dav-4-p2a-trunk@2e9e674ceff062b7b35ae010a954b61f7575c47e`
- 候选：`target/integration/dav256-dav258-on-2e9e674@b1fa020a0b511be9266651cd69dd2eb091d84cc8`
- 父链：`2e9e674 → 9852c70`（DAV-258）`→ b1fa020`（DAV-256）
- `git merge-base --is-ancestor 2e9e674 b1fa020` 必须成立；ahead 2 / behind 0
- 相对 `2e9e674` 仅产业链/知识库相关文件，**不得含** `api/main.py`
- 禁止把旧树 `520f871`（基于 `0b10041`）直接 FF 到当前主干

## 操作

独立 checkout。禁止改宿主工作树、禁止 `reset --hard` / `clean -fd`、禁止碰 `.env`。
对 `codex/dav-4-p2a-trunk` **fast-forward only** 到 `b1fa020`。禁止 merge commit、禁止强推。
GitHub API + `git ls-remote` 回读主干 HEAD = `b1fa020a0b511be9266651cd69dd2eb091d84cc8`。

## 禁止

不部署、不重启 8000、不改用户模型/Key/providers/持久轮次。不得 @项目调度助手。立即执行。
