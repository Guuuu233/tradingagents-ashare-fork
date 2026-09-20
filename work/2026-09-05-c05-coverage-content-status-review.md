# 独立审核（只读）：C-05 切片 10 event_coverage 报告 content_status

**Path A：** 本卡是唯一审核入口。实现卡评论禁止 @独立代码审核员。

开工前必须已有实现卡给出的完整 40 位候选 SHA，且第一父是当时 `origin/codex/dav-4-p2a-trunk`（期望含 `705b4a684b0e09c417c94b427c1ceb500a906722`）。

只读。禁止改代码、push、FF、部署。核对：主要改 `news_event_evidence.py` + 覆盖测试；无 PDF 解析；未 hashed 仍是事件；摘要禁止「确认无公告」与「已读年报」。

书面 ✅通过 / ⚠️有条件通过 / ❌打回，含路径与行号。
