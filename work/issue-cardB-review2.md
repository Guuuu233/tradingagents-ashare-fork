## 卡 B 候选 603a92e 独立只读复审

**只读复审**：不得修改代码、不得提交、不得合入、不得部署、不得重启服务、不得写生产库。

---

### 一、被审对象

```
候选 SHA   603a92ec85a43ce03ae899cc87a1559fa45f7016
直接父     4cc436ac058b3d4a57c2e52ab3c6da3ab76ff96b
累计基线   8589e6526e470d6da056a865f21ffa1532952b8e （卡 A 冻结候选）
提交链     8589e65 → 5feac15(B1) → 24f1362(B2) → 04fc223 → 4cc436a → 603a92e
工作树     /Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/dav-1009-a563fbe02617/workdir/tradingagents-ashare-fork
```

**重要**：前一张复审卡 DAV-1012 的 PASS **只覆盖 `4cc436a`**。本卡对**完整新 SHA `603a92e`** 重新出结论，**不得沿用**旧结论。

### 二、本次增量（`4cc436a..603a92e`）

仅 `tests/test_baostock_fail_fast.py`（+56/-20），**生产代码 0 行改动**（主控已实测）。

**改动意图**：消除三处绝对时间断言（`assert elapsed < 0.1` ×2、`< 0.2` ×1），它们在 4800 用例全量中会因 GC / 调度 / IO 抖动偶发假红。

替代方案：

1. 两个底层 connect 测试 → 改由**行为**证明立即退出（异常类型正确、`__cause__ is None`、socket 关闭、全局为 `None`、**`send` 计数为 0**）
2. 端到端测试 → 改用**防挂死 watchdog**（`subprocess.run(..., timeout=5.0)`），期限内完成后再断言 `attempt == 1`、无 fallback、异常类型正确
3. `elapsed` **仅保留为诊断打印**，不作为通过条件

### 三、本卡必须重点验证

| # | 验证点 |
|---|---|
| ① | **替代断言是否真的更强**——删掉被测修复后，这些测试是否会红？还是变成空真值 |
| ② | **watchdog 是否真能判失败**——`timeout=5.0` 在挂死时确实使测试失败，而非被吞掉 |
| ③ | **`elapsed` 是否彻底退出通过条件**——不得残留任何时间阈值判定 |
| ④ | **增量是否严格限于测试文件**，未夹带生产改动 |
| ⑤ | **全量 SHA 复核**：S1/S2 六条、B2 传播语义、三类路径分流、AST 守卫、加固幂等与 fail-closed（因结论绑定 SHA，需覆盖整个 `8589e65..603a92e`） |
| ⑥ | 禁改项：`registry.py`、`api/main.py`、vendor site-packages、数据库、部署状态 |

### 四、主控已完成的实测（**请独立复跑，勿直接采信**）

```
白名单（8589e65..603a92e）        5 文件，均在授权范围
diff --check                      0 项
生产代码（4cc436a..603a92e）      0 行
assert elapsed                    0 处
tests/test_baostock_fail_fast.py  25 passed in 3.30s
+ v03 + 护栏三件套                 102 passed, 1 deselected in 8.66s
watchdog 负向验证                 sleep(30) + timeout=5.0 → 5.0s 触发 TimeoutExpired ✅
```

### 五、交付

输出 🔴阻断 / 🟡建议 / 🟢认可 三级结论，每条给**文件:行号**与依据。

**本卡不构成合入授权。** 主线 `5a0320f` 保持 HOLD；DAV-998（`33bc23d`）保持 `in_review` 不得合入。
