#!/usr/bin/env python3
"""DAV-2463: unattended daily T+5 backfill ("补评估") runner.

Wraps ``scripts/backfill_tplus5_shadow.run_backfill`` with the safety gates the
chief signed for an automatic job. It never installs anything itself; the
LaunchAgent draft lives in ``releases/launchd/``.

Every invocation writes ONE small run record (JSON) into ``<run_dir>/records``;
the pre-export rollback files, the row audit journal and the retry state live
in the same directory. ``run_dir`` is configurable and defaults to the external
disk; if the volume is not mounted the run fails outright (no internal-disk
fallback, nothing is written there).

Gates (any of them stops the whole run and raises an alarm file):
  * ``/healthz`` active_analysis_count != 0 / service unhealthy -> skipped+alarm
  * provider_rejected > 5% of provider requests
  * a stored ``due_and_evaluated`` unit would have its evaluation rewritten
  * rows to write > newly matured units + data_missing units retried
    + suspension units rechecked
  * ``database is locked`` on a row write (no retry)
  * peak RSS > 6 GiB (also checked once more at the end: status ``aborted``,
    exit 1, never ``success``), or wall time > the configured cap
Non-trading days are skipped without an alarm (a ``skipped`` record is written).
Credentials come from the env file only and are never logged.
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import resource
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import backfill_tplus5_shadow as core  # noqa: E402

RECORD_SCHEMA = "t5-auto-run/1"
DEFAULT_RUN_DIR = "/Volumes/waizhiyingpan/ta-t5-auto"
DEFAULT_WATCH_DIR = "~/ta-watch"
DEFAULT_ENV_FILE = "/Users/davidliu/Documents/TradingAgents-AShare/.env"
DEFAULT_MAX_RSS_GIB = 6.0
DEFAULT_MAX_RETRY_ATTEMPTS = 5
MAX_REJECT_RATIO = 0.05
STALE_TRADING_DAYS = 1

EXIT_OK = 0
EXIT_ABORTED = 1
EXIT_CONFIG = 2
EXIT_SKIPPED_ALARM = 10


class ExternalDiskError(RuntimeError):
    pass


def require_external_volume(path):
    """Fail unless ``path`` sits on a mounted ``/Volumes/<name>`` volume.

    Deliberately never creates the volume root: an unmounted external disk
    must not turn into a directory on the internal disk.
    """
    parts = Path(path).expanduser().parts
    if len(parts) < 4 or parts[:2] != ("/", "Volumes"):
        raise ExternalDiskError("run_dir_not_on_external_volume")
    volume = Path(*parts[:3])
    if not volume.is_dir() or not os.path.ismount(volume):
        raise ExternalDiskError("external_volume_not_mounted")


def _peak_rss_bytes():
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak if sys.platform == "darwin" else peak * 1024


def _atomic_write_json(path, payload):
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def raise_alert(watch_dir, reason, detail):
    """Watch event: ``ALERT-t5auto-<utc>-<reason>-<pid>.log`` (sentinel convention).

    ``detail`` carries controlled strings, counts and report ids only.
    """
    try:
        watch = Path(watch_dir).expanduser()
        watch.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
        lines = ["source=t5-auto-runner", "time_utc=" + stamp, "reason=" + reason]
        lines += [f"{key}={json.dumps(value, ensure_ascii=False)}" for key, value in sorted(detail.items())]
        (watch / f"ALERT-t5auto-{stamp}-{reason}-{os.getpid()}.log").write_text(
            "\n".join(lines) + "\n", encoding="utf-8")
        return True
    except OSError:
        print("t5-auto: cannot write alert " + reason, file=sys.stderr)
        return False


# ---------------------------------------------------------------------------
# Run records, retry state, staleness
# ---------------------------------------------------------------------------

def records_dir(run_dir):
    return Path(run_dir) / "records"


def load_records(run_dir):
    records = []
    folder = records_dir(run_dir)
    if not folder.is_dir():
        return records
    for path in sorted(folder.glob("run-*.json")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(record, dict) and record.get("schema") == RECORD_SCHEMA:
            records.append(record)
    return records


def last_success_date(run_dir):
    days = [r["trading_day"] for r in load_records(run_dir)
            if r.get("status") == "success" and r.get("trading_day")]
    return max(days) if days else None


def trading_days_since_last_success(run_dir, calendar, today):
    """Trading days in (last success, today]; None when there is no success yet.

    One missed night is tolerated; ``> STALE_TRADING_DAYS`` is stale.
    """
    last = last_success_date(run_dir)
    if last is None:
        return None, None
    lag = sum(1 for day in calendar if last < day.isoformat() <= today.isoformat())
    return lag, last


def _retry_state_path(run_dir):
    return Path(run_dir) / "retry-state.json"


def load_retry_state(run_dir):
    try:
        data = json.loads(_retry_state_path(run_dir).read_text(encoding="utf-8"))
        units = data.get("units")
        return units if isinstance(units, dict) else {}
    except (OSError, ValueError):
        return {}


def _unit_key(report_id, horizon):
    return f"{report_id}|{horizon}"


class AutoPolicy:
    """Unit-level work policy for the unattended run.

    * a stored ``due_and_evaluated`` unit is immutable: never refetched
      (``recheck_evaluated=True`` lifts this so the rewrite gate can audit);
    * a ``data_missing`` unit is retried until ``max_attempts`` is reached, then
      listed in the alarm instead of being fetched again;
    * a never-measured unit that is still pending is not stamped, so a fresh
      row only changes when it actually matures.
    """

    def __init__(self, retry_state, max_attempts, recheck_evaluated=False):
        self.retry_state = retry_state
        self.max_attempts = max_attempts
        self.recheck_evaluated = recheck_evaluated
        self.skipped = set()
        self.skipped_evaluated = 0
        self.retry_exceeded = []

    def skip_fetch(self, report, horizon, unit):
        status = core._unit_status(unit)
        key = (report.get("id"), horizon)
        if status == core.T5_EVALUATED and unit.get("t_plus_5_evaluated") is True \
                and not self.recheck_evaluated:
            self.skipped_evaluated += 1
            self.skipped.add(key)
            return True
        if status == "data_missing":
            entry = self.retry_state.get(_unit_key(*key)) or {}
            if entry.get("attempts", 0) >= self.max_attempts:
                self.retry_exceeded.append({"report_id": key[0], "horizon": horizon,
                                            "attempts": entry["attempts"],
                                            "reason": entry.get("reason")})
                self.skipped.add(key)
                return True
        return False

    def skip_stamp(self, report, horizon, unit, fields):
        return fields["t_plus_5_status"] == "pending_due" and not core._unit_status(unit)

    def was_skipped(self, report_id, horizon):
        return (report_id, horizon) in self.skipped


def next_retry_state(old_state, policy, missing_units, today_iso):
    """Units computed this run and still missing +1 attempt; resolved ones drop."""
    state = {}
    for key, entry in old_state.items():
        report_id, _, horizon = key.rpartition("|")
        if (report_id, horizon) in policy.skipped:
            state[key] = entry
    for unit in missing_units:
        key = _unit_key(unit["report_id"], unit["horizon"])
        previous = old_state.get(key) or {}
        state[key] = {"attempts": previous.get("attempts", 0) + 1,
                      "reason": unit.get("reason"),
                      "first_seen": previous.get("first_seen", today_iso),
                      "last_attempt": today_iso}
    return state


def _code_version():
    try:
        return subprocess.run(["git", "-C", str(ROOT), "rev-parse", "HEAD"], capture_output=True,
                              text=True, timeout=5, check=True).stdout.strip() or None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def _build_hook(*, max_seconds, max_rss_bytes, clock, started, rss_reader):
    def hook(phase, count):
        elapsed = clock() - started
        if elapsed > max_seconds:
            raise core.RunAbort("duration_limit_exceeded", {
                "phase": phase, "elapsed_seconds": round(elapsed, 1), "limit_seconds": max_seconds})
        rss = rss_reader()
        if rss > max_rss_bytes:
            raise core.RunAbort("memory_limit_exceeded", {
                "phase": phase, "peak_rss_bytes": rss, "limit_bytes": int(max_rss_bytes)})
    return hook


def run(*, db_path, health_url="http://127.0.0.1:8000", run_dir=DEFAULT_RUN_DIR,
        watch_dir=DEFAULT_WATCH_DIR, max_seconds, max_rss_gib=DEFAULT_MAX_RSS_GIB,
        max_retry_attempts=DEFAULT_MAX_RETRY_ATTEMPTS, production_authorized=False,
        recheck_evaluated=False, env_file=None, today=None,
        mount_check=require_external_volume, calendar_loader=None, run_backfill_fn=None,
        fetcher=None, clock=time.monotonic, rss_reader=_peak_rss_bytes):
    """One unattended run. Returns ``(exit_code, record)``; ``record`` is None
    only when the external disk is unavailable (nowhere to write it)."""
    started_at = core.now_cn()
    started = clock()
    today = today or started_at.date()
    today_iso = today.isoformat()
    run_id = started_at.strftime("%Y%m%dT%H%M%S-%f") + f"-{os.getpid()}"
    try:
        mount_check(run_dir)
    except ExternalDiskError as exc:
        print("t5-auto: " + str(exc), file=sys.stderr)
        raise_alert(watch_dir, str(exc), {"run_dir": str(run_dir), "run_id": run_id})
        return EXIT_CONFIG, None
    run_dir = Path(run_dir).expanduser()

    record = {"schema": RECORD_SCHEMA, "run_id": run_id, "trading_day": today_iso,
              "status": "failed", "reason": None, "started_at": started_at.isoformat(),
              "finished_at": None, "duration_seconds": None, "peak_rss_bytes": None,
              "scan_range": None, "counts": {}, "code_version": _code_version(),
              "pre_export": None, "audit_log": None, "detail": {}}
    exit_code = EXIT_ABORTED
    alerts = []

    def _body():
        for sub in ("records", "audit", "pre-export"):
            (run_dir / sub).mkdir(parents=True, exist_ok=True)
        calendar = (calendar_loader or core.load_calendar)()
        if not calendar or today > calendar[-1]:
            raise core.RunAbort("calendar_out_of_range", {"calendar_end": str(calendar[-1]) if calendar else None})
        if today not in set(calendar):
            record.update(status="skipped", reason="non_trading_day")
            return EXIT_OK

        lag, last = trading_days_since_last_success(run_dir, calendar, today)
        if lag is not None and lag > STALE_TRADING_DAYS:
            alerts.append(("last_success_stale", {"last_success": last, "trading_days_lag": lag}))

        if env_file and Path(env_file).expanduser().is_file():
            from dotenv import load_dotenv
            load_dotenv(Path(env_file).expanduser(), override=False)

        retry_state = load_retry_state(run_dir)
        policy = AutoPolicy(retry_state, max_retry_attempts, recheck_evaluated)
        provider = fetcher or core.PriceSeriesFetcher()
        gates = {"max_reject_ratio": MAX_REJECT_RATIO, "abort_on_evaluated_rewrite": True,
                 "enforce_write_cap": True, "abort_on_lock": True}
        hook = _build_hook(max_seconds=max_seconds, max_rss_bytes=max_rss_gib * 1024 ** 3,
                           clock=clock, started=started, rss_reader=rss_reader)
        audit_log = run_dir / "audit" / f"{run_id}.jsonl"
        runner = run_backfill_fn or core.run_backfill
        with contextlib.redirect_stdout(io.StringIO()):
            result = runner(
                db_path=str(db_path), as_of=today_iso, health_url=health_url,
                production_authorized=production_authorized, audit_log=str(audit_log),
                pre_export_dir=str(run_dir / "pre-export"), fetch_series=provider,
                policy=policy, gates=gates, progress_hook=hook)

        if result.get("skipped"):
            guard = result.get("guard") or {}
            record.update(status="skipped", reason="guard:" + str(guard.get("reason")),
                          detail={"guard": {k: guard[k] for k in ("reason", "running_count") if k in guard}})
            alerts.append(("run_skipped_not_idle", record["detail"]["guard"]))
            return EXIT_SKIPPED_ALARM

        facts = result.get("gate_facts") or {}
        record["scan_range"] = {**(result.get("scan_range") or {}), "sample_count": result.get("sample_count"),
                                "as_of": today_iso}
        record["counts"] = {
            "changed_rows": result.get("changed_rows"), "planned_rows": facts.get("planned_rows"),
            "new_mature_units": facts.get("new_mature_units"), "write_cap": facts.get("write_cap"),
            "retry_attempted_units": facts.get("retry_attempted_units"),
            "suspension_rechecked_units": facts.get("suspension_rechecked_units"),
            "provider_requests": facts.get("provider_requests"),
            "provider_rejected": facts.get("provider_rejected"),
            "skipped_evaluated_units": policy.skipped_evaluated,
            "retry_exceeded_units": len(policy.retry_exceeded),
            "data_missing_units": len(result.get("missing_units") or []),
            "guard_mismatches": result.get("guard_mismatches"),
            "write_conflicts": result.get("write_conflicts"),
            "pre_export_mismatch": result.get("pre_export_mismatch")}
        record["pre_export"] = result.get("pre_export")
        record["audit_log"] = result.get("audit_log")
        failed = [k for k in ("guard_mismatches", "write_conflicts", "pre_export_mismatch") if result.get(k)]
        if failed:
            record.update(status="failed", reason="write_anomaly:" + ",".join(failed))
            alerts.append(("write_anomaly", {"counts": {k: result.get(k) for k in failed}}))
            return EXIT_ABORTED

        _atomic_write_json(_retry_state_path(run_dir), {
            "version": 1, "updated": today_iso,
            "units": next_retry_state(retry_state, policy, result.get("missing_units") or [], today_iso)})
        if policy.retry_exceeded:
            alerts.append(("retry_limit_exceeded", {"count": len(policy.retry_exceeded),
                                                    "units": policy.retry_exceeded[:200]}))
        record.update(status="success", reason=None)
        return EXIT_OK

    try:
        exit_code = _body()
    except core.RunAbort as abort:
        partial = abort.partial or {}
        record.update(status="aborted", reason=abort.reason, detail=abort.detail)
        record["counts"] = {"changed_rows": partial.get("changed_rows"),
                            "sample_count": partial.get("sample_count")}
        alerts.append((abort.reason, abort.detail))
        exit_code = EXIT_ABORTED
    except Exception as exc:
        record.update(status="failed", reason=type(exc).__name__)
        alerts.append(("run_failed", {"error_class": type(exc).__name__}))
        exit_code = EXIT_ABORTED

    record["finished_at"] = core.now_cn().isoformat()
    record["duration_seconds"] = round(clock() - started, 2)
    record["peak_rss_bytes"] = rss_reader()
    if record["peak_rss_bytes"] > max_rss_gib * 1024 ** 3 and record["status"] == "success":
        detail = {"phase": "finish", "peak_rss_bytes": record["peak_rss_bytes"],
                  "limit_bytes": int(max_rss_gib * 1024 ** 3)}
        record.update(status="aborted", reason="memory_limit_exceeded", detail=detail)
        alerts.append(("memory_limit_exceeded", detail))
        exit_code = EXIT_ABORTED
    for reason, detail in alerts:
        raise_alert(watch_dir, reason, {"run_id": run_id, "trading_day": today_iso, **detail})
    try:
        _atomic_write_json(records_dir(run_dir) / f"run-{run_id}.json", record)
    except OSError as exc:
        print("t5-auto: cannot write run record: " + type(exc).__name__, file=sys.stderr)
    return exit_code, record


def check_last_success(run_dir, today=None, calendar_loader=None):
    """Interface for the watch script: stale when > 1 trading day without success."""
    calendar = (calendar_loader or core.load_calendar)()
    today = today or core.now_cn().date()
    lag, last = trading_days_since_last_success(run_dir, calendar, today)
    return {"last_success": last, "trading_days_lag": lag,
            "stale": lag is None or lag > STALE_TRADING_DAYS, "no_success_on_record": lag is None}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--db-path")
    parser.add_argument("--health-url", default="http://127.0.0.1:8000")
    parser.add_argument("--run-dir", default=os.getenv("TA_T5_AUTO_RUN_DIR", DEFAULT_RUN_DIR))
    parser.add_argument("--watch-dir", default=os.getenv("TA_WATCH_DIR", DEFAULT_WATCH_DIR))
    parser.add_argument("--env-file", default=os.getenv("TA_T5_AUTO_ENV_FILE", DEFAULT_ENV_FILE))
    parser.add_argument("--max-seconds", type=float,
                        default=float(os.getenv("TA_T5_AUTO_MAX_SECONDS", "0")) or None)
    parser.add_argument("--max-rss-gib", type=float, default=DEFAULT_MAX_RSS_GIB)
    parser.add_argument("--max-retry-attempts", type=int, default=DEFAULT_MAX_RETRY_ATTEMPTS)
    parser.add_argument("--today", help="override trading-day date (rehearsal only)")
    parser.add_argument("--production-authorized", action="store_true")
    parser.add_argument("--recheck-evaluated", action="store_true",
                        help="refetch already evaluated units (audit mode; the rewrite gate still applies)")
    parser.add_argument("--check-last-success", action="store_true",
                        help="print staleness JSON for the watch script and exit 1 when stale")
    args = parser.parse_args(argv)
    if args.check_last_success:
        status = check_last_success(args.run_dir)
        print(json.dumps(status))
        return EXIT_ABORTED if status["stale"] else EXIT_OK
    if not args.db_path or not args.max_seconds:
        parser.error("--db-path and --max-seconds (3x measured wall time) are required")
    from datetime import date
    exit_code, record = run(
        db_path=args.db_path, health_url=args.health_url, run_dir=args.run_dir,
        watch_dir=args.watch_dir, max_seconds=args.max_seconds, max_rss_gib=args.max_rss_gib,
        max_retry_attempts=args.max_retry_attempts, production_authorized=args.production_authorized,
        recheck_evaluated=args.recheck_evaluated, env_file=args.env_file,
        today=date.fromisoformat(args.today) if args.today else None)
    if record:
        print(json.dumps({"run_id": record["run_id"], "status": record["status"], "reason": record["reason"],
                          "counts": record["counts"]}, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
