#!/bin/bash
# 第 1 项 · .git 中 13 个中断残留的 tmp_pack
# 生成 2026-09-20 · 预期回收 436.25 MiB
#
# 不受台账 §6 约束：非备份、非 worktree，是 git 自身标记的 garbage。
# 已验证：13 个均无对应 .idx；文件头 50 41 43 4b 为半成品包；
#         git fsck --connectivity-only 的 missing/broken 计数为 0。
# 注意：git gc 也会删掉它们，两者是替代关系而非叠加。
#
# 逐路径枚举，可执行行无通配符。/private/tmp 下另有 20 个非本项目目录不得触碰，
# 其中 cc-socks / codex-browser-use / com.openai.sky.CUAService 三个目录里有 22 个
# 活 unix socket，按模式删会打断正在运行的工具。

set -euo pipefail
BEFORE=$(df -k / | tail -1 | awk '{print $4}')
# 守卫：目标必须存在且确实无 idx（防止期间被 git 重新索引）
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_4eXmGM" ] || { echo "缺失中止: tmp_pack_4eXmGM" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_4eXmGM.idx" ] || { echo "已被索引，中止: tmp_pack_4eXmGM" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_4eXmGM"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_CeyEt8" ] || { echo "缺失中止: tmp_pack_CeyEt8" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_CeyEt8.idx" ] || { echo "已被索引，中止: tmp_pack_CeyEt8" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_CeyEt8"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_DLMjAs" ] || { echo "缺失中止: tmp_pack_DLMjAs" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_DLMjAs.idx" ] || { echo "已被索引，中止: tmp_pack_DLMjAs" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_DLMjAs"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_IS9auh" ] || { echo "缺失中止: tmp_pack_IS9auh" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_IS9auh.idx" ] || { echo "已被索引，中止: tmp_pack_IS9auh" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_IS9auh"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_SEqvmd" ] || { echo "缺失中止: tmp_pack_SEqvmd" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_SEqvmd.idx" ] || { echo "已被索引，中止: tmp_pack_SEqvmd" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_SEqvmd"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_VvwKqY" ] || { echo "缺失中止: tmp_pack_VvwKqY" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_VvwKqY.idx" ] || { echo "已被索引，中止: tmp_pack_VvwKqY" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_VvwKqY"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_cFE3uS" ] || { echo "缺失中止: tmp_pack_cFE3uS" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_cFE3uS.idx" ] || { echo "已被索引，中止: tmp_pack_cFE3uS" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_cFE3uS"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_fDC0o6" ] || { echo "缺失中止: tmp_pack_fDC0o6" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_fDC0o6.idx" ] || { echo "已被索引，中止: tmp_pack_fDC0o6" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_fDC0o6"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_gkonBg" ] || { echo "缺失中止: tmp_pack_gkonBg" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_gkonBg.idx" ] || { echo "已被索引，中止: tmp_pack_gkonBg" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_gkonBg"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_jkvYDe" ] || { echo "缺失中止: tmp_pack_jkvYDe" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_jkvYDe.idx" ] || { echo "已被索引，中止: tmp_pack_jkvYDe" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_jkvYDe"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_l4vWtX" ] || { echo "缺失中止: tmp_pack_l4vWtX" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_l4vWtX.idx" ] || { echo "已被索引，中止: tmp_pack_l4vWtX" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_l4vWtX"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_lKk0ll" ] || { echo "缺失中止: tmp_pack_lKk0ll" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_lKk0ll.idx" ] || { echo "已被索引，中止: tmp_pack_lKk0ll" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_lKk0ll"
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_xGhB9Z" ] || { echo "缺失中止: tmp_pack_xGhB9Z" >&2; exit 1; }
[ ! -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_xGhB9Z.idx" ] || { echo "已被索引，中止: tmp_pack_xGhB9Z" >&2; exit 1; }
rm -f "/Users/davidliu/Documents/TradingAgents-AShare/.git/objects/pack/tmp_pack_xGhB9Z"

AFTER=$(df -k / | tail -1 | awk '{print $4}')
echo "tmp_pack 完成，根卷可用空间变化 $(( (AFTER - BEFORE) / 1024 )) MiB"
# APFS 写时复制会让实际释放小于 du 汇总值，以 df 前后差为准
