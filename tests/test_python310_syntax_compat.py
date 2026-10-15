"""Python 3.10 compatibility guard (DAV-2110).

Two regression checks over every ``*.py`` file tracked by git:

1. ``compile()`` every tracked file under the current interpreter. Production
   runs on Python 3.10 (``.venv310``); a file that fails to compile here would
   be a hard production break, so this catches syntax that only parses on
   newer interpreters (e.g. ``except*``, PEP 695 type-parameter syntax,
   ``f-string`` grammar changes from 3.12).
2. Ban a fixed list of Python 3.11+ names/usages via AST, with an EMPTY
   whitelist: ``datetime.UTC`` (the regression that motivated this card — a
   formatter once injected ``from datetime import UTC``, which raises
   ImportError on 3.10) plus other stdlib names first introduced in 3.11/3.12
   that have no 3.10 equivalent.

The banned-name table is deliberately small and version-pinned: every entry
is a name that does not exist in the corresponding 3.10 stdlib module, so a
hit is never a false positive.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Python 3.11+/3.12+ stdlib names with no 3.10 equivalent.
# Each entry: (module, name, introduced_in). Detected both as
# ``from <module> import <name>`` and as ``<module>.<name>`` attribute access
# (where the object is the module itself or the conventional alias).
BANNED_FROM_IMPORTS = {
    # module -> {name: version that introduced it}
    "datetime": {"UTC": "3.11"},
    "enum": {"StrEnum": "3.11", "ReprEnum": "3.11", "EnumCheck": "3.11"},
    "typing": {
        "Self": "3.11",
        "LiteralString": "3.11",
        "Never": "3.11",
        "NotRequired": "3.11",
        "assert_never": "3.11",
        "dataclass_transform": "3.11",
        "TypeVarTuple": "3.11",
        "Unpack": "3.12",
        "get_overloads": "3.11",
        "clear_overloads": "3.11",
    },
    "itertools": {"batched": "3.12"},
    "asyncio": {"timeout": "3.11", "TaskGroup": "3.11"},
    "contextlib": {"chdir": "3.11"},
    "inspect": {"getasyncgenstate": "3.11", "getasyncgenlocals": "3.11"},
    "operator": {"call": "3.11"},
    "re": {"NOFLAG": "3.11"},
}

# Modules that only exist on 3.11+: any import of them is banned unless the
# import is guarded by ``try: ... except ImportError`` (the documented
# ``tomllib`` -> ``tomli`` fallback pattern).
BANNED_MODULES = {
    "tomllib": "3.11",
}

# Attribute access bans: ``<obj>.<attr>`` where attr is a banned name.
# Keyed by the attribute name; checked against any Attribute node whose attr
# matches — the common offender is ``datetime.UTC`` where ``datetime`` is the
# module. To keep this precise we key on (receiver_name, attr) pairs instead.
BANNED_ATTRS = {
    # (receiver, attribute) -> introduced_in
    ("datetime", "UTC"): "3.11",
    ("asyncio", "TaskGroup"): "3.11",
    ("asyncio", "timeout"): "3.11",
    ("enum", "StrEnum"): "3.11",
    ("enum", "ReprEnum"): "3.11",
    ("enum", "EnumCheck"): "3.11",
    ("itertools", "batched"): "3.12",
    ("typing", "Self"): "3.11",
    ("typing", "LiteralString"): "3.11",
    ("typing", "Never"): "3.11",
    ("typing", "NotRequired"): "3.11",
    ("typing", "TypeVarTuple"): "3.11",
    ("typing", "Unpack"): "3.12",
    ("typing", "assert_never"): "3.11",
    ("typing", "dataclass_transform"): "3.11",
    ("contextlib", "chdir"): "3.11",
}


def _tracked_py_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted(REPO_ROOT / line for line in out.splitlines() if line.strip())


def _is_import_guarded(tree: ast.Module, node: ast.stmt) -> bool:
    """Return True if ``node`` sits inside a ``try`` body whose handler is
    ``except ImportError`` (i.e. the ``import tomllib`` / ``except ImportError:
    import tomli`` fallback idiom)."""
    for parent in ast.walk(tree):
        if not isinstance(parent, ast.Try):
            continue
        if node in parent.body and any(
            h.type is not None
            and isinstance(h.type, ast.Name)
            and h.type.id in {"ImportError", "ModuleNotFoundError"}
            for h in parent.handlers
        ):
            return True
    return False


def _scan_file(path: Path) -> list[str]:
    """Return a list of human-readable violations for one file."""
    source = path.read_bytes()
    rel = path.relative_to(REPO_ROOT)
    findings: list[str] = []

    tree = ast.parse(source, filename=str(rel))

    for node in ast.walk(tree):
        # --- banned module imports (tomllib etc.) unless ImportError-guarded
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".", 1)[0]
                if root in BANNED_MODULES and not _is_import_guarded(tree, node):
                    findings.append(
                        f"{rel}:{node.lineno}: `import {alias.name}` requires "
                        f"Python {BANNED_MODULES[root]}+ (no try/except ImportError guard)"
                    )
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            root = mod.split(".", 1)[0]
            if root in BANNED_MODULES and not _is_import_guarded(tree, node):
                findings.append(
                    f"{rel}:{node.lineno}: `from {mod} import ...` requires "
                    f"Python {BANNED_MODULES[root]}+ (no try/except ImportError guard)"
                )
                continue
            banned_names = BANNED_FROM_IMPORTS.get(mod)
            if banned_names:
                for alias in node.names:
                    if alias.name in banned_names:
                        findings.append(
                            f"{rel}:{node.lineno}: `from {mod} import "
                            f"{alias.name}` — `{mod}.{alias.name}` only exists on "
                            f"Python {banned_names[alias.name]}+"
                        )

        # --- banned attribute access: datetime.UTC etc.
        if isinstance(node, ast.Attribute):
            recv = node.value
            recv_name = recv.id if isinstance(recv, ast.Name) else None
            if recv_name is not None:
                key = (recv_name, node.attr)
                if key in BANNED_ATTRS:
                    findings.append(
                        f"{rel}:{node.lineno}: `{recv_name}.{node.attr}` only "
                        f"exists on Python {BANNED_ATTRS[key]}+"
                    )

    return findings


def test_all_tracked_py_files_compile_under_current_interpreter():
    """compile() every git-tracked .py file; production interpreter is 3.10."""
    files = _tracked_py_files()
    assert files, "git ls-files returned no .py files"

    failures = []
    for path in files:
        try:
            source = path.read_bytes()
            compile(source, str(path.relative_to(REPO_ROOT)), "exec")
        except SyntaxError as exc:
            failures.append(
                f"{path.relative_to(REPO_ROOT)}:{exc.lineno}: {exc.msg}"
            )
        except UnicodeDecodeError as exc:
            failures.append(f"{path.relative_to(REPO_ROOT)}: not decodable: {exc}")

    assert not failures, (
        f"{len(failures)} file(s) failed to compile under "
        f"{sys.version.split()[0]}:\n" + "\n".join(failures[:50])
    )


def test_no_python311_plus_names_empty_whitelist():
    """Ban Python 3.11+ stdlib names that do not exist on 3.10.

    Whitelist is EMPTY by requirement. The only escape is the structural one:
    ``try/except ImportError`` fallback imports (e.g. tomllib -> tomli) are
    allowed because they degrade correctly on 3.10.
    """
    files = _tracked_py_files()
    findings: list[str] = []
    for path in files:
        try:
            findings.extend(_scan_file(path))
        except SyntaxError:
            # compile() check reports syntax errors; skip double-reporting.
            continue

    assert not findings, (
        f"{len(findings)} Python 3.11+ usage(s) found (whitelist is EMPTY):\n"
        + "\n".join(findings[:50])
    )
