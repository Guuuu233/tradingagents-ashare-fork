"""DAV-1225 固化回归断言（RED 基线，对 trunk 现行代码逐字断言现状行为）。

每条断言直接调用 trunk ``audit_price_ref_registry`` / ``evaluate_price_basis_gate``，
锁定 DAV-1224 需要修复的现状缺陷。任一断言失败说明 trunk 行为已变化，
需重新校准本基线。

用法：env -u PYTHONPATH .venv310/bin/python work/dav1223-b0-audit/corrected/fixtures.py
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
if REPO not in sys.path:
    sys.path.insert(0, REPO)

from tradingagents.agents.utils.price_ref_registry import audit_price_ref_registry
from tradingagents.agents.utils.price_basis_gate import evaluate_price_basis_gate


def run(reports, td="2026-08-14"):
    st = {"trade_date": td}
    st.update(reports)
    audit_price_ref_registry(st)
    return st["price_refs"], evaluate_price_basis_gate(st)


def kinds(gate):
    return [v["kind"] for v in gate["violations"]]


def main() -> int:
    results = []

    def check(name, cond, detail=""):
        results.append((name, bool(cond), detail))
        print(f"[{'PASS' if cond else 'FAIL'}] {name} {detail}")

    # 1. 「建议入场区间：35.20 元」→ ref 注册 unspecified → wrong_basis
    refs, g = run({"investment_plan": "建议入场区间：35.20 元，止损 34.00 元。"})
    r352 = [r for r in refs if abs(r["value"] - 35.2) < 5e-3]
    check("F1 入场区间单值→unspecified→wrong_basis",
          r352 and r352[0]["basis"] == "unspecified"
          and "executable_level_wrong_basis" in kinds(g),
          f"refs={[(r['value'], r['basis']) for r in refs]}")

    # 2. 「35.20 - 35.50元」→ 首价未登记 → unbacked（shared parser 真实缺口）
    refs, g = run({"investment_plan": "建议入场区间：35.20 - 35.50元，止损 34.00 元。"})
    check("F2 区间首价 unbacked",
          not any(abs(r["value"] - 35.2) < 5e-3 for r in refs)
          and any(v["kind"] == "unbacked_executable_level" and "35.2" in v["detail"]
                  for v in g["violations"]),
          f"refs={[(r['value'], r['basis']) for r in refs]}")

    # 3. Markdown 列表序号被抽成可执行价位 2.0
    refs, g = run({"final_trade_decision": "触发后执行止损。\n2. **宏观**：加息预期升温。"})
    check("F3 列表序号 2.0 → unbacked",
          any(v["kind"] == "unbacked_executable_level" and "2.0" in v["detail"]
              for v in g["violations"]),
          f"violations={kinds(g)}")

    # 4. 「900亿元」被回溯截断成 90.0
    refs, g = run({"fundamentals_report": "按12倍PE测算，市值支撑位在900亿元。"})
    check("F4 900亿→90.0 伪价格",
          any(abs(r["value"] - 90.0) < 5e-3 for r in refs),
          f"refs={[(r['value'], r['basis']) for r in refs]}")

    # 5. 「10日 EMA（933 元）」→ 抽出 1.0；「收盘（2026-08-14）」→ 2026.0
    refs, g = run({"market_report": "现价持续承压于 10日 EMA（933 元）。"})
    check("F5a 10日EMA→1.0", any(abs(r["value"] - 1.0) < 5e-3 for r in refs),
          f"refs={[r['value'] for r in refs]}")
    refs, g = run({"market_report": "收盘（2026-08-14）46.30 元，支撑位 44.00 元。"})
    check("F5b 日期→2026.0", any(abs(r["value"] - 2026.0) < 5e-3 for r in refs),
          f"refs={[r['value'] for r in refs]}")

    # 6. typed disclosure 假命中：「突发行业」→ issuance；「大宗涨价」→ block_trade
    refs, g = run({"news_report": "突发行业利好发酵，现价 50.20 元。"})
    check("F6a 突发行业→issuance",
          any(r.get("disclosure_type") == "issuance" for r in refs),
          f"refs={[(r['value'], r.get('provenance')) for r in refs]}")
    refs, g = run({"news_report": "原油及大宗涨价推高成本，现价 50.20 元。"})
    check("F6b 大宗涨价→block_trade",
          any(r.get("disclosure_type") == "block_trade" for r in refs),
          f"refs={[(r['value'], r.get('provenance')) for r in refs]}")

    # 7. 「折合每股支撑区间」→ invalid_conversion（估值算术被当复权转换）
    refs, g = run({"investment_plan": "大宗交易 78.61 元，折合每股支撑区间约为18.15-19.36元。"})
    check("F7 折合每股→invalid_conversion",
          "invalid_conversion" in kinds(g), f"violations={kinds(g)}")

    n_fail = sum(1 for _n, ok, _d in results if not ok)
    print(f"\n{len(results) - n_fail}/{len(results)} 断言通过（锁定现状缺陷作为 RED 基线）")
    return 1 if n_fail else 0


if __name__ == "__main__":
    sys.exit(main())
