验收规格纠正：最终去重 changed-file 数应为 **8**，不是9。原因：原33c6e6b的6文件已经包含 `tests/test_debate_protocol_metadata.py`；25e52c0增加的该测试仍是同一文件，只新增 `propagation.py` 与 `trading_graph.py` 两个独立路径，因此总数为6+2=8。

当前线性树 `50e1153 -> 33c6e6b -> 25e52c0 -> 34b1dcf(重放8ccd639)` 和已列出的8文件范围正确。禁止为了满足旧卡片的错误数字新增第9文件或修改代码/测试。继续按现有树执行生产可达性smoke、定向/replay、diff-check/compileall和仅一次全量，保存原始全量输出并推送当前组合分支。

[@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3)
