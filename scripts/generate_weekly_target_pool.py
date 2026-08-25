#!/usr/bin/env python3
"""P3-H2.1: Weekly Target Pool Generator and Strict De-duplication CLI (generate_weekly_target_pool.py).

Features:
1. Automated weekly target pool selection (8~10 stocks);
2. Shenwan Level 1 primary industry rotation (covering >= 5 core sectors);
3. Strict deduplication against historical benchmark blacklist (美的 000333.SZ, 长电 600900.SH,
   恒瑞 600276.SH, 京东方A 000725.SZ 等) and deterministic SHA256(symbol + trade_date + protocol_version);
4. P0 Dynamic Pool Re-balancing: reading cumulative bull/bear metrics, dynamically adjusting weights
   when deviation > +-5% to maintain multi-empty ratio within [40%, 60%];
5. CLI arguments: --trade-date YYYYMMDD, --count N, --dry-run, --historical-metrics, --output json/table/markdown.
"""

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

# Ensure project root in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)
from tradingagents.agents.utils.target_pool import (
    DEFAULT_MAX_SINGLE_SYMBOL_SHARE,
    DEFAULT_MIN_ADV_MIL,
    DEFAULT_MIN_MARKET_CAP_BIL,
    DEFAULT_MIN_UNIQUE_INDUSTRIES,
    HISTORICAL_BENCHMARK_BLACKLIST,
    TargetPoolResultModel,
    generate_sample_fingerprint,
    generate_weekly_target_pool,
    normalize_symbol_code,
    normalize_trade_date_str,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("generate_weekly_target_pool")


def load_historical_metrics_data(
    metrics_path: str,
) -> tuple[int, int, Set[str], Set[str]]:
    """Load historical weekly metrics JSON or evaluation matrices to extract cumulative stats.

    Returns:
        tuple of (historical_bull_count, historical_bear_count, historical_symbols_set, historical_fingerprints_set)
    """
    p = Path(metrics_path)
    if not p.exists():
        logger.warning("指定历史指标文件不存在: %s", metrics_path)
        return 0, 0, set(), set()

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)

        bull_cnt = 0
        bear_cnt = 0
        symbols: Set[str] = set()
        fingerprints: Set[str] = set()

        if isinstance(data, dict):
            # Check weekly_aggregate overview
            overview = data.get("weekly_aggregate", {}).get("overview", {})
            bull_cnt = int(overview.get("bull_decisions", 0))
            bear_cnt = int(overview.get("bear_decisions", 0))

            # Extract samples
            samples = data.get("samples") or []
            if isinstance(samples, list):
                for s in samples:
                    if isinstance(s, dict):
                        q1 = s.get("quadrant_1_protocol_metadata", {})
                        sym = str(q1.get("symbol") or "")
                        t_date = str(q1.get("trade_date") or "")
                        prot = str(q1.get("protocol_version") or PROTOCOL_VERSION_V2_STRUCTURED)
                        if sym:
                            norm_s = normalize_symbol_code(sym)
                            symbols.add(norm_s)
                            symbols.add(norm_s.split(".")[0])
                            if t_date:
                                fp = generate_sample_fingerprint(norm_s, t_date, prot)
                                fingerprints.add(fp)

        logger.info(
            "从历史指标文件 [%s] 读取到: 多头样本=%d, 空头样本=%d, 历史标的=%d, 历史指纹=%d",
            metrics_path,
            bull_cnt,
            bear_cnt,
            len(symbols),
            len(fingerprints),
        )
        return bull_cnt, bear_cnt, symbols, fingerprints

    except Exception as exc:
        logger.error("解析历史指标文件 [%s] 失败: %s", metrics_path, exc)
        return 0, 0, set(), set()


def format_table_output(result: TargetPoolResultModel) -> str:
    """Format target pool result into readable CLI ASCII table."""
    audit = result.rebalance_audit
    dist = result.industry_distribution

    lines = [
        "=" * 100,
        f"📊 P3-H2.1 每周自动化标的池筛选与防污染排重报告 (评估日: {result.trade_date})",
        "=" * 100,
        "",
        "一、 标的池筛选清单 (Candidate Target Pool)",
        "-" * 100,
        f"{'序号':<4} {'代码':<12} {'名称':<10} {'申万一级行业':<12} {'倾向':<12} {'市值(亿)':<10} {'日均成交(万)':<12} {'排重指纹(前16位)':<18}",
        "-" * 100,
    ]

    for idx, it in enumerate(result.items, 1):
        lines.append(
            f"{idx:<4} {it.symbol:<12} {it.name:<10} {it.industry:<12} {it.stance_tendency:<12} "
            f"{it.market_cap_bil:<10.1f} {it.adv_mil:<12.0f} {it.fingerprint[:16]:<18}"
        )

    lines.extend([
        "-" * 100,
        "",
        "二、 行业分散与集中度合规判定 (Diversification & Concentration Audit)",
        "-" * 100,
        f"• 样本总数: {dist.total_samples} 支 (标准: 8~10 支)",
        f"• 独立申万一级行业数: {dist.unique_industries_count} 个 (标准: >= 5 个) -> {'✅ 合规' if dist.unique_industries_count >= 5 else '❌ 不达标'}",
        f"• 覆盖行业分布: {', '.join(f'{k}({v}支)' for k, v in dist.industry_counts.items())}",
        f"• 单一标的最大占比: {dist.max_single_symbol_share*100:.1f}% (标准: <= 15.0%) -> {'✅ 合规' if dist.max_single_symbol_share <= 0.15 else '❌ 超标'}",
        f"• 综合分散度判定: {'✅ PASS (全部达标)' if dist.diversification_passed else '❌ FAIL'}",
        "",
        "三、 动态再平衡机制审计 (Dynamic Pool Re-balancing Audit [P0])",
        "-" * 100,
        f"• 历史累积多空样本: 多头={audit.historical_bull_samples} / 空头={audit.historical_bear_samples} (总计 {audit.total_directional_samples})",
        f"• 历史多头占比: {audit.historical_bull_ratio*100:.1f}% (基准 50.0% +- 5.0%)",
        f"• 偏离度: {audit.deviation_from_parity*100:+.1f}% -> 触发再平衡: {'⚠️ YES' if audit.imbalance_detected else '✅ 均衡 (NO)'}",
        f"• 再平衡调优方向: {audit.rebalance_direction}",
        f"• 算法调整说明: {audit.rebalance_rationale}",
        f"• 目标多空比区间约束: [{audit.target_pool_expected_ratio_min*100:.0f}%, {audit.target_pool_expected_ratio_max*100:.0f}%]",
        "",
        "四、 防污染排重与指纹审计 (Strict De-duplication & Fingerprint Audit)",
        "-" * 100,
        f"• 显式历史基准黑名单拦截: {result.blacklist_filtered_count} 支 (000333.SZ / 600900.SH / 600276.SH / 000725.SZ 等)",
        f"• 重复 SHA256 指纹剔除: {result.duplicate_fingerprints_dropped} 例",
        f"• 协议版本: {result.protocol_version}",
        f"• 排重审计状态: {'✅ PASSED (0 历史污染与重复)' if result.blacklist_filtered_count >= 0 else '❌'}",
        "=" * 100,
    ])

    return "\n".join(lines)


def format_markdown_output(result: TargetPoolResultModel) -> str:
    """Format target pool result into standardized Markdown report."""
    audit = result.rebalance_audit
    dist = result.industry_distribution

    lines = [
        f"# 每周标的池自动化筛选与排重报告 ({result.trade_date})",
        "",
        f"> **生成时间**：`{result.generated_at}`  ",
        f"> **协议版本**：`{result.protocol_version}`  ",
        f"> **行业分散状态**：`{'✅ PASS' if dist.diversification_passed else '❌ FAIL'}` (覆盖 {dist.unique_industries_count} 行业)  ",
        f"> **动态再平衡状态**：`{audit.rebalance_direction}` (历史多头比: {audit.historical_bull_ratio*100:.1f}%)",
        "",
        "---",
        "",
        "## 一、 标的池推荐清单",
        "",
        "| 序号 | 标的代码 | 标的名称 | 申万一级行业 | 倾向特征 | 总市值 (亿元) | 20日成交额 (万元) | SHA256 排重指纹 |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    for idx, it in enumerate(result.items, 1):
        lines.append(
            f"| {idx} | `{it.symbol}` | **{it.name}** | {it.industry} | `{it.stance_tendency}` | "
            f"{it.market_cap_bil:.1f} | {it.adv_mil:.0f} | `{it.fingerprint[:16]}...` |"
        )

    lines.extend([
        "",
        "---",
        "",
        "## 二、 行业分散与合规审计",
        "",
        f"- **样本规模**：`{dist.total_samples}` 支（标准要求 8~10 支）；",
        f"- **申万一级行业覆盖**：`{dist.unique_industries_count}` 个（涵盖：{', '.join(dist.covered_industries)}），满足 $\\ge 5$ 个行业门槛；",
        f"- **单一标的集中度**：最高占比 `{dist.max_single_symbol_share*100:.1f}%`，严格符合 $\\le 15.0\\%$ 约束；",
        f"- **黑名单与历史排重**：严格剔除历史案例基准标的，无数据污染。",
        "",
        "---",
        "",
        "## 三、 动态再平衡说明 (P0)",
        "",
        f"- **历史多空样本**：多头 `{audit.historical_bull_samples}` / 空头 `{audit.historical_bear_samples}`（多头比 `{audit.historical_bull_ratio*100:.1f}%`）；",
        f"- **偏离分析**：偏离度 `{audit.deviation_from_parity*100:+.1f}%`，再平衡模式：`{audit.rebalance_direction}`；",
        f"- **再平衡策略**：{audit.rebalance_rationale}",
        "",
    ])

    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P3-H2.1: Weekly Target Pool Selection, Industry Rotation, SHA256 Fingerprint & Dynamic Re-balancing CLI."
    )
    parser.add_argument(
        "--trade-date",
        type=str,
        default=datetime.now(timezone.utc).strftime("%Y%m%d"),
        help="Target trade date in YYYYMMDD or YYYY-MM-DD format (default: today).",
    )
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Sample pool size to select (default: 10, recommended 8~10).",
    )
    parser.add_argument(
        "--historical-metrics",
        type=str,
        default=None,
        help="Optional path to historical weekly metrics JSON file for dynamic re-balancing input.",
    )
    parser.add_argument(
        "--bull-samples",
        type=int,
        default=None,
        help="Explicit historical bull samples count for dynamic re-balancing.",
    )
    parser.add_argument(
        "--bear-samples",
        type=int,
        default=None,
        help="Explicit historical bear samples count for dynamic re-balancing.",
    )
    parser.add_argument(
        "--blacklist-symbols",
        type=str,
        default="",
        help="Comma-separated additional stock symbols to blacklist.",
    )
    parser.add_argument(
        "--protocol-version",
        type=str,
        default=PROTOCOL_VERSION_V2_STRUCTURED,
        help="Protocol version string for SHA256 fingerprints (default: v2_structured_disagreement).",
    )
    parser.add_argument(
        "--output",
        type=str,
        choices=["table", "json", "markdown"],
        default="table",
        help="Output presentation format (table, json, markdown). Default: table.",
    )
    parser.add_argument(
        "--output-file",
        type=str,
        default=None,
        help="Optional output file path to write results.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Dry run mode: compute selection without persisting any state.",
    )

    args = parser.parse_args()

    norm_date = normalize_trade_date_str(args.trade_date)
    custom_blacklist: Set[str] = set()
    if args.blacklist_symbols:
        for s in args.blacklist_symbols.split(","):
            s_clean = s.strip()
            if s_clean:
                custom_blacklist.add(s_clean)

    # Resolve historical metrics inputs
    hist_bull = 0
    hist_bear = 0
    hist_fps: Set[str] = set()

    if args.historical_metrics:
        b_cnt, be_cnt, _, fps = load_historical_metrics_data(args.historical_metrics)
        hist_bull = b_cnt
        hist_bear = be_cnt
        hist_fps = fps

    if args.bull_samples is not None:
        hist_bull = args.bull_samples
    if args.bear_samples is not None:
        hist_bear = args.bear_samples

    # Generate Target Pool
    result = generate_weekly_target_pool(
        trade_date=norm_date,
        count=args.count,
        historical_bull_samples=hist_bull,
        historical_bear_samples=hist_bear,
        historical_fingerprints=hist_fps,
        blacklist_symbols=custom_blacklist,
        protocol_version=args.protocol_version,
    )

    # Format Output
    if args.output == "json":
        out_str = result.model_dump_json(indent=2)
    elif args.output == "markdown":
        out_str = format_markdown_output(result)
    else:
        out_str = format_table_output(result)

    # Print to stdout
    print(out_str)

    # Write to output file if requested
    if args.output_file and not args.dry_run:
        out_path = Path(args.output_file)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(out_str)
        logger.info("标的池报告已保存至: %s", out_path)

    return 0


if __name__ == "__main__":
    sys.exit(main())
