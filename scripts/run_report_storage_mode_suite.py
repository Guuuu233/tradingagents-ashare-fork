#!/usr/bin/env python
"""DAV-1922: 子进程测试入口——按 REPORT_STORAGE_MODE 分组运行模式专用用例。

背景
----
模式专用用例把 ``REPORT_STORAGE_MODE`` 固定在 ``pytestmark.skipif`` 上，因为该
模式在 ``api.database`` import 时就绑定到 ORM 列，进程内不可切换。因此它们只能
在**子进程**里以对应模式运行。之前这靠人肉在两个 shell 里分别敲命令，且环境是
继承来的（本机 Clash 代理 7897 会让出网用例挂死、宿主 PYTHONPATH 会污染 venv），
所以没有一条可复现的「0 failed、0 skipped」入口。

本脚本提供该入口：
  1. 按模式划分用例文件（互斥，保证某模式下不存在「本该 skip」的用例）；
  2. 为子进程封闭环境：剥离 ``PYTHONPATH``、隔离临时 ``DATABASE_URL``、显式
     ``REPORT_STORAGE_MODE``、丢弃 ``RT_NETWORK_ENABLED``、可选代理环境变体；
  3. 断言 **0 failed 且 0 skipped**（xfailed/xpassed/deselected/error 同样拒绝）。

用法
----
    env -u PYTHONPATH python scripts/run_report_storage_mode_suite.py
    ... --env-a --env-b            # 两种代理环境各跑一次（默认只跑 B）
    ... --mode compressed
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# 每个模式只跑该模式「专用」的用例文件；``test_dav1771_b6b_compressed_storage.py``
# 属明文模式，不进任何组，否则在其他模式下会被 skip（违反 0 skipped）。
MODE_FILES: dict[str, tuple[str, ...]] = {
    "compressed": (
        "tests/test_dav1771_b6b_compressed_mode.py",
        "tests/test_dav1885_p4_switchback.py",
    ),
    "compressed_only": (
        "tests/test_dav1885_p4_compressed_only.py",
        "tests/test_dav1893_p4_backfill.py",
    ),
}

# 任何一项非零都算失败：skip 说明分组或模式绑定被破坏，deselected 说明
# addopts/-m 过滤掉了用例，xfail 掩盖了真实断言。
FORBIDDEN = ("failed", "error", "errors", "skipped", "xfailed", "xpassed", "deselected")
SUMMARY_RE = re.compile(r"(\d+)\s+(passed|failed|error|errors|skipped|xfailed|xpassed|deselected)\b")

# 卡面指定的存量死锁用例（非本卡引入，见 DAV-979）。
DESELECT = (
    "tests/test_fund_flow_scale_consumption.py::TestFundFlowScalePersistenceAndReadback"
    "::test_single_horizon_report_persists_and_reads_all_scale_fields"
)

PROXY_VARS = ("http_proxy", "https_proxy", "all_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY")


def base_env() -> dict:
    """子进程封闭环境：与 DAV-1905 ``repo_script_env()`` 同一套口径。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)  # 宿主 PYTHONPATH 会注入异构 site-packages
    env.pop("RT_NETWORK_ENABLED", None)  # 断网守卫 fail-closed，子进程不得被打开
    for var in PROXY_VARS:
        env.pop(var, None)
    env["TA_ALLOW_DEFAULT_SECRET"] = "1"
    return env


def build_env(mode: str, *, env_variant: str = "B", db_dir: Path | None = None) -> dict:
    env = base_env()
    env["REPORT_STORAGE_MODE"] = mode
    db_dir = db_dir or Path(tempfile.mkdtemp(prefix=f"dav1922-{mode}-"))
    db_dir.mkdir(parents=True, exist_ok=True)
    env["DATABASE_URL"] = f"sqlite:///{db_dir}/mode.db"  # 绝不写 data/tradingagents.db
    if env_variant == "A":
        # 代理指向不可用端口：出网请求快速失败，而不是走本机 Clash 7897 挂死。
        for var in PROXY_VARS:
            env[var] = "http://127.0.0.1:9"
        env["PYTHONPATH"] = "/nonexistent"  # 污染 PYTHONPATH，子进程必须自行剥离
    return env


def run_mode(mode: str, *, env_variant: str = "B", timeout: int = 1800, python: str | None = None):
    files = MODE_FILES[mode]
    missing = [f for f in files if not (REPO_ROOT / f).is_file()]
    if missing:
        raise SystemExit(f"mode {mode}: 用例文件缺失 {missing}")
    env = build_env(mode, env_variant=env_variant)
    cmd = [
        python or sys.executable, "-m", "pytest",
        "-q", "-p", "no:randomly", "--no-header",
        "-o", "addopts=",
        *files,
        "--deselect", DESELECT,
    ]
    proc = subprocess.run(
        cmd, cwd=REPO_ROOT, env=env, capture_output=True, text=True, timeout=timeout,
    )
    counts = {kind: int(n) for n, kind in SUMMARY_RE.findall(proc.stdout or "")}
    counts = {k: v for k, v in counts.items() if k != "passed"} or counts
    bad = {k: counts.get(k, 0) for k in FORBIDDEN if counts.get(k, 0)}
    return {
        "mode": mode,
        "env_variant": env_variant,
        "returncode": proc.returncode,
        "counts": {k: counts.get(k, 0) for k in ("passed", *FORBIDDEN) if k in counts or k == "passed"},
        "forbidden": bad,
        "ok": proc.returncode == 0 and not bad,
        "command": " ".join(cmd),
        "env": {k: env.get(k) for k in ("REPORT_STORAGE_MODE", "DATABASE_URL", "PYTHONPATH", "http_proxy")},
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--mode", choices=sorted(MODE_FILES), action="append")
    ap.add_argument("--env-a", action="store_true", help="代理污染 + PYTHONPATH 污染")
    ap.add_argument("--env-b", action="store_true", help="清代理清 PYTHONPATH（默认）")
    args = ap.parse_args()
    modes = args.mode or sorted(MODE_FILES)
    variants = [v for v, on in (("A", args.env_a), ("B", args.env_b or not args.env_a)) if on]

    results = [run_mode(m, env_variant=v) for m in modes for v in variants]
    for r in results:
        print(f"=== mode={r['mode']} env={r['env_variant']} rc={r['returncode']} "
              f"counts={r['counts']} forbidden={r['forbidden']}")
        print(r["stdout"].strip().splitlines()[-1] if r["stdout"].strip() else "(no output)")
    print(json.dumps([{k: r[k] for k in ("mode", "env_variant", "returncode", "counts", "forbidden", "ok")}
                      for r in results], ensure_ascii=False))
    return 0 if all(r["ok"] for r in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())