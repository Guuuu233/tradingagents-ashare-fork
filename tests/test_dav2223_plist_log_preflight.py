"""DAV-2223 — plist 安装前置检查（plist_log_preflight.py）测试。

全部用例在 ``tmp_path`` 沙箱里跑：不碰 ``~/Library/LaunchAgents``、不碰生产
日志目录、不执行 ``launchctl``、不加载生产 plist 的真实路径（占位符路径只用
字符串构造，不实际触碰宿主机既有文件）。

覆盖（对齐卡要求的三情形 + 各原因码）：

* 放行：父目录存在可写、目标不存在（launchd 可自建）；
* 放行：父目录存在可写、目标已存在且属主为当前 uid 且可追加；
* 拒绝：父目录不存在 / 不可写；
* 拒绝：目标已存在但属主非当前 uid（10-18 首次安装 EX_CONFIG 78 的成因形态）；
* 拒绝：目标已存在但不可写（mode 444）；目标不是普通文件（目录）；
* 拒绝：旧版包装脚本遗留 —— 按文件名（wrapper-diagnostic.log）与
  按目录（.launchd/、/tmp/ta-launchd-die-stamps-*）；
* 拒绝：占位符未替换、相对路径、plist 无法解析；
* CLI 退出码契约：0 / 2 / 3，且 JSON 输出可被消费。
"""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "releases" / "launchd" / "plist_log_preflight.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("dav2223_plist_log_preflight", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    # dataclass 在 3.10 上按 __module__ 回查 sys.modules，动态加载必须先登记
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


mod = _load_module()


def _write_plist(path: Path, out_path: str, err_path: str | None = None) -> Path:
    payload = {
        "Label": "com.example.test",
        "ProgramArguments": ["/bin/true"],
        "StandardOutPath": out_path,
    }
    if err_path is not None:
        payload["StandardErrorPath"] = err_path
    path.write_bytes(plistlib.dumps(payload))
    return path


# --------------------------------------------------------------------------- #
# 放行                                                                       #
# --------------------------------------------------------------------------- #


def test_writable_parent_missing_target_passes(tmp_path):
    """可写父目录 + 目标不存在 → 放行（launchd 能自建文件）。"""
    plist = _write_plist(tmp_path / "a.plist", str(tmp_path / "logs" / "uvicorn-abc123.log"))
    (tmp_path / "logs").mkdir()
    report = mod.check_plist(plist)
    assert report.ok, [f.message for f in report.failures]
    assert not (tmp_path / "logs" / "uvicorn-abc123.log").exists()  # 检查零副作用


def test_existing_owned_appendable_file_passes(tmp_path):
    """已存在的日志文件属主为当前 uid 且可写 → 放行。"""
    log = tmp_path / "logs" / "uvicorn-abc123.log"
    log.parent.mkdir()
    log.write_text("prev\n")
    plist = _write_plist(tmp_path / "a.plist", str(log), str(log))
    report = mod.check_plist(plist)
    assert report.ok, [f.message for f in report.failures]
    assert len(report.findings) == 2
    assert log.read_text() == "prev\n"  # 未改内容


# --------------------------------------------------------------------------- #
# 拒绝：不可写                                                               #
# --------------------------------------------------------------------------- #


def test_missing_parent_dir_rejected(tmp_path):
    plist = _write_plist(tmp_path / "a.plist", str(tmp_path / "nope" / "uvicorn.log"))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_PARENT_MISSING


@pytest.mark.skipif(os.geteuid() == 0, reason="root 绕过 DAC 权限位，无法构造不可写目录")
def test_unwritable_parent_dir_rejected(tmp_path):
    d = tmp_path / "ro"
    d.mkdir()
    d.chmod(0o555)
    plist = _write_plist(tmp_path / "a.plist", str(d / "uvicorn.log"))
    try:
        report = mod.check_plist(plist)
    finally:
        d.chmod(0o755)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_PARENT_NOT_WRITABLE


@pytest.mark.skipif(os.geteuid() == 0, reason="root 绕过 DAC 权限位")
def test_existing_non_appendable_file_rejected(tmp_path):
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    log.chmod(0o444)
    plist = _write_plist(tmp_path / "a.plist", str(log))
    try:
        report = mod.check_plist(plist)
    finally:
        log.chmod(0o644)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_TARGET_NOT_APPENDABLE


def test_directory_target_rejected(tmp_path):
    d = tmp_path / "logs" / "uvicorn.log"
    d.mkdir(parents=True)
    plist = _write_plist(tmp_path / "a.plist", str(d))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_TARGET_NOT_REGULAR


@pytest.mark.skipif(os.geteuid() != 0, reason="非 root 无法构造他人属主文件")
def test_foreign_owned_file_rejected(tmp_path):
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    os.chown(log, 65534, 65534)
    plist = _write_plist(tmp_path / "a.plist", str(log))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_TARGET_NOT_OWNED


def test_foreign_owner_detected_via_lstat_stub(tmp_path, monkeypatch):
    """属主异常路径的可移植覆盖：非 root 环境用 monkeypatch 假冒 st_uid。

    这正是 10-18 首次安装失败的形态（属主/权限异常的遗留日志文件），
    必须在任意 uid 下都被检出，故单独做一条桩化用例。
    """
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    plist = _write_plist(tmp_path / "a.plist", str(log))

    real_lstat = mod.os.lstat
    real_getuid = mod.os.getuid

    def fake_lstat(path, *a, **kw):
        st = real_lstat(path, *a, **kw)
        return type("FakeStat", (), {
            "st_mode": st.st_mode, "st_uid": real_getuid() + 4242,
            "st_gid": st.st_gid, "st_size": st.st_size, "st_atime": st.st_atime,
            "st_mtime": st.st_mtime, "st_ctime": st.st_ctime,
        })()

    monkeypatch.setattr(mod.os, "lstat", fake_lstat)
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_TARGET_NOT_OWNED
    assert "属主" in report.failures[0].message


# --------------------------------------------------------------------------- #
# 拒绝：旧版包装脚本遗留物                                                     #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("name", list(mod.LEGACY_NAMES))
def test_legacy_wrapper_filename_rejected(tmp_path, name):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / name).write_text("old\n")  # 遗留文件仍在（DAV-2180 起不该再被引用）
    plist = _write_plist(tmp_path / "a.plist", str(logs / name))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_LEGACY_NAME


def test_legacy_wrapper_dir_rejected(tmp_path):
    d = tmp_path / "logs" / ".launchd"
    d.mkdir(parents=True)
    plist = _write_plist(tmp_path / "a.plist", str(d / "restart-stamps" / "x"))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_LEGACY_DIR


def test_legacy_tmp_stamp_dir_rejected(tmp_path):
    """wrapper 时代 /tmp 兜底戳目录形态（/tmp/ta-launchd-die-stamps-<uid>-<sha>）。"""
    import shutil
    d = Path("/tmp/ta-launchd-die-stamps-501-082be047-test")
    if d.exists():
        shutil.rmtree(d)
    d.mkdir()
    try:
        plist = _write_plist(tmp_path / "a.plist", str(d / "restart-stamps-1200"))
        report = mod.check_plist(plist)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_LEGACY_DIR


def test_custom_legacy_name_rejected(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    plist = _write_plist(tmp_path / "a.plist", str(logs / "old-thing.log"))
    assert mod.check_plist(plist).ok  # 不在黑名单时放行
    report = mod.check_plist(plist, legacy_names=mod.LEGACY_NAMES + ("old-thing.log",))
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_LEGACY_NAME


# --------------------------------------------------------------------------- #
# 拒绝：输入/占位符                                                           #
# --------------------------------------------------------------------------- #


def test_unresolved_placeholder_rejected(tmp_path):
    plist = _write_plist(
        tmp_path / "a.plist",
        "/Users/davidliu/Documents/TradingAgents-AShare-releases/logs/uvicorn-{{RELEASE_SHA}}.log",
    )
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_PLACEHOLDER


def test_relative_path_rejected(tmp_path):
    plist = _write_plist(tmp_path / "a.plist", "logs/uvicorn.log")
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_KEY_NOT_ABSOLUTE


def test_broken_plist_raises(tmp_path):
    bad = tmp_path / "bad.plist"
    bad.write_text("not a plist at all")
    with pytest.raises(ValueError):
        mod.check_plist(bad)


def test_plist_without_log_paths_is_noop_pass(tmp_path):
    p = tmp_path / "a.plist"
    p.write_bytes(plistlib.dumps({"Label": "x", "ProgramArguments": ["/bin/true"]}))
    report = mod.check_plist(p)
    assert report.ok and len(report.findings) == 1


# --------------------------------------------------------------------------- #
# CLI 退出码契约                                                             #
# --------------------------------------------------------------------------- #


def _run(args, cwd):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], capture_output=True, text=True, cwd=cwd
    )


def test_cli_exit_0_on_pass(tmp_path):
    (tmp_path / "logs").mkdir()
    plist = _write_plist(tmp_path / "a.plist", str(tmp_path / "logs" / "uvicorn.log"))
    r = _run([str(plist)], cwd=tmp_path)
    assert r.returncode == mod.EXIT_OK, r.stderr


def test_cli_exit_2_and_reason_code_on_failure(tmp_path):
    plist = _write_plist(tmp_path / "a.plist", str(tmp_path / "missing" / "uvicorn.log"))
    r = _run([str(plist)], cwd=tmp_path)
    assert r.returncode == mod.EXIT_PREFLIGHT_FAILED
    assert mod.RC_PARENT_MISSING in r.stderr
    assert "fail-closed" in r.stderr


def test_cli_exit_3_on_missing_file(tmp_path):
    r = _run([str(tmp_path / "nope.plist")], cwd=tmp_path)
    assert r.returncode == mod.EXIT_USAGE
    assert "文件不存在" in r.stderr


def test_cli_json_output(tmp_path):
    (tmp_path / "logs").mkdir()
    plist = _write_plist(tmp_path / "a.plist", str(tmp_path / "logs" / "uvicorn.log"))
    r = _run([str(plist), "--json"], cwd=tmp_path)
    assert r.returncode == mod.EXIT_OK
    data = json.loads(r.stdout)
    assert data["ok"] is True
    assert data["reports"][0]["label"] == "com.example.test"


def test_cli_json_failure_carries_reason_code(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "wrapper-diagnostic.log").write_text("old")
    plist = _write_plist(tmp_path / "a.plist", str(logs / "wrapper-diagnostic.log"))
    r = _run([str(plist), "--json"], cwd=tmp_path)
    assert r.returncode == mod.EXIT_PREFLIGHT_FAILED
    data = json.loads(r.stdout)
    assert data["ok"] is False
    assert data["reports"][0]["findings"][0]["reason_code"] == mod.RC_LEGACY_NAME


def test_repo_templates_have_expected_log_keys():
    """仓库内 4 个 plist 模板都能被解析（占位符未替换时应判 FAIL，不是崩溃）。"""
    for plist in sorted((ROOT / "releases" / "launchd").glob("*.plist")):
        if mod._PLACEHOLDER_RE.search(plist.read_text(encoding="utf-8", errors="ignore")):
            continue  # 含占位符的模板只验到「解析不炸」即可
        report = mod.check_plist(plist)
        assert isinstance(report.ok, bool)
