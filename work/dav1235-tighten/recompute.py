"""DAV-1235 零 LLM 重算：a181e4a 基线 vs 候选（price-ref 精度收紧）。

用法（仓库根目录，锁定解释器）：
    env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python \
        work/dav1235-tighten/recompute.py --impl baseline|candidate

输入：
- 50 份冻结 state：/Users/davidliu/Documents/TradingAgents-AShare/work/dav1223-b0-audit/ext/
  （只读；pool 由同目录 snapshots + snapshot_manifest.json 重建，与 W4 口径一致）
- 生产报告 4390ddfd08a2406d8784d4c66e0af28e（600519.SH@2026-09-23）与坏锚
  b188060fa75045bd9e52d1eacd265372（000858.SZ@2026-05-22）：
  生产库 file:...?mode=ro 只读读取，不写库。

4390ddfd 的 pool 为「有证据字段重建」：result_data 未持久化 price_ref_source，
唯一有据可依的字段级来源是 market_report 自报指标值（vwma/boll/ema/sma/macd/
rsi/atr）与现价收盘 1251.24（→ derived.limit_up/down@2026-09-23）。
open/high/low 不虚构——该 bar 仅 close 参与字段级匹配。b188060f 无快照可重建
pool，按 pool=None 重算（不桥接，只会更保守，不影响 blocked 结论方向）。

baseline 实现 = 运行时 `git show a181e4a:tradingagents/agents/utils/price_ref_registry.py`
（D-040/D-036：不提交整份基线拷贝；out/*.json 不入库，仅在汇总记 SHA256）。
gate 两版共用 trunk evaluate_price_basis_gate（本卡不改 gate 判定规则）。

输出：work/dav1235-tighten/out/{impl}.json + diff SUMMARY（candidate 跑时生成）。
"""

from __future__ import annotations

import argparse
import glob
import hashlib
import importlib.util
import json
import os
import pickle
import sqlite3
import sys
from typing import Any, Dict, Mapping, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "work", "dav1223-b0-audit"))

from corrected.pool import build_pool  # noqa: E402
from tradingagents.agents.utils.price_basis_gate import (  # noqa: E402
    evaluate_price_basis_gate,
)
from tradingagents.agents.utils.price_ref_registry import (  # noqa: E402
    MarketDataPool,
    NamedValue,
    REPORT_FIELDS,
)

PROD_REPO = "/Users/davidliu/Documents/TradingAgents-AShare"
AUDIT_ROOT = os.path.join(PROD_REPO, "work", "dav1223-b0-audit")
EXT = os.path.join(AUDIT_ROOT, "ext")
MANIFEST = os.path.join(AUDIT_ROOT, "snapshot_manifest.json")
PROD_DB = os.path.join(PROD_REPO, "data", "tradingagents.db")
BASELINE_GIT_PATH = "a181e4a:tradingagents/agents/utils/price_ref_registry.py"
OUT = os.path.join(HERE, "out")

EXTRA_STATE_KEYS = (
    "trade_date", "trade_action", "decision_status",
    "short_term", "medium_term", "result_data",
    "short_term_result", "medium_term_result",
)

REPORT_4390 = "4390ddfd08a2406d8784d4c66e0af28e"
REPORT_B188 = "b188060fa75045bd9e52d1eacd265372"

# market_report 自报的指标值（字段级 provenance，可追溯到报告文本本身）
IND_4390 = {
    "close_10_ema": 1265.55,
    "close_50_sma": 1301.78,
    "close_200_sma": 1331.73,
    "macd": -12.96,
    "rsi": 37.09,
    "boll": 1284.10,
    "boll_ub": 1329.68,
    "boll_lb": 1238.53,
    "atr": 19.26,
    "vwma": 1281.96,
}
CLOSE_4390 = 1251.24  # 「当前收盘价 1251.24 元」（market_report 明写）
TD_4390 = "2026-09-23"


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def load_impl(name: str):
    if name == "candidate":
        import tradingagents.agents.utils.price_ref_registry as m
        return m
    import subprocess
    import types
    src = subprocess.check_output(
        ["git", "show", BASELINE_GIT_PATH], cwd=REPO, text=True)
    m = types.ModuleType("registry_a181e4a")
    sys.modules["registry_a181e4a"] = m  # dataclass 处理需要模块已注册
    exec(compile(src, BASELINE_GIT_PATH, "exec"), m.__dict__)
    return m


def load_state(path: str) -> Dict[str, Any]:
    blob = pickle.load(open(path, "rb"))
    st = blob["final_state"] if isinstance(blob, dict) and "final_state" in blob else blob
    new = {f: st.get(f) for f in REPORT_FIELDS}
    for k in EXTRA_STATE_KEYS:
        if k in st:
            new[k] = st[k]
    return new


def collect_reports(st: Mapping[str, Any]) -> Dict[str, Any]:
    """与 audit_price_ref_registry 相同的报告归集（含 horizon 嵌套回填）。"""
    reports = {name: st.get(name) for name in REPORT_FIELDS}
    for sub_key in ("short_term", "medium_term", "result_data"):
        sub = st.get(sub_key)
        if isinstance(sub, Mapping):
            for name in REPORT_FIELDS:
                if name not in reports or reports[name] in (None, ""):
                    if isinstance(sub.get(name), str):
                        reports[name] = sub[name]
    return reports


def run_one(impl, st: Dict[str, Any], pool) -> Dict[str, Any]:
    reports = collect_reports(st)
    result = impl.build_price_ref_registry(
        reports, cutoff=st.get("trade_date"), pool=pool)
    st2 = dict(st)
    st2["price_refs"] = result["price_refs"]
    st2["price_basis_validation"] = result["validation"]
    gate = evaluate_price_basis_gate(st2)
    return {
        "status": gate["status"],
        "violations": gate["violations"],
        "refs": [
            {k: r.get(k) for k in (
                "ref_id", "value", "basis", "source", "provenance",
                "as_of", "context", "bridge_fields")}
            for r in result["price_refs"]
        ],
        "n_gaps": len(result["price_basis_gaps"]),
        "pool_bridge": result.get("pool_bridge") or [],
    }


def build_pool_4390(impl):
    """有证据字段重建的 MarketDataPool（见模块 docstring）。"""
    pool = impl.MarketDataPool(symbol="600519.SH", trade_date=TD_4390)
    pool.stock_data_basis = "vendor_qfq"
    bar = {"date": TD_4390, "open": None, "high": None,
           "low": None, "close": CLOSE_4390, "volume": None}
    pool.bars = [bar]
    nv = impl.NamedValue
    pool.named_values.append(nv(field=f"stock_data.{TD_4390}.close",
                                value=CLOSE_4390, as_of=TD_4390))
    pool.named_values.append(nv(field="stock_data.latest.close",
                                value=CLOSE_4390, as_of=TD_4390))
    pool.named_values.append(nv(field=f"derived.limit_up@{TD_4390}",
                                value=round(CLOSE_4390 * 1.10 + 1e-9, 2),
                                as_of=TD_4390))
    pool.named_values.append(nv(field=f"derived.limit_down@{TD_4390}",
                                value=round(CLOSE_4390 * 0.90 + 1e-9, 2),
                                as_of=TD_4390))
    for k, v in IND_4390.items():
        pool.indicators[k] = v
        pool.named_values.append(nv(field=f"indicators.{k}", value=v,
                                    as_of=TD_4390))
    return pool


def load_prod_report(report_id: str) -> Dict[str, Any]:
    con = sqlite3.connect(f"file:{PROD_DB}?mode=ro", uri=True)
    row = con.execute("select result_data from reports where id=?",
                      (report_id,)).fetchone()
    con.close()
    if not row:
        raise SystemExit(f"报告 {report_id} 不存在")
    rd = json.loads(row[0])
    st = {"trade_date": rd.get("trade_date")}
    for name in REPORT_FIELDS:
        st[name] = rd.get(name)
    return st


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--impl", choices=["baseline", "candidate"], required=True)
    args = ap.parse_args()
    impl = load_impl(args.impl)
    os.makedirs(OUT, exist_ok=True)

    manifest = json.load(open(MANIFEST))
    snap_files = sorted(glob.glob(os.path.join(EXT, "snapshots", "snapshots", "*.pkl")))
    state_files = sorted(glob.glob(os.path.join(EXT, "states", "states", "*.pkl")))

    input_sha = {
        "states": {os.path.basename(p): sha256(p) for p in state_files},
        "snapshots": {os.path.basename(p): sha256(p) for p in snap_files},
        "manifest_sha256": sha256(MANIFEST),
        "prod_db": PROD_DB,
    }

    pools = {}
    for p in snap_files:
        sample = os.path.basename(p).split("_")[0]
        info = manifest["samples"][sample]
        pools[sample] = build_pool(sample, info["symbol"], info["trade_date"],
                                   pickle.load(open(p, "rb")))

    runs = {}
    for p in state_files:
        tag = os.path.basename(p)[:-4]
        sample = tag.split("__")[0]
        runs[tag] = run_one(impl, load_state(p), pools[sample])

    # 4390ddfd：有证据字段重建 pool；b188060f：pool=None
    st4390 = load_prod_report(REPORT_4390)
    runs["prod_4390ddfd"] = run_one(impl, st4390, build_pool_4390(impl))
    stb188 = load_prod_report(REPORT_B188)
    runs["prod_b188060f"] = run_one(impl, stb188, None)

    out = {"impl": args.impl, "input_sha256": input_sha, "runs": runs}
    path = os.path.join(OUT, f"{args.impl}.json")
    with open(path, "w") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    n_pass = sum(1 for r in runs.values() if r["status"] == "pass")
    print(f"{args.impl}: pass {n_pass}/{len(runs)} -> {path}")


if __name__ == "__main__":
    main()
