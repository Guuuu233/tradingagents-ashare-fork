#!/usr/bin/env python3
"""DAV-1264 复审返工补充——F1/F2/F3 分修复单独开启的 ABSTAIN→VALID 测算（零 LLM，只读）。

口径：对每条 ABSTAIN 记录重放一致性闸门 + 终态判定。
- 扫描面 = 落库 judge_decision（含系统尾缀）+ mv.reason + mv.investment_plan，
  即「对留存文本按其落库形态重判」；F1=剥离开启与否，F3=新旧 _PI_ANNOTATION。
- 非 E-04 的落库 failed_checks（extract/其他门类）不参与本修复，一律保留 →
  存在即仍 ABSTAIN。
- F2 = 新旧 decision_status.status_from_manager_verdict（旧版取自主干父提交
  9056a17，经 importlib 以独立模块加载）。
- 归因口径：「仅靠 F_i 救回」= 仅开 F_i 时 VALID 且全关（none）时非 VALID；
  修复前即算 VALID 的记录（版本漂移残留）不计入任何单修复归因。
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "dav1261-abstain-diag"))
import diag  # noqa: E402

from tradingagents.agents.managers import research_manager as rm  # noqa: E402
from tradingagents.agents.utils import decision_status as ds_new  # noqa: E402

OLD_PI_ANNOTATION = re.compile(
    r"已定价\s*(?:状态|栏位|标注|标记|评级|结论)?\s*(?:为|是|：|:|=|标为|标成|记为)\s*"
    r"(?:unknown|未知|不确定|待验证|待确认)",
    re.IGNORECASE,
)
NEW_PI_ANNOTATION = rm._PI_ANNOTATION


def _load_old_ds():
    """从主干父提交 9056a17 提取旧版 decision_status.py 并独立加载。"""
    import subprocess
    import tempfile
    repo = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
    src = subprocess.run(
        ["git", "-C", repo, "show",
         "9056a17:tradingagents/agents/utils/decision_status.py"],
        check=True, capture_output=True, text=True,
    ).stdout
    fd, path = tempfile.mkstemp(suffix="_ds_old.py", dir=os.path.dirname(__file__))
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(src)
    spec = importlib.util.spec_from_file_location("dav1264_ds_old", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod  # dataclass 处理需要模块已注册
    spec.loader.exec_module(mod)
    return mod


ds_old = _load_old_ds()


def run_scenario(rec: dict, *, f1: bool, f3: bool, f2: bool) -> dict:
    """返回该 scenario 下的重算状态 dict。"""
    mv = dict(rec["mv"])
    # 1) E-04 守卫重扫落库文本（含尾缀），按场景切换剥离与标注正则
    orig_strip = rm._strip_system_gate_text
    orig_pi = rm._PI_ANNOTATION
    try:
        rm._strip_system_gate_text = (
            orig_strip if f1 else (lambda t: t)
        )
        rm._PI_ANNOTATION = NEW_PI_ANNOTATION if f3 else OLD_PI_ANNOTATION
        ok, violations = rm.validate_manager_expectation_revision_consumption(
            manager_verdict=mv,
            raw_response=str(rec["judge_text"] or ""),
            expectation_revisions=mv.get("expectation_revision"),
            claims=rec["deb"].get("claims") or [],
            seven_reports=diag.seven_reports_of(rec["rd"]),
        )
    finally:
        rm._strip_system_gate_text = orig_strip
        rm._PI_ANNOTATION = orig_pi

    # 2) 非 E-04 的落库 failed_checks 保留（本修复不覆盖）
    non_e04_failed = [
        str(x) for x in (mv.get("failed_checks") or [])
        if x and not str(x).startswith("E-04 守卫拦截")
    ]
    mv2 = copy.deepcopy(mv)
    mv2.pop("decision_status", None)
    residual_failed = list(violations) + non_e04_failed
    mv2["consistency_check_passed"] = not residual_failed
    mv2["failed_checks"] = residual_failed

    ds_mod = ds_new if f2 else ds_old
    st = ds_mod.status_from_manager_verdict(
        mv2,
        investment_debate_state=rec["deb"],
        market_data_context=rec["rd"].get("market_data_context"),
    )
    return st.to_dict()


QUALIFIED = {"BUY", "SELL", "HOLD"}


def main() -> int:
    records = diag.load_db_records(
        "/Users/davidliu/Documents/TradingAgents-AShare/data/tradingagents.db",
        "429163f7-50b6-4982-8bdf-96ae99506843",
        "2026-08-26",
        "2026-09-25 01:05:19",
    )
    records += diag.load_state_records(
        "/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/"
        "dav-1249-c35f512940f2/workdir/dav1249/states"
    )
    abstains = [r for r in records if r["analysis_status"] == "ABSTAIN"]
    print(f"abstain records: {len(abstains)}", file=sys.stderr)

    scenarios = {
        "none": dict(f1=False, f2=False, f3=False),
        "f1_only": dict(f1=True, f2=False, f3=False),
        "f2_only": dict(f1=False, f2=True, f3=False),
        "f3_only": dict(f1=False, f2=False, f3=True),
        "all": dict(f1=True, f2=True, f3=True),
    }
    out = {}
    per_rec = {}
    for name, kw in scenarios.items():
        c = {"valid": 0, "qualified": 0, "wait": 0, "other_valid": 0,
             "abstain": 0, "other": 0, "err": 0}
        for rec in abstains:
            try:
                st = run_scenario(rec, **kw)
                per_rec.setdefault(rec["id"], {})[name] = (
                    st.get("analysis_status"), st.get("trade_action"))
                if st.get("analysis_status") == "VALID":
                    c["valid"] += 1
                    if st.get("trade_action") in QUALIFIED:
                        c["qualified"] += 1
                    elif st.get("trade_action") == "WAIT":
                        c["wait"] += 1
                    else:
                        c["other_valid"] += 1
                elif st.get("analysis_status") == "ABSTAIN":
                    c["abstain"] += 1
                else:
                    c["other"] += 1
            except Exception as e:  # noqa: BLE001
                c["err"] += 1
                per_rec.setdefault(rec["id"], {})[name] = (f"err:{type(e).__name__}", "")
        out[name] = c
        print(name, c, file=sys.stderr)

    # 归因：相对 none 基线
    attr = {}
    for name in ("f1_only", "f2_only", "f3_only", "all"):
        rescued = [
            rid for rid, st in per_rec.items()
            if st[name][0] == "VALID" and st["none"][0] != "VALID"
        ]
        attr[name] = {
            "rescued": len(rescued),
            "qualified": sum(1 for r in rescued if per_rec[r][name][1] in QUALIFIED),
            "wait": sum(1 for r in rescued if per_rec[r][name][1] == "WAIT"),
            "ids": rescued,
        }
    result = {"scenario_counts": out, "attribution_vs_none": attr,
              "per_record": per_rec}
    path = os.path.join(os.path.dirname(__file__), "perfix_summary.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)
    print(json.dumps(attr, ensure_ascii=False, indent=1))
    print("wrote", path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
