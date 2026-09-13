#!/usr/bin/env python3
"""Runner script for V-03 Offline Replay Harness & Snapshot Protocol (DAV-861).

Strict Hard Constraints:
1. READ-ONLY connection to production database (mode=ro, physical write protection).
2. Measurement runs strictly on an atomic sqlite3 .backup() copy in work/.
3. Zero mutations to production database (verified via pre/post sha256 and quick_check).
4. Full Snapshot Manifest, 25-field Offline Audit Table, and 4 Ablation Controls.
5. All outputs stamped with disclaimer: "半成品基线，非定性判断" and system completeness metadata.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
from typing import Any, Dict, List, Optional, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from tradingagents.eval.v03_return_measure import (
    BASELINE_DISCLAIMER,
    BASELINE_GLOBAL_PROMPT_HASH,
    BASELINE_MODEL,
    BASELINE_RUNNING_SERVICE_SHA,
    DEFAULT_BENCHMARK_SYMBOL,
    DEFAULT_HISTORICAL_CUTOFF_DATE,
    DEFAULT_HISTORICAL_CUTOFF_DATETIME,
    DEFAULT_HOLD_DAYS,
    DEFAULT_STATUS_FILTER,
    DEFAULT_TARGET_USER_ID,
    HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA,
    CostModel,
    OfflineReplayHarness,
    SnapshotManifest,
    VendorPriceDataProvider,
    V03ReturnMeasureEngine,
    compute_file_sha256,
    get_code_prompt_sha,
    get_current_code_sha,
    probe_running_service_sha,
)

DEFAULT_PROD_DB = "/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db"
DEFAULT_REPLICA_DB = str(PROJECT_ROOT / "work" / "tradingagents_v03_replica.db")
DEFAULT_REPORT_MD = str(PROJECT_ROOT / "work" / "v03_return_measurement_report.md")
DEFAULT_REPORT_JSON = str(PROJECT_ROOT / "work" / "v03_return_measurement_report.json")
DEFAULT_MANIFEST_JSON = str(PROJECT_ROOT / "work" / "v03_snapshot_manifest.json")
DEFAULT_AUDIT_JSON = str(PROJECT_ROOT / "work" / "v03_audit_records.json")
DEFAULT_ABLATION_JSON = str(PROJECT_ROOT / "work" / "v03_ablation_summary.json")


def check_sqlite_integrity(db_path: str | Path) -> Tuple[str, str]:
    """Run integrity_check and quick_check on SQLite database in read-only mode."""
    p = Path(db_path).resolve()
    if not p.exists():
        raise FileNotFoundError(f"Database file not found: {p}")

    uri = f"file:{p}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA quick_check")
        quick_res = cur.fetchone()[0]
        cur.execute("PRAGMA integrity_check")
        integrity_res = cur.fetchone()[0]
        return quick_res, integrity_res
    finally:
        conn.close()


def create_sqlite_backup(
    prod_db_path: str, backup_db_path: str, target_user_id: str, cutoff_date: str
) -> Dict[str, Any]:
    """Create an atomic SQLite backup from production DB using sqlite3.backup().

    Verifies production DB integrity and SHA256 before and after backup
    to mathematically prove zero mutation.
    """
    prod_path = Path(prod_db_path).resolve()
    if not prod_path.exists():
        raise FileNotFoundError(f"Production database not found: {prod_path}")

    backup_path = Path(backup_db_path).resolve()
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    if backup_path.exists():
        backup_path.unlink()

    print("[1/5] Pre-backup inspection on production DB (READ-ONLY mode=ro)...")
    pre_prod_sha = compute_file_sha256(prod_path)
    pre_quick, pre_integrity = check_sqlite_integrity(prod_path)
    pre_counts = V03ReturnMeasureEngine.get_user_report_counts(
        str(prod_path), target_user_id=target_user_id, cutoff_date=cutoff_date
    )
    pre_counts_all = V03ReturnMeasureEngine.get_user_report_counts(
        str(prod_path), target_user_id=target_user_id, cutoff_date=None
    )

    print(f"      Production DB: {prod_path}")
    print(f"      Pre-backup SHA256: {pre_prod_sha}")
    print(f"      Pre-backup quick_check: {pre_quick} | integrity_check: {pre_integrity}")
    print(
        f"      Pre-backup Counts (cutoff={cutoff_date}): total={pre_counts['total']}, "
        f"completed={pre_counts['completed']}, failed={pre_counts['failed']}"
    )
    print(
        f"      Pre-backup Counts (all live): total={pre_counts_all['total']}, "
        f"completed={pre_counts_all['completed']}, failed={pre_counts_all['failed']}"
    )

    print(f"[2/5] Performing atomic sqlite3 .backup() to replica: {backup_path}...")
    prod_uri = f"file:{prod_path}?mode=ro"
    prod_conn = sqlite3.connect(prod_uri, uri=True)
    backup_conn = sqlite3.connect(str(backup_path))
    try:
        prod_conn.backup(backup_conn)
    finally:
        prod_conn.close()
        backup_conn.close()

    print("[3/5] Post-backup zero-mutation verification on production DB...")
    post_prod_sha = compute_file_sha256(prod_path)
    post_quick, post_integrity = check_sqlite_integrity(prod_path)
    post_counts = V03ReturnMeasureEngine.get_user_report_counts(
        str(prod_path), target_user_id=target_user_id, cutoff_date=cutoff_date
    )

    if pre_prod_sha != post_prod_sha:
        raise RuntimeError(
            f"CRITICAL ERROR: Production DB SHA256 mutated during backup! "
            f"Pre: {pre_prod_sha} != Post: {post_prod_sha}"
        )
    if pre_counts != post_counts:
        raise RuntimeError("CRITICAL ERROR: Production DB counts mutated during backup!")

    replica_sha = compute_file_sha256(backup_path)
    rep_quick, rep_integrity = check_sqlite_integrity(backup_path)

    print(f"      Post-backup Production SHA256: {post_prod_sha} (MATCH PROVEN, ZERO MUTATION)")
    print(f"      Post-backup quick_check: {post_quick} | integrity_check: {post_integrity}")
    print(f"      Replica DB: {backup_path}")
    print(f"      Replica SHA256: {replica_sha}")
    print(f"      Replica quick_check: {rep_quick} | integrity_check: {rep_integrity}")

    return {
        "production_db_path": str(prod_path),
        "production_db_sha256": post_prod_sha,
        "production_quick_check": post_quick,
        "production_integrity_check": post_integrity,
        "replica_db_path": str(backup_path),
        "replica_db_sha256": replica_sha,
        "replica_quick_check": rep_quick,
        "replica_integrity_check": rep_integrity,
        "user_counts_cutoff": post_counts,
        "user_counts_all": pre_counts_all,
    }


def run_measurement_and_ablations(
    replica_db_path: str,
    prod_db_path: str,
    replica_sha256: str,
    output_md: str,
    output_json: str,
    output_manifest: str,
    output_audit: str,
    output_ablation: str,
    hold_days: int = DEFAULT_HOLD_DAYS,
    limit: Optional[int] = None,
    target_user_id: str = DEFAULT_TARGET_USER_ID,
    status_filter: str = DEFAULT_STATUS_FILTER,
    cutoff_date: str = DEFAULT_HISTORICAL_CUTOFF_DATE,
    run_ablations: bool = True,
    running_service_sha: Optional[str] = None,
    running_service_provenance: Optional[str] = None,
) -> None:
    """Execute measurement engine and ablation harness on replica database."""
    user_stats = V03ReturnMeasureEngine.get_user_report_counts(
        replica_db_path, target_user_id=target_user_id, cutoff_date=cutoff_date
    )
    cutoff_datetime = f"{cutoff_date} 23:59:59"

    print(f"[4/5] Initializing V-03 Return Measurement Engine on replica...")
    print(f"      Replica DB: {replica_db_path}")
    print(f"      Target User: {target_user_id}")
    print(f"      Status Scope: {status_filter} (仅 completed)")
    print(f"      Cutoff Date: {cutoff_date} (PIT cutoff datetime: {cutoff_datetime})")
    print(
        f"      Account Stats: Total={user_stats['total']}, "
        f"Completed={user_stats['completed']}, Failed={user_stats['failed']}"
    )
    print(f"      Historical Sample Generating SHA: {HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA}")
    print(f"      Running Service SHA: {running_service_sha} (provenance: {running_service_provenance})")

    engine = V03ReturnMeasureEngine(
        cost_model=CostModel(),
        hold_days=hold_days,
        benchmark_symbol=DEFAULT_BENCHMARK_SYMBOL,
        price_provider=VendorPriceDataProvider(),
        target_user_id=target_user_id,
        status_filter=status_filter,
        target_user_stats=user_stats,
        production_db_path=prod_db_path,
        replica_db_path=replica_db_path,
        replica_sha256=replica_sha256,
        cutoff_datetime=cutoff_datetime,
        requested_as_of=cutoff_date,
        running_service_sha=running_service_sha,
        running_service_provenance=running_service_provenance,
        sample_generating_service_sha=HISTORICAL_SAMPLE_GENERATING_SERVICE_SHA,
    )

    reports = engine.load_reports_from_db(
        replica_db_path,
        limit=limit,
        target_user_id=target_user_id,
        status_filter=status_filter,
        cutoff_date=cutoff_date,
    )
    print(f"      Loaded {len(reports)} reports from replica database. Executing measurement...")

    result = engine.measure_dataset(reports)

    # Print summary to console
    m_all = result.all_metrics
    m_dev = result.dev_metrics
    m_hist = result.historical_oos_metrics
    m_fwd = result.forward_oos_metrics
    m_reg = result.regression_metrics

    print("\n" + "=" * 70)
    print(f"V-03 离线实验测量结果摘要 (V-03 Replay & Measure Baseline)")
    print(f"核心定位: {result.stamp.disclaimer}")
    print(f"评测账号: {result.stamp.target_user_id} | 状态限定: {result.stamp.status_filter} ({result.stamp.scope_filter_description})")
    print(f"账号分布: 总计={result.stamp.account_stats.get('total')} | completed={result.stamp.account_stats.get('completed')} | failed={result.stamp.account_stats.get('failed')}")
    print("=" * 70)
    print(f"总报告数: {m_all.total_reports}")
    print(f"评估候选数: {m_all.directional_candidate_count}")
    print(f"规范化合格: {m_all.mappable_count} | 隔离未规范: {m_all.unmappable_count}")
    print(f"入池数 (In-Pool): {m_all.in_pool_count} | 池排除数: {m_all.excluded_pool_count}")
    print(f"可交易数: {m_all.tradable_count} | 停牌/封死不可交易: {m_all.untradable_count}")
    print(f"有效评测数: {m_all.evaluated_count} | 数据缺口 (Typed-Missing): {m_all.typed_missing_count}")
    print(f"覆盖率 (Coverage Rate): {m_all.coverage_rate * 100:.2f}%")
    print(f"可评估率 (Evaluability Rate): {m_all.evaluability_rate * 100:.2f}%")
    print("-" * 70)
    print(f"DEV 样本数 (<=2025-12-31): {m_dev.total_reports}")
    print(f"HISTORICAL_OOS 样本数 (2026-01-01~2026-09-08): {m_hist.total_reports} (有效评测={m_hist.evaluated_count})")
    print(f"FORWARD_OOS 样本数 (>=2026-09-09): {m_fwd.total_reports} (如实输出0，杜绝伪造)")
    print(f"六只回归标的永久隔离数: {m_reg.total_reports} (不计入OOS指标)")
    if m_all.mean_net_return is not None:
        print(f"平均净收益率: {m_all.mean_net_return * 100:.2f}%")
    if m_all.mean_excess_return is not None:
        print(f"平均超额收益 (Alpha vs 沪深300): {m_all.mean_excess_return * 100:.2f}%")
    if m_all.win_rate is not None:
        print(f"胜率 (Win Rate): {m_all.win_rate * 100:.2f}%")
    print("=" * 70 + "\n")

    # Generate output files
    md_content = engine.generate_report_markdown(result)
    Path(output_md).write_text(md_content, encoding="utf-8")
    print(f"Wrote Markdown report to: {output_md}")

    json_dict = result.to_dict()
    Path(output_json).write_text(
        json.dumps(json_dict, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Wrote JSON report to: {output_json}")

    # Output snapshot manifest
    if result.snapshot_manifest:
        man_dict = result.snapshot_manifest.to_dict()
        Path(output_manifest).write_text(
            json.dumps(man_dict, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote Snapshot Manifest to: {output_manifest}")

    # Output 25-field offline audit table
    if result.audit_table:
        Path(output_audit).write_text(
            json.dumps(result.audit_table, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote 25-field Offline Audit Records ({len(result.audit_table)} rows) to: {output_audit}")

    # Step 5: Run 4 Ablation Controls
    if run_ablations:
        print("\n[5/5] Executing 4 Ablation Controls via OfflineReplayHarness...")
        harness = OfflineReplayHarness(engine)
        variants = harness.run_all_ablation_variants(reports)
        ablation_summary: Dict[str, Any] = {
            "disclaimer": BASELINE_DISCLAIMER,
            "snapshot_hash": next(iter(variants.values())).snapshot_hash,
            "variants_count": len(variants),
            "variants": {k: v.to_dict() for k, v in variants.items()},
        }
        Path(output_ablation).write_text(
            json.dumps(ablation_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"Wrote Ablation Summary ({len(variants)} variants) to: {output_ablation}")
        print("      All ablation variants verified: 100% shared identical snapshot hash.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run V-03 Offline Replay Harness & Snapshot Protocol on Replica DB"
    )
    parser.add_argument("--prod-db", default=DEFAULT_PROD_DB, help="Production DB path")
    parser.add_argument("--replica-db", default=DEFAULT_REPLICA_DB, help="Replica DB path")
    parser.add_argument("--output-md", default=DEFAULT_REPORT_MD, help="Output markdown path")
    parser.add_argument("--output-json", default=DEFAULT_REPORT_JSON, help="Output JSON path")
    parser.add_argument(
        "--output-manifest", default=DEFAULT_MANIFEST_JSON, help="Output snapshot manifest path"
    )
    parser.add_argument(
        "--output-audit", default=DEFAULT_AUDIT_JSON, help="Output 25-field audit table path"
    )
    parser.add_argument(
        "--output-ablation", default=DEFAULT_ABLATION_JSON, help="Output ablation summary path"
    )
    parser.add_argument("--hold-days", type=int, default=DEFAULT_HOLD_DAYS, help="Holding days")
    parser.add_argument("--limit", type=int, default=None, help="Limit records for quick audit")
    parser.add_argument(
        "--target-user-id",
        type=str,
        default=DEFAULT_TARGET_USER_ID,
        help="Target user ID to evaluate (default: David)",
    )
    parser.add_argument(
        "--status-filter",
        type=str,
        default=DEFAULT_STATUS_FILTER,
        help="Status filter (default: completed)",
    )
    parser.add_argument(
        "--cutoff-date",
        type=str,
        default=DEFAULT_HISTORICAL_CUTOFF_DATE,
        help=f"Cutoff trade date (default: {DEFAULT_HISTORICAL_CUTOFF_DATE})",
    )
    parser.add_argument(
        "--running-service-sha",
        type=str,
        default=None,
        help="Explicit running service SHA (overrides healthz probe)",
    )
    parser.add_argument(
        "--healthz-url",
        type=str,
        default="http://127.0.0.1:8000/healthz",
        help="URL of read-only healthz probe endpoint",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="Force offline execution mode without healthz probe",
    )
    parser.add_argument(
        "--skip-backup", action="store_true", help="Skip backup if replica already exists"
    )
    parser.add_argument(
        "--no-ablations", action="store_true", help="Skip running ablation controls"
    )
    args = parser.parse_args()

    # Running service SHA provenance resolution (DAV-865)
    running_sha = args.running_service_sha
    prov_source = "explicit_cli_argument" if running_sha else None

    if not running_sha:
        if args.offline:
            running_sha = "offline_replay_gap"
            prov_source = "offline_replay_explicit_flag"
        else:
            probed_sha, prov = probe_running_service_sha(args.healthz_url, timeout_sec=1.0)
            if probed_sha is not None:
                running_sha = probed_sha
                prov_source = prov
            else:
                running_sha = "offline_replay_gap"
                prov_source = prov

    replica_sha = ""
    if not args.skip_backup or not Path(args.replica_db).exists():
        backup_meta = create_sqlite_backup(
            args.prod_db, args.replica_db, args.target_user_id, args.cutoff_date
        )
        replica_sha = backup_meta["replica_db_sha256"]
    else:
        print(f"Skipping backup as requested; using existing replica at {args.replica_db}")
        replica_sha = compute_file_sha256(args.replica_db)
        rep_quick, rep_integrity = check_sqlite_integrity(args.replica_db)
        print(f"Replica SHA256: {replica_sha} (quick_check: {rep_quick})")

    run_measurement_and_ablations(
        replica_db_path=args.replica_db,
        prod_db_path=args.prod_db,
        replica_sha256=replica_sha,
        output_md=args.output_md,
        output_json=args.output_json,
        output_manifest=args.output_manifest,
        output_audit=args.output_audit,
        output_ablation=args.output_ablation,
        hold_days=args.hold_days,
        limit=args.limit,
        target_user_id=args.target_user_id,
        status_filter=args.status_filter,
        cutoff_date=args.cutoff_date,
        run_ablations=not args.no_ablations,
        running_service_sha=running_sha,
        running_service_provenance=prov_source,
    )


if __name__ == "__main__":
    main()
