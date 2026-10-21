"""DAV-2310 — storage_health launchd 启动器 + plist 模板的自动化门禁。

背景
----
DAV-2237 首跑 ``exit=2``：launchd 下 ``/bin/zsh`` 没有 ``~/Documents`` 的 TCC
授权，``du`` / ``find`` 一律 ``Operation not permitted`` → 4 个测量项不可测 →
热备行 fail-closed 判红。DAV-2310 的修法是**由已获授权的 python 当 responsible
process** 启动巡检脚本（临时探针 4 组对照实测：B/C/D 组 PASS，A 组复现 FAIL）。

本文件覆盖卡面要求的四块：

1. **路径解析** —— ``STORAGE_HEALTH_SCRIPT`` 优先；未设时取「与启动器同目录的
   ``storage_health.sh``」（脚本目录推导，不写死绝对路径）；脚本不存在 → 拒绝启动。
2. **参数透传** —— ``argv[1:]`` 逐字转交脚本，不解析、不重排、不补默认值。
3. **退出码透传** —— 脚本退出码即启动器退出码（0 / 2 / 其它 N 均原样传出）。
4. **plist 模板** —— ``ProgramArguments`` 指向启动器；占位符齐全；无 KeepAlive；
   ``plutil -lint`` 通过；占位符替换后能被 ``plistlib`` 解析。

纪律
----
* 全程 **tmp 目录沙箱**：不碰生产库、不碰真实磁盘统计、不安装 launchd、
  不 bootstrap、**不读数据库内容**、不写 ``~/Documents``。
* 巡检脚本在测试里是**桩**（记录 argv 并按需返回指定退出码），**不执行真脚本**，
  因此不产生任何真实 du/find/df 调用。
* 真 ``storage_health.sh`` **一字不改**（sha256 由 DAV-2237 总控 pin 为
  ``fc694aeac4e85ac600053dd8a9522f3ef8776a2e3c0fec752f6f7e321fd60089``）——
  本门禁断言该文件的存在性与哈希，确保本卡没顺手改它。

运行
----
本文件按仓库惯例放在 ``releases/launchd/tests/``（与同目录两个 bash 门禁并列），
**不在 pytest 的默认 ``testpaths``（``tests/``）内**，故需显式指定：

.. code-block:: bash

    env -u PYTHONPATH .venv310/bin/python -m pytest \\
        releases/launchd/tests/test_storage_health_launcher.py -q -p no:randomly
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
LAUNCHD_DIR = ROOT / "releases" / "launchd"
LAUNCHER = LAUNCHD_DIR / "storage_health_launcher.py"
PLIST = LAUNCHD_DIR / "com.tradingagents.storage-health.plist"
REAL_SCRIPT = LAUNCHD_DIR / "storage_health.sh"

#: DAV-2237 总控 pin 的 storage_health.sh sha256（DAV-2310 卡面要求提交前回读核对）。
PINNED_SCRIPT_SHA256 = "fc694aeac4e85ac600053dd8a9522f3ef8776a2e3c0fec752f6f7e321fd60089"

#: 巡检脚本参数由启动器原样透传；这里用带空格/引号形态的参数钉住「逐字转交」。
ARG_SAMPLE = ["auto"]

#: plist 模板里的占位符（DAV-2310 新增 {{PYTHON_BIN}}，共 3 个）。
EXPECTED_PLACEHOLDERS = {"{{PYTHON_BIN}}", "{{REPO_DIR}}", "{{LOG_DIR}}"}

PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


def _python() -> str:
    """用**当前**解释器跑启动器（与 launchd 上的解释器无关：启动器只用到标准库）。"""
    return sys.executable


# --------------------------------------------------------------------------- #
# 桩巡检脚本：记录 argv，按环境变量给退出码，不做任何真实统计                    #
# --------------------------------------------------------------------------- #

_STUB_SCRIPT = """#!/bin/zsh
# DAV-2310 测试桩：只记录 $0 与 argv 到 STUB_RECORD，然后按 STUB_RC 退出。
# 不执行任何 du / find / df，不读写任何数据文件。
{
  printf 'ARGV_START\\n'
  printf 'SCRIPT_PATH:%s\\n' "$0"
  for a in "$@"; do printf 'ARGV:%s\\n' "$a"; done
  printf 'ARGV_END\\n'
} >> "${STUB_RECORD}"
# stdout / stderr 必须在重定向组**之外**，否则会被一起写进 STUB_RECORD。
printf 'STDERR_FROM_SCRIPT\\n' >&2
printf 'STDOUT_FROM_SCRIPT\\n'
exit "${STUB_RC:-0}"
"""


def _make_stub_script(path: Path) -> Path:
    path.write_text(_STUB_SCRIPT, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


def _run_launcher(
    tmp_path: Path,
    *,
    args: list[str] | None = None,
    script: Path | None = None,
    log_dir: Path | None = None,
    env_extra: dict[str, str] | None = None,
    launcher: Path | None = None,
) -> subprocess.CompletedProcess:
    """在沙箱里跑一次启动器，返回 CompletedProcess。"""
    record = tmp_path / "stub-record.txt"
    env = {
        "PATH": "/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "STUB_RECORD": str(record),
        "STUB_RC": "0",
    }
    if script is not None:
        env["STORAGE_HEALTH_SCRIPT"] = str(script)
    if log_dir is not None:
        env["STORAGE_HEALTH_LOG_DIR"] = str(log_dir)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [_python(), str(launcher or LAUNCHER), *(args or [])],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )


# --------------------------------------------------------------------------- #
# 1. 静态门：文件存在、语法、哈希                                                   #
# --------------------------------------------------------------------------- #


def test_launcher_exists_and_compiles():
    assert LAUNCHER.is_file(), f"缺启动器：{LAUNCHER}"
    r = subprocess.run(
        [sys.executable, "-m", "py_compile", str(LAUNCHER)],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr


def test_storage_health_script_is_unchanged():
    """DAV-2310 卡面第 4 条：storage_health.sh 本身不得改动（sha256 仍应是 pin 值）。"""
    assert REAL_SCRIPT.is_file(), f"缺巡检脚本：{REAL_SCRIPT}"
    digest = hashlib.sha256(REAL_SCRIPT.read_bytes()).hexdigest()
    assert digest == PINNED_SCRIPT_SHA256, (
        f"storage_health.sh 哈希变了：{digest} != {PINNED_SCRIPT_SHA256}"
        "（DAV-2310 卡面第 4 条：脚本本身不得改动）"
    )


def test_launcher_has_no_delete_or_write_side_effects():
    """启动器只应打开两个输出文件；不得出现删除/拷贝类动作。"""
    text = LAUNCHER.read_text(encoding="utf-8")
    # 去掉注释行后再查（注释里可以出现这些词做说明）。
    code = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith("#")
    )
    for forbidden in ("shutil", "rmtree", "unlink", "remove(", "copyfile", "copy2"):
        assert forbidden not in code, f"启动器不得含 {forbidden}（只读纪律）"


# --------------------------------------------------------------------------- #
# 2. 路径解析                                                                    #
# --------------------------------------------------------------------------- #


def test_script_path_resolution_prefers_env(tmp_path):
    """STORAGE_HEALTH_SCRIPT 优先于「同目录推导」。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    explicit = _make_stub_script(tmp_path / "explicit.sh")
    record = tmp_path / "record.txt"
    r = _run_launcher(
        tmp_path,
        script=explicit,
        log_dir=log_dir,
        env_extra={"STUB_RECORD": str(record)},
    )
    assert r.returncode == 0, r.stderr
    body = record.read_text(encoding="utf-8")
    # 脚本路径是 zsh 的 $0（不是 $@）——参数才是 $@。
    assert f"SCRIPT_PATH:{explicit}" in body, (
        "启动器应 exec STORAGE_HEALTH_SCRIPT 指定的脚本"
    )


def test_script_path_resolution_falls_back_to_sibling_dir(tmp_path):
    """未设 STORAGE_HEALTH_SCRIPT 时，取**与启动器同目录**的 storage_health.sh。

    这是 DAV-2292「路径写死 → 改脚本目录推导」的同款要求：启动器被复制到别处时，
    仍应找到同目录的脚本，而不是指向某个写死的绝对路径。
    """
    # 把启动器复制到一个只有它 + 桩脚本的目录（模拟「装到镜像目录」）。
    sandbox = tmp_path / "mirror" / "releases" / "launchd"
    sandbox.mkdir(parents=True)
    launcher_copy = sandbox / "storage_health_launcher.py"
    shutil.copy2(LAUNCHER, launcher_copy)
    stub = _make_stub_script(sandbox / "storage_health.sh")
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    record = tmp_path / "record.txt"

    env = {
        "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
        "HOME": str(tmp_path),
        "STUB_RECORD": str(record),
        "STUB_RC": "0",
        "STORAGE_HEALTH_LOG_DIR": str(log_dir),
        # 故意**不设** STORAGE_HEALTH_SCRIPT
    }
    r = subprocess.run(
        [_python(), str(launcher_copy), "auto"],
        capture_output=True,
        text=True,
        env=env,
        cwd=str(tmp_path),
        timeout=120,
    )
    assert r.returncode == 0, r.stderr
    body = record.read_text(encoding="utf-8")
    assert f"SCRIPT_PATH:{stub}" in body, (
        "应回退到「与启动器同目录的 storage_health.sh」"
    )


def test_missing_script_is_fail_closed(tmp_path):
    """脚本不存在 → 拒绝启动，退出码 64（非 0/2），且不产生报告文件。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    r = _run_launcher(
        tmp_path,
        script=tmp_path / "nope.sh",
        log_dir=log_dir,
    )
    assert r.returncode == 64, f"应为 64，实际 {r.returncode}；stderr={r.stderr}"
    assert "storage_health_launcher" in r.stderr
    assert not list(log_dir.glob("storage-health-*.md")), "拒绝启动时不得留下报告文件"


def test_missing_log_dir_env_is_fail_closed(tmp_path):
    """未设 STORAGE_HEALTH_LOG_DIR → 拒绝启动（退出码 64），不静默跑。"""
    script = _make_stub_script(tmp_path / "s.sh")
    r = _run_launcher(tmp_path, script=script, log_dir=None)
    assert r.returncode == 64, f"应为 64，实际 {r.returncode}"
    assert "STORAGE_HEALTH_LOG_DIR" in r.stderr


def test_nonexistent_log_dir_is_fail_closed(tmp_path):
    """报告目录不存在 → 拒绝启动（退出码 64）；启动器**不自动创建**目录。"""
    script = _make_stub_script(tmp_path / "s.sh")
    missing = tmp_path / "no-such-dir"
    r = _run_launcher(tmp_path, script=script, log_dir=missing)
    assert r.returncode == 64, f"应为 64，实际 {r.returncode}"
    assert not missing.exists(), "启动器不得自动创建报告目录（避免无主副作用）"


# --------------------------------------------------------------------------- #
# 3. 参数透传                                                                    #
# --------------------------------------------------------------------------- #


def test_arguments_are_passed_through_verbatim(tmp_path):
    """argv[1:] 逐字转交脚本：含空格、引号、连字符一律不改写。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    script = _make_stub_script(tmp_path / "s.sh")
    record = tmp_path / "record.txt"
    args = ["auto", "a b", "--flag=x y", "'quoted'", "中文参数"]
    r = _run_launcher(
        tmp_path,
        args=args,
        script=script,
        log_dir=log_dir,
        env_extra={"STUB_RECORD": str(record)},
    )
    assert r.returncode == 0, r.stderr
    body = record.read_text(encoding="utf-8")
    for a in args:
        assert f"ARGV:{a}" in body, f"参数被改写或丢失：{a!r}"


def test_no_arguments_are_injected(tmp_path):
    """启动器**不补默认值**：不给参数时，脚本收到的参数个数为 0。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    script = _make_stub_script(tmp_path / "s.sh")
    record = tmp_path / "record.txt"
    r = _run_launcher(
        tmp_path,
        args=[],
        script=script,
        log_dir=log_dir,
        env_extra={"STUB_RECORD": str(record)},
    )
    assert r.returncode == 0, r.stderr
    body = record.read_text(encoding="utf-8")
    # 0 个参数：ARGV_START 与 ARGV_END 之间只有 SCRIPT_PATH 一行（那是 $0，不是参数）。
    between = body.split("ARGV_START\n", 1)[1].split("ARGV_END\n", 1)[0]
    arg_lines = [ln for ln in between.splitlines() if ln.startswith("ARGV:")]
    assert arg_lines == [], "启动器不得自行补默认参数"


# --------------------------------------------------------------------------- #
# 4. 退出码透传                                                                  #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("script_rc", [0, 2, 3, 42])
def test_exit_code_is_passed_through(tmp_path, script_rc):
    """脚本退出码原样传出（0 / 2 / 其它 N 都必须一致）。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    script = _make_stub_script(tmp_path / "s.sh")
    r = _run_launcher(
        tmp_path,
        script=script,
        log_dir=log_dir,
        env_extra={"STUB_RC": str(script_rc)},
    )
    assert r.returncode == script_rc, (
        f"退出码未透传：期望 {script_rc}，实际 {r.returncode}；stderr={r.stderr}"
    )


def test_exit_code_zero_produces_report_and_separates_stderr(tmp_path):
    """成功路径：当日报告落盘（含脚本 stdout），脚本 stderr 落**独立**日志。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    script = _make_stub_script(tmp_path / "s.sh")
    r = _run_launcher(tmp_path, script=script, log_dir=log_dir)
    assert r.returncode == 0, r.stderr

    reports = list(log_dir.glob("storage-health-*.md"))
    assert len(reports) == 1, f"应恰好落一份当日报告，实际 {reports}"
    assert "STDOUT_FROM_SCRIPT" in reports[0].read_text(encoding="utf-8")

    err_log = log_dir / "storage-health-stderr.log"
    assert err_log.exists(), "脚本 stderr 应落独立日志"
    assert "STDERR_FROM_SCRIPT" in err_log.read_text(encoding="utf-8")
    # stderr 日志是追加写：跑两次只多一行，不覆盖。
    r2 = _run_launcher(tmp_path, script=script, log_dir=log_dir)
    assert r2.returncode == 0
    assert err_log.read_text(encoding="utf-8").count("STDERR_FROM_SCRIPT") == 2


def test_report_is_overwritten_on_same_day(tmp_path):
    """当日复跑**覆盖**当日报告（旧行为是 shell 重定向 `>`）。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    script = _make_stub_script(tmp_path / "s.sh")
    r = _run_launcher(tmp_path, script=script, log_dir=log_dir)
    assert r.returncode == 0
    report = next(log_dir.glob("storage-health-*.md"))
    report.write_text("STALE\n", encoding="utf-8")
    r2 = _run_launcher(tmp_path, script=script, log_dir=log_dir)
    assert r2.returncode == 0
    body = report.read_text(encoding="utf-8")
    assert "STALE" not in body, "当日报告应被覆盖，不应保留旧内容"
    assert "STDOUT_FROM_SCRIPT" in body


def test_launcher_own_failure_does_not_use_script_exit_codes(tmp_path):
    """启动器自身失败 → 64（不与脚本的 0 / 2 撞码）。"""
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    r = _run_launcher(tmp_path, script=tmp_path / "missing.sh", log_dir=log_dir)
    assert r.returncode == 64
    assert r.returncode not in (0, 2)


# --------------------------------------------------------------------------- #
# 5. plist 模板                                                                  #
# --------------------------------------------------------------------------- #


def _render_plist(replacements: dict[str, str]) -> bytes:
    text = PLIST.read_text(encoding="utf-8")
    for key, value in replacements.items():
        text = text.replace(key, value)
    return text.encode("utf-8")


def test_plist_parses_with_plistlib():
    """plistlib 能解析（plistlib 比 plutil 严：XML 注释里不能有双连字符）。"""
    rendered = _render_plist(
        {
            "{{PYTHON_BIN}}": "/usr/bin/python3",
            "{{REPO_DIR}}": "/tmp/repo",
            "{{LOG_DIR}}": "/tmp/logs",
        }
    )
    obj = plistlib.loads(rendered)
    assert isinstance(obj, dict)


def test_plutil_lint_passes_after_substitution(tmp_path):
    rendered = _render_plist(
        {
            "{{PYTHON_BIN}}": "/usr/bin/python3",
            "{{REPO_DIR}}": str(tmp_path),
            "{{LOG_DIR}}": str(tmp_path),
        }
    )
    target = tmp_path / "job.plist"
    target.write_bytes(rendered)
    r = subprocess.run(["plutil", "-lint", str(target)], capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr


def test_plist_program_arguments_point_at_launcher():
    """plist 的 ProgramArguments 必须指向启动器（不再是 /bin/zsh）。"""
    obj = plistlib.loads(
        _render_plist(
            {
                "{{PYTHON_BIN}}": "/usr/bin/python3",
                "{{REPO_DIR}}": "/tmp/repo",
                "{{LOG_DIR}}": "/tmp/logs",
            }
        )
    )
    args = obj["ProgramArguments"]
    assert len(args) == 3, args
    assert args[0] == "/usr/bin/python3", "ProgramArguments[0] 应是解释器（responsible process）"
    assert args[1].endswith("releases/launchd/storage_health_launcher.py"), args[1]
    assert args[2] == "auto", args[2]
    # 旧版是 /bin/zsh：必须已被换掉，否则 launchd 下仍无 ~/Documents 授权。
    assert "/bin/zsh" not in args, "plist 不得再直接以 /bin/zsh 为 responsible process"


def test_plist_declares_all_placeholders():
    text = PLIST.read_text(encoding="utf-8")
    found = set(PLACEHOLDER_RE.findall(text))
    assert found == EXPECTED_PLACEHOLDERS, (
        f"占位符集合不符：{sorted(found)} != {sorted(EXPECTED_PLACEHOLDERS)}"
    )


def test_plist_env_declares_log_dir_and_script():
    obj = plistlib.loads(
        _render_plist(
            {
                "{{PYTHON_BIN}}": "/usr/bin/python3",
                "{{REPO_DIR}}": "/tmp/repo",
                "{{LOG_DIR}}": "/tmp/logs",
            }
        )
    )
    env = obj["EnvironmentVariables"]
    assert env["STORAGE_HEALTH_LOG_DIR"] == "/tmp/logs"
    assert env["STORAGE_HEALTH_SCRIPT"] == "/tmp/repo/releases/launchd/storage_health.sh"


def test_plist_repo_dir_is_consistent_across_keys():
    """{{REPO_DIR}} 在 ProgramArguments / WorkingDirectory / 环境变量里必须同源。"""
    obj = plistlib.loads(
        _render_plist(
            {
                "{{PYTHON_BIN}}": "/usr/bin/python3",
                "{{REPO_DIR}}": "/tmp/repo",
                "{{LOG_DIR}}": "/tmp/logs",
            }
        )
    )
    args = obj["ProgramArguments"]
    assert args[1].startswith("/tmp/repo/")
    assert obj["WorkingDirectory"] == "/tmp/repo"
    assert obj["EnvironmentVariables"]["STORAGE_HEALTH_SCRIPT"].startswith("/tmp/repo/")


def test_plist_has_no_keepalive_and_daily_schedule():
    """不设 KeepAlive（非零退出不得被无界重拉）；每日 09:00 触发。"""
    obj = plistlib.loads(
        _render_plist(
            {
                "{{PYTHON_BIN}}": "/usr/bin/python3",
                "{{REPO_DIR}}": "/tmp/repo",
                "{{LOG_DIR}}": "/tmp/logs",
            }
        )
    )
    assert "KeepAlive" not in obj
    cal = obj["StartCalendarInterval"]
    assert cal["Hour"] == 9
    assert cal["Minute"] == 0


def test_plist_has_no_hardcoded_host_paths():
    """模板不得硬编码宿主机绝对路径（与既有门禁同口径：装前必须替换占位符）。"""
    text = PLIST.read_text(encoding="utf-8")
    assert "/Users/davidliu" not in text, "模板内硬编码了宿主机绝对路径"
    assert "~/Documents" not in text or "{{" in text


def test_plist_stderr_path_is_separate_from_report():
    """launchd 自身诊断日志与脚本 stderr 日志分开（不污染当日报告）。"""
    obj = plistlib.loads(
        _render_plist(
            {
                "{{PYTHON_BIN}}": "/usr/bin/python3",
                "{{REPO_DIR}}": "/tmp/repo",
                "{{LOG_DIR}}": "/tmp/logs",
            }
        )
    )
    assert obj["StandardErrorPath"] == "/tmp/logs/launchd-storage-health-stderr.log"
    # 启动器自己把脚本 stderr 写到 storage-health-stderr.log（不在 plist 里）。
    # 两者名字不同：launchd 诊断（launchd- 前缀）vs 脚本 stderr。
    assert Path(obj["StandardErrorPath"]).name != "storage-health-stderr.log"
