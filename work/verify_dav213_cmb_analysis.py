import requests
import json
import time
import sqlite3
import datetime
import sys
import os

BASE_URL = "http://127.0.0.1:8000"
DB_PATH = "data/tradingagents.db"
TARGET_EMAIL = "davidliu022305@gmail.com"
EXPECTED_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"
FORBIDDEN_REPORT_ID = "50d1f2f94bf2489db7897eab4c00928e"
SYMBOL = "600036.SH"

print(f"=== Starting DAV-213 Verification at {datetime.datetime.now()} ===")

# 1. Login
print("\n[Step 1] Requesting auth code and logging in...")
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

# 2. Check /v1/auth/me
print("\n[Step 2] Verifying /v1/auth/me...")
me_res = requests.get(f"{BASE_URL}/v1/auth/me", headers=headers)
assert me_res.status_code == 200, f"/v1/auth/me returned {me_res.status_code}"
me_data = me_res.json()
assert me_data["id"] == EXPECTED_USER_ID, f"Wrong user_id in /v1/auth/me: {me_data['id']}"
print(f"/v1/auth/me verified: id={me_data['id']}, email={me_data['email']}")

# 3. Check persistent config before analyze
print("\n[Step 3] Checking persistent config before analyze...")
cfg_res = requests.get(f"{BASE_URL}/v1/config", headers=headers)
assert cfg_res.status_code == 200
pre_cfg = cfg_res.json()
print(f"Pre-analysis config: max_debate_rounds={pre_cfg.get('max_debate_rounds')}, max_risk_discuss_rounds={pre_cfg.get('max_risk_discuss_rounds')}")
assert pre_cfg.get("max_debate_rounds") == 3, f"Expected debate=3, got {pre_cfg.get('max_debate_rounds')}"
assert pre_cfg.get("max_risk_discuss_rounds") == 1, f"Expected risk=1, got {pre_cfg.get('max_risk_discuss_rounds')}"

# 4. Trigger Analyze for 600036.SH with config_overrides
print("\n[Step 4] Initiating analysis for 600036.SH with config_overrides={\"max_debate_rounds\":3, \"max_risk_discuss_rounds\":3}...")
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
assert job_id != FORBIDDEN_REPORT_ID, f"Reused failed report id {job_id}!"

# 5. Assert report created in DB immediately with correct user_id
print("\n[Step 5] Asserting newly created report in DB...")
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

# 6. Monitor / poll job status until completion
print("\n[Step 6] Polling job status until completion...")
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

if status != "completed":
    print(f"FATAL: Job failed! Details: {status_res.json()}")
    sys.exit(1)

# 7. Fetch report and audit result_data
print("\n[Step 7] Fetching completed report from DB...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT id, user_id, symbol, status, created_at, report_text, result_data FROM reports WHERE id=?", (job_id,))
final_row = cursor.fetchone()
conn.close()

assert final_row is not None, "Final report row not found in DB!"
report_text = final_row[5] or ""
raw_result_data = final_row[6]
result_data = json.loads(raw_result_data) if raw_result_data else {}

# Save audit json dump for record
output_path = f"work/dav213_report_{job_id}.json"
with open(output_path, "w", encoding="utf-8") as f:
    json.dump({
        "job_id": job_id,
        "user_id": final_row[1],
        "symbol": final_row[2],
        "status": final_row[3],
        "created_at": final_row[4],
        "duration_seconds": total_duration,
        "result_data": result_data,
        "report_text_sample": report_text[:3000]
    }, f, ensure_ascii=False, indent=2)

print(f"Dumped report result to {output_path}")

# 8. Deep Audit on Debate States and Machine Blocks
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

inv_bull_history = inv_debate.get("bull_history") or []
inv_bear_history = inv_debate.get("bear_history") or []
inv_history = inv_debate.get("history") or []
inv_judge = inv_debate.get("judge_decision")
inv_claims = inv_debate.get("claims")
inv_responded = inv_debate.get("responded_claim_ids")
inv_resolved = inv_debate.get("resolved_claims")

assert len(inv_bull_history) == 3, f"Expected 3 bull rounds, got {len(inv_bull_history)}"
assert len(inv_bear_history) == 3, f"Expected 3 bear rounds, got {len(inv_bear_history)}"
assert len(inv_history) == 6, f"Expected 6 total turns in investment history, got {len(inv_history)}"
assert inv_judge is not None and len(str(inv_judge).strip()) > 0, "investment judge_decision is missing or empty!"
assert inv_claims is not None, "investment claims is missing!"

# Check history for tags
for i, turn in enumerate(inv_history):
    turn_str = json.dumps(turn, ensure_ascii=False) if isinstance(turn, (dict, list)) else str(turn)
    assert "<!-- DEBATE_STATE" not in turn_str, f"investment history turn {i} contains DEBATE_STATE tag!"
    assert "<!-- RISK_STATE" not in turn_str, f"investment history turn {i} contains RISK_STATE tag!"

print(f"\n[Investment Debate Audit]")
print(f"  - count: {inv_count} (PASS)")
print(f"  - bull rounds: {len(inv_bull_history)} (PASS)")
print(f"  - bear rounds: {len(inv_bear_history)} (PASS)")
print(f"  - total history turns: {len(inv_history)} (PASS, verified tag-free)")
print(f"  - claims count: {len(inv_claims) if isinstance(inv_claims, list) else type(inv_claims)}")
print(f"  - responded_claim_ids: {len(inv_responded) if isinstance(inv_responded, list) else inv_responded}")
print(f"  - resolved_claims: {len(inv_resolved) if isinstance(inv_resolved, list) else inv_resolved}")
print(f"  - judge_decision: {str(inv_judge)[:200]}... (PASS)")

# Risk debate assertions
assert risk_debate is not None, "risk_debate_state is missing in result_data!"
risk_count = risk_debate.get("count")
assert risk_count == 9, f"Expected risk_debate_state.count == 9, got {risk_count}"

risk_agg = risk_debate.get("aggressive_history") or []
risk_cons = risk_debate.get("conservative_history") or []
risk_neu = risk_debate.get("neutral_history") or []
risk_history = risk_debate.get("history") or []
risk_judge = risk_debate.get("judge_decision")

assert len(risk_agg) == 3, f"Expected 3 aggressive rounds, got {len(risk_agg)}"
assert len(risk_cons) == 3, f"Expected 3 conservative rounds, got {len(risk_cons)}"
assert len(risk_neu) == 3, f"Expected 3 neutral rounds, got {len(risk_neu)}"
assert len(risk_history) == 9, f"Expected 9 total turns in risk history, got {len(risk_history)}"
assert risk_judge is not None and len(str(risk_judge).strip()) > 0, "risk judge_decision is missing or empty!"

for i, turn in enumerate(risk_history):
    turn_str = json.dumps(turn, ensure_ascii=False) if isinstance(turn, (dict, list)) else str(turn)
    assert "<!-- DEBATE_STATE" not in turn_str, f"risk history turn {i} contains DEBATE_STATE tag!"
    assert "<!-- RISK_STATE" not in turn_str, f"risk history turn {i} contains RISK_STATE tag!"

print(f"\n[Risk Debate Audit]")
print(f"  - count: {risk_count} (PASS)")
print(f"  - aggressive rounds: {len(risk_agg)} (PASS)")
print(f"  - conservative rounds: {len(risk_cons)} (PASS)")
print(f"  - neutral rounds: {len(risk_neu)} (PASS)")
print(f"  - total history turns: {len(risk_history)} (PASS, verified tag-free)")
print(f"  - judge_decision: {str(risk_judge)[:200]}... (PASS)")

# Risk feedback assertions
assert risk_feedback is not None, "risk_feedback_state is missing in result_data!"
print(f"\n[Risk Feedback Audit]")
print(f"  - risk_feedback_state: {str(risk_feedback)[:200]}... (PASS)")

# Report Service validate_report_machine_blocks
print("\n[Report Service Machine Block Validation]")
from api.services.report_service import validate_report_machine_blocks
validate_report_machine_blocks(result_data)
print("validate_report_machine_blocks(result_data): PASSED (no exception raised)")

# 9. Check persistent user config after analyze
print("\n[Step 9] Checking persistent config after analyze...")
post_cfg_res = requests.get(f"{BASE_URL}/v1/config", headers=headers)
assert post_cfg_res.status_code == 200
post_cfg = post_cfg_res.json()
print(f"Post-analysis config: max_debate_rounds={post_cfg.get('max_debate_rounds')}, max_risk_discuss_rounds={post_cfg.get('max_risk_discuss_rounds')}")
assert post_cfg.get("max_debate_rounds") == 3, f"Persistent config mutated! Expected debate=3, got {post_cfg.get('max_debate_rounds')}"
assert post_cfg.get("max_risk_discuss_rounds") == 1, f"Persistent config mutated! Expected risk=1, got {post_cfg.get('max_risk_discuss_rounds')}"
print("Assertion PASSED: Persistent user config remained unchanged (debate=3, risk=1).")

# 10. Print Summary
print("\n=== FINAL AUDIT SUMMARY ===")
print(f"Report ID: {job_id}")
print(f"User ID: {final_row[1]}")
print(f"Status: {final_row[3]}")
print(f"Duration: {total_duration:.1f}s ({total_duration/60:.2f} mins)")
print(f"Report text length: {len(report_text)} chars")
print(f"Investment debate count: {inv_count} (Bull: {len(inv_bull_history)}, Bear: {len(inv_bear_history)})")
print(f"Risk debate count: {risk_count} (Aggressive: {len(risk_agg)}, Conservative: {len(risk_cons)}, Neutral: {len(risk_neu)})")
print(f"validate_report_machine_blocks: PASSED")
print(f"Persistent config: debate={post_cfg.get('max_debate_rounds')}, risk={post_cfg.get('max_risk_discuss_rounds')}")
print("ALL AUDIT ASSERTIONS COMPLETED AND PASSED!")
