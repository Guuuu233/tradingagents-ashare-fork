## 请按 DAV-977 的打回意见返修

DAV-977 已在项目锁定解释器（`Python 3.10.20`）下复审候选 `f317e82431ab4d950a599e6ebd304cc3e53f1e51`，结论为 ❌ **打回修改**。请到 DAV-977 阅读完整意见后在本卡返修。

要求：

1. 只改打回意见点名的问题，不扩大白名单。
2. 新候选的**直接父必须等于交付当时的 `origin/codex/dav-4-p2a-trunk` tip**（现为 `b95a9b88c81e87a4da121f9945aedbe844fa04e0`，交付前用 `git ls-remote` 重新核对）。
3. 全量回归用固定命令：绝对路径 `.venv310/bin/python`、`-p no:randomly`、deselect DAV-979 死锁用例、`DATABASE_URL` 指向隔离临时库；贴 `python -V` 与精确数字。
4. 交付完整 40 位 SHA + 精确远端 ref + 白名单清单 + `git diff --check` + clean 工作树。

注意：你是本候选的实现者，**返修由你做，但复审不得由你执行**（D-011 §3 写审分离）；复审仍走代码审核员。

不合入、不部署、不重启、不写生产库。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
