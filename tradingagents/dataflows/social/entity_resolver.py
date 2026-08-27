"""Deterministic Equity Entity Resolver for Social Media Content (Task 4 / B4).

Specifications:
- docs/social_data/implementation_plan.md §5.3, Task 4
- Confidence rules (§5.3):
  * Full stock code (完整代码): 1.00
  * Standard name (标准名): 1.00
  * Unique alias (唯一别名): 0.95
  * Exclusive keyword without conflict (专属关键词无冲突): 0.90
- Industry / concept terms (行业/概念词): topic only, never bound to individual stocks.
- Multi-stock in one text: mapped separately.
- Full-width / half-width: NFKC normalization.
- No reverse imports from api.main.
- Deterministic, unit-testable with built-in sample dictionary.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from tradingagents.dataflows.social.contracts import EntityMention

DEFAULT_RESOLVER_VERSION = "v1"

# Confidence Levels (§5.3)
CONFIDENCE_CODE: float = 1.00
CONFIDENCE_STANDARD_NAME: float = 1.00
CONFIDENCE_UNIQUE_ALIAS: float = 0.95
CONFIDENCE_EXCLUSIVE_KEYWORD: float = 0.90

# Match Methods (§5.3)
MATCH_METHOD_CODE: str = "code"
MATCH_METHOD_STANDARD_NAME: str = "standard_name"
MATCH_METHOD_UNIQUE_ALIAS: str = "unique_alias"
MATCH_METHOD_EXCLUSIVE_KEYWORD: str = "exclusive_keyword"

# Industry & Concept Topics (§5.3)
# These terms must NOT be bound to individual equities.
INDUSTRY_CONCEPT_TOPICS: Set[str] = {
    "白酒", "半导体", "芯片", "新能源", "光伏", "锂电池", "算力", "人工智能",
    "AI", "低空经济", "医药", "券商", "银行", "军工", "信创", "消费电子",
    "机器人", "固态电池", "华为概念", "苹果概念", "稀土", "煤炭", "有色金属",
    "电力", "大盘", "上证指数", "创业板", "科创板", "沪深300", "中证500",
    "中证1000", "A股", "港股", "美股", "大宗商品", "降息", "央行", "牛市", "熊市",
    "商业航天", "量子科技", "存储芯片", "脑机接口", "智能驾驶", "车路协同",
}


@dataclass
class EquityEntity:
    """Equity entity dictionary definition."""

    symbol: str  # Normalized ticker, e.g. "600519.SH"
    standard_name: str  # Standard name, e.g. "贵州茅台"
    aliases: List[str] = field(default_factory=list)  # Unique aliases, e.g. ["茅台"]
    keywords: List[str] = field(default_factory=list)  # Exclusive keywords, e.g. ["飞天茅台"]


# Built-in sample dictionary covering common A-share benchmark equities
BUILTIN_EQUITY_ENTITIES: List[EquityEntity] = [
    EquityEntity("600519.SH", "贵州茅台", ["茅台"], ["飞天53度", "国酒53度", "汉酱", "茅酒", "飞天茅台"]),
    EquityEntity("300750.SZ", "宁德时代", ["宁王"], ["麒麟电池", "神行电池", "凝聚态电池"]),
    EquityEntity("688256.SH", "寒武纪", ["寒武纪-U", "寒武纪-u", "寒武纪U", "寒武纪u"], ["思元芯片", "思元"]),
    EquityEntity("002594.SZ", "比亚迪", ["迪王"], ["仰望U8", "刀片电池", "易四方"]),
    EquityEntity("601318.SH", "中国平安", ["平安保险"], []),
    EquityEntity("000001.SZ", "平安银行", [], []),
    EquityEntity("000002.SZ", "万科A", ["万科"], []),
    EquityEntity("600030.SH", "中信证券", [], []),
    EquityEntity("000333.SZ", "美的集团", ["美的"], []),
    EquityEntity("600900.SH", "长江电力", ["长电"], []),
    EquityEntity("600276.SH", "恒瑞医药", ["恒瑞"], []),
    EquityEntity("000725.SZ", "京东方A", ["京东方"], []),
    EquityEntity("688981.SH", "中芯国际", ["中芯"], []),
    EquityEntity("002371.SZ", "北方华创", ["华创"], []),
    EquityEntity("601138.SH", "工业富联", ["富士康"], []),
    EquityEntity("300308.SZ", "中际旭创", ["旭创"], []),
    EquityEntity("000977.SZ", "浪潮信息", ["浪潮"], []),
    EquityEntity("603259.SH", "药明康德", ["药明"], []),
    EquityEntity("300760.SZ", "迈瑞医疗", ["迈瑞"], []),
    EquityEntity("300015.SZ", "爱尔眼科", ["爱尔"], []),
    EquityEntity("002475.SZ", "立讯精密", ["立讯"], []),
    EquityEntity("002241.SZ", "歌尔股份", ["歌尔"], []),
    EquityEntity("300433.SZ", "蓝思科技", ["蓝思"], []),
    EquityEntity("688036.SH", "传音控股", ["传音"], []),
    EquityEntity("600584.SH", "长电科技", [], []),
    EquityEntity("603501.SH", "韦尔股份", ["韦尔"], []),
    EquityEntity("688012.SH", "中微公司", ["中微"], []),
    EquityEntity("300122.SZ", "智飞生物", ["智飞"], []),
    EquityEntity("600036.SH", "招商银行", ["招行"], []),
    EquityEntity("601857.SH", "中国石油", ["中石油"], []),
    EquityEntity("601398.SH", "工商银行", ["工行", "中国工商银行"], []),
    EquityEntity("601288.SH", "农业银行", ["农行", "中国农业银行"], []),
    EquityEntity("601939.SH", "建设银行", ["建行", "中国建设银行"], []),
    EquityEntity("601988.SH", "中国银行", ["中行"], []),
    EquityEntity("600000.SH", "浦发银行", ["浦发"], []),
    EquityEntity("601899.SH", "紫金矿业", ["紫金"], []),
    EquityEntity("600028.SH", "中国石化", ["中石化"], []),
    EquityEntity("601088.SH", "中国神华", ["神华"], []),
    EquityEntity("601012.SH", "隆基绿能", ["隆基股份", "隆基"], []),
    EquityEntity("300059.SZ", "东方财富", ["东财"], []),
    EquityEntity("600438.SH", "通威股份", ["通威"], []),
    EquityEntity("002460.SZ", "赣锋锂业", ["赣锋"], []),
    EquityEntity("002466.SZ", "天齐锂业", ["天齐"], []),
    EquityEntity("002415.SZ", "海康威视", ["海康"], []),
    EquityEntity("000651.SZ", "格力电器", ["格力"], []),
    EquityEntity("000858.SZ", "五粮液", [], ["普五"]),
    EquityEntity("000568.SZ", "泸州老窖", ["老窖"], ["国窖1573"]),
    EquityEntity("600809.SH", "山西汾酒", ["汾酒"], ["青花汾酒"]),
    EquityEntity("600887.SH", "伊利股份", ["伊利"], ["金典牛奶"]),
    EquityEntity("600309.SH", "万华化学", ["万华"], []),
    EquityEntity("600690.SH", "海尔智家", ["海尔"], ["卡萨帝"]),
]


# ============================================================================
# Helper Functions
# ============================================================================

def normalize_stock_code(raw: str) -> Optional[str]:
    """Normalize raw stock code string into standard symbol (e.g. 600519.SH).

    Rules:
    - 6-digit pure code:
      * 60xxxx, 68xxxx, 90xxxx, 5xxxxx -> .SH
      * 00xxxx, 30xxxx, 20xxxx, 1xxxxx -> .SZ
      * 82xxxx, 83xxxx, 87xxxx, 43xxxx, 92xxxx -> .BJ
    - With prefix SH/SZ/BJ: SH600519 -> 600519.SH
    - With suffix .SH/.SZ/.BJ/.SS: 600519.SS -> 600519.SH
    - Returns None if invalid or cannot be normalized.
    """
    if not raw:
        return None

    s = unicodedata.normalize("NFKC", str(raw)).strip().upper()
    if not s:
        return None

    # Pattern 1: Suffix notation: 600519.SH, 600519.SZ, 600519.BJ, 600519.SS
    m_suffix = re.match(r"^(\d{6})\.(SH|SZ|BJ|SS)$", s)
    if m_suffix:
        code, ex = m_suffix.group(1), m_suffix.group(2)
        return f"{code}.SH" if ex == "SS" else f"{code}.{ex}"

    # Pattern 2: Prefix notation: SH600519, SZ000001, BJ830000
    m_prefix = re.match(r"^(SH|SZ|BJ)(\d{6})$", s)
    if m_prefix:
        ex, code = m_prefix.group(1), m_prefix.group(2)
        return f"{code}.{ex}"

    # Pattern 3: Pure 6-digit code
    if re.match(r"^\d{6}$", s):
        code = s
        if code.startswith(("60", "68", "90", "50", "51", "56", "58")):
            return f"{code}.SH"
        if code.startswith(("00", "30", "20", "15", "16", "18")):
            return f"{code}.SZ"
        if code.startswith(("82", "83", "87", "43", "92")):
            return f"{code}.BJ"
        # Fallback by leading digit
        if code.startswith(("5", "6", "9")):
            return f"{code}.SH"
        if code.startswith(("0", "1", "2", "3")):
            return f"{code}.SZ"
        if code.startswith(("4", "8")):
            return f"{code}.BJ"
        return f"{code}.SH"

    return None


def extract_topics(text: Optional[str]) -> List[str]:
    """Extract industry and concept topic keywords from text (topic only, not equity)."""
    if not text:
        return []
    normalized_text = unicodedata.normalize("NFKC", text)
    matched_topics: List[str] = []
    for topic in sorted(INDUSTRY_CONCEPT_TOPICS, key=len, reverse=True):
        if topic in normalized_text:
            matched_topics.append(topic)
    return matched_topics


# ============================================================================
# Entity Resolver Class
# ============================================================================

class EntityResolver:
    """Deterministic A-share stock entity resolver for social media texts."""

    def __init__(
        self,
        resolver_version: str = DEFAULT_RESOLVER_VERSION,
        custom_entities: Optional[List[EquityEntity]] = None,
    ) -> None:
        self.resolver_version = resolver_version

        # Build entity dictionaries
        all_entities = list(BUILTIN_EQUITY_ENTITIES)
        if custom_entities:
            all_entities.extend(custom_entities)

        self.entities_by_symbol: Dict[str, EquityEntity] = {}
        self.standard_name_map: Dict[str, str] = {}  # name -> symbol
        self.alias_map: Dict[str, str] = {}  # alias -> symbol
        self.keyword_map: Dict[str, str] = {}  # keyword -> symbol

        # Temporary structures to check keyword conflicts
        keyword_counts: Dict[str, Set[str]] = {}

        for entity in all_entities:
            norm_symbol = normalize_stock_code(entity.symbol) or entity.symbol.upper()
            self.entities_by_symbol[norm_symbol] = entity

            # Standard name
            name_norm = unicodedata.normalize("NFKC", entity.standard_name).strip()
            if name_norm:
                self.standard_name_map[name_norm] = norm_symbol

            # Aliases
            for alias in entity.aliases:
                alias_norm = unicodedata.normalize("NFKC", alias).strip()
                if alias_norm:
                    self.alias_map[alias_norm] = norm_symbol

            # Keywords (track conflicts)
            for kw in entity.keywords:
                kw_norm = unicodedata.normalize("NFKC", kw).strip()
                if kw_norm:
                    keyword_counts.setdefault(kw_norm, set()).add(norm_symbol)

        # Only retain non-conflicting keywords (§5.3: 专属关键词无冲突 0.90)
        for kw, symbols in keyword_counts.items():
            if len(symbols) == 1:
                self.keyword_map[kw] = next(iter(symbols))

        # Precompile code regex patterns
        # 1) Full explicit ticker: e.g. 600519.SH, 000001.SZ, 830000.BJ, 600519.SS
        self._re_explicit_suffix = re.compile(
            r"(?<![0-9A-Za-z])(\d{6})\.(SH|SZ|BJ|SS)(?![0-9A-Za-z])",
            re.IGNORECASE,
        )
        # 2) Prefix ticker: e.g. SH600519, SZ000001, BJ830000
        self._re_prefix_code = re.compile(
            r"(?<![0-9A-Za-z])(SH|SZ|BJ)(\d{6})(?![0-9A-Za-z])",
            re.IGNORECASE,
        )
        # 3) Pure 6-digit A-share code with context boundary
        # A-share codes start with 60, 68, 90, 00, 30, 20, 82, 83, 87, 43, 92, 50, 51, 56, 58, 15, 16, 18
        self._re_pure_code = re.compile(
            r"(?<![\d\.\-\+])(60\d{4}|68\d{4}|90\d{4}|00\d{4}|30\d{4}|20\d{4}|82\d{4}|83\d{4}|87\d{4}|43\d{4}|92\d{4}|50\d{4}|51\d{4}|56\d{4}|58\d{4}|15\d{4}|16\d{4}|18\d{4})(?![\d\.\%])"
        )

    def resolve(
        self,
        text: Optional[str] = "",
        title: Optional[str] = None,
        source_keyword: Optional[str] = None,
    ) -> List[EntityMention]:
        """Resolve stock entity mentions from text, title, and/or source_keyword.

        Returns a deduplicated list of EntityMention instances (highest confidence per symbol).
        """
        combined_parts: List[str] = []
        if title:
            combined_parts.append(str(title))
        if text:
            combined_parts.append(str(text))
        if source_keyword:
            combined_parts.append(str(source_keyword))

        if not combined_parts:
            return []

        raw_content = "\n".join(combined_parts)
        # NFKC normalization (§5.3)
        normalized_content = unicodedata.normalize("NFKC", raw_content)

        # Dictionary to store the best mention per symbol: symbol -> EntityMention
        best_mentions: Dict[str, EntityMention] = {}

        # --------------------------------------------------------------------
        # 1. Match Explicit Stock Codes (Confidence 1.00)
        # --------------------------------------------------------------------
        # Suffix code matches (e.g. 600519.SH)
        for m in self._re_explicit_suffix.finditer(normalized_content):
            matched_str = m.group(0)
            norm_symbol = normalize_stock_code(matched_str)
            if norm_symbol:
                self._update_best_mention(
                    best_mentions,
                    symbol=norm_symbol,
                    matched_text=matched_str,
                    match_method=MATCH_METHOD_CODE,
                    confidence=CONFIDENCE_CODE,
                )

        # Prefix code matches (e.g. SH600519)
        for m in self._re_prefix_code.finditer(normalized_content):
            matched_str = m.group(0)
            norm_symbol = normalize_stock_code(matched_str)
            if norm_symbol:
                self._update_best_mention(
                    best_mentions,
                    symbol=norm_symbol,
                    matched_text=matched_str,
                    match_method=MATCH_METHOD_CODE,
                    confidence=CONFIDENCE_CODE,
                )

        # Pure 6-digit code matches (e.g. 600519)
        for m in self._re_pure_code.finditer(normalized_content):
            code_str = m.group(1)
            # Guard against timestamps e.g. 20260826 (handled by regex start digits)
            norm_symbol = normalize_stock_code(code_str)
            if norm_symbol:
                self._update_best_mention(
                    best_mentions,
                    symbol=norm_symbol,
                    matched_text=code_str,
                    match_method=MATCH_METHOD_CODE,
                    confidence=CONFIDENCE_CODE,
                )

        # --------------------------------------------------------------------
        # 2. Match Standard Names (Confidence 1.00)
        # --------------------------------------------------------------------
        # Sort names longest first for greedy matching
        for name in sorted(self.standard_name_map.keys(), key=len, reverse=True):
            if name in normalized_content:
                norm_symbol = self.standard_name_map[name]
                self._update_best_mention(
                    best_mentions,
                    symbol=norm_symbol,
                    matched_text=name,
                    match_method=MATCH_METHOD_STANDARD_NAME,
                    confidence=CONFIDENCE_STANDARD_NAME,
                )

        # --------------------------------------------------------------------
        # 3. Match Unique Aliases (Confidence 0.95)
        # --------------------------------------------------------------------
        for alias in sorted(self.alias_map.keys(), key=len, reverse=True):
            if alias in normalized_content:
                norm_symbol = self.alias_map[alias]
                self._update_best_mention(
                    best_mentions,
                    symbol=norm_symbol,
                    matched_text=alias,
                    match_method=MATCH_METHOD_UNIQUE_ALIAS,
                    confidence=CONFIDENCE_UNIQUE_ALIAS,
                )

        # --------------------------------------------------------------------
        # 4. Match Exclusive Keywords (Confidence 0.90)
        # --------------------------------------------------------------------
        for kw in sorted(self.keyword_map.keys(), key=len, reverse=True):
            if kw in normalized_content:
                norm_symbol = self.keyword_map[kw]
                self._update_best_mention(
                    best_mentions,
                    symbol=norm_symbol,
                    matched_text=kw,
                    match_method=MATCH_METHOD_EXCLUSIVE_KEYWORD,
                    confidence=CONFIDENCE_EXCLUSIVE_KEYWORD,
                )

        # Return deterministically sorted by symbol
        return sorted(best_mentions.values(), key=lambda m: m.symbol)

    def _update_best_mention(
        self,
        mentions_dict: Dict[str, EntityMention],
        symbol: str,
        matched_text: str,
        match_method: str,
        confidence: float,
    ) -> None:
        """Update mention if new mention has higher confidence or better precedence."""
        if symbol not in mentions_dict:
            mentions_dict[symbol] = EntityMention(
                symbol=symbol,
                matched_text=matched_text,
                match_method=match_method,
                confidence=confidence,
                resolver_version=self.resolver_version,
            )
            return

        existing = mentions_dict[symbol]
        # Compare confidence first
        if confidence > existing.confidence:
            mentions_dict[symbol] = EntityMention(
                symbol=symbol,
                matched_text=matched_text,
                match_method=match_method,
                confidence=confidence,
                resolver_version=self.resolver_version,
            )
        elif abs(confidence - existing.confidence) < 1e-6:
            # If tied confidence, prefer longer matched text or higher method priority
            # Method priority: code > standard_name > unique_alias > exclusive_keyword
            method_priority = {
                MATCH_METHOD_CODE: 4,
                MATCH_METHOD_STANDARD_NAME: 3,
                MATCH_METHOD_UNIQUE_ALIAS: 2,
                MATCH_METHOD_EXCLUSIVE_KEYWORD: 1,
            }
            if len(matched_text) > len(existing.matched_text) or method_priority.get(match_method, 0) > method_priority.get(existing.match_method, 0):
                mentions_dict[symbol] = EntityMention(
                    symbol=symbol,
                    matched_text=matched_text,
                    match_method=match_method,
                    confidence=confidence,
                    resolver_version=self.resolver_version,
                )


# ============================================================================
# Module-level Convenience Function
# ============================================================================

_default_resolver = EntityResolver()


def resolve_entities(
    text: Optional[str] = "",
    title: Optional[str] = None,
    source_keyword: Optional[str] = None,
    resolver: Optional[EntityResolver] = None,
) -> List[EntityMention]:
    """Convenience function to resolve stock entity mentions."""
    active_resolver = resolver or _default_resolver
    return active_resolver.resolve(text=text, title=title, source_keyword=source_keyword)
