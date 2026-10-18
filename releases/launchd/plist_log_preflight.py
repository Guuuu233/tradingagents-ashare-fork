#!/usr/bin/env python3
"""DAV-2223 — launchd plist 安装前置检查（fail-closed，防 EX_CONFIG 78 复发）。

背景（10-18 实测）
------------------
``launchctl bootstrap gui/$UID <plist>`` 在作业 ``StandardOutPath`` /
``StandardErrorPath`` 打不开时，launchd 直接拒绝装载并返回
``EX_CONFIG``（= 78）。10-18 首次安装失败即属此情形：日志文件是
**旧版包装脚本时代由 bash/``>>`` 写的既存文件**，带着
``com.apple.provenance`` 扩展属性（属主/权限位完全正常），改名留档后由
launchd 重建同名文件才恢复。launchd 自身只在 stderr 留一行，不区分
「权限问题」与「文件打不开」，运维侧无法在安装前察觉。

本脚本做安装**前**的确定性检查，只读、不改任何文件、不碰生产：

1. 解析 plist（``plistlib``，标准库；占位符 ``{{NAME}}`` 必须先替换）；
2. 对每个 ``StandardOutPath`` / ``StandardErrorPath`` 判定：
   - 父目录存在、是目录、可写（必要时可创建）；
   - 目标不存在则放行（launchd 会自建）；已存在必须是**普通文件**、**不带
     ``com.apple.provenance``**（该属性即「非 launchd 创建」的判据）、
     **属主为当前 uid**、**可追加**；
   - 不属于旧版包装脚本遗留物（废弃文件名 / 已废弃目录）。
3. 任一条不满足 → **fail-closed**：打印原因码 + 明确文案，退出码非 0，
   绝不静默放行。

用法
----
    python releases/launchd/plist_log_preflight.py <plist> [<plist> ...]
        [--legacy-name NAME]... [--legacy-dir PATH]... [--json]

退出码
------
====  =========================================================
  0   全部检查通过
  2   前置检查不通过（fail-closed；输出原因码）
  3   用法/输入错误（plist 不存在、无法解析、占位符未替换）
====  =========================================================

``--legacy-name`` / ``--legacy-dir`` 供调用方按现行部署补充遗留物黑名单；
默认内置 DAV-2180 直启版之前 wrapper 时代的废弃落点（见 ``LEGACY_*``）。
脚本不写入、不删除、不改名任何文件（DAV-2215 只读纪律同款）。
"""
from __future__ import annotations

import argparse
import json
import os
import plistlib
import pwd
import re
import stat
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

EXIT_OK = 0
EXIT_PREFLIGHT_FAILED = 2
EXIT_USAGE = 3

# --- 原因码 ----------------------------------------------------------------
# 稳定契约：调用方/运维脚本可按码分派；新增只追加，不改含义。
RC_PLACEHOLDER = "LOGPATH_UNRESOLVED_PLACEHOLDER"
RC_KEY_NOT_ABSOLUTE = "LOGPATH_NOT_ABSOLUTE"
RC_PARENT_MISSING = "LOGPATH_PARENT_MISSING"
RC_PARENT_NOT_DIR = "LOGPATH_PARENT_NOT_DIR"
RC_PARENT_NOT_WRITABLE = "LOGPATH_PARENT_NOT_WRITABLE"
RC_TARGET_NOT_REGULAR = "LOGPATH_TARGET_NOT_REGULAR_FILE"
RC_TARGET_PROVENANCE = "LOGPATH_TARGET_PROVENANCE_XATTR"
RC_TARGET_NOT_OWNED = "LOGPATH_TARGET_NOT_OWNED_BY_UID"
RC_TARGET_NOT_APPENDABLE = "LOGPATH_TARGET_NOT_APPENDABLE"
RC_LEGACY_NAME = "LOGPATH_LEGACY_WRAPPER_ARTIFACT"
RC_LEGACY_DIR = "LOGPATH_LEGACY_WRAPPER_DIRECTORY"
RC_OPEN_PROBE = "LOGPATH_OPEN_PROBE_FAILED"

#: 宿主 ``/usr/bin/xattr``；判 provenance 用（macOS Python 无 os.getxattr）。
XATTR_BIN = "/usr/bin/xattr"
PROVENANCE_XATTR = "com.apple.provenance"

# --- 旧版包装脚本（ta-launchd-wrapper.sh 时代）遗留物黑名单 ------------------
# DAV-2180 起新 plist 不再引用 wrapper，wrapper 独有的落点若仍出现在
# Standard*Path 上，即是安装指向了旧时代残留。
LEGACY_NAMES = (
    "wrapper-diagnostic.log",  # wrapper 专属诊断日志（DAV-2180 已随脚本移除）
    "diag-fallback.log",       # wrapper 兜底戳目录内的诊断落点
)
LEGACY_DIRS = (
    ".launchd",  # wrapper 专属戳目录（restart-stamps / manual-bypass）
    "ta-launchd-die-stamps",  # /tmp 兜底戳目录前缀形态
)
LEGACY_DIR_PREFIXES = ("/tmp/ta-launchd-die-stamps-",)

_PLACEHOLDER_RE = re.compile(r"\{\{[A-Z0-9_]+\}\}")


@dataclass
class Finding:
    """单条判定结果。``ok=False`` 即阻断安装。"""

    plist: str
    key: str
    path: str
    ok: bool
    reason_code: str | None = None
    message: str | None = None


@dataclass
class PlistReport:
    plist: str
    label: str | None
    findings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return all(f.ok for f in self.findings)

    @property
    def failures(self) -> list[Finding]:
        return [f for f in self.findings if not f.ok]


def _is_legacy_dir(path: Path, legacy_dirs: tuple[str, ...]) -> str | None:
    for part in path.parts:
        if part in legacy_dirs:
            return part
    for prefix in LEGACY_DIR_PREFIXES:
        if str(path).startswith(prefix):
            return prefix
    return None


def _carries_provenance(path: str) -> bool:
    """目标是否带 ``com.apple.provenance`` 扩展属性。

    口径来源：宿主 ``RESTART.md`` 的 10-18 三次对照——launchd（xpcproxy）创建的
    文件带 ``com.apple.macl``，由 bash/``>>``（wrapper 时代）写的既存文件带
    ``com.apple.provenance``，后者 launchd 打不开 → ``78 EX_CONFIG``。

    macOS 的 Python 没有 ``os.getxattr`` / ``os.listxattr``（实测 AttributeError），
    故走 ``xattr(1)``。**刻意不用 ``open(path, "a")`` 探针**——那会在宿主机留下
    空文件，违背本脚本的零副作用承诺。``xattr -p`` 只读；rc==0 即带。
    """
    try:
        proc = subprocess.run(
            [XATTR_BIN, "-p", PROVENANCE_XATTR, "--", path],
            capture_output=True,
        )
    except OSError:  # 非 macOS / 无 xattr(1)：无法判定即视为带（fail-closed）
        return True
    return proc.returncode == 0


def _check_path(
    *, plist: str, key: str, raw: str, legacy_names: tuple[str, ...], legacy_dirs: tuple[str, ...]
) -> Finding:
    """判定单条 Standard*Path；返回 Finding（不做任何写操作）。"""
    if not raw:
        return Finding(plist, key, raw, False, RC_PLACEHOLDER, f"{key} 为空")
    if _PLACEHOLDER_RE.search(raw):
        return Finding(
            plist, key, raw, False, RC_PLACEHOLDER,
            f"{key} 仍含未替换的占位符 {_PLACEHOLDER_RE.findall(raw)[0]}——"
            "安装前必须先替换（grep -cE '\\{\\{[A-Z_]+\\}\\}' 应为 0）",
        )
    if not os.path.isabs(raw):
        return Finding(
            plist, key, raw, False, RC_KEY_NOT_ABSOLUTE,
            f"{key} 不是绝对路径：{raw}（launchd 工作目录不确定，须写绝对路径）",
        )

    p = Path(raw)

    # 1. 遗留物黑名单：先判文件名，再判目录（两者都指向 wrapper 时代残留）
    if p.name.casefold() in {name.casefold() for name in legacy_names}:
        return Finding(
            plist, key, raw, False, RC_LEGACY_NAME,
            f"{key} 指向旧版包装脚本遗留文件 {p.name}（大小写不敏感匹配）——DAV-2180 起新 plist 不再引用 "
            "wrapper，请改指现行日志（如 uvicorn-<sha>.log）；旧文件已改名留档，不要复用",
        )
    legacy_dir = _is_legacy_dir(p.parent, legacy_dirs)
    if legacy_dir:
        return Finding(
            plist, key, raw, False, RC_LEGACY_DIR,
            f"{key} 落在旧版包装脚本的专属目录 {legacy_dir}——该目录随 DAV-2180 一并废弃，"
            "改指现行日志目录",
        )

    # 2. 父目录：存在 / 是目录 / 可写
    parent = p.parent
    if not parent.exists():
        return Finding(
            plist, key, raw, False, RC_PARENT_MISSING,
            f"{key} 的父目录不存在：{parent}（launchd 不会自建父目录，bootstrap 必 EX_CONFIG）",
        )
    if not parent.is_dir():
        return Finding(
            plist, key, raw, False, RC_PARENT_NOT_DIR,
            f"{key} 的父路径不是目录：{parent}",
        )
    if not os.access(parent, os.W_OK | os.X_OK):
        return Finding(
            plist, key, raw, False, RC_PARENT_NOT_WRITABLE,
            f"{key} 的父目录不可写：{parent}（launchd 打不开该目录内的日志文件）",
        )

    # 3. 目标已存在 → 必须可被 launchd 追加打开
    if p.exists() or p.is_symlink():
        try:
            st = os.lstat(raw)
        except OSError as exc:  # 竞态/权限：lstat 失败即视为不可用（fail-closed）
            return Finding(
                plist, key, raw, False, RC_OPEN_PROBE,
                f"{key} 无法 stat：{exc}",
            )
        if not stat.S_ISREG(st.st_mode):
            return Finding(
                plist, key, raw, False, RC_TARGET_NOT_REGULAR,
                f"{key} 已存在但不是普通文件（mode={stat.filemode(st.st_mode)}）：{raw}"
                "——launchd 只接受普通文件，目录/设备/套接字一律 EX_CONFIG",
            )
        # 3a. provenance 属性判定（放在属主/权限之前：10-18 真因在这里）
        if _carries_provenance(raw):
            return Finding(
                plist, key, raw, False, RC_TARGET_PROVENANCE,
                f"{key} 的目标已存在且带 {PROVENANCE_XATTR} 扩展属性（xattr -l "
                f"{p.name} 可复核）——该属性标记它是由 bash/>> 等终端侧进程写出的，"
                "launchd 的 xpcproxy 不带 ~/Documents 的 TCC 授权、打不开它，"
                "bootstrap 必 EX_CONFIG 78（属主/权限正常也会踩）；"
                "请改名留档后由 launchd 重建该文件，不要复用该路径",
            )
        owner_uid = st.st_uid
        try:
            current_uid = os.getuid()
        except AttributeError:  # pragma: no cover - 非 POSIX
            current_uid = None
        if current_uid is not None and owner_uid != current_uid:
            try:
                owner_name = pwd.getpwuid(owner_uid).pw_name
            except KeyError:
                owner_name = str(owner_uid)
            return Finding(
                plist, key, raw, False, RC_TARGET_NOT_OWNED,
                f"{key} 的属主是 {owner_name}(uid={owner_uid})，当前 uid={current_uid}"
                "——属主异常的日志文件同样会让 launchd 打不开，"
                "请改名留档后另起新文件，勿复用该路径",
            )
        if not os.access(raw, os.W_OK):
            return Finding(
                plist, key, raw, False, RC_TARGET_NOT_APPENDABLE,
                f"{key} 已存在但不可追加写（mode={stat.filemode(st.st_mode)}）：{raw}",
            )
    # 目标不存在即放行：父目录可写时 launchd 可自行创建新文件。
    # 本脚本不预创建、不探测写入，保持对宿主机零副作用。
    return Finding(plist, key, raw, True)


def check_plist(
    plist_path: Path,
    *,
    legacy_names: tuple[str, ...] = LEGACY_NAMES,
    legacy_dirs: tuple[str, ...] = LEGACY_DIRS,
) -> PlistReport:
    """解析 plist 并检查其全部 Standard*Path。解析失败抛 ValueError。"""
    try:
        with plist_path.open("rb") as fh:
            plist_obj = plistlib.load(fh)
    except Exception as exc:  # plistlib 各类解析错误统一上抛给调用方
        raise ValueError(f"plist 解析失败：{exc}") from exc
    if not isinstance(plist_obj, dict):
        raise ValueError("plist 顶层不是 dict")

    report = PlistReport(str(plist_path), plist_obj.get("Label"))
    for key in ("StandardOutPath", "StandardErrorPath"):
        if key not in plist_obj:
            continue
        value = plist_obj[key]
        if not isinstance(value, str):
            report.findings.append(
                Finding(str(plist_path), key, repr(value), False, RC_PLACEHOLDER,
                        f"{key} 不是字符串：{value!r}")
            )
            continue
        report.findings.append(
            _check_path(
                plist=str(plist_path), key=key, raw=value,
                legacy_names=legacy_names, legacy_dirs=legacy_dirs,
            )
        )
    if not report.findings:
        report.findings.append(
            Finding(str(plist_path), "StandardOutPath", "", True,
                    message="plist 未设置 StandardOutPath/StandardErrorPath，无需日志前置检查")
        )
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="DAV-2223 launchd plist 日志路径前置检查（fail-closed，防 EX_CONFIG 78）"
    )
    parser.add_argument("plists", nargs="+", help="待检查的 plist 文件（占位符须已替换）")
    parser.add_argument("--legacy-name", action="append", default=[],
                        help="追加旧版包装脚本遗留文件名黑名单（可重复）")
    parser.add_argument("--legacy-dir", action="append", default=[],
                        help="追加旧版包装脚本遗留目录名黑名单（可重复）")
    parser.add_argument("--json", action="store_true", help="输出 JSON（便于门禁脚本消费）")
    try:
        args = parser.parse_args(argv)
    except SystemExit as exc:
        # argparse 用法错误自带 exit 2，与 EXIT_PREFLIGHT_FAILED 撞码；此处统一归 3。
        if not exc.code:  # --help 正常退出
            raise
        return EXIT_USAGE

    legacy_names = LEGACY_NAMES + tuple(args.legacy_name)
    legacy_dirs = LEGACY_DIRS + tuple(args.legacy_dir)

    reports: list[PlistReport] = []
    usage_errors: list[str] = []
    for raw in args.plists:
        p = Path(raw)
        if not p.is_file():
            usage_errors.append(f"{raw}：文件不存在")
            continue
        try:
            reports.append(check_plist(p, legacy_names=legacy_names, legacy_dirs=legacy_dirs))
        except ValueError as exc:
            usage_errors.append(f"{raw}：{exc}")

    if args.json:
        print(json.dumps(
            {
                "ok": not usage_errors and all(r.ok for r in reports),
                "reports": [
                    {
                        "plist": r.plist, "label": r.label,
                        "ok": r.ok,
                        "findings": [asdict(f) for f in r.findings],
                    }
                    for r in reports
                ],
                "usage_errors": usage_errors,
            },
            ensure_ascii=False, indent=2,
        ))
    else:
        if usage_errors:
            for msg in usage_errors:
                print(f"ERROR {msg}", file=sys.stderr)
        for r in reports:
            print(f"[{r.plist}] label={r.label}")
            for f in r.findings:
                mark = "PASS" if f.ok else "FAIL"
                if f.ok:
                    print(f"  {mark} {f.key} = {f.path}" + (f" — {f.message}" if f.message else ""))
                else:
                    print(f"  {mark} [{f.reason_code}] {f.key} = {f.path}\n       {f.message}")

    if usage_errors:
        print("前置检查未执行完毕（输入错误），fail-closed：拒绝安装。", file=sys.stderr)
        return EXIT_USAGE
    failed = [(r, f) for r in reports for f in r.failures]
    if failed:
        print("", file=sys.stderr)
        for r, f in failed:
            print(f"前置检查不通过 [{f.reason_code}] {r.plist} {f.key}", file=sys.stderr)
        print(
            "fail-closed：日志路径不满足安装条件，拒绝 launchctl bootstrap"
            "（否则 launchd 会以 EX_CONFIG 78 拒绝装载）。",
            file=sys.stderr,
        )
        return EXIT_PREFLIGHT_FAILED
    if not args.json:
        print("前置检查通过：全部日志路径可用且非终端侧遗留（无 provenance 扩展属性），可继续安装。")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
