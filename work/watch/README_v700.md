# TA守望 v700 — launchd 定时检测 + 一次性智能体（DAV-1509）

把守望从「会话内后台循环 + 事件退出 + 靠人/会话重新布防」改成「launchd 定时拉起检测 → 有事件时唤起一个**无历史**的一次性智能体处理」。不依赖任何聊天会话常开。

## 组件

| 文件 | 作用 |
|---|---|
| `work/watch/watch_v700.sh` | 单轮事件检测脚本。launchd 每 `StartInterval` 拉起一次；检出事件则写 payload + `multica autopilot trigger`，随后退出。含 `flock` 互斥防止上一轮未结束时叠加。 |
| `work/watch/com.davidliu.ta-watch.plist` | launchd LaunchAgent 模板（`StartInterval=120`）。**模板，不随本任务安装**。 |
| `work/watch/bootstrap_autopilot.sh` | 一次性创建 `run_only` autopilot（= 那个"一次性智能体"），把 autopilot id 落到 `/tmp/watch_v700.autopilot_id`。 |

## 与 v600 的关系

- 事件范围**完全一致**：新卡、成员评论、run 终态、主干 tip 与已签清单不符、prod 底线（DOWN / healthz SHA 偏离 / 真崩溃）、无卡批量突增。
- 沿用防抖：CLI 拉空不算事件不覆盖种子；runid 用 `rid:status:agent` 三元组 diff；0-run 卡跳过 empty 检测。
- **行为修正**：v600 第 6 项（无卡批量突增）里 `PRODDB` 从未定义，是死代码；v700 显式 `PRODDB=$REPO/data/tradingagents.db`。
- v600 退出即停；v700 由 launchd 周期性拉起，事件驱动的语义不变（每个 tick 都可能产出一批事件）。

## 事件 → 一次性智能体 的数据流

```
launchd tick (120s)
   └─ watch_v700.sh one_round()
        └─ 命中若干事件 → 累积写 /tmp/watch_v700_payload/latest.json {consumed:false,events:[...]}
        └─ multica autopilot trigger <id>   ← run_only autopilot，无聊天历史
             └─ agent run：读 latest.json → 逐条处理 → 在对应 issue 交付 → 置 consumed=true
```

- **节流**：同一 payload 未消费只追加；两次 `trigger` 至少隔 `WATCH_DISPATCH_MIN_INTERVAL`（默认 300s）。
- **无 autopilot id** 时只记 alerts、不派发、不炸（`NO-AP` 行）。

## 部署（本任务只交付模板，不安装）

```bash
# 1) 一次性智能体（只做一次）
bash work/watch/bootstrap_autopilot.sh "高级开发·支援" "TA守望·一次性处理"
#    -> 写 /tmp/watch_v700.autopilot_id

# 2) 脚本放 launchd 可读处（launchd 受 TCC 读不了 ~/Documents）
cp work/watch/watch_v700.sh ~/scripts/watch_v700.sh
chmod +x ~/scripts/watch_v700.sh

# 3) 装 LaunchAgent
cp work/watch/com.davidliu.ta-watch.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.davidliu.ta-watch.plist

# 4) 停掉旧的会话内守望（v600 while 循环）与 urgent-notify（按需）
```

卸载：`launchctl bootout gui/$(id -u)/com.davidliu.ta-watch`

## 环境变量覆盖

| 变量 | 默认 | 说明 |
|---|---|---|
| `WATCH_POLL` | 120 | 仅注释用；真实周期由 plist `StartInterval` 决定 |
| `WATCH_AUTOPILOT_ID` | `/tmp/watch_v700.autopilot_id` 内容 | 直填 uuid 可跳过文件 |
| `WATCH_DISPATCH_MIN_INTERVAL` | 300 | 两次 trigger 最小间隔（秒） |
| `WATCH_STATE` / `WATCH_ALERTS` / `WATCH_CARDSTATE` / `WATCH_SEEN_COMMENTS` / `WATCH_HIGHNUM` / `WATCH_PAYLOAD_DIR` / `WATCH_LOCKFILE` / `WATCH_DISPATCH_LOG` | `/tmp/watch_v700.*` | 状态文件路径 |

## 自测（不装 launchd）

```bash
# 语法
bash -n work/watch/watch_v700.sh

# 首轮布防（建种子）+ 单轮检测，不触发派发（无 autopilot id）
WATCH_AUTOPILOT_ID= bash work/watch/watch_v700.sh
# 期望输出 "v700 tick: no event"（或抓到现存真实事件则列出并记 NO-AP）
```

## 已知边界 / 待总控定夺

- autopilot `trigger` 的 run 由 Multica 平台异步执行，非本脚本进程内同步等待；脚本只负责"唤起"。
- autopilot agent 的 runtime/model 由 `bootstrap_autopilot.sh` 指定的 agent 决定。
- v500 里存在但 v600/v700 未迁移的检测（blocked 解锁、schema drift、launchd 新增 label、1460 采集、model-400 计数）沿用 v600 的取舍——**未迁移**，如需恢复请在 v700 里补 check 再更新已签清单。
