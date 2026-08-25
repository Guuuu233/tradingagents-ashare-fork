"""P3-H2.1 Target Pool Selection, Shenwan Industry Rotation, SHA256 Fingerprints & Dynamic Re-balancing.

Core module responsible for:
1. Automated weekly candidate selection (8~10 fresh A-share stocks);
2. Shenwan Level-1 primary industry rotation (covering >= 5 core sectors: Electronics,
   Pharma, Consumer, New Energy, Machinery, Cyclicals, Financials, Utilities, etc.);
3. Market cap (>= 10B RMB) and liquidity ADV (>= 100M RMB) admission filters;
4. Strict deduplication against historical benchmark blacklist (e.g. 000333.SZ, 600900.SH,
   600276.SH, 000725.SZ) and deterministic SHA256(symbol + trade_date + protocol_version) fingerprints;
5. P0 Dynamic Pool Re-balancing: reading historical cumulative bull/bear sample counts,
   and dynamically re-weighting contrary/divergence industries when bull/bear ratio deviates
   from 50% by more than +-5%, stabilizing the ratio within [40%, 60%];
6. Strict constraint validation (unique industries >= 5, max single symbol share <= 15%).

Pure functions, deterministic, zero network dependencies, business code 0-intrusion.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import logging
import math
import os
import re
from typing import Any, Dict, List, Literal, Mapping, Optional, Sequence, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tradingagents.agents.utils.agent_states import (
    PROTOCOL_VERSION_V1_LEGACY,
    PROTOCOL_VERSION_V2_STRUCTURED,
)

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# 1. Historical Benchmark Blacklist & Core Constants
# ══════════════════════════════════════════════════════════════════════════════

# Explicit historical benchmark fixtures that MUST be excluded to prevent data contamination
HISTORICAL_BENCHMARK_BLACKLIST: Set[str] = {
    "000333.SZ",  # 美的集团 (Golden Benchmark Case)
    "000333",
    "600900.SH",  # 长江电力 (Golden Benchmark Case)
    "600900",
    "600276.SH",  # 恒瑞医药 (Golden Benchmark Case)
    "600276",
    "000725.SZ",  # 京东方A (Historical Case)
    "000725",
}

# Standard admission thresholds
DEFAULT_MIN_MARKET_CAP_BIL: float = 10.0  # >= 10.0 Billion RMB (100亿市值准入门槛)
DEFAULT_MIN_ADV_MIL: float = 100.0  # >= 100.0 Million RMB (1亿元日均成交额准入门槛)
DEFAULT_MAX_SINGLE_SYMBOL_SHARE: float = 0.15  # <= 15.0% 单标的最大样本占比
DEFAULT_MIN_UNIQUE_INDUSTRIES: int = 5  # >= 5 申万一级行业覆盖

# Dynamic re-balancing thresholds
REBALANCE_PARITY_TARGET: float = 0.50  # 50.0% 均衡目标基准
REBALANCE_TOLERANCE_BAND: float = 0.05  # +-5.0% 偏离容差带 (45.0% ~ 55.0%)
REBALANCE_MIN_RATIO: float = 0.40  # 40.0% 目标区间下限
REBALANCE_MAX_RATIO: float = 0.60  # 60.0% 目标区间上限

# ══════════════════════════════════════════════════════════════════════════════
# 2. Shenwan Primary Industry Classification & Industry Clusters
# ══════════════════════════════════════════════════════════════════════════════

SHENWAN_PRIMARY_INDUSTRIES: List[str] = [
    "电子",
    "医药生物",
    "食品饮料",
    "电力设备",
    "机械设备",
    "有色金属",
    "石油石化",
    "基础化工",
    "煤炭",
    "汽车",
    "家用电器",
    "非银金融",
    "银行",
    "公用事业",
    "交通运输",
    "通信",
    "计算机",
    "国防军工",
    "农林牧渔",
    "建筑装饰",
    "钢铁",
    "房地产",
    "商贸零售",
    "社会服务",
    "传媒",
    "轻工制造",
    "环保",
    "美容护理",
    "纺织服饰",
    "建筑材料",
]

# Industry cluster classifications and characteristic stance tilts
INDUSTRY_CLUSTERS: Dict[str, Dict[str, Any]] = {
    "TMT_GROWTH": {
        "name": "科技成长 (TMT)",
        "industries": ["电子", "计算机", "通信", "传媒"],
        "default_tilt": "bull_tilt",  # High beta, tech innovation momentum
        "beta": 1.25,
        "divergence_score": 0.70,
    },
    "HEALTHCARE": {
        "name": "医药健康 (Healthcare)",
        "industries": ["医药生物", "美容护理"],
        "default_tilt": "divergence",  # Policy vs innovation structural divergence
        "beta": 0.95,
        "divergence_score": 0.85,
    },
    "CONSUMER": {
        "name": "核心大消费 (Consumer)",
        "industries": ["食品饮料", "家用电器", "商贸零售", "社会服务", "纺织服饰", "轻工制造"],
        "default_tilt": "neutral",  # Stable demand, valuation sensitive
        "beta": 0.85,
        "divergence_score": 0.60,
    },
    "NEW_ENERGY_AUTO": {
        "name": "高端制造与新能源 (New Energy & Auto)",
        "industries": ["电力设备", "汽车", "环保"],
        "default_tilt": "bull_tilt",  # High growth, capacity cycle divergence
        "beta": 1.20,
        "divergence_score": 0.75,
    },
    "MACHINERY_ADVANCED": {
        "name": "机械设备与工业母机 (Machinery)",
        "industries": ["机械设备", "国防军工", "建筑装饰", "建筑材料"],
        "default_tilt": "divergence",  # Capex cycle, export vs domestic demand
        "beta": 1.05,
        "divergence_score": 0.65,
    },
    "CYCLICAL_COMMODITIES": {
        "name": "周期大宗与资源 (Cyclicals)",
        "industries": ["有色金属", "石油石化", "基础化工", "煤炭", "钢铁"],
        "default_tilt": "bear_tilt",  # Commodity price cycles, macro down-cycle exposure
        "beta": 1.10,
        "divergence_score": 0.80,
    },
    "FINANCIALS_REAL_ESTATE": {
        "name": "金融与地产 (Financials)",
        "industries": ["银行", "非银金融", "房地产"],
        "default_tilt": "bear_tilt",  # Low PB, macro credit & interest rate sensitivity
        "beta": 0.75,
        "divergence_score": 0.65,
    },
    "DEFENSIVE_UTILITIES": {
        "name": "防御公用与交通 (Defensive & Utilities)",
        "industries": ["公用事业", "交通运输", "农林牧渔"],
        "default_tilt": "defensive",  # High dividend, low beta, contrarian bear hedge
        "beta": 0.55,
        "divergence_score": 0.50,
    },
}

# ══════════════════════════════════════════════════════════════════════════════
# 3. High-Quality Candidate Stock Universe (涵盖主流申万行业代表性标的)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class StockCandidate:
    """Represents a qualified stock candidate in the selection universe."""

    symbol: str
    name: str
    industry: str
    cluster: str
    market_cap_bil: float  # Total Market Cap in Billion CNY
    adv_mil: float  # 20-day Average Daily Trading Volume in Million CNY
    stance_tendency: Literal["bull_tilt", "bear_tilt", "neutral", "divergence", "defensive"]
    base_price: float
    description: str = ""


CANDIDATE_STOCK_UNIVERSE: List[StockCandidate] = [
    # ── 1. 电子 / TMT ────────────────────────────────────────────────────────
    StockCandidate("688981.SH", "中芯国际", "电子", "TMT_GROWTH", 395.0, 3200.0, "bull_tilt", 52.3, "晶圆代工龙头"),
    StockCandidate("002371.SZ", "北方华创", "电子", "TMT_GROWTH", 185.0, 1850.0, "bull_tilt", 345.0, "半导体前道设备龙头"),
    StockCandidate("688012.SH", "中微公司", "电子", "TMT_GROWTH", 96.0, 980.0, "bull_tilt", 155.0, "刻蚀设备龙头"),
    StockCandidate("603501.SH", "韦尔股份", "电子", "TMT_GROWTH", 118.0, 1420.0, "divergence", 98.5, "CIS图像传感器与模拟芯片"),
    StockCandidate("300308.SZ", "中际旭创", "通信", "TMT_GROWTH", 132.0, 2800.0, "bull_tilt", 125.0, "800G/1.6T高速光模块龙头"),
    StockCandidate("601138.SH", "工业富联", "计算机", "TMT_GROWTH", 460.0, 3100.0, "bull_tilt", 23.5, "AI服务器与算力硬件制造龙头"),
    StockCandidate("002475.SZ", "立讯精密", "电子", "TMT_GROWTH", 278.0, 2200.0, "neutral", 38.6, "消费电子与汽车精密制造龙头"),
    StockCandidate("002241.SZ", "歌尔股份", "电子", "TMT_GROWTH", 72.0, 1150.0, "divergence", 21.2, "声学及VR/AR智能硬件"),
    StockCandidate("300433.SZ", "蓝思科技", "电子", "TMT_GROWTH", 85.0, 890.0, "neutral", 17.1, "外观防护玻璃与结构件"),
    StockCandidate("688036.SH", "传音控股", "电子", "TMT_GROWTH", 88.0, 760.0, "divergence", 78.4, "新兴市场智能手机龙头"),
    StockCandidate("000977.SZ", "浪潮信息", "计算机", "TMT_GROWTH", 68.0, 1950.0, "bull_tilt", 46.2, "AI服务器与通用算力基座"),
    StockCandidate("688256.SH", "寒武纪", "电子", "TMT_GROWTH", 102.0, 1680.0, "bull_tilt", 245.0, "云端AI智能推理芯片"),
    StockCandidate("600584.SH", "长电科技", "电子", "TMT_GROWTH", 62.0, 1120.0, "divergence", 34.8, "先进封测封装技术龙头"),
    # ── 2. 医药生物 ──────────────────────────────────────────────────────────
    StockCandidate("603259.SH", "药明康德", "医药生物", "HEALTHCARE", 128.0, 1850.0, "bear_tilt", 43.5, "CXO全球一体化研发服务龙头"),
    StockCandidate("300760.SZ", "迈瑞医疗", "医药生物", "HEALTHCARE", 315.0, 1450.0, "neutral", 260.0, "高端医疗器械与体外诊断龙头"),
    StockCandidate("300015.SZ", "爱尔眼科", "医药生物", "HEALTHCARE", 135.0, 920.0, "bear_tilt", 14.5, "眼科连锁医疗服务龙头"),
    StockCandidate("300122.SZ", "智飞生物", "医药生物", "HEALTHCARE", 65.0, 780.0, "bear_tilt", 27.2, "人用疫苗与重组蛋白研发"),
    StockCandidate("688180.SH", "君实生物", "医药生物", "HEALTHCARE", 32.0, 420.0, "divergence", 32.8, "创新单抗与肿瘤免疫药物"),
    StockCandidate("688235.SH", "百济神州", "医药生物", "HEALTHCARE", 185.0, 960.0, "bull_tilt", 138.0, "全球化创新抗肿瘤药物研发"),
    StockCandidate("000538.SZ", "云南白药", "医药生物", "HEALTHCARE", 95.0, 510.0, "defensive", 53.0, "中药独家品种与大健康"),
    StockCandidate("600436.SH", "片仔癀", "医药生物", "HEALTHCARE", 138.0, 680.0, "divergence", 228.0, "中药绝密配方与传统名药"),
    StockCandidate("688271.SH", "联影医疗", "医药生物", "HEALTHCARE", 105.0, 590.0, "neutral", 128.0, "高端医学影像大型诊疗设备"),
    StockCandidate("688617.SH", "惠泰医疗", "医药生物", "HEALTHCARE", 38.0, 390.0, "bull_tilt", 385.0, "电生理与血管介入创新器械"),
    # ── 3. 食品饮料 / 核心消费 ───────────────────────────────────────────────
    StockCandidate("600519.SH", "贵州茅台", "食品饮料", "CONSUMER", 2080.0, 4500.0, "neutral", 1650.0, "超高端白酒核心资产"),
    StockCandidate("000858.SZ", "五粮液", "食品饮料", "CONSUMER", 540.0, 2100.0, "neutral", 139.0, "浓香型高端白酒龙头"),
    StockCandidate("000568.SZ", "泸州老窖", "食品饮料", "CONSUMER", 195.0, 1380.0, "neutral", 132.5, "国窖1573高端白酒"),
    StockCandidate("600809.SH", "山西汾酒", "食品饮料", "CONSUMER", 245.0, 1260.0, "bull_tilt", 201.0, "清香型白酒全国化龙头"),
    StockCandidate("002304.SZ", "洋河股份", "食品饮料", "CONSUMER", 122.0, 750.0, "bear_tilt", 81.0, "绵柔型苏酒龙头"),
    StockCandidate("603288.SH", "海天味业", "食品饮料", "CONSUMER", 195.0, 820.0, "bear_tilt", 35.1, "调味品与酱油制造龙头"),
    StockCandidate("600887.SH", "伊利股份", "食品饮料", "CONSUMER", 145.0, 1150.0, "defensive", 22.8, "乳制品全产业链综合龙头"),
    StockCandidate("600600.SH", "青岛啤酒", "食品饮料", "CONSUMER", 88.0, 520.0, "neutral", 64.5, "高端啤酒龙头品牌"),
    StockCandidate("605499.SH", "东鹏饮料", "食品饮料", "CONSUMER", 89.0, 680.0, "bull_tilt", 222.5, "功能能量饮料全国化高增长"),
    StockCandidate("600690.SH", "海尔智家", "家用电器", "CONSUMER", 265.0, 1350.0, "defensive", 28.2, "全球化白色家电龙头"),
    StockCandidate("000651.SZ", "格力电器", "家用电器", "CONSUMER", 235.0, 1680.0, "defensive", 41.8, "高股息空调制造龙头"),
    StockCandidate("688169.SH", "石头科技", "家用电器", "CONSUMER", 48.0, 620.0, "bull_tilt", 260.0, "扫地机器人与智能清洁出海"),
    # ── 4. 电力设备 / 新能源 / 汽车 ──────────────────────────────────────────
    StockCandidate("300750.SZ", "宁德时代", "电力设备", "NEW_ENERGY_AUTO", 980.0, 3600.0, "bull_tilt", 222.0, "全球动力电池与储能龙头"),
    StockCandidate("002594.SZ", "比亚迪", "汽车", "NEW_ENERGY_AUTO", 735.0, 2900.0, "bull_tilt", 252.5, "新能源汽车与电池全产业链"),
    StockCandidate("601012.SH", "隆基绿能", "电力设备", "NEW_ENERGY_AUTO", 102.0, 1950.0, "bear_tilt", 13.5, "单晶硅片与光伏组件龙头"),
    StockCandidate("300274.SZ", "阳光电源", "电力设备", "NEW_ENERGY_AUTO", 145.0, 2100.0, "bull_tilt", 98.0, "光伏逆变器与储能系统龙头"),
    StockCandidate("600438.SH", "通威股份", "电力设备", "NEW_ENERGY_AUTO", 88.0, 1250.0, "bear_tilt", 19.5, "高纯晶硅与高效太阳能电池"),
    StockCandidate("002460.SZ", "赣锋锂业", "有色金属", "CYCLICAL_COMMODITIES", 58.0, 1050.0, "bear_tilt", 28.8, "锂矿开采与深加工"),
    StockCandidate("002466.SZ", "天齐锂业", "有色金属", "CYCLICAL_COMMODITIES", 52.0, 980.0, "bear_tilt", 31.7, "优质锂辉石与基础锂盐"),
    StockCandidate("300014.SZ", "亿纬锂能", "电力设备", "NEW_ENERGY_AUTO", 76.0, 1180.0, "divergence", 37.2, "消费与动力多技术路线电池"),
    StockCandidate("601633.SH", "长城汽车", "汽车", "NEW_ENERGY_AUTO", 195.0, 950.0, "divergence", 22.8, "越野SUV与皮卡全球化"),
    StockCandidate("601127.SH", "赛力斯", "汽车", "NEW_ENERGY_AUTO", 135.0, 3200.0, "bull_tilt", 89.5, "华为智选车高端智能电动SUV"),
    StockCandidate("002050.SZ", "三花智控", "汽车", "NEW_ENERGY_AUTO", 78.0, 1250.0, "bull_tilt", 21.0, "新能源汽车热管理与机器人执行器"),
    # ── 5. 机械设备 / 工业母机 / 先进制造 ───────────────────────────────────
    StockCandidate("600031.SH", "三一重工", "机械设备", "MACHINERY_ADVANCED", 145.0, 1120.0, "divergence", 17.1, "工程机械挖掘机龙头出海"),
    StockCandidate("000425.SZ", "徐工机械", "机械设备", "MACHINERY_ADVANCED", 82.0, 780.0, "neutral", 6.95, "全系列起重与工程机械"),
    StockCandidate("000157.SZ", "中联重科", "机械设备", "MACHINERY_ADVANCED", 68.0, 690.0, "defensive", 7.85, "高分红工程机械与农机"),
    StockCandidate("300124.SZ", "汇川技术", "机械设备", "MACHINERY_ADVANCED", 168.0, 1380.0, "bull_tilt", 63.0, "工控变频器/伺服系统龙头"),
    StockCandidate("601100.SH", "恒立液压", "机械设备", "MACHINERY_ADVANCED", 72.0, 650.0, "bull_tilt", 53.8, "高端高压油缸与液压泵阀"),
    StockCandidate("688305.SH", "科德数控", "机械设备", "MACHINERY_ADVANCED", 12.0, 210.0, "divergence", 72.0, "五轴高端数控机床与数控系统"),
    StockCandidate("601668.SH", "中国建筑", "建筑装饰", "MACHINERY_ADVANCED", 235.0, 1550.0, "defensive", 5.65, "超低估值房建基建龙头"),
    StockCandidate("600585.SH", "海螺水泥", "建筑材料", "MACHINERY_ADVANCED", 118.0, 680.0, "bear_tilt", 22.3, "水泥熟料制造龙头"),
    # ── 6. 周期大宗 / 基础化工 / 煤炭 / 有色 ─────────────────────────────────
    StockCandidate("601899.SH", "紫金矿业", "有色金属", "CYCLICAL_COMMODITIES", 435.0, 2650.0, "bull_tilt", 16.5, "铜金资源全球化开采龙头"),
    StockCandidate("601857.SH", "中国石油", "石油石化", "CYCLICAL_COMMODITIES", 1520.0, 1950.0, "defensive", 8.35, "高股息油气上游开采龙头"),
    StockCandidate("600309.SH", "万华化学", "基础化工", "CYCLICAL_COMMODITIES", 258.0, 1420.0, "divergence", 82.2, "MDI聚氨酯与新材料全球龙头"),
    StockCandidate("601088.SH", "中国神华", "煤炭", "CYCLICAL_COMMODITIES", 790.0, 1680.0, "defensive", 39.8, "高股息煤电港航一体化龙头"),
    StockCandidate("601225.SH", "陕西煤业", "煤炭", "CYCLICAL_COMMODITIES", 235.0, 1150.0, "defensive", 24.2, "高盈利动力煤开采龙头"),
    StockCandidate("600028.SH", "中国石化", "石油石化", "CYCLICAL_COMMODITIES", 740.0, 1250.0, "defensive", 6.18, "炼化一体化与加油站网络"),
    StockCandidate("600938.SH", "中国海油", "石油石化", "CYCLICAL_COMMODITIES", 1380.0, 1850.0, "defensive", 29.1, "海上高成长高分红油气开采"),
    StockCandidate("600019.SH", "宝钢股份", "钢铁", "CYCLICAL_COMMODITIES", 145.0, 890.0, "bear_tilt", 6.55, "高端板材钢铁制造龙头"),
    StockCandidate("601600.SH", "中国铝业", "有色金属", "CYCLICAL_COMMODITIES", 125.0, 1350.0, "divergence", 7.28, "铝土矿与电解铝全产业链"),
    StockCandidate("600547.SH", "山东黄金", "有色金属", "CYCLICAL_COMMODITIES", 132.0, 1550.0, "bull_tilt", 29.5, "黄金开采与贵金属避险"),
    StockCandidate("603993.SH", "洛阳钼业", "有色金属", "CYCLICAL_COMMODITIES", 168.0, 1920.0, "bull_tilt", 7.78, "刚果金铜钴与国内钼钨矿山"),
    # ── 7. 金融与地产 ────────────────────────────────────────────────────────
    StockCandidate("601318.SH", "中国平安", "非银金融", "FINANCIALS_REAL_ESTATE", 820.0, 2900.0, "bear_tilt", 45.2, "综合金融险企龙头"),
    StockCandidate("600036.SH", "招商银行", "银行", "FINANCIALS_REAL_ESTATE", 895.0, 2450.0, "neutral", 35.5, "零售银行财富管理龙头"),
    StockCandidate("601398.SH", "工商银行", "银行", "FINANCIALS_REAL_ESTATE", 2150.0, 2100.0, "defensive", 6.05, "宇宙行高股息防御底仓"),
    StockCandidate("600030.SH", "中信证券", "非银金融", "FINANCIALS_REAL_ESTATE", 295.0, 2250.0, "bull_tilt", 19.8, "证券行业综合实力第一龙头"),
    StockCandidate("601688.SH", "华泰证券", "非银金融", "FINANCIALS_REAL_ESTATE", 128.0, 1150.0, "neutral", 14.1, "科技赋能证券与财富管理"),
    StockCandidate("300059.SZ", "东方财富", "非银金融", "FINANCIALS_REAL_ESTATE", 215.0, 3800.0, "bull_tilt", 13.6, "互联网券商与基金代销龙头"),
    StockCandidate("601166.SH", "兴业银行", "银行", "FINANCIALS_REAL_ESTATE", 365.0, 1250.0, "defensive", 17.6, "商行+投行绿色金融先驱"),
    StockCandidate("601288.SH", "农业银行", "银行", "FINANCIALS_REAL_ESTATE", 1650.0, 1950.0, "defensive", 4.75, "县域金融高股息低波动"),
    StockCandidate("600048.SH", "保利发展", "房地产", "FINANCIALS_REAL_ESTATE", 108.0, 1420.0, "bear_tilt", 9.02, "央企核心房企稳健龙头"),
    StockCandidate("000002.SZ", "万科A", "房地产", "FINANCIALS_REAL_ESTATE", 92.0, 1680.0, "bear_tilt", 7.72, "龙头房企多元化转型"),
    # ── 8. 公用事业 / 交通运输 / 通信 ────────────────────────────────────────
    StockCandidate("601985.SH", "中国核电", "公用事业", "DEFENSIVE_UTILITIES", 198.0, 1350.0, "defensive", 10.5, "核电基荷能源与清洁绿电"),
    StockCandidate("600905.SH", "三峡能源", "公用事业", "DEFENSIVE_UTILITIES", 135.0, 890.0, "defensive", 4.72, "海上风电与大型光伏绿电基地"),
    StockCandidate("600011.SH", "华能国际", "公用事业", "DEFENSIVE_UTILITIES", 112.0, 950.0, "divergence", 7.15, "火电容量电价与风光转型"),
    StockCandidate("601919.SH", "中远海控", "交通运输", "DEFENSIVE_UTILITIES", 225.0, 2450.0, "divergence", 14.05, "集装箱航运全球龙头高股息"),
    StockCandidate("002352.SZ", "顺丰控股", "交通运输", "DEFENSIVE_UTILITIES", 182.0, 1250.0, "neutral", 37.3, "综合快递物流与供应链龙头"),
    StockCandidate("600941.SH", "中国移动", "通信", "DEFENSIVE_UTILITIES", 2250.0, 1850.0, "defensive", 105.0, "高股息通信运营商与算力网络"),
    StockCandidate("601728.SH", "中国电信", "通信", "DEFENSIVE_UTILITIES", 590.0, 1120.0, "defensive", 6.45, "云网融合运营商龙头"),
    StockCandidate("000063.SZ", "中兴通讯", "通信", "TMT_GROWTH", 138.0, 1850.0, "divergence", 28.8, "5G/6G通信设备与AI算力服务器"),
]

# ══════════════════════════════════════════════════════════════════════════════
# 4. Fingerprint Generator & De-duplication Logic
# ══════════════════════════════════════════════════════════════════════════════


def normalize_symbol_code(symbol: str) -> str:
    """Normalize stock symbol to clean uppercase standard format (e.g. '600519.SH' or '000858.SZ')."""
    if not symbol or not isinstance(symbol, str):
        return ""
    clean = symbol.strip().upper()
    # Add market suffix if 6 digits without suffix
    if re.match(r"^\d{6}$", clean):
        if clean.startswith(("60", "68", "90")):
            clean = f"{clean}.SH"
        elif clean.startswith(("00", "30", "20")):
            clean = f"{clean}.SZ"
        elif clean.startswith(("8", "4", "92")):
            clean = f"{clean}.BJ"
    return clean


def normalize_trade_date_str(trade_date: str) -> str:
    """Normalize various date formats (YYYYMMDD, YYYY-MM-DD, YYYY/MM/DD) to ISO YYYY-MM-DD."""
    if not trade_date or not isinstance(trade_date, str):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    s = trade_date.strip().replace("/", "-")
    # If YYYYMMDD
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    # If YYYY-MM-DD
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # Fallback to date parsing
    try:
        dt = datetime.fromisoformat(s)
        return dt.strftime("%Y-%m-%d")
    except Exception:
        return s


def generate_sample_fingerprint(
    symbol: str,
    trade_date: str,
    protocol_version: str = PROTOCOL_VERSION_V2_STRUCTURED,
) -> str:
    """Generate deterministic SHA256 deduplication fingerprint: SHA256(symbol + trade_date + protocol_version).

    Guarantees cross-week and within-week duplicate prevention and isolation against historical benchmark fixtures.
    """
    norm_sym = normalize_symbol_code(symbol)
    norm_date = normalize_trade_date_str(trade_date)
    norm_prot = (protocol_version or PROTOCOL_VERSION_V2_STRUCTURED).strip()

    fingerprint_raw = f"{norm_sym}:{norm_date}:{norm_prot}"
    return hashlib.sha256(fingerprint_raw.encode("utf-8")).hexdigest()


def verify_sample_fingerprint(
    fingerprint: str,
    symbol: str,
    trade_date: str,
    protocol_version: str = PROTOCOL_VERSION_V2_STRUCTURED,
) -> bool:
    """Verify whether a given SHA256 fingerprint matches the calculated expectation."""
    expected = generate_sample_fingerprint(symbol, trade_date, protocol_version)
    return bool(fingerprint and fingerprint.strip().lower() == expected.lower())


# ══════════════════════════════════════════════════════════════════════════════
# 5. Dynamic Pool Re-balancing Calculator (P0 Implementation)
# ══════════════════════════════════════════════════════════════════════════════


@dataclass
class RebalanceAudit:
    """Structured audit container for dynamic pool re-balancing."""

    historical_bull_samples: int = 0
    historical_bear_samples: int = 0
    total_directional_samples: int = 0
    historical_bull_ratio: float = 0.50
    deviation_from_parity: float = 0.0
    imbalance_detected: bool = False
    rebalance_direction: Literal["bear_compensate", "bull_compensate", "balanced"] = "balanced"
    cluster_weight_adjustments: Dict[str, float] = field(default_factory=dict)
    rebalance_rationale: str = ""
    target_pool_expected_ratio_min: float = REBALANCE_MIN_RATIO
    target_pool_expected_ratio_max: float = REBALANCE_MAX_RATIO


def calculate_rebalance_state(
    historical_bull_samples: int = 0,
    historical_bear_samples: int = 0,
    tolerance_band: float = REBALANCE_TOLERANCE_BAND,
) -> RebalanceAudit:
    """Calculate the dynamic re-balancing adjustments based on cumulative bull/bear sample counts.

    Rule (P0):
    1. Reads cumulative historical bull_samples and bear_samples.
    2. Calculates bull_ratio = N_bull / (N_bull + N_bear).
    3. If bull_ratio > 0.50 + tolerance (e.g. > 0.55), bullish drift is detected:
       - Up-weights defensive, high-dividend, supply-cycle bear_tilt / divergence / defensive industries;
       - Down-weights pure high-beta bullish momentum sectors to stabilize ratio back towards 50%.
    4. If bull_ratio < 0.50 - tolerance (e.g. < 0.45), bearish drift is detected:
       - Up-weights tech growth, innovation, and high-quality bull_tilt industries;
       - Down-weights deep defensive / bear_tilt sectors.
    5. Keeps multi-empty ratio within [40%, 60%].
    """
    n_bull = max(0, int(historical_bull_samples))
    n_bear = max(0, int(historical_bear_samples))
    total_dir = n_bull + n_bear

    if total_dir == 0:
        bull_ratio = REBALANCE_PARITY_TARGET
        deviation = 0.0
        imbalance = False
        direction: Literal["bear_compensate", "bull_compensate", "balanced"] = "balanced"
        rationale = "无历史多空样本累积，采用标准均衡基准权重分配。"
    else:
        bull_ratio = round(n_bull / total_dir, 4)
        deviation = round(bull_ratio - REBALANCE_PARITY_TARGET, 4)
        imbalance = abs(deviation) > tolerance_band

        if deviation > tolerance_band:
            direction = "bear_compensate"
            rationale = (
                f"历史多头样本偏置过高 (多头占比 {bull_ratio*100:.1f}% > 55.0%)，"
                f"启动多空再平衡：动态加权防御/逆向/周期/分歧特征标的，压降高Beta单边动量板块权重。"
            )
        elif deviation < -tolerance_band:
            direction = "bull_compensate"
            rationale = (
                f"历史空头样本偏置过高 (多头占比 {bull_ratio*100:.1f}% < 45.0%)，"
                f"启动多空再平衡：动态加权科技成长/新能源/核心消费高景气多头特征标的。"
            )
        else:
            direction = "balanced"
            rationale = f"历史多空样本分布处于健康均衡区间 (多头占比 {bull_ratio*100:.1f}% in [45.0%, 55.0%])，维持基础分散轮换权重。"

    # Compute cluster dynamic multipliers
    cluster_multipliers: Dict[str, float] = {}
    intensity = min(2.5, abs(deviation) / tolerance_band) if tolerance_band > 0 else 1.0

    for c_key, c_info in INDUSTRY_CLUSTERS.items():
        tilt = c_info.get("default_tilt", "neutral")
        mult = 1.0
        if direction == "bear_compensate":
            # History is too bull -> boost defensive/bear_tilt/divergence, curb bull_tilt
            if tilt in ("defensive", "bear_tilt"):
                mult = 1.0 + 0.35 * intensity
            elif tilt == "divergence":
                mult = 1.0 + 0.20 * intensity
            elif tilt == "bull_tilt":
                mult = max(0.40, 1.0 - 0.30 * intensity)
            else:
                mult = 1.0
        elif direction == "bull_compensate":
            # History is too bear -> boost bull_tilt/growth, curb deep defensive/bear_tilt
            if tilt == "bull_tilt":
                mult = 1.0 + 0.35 * intensity
            elif tilt == "neutral":
                mult = 1.0 + 0.15 * intensity
            elif tilt in ("defensive", "bear_tilt"):
                mult = max(0.40, 1.0 - 0.30 * intensity)
            else:
                mult = 1.0
        else:
            mult = 1.0

        cluster_multipliers[c_key] = round(mult, 4)

    return RebalanceAudit(
        historical_bull_samples=n_bull,
        historical_bear_samples=n_bear,
        total_directional_samples=total_dir,
        historical_bull_ratio=bull_ratio,
        deviation_from_parity=deviation,
        imbalance_detected=imbalance,
        rebalance_direction=direction,
        cluster_weight_adjustments=cluster_multipliers,
        rebalance_rationale=rationale,
        target_pool_expected_ratio_min=REBALANCE_MIN_RATIO,
        target_pool_expected_ratio_max=REBALANCE_MAX_RATIO,
    )


# ══════════════════════════════════════════════════════════════════════════════
# 6. Pydantic v2 Models for Target Pool Output
# ══════════════════════════════════════════════════════════════════════════════


class TargetPoolItemModel(BaseModel):
    """Normalized atomic model for a selected target pool stock candidate."""

    model_config = ConfigDict(extra="ignore")

    symbol: str = Field(description="Normalized stock ticker (e.g. 600519.SH)")
    name: str = Field(description="Display security company name (e.g. 贵州茅台)")
    industry: str = Field(description="Shenwan Level 1 industry classification")
    industry_cluster: str = Field(description="Industry cluster category key")
    market_cap_bil: float = Field(ge=0.0, description="Total market capitalization in Billion CNY")
    adv_mil: float = Field(ge=0.0, description="20-day Average Daily Volume in Million CNY")
    fingerprint: str = Field(description="Deterministic SHA256 deduplication fingerprint")
    stance_tendency: str = Field(description="Theoretical debate stance tilt (bull_tilt, bear_tilt, neutral, divergence, defensive)")
    selection_weight: float = Field(ge=0.0, description="Final calculated selection weight")
    selection_reason: str = Field(description="Selection rationale and industry rotation notes")


class IndustryDistributionSummaryModel(BaseModel):
    """Industry diversification and concentration summary."""

    model_config = ConfigDict(extra="ignore")

    total_samples: int = Field(ge=0, description="Total sample pool size")
    unique_industries_count: int = Field(ge=0, description="Count of distinct Shenwan L1 industries covered")
    covered_industries: List[str] = Field(default_factory=list, description="List of unique Shenwan L1 industries")
    industry_counts: Dict[str, int] = Field(default_factory=dict, description="Sample count breakdown per industry")
    max_single_symbol_share: float = Field(
        ge=0.0, le=1.0, description="Concentration ratio of single most frequent symbol (strictly <= 15%)"
    )
    diversification_passed: bool = Field(default=True, description="Whether industry count >= 5 and max share <= 15%")


class RebalanceAuditModel(BaseModel):
    """Pydantic model for dynamic pool rebalancing audit."""

    model_config = ConfigDict(extra="ignore")

    historical_bull_samples: int = Field(default=0, ge=0)
    historical_bear_samples: int = Field(default=0, ge=0)
    total_directional_samples: int = Field(default=0, ge=0)
    historical_bull_ratio: float = Field(default=0.50, ge=0.0, le=1.0)
    deviation_from_parity: float = Field(default=0.0)
    imbalance_detected: bool = Field(default=False)
    rebalance_direction: str = Field(default="balanced")
    cluster_weight_adjustments: Dict[str, float] = Field(default_factory=dict)
    rebalance_rationale: str = Field(default="")
    target_pool_expected_ratio_min: float = Field(default=0.40)
    target_pool_expected_ratio_max: float = Field(default=0.60)


class TargetPoolResultModel(BaseModel):
    """Top-level schema model for generated weekly target pool."""

    model_config = ConfigDict(extra="ignore")

    schema_version: str = Field(default="target_pool_v1", description="Schema contract version")
    trade_date: str = Field(description="Target evaluation trade date (YYYY-MM-DD)")
    protocol_version: str = Field(default=PROTOCOL_VERSION_V2_STRUCTURED, description="Protocol version for fingerprints")
    count: int = Field(ge=1, description="Generated pool sample count")
    items: List[TargetPoolItemModel] = Field(default_factory=list, description="Selected stock candidates list")
    rebalance_audit: RebalanceAuditModel = Field(default_factory=RebalanceAuditModel)
    industry_distribution: IndustryDistributionSummaryModel = Field(default_factory=IndustryDistributionSummaryModel)
    blacklist_filtered_count: int = Field(default=0, ge=0, description="Count of blacklisted symbols filtered out")
    duplicate_fingerprints_dropped: int = Field(default=0, ge=0, description="Count of duplicate fingerprints rejected")
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


# ══════════════════════════════════════════════════════════════════════════════
# 7. Target Pool Generation Engine (Pure Function Core)
# ══════════════════════════════════════════════════════════════════════════════


def generate_weekly_target_pool(
    trade_date: str,
    *,
    count: int = 10,
    historical_bull_samples: int = 0,
    historical_bear_samples: int = 0,
    historical_fingerprints: Optional[Set[str]] = None,
    blacklist_symbols: Optional[Set[str]] = None,
    protocol_version: str = PROTOCOL_VERSION_V2_STRUCTURED,
    min_market_cap_bil: float = DEFAULT_MIN_MARKET_CAP_BIL,
    min_adv_mil: float = DEFAULT_MIN_ADV_MIL,
    min_unique_industries: int = DEFAULT_MIN_UNIQUE_INDUSTRIES,
    max_single_symbol_share: float = DEFAULT_MAX_SINGLE_SYMBOL_SHARE,
    seed: Optional[int] = None,
) -> TargetPoolResultModel:
    """Generate weekly target pool with industry rotation, strict deduplication, and dynamic rebalancing.

    Pure function executing deterministically:
    1. Normalizes date and protocol version;
    2. Filters candidate universe against liquidity/market-cap admission rules;
    3. Excludes historical benchmark blacklist (美的、长电、恒瑞、京东方A等) and custom blacklist;
    4. Computes SHA256(symbol + trade_date + protocol_version) fingerprints and eliminates duplicates;
    5. Computes dynamic re-balancing weights from historical bull/bear metrics;
    6. Performs industry rotation across clusters, selecting >= 5 Shenwan L1 industries with <= 15% single share;
    7. Emits structured TargetPoolResultModel.
    """
    target_date = normalize_trade_date_str(trade_date)
    prot_ver = (protocol_version or PROTOCOL_VERSION_V2_STRUCTURED).strip()
    req_count = max(1, count)

    # Build active blacklist set
    active_blacklist = set(HISTORICAL_BENCHMARK_BLACKLIST)
    if blacklist_symbols:
        for s in blacklist_symbols:
            if s and isinstance(s, str):
                norm_b = normalize_symbol_code(s)
                active_blacklist.add(norm_b)
                active_blacklist.add(norm_b.split(".")[0])

    hist_fp_set = set(historical_fingerprints or [])

    # 1. Evaluate Rebalance State
    rebalance_state = calculate_rebalance_state(
        historical_bull_samples=historical_bull_samples,
        historical_bear_samples=historical_bear_samples,
    )

    # 2. Filter Candidate Universe
    blacklist_filtered_cnt = 0
    duplicate_fp_dropped_cnt = 0
    qualified_candidates: List[Tuple[StockCandidate, str, float]] = []

    for cand in CANDIDATE_STOCK_UNIVERSE:
        # Check market cap and liquidity
        if cand.market_cap_bil < min_market_cap_bil or cand.adv_mil < min_adv_mil:
            continue

        # Check blacklist
        sym_norm = normalize_symbol_code(cand.symbol)
        sym_raw = sym_norm.split(".")[0]
        if sym_norm in active_blacklist or sym_raw in active_blacklist:
            blacklist_filtered_cnt += 1
            continue

        # Compute SHA256 fingerprint
        fp = generate_sample_fingerprint(sym_norm, target_date, prot_ver)
        if fp in hist_fp_set:
            duplicate_fp_dropped_cnt += 1
            continue

        # Calculate dynamic selection weight
        cluster_mult = rebalance_state.cluster_weight_adjustments.get(cand.cluster, 1.0)
        # Base weight derived from market cap and liquidity log-scale
        cap_weight = math.log10(max(10.0, cand.market_cap_bil))
        adv_weight = math.log10(max(100.0, cand.adv_mil))
        base_w = cap_weight * 0.5 + adv_weight * 0.5

        # Apply stance tendency tilt bonus if rebalancing is active
        stance_bonus = 1.0
        if rebalance_state.rebalance_direction == "bear_compensate":
            if cand.stance_tendency in ("defensive", "bear_tilt"):
                stance_bonus = 1.30
            elif cand.stance_tendency == "divergence":
                stance_bonus = 1.15
            elif cand.stance_tendency == "bull_tilt":
                stance_bonus = 0.70
        elif rebalance_state.rebalance_direction == "bull_compensate":
            if cand.stance_tendency == "bull_tilt":
                stance_bonus = 1.30
            elif cand.stance_tendency == "neutral":
                stance_bonus = 1.15
            elif cand.stance_tendency in ("defensive", "bear_tilt"):
                stance_bonus = 0.70

        final_weight = round(base_w * cluster_mult * stance_bonus, 4)
        qualified_candidates.append((cand, fp, final_weight))

    # 3. Deterministic Seed / Rotation Scoring
    # If seed is provided or derived from date string hash
    if seed is None:
        # Generate deterministic seed from date string to ensure reproducibility
        date_hash_int = int(hashlib.md5(target_date.encode("utf-8")).hexdigest()[:8], 16)
        active_seed = date_hash_int
    else:
        active_seed = seed

    # Sort candidates deterministically with combined weight and hash rotation
    def _rank_key(item: Tuple[StockCandidate, str, float]) -> Tuple[float, str]:
        c, fp, w = item
        # Deterministic pseudo-random jitter derived from seed + symbol hash
        sym_hash_int = int(hashlib.md5(f"{active_seed}:{c.symbol}".encode("utf-8")).hexdigest()[:6], 16)
        jitter = (sym_hash_int % 1000) / 1000.0 * 0.25  # 0.0 ~ 0.25 jitter
        composite_score = w + jitter
        return (composite_score, c.symbol)

    ranked_candidates = sorted(qualified_candidates, key=_rank_key, reverse=True)

    # 4. Multi-Industry Rotation Selection
    selected_items: List[TargetPoolItemModel] = []
    selected_symbols: Set[str] = set()
    selected_industries: Set[str] = set()
    selected_clusters: Dict[str, int] = {}

    # Pass 1: Ensure industry diversity (pick top candidate from distinct industries first)
    for cand, fp, weight in ranked_candidates:
        if len(selected_items) >= req_count:
            break
        if cand.symbol in selected_symbols:
            continue
        if cand.industry in selected_industries:
            continue

        # Add candidate
        cluster_name = INDUSTRY_CLUSTERS.get(cand.cluster, {}).get("name", cand.cluster)
        reason = (
            f"申万【{cand.industry}】行业轮换覆盖；市值 {cand.market_cap_bil:.1f}亿 / "
            f"日均成交 {cand.adv_mil:.0f}万；{cand.stance_tendency}特征匹配再平衡权重 ({weight:.2f})。"
        )
        item = TargetPoolItemModel(
            symbol=cand.symbol,
            name=cand.name,
            industry=cand.industry,
            industry_cluster=cand.cluster,
            market_cap_bil=cand.market_cap_bil,
            adv_mil=cand.adv_mil,
            fingerprint=fp,
            stance_tendency=cand.stance_tendency,
            selection_weight=weight,
            selection_reason=reason,
        )
        selected_items.append(item)
        selected_symbols.add(cand.symbol)
        selected_industries.add(cand.industry)
        selected_clusters[cand.cluster] = selected_clusters.get(cand.cluster, 0) + 1

    # Pass 2: If we still need more candidates to meet count, fill with remaining highest-weighted distinct symbols
    for cand, fp, weight in ranked_candidates:
        if len(selected_items) >= req_count:
            break
        if cand.symbol in selected_symbols:
            continue

        # Prevent extreme cluster over-concentration (e.g. max 3 per cluster in a batch of 10)
        c_count = selected_clusters.get(cand.cluster, 0)
        if c_count >= max(2, math.ceil(req_count * 0.30)) and len(selected_items) < req_count - 1:
            continue

        reason = (
            f"优选补充【{cand.industry}】高流动性标的；市值 {cand.market_cap_bil:.1f}亿 / "
            f"日均成交 {cand.adv_mil:.0f}万；再平衡综合得分 {weight:.2f}。"
        )
        item = TargetPoolItemModel(
            symbol=cand.symbol,
            name=cand.name,
            industry=cand.industry,
            industry_cluster=cand.cluster,
            market_cap_bil=cand.market_cap_bil,
            adv_mil=cand.adv_mil,
            fingerprint=fp,
            stance_tendency=cand.stance_tendency,
            selection_weight=weight,
            selection_reason=reason,
        )
        selected_items.append(item)
        selected_symbols.add(cand.symbol)
        selected_industries.add(cand.industry)
        selected_clusters[cand.cluster] = selected_clusters.get(cand.cluster, 0) + 1

    # 5. Summarize Industry & Diversification Metrics
    total_selected = len(selected_items)
    ind_list = [it.industry for it in selected_items]
    ind_counts = {ind: ind_list.count(ind) for ind in set(ind_list)}
    unique_ind_count = len(ind_counts)

    # Calculate max single symbol share
    sym_list = [it.symbol for it in selected_items]
    sym_counts = {s: sym_list.count(s) for s in set(sym_list)}
    max_sym_cnt = max(sym_counts.values()) if sym_counts else 0
    max_share = round((max_sym_cnt / total_selected), 4) if total_selected > 0 else 0.0

    div_passed = (unique_ind_count >= min(min_unique_industries, total_selected)) and (
        max_share <= max_single_symbol_share
    )

    ind_dist = IndustryDistributionSummaryModel(
        total_samples=total_selected,
        unique_industries_count=unique_ind_count,
        covered_industries=sorted(list(set(ind_list))),
        industry_counts=ind_counts,
        max_single_symbol_share=max_share,
        diversification_passed=div_passed,
    )

    reb_model = RebalanceAuditModel(
        historical_bull_samples=rebalance_state.historical_bull_samples,
        historical_bear_samples=rebalance_state.historical_bear_samples,
        total_directional_samples=rebalance_state.total_directional_samples,
        historical_bull_ratio=rebalance_state.historical_bull_ratio,
        deviation_from_parity=rebalance_state.deviation_from_parity,
        imbalance_detected=rebalance_state.imbalance_detected,
        rebalance_direction=rebalance_state.rebalance_direction,
        cluster_weight_adjustments=rebalance_state.cluster_weight_adjustments,
        rebalance_rationale=rebalance_state.rebalance_rationale,
        target_pool_expected_ratio_min=rebalance_state.target_pool_expected_ratio_min,
        target_pool_expected_ratio_max=rebalance_state.target_pool_expected_ratio_max,
    )

    return TargetPoolResultModel(
        trade_date=target_date,
        protocol_version=prot_ver,
        count=total_selected,
        items=selected_items,
        rebalance_audit=reb_model,
        industry_distribution=ind_dist,
        blacklist_filtered_count=blacklist_filtered_cnt,
        duplicate_fingerprints_dropped=duplicate_fp_dropped_cnt,
    )
