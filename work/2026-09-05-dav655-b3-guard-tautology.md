# DAV-655：Track B-3 防降级测试 tautology 返修

**基线：** `fd5266cba24bab52f547c25b0bb53a5266e73c31`（`origin/agent/2/4136dcb57375`；主干仍是 `4fdcf8e`，本卡不要 FF）。  
**父卡：** DAV-651。DAV-654 可能仍在审旧 SHA；本卡出**新 commit**，合入审 SHA 以本卡 tip 为准。

## 缺陷

`tests/test_social_acceptance_plan_guard.py` 的 `test_historical_snapshot_no_backfill_invariant`：

```python
assert "禁止用当天新采回填" in content or "严禁当天新采回填" or "禁止用当天或事后新采" in content
```

中间项是非空字符串，恒为真。空文档也会 pass。Cursor 用无匹配文本复现：表达式求值为该字符串本身。

## 允许改

只改 `tests/test_social_acceptance_plan_guard.py`（必要时加一条负例：临时内容缺禁令句应失败——不要改产品代码）。文档门槛数字不得改低。

正确写法：三个短语都用 `in content`，用括号。

单 commit。禁止 push 主干、采集、active、部署。完成后 40 位 SHA，`in_review`。
