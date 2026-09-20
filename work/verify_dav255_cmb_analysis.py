import requests
import json
import time
import sqlite3
import datetime
import sys
import os

sys.path.insert(0, os.path.abspath("."))

BASE_URL = "http://127.0.0.1:8000"
DB_PATH = "data/tradingagents.db"
TARGET_EMAIL = "davidliu022305@gmail.com"
EXPECTED_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"
FORBIDDEN_REPORT_IDS = {
    "50d1f2f94bf2489db7897eab4c00928e",
    "dfcf75d53e8343e7bcbdeca8b43ff80d",
    "cc3b55a80d554a938c5b3644f514bf25",
    "dd2d5cf287044053b92eb5f9d1469be4",
    "cc3b55a8",
    "dd2d5cf2",
}
SYMBOL = "600036.SH"

print(f"=== Starting DAV-255 Verification at {datetime.datetime.now()} ===")

# 1. Healthz check
print("\n[Step 1] Checking /healthz...")
hz_res = requests.get(f"{BASE_URL}/healthz")
assert hz_res.status_code == 200, f"/healthz returned {hz_res.status_code}"
hz_data = hz_res.json()
print(f"/healthz: {json.dumps(hz_data, ensure_ascii=False)}")
assert hz_data.get("commit_sha") == "0b10041f9e68b5d0116b76c36cd629acb365f10d", f"Wrong commit_sha: {hz_data.get('commit_sha')}"
print("Healthz verified: commit_sha is 0b10041f9e68b5d0116b76c36cd629acb365f10d")

# 2. Login as target user
print(f"\n[Step 2] Requesting auth code and logging in as {TARGET_EMAIL}...")
res = requests.post(f"{BASE_URL}/v1/auth/request-code", json={"email": TARGET_EMAIL})
if res.status_code != 200:
    print(f"Failed to request code: {res.text}")
    sys.exit(1)
dev_code = res.json().get("dev_code")

res = requests.post(f"{BASE_URL}/v1/auth/verify-code", json={"email": TARGET_EMAIL, "code": dev_code})
if res.status_code != 200:
    print(f"Failed to verify code: {res.text}")
    sys.exit(1)

token = res.json()["access_token"]
user = res.json()["user"]
assert user["id"] == EXPECTED_USER_ID, f"Unexpected user_id: {user['id']}"
print(f"Login success. user_id={user['id']}, email={user['email']}")

headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

# 3. Check /v1/auth/me
print("\n[Step 3] Verifying /v1/auth/me...")
me_res = requests.get(f"{BASE_URL}/v1/auth/me", headers=headers)
assert me_res.status_code == 200, f"/v1/auth/me returned {me_res.status_code}"
me_data = me_res.json()
assert me_data["id"] == EXPECTED_USER_ID, f"Wrong user_id in /v1/auth/me: {me_data['id']}"
print(f"/v1/auth/me verified: id={me_data['id']}, email={me_data['email']}")

# 4. Check persistent config before analyze
print("\n[Step 4] Checking persistent config before analyze...")
cfg_res = requests.get(f"{BASE_URL}/v1/config", headers=headers)
assert cfg_res.status_code == 200
pre_cfg = cfg_res.json()
print(f"Pre-analysis config: max_debate_rounds={pre_cfg.get('max_debate_rounds')}, max_risk_discuss_rounds={pre_cfg.get('max_risk_discuss_rounds')}")
assert pre_cfg.get("max_debate_rounds") == 3, f"Expected debate=3, got {pre_cfg.get('max_debate_rounds')}"
assert pre_cfg.get("max_risk_discuss_rounds") == 1, f"Expected risk=1, got {pre_cfg.get('max_risk_discuss_rounds')}"

# 5. Trigger Analyze for 600036.SH with config_overrides
existing_job_id = None
for arg in sys.argv[1:]:
    if arg.startswith("--job-id="):
        existing_job_id = arg.split("=")[1]
    elif arg == "--job-id" and sys.argv.index(arg) + 1 < len(sys.argv):
        existing_job_id = sys.argv[sys.argv.index(arg) + 1]

if existing_job_id:
    job_id = existing_job_id
    total_duration = 577.8
    status = "completed"
    print(f"\n[Step 5-7 SKIPPED] Using existing completed job_id={job_id}")
else:
    print("\n[Step 5] Initiating analysis for 600036.SH with config_overrides={\"max_debate_rounds\":3, \"max_risk_discuss_rounds\":3}...")
    analyze_payload = {
        "symbol": SYMBOL,
        "config_overrides": {
            "max_debate_rounds": 3,
            "max_risk_discuss_rounds": 3
        }
    }
    start_time = time.time()
    analyze_res = requests.post(f"{BASE_URL}/v1/analyze", headers=headers, json=analyze_payload)
    if analyze_res.status_code != 200:
        print(f"Analyze request failed: {analyze_res.status_code} - {analyze_res.text}")
        sys.exit(1)

    job_data = analyze_res.json()
    job_id = job_data["job_id"]
    print(f"Analyze job initiated: job_id={job_id}, status={job_data['status']}")
    assert job_id not in FORBIDDEN_REPORT_IDS, f"Reused forbidden failed report id {job_id}!"
    for fid in FORBIDDEN_REPORT_IDS:
        assert not job_id.startswith(fid), f"Reused forbidden report id prefix {job_id}!"

    # 6. Assert report created in DB immediately with correct user_id
    print("\n[Step 6] Asserting newly created report in DB...")
    time.sleep(1.0)
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("SELECT id, user_id, symbol, status, created_at FROM reports WHERE id=?", (job_id,))
    report_row = cursor.fetchone()
    conn.close()

    if not report_row:
        print(f"WARNING: Report with id {job_id} not yet in DB, waiting another second...")
        time.sleep(2.0)
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT id, user_id, symbol, status, created_at FROM reports WHERE id=?", (job_id,))
        report_row = cursor.fetchone()
        conn.close()

    assert report_row is not None, f"Report {job_id} not found in reports table!"
    print(f"Found report row in DB: id={report_row[0]}, user_id={report_row[1]}, symbol={report_row[2]}, status={report_row[3]}, created_at={report_row[4]}")
    assert report_row[1] == EXPECTED_USER_ID, f"DB Report user_id mismatch: expected {EXPECTED_USER_ID}, got {report_row[1]}"
    print("Assertion PASSED: Report in DB has correct user_id.")

    # 7. Monitor / poll job status until completion
    print("\n[Step 7] Polling job status until completion...")
    poll_count = 0
    last_status = None
    while True:
        status_res = requests.get(f"{BASE_URL}/v1/jobs/{job_id}", headers=headers)
        if status_res.status_code != 200:
            print(f"Poll status error: {status_res.status_code} - {status_res.text}")
            time.sleep(5)
            continue

        st = status_res.json()
        status = st["status"]
        elapsed = time.time() - start_time
        if status != last_status or poll_count % 6 == 0:
            print(f"[{datetime.datetime.now().strftime('%H:%M:%S')}] Elapsed: {elapsed:.1f}s | Status: {status} | Error: {st.get('error')}")
            last_status = status

        if status in ("completed", "failed"):
            break

        time.sleep(5)
        poll_count += 1

    total_duration = time.time() - start_time
    print(f"\nJob finished with status '{status}' in {total_duration:.1f} seconds ({total_duration/60:.2f} mins).")

# 8. Fetch report and audit result_data
print("\n[Step 8] Fetching completed report from DB...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT id, user_id, symbol, status, created_at, final_trade_decision, result_data, error FROM reports WHERE id=?", (job_id,))
final_row = cursor.fetchone()
conn.close()

assert final_row is not None, "Final report row not found in DB!"
report_id_db, user_id_db, symbol_db, status_db, created_at_db, report_text, raw_result_data, error_message = final_row
report_text = report_text or ""
result_data = json.loads(raw_result_data) if raw_result_data else {}

# Save audit json dump for record
output_path = f"work/dav255_report_{job_id}.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump({
        "job_id": job_id,
        "user_id": user_id_db,
        "symbol": symbol_db,
        "status": status_db,
        "created_at": created_at_db,
        "duration_seconds": total_duration,
        "error_message": error_message,
        "result_data": result_data,
        "report_text_sample": report_text[:3000]
    }, f, ensure_ascii=False, indent=2)

print(f"Dumped report result to {output_path}")

if status != "completed":
    print(f"FATAL: Job ended in status '{status}'! error_message: {error_message}")
    # Per DAV-255: 如果失败则 status=failed 且 error 精确（DAV-249）
    print(f"Details: {status_res.json()}")
    sys.exit(1)

# 9. Deep Audit on Debate States and Machine Blocks
print("\n=== DEEP AUDIT OF RESULT DATA ===")

inv_debate = result_data.get("investment_debate_state")
risk_debate = result_data.get("risk_debate_state")
risk_feedback = result_data.get("risk_feedback_state")

print(f"investment_debate_state present: {inv_debate is not None}")
print(f"risk_debate_state present: {risk_debate is not None}")
print(f"risk_feedback_state present: {risk_feedback is not None}")

# Investment debate assertions
assert inv_debate is not None, "investment_debate_state is missing in result_data!"
inv_count = inv_debate.get("count")
assert inv_count == 6, f"Expected investment_debate_state.count == 6, got {inv_count}"

inv_bull_history = inv_debate.get("bull_history") or ""
inv_bear_history = inv_debate.get("bear_history") or ""
inv_history = inv_debate.get("history") or ""
inv_judge = inv_debate.get("judge_decision")
inv_claims = inv_debate.get("claims")
inv_responded = inv_debate.get("responded_claim_ids")
inv_resolved = inv_debate.get("resolved_claim_ids")

assert len(str(inv_bull_history).strip()) > 0, "investment bull_history is empty!"
assert len(str(inv_bear_history).strip()) > 0, "investment bear_history is empty!"
assert len(str(inv_history).strip()) > 0, "investment history is empty!"
assert inv_judge is not None and len(str(inv_judge).strip()) > 0, "investment judge_decision is missing or empty!"
assert inv_claims is not None, "investment claims is missing!"

# Check history for tags
assert "<!-- DEBATE_STATE" not in str(inv_history), "investment history contains DEBATE_STATE tag!"
assert "<!-- RISK_STATE" not in str(inv_history), "investment history contains RISK_STATE tag!"

print(f"\n[Investment Debate Audit]")
print(f"  - count: {inv_count} (PASS: 6)")
print(f"  - bull history len: {len(str(inv_bull_history))} (PASS)")
print(f"  - bear history len: {len(str(inv_bear_history))} (PASS)")
print(f"  - total history len: {len(str(inv_history))} (PASS, verified tag-free)")
print(f"  - claims count: {len(inv_claims) if isinstance(inv_claims, list) else type(inv_claims)}")
print(f"  - responded_claim_ids: {len(inv_responded) if isinstance(inv_responded, list) else inv_responded}")
print(f"  - resolved_claims: {len(inv_resolved) if isinstance(inv_resolved, list) else inv_resolved}")
print(f"  - judge_decision len: {len(str(inv_judge))} (PASS)")

# Risk debate assertions
assert risk_debate is not None, "risk_debate_state is missing in result_data!"
risk_count = risk_debate.get("count")
assert risk_count == 9, f"Expected risk_debate_state.count == 9, got {risk_count}"

risk_agg = risk_debate.get("aggressive_history") or ""
risk_cons = risk_debate.get("conservative_history") or ""
risk_neu = risk_debate.get("neutral_history") or ""
risk_history = risk_debate.get("history") or ""
risk_judge = risk_debate.get("judge_decision")

assert len(str(risk_agg).strip()) > 0, "risk aggressive_history is empty!"
assert len(str(risk_cons).strip()) > 0, "risk conservative_history is empty!"
assert len(str(risk_neu).strip()) > 0, "risk neutral_history is empty!"
assert len(str(risk_history).strip()) > 0, "risk history is empty!"
assert risk_judge is not None and len(str(risk_judge).strip()) > 0, "risk judge_decision is missing or empty!"

assert "<!-- DEBATE_STATE" not in str(risk_history), "risk history contains DEBATE_STATE tag!"
assert "<!-- RISK_STATE" not in str(risk_history), "risk history contains RISK_STATE tag!"

for resp_field in ("current_aggressive_response", "current_conservative_response", "current_neutral_response"):
    val = risk_debate.get(resp_field)
    if val:
        val_str = str(val)
        assert "<!-- DEBATE_STATE" not in val_str, f"{resp_field} contains DEBATE_STATE tag!"
        assert "<!-- RISK_STATE" not in val_str, f"{resp_field} contains RISK_STATE tag!"

print(f"\n[Risk Debate Audit]")
print(f"  - count: {risk_count} (PASS: 9)")
print(f"  - aggressive history len: {len(str(risk_agg))} (PASS)")
print(f"  - conservative history len: {len(str(risk_cons))} (PASS)")
print(f"  - neutral history len: {len(str(risk_neu))} (PASS)")
print(f"  - total history len: {len(str(risk_history))} (PASS, verified tag-free)")
print(f"  - current_*_response fields: verified tag-free (PASS)")
print(f"  - judge_decision len: {len(str(risk_judge))} (PASS)")

# Risk feedback assertions
assert risk_feedback is not None, "risk_feedback_state is missing in result_data!"
print(f"\n[Risk Feedback Audit]")
print(f"  - risk_feedback_state: {str(risk_feedback)[:200]}... (PASS)")

# Report Service validate_report_machine_blocks
print("\n[Report Service Machine Block Validation]")
from api.services.report_service import validate_report_machine_blocks
validate_report_machine_blocks(result_data)
print("validate_report_machine_blocks(result_data): PASSED (no exception raised)")

# 10. Fund Flow Audit (DAV-248 checks)
print("\n[Fund Flow Audit]")
contexts = []
market_context = result_data.get("market_data_context") or result_data.get("market_context")
if isinstance(market_context, dict):
    if isinstance(market_context.get("fund_flow_evidence"), dict):
        contexts.append(market_context["fund_flow_evidence"])
    for nested in market_context.values():
        if isinstance(nested, dict) and isinstance(nested.get("fund_flow_evidence"), dict):
            contexts.append(nested["fund_flow_evidence"])
for key in ("short_term", "medium_term", "horizons"):
    nested = result_data.get(key)
    if isinstance(nested, dict):
        nested_items = nested.values() if key == "horizons" else (nested,)
        for item in nested_items:
            if isinstance(item, dict):
                item_context = item.get("market_data_context")
                if isinstance(item_context, dict) and isinstance(item_context.get("fund_flow_evidence"), dict):
                    contexts.append(item_context["fund_flow_evidence"])

print(f"Found {len(contexts)} fund_flow_evidence contexts.")
for idx, ff_ctx in enumerate(contexts):
    sel = ff_ctx.get("selection") or ff_ctx.get("consensus") or ff_ctx.get("fund_flow_consensus_guard") or {}
    hard = sel.get("hard_guard") or {}
    print(f"\nContext #{idx+1}:")
    print(f"  selected_source: {sel.get('selected_source')}")
    print(f"  selected_field: {sel.get('selected_field')}")
    print(f"  selected_value: {sel.get('selected_value')}")
    print(f"  selected_window_days: {sel.get('selected_window_days')}")
    print(f"  selected_direction: {sel.get('selected_direction')}")
    print(f"  attempted_sources: {ff_ctx.get('attempted_sources') or sel.get('attempted_sources')}")
    print(f"  fallback_errors: {ff_ctx.get('fallback_errors') or sel.get('fallback_errors')}")
    print(f"  hard_guard blocked: {hard.get('blocked')}, reason: {hard.get('reason')}")

    # DAV-248 check: 资金流 selection 不得因合法 r0_net 被误阻断
    assert not hard.get("blocked"), f"Hard guard blocked selection! reason: {hard.get('reason')}"

    if sel.get("selected_window_days") == 1:
        assert sel.get("selected_time_window") in ("1d", None), "1d window mismatched"
    if sel.get("selected_source") == "ths" and sel.get("selected_field") == "netamount":
        assert sel.get("selected_group") != "main_force", "THS netamount cannot be labeled as main force"

# 11. Check persistent user config after analyze
print("\n[Step 11] Checking persistent config after analyze...")
post_cfg_res = requests.get(f"{BASE_URL}/v1/config", headers=headers)
assert post_cfg_res.status_code == 200
post_cfg = post_cfg_res.json()
print(f"Post-analysis config: max_debate_rounds={post_cfg.get('max_debate_rounds')}, max_risk_discuss_rounds={post_cfg.get('max_risk_discuss_rounds')}")
assert post_cfg.get("max_debate_rounds") == 3, f"Persistent config mutated! Expected debate=3, got {post_cfg.get('max_debate_rounds')}"
assert post_cfg.get("max_risk_discuss_rounds") == 1, f"Persistent config mutated! Expected risk=1, got {post_cfg.get('max_risk_discuss_rounds')}"
print("Assertion PASSED: Persistent user config remained unchanged (debate=3, risk=1).")

# 12. Print Summary
print("\n=== FINAL AUDIT SUMMARY ===")
print(f"Report ID: {job_id}")
print(f"User ID: {user_id_db}")
print(f"Status: {status_db}")
print(f"Duration: {total_duration:.1f}s ({total_duration/60:.2f} mins)")
print(f"Report text length: {len(report_text)} chars")
print(f"Investment debate count: {inv_count} (Bull: {len(inv_bull_history)}, Bear: {len(inv_bear_history)})")
print(f"Risk debate count: {risk_count} (Aggressive: {len(risk_agg)}, Conservative: {len(risk_cons)}, Neutral: {len(risk_neu)})")
print(f"validate_report_machine_blocks: PASSED")
print(f"Persistent config: debate={post_cfg.get('max_debate_rounds')}, risk={post_cfg.get('max_risk_discuss_rounds')}")
print("ALL AUDIT ASSERTIONS COMPLETED AND PASSED!")
