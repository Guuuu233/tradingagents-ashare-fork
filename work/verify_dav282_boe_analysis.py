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
SYMBOL = "000725.SZ"
EXPECTED_COMMIT_SHA = "cfc1e22ce8b060a18017acbfa8f4d92144df9cd5"

print(f"=== Starting DAV-282 Verification at {datetime.datetime.now()} ===")

# 1. Healthz check
print("\n[Step 1] Checking /healthz...")
hz_res = requests.get(f"{BASE_URL}/healthz")
assert hz_res.status_code == 200, f"/healthz returned {hz_res.status_code}"
hz_data = hz_res.json()
print(f"/healthz: {json.dumps(hz_data, ensure_ascii=False)}")
actual_sha = hz_data.get("commit_sha")
assert actual_sha == EXPECTED_COMMIT_SHA, f"Wrong commit_sha: {actual_sha} != {EXPECTED_COMMIT_SHA}"
print(f"Healthz verified: commit_sha is {EXPECTED_COMMIT_SHA}")

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

# 5. Trigger Analyze for 000725.SZ with config_overrides
existing_job_id = None
for arg in sys.argv[1:]:
    if arg.startswith("--job-id="):
        existing_job_id = arg.split("=")[1]
    elif arg == "--job-id" and sys.argv.index(arg) + 1 < len(sys.argv):
        existing_job_id = sys.argv[sys.argv.index(arg) + 1]

if existing_job_id:
    job_id = existing_job_id
    total_duration = 0.0
    status = "completed"
    print(f"\n[Step 5-7 SKIPPED] Using existing completed job_id={job_id}")
else:
    print(f"\n[Step 5] Initiating analysis for {SYMBOL} with config_overrides={{\"max_debate_rounds\":3, \"max_risk_discuss_rounds\":3}} (no trade_date)...")
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
output_path = f"work/dav282_report_{job_id}.json"
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

if status_db != "completed":
    print(f"FATAL: Job ended in status '{status_db}'! error_message: {error_message}")
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

# 10. Audit DataCollector, Industry linkage & Knowledge Injection
print("\n[Industry Linkage & Macro/Fundamentals Audit]")
market_data_context = result_data.get("market_data_context") or {}
industry_mapping = result_data.get("industry") or market_data_context.get("industry")
print(f"  - Industry mapping: {industry_mapping}")

macro_report = result_data.get("macro_report") or ""
fundamentals_report = result_data.get("fundamentals_report") or ""
macro_context = result_data.get("macro_context") or ""
fundamentals_context = result_data.get("fundamentals_context") or ""

full_text = f"{report_text}\n{macro_report}\n{fundamentals_report}\n{macro_context}\n{fundamentals_context}"

# Check industry linkage in macro report / context
linkage_injected = (
    "【产业链联想数据】" in full_text
    or "产业链" in full_text
    or "消费电子" in full_text
    or "面板" in full_text
    or "LME" in full_text
)
print(f"  - Industry linkage injected in macro/report: {linkage_injected}")
assert linkage_injected, "Industry linkage not found in macro/report!"

# Check keyword counts
keywords = ["上游", "下游", "议价权", "产业链", "消费电子", "面板", "京东方", "【产业链联想数据】", "【数据缺失】", "【历史案例复盘】", "【历史案例未命中】"]
kw_counts = {}
for kw in keywords:
    kw_counts[kw] = full_text.count(kw)
    print(f"  - Keyword '{kw}': {kw_counts[kw]} occurrences")

assert kw_counts["上游"] + kw_counts["下游"] + kw_counts["议价权"] > 0, "No upstream/downstream/bargaining power expressions found!"

# 11. Audit Historical Cases DB Record
print("\n[Step 11] Auditing historical_cases DB table for new record...")
conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()
cursor.execute("SELECT id, report_id, symbol, industry, trade_date, decision, direction, confidence, claims, run_sha, eval_date, actual_change_pct, actual_outcome, is_error, created_at FROM historical_cases WHERE report_id=?", (job_id,))
case_row = cursor.fetchone()
conn.close()

assert case_row is not None, f"No historical_cases row found for report_id={job_id}!"
print(f"Found historical_cases row: {case_row}")
case_id, c_rep_id, c_symbol, c_industry, c_trade_date, c_dec, c_dir, c_conf, c_claims, c_sha, c_eval_date, c_act_pct, c_act_out, c_err, c_cat = case_row

assert c_symbol == SYMBOL, f"Expected symbol={SYMBOL}, got {c_symbol}"
assert c_trade_date == "2026-08-21", f"Expected trade_date=2026-08-21, got {c_trade_date}"
assert c_act_pct is None, f"Expected actual_change_pct=None for future date, got {c_act_pct}"
print(f"  - historical_cases.id: {case_id}")
print(f"  - historical_cases.symbol: {c_symbol} (PASS)")
print(f"  - historical_cases.trade_date: {c_trade_date} (PASS)")
print(f"  - historical_cases.decision: {c_dec}, direction: {c_dir}, confidence: {c_conf}")
print(f"  - historical_cases.run_sha: {c_sha}")
print(f"  - historical_cases.eval_date: {c_eval_date}")
print(f"  - historical_cases.actual_change_pct: {c_act_pct} (None as expected for future T+1)")
print(f"  - historical_cases.actual_outcome: {c_act_out}")

# 12. Check persistent user config after analyze
print("\n[Step 12] Checking persistent config after analyze...")
post_cfg_res = requests.get(f"{BASE_URL}/v1/config", headers=headers)
assert post_cfg_res.status_code == 200
post_cfg = post_cfg_res.json()
print(f"Post-analysis config: max_debate_rounds={post_cfg.get('max_debate_rounds')}, max_risk_discuss_rounds={post_cfg.get('max_risk_discuss_rounds')}")
assert post_cfg.get("max_debate_rounds") == 3, f"Persistent config mutated! Expected debate=3, got {post_cfg.get('max_debate_rounds')}"
assert post_cfg.get("max_risk_discuss_rounds") == 1, f"Persistent config mutated! Expected risk=1, got {post_cfg.get('max_risk_discuss_rounds')}"
print("Assertion PASSED: Persistent user config remained unchanged (debate=3, risk=1).")

# 13. Print Summary
print("\n=== FINAL AUDIT SUMMARY ===")
print(f"Report ID: {job_id}")
print(f"User ID: {user_id_db}")
print(f"Symbol: {symbol_db}")
print(f"Trade Date: {c_trade_date}")
print(f"Status: {status_db}")
print(f"Duration: {total_duration:.1f}s ({total_duration/60:.2f} mins)")
print(f"Report text length: {len(report_text)} chars")
print(f"Investment debate count: {inv_count} (Bull: {len(inv_bull_history)}, Bear: {len(inv_bear_history)})")
print(f"Risk debate count: {risk_count} (Aggressive: {len(risk_agg)}, Conservative: {len(risk_cons)}, Neutral: {len(risk_neu)})")
print(f"validate_report_machine_blocks: PASSED")
print(f"Industry Linkage injected: {linkage_injected}")
print(f"Historical Case Recorded: ID={case_id}, trade_date={c_trade_date}, actual_change_pct={c_act_pct}")
print(f"Persistent config: debate={post_cfg.get('max_debate_rounds')}, risk={post_cfg.get('max_risk_discuss_rounds')}")
print("ALL AUDIT ASSERTIONS COMPLETED AND PASSED!")
