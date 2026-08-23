#!/usr/bin/env python3
"""Measure bull/bear symmetry across 3 debate rounds. Structural metrics only."""
import json, re, sqlite3

DB = "/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db"
RIDS = {"600900": "597f6cf371114a3b9844112238a0f1a9", "000333": "3c09051e7e364d859dfbe5f1af7cc2c9",
        "600276": "ba255b88dfa446279c2d6e9529be6f5e"}
con = sqlite3.connect(DB)
con.row_factory = sqlite3.Row

NUM = re.compile(r"\d+(?:\.\d+)?\s*(?:亿|万|%|元|股|倍|pct|pct|bp|日|天|板|次|家|年|月)")
SRC = {"资金": re.compile(r"主力资金报告|资金流向|超大单|大单|净流入|净流出|两融|融资"),
       "量价": re.compile(r"量价报告|成交量|量比|VWMA|K线|地量|放量|缩量"),
       "技术": re.compile(r"市场报告|技术|均线|EMA|SMA|RSI|MACD|布林|支撑|阻力"),
       "基本面": re.compile(r"基本面报告|营收|毛利|ROE|现金流|净利|应收"),
       "宏观": re.compile(r"宏观报告|ETF|利率|财政|美债|降息|LME|铜价"),
       "新闻": re.compile(r"新闻报告|公告|中报|回购|分红|减持"),
       "情绪": re.compile(r"情绪报告|舆情|涨停|连板|风险偏好")}

agg = {}
for tag, rid in RIDS.items():
    d = json.loads(con.execute("SELECT result_data FROM reports WHERE id=?", (rid,)).fetchone()[0])
    ids = d["investment_debate_state"]
    msgs = ids["round_messages"]
    claims = {c["claim_id"]: c for c in ids["claims"]}
    mv = d["manager_verdict"]
    ces = mv.get("claim_evidence_summary") or {}

    r = {"msgs": {}, "claims": {}, "verdict": {}}
    for m in msgs:
        side = "bull" if m["speaker_key"] == "Bull" else "bear"
        acc = r["msgs"].setdefault(side, {"n": 0, "chars": 0, "nums": 0, "cats": set(), "gain": [], "maxsim": [],
                                          "viol": 0, "responded": 0, "avail_opp_open": 0, "newclaims": 0})
        prose = m.get("cleaned_prose") or ""
        acc["n"] += 1
        acc["chars"] += len(prose)
        acc["nums"] += len(NUM.findall(prose))
        for cname, rx in SRC.items():
            if rx.search(prose): acc["cats"].add(cname)
        acc["gain"].append(m.get("information_gain_score") or 0)
        acc["maxsim"].append(m.get("max_similarity") or 0)
        acc["newclaims"] += len(m.get("new_claim_ids") or [])
        acc["responded"] += len(set(m.get("responded_claim_ids") or []))
        # opponent claims open before this message: opponent stance claims with round_index < this msg's round OR same round earlier index
        mi = m.get("message_index")
        opp = "Bear" if side == "bull" else "Bull"
        opp_open = [cid for cid, c in claims.items()
                    if c.get("speaker_key") == opp and c.get("status") != "resolved" and c.get("round_index", 0) * 2 - (1 if opp == "Bull" else 0) < mi]
        acc["avail_opp_open"] += len(opp_open)
        for a in (m.get("attempts") or []):
            if a.get("parse_status") == "invalid_protocol": acc["viol"] += 1

    for c in ids["claims"]:
        side = "bull" if c["speaker_key"] == "Bull" else "bear"
        acc = r["claims"].setdefault(side, {"n": 0, "conf": []})
        acc["n"] += 1
        acc["conf"].append(c.get("confidence") or 0)

    # verified rates by side from claim_evidence_summary
    for cid, cs in ces.items():
        side = "bull" if cs.get("speaker_key") == "Bull" else "bear"
        acc = r["verdict"].setdefault(side, {"claims": 0, "ev_total": 0, "ev_verified": 0,
                                             "decisions": {"adopt": 0, "reject": 0, "partial": 0}})
        acc["claims"] += 1
        cnt = cs.get("counts") or {}
        acc["ev_total"] += cnt.get("total") or 0
        acc["ev_verified"] += cnt.get("verified") or 0
        dec = cs.get("decision")
        if dec in acc["decisions"]: acc["decisions"][dec] += 1

    r["winner"] = mv.get("winner")
    r["adopted"] = mv.get("adopted_claim_ids")
    r["rejected"] = mv.get("rejected_claim_ids")
    agg[tag] = r

    print(f"\n===== {tag} ({rid[:8]}) winner={mv.get('winner')} =====")
    for side in ("bull", "bear"):
        m = r["msgs"].get(side, {})
        c = r["claims"].get(side, {})
        v = r["verdict"].get(side, {})
        vr = (v.get("ev_verified", 0) / v.get("ev_total", 1)) * 100 if v.get("ev_total") else 0
        resp_rate = (m.get("responded", 0) / m.get("avail_opp_open", 1)) * 100 if m.get("avail_opp_open") else None
        print(f"[{side}] msgs={m.get('n')} avg_chars={m.get('chars',0)//max(1,m.get('n',1))} "
              f"nums/msg={m.get('nums',0)/max(1,m.get('n',1)):.1f} cats={len(m.get('cats',set()))} "
              f"gain_avg={sum(m.get('gain',[0]))/max(1,len(m.get('gain',[1]))):.3f} "
              f"gain_min={min(m.get('gain',[1])) if m.get('gain') else '-'} "
              f"maxsim_max={max(m.get('maxsim',[0])) if m.get('maxsim') else '-'} "
              f"viol={m.get('viol')} newclaims={m.get('newclaims')} "
              f"resp_rate={f'{resp_rate:.0f}%' if resp_rate is not None else 'n/a(首发言)'}")
        print(f"       claims={c.get('n')} conf_avg={sum(c.get('conf',[0]))/max(1,len(c.get('conf',[1]))):.2f} | "
              f"verdict: claims={v.get('claims')} ev_verified={v.get('ev_verified')}/{v.get('ev_total')} ({vr:.0f}%) decisions={v.get('decisions')}")

# totals
print("\n===== 3-round totals =====")
for side in ("bull", "bear"):
    T = {"n": 0, "chars": 0, "nums": 0, "cats": set(), "gain": [], "viol": 0, "responded": 0,
         "avail": 0, "claims": 0, "ev_v": 0, "ev_t": 0, "adopt": 0, "reject": 0, "conf": []}
    for tag, r in agg.items():
        m = r["msgs"].get(side, {}); c = r["claims"].get(side, {}); v = r["verdict"].get(side, {})
        T["n"] += m.get("n", 0); T["chars"] += m.get("chars", 0); T["nums"] += m.get("nums", 0)
        T["cats"] |= m.get("cats", set()); T["gain"] += m.get("gain", []); T["viol"] += m.get("viol", 0)
        T["responded"] += m.get("responded", 0); T["avail"] += m.get("avail_opp_open", 0)
        T["claims"] += c.get("n", 0); T["conf"] += c.get("conf", [])
        T["ev_v"] += v.get("ev_verified", 0); T["ev_t"] += v.get("ev_total", 0)
        T["adopt"] += (v.get("decisions") or {}).get("adopt", 0)
        T["reject"] += (v.get("decisions") or {}).get("reject", 0)
    print(f"[{side}] msgs={T['n']} chars/msg={T['chars']//max(1,T['n'])} nums/msg={T['nums']/max(1,T['n']):.1f} "
          f"cats={len(T['cats'])} gain_avg={sum(T['gain'])/max(1,len(T['gain'])):.3f} viol={T['viol']} "
          f"resp_rate={(T['responded']/max(1,T['avail'])*100):.0f}% claims={T['claims']} "
          f"conf_avg={sum(T['conf'])/max(1,len(T['conf'])):.2f} verified={T['ev_v']}/{T['ev_t']}={T['ev_v']/max(1,T['ev_t'])*100:.0f}% "
          f"adopt={T['adopt']} reject={T['reject']}")
print("winners:", {t: r["winner"] for t, r in agg.items()})
