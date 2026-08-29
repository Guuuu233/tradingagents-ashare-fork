"""Deterministic Chinese equity stance classifier (Task 6 / B6).

Specification:
- docs/social_data/implementation_plan.md Task 6, §5.4
- Lexicon version: cn_equity_stance_lexicon.v1
- Purely deterministic; NO LLM calls.
- Empty text: direction weight 0; does not count as direction sample.
- Negation window: negation word within 3 Chinese characters before sentiment word flips polarity.
- Unrecognized text -> 'unknown', NOT 'neutral'.
- Stance labels: 'bullish', 'bearish', 'neutral', 'mixed', 'unknown'.
"""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

LEXICON_VERSION: str = "cn_equity_stance_lexicon.v1"

# ============================================================================
# Lexicon Definitions
# ============================================================================

# Negation words sorted by length descending
NEGATION_WORDS: Tuple[str, ...] = (
    "绝对不会",
    "绝不建议",
    "坚定不移地不",
    "强烈不建议",
    "不再建议",
    "不要轻易",
    "并不建议",
    "绝不抄底",
    "切勿盲目",
    "不再看好",
    "并不看好",
    "不再看多",
    "并不看多",
    "不再看空",
    "并不看空",
    "难以企稳",
    "难以突破",
    "绝不",
    "从不",
    "决不",
    "毫不",
    "并不",
    "绝无",
    "毫无",
    "难以",
    "无法",
    "不能",
    "不会",
    "不大",
    "未必",
    "不再",
    "不要",
    "没有",
    "不是",
    "切勿",
    "严禁",
    "别去",
    "别买",
    "别追",
    "别卖",
    "勿追",
    "莫追",
    "不",
    "没",
    "别",
    "非",
    "未",
    "无",
    "莫",
    "勿",
)

# Bullish keywords (sorted by length descending for longest-match precedence)
BULLISH_KEYWORDS: Tuple[str, ...] = (
    "连续涨停",
    "强烈推荐",
    "极度看好",
    "强烈看多",
    "主升浪启动",
    "突破历史新高",
    "创历史新高",
    "业绩大幅增长",
    "业绩大增",
    "超预期增长",
    "主力资金流入",
    "主力持续加仓",
    "放量突破",
    "主力建仓",
    "主力拉升",
    "放量大涨",
    "翻倍牛股",
    "借壳上市",
    "重组成功",
    "黄金坑底",
    "绝佳买点",
    "坚定持有",
    "重大利好",
    "全线爆发",
    "封死涨停",
    "连板晋级",
    "资金抢筹",
    "筹码锁定",
    "多头排列",
    "跨年妖股",
    "龙回头",
    "涨停潮",
    "超预期",
    "主升浪",
    "创新高",
    "牛市来",
    "大牛市",
    "大牛股",
    "连板",
    "涨停",
    "大涨",
    "暴涨",
    "看多",
    "做多",
    "买入",
    "加仓",
    "建仓",
    "抄底",
    "牛市",
    "突破",
    "起飞",
    "封板",
    "强势",
    "走强",
    "利好",
    "绩优",
    "翻倍",
    "龙头",
    "抢筹",
    "冲高",
    "多头",
    "反弹",
    "企稳",
    "补仓",
    "增持",
    "满仓",
    "梭哈",
    "爆发",
    "领涨",
    "飙升",
    "涨价",
    "涨势",
    "妖股",
    "看好",
    "上攻",
    "逼空",
    "慢牛",
    "阳线",
    "低吸",
    "多头",
)

# Bearish keywords (sorted by length descending)
BEARISH_KEYWORDS: Tuple[str, ...] = (
    "连续跌停",
    "强烈看空",
    "严重看空",
    "极度看空",
    "主跌浪开启",
    "跌破支撑位",
    "创历史新低",
    "业绩大幅下滑",
    "业绩大降",
    "不及预期",
    "主力资金流出",
    "主力疯狂出货",
    "主力砸盘",
    "放量大跌",
    "踩雷暴雷",
    "立案调查",
    "重大利空",
    "全线崩溃",
    "封死跌停",
    "破位下行",
    "资金出逃",
    "阴跌不止",
    "跌停潮",
    "绝佳卖点",
    "赶紧跑路",
    "割肉离场",
    "清仓离场",
    "大烂股",
    "杀跌",
    "暴雷",
    "爆雷",
    "闪崩",
    "腰斩",
    "退市",
    "跌停",
    "大跌",
    "暴跌",
    "看空",
    "做空",
    "卖出",
    "减仓",
    "清仓",
    "割肉",
    "熊市",
    "破位",
    "坠落",
    "炸板",
    "破产",
    "弱势",
    "走弱",
    "利空",
    "出货",
    "砸盘",
    "空头",
    "回调",
    "阴跌",
    "减持",
    "空仓",
    "踏空",
    "崩盘",
    "领跌",
    "狂跌",
    "降价",
    "跌势",
    "忽悠",
    "亏损",
    "计提",
    "踩雷",
    "跑路",
    "破发",
    "诱多",
    "避险",
    "阴线",
    "跳水",
    "接盘",
    "被套",
    "套牢",
)

# Neutral keywords (sorted by length descending)
NEUTRAL_KEYWORDS: Tuple[str, ...] = (
    "窄幅震荡整理",
    "多空力量平衡",
    "维持区间震荡",
    "维持震荡整理",
    "多空反复博弈",
    "窄幅震荡",
    "震荡整理",
    "多空平衡",
    "波动不大",
    "窄幅整理",
    "震荡走势",
    "均线缠绕",
    "筹码密集",
    "保持观望",
    "谨慎观望",
    "静待方向",
    "多空博弈",
    "趋势不明",
    "暂无方向",
    "窄幅波动",
    "底部震荡",
    "平台整理",
    "走势平稳",
    "区间震荡",
    "震荡",
    "盘整",
    "观望",
    "横盘",
    "中性",
    "走平",
    "箱体",
    "整理",
    "待观察",
)


# ============================================================================
# Dataclasses
# ============================================================================

@dataclass
class StanceHit:
    """Detailed hit auditing record for a matched sentiment term."""

    keyword: str
    start_pos: int
    end_pos: int
    original_polarity: str  # 'bullish' | 'bearish' | 'neutral'
    effective_polarity: str  # 'bullish' | 'bearish' | 'neutral'
    is_negated: bool = False
    negation_word: Optional[str] = None
    rule_id: str = "lexicon_match"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "keyword": self.keyword,
            "start_pos": self.start_pos,
            "end_pos": self.end_pos,
            "original_polarity": self.original_polarity,
            "effective_polarity": self.effective_polarity,
            "is_negated": self.is_negated,
            "negation_word": self.negation_word,
            "rule_id": self.rule_id,
        }


@dataclass
class StanceClassificationResult:
    """Classification outcome for a single text or raw record."""

    stance: str  # 'bullish' | 'bearish' | 'neutral' | 'mixed' | 'unknown'
    lexicon_version: str = LEXICON_VERSION
    is_empty: bool = False
    hits: List[StanceHit] = field(default_factory=list)
    bullish_hits: int = 0
    bearish_hits: int = 0
    neutral_hits: int = 0
    raw_text: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "stance": self.stance,
            "lexicon_version": self.lexicon_version,
            "is_empty": self.is_empty,
            "bullish_hits": self.bullish_hits,
            "bearish_hits": self.bearish_hits,
            "neutral_hits": self.neutral_hits,
            "hits": [h.to_dict() for h in self.hits],
        }


# ============================================================================
# Helper Functions
# ============================================================================

def normalize_text_nfkc(text: Optional[str]) -> str:
    """Normalize text using Unicode NFKC for robust character matching."""
    if text is None:
        return ""
    return unicodedata.normalize("NFKC", str(text))


def find_negation_before(
    text: str,
    start_pos: int,
    max_char_distance: int = 3,
) -> Tuple[bool, Optional[str]]:
    """Check if a negation word occurs within max_char_distance characters before start_pos.

    Rule:
    - Distance is measured in characters between end of negation word and start_pos of sentiment word.
    - 0 <= distance <= 3.
    """
    if start_pos <= 0:
        return False, None

    # Window to search backward
    window_start = max(0, start_pos - (max_char_distance + 6))
    prefix_text = text[window_start:start_pos]

    for neg in NEGATION_WORDS:
        idx = prefix_text.rfind(neg)
        if idx != -1:
            neg_end_in_prefix = idx + len(neg)
            distance = len(prefix_text) - neg_end_in_prefix
            if 0 <= distance <= max_char_distance:
                return True, neg

    return False, None


# ============================================================================
# Classifier Core
# ============================================================================

class StanceClassifier:
    """Deterministic Chinese equity stance classifier v1."""

    def __init__(self, lexicon_version: str = LEXICON_VERSION):
        self.lexicon_version = lexicon_version

    def classify(self, text: Optional[str]) -> StanceClassificationResult:
        """Classify a text string into stance and auditable hit details."""
        normalized = normalize_text_nfkc(text)
        stripped = normalized.strip()

        if not stripped:
            return StanceClassificationResult(
                stance="unknown",
                lexicon_version=self.lexicon_version,
                is_empty=True,
                hits=[],
                bullish_hits=0,
                bearish_hits=0,
                neutral_hits=0,
                raw_text=normalized,
            )

        hits: List[StanceHit] = []
        occupied_ranges: List[Tuple[int, int]] = []

        # All keyword candidate lists with original polarities
        # Search longer keywords first
        all_vocab: List[Tuple[str, str]] = (
            [(kw, "bullish") for kw in BULLISH_KEYWORDS]
            + [(kw, "bearish") for kw in BEARISH_KEYWORDS]
            + [(kw, "neutral") for kw in NEUTRAL_KEYWORDS]
        )
        all_vocab.sort(key=lambda x: len(x[0]), reverse=True)

        for kw, polarity in all_vocab:
            start_search = 0
            while True:
                idx = normalized.find(kw, start_search)
                if idx == -1:
                    break

                end_idx = idx + len(kw)
                start_search = idx + 1

                # Check overlap with existing matched spans
                overlaps = any(
                    not (end_idx <= o_start or idx >= o_end)
                    for o_start, o_end in occupied_ranges
                )
                if overlaps:
                    continue

                # Check negation window within 3 characters before keyword
                is_neg, neg_word = find_negation_before(normalized, idx, max_char_distance=3)

                # Determine effective polarity
                if is_neg:
                    if polarity == "bullish":
                        effective_polarity = "bearish"
                    elif polarity == "bearish":
                        effective_polarity = "bullish"
                    else:
                        effective_polarity = "neutral"
                else:
                    effective_polarity = polarity

                hits.append(
                    StanceHit(
                        keyword=kw,
                        start_pos=idx,
                        end_pos=end_idx,
                        original_polarity=polarity,
                        effective_polarity=effective_polarity,
                        is_negated=is_neg,
                        negation_word=neg_word,
                        rule_id="negation_flipped" if is_neg else "lexicon_match",
                    )
                )
                occupied_ranges.append((idx, end_idx))

        # Sort hits chronologically by start position in text
        hits.sort(key=lambda h: (h.start_pos, h.end_pos))

        bullish_cnt = sum(1 for h in hits if h.effective_polarity == "bullish")
        bearish_cnt = sum(1 for h in hits if h.effective_polarity == "bearish")
        neutral_cnt = sum(1 for h in hits if h.effective_polarity == "neutral")

        # Determine overall stance
        if len(hits) == 0:
            stance = "unknown"
        elif bullish_cnt > 0 and bearish_cnt > 0:
            stance = "mixed"
        elif bullish_cnt > 0:
            stance = "bullish"
        elif bearish_cnt > 0:
            stance = "bearish"
        elif neutral_cnt > 0:
            stance = "neutral"
        else:
            stance = "unknown"

        return StanceClassificationResult(
            stance=stance,
            lexicon_version=self.lexicon_version,
            is_empty=False,
            hits=hits,
            bullish_hits=bullish_cnt,
            bearish_hits=bearish_cnt,
            neutral_hits=neutral_cnt,
            raw_text=normalized,
        )


_DEFAULT_CLASSIFIER = StanceClassifier()


def classify_text(text: Optional[str]) -> StanceClassificationResult:
    """Convenience function to classify text with the default classifier."""
    return _DEFAULT_CLASSIFIER.classify(text)
