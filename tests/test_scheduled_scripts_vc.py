"""DAV-2111: minimal tests for the version-controlled daily batch / B1 monitor scripts.

Hard rule for this file (DAV-2111 incident, 2026-10-15): no test may reach the
live analysis service on 127.0.0.1:8000. An earlier revision of this file tried to
stub ``run_daily.BASE`` by monkeypatching a module constant from outside; the patch
did not take effect and the test submitted two real 24-symbol batches to production.

Two independent protections, each of which fails loudly rather than silently:

1. ``run_daily.py`` reads its base URL from ``--base`` / ``$TA_DAILY_BASE`` at
   runtime, so the stub is actually dialled -- and that is asserted by having the
   stub record the request. Stubbing is therefore verified by the test's own
   success criterion, not assumed.
2. A ``sitecustomize`` shim loaded into every child interpreter denies any
   connection to port 8000. Because a guard that silently fails to load is exactly
   how the incident happened, ``test_guard_itself_works`` is a positive control: it
   proves the shim is really active in child interpreters before the other tests
   rely on it.
"""
from __future__ import annotations

import http.server
import json
import os
import socket
import socketserver
import subprocess
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
RUN_DAILY = REPO_ROOT / "work" / "daily-batch" / "run_daily.py"
B1_MONITOR = REPO_ROOT / "work" / "b1-monitor" / "b1_daily_monitor.py"

PRODUCTION_PORT = 8000

# Explicit script revisions, so a silent edit is caught in review. DAV-2322
# updates the daily script to resolve its calendar from the running interpreter.
EXPECTED_SHA256 = {
    "work/b1-monitor/b1_daily_monitor.py":
        "386f277ac2ddd112de71bf6173a62a101d85370664344c971c3eeef6e9db6625",
    "work/daily-batch/run_daily.py":
        "0762ad2d9b0f60b190fba7d4f7eee4dc7b7d6157ae8b9a5385b1ba27d266eb73",
}

# b1_daily_monitor.py is the verbatim production copy and must stay so.
B1_MONITOR_SHA256 = "386f277ac2ddd112de71bf6173a62a101d85370664344c971c3eeef6e9db6625"

SITECUSTOMIZE = '''
import os
import socket

_BLOCKED_PORT = int(os.environ["DAV2111_BLOCKED_PORT"])
_real_connect = socket.socket.connect
_real_connect_ex = socket.socket.connect_ex


class Blocked(Exception):
    pass


def _check(address):
    port = address[1] if isinstance(address, tuple) else None
    if port == _BLOCKED_PORT:
        raise Blocked("refused: port %d is the live service" % _BLOCKED_PORT)


def _connect(self, address, *a, **kw):
    _check(address)
    return _real_connect(self, address, *a, **kw)


def _connect_ex(self, address, *a, **kw):
    _check(address)
    return _real_connect_ex(self, address, *a, **kw)


socket.socket.connect = _connect
socket.socket.connect_ex = _connect_ex
'''

CONNECT_PROBE = """
import socket, sys
try:
    socket.socket().connect(("127.0.0.1", int(sys.argv[1])))
except OSError as e:
    print("refused", type(e).__name__)
    sys.exit(1)
except Exception as e:
    print("refused", type(e).__name__)
    sys.exit(1)
print("connected")
sys.exit(0)
"""


def sha256(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="session")
def guarded_env(tmp_path_factory):
    """Child-interpreter env whose port 8000 is unconnectable by construction."""
    guard_dir = tmp_path_factory.mktemp("dav2111-guard")
    (guard_dir / "sitecustomize.py").write_text(SITECUSTOMIZE)
    env = dict(os.environ)
    # Prepend our dir, drop the repo's PYTHONPATH: the operator's environment
    # must not be able to shadow the guard or reintroduce a proxy.
    env["PYTHONPATH"] = str(guard_dir)
    for var in ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        env.pop(var, None)
    env["DAV2111_BLOCKED_PORT"] = str(PRODUCTION_PORT)
    calendar = guard_dir / "calendar.json"
    calendar.write_text(json.dumps(["20261015", "20261016"]))
    env["TA_DAILY_CALENDAR"] = str(calendar)
    return env


def run_script(guarded_env, argv, timeout=120):
    assert str(PRODUCTION_PORT) not in " ".join(str(a) for a in argv), \
        "test argv mentions the production port"
    return subprocess.run([sys.executable, *argv], capture_output=True,
                          text=True, timeout=timeout, env=guarded_env)


# ---------------------------------------------------------------------------
# Guard integrity: positive controls, so we never again trust an unverified guard
# ---------------------------------------------------------------------------

def test_guard_itself_blocks_the_production_port(guarded_env, tmp_path):
    """Positive control: the shim really loads and really refuses port 8000."""
    probe = tmp_path / "probe.py"
    probe.write_text(CONNECT_PROBE)
    result = subprocess.run([sys.executable, str(probe), str(PRODUCTION_PORT)],
                            capture_output=True, text=True, timeout=30, env=guarded_env)
    assert result.returncode != 0, "guard failed to block the production port"
    assert "refused" in result.stdout


def test_guard_still_allows_the_stub_port(guarded_env, stub_service, tmp_path):
    """Positive control: the guard must not be so broad that it blocks the stub."""
    probe = tmp_path / "probe.py"
    probe.write_text(CONNECT_PROBE)
    port = stub_service.rsplit(":", 1)[1]
    result = subprocess.run([sys.executable, str(probe), port],
                            capture_output=True, text=True, timeout=30, env=guarded_env)
    assert result.stdout.strip() == "connected"


# ---------------------------------------------------------------------------
# Content pins
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("path", sorted(EXPECTED_SHA256))
def test_script_content_pinned(path: str):
    assert sha256(REPO_ROOT / path) == EXPECTED_SHA256[path]


def test_b1_monitor_content_unchanged():
    """b1_daily_monitor.py is still the verbatim production copy."""
    assert sha256(B1_MONITOR) == B1_MONITOR_SHA256


# ---------------------------------------------------------------------------
# Stub service
# ---------------------------------------------------------------------------

class _StubHandler(http.server.BaseHTTPRequestHandler):
    def _reply(self, payload, status=200):
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 - stdlib naming
        self.server.seen.append(("GET", self.path))
        if self.path == "/healthz":
            self._reply({"status": self.server.health})
        else:
            self._reply({"detail": "not found"}, status=404)

    def do_POST(self):  # noqa: N802 - stdlib naming
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.server.seen.append(("POST", self.path))
        self._reply({"detail": "not found"}, status=404)

    def log_message(self, *args):
        pass


class _StubServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


@pytest.fixture(scope="session")
def stub_server():
    """A controllable stand-in for the analysis service on an ephemeral port."""
    with _StubServer(("127.0.0.1", 0), _StubHandler) as server:
        server.seen = []
        server.health = "not-ok"
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server
        finally:
            server.shutdown()
            thread.join(timeout=5)


@pytest.fixture(scope="session")
def stub_service(stub_server):
    return f"http://127.0.0.1:{stub_server.server_address[1]}"


@pytest.fixture
def stub(stub_server):
    stub_server.seen.clear()
    stub_server.health = "not-ok"
    return stub_server


# ---------------------------------------------------------------------------
# Import / argparse
# ---------------------------------------------------------------------------

def test_run_daily_imports(guarded_env):
    result = run_script(guarded_env, [
        "-c",
        "import importlib.util as u;"
        "s=u.spec_from_file_location('rd', %r);"
        "m=u.module_from_spec(s); s.loader.exec_module(m);"
        "print(len(m.GROUP_A), len(m.GROUP_B), m.BASE)" % str(RUN_DAILY)])
    assert result.returncode == 0, result.stderr
    assert result.stdout.split()[:2] == ["24", "24"]


def test_b1_monitor_imports(guarded_env):
    result = run_script(guarded_env, [
        "-c",
        "import importlib.util as u;"
        "s=u.spec_from_file_location('b1', %r);"
        "m=u.module_from_spec(s); s.loader.exec_module(m);"
        "print(callable(m.read_database), callable(m.summarize))" % str(B1_MONITOR)])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "True True"


@pytest.mark.parametrize("path", [RUN_DAILY, B1_MONITOR])
def test_help_exits_zero(guarded_env, path):
    assert run_script(guarded_env, [str(path), "--help"]).returncode == 0


def test_b1_argparse_rejects_both_inputs(guarded_env, tmp_path):
    db = tmp_path / "x.db"
    db.touch()
    result = run_script(guarded_env, [str(B1_MONITOR), "--db", str(db),
                                      "--input-json", "[]"])
    assert result.returncode != 0


def test_b1_argparse_rejects_malformed_ids(guarded_env, tmp_path):
    db = tmp_path / "x.db"
    db.touch()
    ids = tmp_path / "ids.json"
    ids.write_text('["a", 1]')
    result = run_script(guarded_env, [str(B1_MONITOR), "--db", str(db),
                                      "--ids", str(ids)])
    assert result.returncode != 0


def test_b1_self_test_is_offline(guarded_env):
    """--self-test runs the summary logic on synthetic records only."""
    result = run_script(guarded_env, [str(B1_MONITOR), "--self-test"])
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["synthetic_checks"] == "passed"
    assert payload["model_calls"] == payload["provider_calls"] == 0


# ---------------------------------------------------------------------------
# run_daily argument handling and exit codes, always against the stub
# ---------------------------------------------------------------------------

def _fake_root(tmp_path: Path) -> Path:
    """Self-contained data root; guarded_env supplies the calendar separately."""
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    (root / "data" / "tradingagents.db").touch()
    bench = root / "work/hist-bench-20260926"
    bench.mkdir(parents=True)
    (bench / "run.py").write_text('EMAIL = "batch@example.test"\n')
    return root


def test_dry_run_makes_no_request(guarded_env, stub, tmp_path):
    root = _fake_root(tmp_path)
    result = run_script(guarded_env, [
        str(RUN_DAILY), "--dry-run", "--date", "2026-10-15",
        "--base", "http://127.0.0.1:1", "--root", str(root)])
    assert result.returncode == 0, result.stderr
    assert "dry-run:" in result.stdout
    assert stub.seen == [], f"dry-run contacted the service: {stub.seen}"


def test_healthz_failure_exits_nonzero(guarded_env, stub, stub_service, tmp_path):
    """DAV-2111: a failing health check must abort with a non-zero exit code."""
    root = _fake_root(tmp_path)
    result = run_script(guarded_env, [
        str(RUN_DAILY), "--date", "2026-10-15",
        "--base", stub_service, "--root", str(root)])
    assert result.returncode != 0, f"health check failure exited 0\n{result.stdout}"
    assert result.returncode == 3, result.stdout
    assert "ABORT healthz not ok" in result.stdout


def test_unreachable_service_exits_nonzero(guarded_env, stub, tmp_path):
    """A refused connection is a health-check failure, not a silent success."""
    root = _fake_root(tmp_path)
    result = run_script(guarded_env, [
        str(RUN_DAILY), "--date", "2026-10-15",
        "--base", "http://127.0.0.1:1", "--root", str(root)])
    assert result.returncode == 3, result.stdout
    assert "ABORT healthz unreachable" in result.stdout


def test_stub_was_actually_reached(guarded_env, stub, stub_service, tmp_path):
    """Proves the stub is dialled for real -- stubbing is observed, not assumed."""
    root = _fake_root(tmp_path)
    run_script(guarded_env, [
        str(RUN_DAILY), "--date", "2026-10-15",
        "--base", stub_service, "--root", str(root)])
    assert ("GET", "/healthz") in stub.seen, f"stub never received a request: {stub.seen}"


def test_base_is_configurable(guarded_env, stub, stub_service, tmp_path):
    """--base overrides the default, which stays pinned to production."""
    root = _fake_root(tmp_path)
    default = run_script(guarded_env, [
        "-c",
        "import importlib.util as u;"
        "s=u.spec_from_file_location('rd', %r);"
        "m=u.module_from_spec(s); s.loader.exec_module(m);"
        "print(m.BASE)" % str(RUN_DAILY)])
    assert default.stdout.strip() == "http://127.0.0.1:8000"

    result = run_script(guarded_env, [
        str(RUN_DAILY), "--date", "2026-10-15",
        "--base", stub_service, "--root", str(root)])
    assert "ABORT healthz not ok" in result.stdout


def test_unrecognized_argument_exits_nonzero(guarded_env, tmp_path):
    result = run_script(guarded_env, [str(RUN_DAILY), "--not-a-flag"])
    assert result.returncode != 0
    assert "unrecognized argument" in result.stderr


def test_missing_flag_value_exits_nonzero(guarded_env, tmp_path):
    result = run_script(guarded_env, [str(RUN_DAILY), "--base"])
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# DAV-2115 review findings
# ---------------------------------------------------------------------------

def test_b1_carries_the_b6d_annotation():
    """🔴-1: the raw result_data read must carry the B-6d annotation and wire
    the runtime guard, or the trunk gate test fails on this file."""
    source = B1_MONITOR.read_text(encoding="utf-8")
    assert "压缩存储前适用" in source
    assert "require_plaintext_complete" in source


REPORTS_DDL = (
    "CREATE TABLE reports (id TEXT PRIMARY KEY, user_id TEXT, trade_date TEXT, "
    "status TEXT, result_data TEXT, result_data_zst BLOB, created_at TEXT)"
)


def test_b1_aborts_on_a_compressed_only_database(guarded_env, tmp_path):
    """🔴-1 behaviour: a P5b-cleared row must abort, not silently read NULL."""
    import sqlite3

    db = tmp_path / "cleared.db"
    con = sqlite3.connect(db)
    con.execute(REPORTS_DDL)
    con.execute("INSERT INTO reports (id, user_id, trade_date, status, result_data, "
                "result_data_zst, created_at) "
                "VALUES ('r1', ?, '2026-10-08', 'completed', NULL, X'00', '2026-10-08 16:37:00')",
                ("429163f7-50b6-4982-8bdf-96ae99506843",))
    con.commit()
    con.close()

    result = run_script(guarded_env, [str(B1_MONITOR), "--db", str(db)])
    assert result.returncode == 2, result.stdout
    assert "compressed result_data" in result.stderr


def test_b1_reads_rows_of_the_fixed_account_only(guarded_env, tmp_path):
    """🟡-3: read_database returns user_id and enforces the account filter."""
    import sqlite3

    db = tmp_path / "two-accounts.db"
    con = sqlite3.connect(db)
    con.execute(REPORTS_DDL)
    payload = '{"forecast": {"contract": "b1.v1", "status": "valid", "p_rel_t10": 60}}'
    con.execute("INSERT INTO reports (id, user_id, trade_date, status, result_data, "
                "result_data_zst, created_at) "
                "VALUES ('mine', ?, '2026-10-08', 'completed', ?, NULL, '2026-10-08 16:37:00')",
                ("429163f7-50b6-4982-8bdf-96ae99506843", payload))
    con.execute("INSERT INTO reports (id, user_id, trade_date, status, result_data, "
                "result_data_zst, created_at) "
                "VALUES ('theirs', ?, '2026-10-08', 'completed', ?, NULL, '2026-10-08 16:37:01')",
                ("00000000-0000-0000-0000-000000000000", payload))
    con.commit()
    con.close()

    import importlib.util
    spec = importlib.util.spec_from_file_location("b1", B1_MONITOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    records = module.read_database(str(db))
    assert [r["id"] for r in records] == ["mine"]
    assert all("user_id" not in r or r.get("user_id") in (None, module.ACCOUNT)
               for r in records)


def test_b1_input_json_requires_the_fixed_account(guarded_env, tmp_path):
    """🟡-3: --input-json records must carry user_id and be filtered on it."""
    mine = {"id": "a", "trade_date": "2026-10-08", "status": "completed",
            "user_id": "429163f7-50b6-4982-8bdf-96ae99506843",
            "result_data": {"forecast": {"contract": "b1.v1", "status": "valid",
                                         "p_rel_t10": 60}}}
    theirs = dict(mine, id="b", user_id="00000000-0000-0000-0000-000000000000")

    only_mine = tmp_path / "mine.json"
    only_mine.write_text(json.dumps([mine]))
    result = run_script(guarded_env, [str(B1_MONITOR), "--input-json", str(only_mine)])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["b1_slices"] == 1

    mixed = tmp_path / "mixed.json"
    mixed.write_text(json.dumps([mine, theirs]))
    result = run_script(guarded_env, [str(B1_MONITOR), "--input-json", str(mixed)])
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["b1_slices"] == 1, "foreign-account record leaked in"


def test_account_resolution_prefers_explicit_config(guarded_env):
    """🟡-4: --account / $TA_DAILY_ACCOUNT wins over the legacy bench file."""
    code = (
        "import importlib.util as u;"
        "s=u.spec_from_file_location('rd', %r);"
        "m=u.module_from_spec(s); s.loader.exec_module(m);"
        "print(m.resolve_account('ops@example.test', '/nonexistent'))"
        % str(RUN_DAILY))
    result = run_script(guarded_env, ["-c", code])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "ops@example.test"


def test_account_fallback_reads_the_legacy_bench_file(guarded_env, tmp_path):
    """🟡-4: with no explicit account the legacy path still resolves."""
    root = _fake_root(tmp_path)
    code = (
        "import importlib.util as u;"
        "s=u.spec_from_file_location('rd', %r);"
        "m=u.module_from_spec(s); s.loader.exec_module(m);"
        "print(m.resolve_account(None, %r))" % (str(RUN_DAILY), str(root)))
    result = run_script(guarded_env, ["-c", code])
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "batch@example.test"


def test_account_resolution_fails_loudly(guarded_env):
    """🟡-4: a missing/!parsable account file must raise, not yield an empty value."""
    code = (
        "import importlib.util as u;"
        "s=u.spec_from_file_location('rd', %r);"
        "m=u.module_from_spec(s); s.loader.exec_module(m);"
        "\ntry:\n"
        "    m.resolve_account(None, '/nonexistent-root')\n"
        "except ValueError as e:\n"
        "    print('raised'); raise SystemExit(0)\n"
        "raise SystemExit(1)" % str(RUN_DAILY))
    result = run_script(guarded_env, ["-c", code])
    assert result.returncode == 0, result.stdout + result.stderr
    assert "raised" in result.stdout


def test_missing_account_exits_nonzero_before_any_analysis(guarded_env, stub, stub_service, tmp_path):
    """🟡-4 end-to-end: no account and no legacy file => exit 3, nothing submitted."""
    root = _fake_root(tmp_path)
    (root / "work/hist-bench-20260926/run.py").unlink()
    stub.health = "ok"  # get past the health gate so the account step is reached
    result = run_script(guarded_env, [
        str(RUN_DAILY), "--date", "2026-10-15",
        "--base", stub_service, "--root", str(root)])
    assert result.returncode == 3, result.stdout
    assert "ABORT login failed" in result.stdout
    assert "TA_DAILY_ACCOUNT" in result.stdout, "the error does not say how to fix it"
    assert not [entry for entry in stub.seen if entry[0] == "POST"], \
        f"a request was issued despite the account failure: {stub.seen}"