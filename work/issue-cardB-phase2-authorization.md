## 🟢 第二阶段实施授权（DAV-1009 卡 B）

**本条是实施授权。** 第一阶段已闭环（判定 H2，见上条裁定）。以下为主控裁定的实施范围与验收条件。

**父提交固定 `8589e65`。第二阶段不构成合入授权。**

---

### 〇、先纠正一处主控自己的错误

主控此前提到「`cn_baostock_provider` 已有 `ensure_baostock_socket_hardening()` 这个既有 vendor 加固入口」——**该说法错误，已实测推翻**：

```
8589e65 中 grep ensure_baostock_socket_hardening  → 0 个文件命中（不存在）
85db74d 是否为 8589e65 祖先                        → 否
```

该函数**只存在于旧的平行候选 `85db74d`**，不是本线祖先，且其中包含**静默吞错、进程级默认超时**等**已判废设计**。

> **只能参考问题范围，不得 cherry-pick、不得原样复用。**

当前实况（`8589e65`）：`cn_baostock_provider.py:41` 的 `_bs()` 仍是**直接 `import baostock` 后调用 `login()`**，无任何加固。

### 一、总裁定：主修改点在 provider 集成层

- **主战场 = `cn_baostock_provider.py`**（provider 集成层）
- **`interface.py` 只承担「明确拒绝不得重试」的窄语义**，**不作为修复 baostock 的主战场**

### 二、拆成两个提交

#### 提交 B1：baostock 集成加固

1. 在 `cn_baostock_provider.py` 建立**唯一的生产访问入口和 session**
2. **`connect` 成功后才能写入 `context.default_socket`**；失败必须**关闭、清空并原样抛出异常**（这正是 `socketutil.py:39/41` 的病灶）
3. 使用**单 socket 超时**，**不得调用 `socket.setdefaulttimeout()`**（进程级默认超时是已判废设计，也正是本次挂死的成因之一）
4. `send_msg` 使用**受控发送、EOF 检查、异常后清理**；**不得静默吞错**
5. **安装失败必须 fail-closed**——**不能只写状态位后继续 `bs.login()`**
   > 复审口径：见到新增状态位/返回码必问「谁在读它？读到坏值时行为变了吗？」
6. 若使用锁，**只允许保护一次性补丁安装**；**禁止用全局锁串行化所有 baostock 请求**
7. 把 `tradingagents/eval/v03_return_measure.py:1148` 与 `:1202` 的**两处直接导入迁移到同一入口**
8. 加 **AST 守卫**：`tradingagents/`、`api/`、`scheduler/`、`scripts/` **不得再出现旁路导入**

   实测当前旁路导入共 **3 处**：
   ```
   tradingagents/dataflows/providers/cn_baostock_provider.py:43
   tradingagents/eval/v03_return_measure.py:1148
   tradingagents/eval/v03_return_measure.py:1202
   ```

#### 提交 B2：明确拒绝的传播语义

1. 新建**轻量、生产侧定义**的通用 **`NetworkAccessDeniedError`**
2. 测试侧 `OfflineTestGuardrailError` **继承它**——依赖方向保持 **「测试 → 生产契约」**
   > 生产侧**不得**识别测试模块、不得引入 pytest / offline guard 概念
3. `interface.py:477` 在 **`TimeoutError` 和通用异常之前**单独处理它：**不重试、不 fallback、立即上传播**
4. **普通 `TimeoutError`、`OSError` 与既有 45 秒策略保持原样**
   > 注意异常层级顺序：`TimeoutError` 是 `OSError` 子类，把宽泛分支放前面会使后面的分支变成死代码——前一版候选 `a7b45dd` 已犯过此错

### 三、验收至少锁住这几条

| # | 验收点 |
|---|---|
| 1 | 护栏拒绝后：**一次 connect、零 send**、耗时**明显低于 45 秒**、**工作线程结束**、**全局 socket 为 None** |
| 2 | **EOF**：一次空读后**明确失败**，**不忙循环** |
| 3 | **加固安装失败**：`bs.login()` 调用次数为 **零** |
| 4 | `NetworkAccessDeniedError` **不重试**；**普通超时仍走原有重试语义** |
| 5 | v03 **两条路径均使用统一 session** |
| 6 | 静态守卫能**真实抓出**直接导入**和动态字面量导入** |

### 四、禁止触碰

```
registry.py · api/main.py · vendor site-packages · 数据库 · 部署状态
```

卡 A 三文件（`tests/conftest.py`、`tests/test_offline_network_guardrail.py`、`tests/test_v03_return_measure.py`）**保持 0 diff**。

### 五、硬性纪律（沿用，不再重复解释）

- **禁止裸 `except:` 与 `except Exception: pass`**（AGENTS.md:115-116），捕获必须具体且留日志
- **`NotImplementedError` 不得用于表达网络不可达**
- 不得在生产路径上增加探测性连接
- 任何改变生产超时语义的常量须给出依据
- 验收材料（日志、报告、采样）**一律走仓库外路径**，不得进候选提交

### 六、流程

1. B1、B2 **分两个提交**，父提交 `8589e65`
2. 先做 **B 的定向测试**
3. 再做**独立复审**（另开复审卡，不复用 DAV-1010）
4. 锁定 **A+B 组合 SHA** 后，才恢复**唯一一次**完整全量门禁

**第二阶段不构成合入授权。** 主线 `5a0320f` 继续 HOLD；DAV-998（`33bc23d`）保持 `in_review` 不得合入。
