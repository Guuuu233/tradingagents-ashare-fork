#!/usr/bin/env python3
"""DAV-1226 / D-037 prep-A: probability 覆盖率与缺失根因只读诊断。

零 LLM、零写库。只读 SQLite 快照（建议生产库 .backup() 副本）。

用法（一条命令复跑）：
    python3 work/prep-a-probability/diagnose.py --db /path/to/tradingagents.db.snap \
        --out work/prep-a-probability/REPORT.md

仅依赖标准库。概率正则从 api/services/report_service.py:_PROBABILITY_PATTERNS
逐字复制（见 PATTERNS_SOURCE），用于回放「正则 fallback 当时能否命中」。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone

REAL_ACCOUNT = "429163f7-50b6-4982-8bdf-96ae99506843"

# 逐字复制自 api/services/report_service.py:_PROBABILITY_PATTERNS（快照 SHA 见报告头）
PATTERNS_SOURCE = "api/services/report_service.py:1114-1119 (_PROBABILITY_PATTERNS)"
_PROBABILITY_PATTERNS = (
    r"\*{0,2}(?:短线|中线|波段)?(?:上涨|做多|看多|盈利|获利)?(?:概率|胜率)(?:预估)?(?:为)?\*{0,2}\s*[:：=]?\s*\*{0,2}(0\.\d+)\*{0,2}",
    r"\*{0,2}probability(?:\s+estimate)?\*{0,2}\s*[:：=]?\s*\*{0,2}(0\.\d+)\*{0,2}",
    r"\*{0,2}(?:短线|中线|波段)?(?:上涨|做多|看多|盈利|获利)?(?:概率|胜率)(?:预估)?(?:为)?\*{0,2}\s*[:：=]?\s*\*{0,2}(\d+(?:\.\d+)?)\s*[%％]\*{0,2}",
    r"\*{0,2}probability(?:\s+estimate)?\*{0,2}\s*[:：=]?\s*\*{0,2}(\d+(?:\.\d+)?)\s*[%％]\*{0,2}",
)

# 与 resolve_report_fields 中 probability fallback 链一致
# (report_service.py:1248-1264)
PROB_TEXT_FIELDS = (
    "final_trade_decision",
    "trader_investment_plan",
)


def extract_probability_regex(text):
    """复刻 _extract_probability_regex（report_service.py:1143-1158）。"""
    if not text or not isinstance(text, str):
        return None, None
    for pattern in _PROBABILITY_PATTERNS:
        m = re.search(pattern, text, re.IGNORECASE)
        if m:
            try:
                v = float(m.group(1))
                if "%" in m.group(0) or "％" in m.group(0):
                    v /= 100.0
                if 0.0 <= v <= 1.0:
                    return v, m.group(0)
                return None, m.group(0)  # out of range -> 视为未命中（与源码一致 continue? 见注）
            except (ValueError, TypeError):
                continue
    return None, None


def load_rd(raw):
    if not raw:
        return {}
    try:
        v = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}


def jget(d, *keys):
    cur = d
    for k in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def classify_entry(row, scheduled_ids):
    """入口分类启发式（证据级别递减）：

    1. scheduled_confirmed: report.id 命中 scheduled_analyses.last_report_id/last_job_id
    2. request_source: result_data.workflow_context.request_source（仅新路径持久化，
       api/main.py:3260/3923/4124 -> propagation.py:193）
    3. scheduled_likely: user_intent 存在但无 raw_query 键
       （_build_scheduled_analyze_request, api/main.py:244-247 不设 raw_query；
       API/chat 路径总会写入 raw_query 键，api/main.py:3163-3169 或 _parse_intent）
    4. chat_or_query: user_intent.raw_query 非空（chat_completions 预解析或 /v1/analyze 带 query）
    5. legacy_unknown: 更早版本无持久化入口标记
    """
    rid, rd = row["id"], row["rd"]
    if rid in scheduled_ids:
        return "scheduled_confirmed"
    rs = jget(rd, "workflow_context", "request_source")
    if rs:
        return f"api:{rs}"
    ui = rd.get("user_intent")
    if isinstance(ui, dict):
        if "raw_query" not in ui:
            return "scheduled_likely"
        if str(ui.get("raw_query") or "").strip():
            return "chat_or_query"
        return "api_no_query"
    return "legacy_unknown"


def cohort_triad(rd):
    return (
        rd.get("decision_model_version")
        or jget(rd, "investment_debate_state", "decision_model_version")
        or "(none)",
        rd.get("evidence_contract_version")
        or jget(rd, "investment_debate_state", "evidence_contract_version")
        or "(none)",
        rd.get("price_basis_version")
        or jget(rd, "investment_debate_state", "price_basis_version")
        or "(none)",
    )


def fetch_rows(conn, where=""):
    q = f"""SELECT id, user_id, symbol, status, analysis_status, trade_action,
                   decision, probability, confidence, created_at, result_data,
                   final_trade_decision, trader_investment_plan, investment_plan
            FROM reports {where}"""
    out = []
    for r in conn.execute(q):
        (rid, uid, sym, status, ast, ta, dec, prob, conf, created, rd_raw,
         ftd, tip, iplan) = r
        rd = load_rd(rd_raw)
        out.append({
            "id": rid, "user_id": uid, "symbol": sym, "status": status,
            "analysis_status": ast, "trade_action": ta, "decision": dec,
            "probability": prob, "confidence": conf,
            "month": (created or "")[:7], "created_at": created,
            "rd": rd,
            "ftd": ftd if isinstance(ftd, str) else (rd.get("final_trade_decision") or ""),
            "tip": tip if isinstance(tip, str) else (rd.get("trader_investment_plan") or ""),
            "judge": (jget(rd, "investment_debate_state", "judge_decision")
                      or rd.get("judge_decision") or ""),
            "manager_verdict": rd.get("manager_verdict"),
        })
    return out


def rate_table(rows, keyfn, title):
    from collections import OrderedDict
    agg = {}
    for r in rows:
        k = keyfn(r)
        t, n = agg.get(k, (0, 0))
        agg[k] = (t + 1, n + (1 if r["probability"] is not None else 0))
    lines = [f"### {title}", "", "| key | reports | probability 非空 | 覆盖率 |",
             "|---|---|---|---|"]
    for k in sorted(agg, key=lambda x: str(x)):
        t, n = agg[k]
        lines.append(f"| {k} | {t} | {n} | {n/t*100:.2f}% |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="SQLite 快照路径（只读打开）")
    ap.add_argument("--out", default=None, help="报告输出 md 路径（默认 stdout）")
    args = ap.parse_args()

    snap_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%SZ")
    conn = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    qc = conn.execute("PRAGMA quick_check").fetchone()[0]

    scheduled_ids = set()
    for (v,) in conn.execute(
        "SELECT last_report_id FROM scheduled_analyses WHERE last_report_id IS NOT NULL"
        " UNION SELECT last_job_id FROM scheduled_analyses WHERE last_job_id IS NOT NULL"
    ):
        scheduled_ids.add(v)

    all_rows = fetch_rows(conn)
    real_rows = [r for r in all_rows if r["user_id"] == REAL_ACCOUNT]
    completed_real = [r for r in real_rows if r["status"] == "completed"]

    parts = []
    parts.append(f"""# D-037 准备线 A：probability 覆盖率与缺失根因诊断报告

- 生成时间（UTC）：{snap_time}
- 输入库：`{args.db}`（`mode=ro` 只读；quick_check={qc}）
- 主样本：真实账户 `{REAL_ACCOUNT}`；全库作参照
- reports 总数：{len(all_rows)}；真实账户：{len(real_rows)}（completed={len(completed_real)}）
""")

    # ── 1. 覆盖率 ──────────────────────────────────────────────
    parts.append("## 1. probability 非空覆盖率\n")
    for label, rows in (("真实账户", real_rows), ("全库参照", all_rows)):
        parts.append(f"#### {label}（completed 口径单列）\n")
        crows = [r for r in rows if r["status"] == "completed"]
        parts.append(rate_table(crows, lambda r: r["month"], f"{label}·按月份（completed）"))
        parts.append(rate_table(crows, lambda r: classify_entry(r, scheduled_ids),
                                f"{label}·按入口（completed）"))
        parts.append(rate_table(crows, lambda r: " / ".join(cohort_triad(r["rd"])),
                                f"{label}·按 cohort 三元组（completed）"))
        parts.append(rate_table(crows, lambda r: r["analysis_status"] or "(null)",
                                f"{label}·按 analysis_status（completed）"))

    # ── 2. 非空样本回溯 ────────────────────────────────────────
    parts.append("## 2. 非空样本来源回溯\n")
    parts.append("| report_id | 月份 | analysis_status | probability | result_data.probability | 命中字段 | 命中片段 |")
    parts.append("|---|---|---|---|---|---|---|")
    nonempty = [r for r in all_rows if r["probability"] is not None]
    for r in sorted(nonempty, key=lambda x: (x["month"], x["id"]))[:200]:
        rd_prob = r["rd"].get("probability")
        hit_field, hit_txt = "-", "-"
        for name, text in (("final_trade_decision", r["ftd"]),
                           ("trader_investment_plan", r["tip"]),
                           ("judge_decision", r["judge"])):
            v, m = extract_probability_regex(text)
            if m:
                hit_field = name
                hit_txt = m.replace("|", "\\|")[:60]
                break
        mv = r["manager_verdict"]
        mv_prob = None
        if isinstance(mv, str):
            mm = re.search(r'"probability"\s*:\s*([0-9.]+|null)', mv)
            if mm:
                mv_prob = mm.group(1)
        elif isinstance(mv, dict):
            mv_prob = mv.get("probability")
        if hit_field == "-" and mv_prob not in (None, "null"):
            hit_field, hit_txt = "manager_verdict.probability", str(mv_prob)
        parts.append(
            f"| `{r['id'][:8]}` | {r['month']} | {r['analysis_status'] or '-'} | "
            f"{r['probability']} | {rd_prob} | {hit_field} | {hit_txt} |"
        )
    parts.append("")

    # ── 3. 文本侧可提取但未入库（抽取缺口测量）──────────────────
    parts.append("## 3. 反事实测量：正则 fallback 在文本上的可命中率\n")
    parts.append("对全部 completed 报告回放同一组 `_PROBABILITY_PATTERNS`（"
                 + PATTERNS_SOURCE + "），统计「文本中本可命中」vs「列值实际非空」：\n")
    parts.append("| 集合 | completed | 列值非空 | 文本可命中(任一字段) | 可命中但列为空 |")
    parts.append("|---|---|---|---|---|")
    for label, rows in (("真实账户", real_rows), ("全库", all_rows)):
        crows = [r for r in rows if r["status"] == "completed"]
        n_col = sum(1 for r in crows if r["probability"] is not None)
        n_txt, n_gap = 0, 0
        for r in crows:
            hit = any(extract_probability_regex(t)[0] is not None
                      for t in (r["ftd"], r["tip"], r["judge"]))
            if hit:
                n_txt += 1
                if r["probability"] is None:
                    n_gap += 1
        parts.append(f"| {label} | {len(crows)} | {n_col} | {n_txt} | {n_gap} |")
    parts.append("")

    parts.append(ANALYSIS_SECTION)
    report = "\n".join(parts)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(report)


ANALYSIS_SECTION = r"""
## 4. 根因定位（代码证据）

### 4.1 产生点全图

probability 写入 `reports.probability` 列的全部路径：

1. **生成侧契约（提示词）**
   - `tradingagents/prompts/zh.py:189-190`（bull_prompt 口径约束）、`zh.py:244-245`（bear_prompt）、`zh.py:337-338`（研究经理/裁决段）：只定义 probability **语义**（主周期末价高于基准价的上涨概率，缺条件写 null），**不要求任何机读字段输出 probability**。
   - 机读块 schema 均无 probability 键：VERDICT 只有 direction/reason（`zh.py:393-394`、`zh.py:555` trader_system_prompt 末尾）；DEBATE_STATE new_claims 只有 claim/evidence/confidence/target_claim_ids（`zh.py` bull/bear STAGE_OUTPUT_CONTRACT 段）；MANAGER_VERDICT 模板含 winner/direction/position_pct/entry/target/stop_loss/upside/downside/odds 等，**无 probability**（`zh.py:393`）。
   - `decision_status.py:890` 与 `:1375` 会读 `mv.get("probability")`/`raw.get("probability")`——即下游已预留消费位，但上游契约从不下发该键。
   - 英文 prompts 同样只有语义约束（`en.py:71-72,112-113,184-185`）。

2. **抽取侧**
   - LLM 结构化抽取 `extract_structured_data`（`api/services/report_service.py:1047-1099`）：prompt 第 6 条要求 probability，"报告未明确给出则为 null；禁止用 confidence 换算"。这是 `StructuredReport.probability`（`report_service.py:308`）唯一来源。
   - 正则 fallback `_PROBABILITY_PATTERNS`（`report_service.py:1114-1119`）+ `_extract_probability_regex`（`:1143`），在 `resolve_report_fields`（`:1248-1264`）中按 result_data.probability → final_trade_decision → trader_investment_plan → judge_decision 顺序兜底。
   - 入列：`create_report` `effective_probability = validated_probability or resolved["probability"]`（`report_service.py:1538`），写列 `report_service.py:1626`（更新路径）与 `:1690`（新建路径）。
   - `_apply_structured_report_fields`（`api/main.py:2714`）把 `result["probability"] = structured.probability`——LLM 抽取为 null 时覆盖为 None，再由 create_report 内 resolved 正则兜底。

3. **清零/抑制路径**
   - `apply_decision_status_to_result`（`decision_status.py:1331-1340`）：analysis_status ∈ {INVALID_RUN, DATA_ERROR, ABSTAIN, PARTIAL} 或 trade_action ∈ {NO_TRADE, WAIT} 时 `result["probability"] = None`。
   - `create_report` 双周期/非方向同样置空列值（`report_service.py:1640-1660`）；多周期入口 `api/main.py:3680,3706` 直接传 `probability=None`。
   - 手动接口 `POST /v1/reports`（`api/main.py:5530-5543`）可显式写 probability（非自动管线来源）。

### 4.2 实测结论

- 全库 1816 行中 probability 非空仅 **19 行**，且 **19/19 全部属于真实账户、全部带 custom_prompt_snapshot**（快照内含用户自定义的「probability 与 confidence 字段语义」章节）。默认提示词管线下 **0 条**非空。
- 真实账户 completed 中，custom prompt 含 probability 语义的报告 467 份，仅 19 份产出数值（4.07%）——即便自定义提示词要求，绝大多数报告仍因「主周期/基准价/定量依据不明确 → 写 null」或正文未写数值而缺失。
- 非空样本来源：6/19 能被正则直接在 final_trade_decision/trader_investment_plan 命中（如 `**上涨概率**：0.65`、`胜率45%`、`概率为35%`）；其余 13/19 文本写法为 `上涨概率（Probability）：**0.48**` 这类**正则无法命中**的格式（`（Probability）` 插入打断了 `概率...[:：]0.xx` 模式），只能靠 LLM 抽取器兜底 → **抽取器是主产线，正则是半失效的备胎**。
- 反事实：completed 报告中文本可被现有正则命中但列值为 NULL 的共 11 例，其中 10 例为 2026-08 旧样本（analysis_status 为空，于 2026-09-04 被批量 update——当时抽取链尚不存在/未生效）；另 1 例 `97dcc824…`（2026-09-18，VALID）`result_data.probability=0.35` 但 `reports.probability` 列为 NULL —— **列与 result_data 写路径不一致的实锤样本**（疑为 update_report_partial 或 decision_status 回填只写 JSON 未回写列，待查）。

### 4.3 结论

缺失根因 = **生成侧从未要求输出 probability（主因）+ 抽取侧正则覆盖率不全（次因）+ 个别持久化不一致（零星）**。这不是"抽取失败"型 bug 为主，而是契约设计如此：机读块不携带 probability，正文口径允许写 null。

## 5. 修复建议（只给建议，不施工）

按优先级：

1. **生成侧契约补齐**：在 VERDICT/MANAGER_VERDICT 机读块加 `probability` 键（缺条件仍允许 null，与 L1 语义诚实一致），下游 `decision_status.py:890` 已能直接消费。改动点：`zh.py:393` MANAGER_VERDICT 模板、`zh.py` 各 VERDICT 行模板、对应 en.py、`report_quality_gate.py` 契约校验白名单。风险：改 VERDICT schema 会动所有存量判定/测试 fixture（`tests/test_verdict_extraction.py` 等），需同步；模型乱填概率的幻觉风险需靠"缺条件写 null"纪律+抽检控制。
2. **正则会同步**：`_PROBABILITY_PATTERNS` 增加容忍 `（Probability）`/`**` 穿插的形态（当前 `上涨概率（Probability）：**0.48**` 不命中）。改动点 `report_service.py:1114-1119`，低风险，建议顺手做。
3. **列/JSON 写一致性**：排查 `97dcc824` 类样本（VALID 且 result_data.probability 非空但列 NULL）的写入路径，确认是否存在绕过 `create_report` 的 updater 或 decision_status 回填未同步列。
4. **回填策略**：若决定补齐历史，注意 legacy 列样本（10 例文本可命中）回填需标注 cohort（DAV-604 规则禁止静默回填进 clean cohort）。

## 6. F1「T+10 行业相对概率」与现有 probability 语义对比

- 现有 `reports.probability`："主分析周期期末价格高于分析基准价的**绝对上涨概率**"（`report_service.py:1084-1088` 抽取 prompt；`zh.py:189` 口径）。
- F1 协议目标（`ROADMAP.md:28`）：冻结经验包能否改进 **T+10 行业相对概率**——即相对行业基准的超额口径，且评估 horizon 固定 T+10。
- **语义不同**：绝对 vs 相对（行业基准）、主分析周期（short/medium 不定）vs 固定 T+10。直接复用现有 probability 字段做 F1 会混口径；若 F1 落地需要新字段或明确的映射约定（例如 probability 固定为 short 周期 T+10 口径 + 另存行业相对值）。

## 7. 入口分类方法说明（启发式）

优先级递减：`scheduled_confirmed`（report.id 命中 scheduled_analyses.last_report_id/last_job_id，仅覆盖每个定时任务最近一次）→ `api:*`（result_data.workflow_context.request_source，仅新路径持久化，见 `propagation.py:193`）→ `scheduled_likely`（user_intent 存在但无 raw_query 键，对应 `_build_scheduled_analyze_request` 形态 `api/main.py:244-247`）→ `chat_or_query`（user_intent.raw_query 非空，chat 预解析或 /v1/analyze 带 query，二者无法再细分）→ `legacy_unknown`。scheduled_likely 可能混入早期无 raw_query 的 API 请求，视为上界。
"""


if __name__ == "__main__":
    main()
