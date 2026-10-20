"""DAV-2223 — plist 安装前置检查（plist_log_preflight.py）测试。

全部用例在 ``tmp_path`` 沙箱里跑：不碰 ``~/Library/LaunchAgents``、不碰生产
日志目录、不执行 ``launchctl``、不加载生产 plist 的真实路径（占位符路径只用
字符串构造，不实际触碰宿主机既有文件）。

覆盖（对齐卡要求的三情形 + 各原因码）：

* 放行：父目录存在可写、目标不存在（launchd 可自建）→ PASS；
* **WARN 不拦**：父目录存在可写、目标已存在、无 provenance 扩展属性、属主为当前
  uid 且可追加 → ``WARN[LOGPATH_TARGET_EXISTING_APPENDABLE]``、``ok=True``、退出码 0，
  并在终端与 ``--json`` 中**逐条列出**（总控 10-18 口径第 2 条）；
* 拒绝（FAIL）：目标已存在且带 ``com.apple.provenance``（10-18 事故真因形态：属主/
  权限正常仍被 launchd 判 EX_CONFIG 78；处置 = 改名留档，不直接覆盖）；
* 拒绝：父目录不存在 / 不可写；
* 拒绝：目标已存在但属主非当前 uid（**非** 10-18 形态）；
* 拒绝：目标已存在但不可写（mode 444）；目标不是普通文件（目录）；
* 拒绝：xattr(1) 不可用/执行失败 → ``LOGPATH_PROVENANCE_PROBE_UNAVAILABLE``；
* 拒绝：父路径存在但不是目录 → ``LOGPATH_PARENT_NOT_DIR``；lstat 竞态失败 →
  ``LOGPATH_OPEN_PROBE_FAILED``（两条均为 fail-closed 兜底，DAV-2275 补覆盖）；
* 拒绝：旧版包装脚本遗留 —— 按文件名（wrapper-diagnostic.log）与
  按目录（.launchd/、/tmp/ta-launchd-die-stamps-*）；
* 拒绝：占位符未替换、相对路径、plist 无法解析；
* CLI 退出码契约：0 / 2 / 3（无参与坏参数也走 3），且 JSON 输出可被消费。

DAV-2232（复审 DAV-2230 打回）：非 provenance 主题的用例一律
``monkeypatch`` 掉 ``_carries_provenance``，整套测试不再依赖宿主 ``/usr/bin/xattr``
的运行时状态（此前标准全量口径下曾 4 条不可复现失败）。依赖真调 ``xattr`` 的
只有本文件内标 ``needs_xattr`` 的用例（仅 macOS），仍随文件全部 skip 语义保持。
"""
from __future__ import annotations

import importlib.util
import json
import os
import plistlib
import shlex
import subprocess
import sys
import time
from pathlib import Path
from typing import NamedTuple

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "releases" / "launchd" / "plist_log_preflight.py"

needs_xattr = pytest.mark.skipif(
    sys.platform != "darwin",
    reason="需要宿主 /usr/bin/xattr（仅 macOS；DAV-2232 🟢-6）",
)


@pytest.fixture
def no_provenance(monkeypatch):
    """把 provenance 探测钉死为「不带」——非 provenance 主题用例用。

    使这些用例与宿主 ``xattr(1)`` 的运行时状态（压力下的 ``OSError``、外部写入的
    属性）完全解耦，消除 DAV-2230 复审记录到的“首次全量 4 条失败、二次全绿”。
    """
    monkeypatch.setattr(mod, "_carries_provenance", lambda _p: False)
    return lambda _p: False


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


def test_writable_parent_missing_target_passes(tmp_path, no_provenance):
    """可写父目录 + 目标不存在 → 放行（launchd 能自建文件）。"""
    plist = _write_plist(tmp_path / "a.plist", str(tmp_path / "logs" / "uvicorn-abc123.log"))
    (tmp_path / "logs").mkdir()
    report = mod.check_plist(plist)
    assert report.ok, [f.message for f in report.failures]
    assert not (tmp_path / "logs" / "uvicorn-abc123.log").exists()  # 检查零副作用


def test_existing_owned_appendable_file_warns_but_passes(tmp_path, no_provenance):
    """口径第 2 条：既存 + 无 provenance + 属主本 uid + 可追加 → WARN、**不拦**。

    launchd 能正常打开并追加该文件，与 10-18 事故形态（带 provenance）不同，
    故不得判 FAIL；但必须让运维知情，故升级为 WARN 并在输出里列出。
    """
    log = tmp_path / "logs" / "uvicorn-abc123.log"
    log.parent.mkdir()
    log.write_text("prev\n")
    plist = _write_plist(tmp_path / "a.plist", str(log), str(log))
    report = mod.check_plist(plist)
    assert report.ok, [f.message for f in report.failures]  # 不拦
    assert [f.severity for f in report.findings] == ["WARN", "WARN"]
    assert [f.reason_code for f in report.findings] == [
        mod.RC_TARGET_EXISTING_APPENDABLE, mod.RC_TARGET_EXISTING_APPENDABLE
    ]
    assert [f.path for f in report.warnings] == [str(log), str(log)]  # 逐条列出
    assert log.read_text() == "prev\n"  # 未改内容


def test_missing_target_is_pass_not_warn(tmp_path, no_provenance):
    """口径第 3 档：目标不存在 → PASS（launchd 自建），不得混入 WARN 列表。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    plist = _write_plist(tmp_path / "a.plist", str(log))
    report = mod.check_plist(plist)
    assert report.ok and report.warnings == []
    assert report.findings[0].severity == "PASS"
    assert report.findings[0].reason_code is None


def test_warn_does_not_break_fail_closed_on_sibling_finding(tmp_path, no_provenance):
    """同一 plist 内 WARN 与 FAIL 共存时，仍以 FAIL 阻断（ok=False）。"""
    good = tmp_path / "logs" / "uvicorn.log"
    good.parent.mkdir()
    good.write_text("prev\n")
    bad = tmp_path / "logs" / "missing-parent" / "x.log"
    plist = _write_plist(tmp_path / "a.plist", str(good), str(bad))
    report = mod.check_plist(plist)
    assert not report.ok
    assert len(report.warnings) == 1
    assert report.failures[0].reason_code == mod.RC_PARENT_MISSING


def test_provenance_existing_file_is_fail_not_warn(tmp_path):
    """口径第 1 条：带 provenance 的既存文件仍是 FAIL（10-18 事故形态）。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    _set_provenance(log)
    plist = _write_plist(tmp_path / "a.plist", str(log))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.warnings == []
    assert report.failures[0].severity == "FAIL"
    assert report.failures[0].reason_code == mod.RC_TARGET_PROVENANCE
    assert "改名留档" in report.failures[0].message  # 处置 = 改名留档


def test_finding_severity_ok_contract():
    """severity 与 ok 必须一致（否则 WARN/FAIL 语义可被静默改坏）。"""
    with pytest.raises(ValueError):
        mod.Finding("p", "k", "", True, None, None, severity="FAIL")
    with pytest.raises(ValueError):
        mod.Finding("p", "k", "", False, None, None, severity="WARN")
    with pytest.raises(ValueError):
        mod.Finding("p", "k", "", True, None, None, severity="NOPE")
    assert mod.Finding("p", "k", "", True, None, None, severity="WARN").ok is True
    assert mod.Finding("p", "k", "", False, "C", None, severity="FAIL").ok is False


@needs_xattr
def test_existing_file_without_attribute_warns_end_to_end(tmp_path):
    """端到端：真实 xattr 判定下，无 provenance 的既存文件 = WARN 且退出码 0。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("prev\n")
    _drop_provenance(log)  # 清理并验证，不依赖宿主此刻有无打标记
    assert mod._carries_provenance(str(log)) is False  # 前提：真无属性（已验证）
    plist = _write_plist(tmp_path / "a.plist", str(log))
    report = mod.check_plist(plist)
    assert report.ok and len(report.warnings) == 1

    r = _run([str(plist)], cwd=tmp_path)
    assert r.returncode == mod.EXIT_OK  # WARN 不拦
    assert "WARN" in r.stderr and "LOGPATH_TARGET_EXISTING_APPENDABLE" in r.stderr
    assert "不阻断安装" in r.stderr

    rj = _run([str(plist), "--json"], cwd=tmp_path)
    assert rj.returncode == mod.EXIT_OK
    data = json.loads(rj.stdout)
    assert data["ok"] is True
    assert data["reports"][0]["warnings"][0]["path"] == str(log)
    assert data["reports"][0]["warnings"][0]["reason_code"] == (
        mod.RC_TARGET_EXISTING_APPENDABLE
    )


def test_messages_give_full_path_in_recheck_command(tmp_path, monkeypatch):
    """文案里的复核命令必须给**完整路径**，且是合法可跑的 ``xattr`` 调用。

    旧文案写的是 ``xattr -l <文件名>``（**裸文件名**，依赖调用者的 cwd），
    运维在别的目录照抄会得到「看起来没属性」的误导结果——而这一步恰恰是用来
    证实 WARN 档安全的。本仓推荐形式为 ``xattr -p <attr> -- <path>``（无属性时
    rc=1 + stderr 明写 ``No such xattr``，**不会**像 ``-l`` 那样空输出）。

    🟡-1 口径修正：本用例**不**断言 ``xattr -l`` 是「非法调用」——``man xattr``
    第一种形式 ``xattr [-lrsvx] file ...`` 里 ``-l`` 的第一个位置参数**就是**文件
    路径，带全路径的 ``xattr -l <path>`` 合法可用。真正的缺陷只有「裸文件名」，
    故此处断言「不含裸文件名形式」 + 「含推荐的全路径调用」。

    不真调 ``xattr -w``（在部分宿主/临时卷上写该属性会失败，且与本条要验的文案
    无关）：WARN 档靠钉死探测为假进入，PROVENANCE 档靠钉死为真进入。
    """
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("prev\n")

    # 1) WARN 档：既存、无属性、可追加
    monkeypatch.setattr(mod, "_carries_provenance", lambda _p: False)
    warn_msg = mod.check_plist(
        _write_plist(tmp_path / "warn.plist", str(log))
    ).warnings[0].message

    # 2) PROVENANCE 档：带属性 → FAIL 档文案
    monkeypatch.setattr(mod, "_carries_provenance", lambda _p: True)
    fail_msg = mod.check_plist(
        _write_plist(tmp_path / "fail.plist", str(log))
    ).failures[0].message

    legal = f"xattr -p {mod.PROVENANCE_XATTR} -- {shlex.quote(str(log))}"
    for msg in (warn_msg, fail_msg):
        # 必须给完整路径（而不是裸文件名）
        assert f" {log.name}`" not in msg, msg
        # 含推荐形式的合法全路径调用
        assert legal in msg, msg


def test_recheck_command_quotes_paths_with_spaces(tmp_path, monkeypatch):
    """建议命令里的路径做 shell 引用：含空格时照抄不会断词（DAV-2279 🟢-2）。

    本仓日志目录无空格，故属加固而非缺陷修复；锁住它以免后续回退。
    """
    log = tmp_path / "logs dir" / "uvicorn v2.log"
    log.parent.mkdir()
    log.write_text("prev\n")
    monkeypatch.setattr(mod, "_carries_provenance", lambda _p: True)
    msg = mod.check_plist(
        _write_plist(tmp_path / "a.plist", str(log))
    ).failures[0].message
    assert shlex.quote(str(log)) in msg, msg
    # 原文把未引用的带空格路径直接内联进命令文本——会被 shell 拆成多个参数
    assert f"-- {log}`" not in msg, msg


# --------------------------------------------------------------------------- #
# 拒绝：不可写                                                               #
# --------------------------------------------------------------------------- #


def test_missing_parent_dir_rejected(tmp_path, no_provenance):
    plist = _write_plist(tmp_path / "a.plist", str(tmp_path / "nope" / "uvicorn.log"))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_PARENT_MISSING


@pytest.mark.skipif(os.geteuid() == 0, reason="root 绕过 DAC 权限位，无法构造不可写目录")
def test_unwritable_parent_dir_rejected(tmp_path, no_provenance):
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
def test_existing_non_appendable_file_rejected(tmp_path, no_provenance):
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


def test_directory_target_rejected(tmp_path, no_provenance):
    d = tmp_path / "logs" / "uvicorn.log"
    d.mkdir(parents=True)
    plist = _write_plist(tmp_path / "a.plist", str(d))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_TARGET_NOT_REGULAR


def test_parent_is_a_regular_file_rejected(tmp_path, no_provenance):
    """父路径存在但不是目录（DAV-2275 补覆盖，原先零引用）。

    真实形态：``StandardOutPath`` 少写一层目录，使父路径落在某个普通文件上。
    必须判 fail-closed 的 ``LOGPATH_PARENT_NOT_DIR`` 而不是继续往下 stat 目标。
    """
    not_a_dir = tmp_path / "logs"
    not_a_dir.write_text("i am a file\n")
    plist = _write_plist(tmp_path / "a.plist", str(not_a_dir / "uvicorn.log"))
    report = mod.check_plist(plist)
    assert not report.ok
    assert [f.reason_code for f in report.failures] == [mod.RC_PARENT_NOT_DIR]
    assert "不是目录" in report.failures[0].message


def test_lstat_failure_rejected_as_open_probe_failure(tmp_path, monkeypatch, no_provenance):
    """目标已存在但 lstat 失败 → ``LOGPATH_OPEN_PROBE_FAILED``（DAV-2275 补覆盖）。

    形态是 ``Path.exists()`` 与 ``os.lstat()`` 之间的竞态（删除/权限变化）。
    fail-closed，且**不得**退化为「当作不存在而 PASS」——那会把检查不到的路径放行。
    """
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")

    def boom(path, *a, **kw):
        if str(path).endswith("uvicorn.log"):
            raise OSError(5, "Input/output error")
        return real_lstat(path, *a, **kw)

    real_lstat = mod.os.lstat
    monkeypatch.setattr(mod.os, "lstat", boom)
    plist = _write_plist(tmp_path / "a.plist", str(log))
    report = mod.check_plist(plist)
    assert not report.ok
    assert [f.reason_code for f in report.failures] == [mod.RC_OPEN_PROBE]
    assert "无法 stat" in report.failures[0].message


@pytest.mark.skipif(os.geteuid() != 0, reason="非 root 无法构造他人属主文件")
def test_foreign_owned_file_rejected(tmp_path, no_provenance):
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    os.chown(log, 65534, 65534)
    plist = _write_plist(tmp_path / "a.plist", str(log))
    report = mod.check_plist(plist)
    assert not report.ok
    assert report.failures[0].reason_code == mod.RC_TARGET_NOT_OWNED


def test_foreign_owner_detected_via_lstat_stub(tmp_path, monkeypatch, no_provenance):
    """属主异常路径的可移植覆盖：非 root 环境用 monkeypatch 假冒 st_uid。

    **这不是 10-18 首次安装失败的形态**：属主/权限异常同样会让 launchd 打不开，
    但 10-18 的真因是既存文件带 ``com.apple.provenance`` 且属主/权限完全正常
    （见本文件模块 docstring）。本条只覆盖属主分支，故必须把 provenance 判定
    钉死为 False，否则真因会把属主分支遮住、这条用例就变成假覆盖。
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


def _probe_provenance(path: Path) -> bool:
    """不经产品代码，直接问 xattr(1) 该路径当前是否带 provenance。"""
    proc = subprocess.run(
        [mod.XATTR_BIN, "-p", mod.PROVENANCE_XATTR, "--", str(path)], capture_output=True
    )
    return proc.returncode == 0


#: 清理重试窗口（DAV-2236 🟡-1）。清理本身瞬时完成，多出来的几轮是为使
#: 「清理 → 探测验证」重复观察到同一结论（**防御性兜底，机制未证实**：不存在
#: 「macOS 必然异步打标」的已验证结论，见下方 docstring）。
_DROP_RETRIES = 5
_DROP_RETRY_SLEEP = 0.05


class _DropOutcome(NamedTuple):
    """「清理 → 探测验证」的结果；**透出末轮清理命令的 (rc, stderr)**。

    ``rc`` / ``stderr`` 是让 skip 里的「环境问题」结论**自证**的关键（DAV-2276
    🟡-1）：若清理命令被改成无操作探测，rc/stderr 会当场暴露，而不再把实现回归
    伪装成「环境自动附加、清不掉」。
    """

    ok: bool
    attempts: int
    last_rc: int | None
    last_stderr: str


def _drop_provenance(path: Path) -> _DropOutcome:
    """确保沙箱文件不带 provenance；**清不掉则 skip，而非 fail**（DAV-2274）。

    **DAV-2236 🟡-1**：``xattr -c`` 的正确语法是 ``xattr -c <file>``（清空该文件
    **全部**属性），**不接受属性名参数**。旧写法 ``xattr -c <attr> -- <path>`` 把
    属性名当文件名，恒报 ``No such file``、rc=1、什么也清不掉（旧 docstring 把
    「rc=1 即属性不存在」当正常返回，是误读），于是真调用例的「无属性」前提实际靠
    「宿主恰好没打标记」支撑——这正是本链反复出现的偶发假失败根源。

    重试的触发条件**因宿主而异，两侧都记录在案**：

    * DAV-2274：部分宿主（实测新建文件即被系统自动附加 ``com.apple.provenance``
      且 ``xattr -c`` 无法清除）上，重试耗尽仍清不掉。
    * DAV-2245 复审：在沙箱 52 个文件、8 轮创建后探测与 36 次全文件压力跑中
      **未观察到**任何自动打标（带属性的均为 ``_set_provenance`` 主动打的 0100）；
      该侧更符合证据的解释是**上一次 ``_set_provenance`` 的残留**被旧的无操作
      ``_drop_provenance`` 留下了。

    故此处的重试是**防御性兜底**：保留是为了真的出现该形态时不至于静默返回一个
    没清干净的文件，但**不应被理解为「macOS 必然会异步打标」**——那是一个未在
    全部宿主上复现的推断，本 docstring 不再作此断言。

    **DAV-2274**：重试耗尽属**环境问题**，不是被测代码行为，故改 ``pytest.skip``
    并在原因里写明「环境自动附加 com.apple.provenance」，不得让环境差异伪装成产品
    缺陷。**重试轮数/sleep 与清理命令均与 DAV-2236 一致**（``xattr -p`` 只读探测、
    ``xattr -c`` 清理）。

    **DAV-2276 🟡-1**：skip 原因必须**自证**。旧写法把失败一律归因为「环境自动附加
    且 -c 清不掉」，而清理命令本身若被换成**无操作只读探测**（不报错、rc=0），
    这条结论就完全失去证据——实现回归被伪装成环境跳过。故本层改为透出末轮清理
    命令的 ``(rc, stderr)`` 并写进 skip 原因（见 :class:`_DropOutcome`），并由
    ``test_drop_provenance_skips_instead_of_failing_when_attribute_survives``
    断言清理 argv 恰为 ``[XATTR_BIN, "-c", <path>]``。

    夹具绝不静默返回一个没清干净的文件让断言去解释。
    """
    outcome = _try_drop_provenance(path)
    if not outcome.ok:
        pytest.skip(
            f"环境自动附加 {mod.PROVENANCE_XATTR} 且 {mod.XATTR_BIN} -c 无法清除：{path}"
            f"（已跑满 {outcome.attempts} 轮；末轮 `xattr -c <path>` 实测 "
            f"rc={outcome.last_rc if outcome.last_rc is not None else '未执行'}、"
            f"stderr={outcome.last_stderr or '<无 stderr>'}）"
            "（宿主环境问题，非被测代码缺陷；DAV-2274）"
        )
    return outcome


def _try_drop_provenance(path: Path) -> _DropOutcome:
    """「清理 → 探测验证」重试循环；返回是否确已清干净（不含 skip 语义）。

    与 :func:`_drop_provenance` 拆开，是为了让 skip 路径本身可被用例覆盖
    （DAV-2274 新增用例：清不掉时必须 skip 而不是 fail）。

    末轮 ``xattr -c <path>`` 的 ``(rc, stderr)`` 一并透出（DAV-2276 🟡-1）：skip
    文案带上它，「环境自动附加且清不掉」才是**有证据的结论**而不是一句断言。
    """
    outcome = _DropOutcome(ok=False, attempts=0, last_rc=None, last_stderr="<清理命令未执行>")
    for _ in range(_DROP_RETRIES):
        try:
            proc = subprocess.run([mod.XATTR_BIN, "-c", str(path)], capture_output=True)
        except OSError as exc:  # xattr(1) 缺失/不可执行：同样要进 skip 原因
            outcome = _DropOutcome(
                False, outcome.attempts + 1, None,
                f"{mod.XATTR_BIN} 不可执行：{exc.__class__.__name__}: {exc}",
            )
        else:
            raw_err = getattr(proc, "stderr", b"") or b""
            stderr = raw_err.decode("utf-8", "replace").strip()
            outcome = _DropOutcome(
                False, outcome.attempts + 1, getattr(proc, "returncode", None),
                stderr or "<无 stderr>",
            )
        if not _probe_provenance(path):
            return outcome._replace(ok=True)
        time.sleep(_DROP_RETRY_SLEEP)
    return outcome


def _set_provenance(path: Path) -> None:
    """给沙箱文件打上 com.apple.provenance，模拟 bash/>> 写出的既有日志。"""
    subprocess.run(
        [mod.XATTR_BIN, "-w", mod.PROVENANCE_XATTR, "0100", str(path)], check=True
    )


# --------------------------------------------------------------------------- #
# 拒绝：com.apple.provenance（10-18 事故真因形态）                             #
# --------------------------------------------------------------------------- #


def test_provenance_target_rejected_with_owner_and_mode_intact(tmp_path):
    """10-18 事故形态：属主=当前 uid、mode 0644、只因带 provenance 就必须拒。

    旧实现只查 mode/uid，对这一形态判 PASS（复审 DAV-2226 红队实测）。
    """
    log = tmp_path / "logs" / "uvicorn-082be047.log.pre-launchd-20261018T0210Z"
    log.parent.mkdir()
    log.write_text("prev\n")
    os.chmod(log, 0o644)
    _set_provenance(log)

    st = log.stat()
    assert st.st_uid == os.getuid() and st.st_mode & 0o777 == 0o644
    assert os.access(log, os.W_OK)

    plist = _write_plist(tmp_path / "a.plist", str(log), str(log))
    report = mod.check_plist(plist)
    assert not report.ok
    assert [f.reason_code for f in report.failures] == [
        mod.RC_TARGET_PROVENANCE, mod.RC_TARGET_PROVENANCE,
    ]
    assert "改名留档" in report.failures[0].message


def test_provenance_check_precedes_owner_check(tmp_path, monkeypatch):
    """provenance 判定在属主判定之前——真因优先，且不依赖 uid 改写。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("prev\n")
    _set_provenance(log)
    plist = _write_plist(tmp_path / "a.plist", str(log))
    assert mod.check_plist(plist).failures[0].reason_code == mod.RC_TARGET_PROVENANCE


@needs_xattr
def test_drop_provenance_skips_instead_of_failing_when_attribute_survives(
    tmp_path, monkeypatch
):
    """清不掉时必须 skip，绝不 fail（DAV-2274），且原因**自证**（DAV-2276 🟡-1）。

    背景：部分宿主上新建文件会被系统自动附加 ``com.apple.provenance`` 且
    ``xattr -c`` 清不掉。改判 fail 会把环境问题伪装成产品缺陷。此处把
    「探测恒为真」注入掉，稳定复现该环境，再断言走的是 skip 分支。

    🔴-1 关键：假 ``subprocess.run`` **必须校验 argv**。若清理命令被换成无操作
    探测（如 ``xattr -p`` 只读），旧写法（不校验 argv）仍全绿，并把实现回归
    报成「环境清不掉」。此处断言每轮 argv 恰为 ``[XATTR_BIN, "-c", <path>]``，
    并断言 skip 原因带上末轮 ``(rc, stderr)``——两者任一被回退，本用例即红。
    """
    clean_argvs: list[list[str]] = []

    class _Proc:
        returncode = 1
        stderr = b"xattr: Could not clear com.apple.provenance: Operation not permitted"

    def fake_run(argv, *a, **k):
        clean_argvs.append(list(argv))
        return _Proc()

    def always_still_there(path):
        return True

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    monkeypatch.setattr(sys.modules[__name__], "_probe_provenance", always_still_there)

    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")

    with pytest.raises(pytest.skip.Exception) as exc:
        _drop_provenance(log)

    reason = str(exc.value)
    assert mod.PROVENANCE_XATTR in reason  # 原因写明环境自动附加哪个属性
    assert "环境自动附加" in reason
    # 清理重试逻辑本身不变：仍跑满 _DROP_RETRIES 轮清理
    assert len(clean_argvs) == _DROP_RETRIES
    # 🔴-1：每轮清理命令的 argv 必须是真清理（`-c <path>`），不得被换成只读探测
    for argv in clean_argvs:
        assert argv == [mod.XATTR_BIN, "-c", str(log)], argv

    # 🔴-1：skip 原因带上末轮 (rc, stderr)，让「环境清不掉」结论自证
    assert "rc=1" in reason, reason
    assert "Operation not permitted" in reason, reason

    # 反面：探测为假时不得 skip（夹具不能把正常路径也跳过）
    monkeypatch.setattr(
        sys.modules[__name__], "_probe_provenance", lambda _p: False
    )
    assert _drop_provenance(log).ok is True


def test_drop_provenance_skip_reports_actual_cleanup_failure(monkeypatch):
    """清理命令真失败时，skip 原因里的 rc/stderr 必须**逐字来自**该次执行。

    本用例锁的是**透出通路**：把 rc/stderr 透出撤掉（或写死成别的值），本用例即
    红。归因变异（把 ``xattr -c`` 换成无操作只读探测）**不由**本用例捕获——它把
    整个 ``subprocess.run`` 替换成假实现，看不到真实 argv；那一变异由
    ``test_drop_provenance_skips_instead_of_failing_when_attribute_survives`` 的
    argv 断言锁住（DAV-2283 🟢-1 实测：变异下本用例仍绿、该用例红）。
    """
    log = Path("/tmp/dav2281/payload.log")

    class _Probe:
        """无操作探测的返回形态：成功、且没有任何失败证据。"""

        returncode = 0
        stderr = b""

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Probe())
    monkeypatch.setattr(
        sys.modules[__name__], "_probe_provenance", lambda _p: True
    )
    with pytest.raises(pytest.skip.Exception) as exc:
        _drop_provenance(log)
    reason = str(exc.value)
    # 真实执行是 rc=0 / 无 stderr，skip 原因就不得声称「末轮失败」的具体证据
    assert "rc=0" in reason, reason
    assert "stderr=<无 stderr>" in reason, reason


def test_drop_provenance_skip_reports_unexecutable_xattr(tmp_path, monkeypatch):
    """xattr(1) 缺失/不可执行（``subprocess.run`` 抛 ``OSError``）也走 skip，且文案可读。

    DAV-2283 🟢-2：该 ``except OSError`` 分支此前零覆盖——既有的假 ``run`` 只
    返回对象，从不抛错。此处令 ``subprocess.run`` 恒抛 ``FileNotFoundError``，
    断言：重试仍跑满 ``_DROP_RETRIES`` 轮、skip 原因带上异常类型，且 rc 位不得
    渲染成字面 ``None``（「命令返回了 None」是误读）。
    """
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    clean_argvs: list[list[str]] = []

    def raising_run(argv, *a, **k):
        clean_argvs.append(list(argv))
        raise FileNotFoundError(2, "No such file or directory", mod.XATTR_BIN)

    monkeypatch.setattr(mod.subprocess, "run", raising_run)
    monkeypatch.setattr(
        sys.modules[__name__], "_probe_provenance", lambda _p: True
    )
    with pytest.raises(pytest.skip.Exception) as exc:
        _drop_provenance(log)

    reason = str(exc.value)
    assert len(clean_argvs) == _DROP_RETRIES
    assert f"已跑满 {_DROP_RETRIES} 轮" in reason, reason
    assert "FileNotFoundError" in reason, reason
    assert f"{mod.XATTR_BIN} 不可执行" in reason, reason
    assert "rc=None" not in reason, reason
    assert "rc=未执行" in reason, reason


def test_try_drop_provenance_reports_failure_without_skipping(tmp_path, monkeypatch):
    """底层清理函数只报告结果（含末轮 rc/stderr），不抛 skip——skip 决策只在夹具层做。"""
    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: None)
    monkeypatch.setattr(
        sys.modules[__name__], "_probe_provenance", lambda _p: True
    )
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    assert _try_drop_provenance(log).ok is False

    monkeypatch.setattr(
        sys.modules[__name__], "_probe_provenance", lambda _p: False
    )
    assert _try_drop_provenance(log).ok is True


def test_provenance_probe_reads_real_xattr_without_writing(tmp_path):
    """判定走 /usr/bin/xattr -p（只读）；不带属性时 rc != 0 → 放行。

    DAV-2236 🟡-1：「创建后立即探测」的旧写法踩在宿主恰好打过标记的窗口上
    （探测结果取决于宿主运行时状态）。改为经 ``_drop_provenance`` **清理并验证**
    后再探测，前提变得确定。
    """
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("prev\n")
    _drop_provenance(log)  # 内含「清理 → 探测验证」重试
    before = log.stat().st_size
    assert mod._carries_provenance(str(log)) is False
    assert mod._carries_provenance(str(tmp_path / "nope.log")) is False
    assert log.stat().st_size == before  # 零副作用：不预创建、不追加


@needs_xattr
def test_drop_provenance_actually_clears_attribute(tmp_path):
    """_drop_provenance 必须真能清掉属性（旧写法是无操作，DAV-2236 🟡-1）。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    _set_provenance(log)
    assert _probe_provenance(log) is True  # 前置：确实带上了
    _drop_provenance(log)
    assert _probe_provenance(log) is False
    assert mod._carries_provenance(str(log)) is False


@needs_xattr
def test_provenance_probe_detects_real_attribute(tmp_path):
    """真调 xattr：带属性时 rc==0 → True（漏判会让假 PASS 复发）。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    _set_provenance(log)
    assert mod._carries_provenance(str(log)) is True


def test_provenance_probe_unavailable_when_xattr_missing(tmp_path, monkeypatch):
    """xattr(1) 不可用 → 「无法判定」，不得报成「带属性」（DAV-2232 🟡-2）。"""
    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    with pytest.raises(mod.ProvenanceProbeUnavailable):
        mod._carries_provenance(str(tmp_path))


def test_provenance_probe_unavailable_reported_as_own_reason_code(tmp_path, monkeypatch):
    """探测不可用 → 独立原因码 + fail-closed，且文案不诱导改名留档。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("prev\n")
    plist = _write_plist(tmp_path / "a.plist", str(log))

    def boom(*a, **k):
        raise FileNotFoundError(2, "No such file or directory")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    report = mod.check_plist(plist)
    assert not report.ok
    assert [f.reason_code for f in report.failures] == [
        mod.RC_PROVENANCE_PROBE_UNAVAILABLE
    ]
    msg = report.failures[0].message
    assert "FileNotFoundError" in msg and "No such file or directory" in msg
    assert "不等于" in msg  # 明说「不等于带属性」
    assert "请改名留档" not in msg  # 不诱导高风险动作
    # 🟢-1：三处 xattr 复核文案**全都要盖到**（此处是 PROVENANCE_UNAVAILABLE 档，
    # 其余两处由 test_messages_give_full_path_in_recheck_command 盖）。
    # 少了这条断言，只回退本处文案（plist_log_preflight.py 的 unavailable 分支）
    # 仍然全绿——护栏有洞。
    assert f"xattr -p {mod.PROVENANCE_XATTR} -- {shlex.quote(str(log))}" in msg, msg


def test_provenance_probe_nonzero_rc_without_no_such_xattr_is_unavailable(
    tmp_path, monkeypatch
):
    """rc!=0 且不是「无此属性」（如 EACCES）→ 无法判定，不得静默放行（🟢-4）。"""

    class _Proc:
        returncode = 1
        stderr = b"xattr: [Errno 13] Permission denied: 'uvicorn.log'"

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc())
    with pytest.raises(mod.ProvenanceProbeUnavailable):
        mod._carries_provenance(str(tmp_path))


def test_provenance_probe_rc1_no_such_xattr_is_false(tmp_path, monkeypatch):
    """rc==1 且 stderr 明说「无此属性」→ 唯一可安全放行的失败态。"""

    class _Proc:
        returncode = 1
        stderr = b"xattr: uvicorn.log: No such xattr: com.apple.provenance"

    monkeypatch.setattr(mod.subprocess, "run", lambda *a, **k: _Proc())
    assert mod._carries_provenance(str(tmp_path)) is False


def test_non_provenance_failure_path_does_not_call_host_xattr(
    tmp_path, monkeypatch, no_provenance
):
    """非 provenance 主题的拒绝路径不碰宿主 xattr（DAV-2232 🟡-2 用例注入）。"""
    log = tmp_path / "logs" / "uvicorn.log"
    log.parent.mkdir()
    log.write_text("x")
    log.chmod(0o444)
    plist = _write_plist(tmp_path / "a.plist", str(log))

    def boom(*a, **k):  # 若用例仍走真调用则此用例必红
        raise AssertionError("非 provenance 主题用例不应调用 xattr(1)")

    monkeypatch.setattr(mod.subprocess, "run", boom)
    try:
        report = mod.check_plist(plist)
    finally:
        log.chmod(0o644)
    assert report.failures[0].reason_code == mod.RC_TARGET_NOT_APPENDABLE


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


@pytest.mark.parametrize("name", ["Wrapper-Diagnostic.log", "WRAPPER-DIAGNOSTIC.LOG"])
def test_legacy_wrapper_filename_case_insensitive(tmp_path, name):
    """宿主机卷大小写不敏感：logs/Wrapper-Diagnostic.log 实测命中真实文件 → 需 casefold。"""
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "Wrapper-Diagnostic.log").write_text("old\n")
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


def test_legacy_tmp_stamp_dir_rejected(tmp_path, monkeypatch):
    """wrapper 时代 /tmp 兜底戳目录形态（/tmp/ta-launchd-die-stamps-<uid>-<sha>）。

    旧写法在真实 /tmp 建/删目录（违背「全部用例在 tmp_path 沙箱跑」的声明），
    改用 monkeypatch 改写 LEGACY_DIR_PREFIXES。
    """
    monkeypatch.setattr(mod, "LEGACY_DIR_PREFIXES", (str(tmp_path / "ta-launchd-die-stamps-"),))
    d = tmp_path / "ta-launchd-die-stamps-501-082be047-test"
    d.mkdir()
    plist = _write_plist(tmp_path / "a.plist", str(d / "restart-stamps-1200"))
    report = mod.check_plist(plist)
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


def test_cli_usage_errors_are_exit_3_not_2(tmp_path):
    """无参 / 未知参数 → EXIT_USAGE(3)，不得与 EXIT_PREFLIGHT_FAILED(2) 撞码。"""
    assert _run([], cwd=tmp_path).returncode == mod.EXIT_USAGE
    assert _run(["--bogus-flag"], cwd=tmp_path).returncode == mod.EXIT_USAGE


def test_cli_help_exits_0(tmp_path):
    assert _run(["--help"], cwd=tmp_path).returncode == mod.EXIT_OK


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


def test_repo_templates_parse_after_placeholder_substitution(tmp_path):
    """仓库内 4 个 plist 模板：占位符替换后都能被 plistlib 解析。

    断言是真实断言（复审 DAV-2226 🟡-2：旧写法遇到占位符就 continue，等于把会炸的
    模板全跳过了——而 ta-daily-reports.plist 注释里的 ``--`` 确实让 plistlib 报
    ExpatError，plutil -lint 却一直判 OK）。
    """
    templates = sorted((ROOT / "releases" / "launchd").glob("*.plist"))
    assert len(templates) == 4, [p.name for p in templates]
    for src in templates:
        rendered = mod._PLACEHOLDER_RE.sub(
            "PLACEHOLDER", src.read_text(encoding="utf-8")
        ).encode()
        obj = plistlib.loads(rendered)  # 解析失败即用例失败
        assert isinstance(obj, dict), src.name
        target = tmp_path / src.name
        target.write_bytes(plistlib.dumps(obj))
        assert isinstance(mod.check_plist(target).ok, bool), src.name
