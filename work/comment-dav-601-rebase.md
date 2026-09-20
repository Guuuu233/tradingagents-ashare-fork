主干已线性 FF 到 `b3ba1963e16cb3a5bd6736439db0fd57e7d03e3f`（DAV-595）。

你的候选 `733d40ba3dd0bc67f2b7396f03a393b70557e2a6` 目前 **不在 origin**（`git fetch`/`ls-remote` 均无此 SHA），独立审核无法按远端 exact SHA 验收。

要求：
1. 从新主干 `b3ba1963e16cb3a5bd6736439db0fd57e7d03e3f` rebase（禁止 merge commit）。
2. `git push -u origin HEAD` 使新 40 字符 SHA 出现在 origin。
3. 在本卡贴出 **新** exact SHA、changed files、pytest 命令与退出码。
4. 不得改 `evidence_verifier.py`。不得直接 FF 旧 SHA `733d40b…`。
5. DAV-603 必须改审 **rebase 后的新 SHA**，旧 SHA 作废。
