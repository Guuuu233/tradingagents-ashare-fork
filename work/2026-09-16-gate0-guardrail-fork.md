# Gate 0：护栏独立验证与分叉规则（2026-09-16 收口决定）

> ## ✅ 执行结果：Gate 0 = **FAIL**（候选 `28c995d`，2026-09-16 晚）
>
> | 条目 | 报告 | 主控复核 |
> |---|---|---|
> | ① 代理变量全空 | PASS | 可信，但在②③失效前提下无独立意义 |
> | ② audit hook 落盘 | PASS | **不予采信**：日志仅 3 行，全为 INIT 期 Redis 本地连接，无任何用例级记录 |
> | ③ 非本地外连 0 次 | PASS | **空真值**：用例级联崩溃，测试未有效执行 |
> | ④ 身份无漂移 | **FAIL** | 属实，已逐行核对 |
> | ⑤ RT-FULL 自然结束 | **FAIL** | 属实，5143 用例级联 `AttributeError` |
>
> **分叉已执行**：DAV-1003 → `blocked`；DAV-998 → 独立候选已开工；DAV-995 / DAV-989+996 → 待拆回专门返修线。
>
> **下一轮 Gate 0 必须从零重建 ①②③**，不得标记为「上轮已通过」，并补齐 §正控 sentinel 与 §证据边界 两项。
>
> **根因定性更正**：身份漂移**不是**「测试非法 mock 破坏」，而是 **两代护栏相撞**——
> `test_fund_flow_scale_consumption.py:48` 的 `guard_no_network_calls`（autouse，`patch("socket.socket.connect")`）与
> `test_horizon_return_labels.py:806` 的 `monkeypatch.setattr(socket, "socket", block_socket)`
> 都是**先于 DAV-995 存在的、用例自带的离线防护**，意图与全局护栏一致。
> 真正的缺陷是全局护栏**未与既有用例级防护做任何调和**，且在 `socket.socket` 被替换为函数后仍按类去读写 `.connect` → 级联崩溃。
> **这是护栏的健壮性缺陷，不是测试的错。不得以「非法 mock」为由删除既有防护。**

## 为什么设这道闸

阻断 4 证明：此前所有 RT-FULL-OFFLINE 都带 `http_proxy=http://127.0.0.1:9` 代理黑洞，
护栏的有效性**从未与黑洞分离验证过**。因此 DAV-995 的「护栏有效」与「28min→7min51s 提速」
两个结论**均不成立**，此前全部 RT-FULL 证据**失去独立性**。

护栏是整合候选的基石。基石未验之前，继续在 DAV-1003 上叠加修复是无效投入——
后面十一项验了也没意义。故设 **Gate 0**：**只验护栏，一次性判定，不开代码复审、不跑其余验收。**

## Gate 0 执行前提

- 先 **commit**，锁定一个**完整 SHA**（不得在未提交工作树上取证）
- 该 SHA 上**只做 Gate 0**，不开代码复审卡，不跑其余十一项验收

## Gate 0 通过条件（五条全满足）

1. **清除大小写全部代理变量并记录环境**
   `http_proxy` / `HTTP_PROXY` / `https_proxy` / `HTTPS_PROXY` / `all_proxy` / `ALL_PROXY` / `no_proxy` / `NO_PROXY`
   交付报告须附清除后的环境快照。**不得设任何代理黑洞。**

2. **audit hook 只观察、只落盘，不负责拦截**
   用 `sys.addaudithook` 监听 `socket.connect`，逐行 append + flush 到文件，记录 `(nodeid, 地址, 阶段, 时间)`。
   ⚠️ **hook 绝不可参与拦截**——否则它会替失效的 wrapper 兜底，**再次造成假绿**。
   拦截只能由护栏 wrapper 负责，hook 仅作独立观测层。

3. **非 network 用例出现任何非本地 `socket.connect` audit 事件即失败**
   一次都不行。

4. **setup / call / teardown 三阶段均无八个 callable 身份漂移**
   `connect` / `connect_ex` / `sendto` / `create_connection` / `send` / `sendall` / `recv` / `recv_into`
   须 `is` 到护栏版本。记录**第一个**漂移的 nodeid 与阶段。

5. **RT-FULL 自然结束，失败集合与 `5a0320f` 基线一致**
   基线 20 项，零新增。不得带任何 `--deselect` / `-k` / 路径限定。

## 判定纪律

**卡死、外连、身份漂移——只需出现一次即为失败，不允许靠重跑洗掉。**
（这三类缺陷都是间歇性的：需要对端恰好关闭、或恰好命中某个用例顺序。重跑变绿只说明没命中，不说明已修复。）

## 分叉规则

### Gate 0 通过

→ 在**同一 SHA** 上继续验其余十一项 → 通过后建立**新复审卡** → 再议合入。

### Gate 0 失败

→ **立即停止 DAV-1003，不再往整合候选上叠加修复。**

拆分重做：

| 部分 | 去向 |
|---|---|
| DAV-995 护栏与 baostock EOF | 拆回**专门返修线**，独立重做 |
| DAV-989 / DAV-996 的网络与生命周期部分 | 同上，拆回专门返修线 |
| DAV-998 | 恢复为从 `5a0320f` 派生的**两文件独立候选** |

## DAV-998 独立路径（与 Gate 0 解耦）

- 候选：从 `5a0320f` 派生，仅 `tests/test_historical_cases.py` + `tradingagents/knowledge/historical_cases.py`
- 须先清掉 `tests/test_historical_cases.py:2055: new blank line at EOF`，产出新 SHA
- 流程：**定向测试 → 只读复审**
- **终点限定**：最多到达「**候选审查完成**」

  ⚠️ **在全局 RT-FULL 门禁重新可信之前，不得准予合入。**
  DAV-998 自身证据是干净的，但「定向测试 + 只读复审通过」**不等于**合入授权——
  全局回归门禁当前不可信，无法排除它与其余改动的交互风险。

- **资源纪律**：Gate 0 的 RT-FULL 运行期间**不得并发启动**，避免抢占资源污染门禁结果

## 不变项

主线 `5a0320f` 继续 HOLD。不合入、不部署、不重启服务、不写生产库。
