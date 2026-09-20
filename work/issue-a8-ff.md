# Track A8：线性 FF 主干并回归（018fdef，不准部署）

## 授权

Cursor 已在 DAV-550 **准予合入**；独立审核 DAV-551 ✅。

## 目标 tip（唯一）

`018fdef6f79c82fc8b24e2ac4630774f57cf6338`

分支：`origin/agent/dev2/a8-calibration-v2-sample-honesty`  
父：`97f7c26dcb98bf2545ecfc42b90a9d9015b14f97`

## 动作

对主干 `codex/dav-4-p2a-trunk` **线性 FF only**。  
回归：`tests/test_calibration_service.py` + `tests/test_decision_status.py`。

## 禁止

merge 改写 / 部署 / 开加权 / 编造 probability。

完成后评论主干 tip SHA → `done`。
