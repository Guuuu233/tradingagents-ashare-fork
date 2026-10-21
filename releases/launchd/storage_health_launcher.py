#!/usr/bin/env python3
"""DAV-2310 — storage_health launchd 启动器（由已获 TCC 授权的 python 当 responsible process）。

背景
----
DAV-2237 首跑 ``exit=2``（fail-closed 判红）：launchd 下 ``/bin/zsh`` 没有
``~/Documents`` 的 TCC 授权，而巡检对象（活库、发布备份 ``backups/``、发布期
``.pre-slim`` 副本）全在 ``~/Documents`` 之下，于是 ``du`` / ``find`` 一律
``Operation not permitted`` → 4 个测量项不可测 → 热备行判红。

同一台机器上 ``com.tradingagents.ashare`` 以 ``~/ta-venv/py310/bin/python`` 作
``ProgramArguments[0]`` 则能持有 ``~/Documents/…/tradingagents.db``：macOS TCC
按 **responsible process** 判定授权，**由已授权的 python 启动的子进程继承该授权**。

DAV-2310 临时探针（launchd 上下文，4 组对照；见 STORAGE_HEALTH.md §TCC 探针）实测：

======  ======================================================  ==============
 组别   形态                                                     结果
======  ======================================================  ==============
 A      ``/bin/zsh`` 直接当 responsible process（对照组）          **FAIL**（复现 D2）
 B      python **spawn** ``/bin/zsh``（python 仍是父进程）           PASS
 C      python **exec**  ``/bin/zsh``（映像被替换，同一 PID）         PASS
 D      同 C，但 ``WorkingDirectory`` 在 ``~/Documents`` 下          PASS
======  ======================================================  ==============

本启动器取 **C 形态（exec）**：进程映像被 zsh 取代，脚本的退出码**天然**就是本进程的
退出码（无需中转），且与已实测通过的探针形态逐字一致（``os.execv('/bin/zsh', ['/bin/zsh',
<脚本>, *参数])``）。

职责（刻意最小 —— 卡面三条：路径解析 / 参数透传 / 退出码透传）
------------------------------------------------------------
1. **路径解析**
   · 巡检脚本：``STORAGE_HEALTH_SCRIPT`` 环境变量；未设时取**与本文件同目录**的
     ``storage_health.sh``（由 ``__file__`` 推导，**不写死绝对路径** —— DAV-2292
     「路径写死 → 改 env/脚本目录推导」的同款教训）。
   · 报告目录：``STORAGE_HEALTH_LOG_DIR`` 环境变量（plist 的
     ``EnvironmentVariables`` 注入）。**不自动创建**：目录缺失即拒绝启动，
     由安装步骤负责建目录，避免启动器在 launchd 下留下无主副作用。
2. **参数透传**：``argv[1:]`` 逐字转交脚本（不解析、不重排、不补默认值）。
3. **退出码透传**：脚本退出码即本进程退出码（exec 形态天然如此）。

输出落盘（替代旧 plist 的 shell 重定向，语义逐字一致）
--------------------------------------------------------
* 当日报告 ``<log-dir>/storage-health-<YYYY-MM-DD>.md``：**覆盖写**（旧行为是
  ``>``，当日复跑覆盖当日文件）；
* 脚本 stderr ``<log-dir>/storage-health-stderr.log``：**追加写**（旧行为是 ``2>>``）；
* 启动器**自身**的配置错误走 launchd 的 ``StandardErrorPath``（不与脚本 stderr 混流）。

只读纪律
--------
本启动器只**打开两个输出文件**（当日报告 / stderr 日志）并 exec 巡检脚本；
**不删、不改、不复制任何数据文件**，**不读数据库内容**，**不碰生产库**。

退出码
------
===  ===========================================================
 0   巡检脚本退出 0
 N   巡检脚本退出 N（**原样透传**；脚本自身的 0 / 2 语义不变）
 64  启动器自身配置或环境不满足，**拒绝启动**（fail-closed）
     · 未设 ``STORAGE_HEALTH_LOG_DIR``
     · 报告目录不存在或不是目录
     · 巡检脚本不存在
     · 报告 / stderr 日志打不开
     · ``exec`` 失败
===  ===========================================================

（64 是**本启动器**的「拒绝启动」，与脚本自身的 0 / 2 不撞码；它**不是**巡检结论。）
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

#: 启动器自身的失败退出码（与脚本的 0 / 2 不撞码；64 = 拒绝启动）。
EXIT_CONFIG = 64

#: 巡检脚本的默认文件名（与本启动器同目录）。
DEFAULT_SCRIPT_NAME = "storage_health.sh"

#: 环境变量契约（plist 的 EnvironmentVariables 必须注入 LOG_DIR）。
SCRIPT_ENV = "STORAGE_HEALTH_SCRIPT"
LOG_DIR_ENV = "STORAGE_HEALTH_LOG_DIR"

#: 报告 / 日志文件名约定（与旧 plist 的 shell 重定向逐字一致）。
REPORT_PREFIX = "storage-health-"
REPORT_SUFFIX = ".md"
STDERR_NAME = "storage-health-stderr.log"

#: 巡检脚本是 zsh 脚本（shebang ``#!/bin/zsh``）。此处显式给出解释器：
#: 不依赖脚本自身的可执行位，也不依赖 PATH 里有 zsh —— launchd 的默认 PATH 极简。
ZSH = "/bin/zsh"


def _fail(message: str) -> "None":
    """拒绝启动（fail-closed）。退出码 64。"""
    sys.stderr.write(f"storage_health_launcher: {message}\n")
    sys.stderr.flush()
    raise SystemExit(EXIT_CONFIG)


def resolve_script() -> Path:
    """解析巡检脚本路径。

    优先 ``STORAGE_HEALTH_SCRIPT``；未设时取**与本文件同目录**的
    ``storage_health.sh``（由 ``__file__`` 推导，绝不写死绝对路径）。
    """
    raw = os.environ.get(SCRIPT_ENV, "").strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        path = Path(__file__).resolve().parent / DEFAULT_SCRIPT_NAME
    if not path.is_file():
        _fail(f"巡检脚本不存在：{path}")
    return path


def resolve_log_dir() -> Path:
    """解析报告目录（plist 注入 ``STORAGE_HEALTH_LOG_DIR``）。

    **不自动创建**目录：目录缺失即拒绝启动（fail-closed），由安装步骤负责建目录。
    """
    raw = os.environ.get(LOG_DIR_ENV, "").strip()
    if not raw:
        _fail(
            f"未设置 {LOG_DIR_ENV}（plist 的 EnvironmentVariables 必须注入报告目录，"
            "否则当日报告无处落盘）"
        )
    path = Path(raw).expanduser()
    if not path.is_dir():
        _fail(f"报告目录不存在或不是目录：{path}")
    return path


def main(argv: list[str]) -> int:
    # 原 stderr 的副本：exec 失败时要把原因报给 launchd 的 StandardErrorPath
    #（此刻 fd 2 已被重定向到脚本 stderr 日志）。
    orig_stderr = os.dup(2)

    script = resolve_script()
    log_dir = resolve_log_dir()
    # 参数原样透传：argv[1:] 逐字转交，不解析、不重排、不补默认值。
    args = list(argv[1:])

    report = log_dir / f"{REPORT_PREFIX}{time.strftime('%Y-%m-%d')}{REPORT_SUFFIX}"
    stderr_log = log_dir / STDERR_NAME

    try:
        # 当日报告：覆盖写（与旧 shell 重定向 `>` 一致）。
        out_fd = os.open(str(report), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
    except OSError as exc:
        _fail(f"无法打开当日报告 {report}：{exc}")
    try:
        # 脚本 stderr：追加写（与旧 shell 重定向 `2>>` 一致）。
        err_fd = os.open(str(stderr_log), os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    except OSError as exc:
        _fail(f"无法打开 stderr 日志 {stderr_log}：{exc}")

    sys.stdout.flush()
    sys.stderr.flush()
    # dup2 后的 fd 1 / fd 2 不带 CLOEXEC，跨 exec 保持打开（PEP 446）。
    os.dup2(out_fd, 1)
    os.dup2(err_fd, 2)
    if out_fd > 2:
        os.close(out_fd)
    if err_fd > 2:
        os.close(err_fd)

    # exec：进程映像被 zsh 取代 → 脚本退出码即本进程退出码（天然透传）。
    try:
        os.execv(ZSH, [ZSH, str(script), *args])
    except OSError as exc:
        os.write(
            orig_stderr,
            f"storage_health_launcher: exec {ZSH} 失败：{exc}\n".encode("utf-8"),
        )
        raise SystemExit(EXIT_CONFIG) from exc
    # 不可达：execv 成功即不返回。
    raise SystemExit(EXIT_CONFIG)


if __name__ == "__main__":
    sys.exit(main(sys.argv))
