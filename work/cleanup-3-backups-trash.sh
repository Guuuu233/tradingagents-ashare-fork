#!/bin/bash
# 第 3 项 · 12 份逐字节重复的数据库备份（含 16 个伴生文件）
# 生成 2026-09-20 · 预期回收 4.087 GiB（延后回收）
#
# 受台账 §6 全部四条约束：
#   §6.1 台账已提交 d3bcf14 — 满足
#   §6.2 保留代表清理当时重跑 quick_check — 本脚本内逐份执行
#   §6.3 走可恢复方式、不用 rm — 全程 trash
#   §6.4 只针对备份文件、不碰 worktree — 本脚本仅含备份
# 
# 重要：trash 与源文件同卷 /dev/disk3s5，移入回收站不释放任何空间。
#       4.087 GiB 属延后收益，须手动清空回收站后才实际回收。
#       要立即回收必须先显式修订台账 §6.3 并提交，不得在此绕过。
#
# 逐路径枚举，可执行行无通配符。/private/tmp 下另有 20 个非本项目目录不得触碰，
# 其中 cc-socks / codex-browser-use / com.openai.sky.CUAService 三个目录里有 22 个
# 活 unix socket，按模式删会打断正在运行的工具。

set -euo pipefail
BEFORE=$(df -k / | tail -1 | awk '{print $4}')
command -v trash >/dev/null || { echo "无 trash 命令，中止（§6.3 不允许 rm）" >&2; exit 1; }

# --- tradingagents.db.bak-20260918-001610-deploy-df753841
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-001610-deploy-df753841" ] || { echo "缺失中止: tradingagents.db.bak-20260918-001610-deploy-df753841" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-54077b6?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-001610-deploy-df753841")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-54077b6")" ] || { echo "中止: tradingagents.db.bak-20260918-001610-deploy-df753841 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-001610-deploy-df753841"
# --- tradingagents.db.bak-20260909-upto-fab99d9
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-upto-fab99d9" ] || { echo "缺失中止: tradingagents.db.bak-20260909-upto-fab99d9" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-serveupgrade?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-upto-fab99d9")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-serveupgrade")" ] || { echo "中止: tradingagents.db.bak-20260909-upto-fab99d9 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-upto-fab99d9"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-upto-fab99d9-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-upto-fab99d9-wal"   # 伴生
# --- tradingagents.db.bak-20260911-deploy-4a5206f
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260911-deploy-4a5206f" ] || { echo "缺失中止: tradingagents.db.bak-20260911-deploy-4a5206f" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-serveupgrade?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260911-deploy-4a5206f")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260909-serveupgrade")" ] || { echo "中止: tradingagents.db.bak-20260911-deploy-4a5206f 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260911-deploy-4a5206f"
# --- tradingagents.db.bak-20260914-predeploy-f094d6a
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-f094d6a" ] || { echo "缺失中止: tradingagents.db.bak-20260914-predeploy-f094d6a" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-9d702e7?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-f094d6a")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-9d702e7")" ] || { echo "中止: tradingagents.db.bak-20260914-predeploy-f094d6a 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-f094d6a"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-f094d6a-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-f094d6a-wal"   # 伴生
# --- tradingagents.db.bak-20260918-pre998replay
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-pre998replay" ] || { echo "缺失中止: tradingagents.db.bak-20260918-pre998replay" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-9d702e7?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-pre998replay")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-9d702e7")" ] || { echo "中止: tradingagents.db.bak-20260918-pre998replay 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-pre998replay"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-pre998replay-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260918-pre998replay-wal"   # 伴生
# --- tradingagents.db.bak-20260919-172726-deploy-ac8955e
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-172726-deploy-ac8955e" ] || { echo "缺失中止: tradingagents.db.bak-20260919-172726-deploy-ac8955e" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-163716-deploy-4a48ec0?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-172726-deploy-ac8955e")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-163716-deploy-4a48ec0")" ] || { echo "中止: tradingagents.db.bak-20260919-172726-deploy-ac8955e 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-172726-deploy-ac8955e"
# --- tradingagents.db.bak-20260919-182312-deploy-7a98819
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-182312-deploy-7a98819" ] || { echo "缺失中止: tradingagents.db.bak-20260919-182312-deploy-7a98819" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-163716-deploy-4a48ec0?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-182312-deploy-7a98819")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-163716-deploy-4a48ec0")" ] || { echo "中止: tradingagents.db.bak-20260919-182312-deploy-7a98819 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260919-182312-deploy-7a98819"
# --- tradingagents.db.bak-20260914-deploy-4f1a1aa3
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-deploy-4f1a1aa3" ] || { echo "缺失中止: tradingagents.db.bak-20260914-deploy-4f1a1aa3" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-deploy-4f1a1aa3")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8")" ] || { echo "中止: tradingagents.db.bak-20260914-deploy-4f1a1aa3 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-deploy-4f1a1aa3"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-deploy-4f1a1aa3-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-deploy-4f1a1aa3-wal"   # 伴生
# --- tradingagents.db.bak-20260914-predeploy-0263496
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-0263496" ] || { echo "缺失中止: tradingagents.db.bak-20260914-predeploy-0263496" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-0263496")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8")" ] || { echo "中止: tradingagents.db.bak-20260914-predeploy-0263496 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-0263496"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-0263496-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-0263496-wal"   # 伴生
# --- tradingagents.db.bak-20260914-predeploy-63d5648
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-63d5648" ] || { echo "缺失中止: tradingagents.db.bak-20260914-predeploy-63d5648" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-63d5648")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8")" ] || { echo "中止: tradingagents.db.bak-20260914-predeploy-63d5648 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-63d5648"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-63d5648-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-63d5648-wal"   # 伴生
# --- tradingagents.db.bak-20260914-predeploy-6cc4e1
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-6cc4e1" ] || { echo "缺失中止: tradingagents.db.bak-20260914-predeploy-6cc4e1" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-6cc4e1")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8")" ] || { echo "中止: tradingagents.db.bak-20260914-predeploy-6cc4e1 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-6cc4e1"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-6cc4e1-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-6cc4e1-wal"   # 伴生
# --- tradingagents.db.bak-20260914-predeploy-79757a6
[ -f "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-79757a6" ] || { echo "缺失中止: tradingagents.db.bak-20260914-predeploy-79757a6" >&2; exit 1; }
[ "$(sqlite3 "file:/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8?immutable=1" "pragma quick_check;")" = "ok" ] || { echo "中止: 代表 quick_check 未通过" >&2; exit 1; }
[ "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-79757a6")" = "$(md5 -q "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260913-deploy-bdb95f8")" ] || { echo "中止: tradingagents.db.bak-20260914-predeploy-79757a6 与代表不再逐字节一致" >&2; exit 1; }
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-79757a6"
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-79757a6-shm"   # 伴生
trash "/Users/davidliu/Documents/TradingAgents-AShare/work/tradingagents.db.bak-20260914-predeploy-79757a6-wal"   # 伴生

AFTER=$(df -k / | tail -1 | awk '{print $4}')
echo "备份(trash) 完成，根卷可用空间变化 $(( (AFTER - BEFORE) / 1024 )) MiB"
# APFS 写时复制会让实际释放小于 du 汇总值，以 df 前后差为准
echo "提醒：以上 4.087 GiB 仍占用磁盘，需手动清空回收站才实际回收"
