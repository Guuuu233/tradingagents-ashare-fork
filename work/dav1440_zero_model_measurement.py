"""DAV-1440 step 1: read-only persisted-decision policy replay, NOT a gate patch.

Only standard-library imports: no product initialization, providers, or models.
NEUTRAL/ABSTAIN are parallel what-if candidates, not an adjudicated policy.
Missing historical evidence is reported, never inferred as unledgered.
"""
from __future__ import annotations

import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
import sys

MANAGER_KEYS = ("direction", "winner", "adopted_claim_ids", "partially_adopted_claims",
                "direction_basis")
STATUS_KEYS = ("analysis_status", "direction", "trade_action", "risk_status",
               "confirmation_state", "reason_codes")
BASIS_CONDITION = "unledgered_and_empty_same_direction_claims"


def mapping(value):
    return value if isinstance(value, dict) else {}


def horizons(data):
    if not isinstance(data, dict):
        return []
    selected = {}
    for name, key in (("short", "short_term"), ("medium", "medium_term"),
                      ("long", "long_term")):
        if isinstance(data.get(key), dict) and data[key]:
            selected[name] = (key, data[key])
    for name, value in mapping(data.get("horizons")).items():
        if isinstance(value, dict) and value and name not in selected:
            selected[name] = ("horizons." + name, value)
    if selected:
        return [(name, path, value) for name, (path, value) in selected.items()]
    return [(data.get("horizon") or "single_unspecified", "result_data", data)]


def original_manager(payload):
    if isinstance(payload.get("manager_original"), dict):
        return payload["manager_original"]
    debate = mapping(payload.get("investment_debate_state"))
    for text in (payload.get("investment_plan"), debate.get("judge_decision")):
        if not isinstance(text, str):
            continue
        match = re.search(r"<!--\s*MANAGER_VERDICT:\s*(.*?)\s*-->", text, re.S)
        if match:
            try:
                raw = json.loads(match.group(1))
            except json.JSONDecodeError:
                continue
            if isinstance(raw, dict):
                return {key: raw[key] for key in ("direction", "partially_adopted_claims")
                        if key in raw}
    return {}


def compact_payload(payload):
    debate = mapping(payload.get("investment_debate_state"))
    mv = mapping(payload.get("manager_verdict")) or mapping(debate.get("manager_verdict"))
    status = mapping(payload.get("decision_status"))
    # Preserve absence of canonical status; do not manufacture a historical VALID.
    if not status:
        status = {key: payload[key] for key in STATUS_KEYS if key in payload}
    return {
        "decision_status": {key: status[key] for key in STATUS_KEYS if key in status},
        "manager_verdict": {key: mv[key] for key in MANAGER_KEYS if key in mv},
        "manager_original": original_manager(payload),
        "manager_source": payload.get("manager_source") or (
            "manager_verdict" if mapping(payload.get("manager_verdict"))
            else "investment_debate_state.manager_verdict"
        ),
    }


def project_horizon(payload):
    p = compact_payload(payload)
    status, mv = p["decision_status"], p["manager_verdict"]
    basis = mapping(mv.get("direction_basis"))
    codes = status.get("reason_codes")
    conditions = []
    if isinstance(codes, list) and "no_adjudicated_support" in codes:
        conditions.append("no_adjudicated_support")
    # Missing/null same_direction_claims is NOT a verified empty list.
    if basis.get("status") == "unledgered" and basis.get("same_direction_claims") == []:
        conditions.append(BASIS_CONDITION)
    assessable = status.get("analysis_status") is not None and status.get("direction") is not None
    change = bool(conditions and status.get("analysis_status") == "VALID"
                  and status.get("direction") in {"BULL", "BEAR"})
    action = status.get("trade_action")
    candidate_action = action if action in {"WAIT", "NO_TRADE"} else "WAIT"
    raw = p["manager_original"]
    return {
        "assessable": assessable, "conditions": conditions, "would_change": change,
        "original_direction": status.get("direction"),
        "original_analysis_status": status.get("analysis_status"),
        "original_action": action,
        "proposed_status": "NEUTRAL 或 ABSTAIN（待总控裁定）" if change else "不改判",
        "neutral_candidate": "VALID/NEUTRAL/" + candidate_action if change else None,
        "abstain_candidate": "ABSTAIN/N/A/" + candidate_action if change else None,
        "manager_direction": mv.get("direction"),
        "manager_direction_original": raw.get("direction", mv.get("direction")),
        "manager_original_source": "MANAGER_VERDICT 原文" if "direction" in raw else p["manager_source"],
        "manager_winner": mv.get("winner"),
        "has_partially_adopted_claims": bool(mv.get("partially_adopted_claims")),
        "partially_adopted_claims": mv.get("partially_adopted_claims"),
        "raw_partially_adopted_claims": raw.get("partially_adopted_claims"),
        "adopted_claim_ids": mv.get("adopted_claim_ids"),
        "direction_basis": mv.get("direction_basis"),
        "original_reason_codes": codes,
    }


def sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def freeze(db):
    con = sqlite3.connect(Path(db).resolve().as_uri() + "?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("BEGIN")
    before = con.execute("SELECT count(*) FROM reports").fetchone()[0]
    cutoff = utc_now()  # First SELECT has pinned a consistent SQLite read snapshot.
    status_counts = dict(con.execute("SELECT status,count(*) FROM reports GROUP BY status"))
    rows, anomalies = [], []
    sql = "SELECT id,symbol,trade_date,created_at,updated_at,result_data FROM reports WHERE status='completed' ORDER BY created_at,id"
    for row in con.execute(sql):
        item = {key: row[key] for key in ("id", "symbol", "trade_date", "created_at", "updated_at")}
        item["result_data_sha256"] = hashlib.sha256(str(row["result_data"]).encode()).hexdigest()
        try:
            data = json.loads(row["result_data"]) if row["result_data"] else None
        except json.JSONDecodeError:
            data = None
            anomalies.append({"id": row["id"], "type": "invalid_result_data_json"})
        item["result_data_type"] = type(data).__name__
        item["horizons"] = []
        for name, path, payload in horizons(data):
            item["horizons"].append({"horizon": name, "source_path": path,
                                     "payload": compact_payload(payload)})
            alias = mapping(mapping(data.get("horizons")).get(name))
            if alias and path != "horizons." + name:
                if compact_payload(alias) != compact_payload(payload):
                    anomalies.append({"id": row["id"], "horizon": name,
                                      "type": "alias_projection_mismatch"})
        rows.append(item)
    after = con.execute("SELECT count(*) FROM reports").fetchone()[0]
    con.rollback()
    con.close()
    return {"manifest": {"snapshot_cutoff_utc": cutoff, "read_finished_utc": utc_now(),
                         "interpreter": sys.version.split()[0],
                         "sqlite_version": sqlite3.sqlite_version,
                         "read_mode": "mode=ro; PRAGMA query_only=ON; BEGIN snapshot",
                         "production_rows_before": before, "production_rows_after": after,
                         "status_counts": status_counts, "completed_reports": len(rows),
                         "model_calls": 0, "provider_calls": 0, "anomalies": anomalies,
                         "scope": "all completed reports, no date/account filter; whitelist only",
                         "replay_type": "persisted-status what-if, not regeneration or historical claim re-adjudication"},
            "reports": rows}


def replay(frozen):
    inventory = []
    for report in frozen["reports"]:
        for horizon in report["horizons"]:
            projection = project_horizon(horizon["payload"])
            inventory.append({"report_id": report["id"], "symbol": report["symbol"],
                              "trade_date": report["trade_date"], "created_at": report["created_at"],
                              "horizon": horizon["horizon"], "source_path": horizon["source_path"],
                              **projection})
    changes = [row for row in inventory if row["would_change"]]
    summary = {
        **frozen["manifest"], "frozen_input_sha256": sha(frozen),
        "report_inventory_sha256": sha(frozen["reports"]),
        "horizons_scanned": len(inventory),
        "assessable_horizons": sum(r["assessable"] for r in inventory),
        "unassessable_horizons": sum(not r["assessable"] for r in inventory),
        "reports_without_result_mapping": sum(r["result_data_type"] != "dict" for r in frozen["reports"]),
        "valid_directional_horizons": sum(r["original_analysis_status"] == "VALID" and r["original_direction"] in {"BULL", "BEAR"} for r in inventory),
        "changed_horizons": len(changes), "changed_reports": len({r["report_id"] for r in changes}),
        "by_direction": dict(Counter(r["original_direction"] for r in changes)),
        "by_action": dict(Counter(r["original_action"] for r in changes)),
        "by_horizon": dict(Counter(r["horizon"] for r in changes)),
        "with_partial_claims": sum(r["has_partially_adopted_claims"] for r in changes),
        "reason_condition_changes": sum("no_adjudicated_support" in r["conditions"] for r in changes),
        "basis_condition_changes": sum(BASIS_CONDITION in r["conditions"] for r in changes),
        "condition_hits_already_nondirectional_or_nonvalid": sum(bool(r["conditions"]) and not r["would_change"] for r in inventory),
        "proposals_are_not_final_policy": True,
    }
    return summary, changes, inventory


def write_outputs(frozen, output):
    output.mkdir(parents=True, exist_ok=True)
    summary, changes, inventory = replay(frozen)
    for name, data in (("frozen_inputs.json", frozen), ("summary.json", summary),
                       ("changes.json", changes), ("inventory.json", inventory)):
        (output / name).write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    columns = ["report_id", "symbol", "trade_date", "horizon", "original_direction",
               "original_analysis_status", "original_action", "proposed_status",
               "neutral_candidate", "abstain_candidate", "conditions", "manager_direction_original",
               "manager_direction", "has_partially_adopted_claims", "partially_adopted_claims",
               "raw_partially_adopted_claims", "adopted_claim_ids", "direction_basis", "source_path"]
    with (output / "changes.csv").open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in changes:
            writer.writerow({key: json.dumps(value, ensure_ascii=False) if isinstance(value, (list, dict))
                             else value for key, value in row.items()})
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--db", type=Path)
    source.add_argument("--frozen-input", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    frozen = freeze(args.db) if args.db else json.loads(args.frozen_input.read_text())
    write_outputs(frozen, args.output)


if __name__ == "__main__":
    main()
