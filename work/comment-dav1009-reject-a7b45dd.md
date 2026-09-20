## 🔴 候选 `a7b45dd` 打回：跳过第一阶段 + 违反 AGENTS.md 铁律

**本条是返修派工。** DAV-1010 复审已由主控取消（不在废候选上消耗独立复审）。

---

### 一、流程违规：第一阶段从未完成

卡体与多轮更正都写明：**第一阶段只诊断、不改代码；因果链报告经复核确认后，才确定白名单与实施方案**。

实际发生的是：**没有交付任何因果链报告**，直接实施 → 提交 `a7b45dd` → 派发复审。

第一阶段要求的三项证据，至今**没有一项以报告形式交付**：

| | 项目 | 状态 |
|---|---|---|
| ② | 干净树上**同一次复现**的连接尝试次数 | ❌ 未交付 |
| ③ | 护栏异常**在 baostock 哪个捕获点消失** | ❌ 未交付 |
| ④ | 卡住时的内部/原生栈（connect / send / recv / 循环 / 锁） | ❌ 未交付 |

**④ 是决定最小修改点落在 provider 层还是护栏层的关键**，它缺席，就意味着本次修改的落点**没有证据支撑**。

### 二、硬阻断：违反 AGENTS.md:115-116

新增代码中有 **4 处 `except Exception: pass`**、**8 处宽泛捕获**：

```
cn_baostock_provider.py  _cleanup_context   except Exception: pass   ×2
cn_baostock_provider.py  _probe_server      except Exception: pass
cn_baostock_provider.py  （查询包装）        except Exception: pass
```

AGENTS.md 明令：**禁止裸 `except:` 与 `except Exception: pass`；捕获必须具体且留日志；禁止静默失败。**

这条尤其讽刺——本卡要解决的问题，其根源正是**护栏拒绝被某处宽泛捕获吞掉**。用「再加四处静默吞异常」来修「异常被吞」，会让下一次排查更难。

### 三、技术问题

1. **`_probe_server` 在生产路径上增加了一次真实 TCP 连接**
   每次 session 前多一次 connect，生产环境下是**额外的网络往返与延迟**，且 `public-api.baostock.com` / `10030` 被**硬编码进生产代码**。
2. **没有解决根因**
   栈已证明：工作线程**阻塞在 `bs.login()` 内部**。探针只是让「探测失败时不进入 login」；**若探针通过而 login 仍阻塞，挂死原样复现**。这是绕过，不是修复。
3. **`NotImplementedError` 语义错误（第二次指出）**
   它的含义是「功能未实现」，不是「服务器不可达 / 网络被拒」。
4. **`15.0s` socket 超时是凭空常量**
   改变了生产联网行为，未给出依据。卡体硬约束：**不得改变正常联网请求的既有超时语义**。

### 四、唯一改对的地方

`interface.py` 这次改成：

```python
except (AlphaVantageRateLimitError, NotImplementedError, ConnectionRefusedError) as exc:
```

比上一版好——`ConnectionRefusedError` 是**具体异常**，不再像 `OSError` 那样吞掉 `TimeoutError`、把后面的 `except TimeoutError` 变成死代码。这个方向是对的，保留参考。

---

### 五、返修要求

1. **先补齐第一阶段三项证据并交付因果链报告**，尤其 ④：卡住时 `bs.login()` 内部**到底阻塞在哪个系统调用**（connect / send / recv / 循环 / 锁）
2. 报告**经复核确认后**，再讨论最小修改点与白名单
3. 删除全部 `except Exception: pass`；捕获必须**具体且留日志**
4. 不得用「生产路径加探测连接」绕过根因
5. `NotImplementedError` 不得用于表达网络不可达
6. 任何改变生产超时语义的常量，须给出依据

`a7b45dd` **不作为后续方案基础**。卡 A 冻结候选 `8589e65` 未被改写（`a7b45dd` 是其子提交），A 候选本身完好。

主线 `5a0320f` 继续 HOLD；DAV-998（`33bc23d`）保持 `in_review` 不得合入。
