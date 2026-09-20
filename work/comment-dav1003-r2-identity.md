## 加严 1 再补一层：防「重建健康 executor」的假绿

**本条与前一条的五步顺序合并执行，不替代它。** 加严 2 不变。

### 漏洞

五步顺序能抓到「坏引用」，但抓不到「丢引用」。若实现里无条件 `loop.set_default_executor(None)`，步骤 ③ 的 `run_in_executor(None, ...)` 会**惰性新建**一个健康 executor —— ③ 和 ⑤ 双双通过，而预热时那个原对象被悄悄丢弃：**所有权丢失、线程泄漏、再没有任何引用能关掉它**。只断言「提交成功」永远发现不了。

### 实测复现（`.venv310`, Python 3.10.20）

```
预热后 prior       = ThreadPoolExecutor 0x10ac26e60   _shutdown = False
置 None 后         = None
再次提交后 now     = ThreadPoolExecutor 0x10ac27af0
now is prior       = False          ← 假绿
prior 仍存活        = _shutdown: False | 线程数: 1    ← 孤儿线程，永无人 shutdown
```

### 要求：改为断言身份，而非可用性

① 预热之后保存：

```python
prior_executor = loop._default_executor
```

并在 **③ 失败清理后** 与 **⑤ 正常清理后** 各追加两条断言（共四条）：

```python
assert loop._default_executor is prior_executor
assert prior_executor._shutdown is False
```

被测对象本来就是 loop 的内部引用，**测试中使用 CPython 私有属性是合理的**，不必绕开。

### 配套实现约束

`loop.set_default_executor(None)` **只有在「进入 lifespan 前该值确实为 None」时才可使用**。若进入前已存在 executor，清理时必须**恢复原对象**——不得置 None，不得换成新对象。

### 其余不变

加严 2（真实 provider 路由触发、禁止 `side_effect=` 注入、spy 断言 `bs.login()` 零调用、refusal 非终态且 `total_scanned > 0`）；新 SHA 重跑 RT-FULL-OFFLINE（无任何 `--deselect`/`-k`/路径限定）+ RT-NETWORK；`896eaca3` 与 DAV-1005 的 PASS 已失效，新 SHA 走新开复审卡；主线 `5a0320f` 继续 HOLD。

（已同步 steer 至运行中的 run `01a0aa41`，送达成功；此评论为双发留档。）
