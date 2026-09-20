import requests
import json
import time
import sqlite3
import datetime
import sys

BASE_URL = "http://127.0.0.1:8000"
DB_PATH = "data/tradingagents.db"
TARGET_EMAIL = "davidliu022305@gmail.com"
EXPECTED_USER_ID = "429163f7-50b6-4982-8bdf-96ae99506843"
SYMBOL = "600036.SH"

print(f"=== Starting DAV-209 Verification at {datetime.datetime.now()} ===")

# 1. Login
print("\n[Step 1] Logging in...")
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
assert me_res.status_code == 200
assert me_res.json()["id"] == EXPECTED_USER_ID
print(f"/v1/auth/me verified: {me_res.json()}")

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
print("\n[Step 7] Fetching completed report and auditing result_data...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT id, user_id, symbol, status, created_at, report_text, result_data FROM reports WHERE id=?", (job_id,))
final_row = cursor.fetchone()
conn.close()

assert final_row is not None
report_text = final_row[5] or ""
raw_result_data = final_row[6]
result_data = json.loads(raw_result_data) if raw_result_data else {}

# Save audit json dump for record
with open(f"work/dav209_report_{job_id}.json", "w", encoding="utf-8") as f:
    json.dump({
        "job_id": job_id,
        "user_id": final_row[1],
        "symbol": final_row[2],
        "status": final_row[3],
        "created_at": final_row[4],
        "duration_seconds": total_duration,
        "result_data": result_data,
        "report_text_sample": report_text[:2000]
    }, f, ensure_ascii=False, indent=2)

print(f"Dumped report result to work/dav209_report_{job_id}.json")

# Deep Audit on Debate States
print("\n=== DEEP AUDIT OF RESULT DATA ===")

inv_debate = result_data.get("investment_debate_state")
risk_debate = result_data.get("risk_debate_state")
risk_feedback = result_data.get("risk_feedback_state")

print(f"investment_debate_state present: {inv_debate is not None}")
print(f"risk_debate_state present: {risk_debate is not None}")
print(f"risk_feedback_state present: {risk_feedback is not None}")

# Investment debate checks
assert inv_debate is not None, "investment_debate_state is missing in result_data!"
inv_count = inv_debate.get("count")
inv_bull_history = inv_debate.get("bull_history") or []
inv_bear_history = inv_debate.get("bear_history") or []
inv_history = inv_debate.get("history") or []
inv_judge = inv_debate.get("judge_decision")

print(f"\n[Investment Debate]")
print(f"  - count: {inv_count}")
print(f"  - bull_history rounds: {len(inv_bull_history) if isinstance(inv_bull_history, list) else 'text/other'}")
print(f"  - bear_history rounds: {len(inv_bear_history) if isinstance(inv_bear_history, list) else 'text/other'}")
print(f"  - history entries: {len(inv_history) if isinstance(inv_history, list) else 'text/other'}")
print(f"  - judge_decision: {str(inv_judge)[:200]}...")

# Risk debate checks
assert risk_debate is not None, "risk_debate_state is missing in result_data!"
risk_count = risk_debate.get("count")
risk_agg = risk_debate.get("aggressive_history") or []
risk_cons = risk_debate.get("conservative_history") or []
risk_neu = risk_debate.get("neutral_history") or []
risk_history = risk_debate.get("history") or []
risk_judge = risk_debate.get("judge_decision")

print(f"\n[Risk Debate]")
print(f"  - count: {risk_count}")
print(f"  - aggressive rounds: {len(risk_agg) if isinstance(risk_agg, list) else 'text/other'}")
print(f"  - conservative rounds: {len(risk_cons) if isinstance(risk_cons, list) else 'text/other'}")
print(f"  - neutral rounds: {len(risk_neu) if isinstance(risk_neu, list) else 'text/other'}")
print(f"  - history entries: {len(risk_history) if isinstance(risk_history, list) else 'text/other'}")
print(f"  - judge_decision: {str(risk_judge)[:200]}...")

# Risk feedback checks
assert risk_feedback is not None, "risk_feedback_state is missing in result_data!"
print(f"\n[Risk Feedback]")
print(f"  - risk_feedback_state: {risk_feedback}")

# 8. Check persistent user config after analyze
print("\n[Step 8] Checking persistent config after analyze...")
post_cfg_res = requests.get(f"{BASE_URL}/v1/config", headers=headers)
assert post_cfg_res.status_code == 200
post_cfg = post_cfg_res.json()
print(f"Post-analysis config: max_debate_rounds={post_cfg.get('max_debate_rounds')}, max_risk_discuss_rounds={post_cfg.get('max_risk_discuss_rounds')}")
assert post_cfg.get("max_debate_rounds") == 3, f"Persistent config mutated! Expected debate=3, got {post_cfg.get('max_debate_rounds')}"
assert post_cfg.get("max_risk_discuss_rounds") == 1, f"Persistent config mutated! Expected risk=1, got {post_cfg.get('max_risk_discuss_rounds')}"
print("Assertion PASSED: Persistent user config remained unchanged (debate=3, risk=1).")

# 9. Print Summary
print("\n=== FINAL AUDIT SUMMARY ===")
print(f"Report ID: {job_id}")
print(f"User ID: {final_row[1]}")
print(f"Duration: {total_duration:.1f}s ({total_duration/60:.2f} mins)")
print(f"Report text length: {len(report_text)} chars")
print(f"Investment debate count: {inv_count}")
print(f"Risk debate count: {risk_count}")
print("ALL ASSERTIONS PASSED!")
