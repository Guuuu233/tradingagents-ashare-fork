# 独立审核（只读）：C-05 切片 9 collector 调用 qualify_cninfo_content

**Path A：** 本卡是唯一审核入口。实现卡评论禁止 @独立代码审核员。

开工前必须已有实现卡给出的完整 40 位候选 SHA，且该 SHA 第一父是当时 `origin/codex/dav-4-p2a-trunk`（期望含 `9a2878c8e94a0bf5ccab3ba222067d9f47d89138`）。

只读。禁止改代码、push、FF、部署。核对：只改 collector + 本卡测试；调用的是已有 `qualify_cninfo_content`；无 PDF 解析；无 `anns_d`；失败不是确认无公告；测试 mock 网络。

书面 ✅通过 / ⚠️有条件通过 / ❌打回，含路径与行号。
