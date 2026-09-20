C1当前测试中2条claim虽然被拒绝，但可能只是命中后置“少于3个battlefield”闸；这不能证明数量契约已从2-3改为恰好3。

请把2条claim断言收紧：除`invalid_protocol`外，`error_detail`必须明确命中“数量/恰好3/exactly 3”，且不得只因“战场不足”通过。先在当前`2 <= len <= 3`实现上观察该断言RED，再改生产为`len(new_claims_list) != 3`并GREEN。1/4条同样应命中数量错误。

另外保留现有两条有效RED：静态INV泄漏、Bear后state stage未切challenge。继续当前唯一worktree，不提交。