## A+B 组合 SHA `603a92e` 唯一一次完整离线全量 —— 结果与判定

**本条为结果裁定存档。** 全量已自然结束，非中止。

---

### 一、运行参数（可复验）

```
SHA        603a92ec85a43ce03ae899cc87a1559fa45f7016（A+B 组合）
工作树      dav-1009 workdir，HEAD 精确等于该 SHA，status 0 项
命令        pytest -q -o faulthandler_timeout=120
           （不带 --deselect / -k / 路径限定，符合完整回归铁律）
环境        无 PYTHONPATH · 无任何代理 · 无 TA_OFFLINE_TESTING
           → 候选自身审计 hook 是唯一网络防线，本次全量同时验证它
日志        /tmp/dav1009_rtfull_offline_603a92e.log（59,547B）
指纹        SHA256 0f656cd6ee7889ca0bb71ec9cb65c6de
耗时        384.51s（6 分 24 秒）· rc=1
```

### 二、结果

```
20 failed, 4851 passed, 1 skipped, 4 deselected, 182 warnings, 3 subtests passed
```

### 三、与基线对照

| 项 | 基线 `5a0320f` | 本次 `603a92e` | 判定 |
|---|---|---|---|
| failed | 20 | 20 | **同一批**（文件级逐一比对，无新增失败）✅ |
| passed | 4801 | 4851 | **+50**（候选修复使更多用例可达 / 原带代理黑洞的差异） |
| deselected | 3 | 4 | +1：卡 A 新增 `network` 标记测试被 `addopts -m 'not network'` 合理过滤 ✅ |

**20 个失败文件与基线完全同一批**（test_cninfo / dav27 / debate_state / game_theory / h1b / provider_date / recalculate / signal / social_data / two_stage），**均为既有失败，无新增**。

### 四、护栏行为（本次全量顺带验证）

```
日志中护栏拦截记录：1 条
  test_dav623_same_query_response_captured_without_duplicate_query
  → AKShare cninfo 试图连 www.cninfo.com.cn:80
  → OfflineTestGuardrailError 拦截（预期行为）✅
```

**真实离线环境下，护栏只拦了 1 次外连尝试，且失败数与基线持平**——证明：
- 护栏**不误伤**正常测试（4851 passed）
- baostock 挂死根因已消除（此前 RT-FULL-OFFLINE 会卡死在 send 上 21 分钟+，本次 6 分 24 秒自然结束）

### 五、判定

```
✅ A+B 组合 SHA 603a92e 通过唯一一次完整离线全量
✅ 20 个失败全部为基线既有失败，无回归
✅ 护栏在真实离线环境正确拦截且不误伤
✅ 挂死根因（connect 被拒 → 吞异常 → 未连接 socket 入全局 → send 卡 45s）已消除
```

### 六、仍受约束

- 本结果**不构成合入授权**
- 主线 `5a0320f` 继续 **HOLD**
- DAV-998（`33bc23d`）保持 `in_review`，**禁止合入**
- DAV-1003 `blocked`，DAV-1007 `8589e65` 冻结候选保持
- 卡 C（lifespan 状态恢复）**尚未建卡**

