编排打断：本卡是 **线性 FF only**。祖先核验与候选 SHA 已齐，**不要跑全量 pytest**（会卡 120s+ 超时）。

立即执行并贴读回：
```
git push target d50b0dc3a7b3721aba16ac4474530c1565be79de:refs/heads/codex/dav-4-p2a-trunk
git ls-remote target refs/heads/codex/dav-4-p2a-trunk
```
期望 tip=`d50b0dc3a7b3721aba16ac4474530c1565be79de`。三禁仍有效：禁 force / 改代码 / 重启。

[@代码运维测试员](agent://f179edb8-9a81-4dbd-8787-afbfd307eda4)
