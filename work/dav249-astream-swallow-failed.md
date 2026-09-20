# DAV-249 单horizon astream 吞异常导致伪 completed

## 精确基线
`target/codex/dav-4-p2a-trunk@c956e43db0fea1fea88b85103d10218734b2b1c8`

项目主管已授权此 P0（DAV-247 评论 2026-08-20T23:03:37Z）。

## 已核实
报告 `cc3b55a8a35b433fb908f8a78af96f66` status=completed，但 `investment_debate_state.count=0`、`risk_debate_state.count=0`。日志同时出现 `insufficient_quota/403`。`api/main.py:3412-3413`：

```python
except Exception as e:
    _log(f"Error during default streaming: {e}")
```

不 re-raise，finalize 仍当成功。禁止伪造成功。

## 允许修改
- `api/main.py` 该 except 路径
- 对应单测（优先追加现有 API/报告生命周期测试，不要整文件替换）

## 验收
1. 流式路径 403/配额/连接失败：报告 status=failed，error 含精确异常类型与消息，不得 completed。
2. 正常流式完成路径不回归。
3. 不改用户配置、不部署、不合主干、不跑真实 3/3。

独立 worktree + `.venv310`，定向测试 + `TUSHARE_TOKEN='' pytest tests -q` + compileall + diff-check。推送精确 SHA。
