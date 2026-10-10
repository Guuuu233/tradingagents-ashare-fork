# 【压缩存储前适用】(DAV-1773 B-6d)：本脚本直读 reports.result_data 明文列，
# 仅适用于未启用压缩存储（REPORT_STORAGE_MODE=compressed）的数据库。
# 压缩库请改用 tradingagents.storage.compressed_json.decode_result_data，
# 或在 SQL 中取 COALESCE(result_data_zst, result_data) 后交给它解码；
# 参考实现：scripts/backfill_tplus5_shadow.py 的 _rd_text/_rd_col 模式。
import sqlite3, json, sys
sys.path.insert(0,"/Users/davidliu/Documents/TradingAgents-AShare")
from tradingagents.agents.utils.evidence_verifier import (
    EvidenceFactualTruthEvaluator, SEVEN_REPORT_KEYS,
    _extract_metric_keywords, extract_bound_numbers, _METRIC_CANONICAL_MAP,
)
c=sqlite3.connect("file:data/tradingagents.db?mode=ro",uri=True); c.execute("PRAGMA query_only=ON")
cols=[d[1] for d in c.execute("pragma table_info(reports)")]
row=dict(zip(cols,c.execute("select * from reports where id='9e2dd38b79a04819ae619f0078cefbf5'").fetchone()))
seven={k:(row.get(k) or "") for k in SEVEN_REPORT_KEYS}
mdc=json.loads(row['result_data']).get('market_data_context')

ev6="基本面报告显示2026H1每股净资产45.40元对应PB仅0.89倍且经营现金流达3046亿元"
ev10="基本面报告显示2026H1每股净资产45.40元且现金流达3046亿元提供极强反脆弱缓冲"
E=EvidenceFactualTruthEvaluator()
for tag, ev in (("INV-6", ev6), ("INV-10", ev10)):
    print(f"########## {tag} ##########")
    print("  keywords:", sorted(_extract_metric_keywords(ev)))
    bns=extract_bound_numbers(ev)
    print("  抽出的数:")
    for b in bns: print(f"     raw={b.raw!r} val={b.val} unit={b.unit!r} metric={b.metric!r} raw_metric={getattr(b,'raw_metric',None)!r}")
    r=E.evaluate_single_evidence(ev, seven, market_data_context=mdc,
        analysis_baseline_date=row['trade_date'], claim_id=tag)
    print(f"  => status={r['status']}  role={r['matched_role']}  is_fatal={r['is_fatal']}")
    print(f"     details={r['details'][:140]}")
    print()
