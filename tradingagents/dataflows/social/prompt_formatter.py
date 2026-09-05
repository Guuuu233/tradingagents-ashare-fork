"""Prompt formatting for social media analyst (Task 11 / §六 / D-009 / D-010).

Formats structured SentimentBundleV1 and market_attention into the four-section HumanMessage
prompt for active mode social analyst:
1. 【数据状态】(Data Status & Coverage)
2. 【社交观点】(Social Stance & Sentiment)
3. 【社交热度】(Social Attention & Volume)
4. 【市场关注度】(Market Attention: zt_pool, hot_stocks)

Enforces core guardrails:
- 热度 ≠ 看多 (Attention != Bullish)
- score 非校准概率 (Score is not calibrated probability)
- 数据不足/失败 → 不可判断，不得编造方向 (Insufficient/Failed -> Direction cannot be judged)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Union

from tradingagents.dataflows.social.contracts import (
    SentimentBundleV1,
    SocialAttention,
    SocialSentiment,
)


def format_social_sections(
    bundle: Optional[Union[SentimentBundleV1, Dict[str, Any]]] = None,
    market_attention: Optional[Dict[str, Any]] = None,
    ticker_display: str = "",
    current_date: str = "",
    horizon_ctx: str = "",
    status: Optional[str] = None,
    direction_allowed: Optional[bool] = None,
    reason_codes: Optional[List[str]] = None,
) -> str:
    """Format the four structured sections for active social media analyst HumanMessage."""
    # 1. Normalize bundle
    bundle_dict: Dict[str, Any] = {}
    if isinstance(bundle, SentimentBundleV1):
        bundle_dict = bundle.to_dict()
    elif isinstance(bundle, dict):
        bundle_dict = bundle

    # Extract fields with fallbacks
    bundle_status = status or bundle_dict.get("status", "empty")
    requested_as_of = bundle_dict.get("requested_as_of") or current_date or "unknown"
    cutoff_at = bundle_dict.get("cutoff_at")
    if not cutoff_at:
        if current_date:
            try:
                from tradingagents.dataflows.social.provider import compute_as_of_cutoff

                _, cutoff_utc, _ = compute_as_of_cutoff(current_date)
                cutoff_at = (
                    cutoff_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
                    if cutoff_utc.microsecond == 0
                    else cutoff_utc.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
                )
            except Exception:
                cutoff_at = "unknown"
        else:
            cutoff_at = "unknown"
    content_as_of = bundle_dict.get("content_as_of")
    metric_as_of = bundle_dict.get("metric_as_of")

    if direction_allowed is None:
        direction_allowed = bool(bundle_dict.get("direction_allowed", False))

    reasons = reason_codes if reason_codes is not None else bundle_dict.get("reason_codes", [])

    # Social attention & sentiment extraction
    attention_raw = bundle_dict.get("social_attention") or {}
    if isinstance(attention_raw, SocialAttention):
        attention_dict = attention_raw.to_dict()
    elif isinstance(attention_raw, dict):
        attention_dict = attention_raw
    else:
        attention_dict = {}

    sentiment_raw = bundle_dict.get("social_sentiment") or {}
    if isinstance(sentiment_raw, SocialSentiment):
        sentiment_dict = sentiment_raw.to_dict()
    elif isinstance(sentiment_raw, dict):
        sentiment_dict = sentiment_raw
    else:
        sentiment_dict = {}

    evidence_samples = bundle_dict.get("evidence_samples") or []
    platform_breakdown = bundle_dict.get("platform_breakdown") or {}

    # ── Section 1: 【数据状态】 ───────────────────────────────────────
    status_lines = [
        "【一、数据状态与数据源有效性】",
        f"- 标的与分析日期：{ticker_display} ({current_date})",
        f"- 社交归档状态：{bundle_status}",
        f"- 时间截点：请求基线 {requested_as_of} | 截断时间 {cutoff_at}",
        f"- 最新正文时效：{content_as_of if content_as_of else '无有效正文'}",
        f"- 最新指标时效：{metric_as_of if metric_as_of else '无有效指标'}",
        f"- 允许方向推断 (direction_allowed)：{'是 (True)' if direction_allowed else '否 (False)'}",
    ]
    if reasons:
        status_lines.append(f"- 状态原因码 (reason_codes)：{', '.join(reasons)}")

    if platform_breakdown:
        breakdown_parts = []
        for plat, stats in platform_breakdown.items():
            if isinstance(stats, dict):
                p_cnt = stats.get("post_count", 0)
                c_cnt = stats.get("comment_count", 0)
                breakdown_parts.append(f"{plat}: 帖子 {p_cnt} 篇, 评论 {c_cnt} 条")
            else:
                breakdown_parts.append(f"{plat}: {stats}")
        status_lines.append(f"- 平台覆盖明细：{'; '.join(breakdown_parts)}")
    else:
        status_lines.append("- 平台覆盖明细：无平台有效数据")

    if not direction_allowed or bundle_status != "available":
        status_lines.append(
            "【社交方向不可判断】社交归档数据处于不可用/未采集/样本不足状态"
            f"（社交归档状态：{bundle_status}，direction_allowed=False）。\n"
            "【核心语义铁律（不可用 ≠ 市场冷淡）】：\n"
            "1. 严禁将数据不可用、未采集、样本不足或接口关闭推导为「市场冷淡」、「无人关注」、「散户没有讨论」、「讨论真空」或「处于休眠/沉寂期」等市场事实！数据缺失仅代表采集与系统归档状态，绝不等于市场真实情绪冷淡。\n"
            "2. 社交方向不可判断：因社交数据不可用，无法进行实质多空分析，社交不得作为方向性证据；机器结论兼容保留「中性（不可判断）」，但严禁当作经分析得出的实质中性观点，该中性不进入有效中性票或校准样本。"
        )

    status_lines.append(
        "【硬约束声明】情绪得分（score）非校准概率（is_calibrated_probability=False）；"
        "社交讨论热度不等于利多（热度≠看多）。"
    )

    section_1_text = "\n".join(status_lines)

    # ── Section 2: 【社交观点】 ───────────────────────────────────────
    label = sentiment_dict.get("label", "insufficient")
    score_val = sentiment_dict.get("score")
    if score_val is not None:
        score_str = f"{score_val:+.2f} (区间[-1.0, +1.0]，非校准概率)"
    else:
        score_str = "无 (数据不足以量化)"

    bullish_cnt = sentiment_dict.get("bullish_count", 0)
    bearish_cnt = sentiment_dict.get("bearish_count", 0)
    neutral_cnt = sentiment_dict.get("neutral_count", 0)
    insufficient_cnt = sentiment_dict.get("insufficient_count", 0)

    sentiment_lines = [
        "【二、社交观点与立场解构】",
        f"- 聚合情绪定性：{label}",
        f"- 情绪量化得分：{score_str}",
        f"- 样本立场分布：看多 {bullish_cnt} 篇 | 看空 {bearish_cnt} 篇 | 中性 {neutral_cnt} 篇 | 不足/未定 {insufficient_cnt} 篇",
    ]

    if evidence_samples:
        sentiment_lines.append("- 典型社交正文与评论样本：")
        for idx, sample in enumerate(evidence_samples[:10], 1):
            plat = sample.get("platform", "unknown")
            rec_type = sample.get("record_type", "post")
            pub_at = sample.get("published_at", "")
            sample_stance = sample.get("stance_label") or sample.get("sentiment") or "neutral"
            title = (sample.get("title") or "").strip()
            text = (sample.get("text") or "").strip()
            content_preview = f"标题: {title} | 内容: {text}" if title else f"内容: {text}"
            if len(content_preview) > 120:
                content_preview = content_preview[:117] + "..."
            sentiment_lines.append(
                f"  [{idx}] [{plat} {rec_type} | {pub_at} | 立场:{sample_stance}] {content_preview}"
            )
    else:
        sentiment_lines.append("- 典型社交正文与评论样本：无可用样本")

    if not direction_allowed or bundle_status != "available":
        sentiment_lines.append(
            "【语义提示】由于社交数据不可用/未采集/方向未解锁，严禁主观推断散户观点为空白或市场无多空分歧，"
            "正文必须如实说明数据不可用/未采集，禁止将数据缺失编造为市场事实，不得作为多空方向依据。"
        )
    elif not direction_allowed:
        sentiment_lines.append("【提示】由于方向判断未解锁，上述样本仅供背景参考，不得作为多空方向依据。")

    section_2_text = "\n".join(sentiment_lines)

    # ── Section 3: 【社交热度】 ───────────────────────────────────────
    p_cnt = attention_dict.get("post_count", 0)
    c_cnt = attention_dict.get("comment_count", 0)
    a_cnt = attention_dict.get("author_count", 0)
    tot_inter = attention_dict.get("total_interactions", 0)
    vel = attention_dict.get("interaction_velocity")
    vel_str = f"{vel:.2f}/小时" if vel is not None else "无/未统计"

    if not direction_allowed or bundle_status != "available":
        attention_lines = [
            "【三、社交热度与互动特征】",
            f"- 讨论样本量：发帖数 {p_cnt} | 评论数 {c_cnt} | 独立作者数 {a_cnt}（数据不可用/未采集/样本不足）",
            f"- 互动总量（点赞/收藏/分享）：{tot_inter}（无有效统计）",
            f"- 互动扩散速度：{vel_str}",
            "【重要提示】当前社交热度数据处于不可用/未采集状态，严禁将数据缺失解释为「讨论热度为零」、「市场冷淡」、「无人讨论」或「处于冷淡真空期」！热度≠看多。严禁将热度高直接视为看多信号！",
        ]
    else:
        attention_lines = [
            "【三、社交热度与互动特征】",
            f"- 讨论样本量：发帖数 {p_cnt} | 评论数 {c_cnt} | 独立作者数 {a_cnt}",
            f"- 互动总量（点赞/收藏/分享）：{tot_inter}",
            f"- 互动扩散速度：{vel_str}",
            "【重要提示】社交热度仅衡量注意力与讨论活跃度，热度≠看多。严禁将热度高直接视为看多信号！",
        ]
    section_3_text = "\n".join(attention_lines)

    # ── Section 4: 【市场关注度】 ─────────────────────────────────────
    mkt_lines = [
        "【四、市场关注度（盘面与榜单生态）】",
    ]
    if market_attention and isinstance(market_attention, dict):
        zt_entry = market_attention.get("zt_pool")
        if isinstance(zt_entry, dict):
            zt_status = zt_entry.get("status", "available")
            zt_as_of = zt_entry.get("as_of") or zt_entry.get("requested_as_of") or ""
            zt_raw = zt_entry.get("raw")
            zt_gap = zt_entry.get("gap")
            if zt_status == "available" and zt_raw:
                mkt_lines.append(f"【涨停池数据】(时效: {zt_as_of})\n{zt_raw}")
            else:
                mkt_lines.append(f"【涨停池数据】(状态: {zt_status})\n{zt_gap or '无数据'}")
        elif zt_entry:
            mkt_lines.append(f"【涨停池数据】\n{zt_entry}")
        else:
            mkt_lines.append("【涨停池数据】\n无数据")

        hot_entry = market_attention.get("hot_stocks")
        if isinstance(hot_entry, dict):
            hot_status = hot_entry.get("status", "available")
            hot_as_of = hot_entry.get("as_of") or hot_entry.get("requested_as_of") or ""
            hot_raw = hot_entry.get("raw")
            hot_gap = hot_entry.get("gap")
            if hot_status == "available" and hot_raw:
                mkt_lines.append(f"\n【雪球热门股票】(时效: {hot_as_of})\n{hot_raw}")
            else:
                mkt_lines.append(f"\n【雪球热门股票】(状态: {hot_status})\n{hot_gap or '无数据'}")
        elif hot_entry:
            mkt_lines.append(f"\n【雪球热门股票】\n{hot_entry}")
        else:
            mkt_lines.append("\n【雪球热门股票】\n无数据")
    else:
        mkt_lines.append("【涨停池与热门股票数据】\n无市场关注度数据")

    mkt_lines.append(
        "\n【分栏独立声明】市场关注度数据源自涨停池连板生态与雪球热门榜，反映短线交易资金聚焦度，已在此独立分栏并注明来源；严禁冒充社交正文，严禁在社交数据不可用时用市场关注度倒推散户讨论事实，禁止据此主观推断散户多空情绪偏好。"
    )
    section_4_text = "\n".join(mkt_lines)

    # ── Combine all sections ─────────────────────────────────────────
    prefix = (horizon_ctx + "\n") if horizon_ctx else ""
    header = f"以下是 {ticker_display} 在 {current_date} 的社交舆情与市场关注度分析输入资料。\n\n"

    return prefix + header + section_1_text + "\n\n" + section_2_text + "\n\n" + section_3_text + "\n\n" + section_4_text


def format_social_analyst_prompt(
    bundle: Optional[Union[SentimentBundleV1, Dict[str, Any]]] = None,
    market_attention: Optional[Dict[str, Any]] = None,
    ticker_display: str = "",
    current_date: str = "",
    horizon_ctx: str = "",
) -> str:
    """Convenience entry point to format social analyst HumanMessage content."""
    return format_social_sections(
        bundle=bundle,
        market_attention=market_attention,
        ticker_display=ticker_display,
        current_date=current_date,
        horizon_ctx=horizon_ctx,
    )
