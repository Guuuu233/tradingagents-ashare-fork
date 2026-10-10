#!/usr/bin/env python3

# 【压缩存储前适用】(DAV-1773 B-6d)：本脚本直读 reports.result_data 明文列，
# 仅适用于未启用压缩存储（REPORT_STORAGE_MODE=compressed）的数据库。
# 压缩库请改用 tradingagents.storage.compressed_json.decode_result_data，
# 或在 SQL 中取 COALESCE(result_data_zst, result_data) 后交给它解码；
# 参考实现：scripts/backfill_tplus5_shadow.py 的 _rd_text/_rd_col 模式。
"""DAV-1311 E-04 辅助：对每个含 e04_* 命中的档位，打印命中关键词的句子。"""
import argparse, json, re, sqlite3, sys

KW = re.compile("已定价|priced?[- ]?in|消化|透支|超预期|超出预期|好于预期|不及预期|低于预期|未达预期|重复计入|加票|擅自断言财务|double_count", re.I)
SPLIT = re.compile(r"(?<=[。！？；!?])\s*|\n+")
ALARM = re.compile(r"\n*\[系统硬闸告警\].*$", re.DOTALL)

ap = argparse.ArgumentParser(); ap.add_argument("--db", required=True); ap.add_argument("--out", required=True)
ap.add_argument("ids", nargs="*")
a = ap.parse_args()
idx = json.load(open(f"{a.out}/index.json", encoding="utf-8"))
con = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
sel = set(a.ids)
for e in idx:
    cats = e.get("cats") or []
    if not any(c.startswith("e04") for c in cats):
        continue
    key = f"{e['report_id']}:{e['horizon']}"
    if sel and key not in sel and e['report_id'] not in sel:
        continue
    rd = json.loads(con.execute("SELECT result_data FROM reports WHERE id=?", (e["report_id"],)).fetchone()[0])
    hz = rd[e["horizon"]]
    deb = hz.get("investment_debate_state") or {}
    mv = hz.get("manager_verdict") or {}
    jd = ALARM.sub("", str(deb.get("judge_decision") or ""))
    print("=" * 90)
    print(key, e["symbol"], e["trade_date"])
    for name, txt in (("reason", mv.get("reason")), ("plan", mv.get("investment_plan")), ("judge", jd)):
        hits = [s.strip() for s in SPLIT.split(str(txt or "")) if KW.search(s)]
        for s in hits:
            print(f"  [{name}] {s[:280]}")
