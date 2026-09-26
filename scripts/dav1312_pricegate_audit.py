"""DAV-1312 — 价格门拦截精确率审计（只读，可复跑）。

对 work/hist-bench-20260926/audit-corpus-pricegate.txt 列出的 46 个被拦档位
（日常 daily 6 + 历史回放 batch 40），逐条归类 price_basis_gate 的 violation：

  (a) 模型自拟的价位，无口径或日期可证 —— 正确拦截；
  (b) 抽取误报 —— 差额、非本股价格、窗口日期（DAV-1308 类）、指标/数字误读、
      provenance 误标、文档已声明口径但未传递、估值推演值未归 derived_estimate、
      真实价位漏登记；
  (c) 历史回放特有的前复权偏移（DAV-1309 类）—— 被标 raw/pit_raw 的数值确实是
      当时的真实价格，违规只因坐标系是事后前复权；
  (d) 其他 —— 需在注释中写明。

输出：
  * stdout：汇总（按 daily/batch 分组的误拦档位与逐类计数）；
  * --table <path>：逐条 violation 归类表（Markdown，不含供应商原始数据，D-040）。

数据：仅读 reports.result_data（price_basis_gate / price_refs），不写库、
不改代码、不跑分析。分类规则分两层：
  1. 自动规则——非价数字（百分数截断、倍数、日期/编号、仓位）、非本股价、
     provenance 误标、文档级前复权声明未传递、derived_estimate 漏标；
  2. REF_OVERRIDE / LEVEL_OVERRIDE —— 需人工语义判定的少量条目，
     逐条写明理由（DAV-1309 对照价由 --price-cmp 提供，见 price_cmp 文件；
     其获取脚本见 work/dav1312/fetch_prices.py，仅用于离线判定、不入库原始序列）。

用法：
    python scripts/dav1312_pricegate_audit.py \
        --db /path/to/tradingagents.db \
        --corpus work/hist-bench-20260926/audit-corpus-pricegate.txt \
        --table work/hist-bench-20260926/pricegate-audit-dav1312.md
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------------------
# 人工判定表：ref_id -> (cls, subtype, note)
# cls: a=正确拦截, b=抽取误报, c=前复权偏移(1309), d=其他
# 未列出的 ref 走自动规则。判定依据全部来自语句文本与 provenance，不含外部原始数据。
# ---------------------------------------------------------------------------

REF_OVERRIDE: Dict[str, tuple] = {
    # --- (a) 模型自拟/无口径可证：真实股价但没有任何可审计 basis ---
    "603156.SH@pr-051": ("a", "model_price_no_basis",
        "8月24日套牢区 45.50-47.50 元为模型自述的历史价位断言，即便补上日期仍无 "
        "basis 可归（归为 raw 又会落入坐标混用），门按契约拦下正确"),
    "600519.SH@pr-136": ("a", "model_price_no_basis",
        "『向上修复至1616元附近』系转述投行目标价，无登记口径/日期可证，正确拦截"),
    "000333.SZ@pr-209": ("a", "conflicting_unverifiable",
        "模型自述材料中 77.64 与 81.50 元冲突且复权规则缺失，价位不可核验，正确拦截"),
    "000333.SZ@pr-215": ("a", "conflicting_unverifiable",
        "同 pr-209，同一冲突价位的第二次登记，正确拦截"),
    # --- (c) DAV-1309 前复权偏移：raw 标注正确的当时真实价 ---
    "600519.SH@pr-113": ("c", "genuine_pit_raw_price",
        "1218.61 元为真实大宗折价成交价（=当时不复权收盘 1315×(1-7.33%)），"
        "raw 标注正确；违规只因报告坐标是事后前复权(qfq 1284.6)，1309 修复后消失"),
    # --- (b) 抽取误报：非股价数字被登记为 price_ref ---
    "000657.SZ@pr-131": ("b", "derived_estimate_miss",
        "50.0 来自 MANAGER_VERDICT excluded_evidence『压力测试测算…底部估值42-50元』，"
        "是带测算口径的估值推演值，应归 derived_estimate；且被拒证据本不该算决策驱动"),
    "601398.SH@pr-113": ("b", "derived_estimate_miss",
        "7.23 为 0.65x PB 估值推演值，应归 derived_estimate 而非决策驱动价格"),
    "002027.SZ@pr-112": ("b", "derived_estimate_miss",
        "5.60-6.20 元为 18-20 倍 PE 折合估值推演，应归 derived_estimate"),
    "603156.SH@pr-065": ("b", "non_price_dividend_per_share",
        "『每10股派5元』每股股利被当成股价登记"),
    "600276.SH@pr-145": ("b", "pct_trunc", "-6.5% 跌幅百分数被截成价格 6.0"),
    "688981.SH@pr-116": ("b", "non_price_year", "2026Q1 年份被当成价格 2026.0"),
    "688981.SH@pr-188": ("b", "non_price_position_pct", "仓位 12.0% 被当成价格"),
    "688981.SH@pr-190": ("b", "non_price_position_pct", "仓位 4.0% 被当成价格"),
    "600030.SH@pr-123": ("b", "non_price_pb", "1.05-1.15 倍 PB 被当成价格"),
    "002594.SZ@pr-177": ("b", "pct_trunc", "+5.7% 涨幅被截成价格 5.0"),
    "002594.SZ@pr-179": ("b", "pct_trunc", "-4.3% 跌幅被截成价格 4.0"),
    "002594.SZ@pr-181": ("b", "pct_trunc", "-4.3% 跌幅被截成价格 4.0"),
    "002594.SZ@pr-183": ("b", "pct_trunc", "-4.3% 跌幅被截成价格 4.0"),
    "002594.SZ@pr-196": ("b", "pct_trunc", "-4.3% 跌幅被截成价格 4.0"),
    "600900.SH@pr-152": ("b", "non_price_fraction", "减仓 1/3 的分数被当成价格 1.0"),
    "600900.SH@pr-163": ("b", "non_price_fraction", "减仓 1/3 的分数被当成价格 1.0"),
    "001979.SZ@pr-157": ("b", "non_price_claim_id", "论据编号 INV-4 被当成价格 4.0"),
    "600036.SH@pr-107": ("b", "pct_trunc", "股息率 4.5% 被截成价格 4.0"),
    "600036.SH@pr-102": ("b", "non_stock_price",
        "『最高享5400元/年节税优惠』节税额度被登记为 pit_raw 价格，provenance 误标"),
    "300750.SZ@pr-173": ("b", "pct_trunc", "+18.8% 涨幅被截成价格 18.0"),
    "300750.SZ@pr-177": ("b", "pct_trunc", "+16.2% 涨幅被截成价格 16.0"),
    "300750.SZ@pr-178": ("b", "pct_trunc", "-5.7% 跌幅被截成价格 5.0"),
    "300750.SZ@pr-185": ("b", "pct_trunc", "+16.2% 涨幅被截成价格 16.0"),
    "300750.SZ@pr-186": ("b", "pct_trunc", "-5.7% 跌幅被截成价格 5.0"),
    "300750.SZ@pr-195": ("b", "pct_trunc", "-5.7% 跌幅被截成价格 5.0"),
    "300750.SZ@pr-212": ("b", "pct_trunc", "-5.7% 跌幅被截成价格 5.0"),
    "300750.SZ@pr-214": ("b", "pct_trunc", "-5.7% 跌幅被截成价格 5.0"),
    "300750.SZ@pr-219": ("b", "pct_trunc", "-5.7% 跌幅被截成价格 5.0"),
    "600276.SH@pr-139": ("b", "pct_trunc", "+3.2% 涨幅被截成价格 3.0"),
    "600276.SH@pr-144": ("b", "pct_trunc", "+3.2% 涨幅被截成价格 3.0"),
    "600276.SH@pr-174": ("b", "non_price_position_pct", "委托量 10% 被当成价格"),
    "600519.SH@pr-162": ("b", "pct_trunc", "+3.5%~+5.7% 涨幅被截成价格 3.0"),
    "600519.SH@pr-164": ("b", "pct_trunc", "-8.0%~-9.6% 回撤被截成价格 8.0"),
    "688981.SH@pr-149": ("b", "pct_trunc", "+11.2% 涨幅被截成价格 11.0"),
    "601899.SH@pr-100": ("b", "non_price_amount",
        "『市值底部支撑区间 3,840~4,480 亿元』中 3,840 被截成价格 3.0"),
    "601088.SH@pr-140": ("b", "non_stock_price",
        "『现货煤价中枢向1000元/吨』商品价格，非本股价"),
    "601088.SH@pr-167": ("b", "pct_trunc", "-6.1% 回撤被截成价格 6.0"),
    "600760.SH@pr-137": ("b", "pct_trunc", "+20.6% 涨幅被截成价格 20.0"),
    "600760.SH@pr-153": ("b", "pct_trunc", "折价1.3%/溢价0.2% 被截成价格 1.0"),
    "601012.SH@pr-096": ("b", "non_price_year", "2026Q1 年份被当成价格 2026.0"),
    "600309.SH@pr-186": ("b", "non_price_claim_id", "论据编号 INV-7 被当成价格 7.0"),
    "601012.SH@pr-155": ("b", "pct_trunc", "+3.0% 被截成价格 3.0"),
    "601012.SH@pr-156": ("b", "pct_trunc", "+3.0% 被截成价格 3.0"),
    "601012.SH@pr-162": ("b", "pct_trunc", "+3.0% 被截成价格 3.0"),
    "601012.SH@pr-163": ("b", "pct_trunc", "+3.0% 被截成价格 3.0"),
    "601012.SH@pr-173": ("b", "pct_trunc", "+3.0% 被截成价格 3.0"),
    "601012.SH@pr-175": ("b", "pct_trunc", "+3.0% 被截成价格 3.0"),
    "600309.SH@pr-083": ("b", "non_stock_price",
        "丙二醇 9766.67 元/吨商品报价被登记为 raw 价格"),
    "603288.SH@pr-148": ("b", "pct_trunc", "+8.4% 涨幅被截成价格 8.0"),
    "603288.SH@pr-149": ("b", "pct_trunc", "+16.6% 涨幅被截成价格 16.0"),
    "000063.SZ@pr-170": ("b", "non_price_ma_period", "SMA50 均线周期数被当成价格 50.0"),
    "000063.SZ@pr-191": ("b", "non_price_date_frag", "日期片段 07-22 被当成价格 7.0"),
    "002027.SZ@pr-245": ("b", "non_price_ma_period", "10日EMA/10.5亿被当成价格 10.0"),
    "600036.SH@pr-111": ("b", "non_price_pb", "0.75x PB 被当成价格"),
    "600276.SH@pr-147": ("b", "pct_trunc", "+21.2% 涨幅被截成价格 21.0"),
    "600276.SH@pr-148": ("b", "pct_trunc", "-5.4% 跌幅被截成价格 5.0"),
    "600276.SH@pr-160": ("b", "pct_trunc", "+21.2% 涨幅被截成价格 21.0"),
    "600276.SH@pr-161": ("b", "pct_trunc", "-5.4% 跌幅被截成价格 5.0"),
    # --- (b) 抽取误报：provenance 误标（真实价格被 typed_disclosure 错挂 basis）---
    "601088.SH@pr-060": ("b", "mislabel_provenance+date_1308",
        "『按当前48.03元股价折算』是当时现价，被 private_placement provenance 误标 "
        "pit_raw，且 as_of 被记成解禁日 2026-10-08（>分析日，1308 同类日期误读）"),
    "001979.SZ@pr-128": ("b", "mislabel_provenance",
        "LPR 3.00% 指标句被挂 private_placement provenance 并登记为 pit_raw 价格 1.0"),
    "001979.SZ@pr-169": ("b", "non_price_ma_period+mislabel",
        "『跌破 10 EMA』的周期数 10 被当成价格并误挂 block_trade provenance"),
    "001979.SZ@pr-106": ("b", "mislabel_provenance",
        "6.78 为 10EMA 真实均线值，被误挂 block_trade provenance 标为 raw"),
    "001979.SZ@pr-107": ("b", "mislabel_provenance",
        "7.61 与前复权阻力值同数，被误挂 block_trade provenance 标为 raw"),
    "601398.SH@pr-159": ("b", "non_stock_price+mislabel",
        "『200亿元永续债发行』事件值 1.0 被挂 issuance provenance 标为 pit_raw"),
    "603288.SH@pr-113": ("b", "mislabel_provenance",
        "35.00 元模型压力位被误挂 block_trade provenance 标为 raw"),
    "603288.SH@pr-114": ("b", "mislabel_provenance",
        "33.50 元模型中枢位被误挂 block_trade provenance 标为 raw"),
    "002027.SZ@pr-116": ("b", "mislabel_provenance",
        "『当前股价4.67元』=前复权值，被误挂 private_placement provenance 标为 pit_raw"),
    "002027.SZ@pr-117": ("b", "mislabel_provenance",
        "5.11 元为真实增发定价；与同句误标的 pr-116 一并触发混用告警"
        "（其 pit_raw 标注本身有据，violations 由 pr-116 误标引起，故归 b）"),
    "600519.SH@pr-171": ("b", "mislabel_provenance",
        "1268.53 为按事后前复权序列算出的布林下轨值，被误挂 block_trade provenance "
        "标为 raw（数值属 qfq 坐标，非当时真实价，故非 1309 类）"),
    # --- (b) 抽取误报：文档已声明前复权但 basis 未传递到 ref ---
    "601088.SH@pr-119": ("b", "declared_basis_miss",
        "目标价 52.00 元；final_trade_decision 明文『第二止盈位：前复权 52.00 元』，"
        "声明口径未传递到 ref"),
    "688981.SH@pr-170": ("b", "declared_basis_miss", "149.50-150.50 入场价，文档声明一律前复权"),
    "688981.SH@pr-172": ("b", "declared_basis_miss", "147.50 元，文档声明一律前复权"),
    "688981.SH@pr-177": ("b", "declared_basis_miss", "150.50 元，文档声明一律前复权"),
    "688981.SH@pr-198": ("b", "declared_basis_miss", "164.00 止盈价，文档声明一律前复权"),
    "688981.SH@pr-205": ("b", "declared_basis_miss", "目标价 164.00 元，文档声明一律前复权"),
    "601088.SH@pr-189": ("b", "declared_basis_miss", "44.40 挂单带，同句标注前复权"),
    "601088.SH@pr-194": ("b", "declared_basis_miss", "44.40 挂单带，文档声明一律前复权"),
    "601088.SH@pr-195": ("b", "declared_basis_miss", "44.40 挂单带，文档声明一律前复权"),
    "601088.SH@pr-197": ("b", "declared_basis_miss", "44.10 挂单价，文档声明一律前复权"),
    "601088.SH@pr-198": ("b", "declared_basis_miss", "43.95 挂单价，文档声明一律前复权"),
    "601088.SH@pr-201": ("b", "declared_basis_miss", "44.40 门槛价，文档声明一律前复权"),
    "601088.SH@pr-208": ("b", "declared_basis_miss", "44.40 挂单带，同句声明前复权"),
    "601088.SH@pr-213": ("b", "declared_basis_miss", "43.90-44.40 区间，同句标注前复权"),
    "601088.SH@pr-216": ("b", "declared_basis_miss", "44.15 建仓中轴，文档声明一律前复权"),
    "601088.SH@pr-217": ("b", "non_price_diff", "0.65 元为止损敞口差额，非价位"),
    "601088.SH@pr-218": ("b", "declared_basis_miss", "44.40 挂单带，同句标注前复权"),
    "601088.SH@pr-220": ("b", "declared_basis_miss", "44.15 挂单价，同句标注前复权"),
    "601088.SH@pr-221": ("b", "declared_basis_miss", "43.95 挂单价，同句标注前复权"),
    "601088.SH@pr-225": ("b", "declared_basis_miss", "46.00 保本止盈触发价，文档声明一律前复权"),
    "601088.SH@pr-227": ("b", "declared_basis_miss", "44.40 挂单带，文档声明一律前复权"),
    "601088.SH@pr-200": ("b", "declared_basis_miss",
        "45.70 入场触发区间均值，final_trade_decision 声明一律前复权"),
    "600760.SH@pr-227": ("b", "non_price_position_pct",
        "『3%+3%+4%』建仓预算百分数被当成价格 3.0"),
    "600036.SH@pr-169": ("b", "non_price_bare_number",
        "MANAGER_VERDICT excluded_evidence 中的孤立数字『7』被当成价格 7.0"),
    "001979.SZ@pr-168": ("b", "non_price_ma_period",
        "『以 10 EMA 动态生命线为刚性止损点』的均线周期数 10 被当成价格"),
    # --- (d) 其他：raw 标注正确的真实披露价，违规为未加不可比标注的并列展示，
    #     门判定符合契约，既非提取误报也非 1309（该股 07-22 后无除权，raw==qfq）---
    "603288.SH@pr-098": ("d", "genuine_raw_unlabeled_dual",
        "39.60 为真实大宗溢价成交价，raw 标注正确；与 qfq 现价并列未标注『不可直接比较』，"
        "按契约 §3 确属违规、拦截正确，但不属抽取误报（07-22 后无除权，非 1309）"),
    "603288.SH@pr-099": ("d", "genuine_raw_unlabeled_dual",
        "37.00 为大宗对应收盘价，raw 标注正确；同上，违规为未标注的跨坐标并列展示"),
}

# unbacked / wrong_basis 的 level 判定： (row_index, field, value) -> (cls, note)
LEVEL_OVERRIDE: Dict[tuple, tuple] = {
    (3, "trader_investment_plan", 7.45): ("b",
        "『工行前复权股价跌破8.00…回踩200日线（7.45元）』同句有前复权口径，真实价位漏登记"),
    (6, "investment_plan", 7.0): ("b", "excluded_evidence 中孤立数字 7 被当成可执行价位"),
    (10, "final_trade_decision", 0.0): ("b", "『准入仓位0%』仓位数被当成可执行价位"),
    (11, "final_trade_decision", 1.5): ("b", "『回撤1.5倍日内ATR』倍数被当成可执行价位"),
    (11, "final_trade_decision", 5.0): ("b", "『盘口5档』档数被当成可执行价位"),
    (12, "trader_investment_plan", 1.0): ("b", "『减仓1/3』分数被当成可执行价位"),
    (12, "final_trade_decision", 1.0): ("b", "『减仓1/3』分数被当成可执行价位"),
    (13, "investment_plan", 0.0): ("b", "position_pct=0 被当成可执行价位"),
    (13, "final_trade_decision", 5.0): ("b", "『前5个交易日』日数被当成可执行价位"),
    (14, "final_trade_decision", 8.0): ("b", "『超过8个交易日』日数被当成可执行价位"),
    (15, "trader_investment_plan", 370.0): ("b",
        "『回踩370.00元区间』（200日线370.07附近）为真实价位；文档声明全部坐标前复权，漏登记"),
    (15, "final_trade_decision", 401.0): ("b",
        "『反抽401.00-404.36元受阻』为真实价位；文档声明全部坐标前复权，漏登记"),
    (18, "trader_investment_plan", 14.0): ("b", "『约14倍PE』估值倍数被当成可执行价位"),
    (22, "trader_investment_plan", 46.0): ("b",
        "『止盈位下调至46.00-46.50元』为真实价位；文档声明全部坐标前复权，漏登记"),
    (22, "final_trade_decision", 0.65): ("b", "0.65 元为止损敞口差额，非价位"),
    (22, "final_trade_decision", 46.0): ("b", "46.00 保本触发价，文档声明一律前复权"),
    (24, "final_trade_decision", 94.0): ("b", "『收紧至94.00元（前复权）』同句有口径，漏登记"),
    (25, "investment_plan", 10.0): ("b", "『10 EMA』周期数被当成可执行价位"),
    (26, "final_trade_decision", 45.7): ("b", "45.70 入场均值，文档声明一律前复权"),
    (26, "final_trade_decision", 5.0): ("b", "『盘口5档』档数被当成可执行价位"),
    (28, "final_trade_decision", 85.0): ("b", "『布伦特原油突破85美元/桶』商品价格，非本股价"),
    (33, "final_trade_decision", 3.0): ("b", "『3%+3%+4%』建仓预算百分数被当成可执行价位"),
    (38, "investment_plan", 50.0): ("b", "SMA50 周期数被当成可执行价位"),
    (39, "trader_investment_plan", 1.0): ("b", "『跨越1个交易日』日数被当成可执行价位"),
    (40, "trader_investment_plan", 73.8): ("b",
        "『止盈目标下调至73.80元』为真实价位；文档声明全部坐标前复权，漏登记"),
    (42, "investment_plan", 0.75): ("b", "0.75x PB 估值倍数被当成可执行价位"),
    (43, "investment_plan", 19.46): ("b", "『TTM PE跌至19.46倍』估值倍数被当成可执行价位"),
    (44, "investment_plan", 42.0): ("b", "『42倍PE』估值倍数被当成可执行价位"),
    (44, "trader_investment_plan", 42.0): ("b", "『42倍PE』估值倍数被当成可执行价位"),
}


def _level_value(detail: str) -> Optional[float]:
    m = re.search(r"可执行价位 ([\d.]+)", detail or "")
    return float(m.group(1)) if m else None


def classify_violation(row_idx: int, symbol: str, violation: Dict[str, Any],
                       refs: Dict[str, Dict[str, Any]]) -> tuple:
    """Return (cls, subtype, note) for one gate violation."""
    kind = violation.get("kind")
    detail = violation.get("detail") or ""
    field = violation.get("source")

    if kind in ("unbacked_executable_level", "executable_level_wrong_basis"):
        val = _level_value(detail)
        ov = LEVEL_OVERRIDE.get((row_idx, field, val))
        if ov:
            return ov[0], "level_" + kind, ov[1]
        return "d", "level_unclassified", f"可执行价位 {val} 未分类（需人工复核）"

    # ref 型 violation：找“问题 ref”（非 vendor_qfq / 无 as_of 的一侧）
    offenders: List[tuple] = []
    for rid in violation.get("ref_ids") or []:
        ref = refs.get(rid) or {}
        if ref.get("basis") == "vendor_qfq" and kind != "decision_driving_missing_as_of":
            continue  # qfq 一侧是无辜配对
        key = f"{symbol}@{rid}"
        if key in REF_OVERRIDE:
            offenders.append(REF_OVERRIDE[key])
        elif ref.get("basis") == "vendor_qfq":
            # missing_as_of 打在 vendor_qfq 上不继承 cutoff 的情况不存在；防御性
            offenders.append(("d", "qfq_side_flagged", f"vendor_qfq ref {rid} 被点名，需人工复核"))
        else:
            offenders.append(("d", "unclassified_ref",
                              f"ref {rid} v={ref.get('value')} basis={ref.get('basis')} 未分类"))
    if not offenders:
        return "d", "no_offender", "违规未点名非 qfq ref，需人工复核"
    # 优先级：a > d > c > b（任一 a/d 即按更保守类记）
    order = {"a": 0, "d": 1, "c": 2, "b": 3}
    offenders.sort(key=lambda t: order[t[0]])
    cls, sub, note = offenders[0]
    if len(offenders) > 1:
        note += "（同违规其余 ref：" + "; ".join(
            f"{o[0]}/{o[1]}" for o in offenders[1:]) + "）"
    return cls, sub, note


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True, help="sqlite 报告库路径（只读打开）")
    ap.add_argument("--corpus", required=True)
    ap.add_argument("--table", help="Markdown 逐条表输出路径")
    args = ap.parse_args()

    con = sqlite3.connect(f"file:{args.db}?mode=ro", uri=True)
    con.execute("PRAGMA query_only=ON")

    rows = [l.strip() for l in open(args.corpus) if l.strip()]
    table: List[Dict[str, Any]] = []
    for i, line in enumerate(rows):
        rid, horizon, src = line.split(":")
        sym, td, rd_raw = con.execute(
            "select symbol, trade_date, result_data from reports where id=?", (rid,)
        ).fetchone()
        rd = json.loads(rd_raw)
        hkey = "short_term" if horizon == "short_term" else "medium_term"
        h = rd.get(hkey) or {}
        gate = h.get("price_basis_gate") or {}
        refs = {x.get("ref_id"): x for x in (h.get("price_refs") or [])}
        for j, v in enumerate(gate.get("violations") or []):
            cls, sub, note = classify_violation(i, sym, v, refs)
            table.append({
                "row": i, "report_id": rid, "symbol": sym, "trade_date": td,
                "horizon": horizon, "source": src, "v_idx": j,
                "kind": kind_of(v), "field": v.get("source"),
                "cls": cls, "subtype": sub, "note": note,
                "detail": (v.get("detail") or "")[:120],
            })

    # ---- 汇总 ----
    from collections import Counter, defaultdict
    per_row = defaultdict(list)
    for t in table:
        per_row[t["row"]].append(t)
    meta = {}
    for i, line in enumerate(rows):
        rid, horizon, src = line.split(":")
        meta[i] = (rid, horizon, src, per_row[i][0]["symbol"], per_row[i][0]["trade_date"])

    tier_stat = {}
    for i, ts in per_row.items():
        cls_set = {t["cls"] for t in ts}
        # 误拦：去掉全部 (b)(c) 后无剩余违规
        residual = cls_set - {"b", "c"}
        tier_stat[i] = ("误拦" if not residual else "正确拦截/其他", sorted(residual))

    for grp in ("daily", "batch"):
        idx = [i for i in per_row if meta[i][2] == grp]
        n = len(idx)
        wrong = sum(1 for i in idx if tier_stat[i][0] == "误拦")
        vc = Counter(t["cls"] for i in idx for t in per_row[i])
        print(f"[{grp}] 档位={n} 误拦={wrong} 正确/其他={n-wrong} "
              f"误拦率={wrong/n*100:.1f}%  violations: a={vc['a']} b={vc['b']} c={vc['c']} d={vc['d']}")
    vc_all = Counter(t["cls"] for t in table)
    print(f"[合计] violations={len(table)} a={vc_all['a']} b={vc_all['b']} c={vc_all['c']} d={vc_all['d']}")
    print("[误拦档位]", sorted(i for i in per_row if tier_stat[i][0] == "误拦"))
    print("[非误拦档位]", {i: tier_stat[i][1] for i in per_row if tier_stat[i][0] != "误拦"})
    # 1308/1309 可消除量
    c_rows = sorted({t["row"] for t in table if t["cls"] == "c"})
    c_only = [i for i in c_rows if tier_stat[i][0] == "误拦" and
              not ({t["cls"] for t in per_row[i]} - {"b", "c"})]
    print(f"[1309类 c] violations={vc_all['c']} 涉及档位={c_rows} "
          f"其中可整体解救档位={[i for i in c_rows if tier_stat[i][0]=='误拦']}")

    if args.table:
        with open(args.table, "w") as f:
            f.write("|#|report|symbol|trade_date|horizon|grp|kind|field|类|子类|说明|\n")
            f.write("|-|-|-|-|-|-|-|-|-|-|-|-|\n")
            for t in table:
                f.write("|{row}|{rid}|{symbol}|{trade_date}|{horizon}|{source}|{kind}|{field}|{cls}|{subtype}|{note}|\n".format(
                    rid=t["report_id"][:8], **{k: t[k] for k in t if k != "report_id"}))
        print("wrote", args.table)


def kind_of(v: Dict[str, Any]) -> str:
    return v.get("kind") or ""


if __name__ == "__main__":
    main()
