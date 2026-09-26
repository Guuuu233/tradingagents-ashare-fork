"""v2 打分（总控 2026-09-26，按 GPT 评审意见与项目 return_labels 契约修正）：
- 入场 = T+1 开盘（前复权），出场 = T+N 收盘；基准沪深300同口径；
- 只统计 gemini-3.8 样本（按 model_config_snapshot 分组）；
- 三层分开：①最终可执行 BUY/SELL；②研究经理原结论 BUY/SELL 被降级；③观望/持有但有方向；
- 另报 |超额|<1% 的“接近持平”个数。"""
import json, sqlite3, collections
from pathlib import Path
import akshare as ak
HERE = Path(__file__).resolve().parent
con = sqlite3.connect(f"file:{HERE.parents[1]/'data'/'tradingagents.db'}?mode=ro", uri=True)
OFF = {"short_term": 10, "medium_term": 40}
jobs = [json.loads(l) for l in (HERE/"manifest.jsonl").read_text().splitlines()]
jobs = {j["job_id"]: j for j in jobs if j["event"] == "finished" and j["status"] == "completed"}
px = {}
def bars(sym):
    if sym not in px:
        c = ("sh" if sym.endswith(".SH") else "sz") + sym[:6]
        q = ak.stock_zh_a_daily(symbol=c, start_date="20260501", end_date="20260925", adjust="qfq")
        px[sym] = {str(d)[:10]: (float(o), float(cl)) for d, o, cl in zip(q.date, q.open, q.close)}
    return px[sym]
b = ak.stock_zh_index_daily(symbol="sh000300")
bench = {str(d)[:10]: (float(o), float(c)) for d, o, c in zip(b.date, b.open, b.close) if str(d) >= "2026-05-01"}
cal = sorted(bench)
rows = []
for rid, j in jobs.items():
    rd = json.loads(con.execute("select result_data from reports where id=?", (rid,)).fetchone()[0])
    snap = rd.get("model_config_snapshot") or {}
    model = "+".join(sorted({v.get("model_name") for v in snap.values() if isinstance(v, dict)}))
    T = rd["trade_date"]; sym = rd["symbol"]; p = bars(sym); i0 = cal.index(T)
    for h, n in OFF.items():
        s = rd.get(h) or {}; ds = s.get("decision_status") or {}
        mv = (s.get("manager_verdict") or {}).get("trade_action")
        act, st, dirn = ds.get("trade_action"), ds.get("analysis_status"), ds.get("direction")
        if i0 + n >= len(cal): continue
        e, x = cal[i0 + 1], cal[i0 + n]
        ret = p[x][1] / p[e][0] - 1 if e in p and x in p else None
        br = bench[x][1] / bench[e][0] - 1
        if act in ("BUY", "SELL"): tier, d = "①最终买卖", {"BUY": "BULL", "SELL": "BEAR"}[act]
        elif (mv or "").upper() in ("BUY", "SELL"): tier, d = "②被降级的买卖", {"BUY": "BULL", "SELL": "BEAR"}[mv.upper()]
        elif st == "VALID" and dirn in ("BULL", "BEAR"): tier, d = "③观望但有方向", dirn
        else: tier, d = "无方向/弃权", None
        rows.append(dict(model=model, sym=sym, sector=j.get("sector"), T=T, h=h, tier=tier, dir=d,
                         ret=ret, excess=None if ret is None else ret - br))
(HERE / "score_v2.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))
def hit(r, key):
    v = r[key]; return (v > 0) if r["dir"] == "BULL" else (v < 0)
for m in sorted({r["model"] for r in rows}):
    for h in OFF:
        R = [r for r in rows if r["model"] == m and r["h"] == h]
        print(f"\n== {m} {h}: {len(R)} 份")
        for t in ("①最终买卖", "②被降级的买卖", "③观望但有方向", "无方向/弃权"):
            T_ = [r for r in R if r["tier"] == t]
            if t == "无方向/弃权": print(f"  {t}: {len(T_)}"); continue
            a = sum(hit(r, "ret") for r in T_); x = sum(hit(r, "excess") for r in T_)
            flat = sum(abs(r["excess"]) < 0.01 for r in T_)
            print(f"  {t}: {len(T_)}  绝对方向对 {a}  跑赢/跑输大盘对 {x}  |超额|<1% {flat}")
