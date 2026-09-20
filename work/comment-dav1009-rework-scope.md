## 返修范围（最小化）——三项，不得外扩

**本条是返修派工。** 三个问题**不能由其他合格项抵消**；合格项不推倒重来。

---

### 一、v03 两条路径**分开测试**

**不要用一个累计 `call_count >= 1` 代替两条路径各自被执行的证明。**

| 路径 | 测试构造要求 |
|---|---|
| `_get_stock_metadata` | **清空实例缓存**，并让**全局元数据明确未命中**，真实进入 baostock fallback |
| `is_st` | **预置可用 metadata**，避免提前返回，再证明**第二条查询路径**使用统一 session |

两条路径各自一条测试，各自断言自己那次 session 调用。

### 二、明确拒绝必须**先穿透**

v03 两处（`:1156`、`:1206`）改为：

```python
except NetworkAccessDeniedError:
    raise
except Exception as exc:
    logger.debug(...)
    return None
```

> 这**不影响** v03 对「普通数据不可用返回 `None`」的既有 fail-closed 语义，
> 只阻止**明确的网络政策拒绝**被降级吞掉。

### 三、重试语义必须**分类保持**

| 异常 | 要求行为 |
|---|---|
| `NetworkAccessDeniedError` | **不重试、不 fallback**，原样抛出 |
| baostock 返回**非零 login 结果** | 保持原有 **「不重试、转下一 vendor」** |
| 普通外层 `TimeoutError` | 保持现有 **45 秒重试策略** |

> **不得把所有连接类异常统一塞进通用 `Exception` 重试分支。**
> 当前 `ConnectionError` 落入 `:538` 通用分支导致多一次 login 尝试，即属此类。

---

### 四、提交与核验

- **线性追加提交**，**不得 amend `5feac15` 或 `24f1362`**
- 最终重新核：**累计白名单**（`git diff --name-only 8589e65..<最终SHA>`）、**谱系**（`git log -1 --pretty=%P` 实测）、**同一套定向测试**
- **全绿后**才能进入独立复审

**不构成合入授权。** 主线 `5a0320f` 继续 HOLD。
