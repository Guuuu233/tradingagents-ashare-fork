"""Unit tests for deterministic equity entity resolver (Task 4 / B4).

Specifications:
- docs/social_data/implementation_plan.md §5.3, Task 4
- Confidence rules:
  * Full code (完整代码): 1.00
  * Standard name (标准名): 1.00
  * Unique alias (唯一别名): 0.95
  * Exclusive keyword (专属关键词无冲突): 0.90
- Industry / concept words (行业/概念词): topic only, never bound to individual stocks.
- Multi-stock in one text: mapped separately.
- Full-width / half-width: NFKC normalization.
- No reverse imports from api.main.
- Deterministic, unit-testable with built-in sample dictionary.
"""

import sqlite3
import pytest

from tradingagents.dataflows.social.contracts import EntityMention
from tradingagents.dataflows.social.entity_resolver import (
    DEFAULT_RESOLVER_VERSION,
    CONFIDENCE_CODE,
    CONFIDENCE_STANDARD_NAME,
    CONFIDENCE_UNIQUE_ALIAS,
    CONFIDENCE_EXCLUSIVE_KEYWORD,
    MATCH_METHOD_CODE,
    MATCH_METHOD_STANDARD_NAME,
    MATCH_METHOD_UNIQUE_ALIAS,
    MATCH_METHOD_EXCLUSIVE_KEYWORD,
    EntityResolver,
    EquityEntity,
    normalize_stock_code,
    resolve_entities,
    extract_topics,
    INDUSTRY_CONCEPT_TOPICS,
)
from tradingagents.dataflows.social.archive_schema import init_archive_db
from tradingagents.dataflows.social.mediacrawler_importer import MediaCrawlerImporter
from tests.social_fixtures import init_mediacrawler_db, populate_sample_mediacrawler_data


# ============================================================================
# 1. Stock Code Normalization Tests
# ============================================================================

def test_normalize_stock_code_valid():
    """Test standard A-share code normalization to suffix format (.SH, .SZ, .BJ)."""
    assert normalize_stock_code("600519") == "600519.SH"
    assert normalize_stock_code("688256") == "688256.SH"
    assert normalize_stock_code("000001") == "000001.SZ"
    assert normalize_stock_code("002594") == "002594.SZ"
    assert normalize_stock_code("300750") == "300750.SZ"
    assert normalize_stock_code("830000") == "830000.BJ"
    assert normalize_stock_code("430000") == "430000.BJ"
    assert normalize_stock_code("920000") == "920000.BJ"

    # Already with suffix (case insensitive)
    assert normalize_stock_code("600519.sh") == "600519.SH"
    assert normalize_stock_code("000001.SZ") == "000001.SZ"
    assert normalize_stock_code("830000.bj") == "830000.BJ"
    assert normalize_stock_code("600519.SS") == "600519.SH"

    # Prefix format (SH600519, sz000001, bj830000)
    assert normalize_stock_code("SH600519") == "600519.SH"
    assert normalize_stock_code("sz000001") == "000001.SZ"
    assert normalize_stock_code("BJ830000") == "830000.BJ"


def test_normalize_stock_code_invalid():
    """Invalid code patterns should return None."""
    assert normalize_stock_code("") is None
    assert normalize_stock_code("12345") is None
    assert normalize_stock_code("1234567") is None
    assert normalize_stock_code("ABCDEF") is None
    assert normalize_stock_code("20260826") is None


# ============================================================================
# 2. Full Code Matching (Confidence 1.00)
# ============================================================================

def test_resolve_full_code_matches():
    """§5.3: 完整代码置信度 1.00."""
    resolver = EntityResolver()

    # 6-digit code in text
    res = resolver.resolve("今日看好 600519 表现")
    assert len(res) == 1
    assert res[0].symbol == "600519.SH"
    assert res[0].matched_text == "600519"
    assert res[0].match_method == MATCH_METHOD_CODE
    assert res[0].confidence == pytest.approx(CONFIDENCE_CODE)
    assert res[0].resolver_version == DEFAULT_RESOLVER_VERSION

    # Code with suffix in text
    res2 = resolver.resolve("关注 300750.SZ 的走势")
    assert len(res2) == 1
    assert res2[0].symbol == "300750.SZ"
    assert res2[0].confidence == pytest.approx(1.00)
    assert res2[0].match_method == MATCH_METHOD_CODE

    # Code with prefix
    res3 = resolver.resolve("SH688256 今日有大宗交易")
    assert len(res3) == 1
    assert res3[0].symbol == "688256.SH"
    assert res3[0].confidence == pytest.approx(1.00)


def test_resolve_code_false_positive_guard():
    """Ensure timestamps, prices, and long numbers are not falsely parsed as stock codes."""
    resolver = EntityResolver()

    # Timestamp / date string should not match
    assert resolver.resolve("日期 20260826 发送通知") == []
    # Long numbers
    assert resolver.resolve("用户ID 1234567890 注册成功") == []
    # Decimal / price
    assert resolver.resolve("成交量 123456.78 万元") == []
    # Negative number
    assert resolver.resolve("净利润 -600519 元") == []


# ============================================================================
# 3. Standard Name Matching (Confidence 1.00)
# ============================================================================

def test_resolve_standard_name_matches():
    """§5.3: 标准名 1.00."""
    resolver = EntityResolver()

    res = resolver.resolve("贵州茅台今日发布最新财报")
    assert len(res) == 1
    assert res[0].symbol == "600519.SH"
    assert res[0].matched_text == "贵州茅台"
    assert res[0].match_method == MATCH_METHOD_STANDARD_NAME
    assert res[0].confidence == pytest.approx(CONFIDENCE_STANDARD_NAME)

    res_catl = resolver.resolve("宁德时代储能业务持续高增长")
    assert len(res_catl) == 1
    assert res_catl[0].symbol == "300750.SZ"
    assert res_catl[0].matched_text == "宁德时代"
    assert res_catl[0].match_method == MATCH_METHOD_STANDARD_NAME
    assert res_catl[0].confidence == pytest.approx(1.00)

    res_camb = resolver.resolve("寒武纪新一代训练芯片发布")
    assert len(res_camb) == 1
    assert res_camb[0].symbol == "688256.SH"
    assert res_camb[0].matched_text == "寒武纪"
    assert res_camb[0].match_method == MATCH_METHOD_STANDARD_NAME
    assert res_camb[0].confidence == pytest.approx(1.00)


# ============================================================================
# 4. Unique Alias Matching (Confidence 0.95)
# ============================================================================

def test_resolve_unique_alias_matches():
    """§5.3: 唯一别名 0.95."""
    resolver = EntityResolver()

    res = resolver.resolve("茅台重返2000元大关")
    assert len(res) == 1
    assert res[0].symbol == "600519.SH"
    assert res[0].matched_text == "茅台"
    assert res[0].match_method == MATCH_METHOD_UNIQUE_ALIAS
    assert res[0].confidence == pytest.approx(CONFIDENCE_UNIQUE_ALIAS)

    res_ning = resolver.resolve("宁王大涨带动创业板走强")
    assert len(res_ning) == 1
    assert res_ning[0].symbol == "300750.SZ"
    assert res_ning[0].matched_text == "宁王"
    assert res_ning[0].match_method == MATCH_METHOD_UNIQUE_ALIAS
    assert res_ning[0].confidence == pytest.approx(0.95)

    res_di = resolver.resolve("迪王海外销量再创新高")
    assert len(res_di) == 1
    assert res_di[0].symbol == "002594.SZ"
    assert res_di[0].matched_text == "迪王"
    assert res_di[0].match_method == MATCH_METHOD_UNIQUE_ALIAS
    assert res_di[0].confidence == pytest.approx(0.95)


# ============================================================================
# 5. Exclusive Keyword Matching (Confidence 0.90)
# ============================================================================

def test_resolve_exclusive_keyword_matches():
    """§5.3: 专属关键词无冲突 0.90."""
    resolver = EntityResolver()

    res = resolver.resolve("飞天53度批价回升")
    assert len(res) == 1
    assert res[0].symbol == "600519.SH"
    assert res[0].matched_text == "飞天53度"
    assert res[0].match_method == MATCH_METHOD_EXCLUSIVE_KEYWORD
    assert res[0].confidence == pytest.approx(CONFIDENCE_EXCLUSIVE_KEYWORD)

    res_battery = resolver.resolve("麒麟电池实现全量产交付")
    assert len(res_battery) == 1
    assert res_battery[0].symbol == "300750.SZ"
    assert res_battery[0].matched_text == "麒麟电池"
    assert res_battery[0].match_method == MATCH_METHOD_EXCLUSIVE_KEYWORD
    assert res_battery[0].confidence == pytest.approx(0.90)

    res_chip = resolver.resolve("思元芯片生态适配完成")
    assert len(res_chip) == 1
    assert res_chip[0].symbol == "688256.SH"
    assert res_chip[0].matched_text == "思元芯片"
    assert res_chip[0].match_method == MATCH_METHOD_EXCLUSIVE_KEYWORD
    assert res_chip[0].confidence == pytest.approx(0.90)


# ============================================================================
# 6. Industry / Concept Words Only Tag Topic, NOT Bound to Stocks (§5.3)
# ============================================================================

def test_industry_and_concept_words_not_bound_to_equities():
    """§5.3: 行业/概念词只打 topic，不绑个股."""
    resolver = EntityResolver()

    # Pure industry / concept texts
    assert resolver.resolve("今日白酒板块表现强劲") == []
    assert resolver.resolve("半导体芯片全线反弹") == []
    assert resolver.resolve("新能源与光伏概念分化") == []
    assert resolver.resolve("算力与人工智能是未来主线") == []
    assert resolver.resolve("券商银行带领大盘冲关") == []

    # Check topic extraction works independently
    topics = extract_topics("今日白酒板块表现强劲，半导体芯片全线反弹，新能源走弱")
    assert "白酒" in topics
    assert "半导体" in topics or "芯片" in topics
    assert "新能源" in topics


def test_mixed_topic_and_equity_resolution():
    """When both topic words and specific equity names appear, only equity is returned as stock mention."""
    resolver = EntityResolver()

    res = resolver.resolve("虽然白酒板块震荡，但贵州茅台依然领涨，同时关注五粮液")
    symbols = {m.symbol for m in res}
    assert symbols == {"600519.SH", "000858.SZ"}
    for m in res:
        assert m.confidence in (1.00, 0.95, 0.90)
        assert m.symbol != "白酒"


# ============================================================================
# 7. Multi-Stock in One Text (多股票同文分别映射)
# ============================================================================

def test_resolve_multiple_stocks_in_same_text():
    """§5.3: 多股票同文分别映射."""
    resolver = EntityResolver()

    text = "今天加仓了贵州茅台和宁德时代，清仓了比亚迪，顺便看了看寒武纪"
    res = resolver.resolve(text)

    # Should detect 4 distinct stocks
    assert len(res) == 4
    symbols = {m.symbol for m in res}
    assert symbols == {"600519.SH", "300750.SZ", "002594.SZ", "688256.SH"}


def test_resolve_multiple_stocks_different_match_methods():
    """Multi-stock text with mixed code, standard name, alias, and keyword."""
    resolver = EntityResolver()

    text = "买了 600519，重仓了宁王，配置了思元芯片"
    res = resolver.resolve(text)

    res_map = {m.symbol: m for m in res}
    assert "600519.SH" in res_map
    assert res_map["600519.SH"].match_method == MATCH_METHOD_CODE
    assert res_map["600519.SH"].confidence == pytest.approx(1.00)

    assert "300750.SZ" in res_map
    assert res_map["300750.SZ"].match_method == MATCH_METHOD_UNIQUE_ALIAS
    assert res_map["300750.SZ"].confidence == pytest.approx(0.95)

    assert "688256.SH" in res_map
    assert res_map["688256.SH"].match_method == MATCH_METHOD_EXCLUSIVE_KEYWORD
    assert res_map["688256.SH"].confidence == pytest.approx(0.90)


# ============================================================================
# 8. Full-Width / Half-Width NFKC Normalization (全角/半角 NFKC 归一)
# ============================================================================

def test_resolve_nfkc_normalization():
    """§5.3: 全角/半角 NFKC 归一."""
    resolver = EntityResolver()

    # Full-width digits and letters: ６００５１９．ＳＨ
    res_fw = resolver.resolve("强烈推荐 ６００５１９．ＳＨ 这只股票")
    assert len(res_fw) == 1
    assert res_fw[0].symbol == "600519.SH"
    assert res_fw[0].confidence == pytest.approx(1.00)

    # Full-width 6-digit code: ３００７５０
    res_fw2 = resolver.resolve("关注 ３００７５０ 宁德时代")
    assert len(res_fw2) == 1
    assert res_fw2[0].symbol == "300750.SZ"

    # Full-width spaces and brackets: （贵州茅台）
    res_fw3 = resolver.resolve("【６８８２５６】　寒武纪－Ｕ")
    assert len(res_fw3) == 1
    assert res_fw3[0].symbol == "688256.SH"


# ============================================================================
# 9. Deduplication and Precedence within Same Text
# ============================================================================

def test_deduplication_prefers_higher_confidence():
    """If the same stock is matched via multiple methods in one text, keep highest confidence."""
    resolver = EntityResolver()

    # Text mentions both code (1.00), standard name (1.00), alias (0.95), keyword (0.90) for 600519.SH
    text = "贵州茅台 600519 茅台 飞天茅台"
    res = resolver.resolve(text)

    # Should only return one EntityMention for 600519.SH
    assert len(res) == 1
    assert res[0].symbol == "600519.SH"
    assert res[0].confidence == pytest.approx(1.00)
    assert res[0].match_method in (MATCH_METHOD_CODE, MATCH_METHOD_STANDARD_NAME)


# ============================================================================
# 10. Custom Dictionary Extension
# ============================================================================

def test_custom_dictionary_extension():
    """Test injecting custom equity entities into EntityResolver."""
    custom_stock = EquityEntity(
        symbol="688001.SH",
        standard_name="华兴源创",
        aliases=["华兴"],
        keywords=["平板检测龙头"],
    )
    resolver = EntityResolver(custom_entities=[custom_stock])

    res = resolver.resolve("关注华兴源创最新动向")
    assert len(res) == 1
    assert res[0].symbol == "688001.SH"
    assert res[0].matched_text == "华兴源创"
    assert res[0].confidence == pytest.approx(1.00)

    res_alias = resolver.resolve("华兴走势稳健")
    assert len(res_alias) == 1
    assert res_alias[0].symbol == "688001.SH"
    assert res_alias[0].confidence == pytest.approx(0.95)


# ============================================================================
# 11. Importer Integration with Entity Resolver
# ============================================================================

def test_importer_writes_social_entity_mentions():
    """Test that MediaCrawlerImporter automatically writes detected mentions into social_entity_mentions."""
    # 1. Initialize archive and source db
    archive_conn = init_archive_db(":memory:")
    source_conn = init_mediacrawler_db(":memory:")
    populate_sample_mediacrawler_data(source_conn)

    # 2. Run importer
    importer = MediaCrawlerImporter(archive_conn=archive_conn)
    result = importer.import_records(source_db=source_conn, platform="all")
    assert result["status"] == "completed"
    assert result["rows_inserted"] > 0

    # 3. Query social_entity_mentions
    cursor = archive_conn.cursor()
    cursor.execute("SELECT snapshot_id, symbol, matched_text, match_method, confidence, resolver_version FROM social_entity_mentions")
    mentions = cursor.fetchall()

    assert len(mentions) > 0

    # The sample fixture contains 寒武纪 (688256.SH)
    camb_mentions = [m for m in mentions if m[1] == "688256.SH"]
    assert len(camb_mentions) > 0
    assert camb_mentions[0][4] in (1.00, 0.95, 0.90)  # confidence
    assert camb_mentions[0][5] == DEFAULT_RESOLVER_VERSION


def test_importer_entity_mentions_idempotency():
    """Repeated imports must not violate primary key on social_entity_mentions or corrupt rows."""
    archive_conn = init_archive_db(":memory:")
    source_conn = init_mediacrawler_db(":memory:")
    populate_sample_mediacrawler_data(source_conn)

    importer = MediaCrawlerImporter(archive_conn=archive_conn)
    importer.import_records(source_db=source_conn, platform="all")

    cursor = archive_conn.cursor()
    cursor.execute("SELECT count(*) FROM social_entity_mentions")
    count1 = cursor.fetchone()[0]

    # Second import with identical source data
    importer.import_records(source_db=source_conn, platform="all")
    cursor.execute("SELECT count(*) FROM social_entity_mentions")
    count2 = cursor.fetchone()[0]

    assert count1 == count2


# ============================================================================
# 12. Punctuation, Hashtag, Cashtag and Formatting Robustness
# ============================================================================

def test_resolve_with_complex_formatting_and_tags():
    """Test resolution with social media formats (hashtags, cashtags, brackets, emojis)."""
    resolver = EntityResolver()

    # Cashtag format: $贵州茅台(SH600519)$ $宁德时代(SZ300750)$
    text1 = "$贵州茅台(SH600519)$ 今日大涨 🚀🚀 另外关注 $宁德时代(SZ300750)$"
    res1 = resolver.resolve(text1)
    symbols1 = {m.symbol for m in res1}
    assert symbols1 == {"600519.SH", "300750.SZ"}

    # Hashtags and quotes: #寒武纪#【688256】
    text2 = "#寒武纪# 发布新品，代码【688256】"
    res2 = resolver.resolve(text2)
    assert len(res2) == 1
    assert res2[0].symbol == "688256.SH"
    assert res2[0].confidence == pytest.approx(1.00)

    # Empty inputs and whitespace
    assert resolver.resolve(None) == []
    assert resolver.resolve("") == []
    assert resolver.resolve("   \n\t  ") == []
    assert extract_topics(None) == []
    assert extract_topics("") == []


def test_resolve_combined_title_text_keyword():
    """Test resolution combining title, text body, and source_keyword."""
    resolver = EntityResolver()

    # Title mentions one, text mentions another, keyword is a third
    res = resolver.resolve(
        title="关于贵州茅台的研报点评",
        text="文章主要分析了宁德时代的产业链优势",
        source_keyword="寒武纪",
    )
    symbols = {m.symbol for m in res}
    assert symbols == {"600519.SH", "300750.SZ", "688256.SH"}
    assert len(res) == 3

