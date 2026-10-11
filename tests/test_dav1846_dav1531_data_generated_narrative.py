"""DAV-1846: report.md 的样本框/估计对象叙述必须由数据生成，不写死快照数字.

DAV-1531 候选（e52ead22）的条件是：`scripts/dav1505_six_stage_reject_stats.py`
生成的 report.md 里，凡叙述性数字（去重档级框总档数、各档位数、基准月覆盖、
缺档月份、"short/medium" 拆分、④ 估计对象的分子分母）此前都是按某一份快照
写死的字符串常量，换一份库（或同一份库重跑）就与同报告算出的表格自相矛盾。

本测试用**两份构造完全不同的库**跑同一脚本，逐项核对：

* 叙述里的每个数字都能由该库**独立重算**得出（不是常量、不是另一份库的值）；
* 两份库产出的叙述段落确实不同（数据驱动，而非模板）；
* 已确认矛盾的写死数字（1108 / 554 / 789 / 423 / 366 / 225 / −36.99pp /
  −45.82pp / "无 10 月基准日" / "4 月、10 月档为 0 份" / "4 月 3 份原始报告"）
  在两份产物里都不出现。

构造库只含 SQLite + JSON，不触网、不调模型、不读生产库。
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "dav1505_six_stage_reject_stats.py"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import dav1505_six_stage_reject_stats as m  # noqa: E402

ACCOUNT = m.FIXED_USER_ID
V1 = m.DECISION_MODEL_V1
HORIZONS = ("short_term", "medium_term")

# 快照期写死的字面量——DAV-1846 要根除的对象。
STALE_LITERALS = (
    "1108",
    "short 554",
    "medium 554",
    "789 份",
    "423/366",
    "225 份原始报告",
    "−36.99pp",
    "−45.82pp",
    "无 10 月基准日",
    "4 月、10 月档为 0 份",
    "4 月 3 份原始报告",
)


# ── 构造库 ────────────────────────────────────────────────────────────────── #

def _claim(cid: str, side: str) -> dict:
    return {
        "claim_id": cid, "speaker_key": side,
        "speaker": side.title() + " Analyst",
        "stance": "bullish" if side == "bull" else "bearish",
        "stage": "opening", "battlefield": "capital_flow", "claim": f"{side} {cid}",
    }


def _slot(prefix: str, *, direction: str, adopt_bull: bool, adopt_bear: bool) -> dict:
    """One horizon slot. adopted list decides the ④ bull_only/bear_only/both bucket."""
    bull = [_claim(f"{prefix}B{i}", "bull") for i in range(2)]
    bear = [_claim(f"{prefix}S{i}", "bear") for i in range(2)]
    claims = bull + bear
    ces = {
        c["claim_id"]: {
            "decision": "adopt", "semantic_decision": "adopt",
            "counts": {"verified": 2, "total": 2}, "coverage": 1.0,
            "is_observation_or_hypothesis": False, "reason": "核验通过",
            "claim": c["claim"], "speaker_key": c["speaker_key"],
            "stance": c["stance"], "speaker": c["speaker"],
        } for c in claims
    }
    adopted = ([c["claim_id"] for c in bull] if adopt_bull else []) + \
              ([c["claim_id"] for c in bear] if adopt_bear else [])
    return {
        "investment_debate_state": {
            "claims": claims,
            "claim_cluster_metrics": {"double_count_guard_audit": {"excluded_claim_ids": []}},
        },
        "manager_verdict": {
            "direction": direction, "consistency_check_passed": True,
            "adopted_claim_ids": adopted, "claim_evidence_summary": ces,
        },
        "decision_status": {"reason_codes": [], "failed_checks": []},
        "price_basis_gate": {"status": "pass", "violations": []},
    }


CATEGORY_TO_ADOPT = {
    "bull_only": (True, False),
    "bear_only": (False, True),
    "both": (True, True),
}


def _report_row(rid, symbol, baseline, created_at, *, category, direction) -> tuple:
    adopt_bull, adopt_bear = CATEGORY_TO_ADOPT[category]
    rd = {"decision_model_version": V1, "analysis_baseline_date": baseline}
    for hz in HORIZONS:
        rd[hz] = _slot(f"{hz[:2].upper()}{rid}", direction=direction,
                       adopt_bull=adopt_bull, adopt_bear=adopt_bear)
    return (rid, symbol, baseline, created_at, ACCOUNT,
            json.dumps(rd, ensure_ascii=False))


def _no_slot_row(rid, baseline, created_at) -> tuple:
    """A v1 report whose short_term/medium_term are both absent (top-level flat)."""
    rd = {"decision_model_version": V1, "analysis_baseline_date": baseline,
          "direction": "看多", "status": "completed"}
    return (rid, "NOSLOT", baseline, created_at, ACCOUNT,
            json.dumps(rd, ensure_ascii=False))


def _legacy_row(rid, baseline, created_at) -> tuple:
    rd = {"short_term": _slot(f"LEG{rid}", direction="看空",
                              adopt_bull=False, adopt_bear=True)}
    return (rid, "LEGACY", baseline, created_at, ACCOUNT,
            json.dumps(rd, ensure_ascii=False))


def _write_db(path: Path, rows) -> None:
    con = sqlite3.connect(path)
    con.execute(
        "CREATE TABLE reports (id TEXT PRIMARY KEY, symbol TEXT, trade_date TEXT, "
        "created_at TEXT, user_id TEXT, result_data TEXT)"
    )
    con.executemany("INSERT INTO reports VALUES (?,?,?,?,?,?)", rows)
    con.commit()
    con.close()


def _plan(baselines, directions):
    """(baseline, category, direction) triples, categories cycling."""
    cats = ("bull_only", "bear_only", "both")
    return [(bd, cats[i % len(cats)], directions[i % len(directions)])
            for i, bd in enumerate(baselines)]


DB_ALPHA = dict(
    run_month="2026-09",
    baselines=[f"2026-05-{d:02d}" for d in range(4, 14)],   # 10 clusters
    directions=("bear", "bull", "bull"),
    no_slot_months=("2026-04",),
    symbols=("000001",),
    legacy=0,
)

DB_BETA = dict(
    run_month="2026-11",
    baselines=[f"2026-07-{d:02d}" for d in range(6, 18)],   # 12 clusters
    directions=("bull", "bear", "bear"),
    no_slot_months=("2026-01", "2026-02", "2026-03"),
    symbols=("600000", "600001"),
    legacy=1,
)


def _build(path: Path, spec: dict) -> None:
    rows = []
    n = 0
    for sym in spec["symbols"]:
        for bd, cat, direction in _plan(spec["baselines"], spec["directions"]):
            n += 1
            rows.append(_report_row(f"r{n:04d}", sym, bd,
                                    f"{spec['run_month']}-05T10:00:00",
                                    category=cat, direction=direction))
    for i, bm in enumerate(spec["no_slot_months"]):
        rows.append(_no_slot_row(f"noslot{i}", f"{bm}-03", f"{bm}-03T09:00:00"))
    for i in range(spec["legacy"]):
        rows.append(_legacy_row(f"leg{i}", "2026-03-05", "2026-03-05T09:00:00"))
    _write_db(path, rows)


# ── 独立重算（不调用被测脚本的函数） ───────────────────────────────────────── #

def _indep_counts(db: Path) -> dict:
    """Recompute the frame numbers straight from the constructed DB."""
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    raw_by_run_month: dict[str, int] = {}
    no_slot_by_month: dict[str, int] = {}
    dedup: dict[tuple, tuple] = {}
    raw_total = 0
    for rid, sym, td, ca, rdj in con.execute(
        "SELECT id, symbol, trade_date, created_at, result_data FROM reports "
        "WHERE user_id=? AND result_data IS NOT NULL", (ACCOUNT,)
    ):
        rd = json.loads(rdj)
        if rd.get("decision_model_version") != V1:
            continue
        raw_total += 1
        run_month = (ca or "")[:7]
        raw_by_run_month[run_month] = raw_by_run_month.get(run_month, 0) + 1
        baseline = rd.get("analysis_baseline_date") or td
        base_month = str(baseline)[:7]
        found = False
        for hz in HORIZONS:
            sl = rd.get(hz)
            if not isinstance(sl, dict) or not sl:
                continue
            found = True
            key = (sym, baseline, hz)
            cand = (ca or "", rid)
            if key not in dedup or cand > dedup[key]:
                dedup[key] = cand
        if not found:
            no_slot_by_month[base_month] = no_slot_by_month.get(base_month, 0) + 1
    con.close()

    dedup_run_month: dict[str, int] = {}
    dedup_base_month: dict[str, int] = {}
    dedup_run_month_by_hz: dict[str, dict[str, int]] = {hz: {} for hz in HORIZONS}
    dedup_base_month_by_hz: dict[str, dict[str, int]] = {hz: {} for hz in HORIZONS}
    per_hz = {hz: 0 for hz in HORIZONS}
    for (sym, baseline, hz), (ca, _rid) in dedup.items():
        per_hz[hz] += 1
        rm = (ca or "")[:7]
        dedup_run_month[rm] = dedup_run_month.get(rm, 0) + 1
        dedup_run_month_by_hz[hz][rm] = dedup_run_month_by_hz[hz].get(rm, 0) + 1
        bm = str(baseline)[:7]
        dedup_base_month[bm] = dedup_base_month.get(bm, 0) + 1
        dedup_base_month_by_hz[hz][bm] = dedup_base_month_by_hz[hz].get(bm, 0) + 1
    return {
        "raw_total": raw_total,
        "raw_by_run_month": raw_by_run_month,
        "no_slot_by_month": no_slot_by_month,
        "no_slot_total": sum(no_slot_by_month.values()),
        "dedup_total": sum(per_hz.values()),
        "dedup_per_hz": per_hz,
        "dedup_run_month": dedup_run_month,
        "dedup_base_month": dedup_base_month,
        "dedup_run_month_by_hz": dedup_run_month_by_hz,
        "dedup_base_month_by_hz": dedup_base_month_by_hz,
        "zero_baseline_months": sorted(
            (set(dedup_run_month) | set(raw_by_run_month)) - set(dedup_base_month)
        ),
    }


# ── 运行 ──────────────────────────────────────────────────────────────────── #

def _run(db: Path, tmp_path: Path, tag: str):
    out_json = tmp_path / f"{tag}.json"
    out_md = tmp_path / f"{tag}.md"
    proc = subprocess.run(
        [sys.executable, str(SCRIPT), "--db", str(db),
         "--output-json", str(out_json), "--output-md", str(out_md)],
        capture_output=True, text=True, cwd=str(ROOT),
    )
    assert proc.returncode == 0, f"{tag} failed:\n{proc.stdout}\n{proc.stderr}"
    stats = json.loads(out_json.read_text(encoding="utf-8"))
    return out_md.read_text(encoding="utf-8"), stats


@pytest.fixture(scope="module")
def two_runs(tmp_path_factory):
    tmp_path = tmp_path_factory.mktemp("dav1846")
    out = {}
    for tag, spec in (("alpha", DB_ALPHA), ("beta", DB_BETA)):
        db = tmp_path / f"{tag}.db"
        _build(db, spec)
        md, stats = _run(db, tmp_path, tag)
        out[tag] = {"md": md, "stats": stats, "expected": _indep_counts(db)}
    return out


def _line(md: str, needle: str) -> str:
    hits = [ln for ln in md.splitlines() if needle in ln]
    assert hits, f"line containing {needle!r} not found in report.md"
    return hits[0]


# ── 断言 ──────────────────────────────────────────────────────────────────── #

@pytest.mark.parametrize("tag", ["alpha", "beta"])
def test_frame_numbers_match_independent_recount(two_runs, tag):
    """样本框叙述里的每个数字都能由该库独立重算得出。"""
    run = two_runs[tag]
    md, exp = run["md"], run["expected"]

    # 去重档级框：总档数 + 各档位数
    dedup_line = _line(md, "去重档级框：")
    assert f"去重档级框：{exp['dedup_total']} 档" in dedup_line
    for hz in HORIZONS:
        assert f"{hz} {exp['dedup_per_hz'][hz]}" in dedup_line

    # 去重档级框：运行月 / 基准月（逐档位，与 report.md 的行粒度一致）
    for hz in HORIZONS:
        hz_line = _line(md, f"- {'短线' if hz == 'short_term' else '中线'}：")
        assert f"{exp['dedup_per_hz'][hz]} 档" in hz_line
        for month, n in sorted(exp["dedup_run_month_by_hz"][hz].items()):
            assert f"{month}={n} 档" in hz_line
        for month, n in sorted(exp["dedup_base_month_by_hz"][hz].items()):
            assert f"{month}={n} 档" in hz_line

    # 原始报告框：总数 + 分月
    assert f"- v1 固定账户原始报告：{exp['raw_total']} 份" in md
    for month, n in sorted(exp["raw_by_run_month"].items()):
        assert f"运行生成月 {month}：{n} 份" in md

    # 缺档月集合（此前写死为「4 月、10 月」）
    zero_txt = "、".join(exp["zero_baseline_months"])
    assert f"本副本基准月计数为 0 的月份：{zero_txt}" in dedup_line

    # 无档报告（此前写死为「4 月 3 份原始报告」）
    if exp["no_slot_total"]:
        noslot_line = _line(md, "均为 NULL（顶层平铺结构）")
        assert f"{exp['no_slot_total']} 份原始报告" in noslot_line
        for month, n in sorted(exp["no_slot_by_month"].items()):
            assert f"{month} {n} 份" in noslot_line
    else:
        assert "0 份原始报告" in _line(md, "均为 NULL（顶层平铺结构）")


def test_narrative_actually_changes_with_the_data(two_runs):
    """两份构造库产出的叙述段落必须不同——证明是数据驱动而非模板。"""
    a, b = two_runs["alpha"], two_runs["beta"]
    assert a["expected"]["dedup_total"] != b["expected"]["dedup_total"]
    for needle in ("去重档级框：", "- v1 固定账户原始报告：",
                   "均为 NULL（顶层平铺结构）"):
        assert _line(a["md"], needle) != _line(b["md"], needle), needle
    # ④ 估计对象的分侧分子分母随数据变化
    for hz in HORIZONS:
        fa = a["stats"]["asymmetry_estimand_fractions"][hz]
        fb = b["stats"]["asymmetry_estimand_fractions"][hz]
        assert fa["incl_both"] != fb["incl_both"], hz
        assert fa["side_only"] != fb["side_only"], hz


def test_estimand_fractions_in_report_match_stats_json(two_runs):
    """report.md 的 ④ 估计对象段落直接回读 stats.json，不是写死数字。"""
    for tag in ("alpha", "beta"):
        md, stats = two_runs[tag]["md"], two_runs[tag]["stats"]
        for hz in HORIZONS:
            label = m.HORIZON_LABEL[hz]
            frac = stats["asymmetry_estimand_fractions"][hz]
            for key, head in (("incl_both", f"- {label} incl_both（含 both 重叠档）："),
                              ("side_only", f"- {label} side_only（仅侧桶）：")):
                line = _line(md, head)
                f = frac[key]
                assert (f"bull {f['bull_numer']}/{f['bull_denom']} "
                        f"− bear {f['bear_numer']}/{f['bear_denom']} "
                        f"= {f['diff_pp']} pp") in line
                # 与同一产物里的不对称点表一致
                asym_key = ("4_same_side_formation_incl_both|" if key == "incl_both"
                            else "4_same_side_formation_side_only|") + hz
                assert stats["asymmetry"][asym_key]["diff_pp_bull_minus_bear"] == f["diff_pp"]


def test_bootstrap_header_reads_module_constants(two_runs):
    """自助法表头的 B / seed 取自模块常量，不是行内字面量。"""
    header = _line(two_runs["alpha"]["md"], "## 不对称点")
    assert f"B={m.BOOTSTRAP_B}" in header
    assert f"seed={m.BOOTSTRAP_SEED}" in header


def test_no_stale_snapshot_literals_survive(two_runs):
    """DAV-1846 要根除的写死数字在两份产物（含 stats.json）里都不出现。"""
    for tag in ("alpha", "beta"):
        run = two_runs[tag]
        haystacks = {
            "report.md": run["md"],
            "stats.json": json.dumps(run["stats"], ensure_ascii=False),
        }
        for where, text in haystacks.items():
            for lit in STALE_LITERALS:
                assert lit not in text, f"{tag}/{where} still contains stale literal {lit!r}"


def test_report_is_self_consistent_on_the_constructed_db(two_runs):
    """叙述里的档数必须与同报告统计段（stages ①）的档数一致——DAV-1846 的矛盾点。"""
    for tag in ("alpha", "beta"):
        md, stats = two_runs[tag]["md"], two_runs[tag]["stats"]
        for hz in HORIZONS:
            units = stats["stages"]["1_proposal"][hz]["units"]
            label = m.HORIZON_LABEL[hz]
            assert f"{label}：{units} 档；" in md
