"""Read-only, zero-model replay of every report/horizon carrying claim_evidence_summary.

Run from repository root with the locked Python 3.10 interpreter. Reads live SQLite
via mode=ro, never touches provider endpoints or report rows.
"""
import collections
import hashlib
import json
import sqlite3
import subprocess
import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from tradingagents.agents.utils.decision_status import status_from_manager_verdict as new_status

old_source = subprocess.check_output([
    "git", "show", "9e0fb9f4f95ec0c20474f3909ca39037161186e3:tradingagents/agents/utils/decision_status.py"
], text=True)
old_module = types.ModuleType("dav1377_old_decision_status")
sys.modules[old_module.__name__] = old_module
exec(compile(old_source, "9e0fb9f4:decision_status.py", "exec"), old_module.__dict__)
old_status = old_module.status_from_manager_verdict

DB = Path('/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db')
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
count = con.execute("SELECT count(*) FROM reports").fetchone()[0]
counts = collections.Counter()
bases = collections.Counter()
changes = []
errors = []
seen = 0
snapshot_hash = hashlib.sha256()

for rid, raw in con.execute("SELECT id, result_data FROM reports ORDER BY id"):
    snapshot_hash.update(str(rid).encode('utf-8') + b'\0')
    snapshot_hash.update(raw.encode('utf-8') if isinstance(raw, str) else repr(raw).encode('utf-8'))
    snapshot_hash.update(b'\0')
    try:
        data = json.loads(raw) if isinstance(raw, str) else raw
    except (TypeError, ValueError):
        continue
    if not isinstance(data, dict):
        continue
    candidates = []
    for name in ("short_term", "medium_term", "primary"):
        item = data.get(name)
        if isinstance(item, dict):
            candidates.append((name, item))
    candidates.append(("root", data))
    fingerprints = set()
    for name, item in candidates:
        debate = item.get("investment_debate_state")
        if not isinstance(debate, dict) or not isinstance(debate.get("claim_evidence_summary"), dict) or not debate["claim_evidence_summary"]:
            continue
        mv = item.get("manager_verdict") or debate.get("manager_verdict")
        if not isinstance(mv, dict):
            errors.append((rid, name, "missing manager_verdict"))
            continue
        fingerprint = json.dumps((debate["claim_evidence_summary"], mv.get("adopted_claim_ids"), mv.get("rejected_claim_ids")), sort_keys=True, ensure_ascii=False)
        if fingerprint in fingerprints:
            continue
        fingerprints.add(fingerprint)
        # Persisted nested decision_status is the OLD result; remove it to force
        # both implementations to calculate the verdict from the original ledger.
        mv = dict(mv)
        mv.pop("decision_status", None)
        kw = dict(investment_debate_state=debate, market_data_context=item.get("market_data_context"))
        try:
            old = old_status(mv, **kw)
            new = new_status(mv, **kw)
        except (TypeError, ValueError, KeyError, AttributeError, ZeroDivisionError) as e:
            errors.append((rid, name, f"{type(e).__name__}: {e}"))
            continue
        seen += 1
        old_tuple = (old.analysis_status, old.confirmation_state, old.trade_action)
        new_tuple = (new.analysis_status, new.confirmation_state, new.trade_action)
        counts[(old_tuple, new_tuple)] += 1
        basis = mv.get("direction_basis")
        basis_status = str(basis.get("status") or "missing") if isinstance(basis, dict) else "missing"
        bases[(old_tuple, new_tuple, basis_status)] += 1
        if old_tuple != new_tuple:
            changes.append((rid, name, old_tuple, new_tuple, basis_status, old.reason_codes, new.reason_codes))

print('report_rows', count, 'result_data_rows_sha256', snapshot_hash.hexdigest(),
      'replayed_distinct_horizons', seen, 'errors', len(errors))
for (before, after), n in sorted(counts.items(), key=lambda p: (-p[1], str(p[0]))):
    print('CROSS', n, '/'.join(before), '=>', '/'.join(after))
for (before, after, basis), n in sorted(bases.items(), key=lambda p: (-p[1], str(p[0]))):
    print('BASIS', n, '/'.join(before), '=>', '/'.join(after), basis)
exec_actions = {'BUY','SELL','HOLD'}
unsafe = [x for x in changes if x[2][2] not in exec_actions and x[3][2] in exec_actions]
print('non_executable_to_executable', len(unsafe), 'changed', len(changes))
for x in changes:
    print('FLIP', *x)
for x in errors:
    print('ERROR', *x)
if errors or unsafe:
    sys.exit(1)
