## ⚠️ 第二阶段授权的两处更正（台账口径）+ 一项实施重点

**承接上一条实施授权，其余内容不变。** 以下两处若不改，交付时会被形式门禁误杀。

---

### 更正①：提交谱系必须是线性的

```
8589e65 → B1 → B2        （B2 即最终 A+B SHA）
```

- **只有 B1 的直接父是 `8589e65`**
- **B2 的直接父必须是 B1**
- **不要把 B1/B2 做成两个同父的平行分支**——那样还要额外做一次整合，白白多一轮

交付时用 `git log -1 --pretty=%P <SHA>` **实测**直接父，不得凭印象书写。

### 更正②：「卡 A 三文件 0 diff」作废

**这条是主控给错了。** 它与 B 的任务本身矛盾：

- B2 **必须修改 `tests/conftest.py`**——要让测试侧 `OfflineTestGuardrailError` **继承**生产侧 `NetworkAccessDeniedError`
- AST 守卫**大概率要修改 `tests/test_offline_network_guardrail.py`**

**准确口径：**

| # | 要求 |
|---|---|
| a | **不 amend、不重写** `8589e65` 的三个既有提交（`2f5fa28` / `2ae1fed` / `8589e65` 一律不动） |
| b | B 相对 `8589e65` **可以在明确白名单内修改 A 的测试文件** |
| c | **每处修改只能服务于 B 的传播契约或静态守卫**——不得顺手改别的 |
| d | 最终按 `git diff --name-only 8589e65..<A+B SHA>` 核**累计白名单**，**而不是要求那三个文件零差异** |

### 实施重点新增：状态位不能只用于报告

加固**安装失败**时：

- **调用路径必须在 `bs.login()` 之前实际拒绝继续**——不是记一个 flag 然后照常往下走
- **必须有测试断言 `bs.login()` 调用次数为零**（对 login 打桩计数，断言 `== 0`）

> 复审按既定口径查：**「谁在读这个状态位？读到坏值时行为变了吗？」** 只写不读 = fail-open = 打回。
> 先例：`ensure_baostock_socket_hardening()` 失败时 `return False`，而调用方丢弃返回值照常继续——修复静默消失。

---

其余授权内容不变：主战场 provider 集成层；`interface.py` 只承担「明确拒绝不得重试」窄语义；禁 `setdefaulttimeout()`；禁裸 `except` 与 `except Exception: pass`；禁止触碰 `registry.py`、`api/main.py`、vendor site-packages、数据库、部署状态。

父提交固定 `8589e65`；**第二阶段不构成合入授权**；主线 `5a0320f` 继续 HOLD。
