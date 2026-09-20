上一次 run 因全量 pytest 卡死，已 cancel。请 **全新执行纯线性 FF**（不要测、不要改文件）：

```bash
cd <workdir>
git fetch origin target
git ls-remote target refs/heads/codex/dav-4-p2a-trunk   # 期望 93212809a099ebc6e5787bc0468e63969f5496f8
git ls-remote origin refs/heads/agent/1/df73803fe99d     # 期望 d50b0dc3a7b3721aba16ac4474530c1565be79de
git merge-base --is-ancestor 93212809a099ebc6e5787bc0468e63969f5496f8 d50b0dc3a7b3721aba16ac4474530c1565be79de && echo OK
git push target d50b0dc3a7b3721aba16ac4474530c1565be79de:refs/heads/codex/dav-4-p2a-trunk
git ls-remote target refs/heads/codex/dav-4-p2a-trunk   # 必须等于 d50b0dc3a7b3721aba16ac4474530c1565be79de
```

三禁：禁 force / 改代码 / 重启进程。完成后把前后 tip 贴评论并关卡。

[@代码运维测试员](agent://f179edb8-9a81-4dbd-8787-afbfd307eda4)
