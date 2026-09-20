好的，我现在开始编写完整的规划执行应用方案。让我先系统查看项目中的关键文件，验证可行性：继续查看项目中现有的数据获取实现和prompt结构：搜索结果信息量不够具体。让我直接查看akshare和tushare的官方文档，同时检查项目中已有的实现模式来编写方案：好的，网络搜索次数已用完。现在我基于已获取的信息和项目现有代码，编写完整的技术方案文档。让我系统地整理：

---

# 🎯 TradingAgents 宏观分析全面增强方案
## 多层次联动分析系统设计与实施文档

**版本**: v1.0  
**编写日期**: 2026年  
**目标读者**: 接手执行的AI助手或开发者  
**项目**: tradingagents（基于LangGraph的多Agent量化分析系统）

---

## 📋 一、方案概述

### 1.1 背景与目标

**当前问题**:
- 宏观分析师只分析"板块资金流 + 新闻"，视野狭窄
- 缺乏国际市场、大类资产、央行政策等维度
- 各分析师之间缺乏产业链联动逻辑
- 无法实现"全球宏观 → 中国市场 → 行业 → 个股"的自上而下分析

**改造目标**:
实现**多层次、多维度、国际化**的宏观分析体系：

```
┌─────────────────────────────────────────────┐
│ 第一层：全球宏观环境（新增）                    │
│ - 国际股市（美日韩欧）                         │
│ - 大类资产（黄金/原油/美债/美元）               │
│ - 央行政策（美联储/欧央行）                    │
└─────────────────────────────────────────────┘
                    ↓ 传导影响
┌─────────────────────────────────────────────┐
│ 第二层：中国市场环境（增强）                    │
│ - 国内大盘指数（沪深300/创业板指/科创50）        │
│ - 央行政策（MLF/LPR/准备金率）                 │
│ - 市场情绪（涨跌停统计/北向资金）               │
└─────────────────────────────────────────────┘
                    ↓ 板块轮动
┌─────────────────────────────────────────────┐
│ 第三层：行业板块（增强）                        │
│ - 板块资金流（已有）                           │
│ - 产业链关键指标联动（新增）                    │
│   例：芯片股 ↔ AI热度 ↔ 费城半导体指数         │
└─────────────────────────────────────────────┘
                    ↓ 个股选择
┌─────────────────────────────────────────────┐
│ 第四层：个股分析（已有）                        │
│ - 技术面/基本面/情绪面                         │
└─────────────────────────────────────────────┘
```

### 1.2 核心原则

1. **按职责分散数据**: 不同性质的数据分配给对应分析师，避免单个节点过载
2. **保留关键信息**: prompt压缩时优先保留技术形态、同比变化、关键转折点
3. **容错与降级**: 所有外部API调用必须有超时/重试/失败降级机制
4. **遵守AGENTS.md规范**: 
   - 按列名取数（禁止位置切片）
   - 失败时返回明确提示（禁止返回None）
   - 所有修改必须有单元测试

---

## 二、数据源与API验证

### 2.1 可用资源清单

#### **Tushare Pro (15000积分权限)**
项目已配置: `tradingagents/config/tushare_config.py`  
Token: 通过第三方中转接口访问

**15000积分可用的关键接口**（需实际验证具体名称）:
| 接口名称（推测） | 功能 | 积分要求（估计） | 优先级 |
|----------------|------|-----------------|--------|
| `index_global()` | 全球指数日线（标普500/纳指/日经225等） | 120-300 | 🔴 高 |
| `index_daily()` | 国内指数日线（沪深300/创业板指等） | 免费 | 🔴 高 |
| `fx_daily()` | 外汇行情（美元指数/人民币汇率） | 100-200 | 🟡 中 |
| `fut_daily()` | 期货日线（可能包含原油/黄金） | 200-400 | 🟡 中 |
| `us_daily()` | 美股个股日线 | 300-500 | 🟢 低 |
| `shibor()` | 上海银行间同业拆放利率 | 免费 | 🟡 中 |
| `moneyflow_hsgt()` | 沪深港通资金流向 | 300 | 🔴 高 |

**⚠️ 警告**: 
- 具体接口名称需验证（tushare官网可能使用不同命名）
- 第三方中转接口可能有额外限制（QPS/每日调用次数）
- 需在实际环境中测试返回字段名

#### **AkShare (开源免费)**
项目已使用: `tradingagents/dataflows/sector_flows.py`

**已验证可用**:
- `stock_board_industry_cons_em()` - 板块成分股
- `stock_board_industry_name_em()` - 板块列表

**待验证的国际数据接口**（从搜索结果推测）:
| 接口名称（推测） | 功能 | 数据源 | 优先级 |
|----------------|------|--------|--------|
| `stock_us_hist()` 或 `stock_us_daily()` | 美股历史数据 | 雅虎财经 | 🔴 高 |
| `index_investing_global()` | 全球指数（Investing.com） | Investing | 🔴 高 |
| `futures_foreign_commodity()` | 国际商品期货（黄金/原油） | 交易所 | 🟡 中 |
| `macro_china_ppi()` | 国内宏观数据（PPI/CPI） | 统计局 | 🟢 低 |

**⚠️ 警告**: 
- AkShare接口稳定性弱于Tushare（依赖爬虫）
- 国际数据可能有时区和延迟问题
- 部分接口可能因网站反爬而失效

### 2.2 API调用验证步骤（需执行者完成）

**在开始编码前，必须先在项目环境中运行以下验证脚本**:

```python
# scripts/verify_apis.py
import tushare as ts
import akshare as ak
from datetime import datetime, timedelta

# 1. 验证Tushare Pro接口
pro = ts.pro_api()  # 使用项目已配置的token

test_date = (datetime.now() - timedelta(days=1)).strftime('%Y%m%d')

print("=== Tushare Pro API验证 ===")
try:
    # 测试国内指数
    df = pro.index_daily(ts_code='000300.SH', start_date=test_date, end_date=test_date)
    print(f"✓ index_daily: {df.columns.tolist()}")
except Exception as e:
    print(f"✗ index_daily失败: {e}")

try:
    # 测试全球指数（需确认实际接口名）
    df = pro.index_global(ts_code='DJI', start_date=test_date, end_date=test_date)
    print(f"✓ index_global: {df.columns.tolist()}")
except Exception as e:
    print(f"✗ index_global失败（可能接口名错误）: {e}")

try:
    # 测试北向资金
    df = pro.moneyflow_hsgt(start_date=test_date, end_date=test_date)
    print(f"✓ moneyflow_hsgt: {df.columns.tolist()}")
except Exception as e:
    print(f"✗ moneyflow_hsgt失败: {e}")

print("\n=== AkShare API验证 ===")
try:
    # 测试美股数据
    df = ak.stock_us_hist(symbol='AAPL', period='daily', adjust='qfq')
    print(f"✓ stock_us_hist: {df.columns.tolist()}")
except Exception as e:
    print(f"✗ stock_us_hist失败: {e}")

try:
    # 测试全球指数
    df = ak.index_investing_global(country='美国', index_name='标普500')
    print(f"✓ index_investing_global: {df.columns.tolist()}")
except Exception as e:
    print(f"✗ index_investing_global失败（可能需要调整参数）: {e}")
```

**验证清单**:
- [ ] 记录所有成功接口的**实际名称**和**返回字段**
- [ ] 测量单次调用耗时（用于设置超时阈值）
- [ ] 确认15000积分下的接口调用频率限制
- [ ] 记录失败接口，准备备用方案

---

## 三、架构设计

### 3.1 分析师职责重新分配

#### **现有分析师（保留+增强）**

##### 1. 宏观分析师 `macro_analyst.py`
**原职责**: 板块资金流分析  
**新增职责**: 
- 国内大盘指数走势（沪深300/创业板指/科创50/上证指数/深证成指）
- 央行政策信号（MLF/LPR利率变动）
- 市场情绪温度（涨停/跌停家数、涨跌比）

**新增数据输入**:
```python
{
    "domestic_indices": {
        "000300.SH": {"name": "沪深300", "close": 3500, "change_pct": -1.2, "ma5": 3520, ...},
        "399006.SZ": {"name": "创业板指", "close": 2100, "change_pct": -2.3, ...}
    },
    "market_sentiment": {
        "limit_up_count": 15,
        "limit_down_count": 45,
        "up_down_ratio": 0.33
    },
    "monetary_policy": {
        "latest_mlf_rate": 2.50,
        "latest_lpr_1y": 3.45,
        "change_from_last_month": 0.0
    },
    "sector_flows": {...}  # 已有数据，保留
}
```

**Prompt调整重点**:
- 强调"自上而下"分析顺序：先看大盘，再看板块
- 要求判断当前是结构性行情还是普涨普跌
- 输出格式增加"大盘环境"章节

---

##### 2. 新闻分析师 `news_analyst.py`
**原职责**: 个股新闻 + A股制度排查  
**新增职责**: 
- 国际重大新闻（美联储政策/地缘政治/中美关系）
- 行业政策新闻（针对个股所属行业）

**新增数据输入**:
```python
{
    "stock_news": [...],  # 已有，保留
    "institution_alerts": [...],  # 已有，保留
    "global_news": [
        {"title": "美联储维持利率不变", "date": "2026-01-15", "source": "Reuters", "summary": "..."},
        {"title": "中东局势升级", "category": "geopolitics", ...}
    ],
    "industry_policy_news": [
        {"title": "芯片产业扶持政策出台", "industry": "半导体", ...}
    ]
}
```

**Prompt调整重点**:
- 新增"国际背景"章节，优先级高于个股新闻
- 要求识别新闻对A股的传导路径（例：美联储加息→美元走强→新兴市场承压→A股外资流出）

---

##### 3. 基本面分析师 `fundamentals_analyst.py`
**原职责**: 财务数据分析  
**新增职责**: 
- **产业链关键指标联动**（这是本次改造的核心创新点）

**产业链映射表**（需在代码中维护）:
```python
INDUSTRY_LINKAGE_MAP = {
    "石油开采": {
        "commodities": ["布伦特原油", "WTI原油"],
        "news_keywords": ["OPEC", "美伊关系", "中东局势"],
        "related_indices": []
    },
    "半导体": {
        "commodities": ["存储芯片价格指数"],
        "news_keywords": ["AI", "ChatGPT", "英伟达", "台积电"],
        "related_indices": ["费城半导体指数"]
    },
    "黄金": {
        "commodities": ["伦敦金现货", "COMEX黄金"],
        "news_keywords": ["避险情绪", "美债收益率"],
        "related_indices": []
    },
    "航空运输": {
        "commodities": ["航空煤油", "WTI原油"],
        "news_keywords": ["油价", "人民币汇率"],
        "related_indices": []
    },
    "新能源车": {
        "commodities": ["碳酸锂价格", "钴价"],
        "news_keywords": ["特斯拉", "新能源补贴"],
        "related_indices": ["特斯拉股价"]
    }
}
```

**新增数据输入**:
```python
{
    "financials": {...},  # 已有，保留
    "industry_linkage": {
        "industry_name": "半导体",
        "key_commodities": {
            "DRAM价格指数": {"latest": 105, "change_30d": +8.5, "trend": "上涨"},
            "NAND价格指数": {"latest": 98, "change_30d": +3.2, "trend": "企稳"}
        },
        "related_indices": {
            "费城半导体指数": {"close": 3500, "change_pct": +2.1, "ma20": 3450}
        },
        "news_signals": [
            {"keyword": "AI", "mention_count_7d": 150, "sentiment": "积极"}
        ]
    }
}
```

**Prompt调整重点**:
- 在财务分析后，增加"产业链环境"章节
- 要求说明关键指标对公司业绩的潜在影响
- 例：分析中国石油时，提示"布伦特原油价格上涨10%，公司营收弹性约为X%"

---

##### 4. 情绪分析师 `social_media_analyst.py`
**原职责**: A股舆情分析  
**新增职责**: 
- VIX恐慌指数（反映全球风险偏好）

**新增数据输入**:
```python
{
    "a_share_sentiment": {...},  # 已有，保留
    "global_risk_appetite": {
        "vix_index": {
            "latest": 18.5,
            "change_pct": +15.3,
            "level": "中等恐慌",  # <15正常, 15-25中等, >25高度恐慌
            "interpretation": "VIX上升表明美股投资者恐慌情绪上升，可能传导至A股"
        }
    }
}
```

---

#### **新增分析师**

##### 5. 国际市场分析师 `global_market_analyst.py` 【新建】
**核心职责**: 提供全球宏观环境背景

**数据输入**:
```python
{
    "global_indices": {
        "^GSPC": {"name": "标普500", "close": 4500, "change_pct": -0.8, "ma20": 4520, ...},
        "^IXIC": {"name": "纳斯达克", "close": 14000, "change_pct": -1.2, ...},
        "^N225": {"name": "日经225", "close": 33000, "change_pct": -0.5, ...},
        "^HSI": {"name": "恒生指数", "close": 17000, "change_pct": +0.3, ...}
    },
    "major_assets": {
        "gold": {"symbol": "GC", "close": 2050, "change_pct": +1.5, "trend": "上涨"},
        "oil_brent": {"symbol": "BZ", "close": 85, "change_pct": -2.3, "trend": "下跌"},
        "us_10y_yield": {"latest": 4.25, "change_bps": +10, "trend": "上行"},
        "dxy": {"close": 103.5, "change_pct": +0.5, "trend": "走强"}
    },
    "fed_policy": {
        "current_rate": 5.25,
        "next_meeting_date": "2026-03-18",
        "market_expectation": "维持不变"
    }
}
```

**Prompt设计**:
```python
"""你是国际市场分析师，负责评估全球宏观环境对A股的潜在影响。

分析框架：
1. 主要股市联动性：美股/日经/恒指与A股的历史相关性
2. 大类资产信号：
   - 美债收益率上升 → 全球流动性收紧
   - 黄金上涨 → 避险情绪升温
   - 原油暴跌 → 通缩预期或需求萎缩
3. 央行政策周期：美联储加息/降息周期对新兴市场的传导时滞
4. 风险传导路径：国际市场波动 → 人民币汇率 → 外资流向 → A股

输出结构：
- 一、全球股市扫描（30字总结当前状态）
- 二、大类资产信号（黄金/原油/美债的关键变化）
- 三、对A股的传导预期（正面/中性/负面，具体路径）
"""
```

---

### 3.2 图结构调整

**原执行顺序**（从 `tradingagents/graph/setup.py` 推测）:
```
START → macro → fundamentals → news → market → social_media → END
```

**新执行顺序**:
```
START 
  ↓
global_market (新增，国际环境)
  ↓
macro (国内大盘+板块)
  ↓
news (国际新闻+个股新闻)
  ↓
fundamentals (财务+产业链联动)
  ↓
market (技术面，不变)
  ↓
social_media (舆情+VIX)
  ↓
END
```

**并行优化**（可选）:
- `global_market` 和 `news` 的国际新闻获取可并行
- `macro` 的多个数据源（指数/板块/央行）可并行获取

---

## 四、数据工具层实现

### 4.1 新建文件：`tradingagents/agents/utils/macro_data_tools.py`

这是**最核心的基础设施文件**，所有新增数据获取逻辑都封装在此。

#### 4.1.1 总体设计原则

```python
"""
宏观数据获取工具集

设计原则：
1. 所有外部调用必须有超时（30秒）、重试（3次）、失败降级
2. 返回格式统一：成功返回dict，失败返回{"error": "具体原因"}
3. 数据必须清洗：按列名取数、剔除空值、计算衍生指标
4. 注入LLM前压缩：只保留最近N天+关键技术指标
"""

import tushare as ts
import akshare as ak
import pandas as pd
from datetime import datetime, timedelta
from typing import Dict, List, Optional
import time
from functools import wraps

# 导入项目配置
from tradingagents.config.tushare_config import get_tushare_pro

# ===== 通用装饰器 =====
def retry_with_timeout(max_retries=3, timeout=30):
    """重试和超时装饰器"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    # 这里需要结合实际API的超时设置
                    result = func(*args, **kwargs)
                    return result
                except Exception as e:
                    if attempt == max_retries - 1:
                        return {"error": f"{func.__name__}失败（重试{max_retries}次）: {str(e)}"}
                    time.sleep(2 ** attempt)  # 指数退避
            return {"error": f"{func.__name__}未知错误"}
        return wrapper
    return decorator
```

#### 4.1.2 核心函数清单

```python
# ===== 1. 国内指数数据 =====
@retry_with_timeout()
def get_cn_index_data(index_codes: List[str], end_date: str, look_back_days: int = 5) -> Dict:
    """
    获取国内指数K线数据
    
    Args:
        index_codes: 指数代码列表，如 ['000300.SH', '399006.SZ']
        end_date: 结束日期 'YYYYMMDD'
        look_back_days: 回看天数
    
    Returns:
        {
            "000300.SH": {
                "name": "沪深300",
                "close": 3500.0,
                "change_pct": -1.2,
                "ma5": 3520.0,
                "ma20": 3480.0,
                "volume_ratio": 1.15,  # 今日成交量/5日均量
                "trend": "下跌",
                "key_levels": {"support": 3450, "resistance": 3550}
            },
            ...
        }
    """
    pro = get_tushare_pro()
    result = {}
    
    INDEX_NAMES = {
        "000300.SH": "沪深300",
        "399006.SZ": "创业板指",
        "000688.SH": "科创50",
        "000001.SH": "上证指数",
        "399001.SZ": "深证成指"
    }
    
    start_date = (datetime.strptime(end_date, '%Y%m%d') - timedelta(days=look_back_days + 30)).strftime('%Y%m%d')
    
    for code in index_codes:
        try:
            # ⚠️ 需验证实际接口名和字段
            df = pro.index_daily(ts_code=code, start_date=start_date, end_date=end_date)
            
            if df.empty:
                result[code] = {"error": "无数据"}
                continue
            
            # 按列名取数（遵守AGENTS.md规范）
            df = df.sort_values('trade_date')
            latest = df.iloc[-1]
            
            # 计算技术指标
            df['ma5'] = df['close'].rolling(5).mean()
            df['ma20'] = df['close'].rolling(20).mean()
            df['volume_ma5'] = df['vol'].rolling(5).mean()
            
            result[code] = {
                "name": INDEX_NAMES.get(code, code),
                "close": float(latest['close']),
                "change_pct": float(latest['pct_chg']),
                "ma5": float(df.iloc[-1]['ma5']) if pd.notna(df.iloc[-1]['ma5']) else None,
                "ma20": float(df.iloc[-1]['ma20']) if pd.notna(df.iloc[-1]['ma20']) else None,
                "volume_ratio": float(latest['vol'] / df.iloc[-1]['volume_ma5']) if pd.notna(df.iloc[-1]['volume_ma5']) else 1.0,
                "trend": "上涨" if latest['pct_chg'] > 0 else "下跌",
                "recent_high": float(df['close'].tail(20).max()),
                "recent_low": float(df['close'].tail(20).min())
            }
        except Exception as e:
            result[code] = {"error": str(e)}
    
    return result


# ===== 2. 国际股指数据 =====
@retry_with_timeout()
def get_global_indices(symbols: List[str], end_date: str, look_back_days: int = 5) -> Dict:
    """
    获取国际主要股指数据
    
    Args:
        symbols: 指数代码，如 ['^GSPC', '^IXIC', '^N225']（需根据实际API调整）
    
    ⚠️ 注意：需要先验证tushare的index_global或akshare的接口
    """
    # 优先使用Tushare（更稳定）
    pro = get_tushare_pro()
    result = {}
    
    SYMBOL_MAP = {
        "DJI": {"name": "道琼斯", "akshare_name": "道琼斯"},
        "SPX": {"name": "标普500", "akshare_name": "标普500"},
        "IXIC": {"name": "纳斯达克", "akshare_name": "纳斯达克"},
        "N225": {"name": "日经225", "akshare_name": "日经225"},
        "HSI": {"name": "恒生指数", "akshare_name": "恒生指数"}
    }
    
    for symbol in symbols:
        try:
            # 尝试Tushare（需验证实际接口）
            df = pro.index_global(ts_code=symbol, start_date=start_date, end_date=end_date)
            
            if df.empty:
                # 降级到AkShare
                raise Exception("Tushare无数据，降级到AkShare")
            
            # 处理逻辑同上
            latest = df.iloc[-1]
            result[symbol] = {
                "name": SYMBOL_MAP[symbol]["name"],
                "close": float(latest['close']),
                "change_pct": float(latest['pct_chg']),
                # ... 其他指标
            }
        except:
            # 降级到AkShare
            try:
                # ⚠️ 需验证实际函数名
                df = ak.index_investing_global(country="美国", index_name=SYMBOL_MAP[symbol]["akshare_name"])
                # 处理逻辑...
                result[symbol] = {"close": 0, "change_pct": 0}  # 占位
            except Exception as e:
                result[symbol] = {"error": f"双重失败: {str(e)}"}
    
    return result


# ===== 3. 大宗商品数据 =====
@retry_with_timeout()
def get_commodity_prices(commodities: List[str], end_date: str) -> Dict:
    """
    获取大宗商品价格
    
    Args:
        commodities: ['gold', 'oil_brent', 'oil_wti', 'copper', 'dxy']
    
    Returns:
        {
            "gold": {"close": 2050, "change_pct": 1.5, "trend": "上涨"},
            "oil_brent": {"close": 85, "change_pct": -2.3, "trend": "下跌"},
            ...
        }
    """
    result = {}
    
    COMMODITY_MAP = {
        "gold": {"tushare_code": "AU", "akshare_func": "futures_comex_gold_daily"},  # 需验证
        "oil_brent": {"tushare_code": "BZ", "akshare_func": "futures_brent_oil_daily"},
        "oil_wti": {"tushare_code": "CL", "akshare_func": "futures_wti_oil_daily"},
        "dxy": {"tushare_code": None, "akshare_func": "currency_us_dollar_index"}
    }
    
    for commodity in commodities:
        try:
            # 优先Tushare期货接口
            if COMMODITY_MAP[commodity]["tushare_code"]:
                pro = get_tushare_pro()
                df = pro.fut_daily(ts_code=COMMODITY_MAP[commodity]["tushare_code"], 
                                   start_date=start_date, end_date=end_date)
                # 处理...
            else:
                # 使用AkShare
                func_name = COMMODITY_MAP[commodity]["akshare_func"]
                df = getattr(ak, func_name)()
                # 处理...
            
            result[commodity] = {"close": 0, "change_pct": 0, "trend": "震荡"}
        except Exception as e:
            result[commodity] = {"error": str(e)}
    
    return result


# ===== 4. 美债收益率 =====
@retry_with_timeout()
def get_bond_yields(end_date: str) -> Dict:
    """
    获取主要国家国债收益率
    
    Returns:
        {
            "us_10y": {"yield": 4.25, "change_bps": 10, "trend": "上行"},
            "cn_10y": {"yield": 2.65, "change_bps": -5, "trend": "下行"}
        }
    """
    # ⚠️ 需验证Tushare的yc_cb或AkShare的bond接口
    pass


# ===== 5. 北向资金 =====
@retry_with_timeout()
def get_northbound_flow(stock_code: str, end_date: str, look_back_days: int = 10) -> Dict:
    """
    获取个股北向资金流向
    
    Returns:
        {
            "net_inflow_10d": 5000000,  # 10日净流入（万元）
            "holding_ratio": 8.5,  # 持股比例
            "trend": "持续流入"
        }
    """
    pro = get_tushare_pro()
    # ⚠️ 需验证moneyflow_hsgt接口的具体用法
    pass


# ===== 6. 市场情绪指标 =====
@retry_with_timeout()
def get_market_sentiment(end_date: str) -> Dict:
    """
    获取市场整体情绪指标
    
    Returns:
        {
            "limit_up_count": 15,
            "limit_down_count": 45,
            "up_down_ratio": 0.33,
            "turnover_rate_avg": 2.5,
            "interpretation": "恐慌情绪浓厚"
        }
    """
    # 使用AkShare的stock_zt_pool_em等接口
    try:
        # 涨停股池
        limit_up_df = ak.stock_zt_pool_em(date=end_date.replace('-', ''))
        # 跌停股池
        limit_down_df = ak.stock_dt_pool_em(date=end_date.replace('-', ''))
        
        limit_up_count = len(limit_up_df)
        limit_down_count = len(limit_down_df)
        ratio = limit_up_count / limit_down_count if limit_down_count > 0 else 999
        
        return {
            "limit_up_count": limit_up_count,
            "limit_down_count": limit_down_count,
            "up_down_ratio": round(ratio, 2),
            "interpretation": "积极" if ratio > 2 else ("恐慌" if ratio < 0.5 else "分化")
        }
    except Exception as e:
        return {"error": str(e)}


# ===== 7. VIX恐慌指数 =====
@retry_with_timeout()
def get_vix_index(end_date: str, look_back_days: int = 5) -> Dict:
    """
    获取VIX恐慌指数
    
    Returns:
        {
            "latest": 18.5,
            "change_pct": 15.3,
            "level": "中等恐慌",  # <15正常, 15-25中等, >25高度恐慌
            "ma20": 16.8
        }
    """
    try:
        # ⚠️ 需验证AkShare的index_vix或类似接口
        df = ak.index_vix()  # 占位，需验证实际函数
        # 处理...
        latest_vix = 18.5  # 占位
        
        return {
            "latest": latest_vix,
            "level": "高度恐慌" if latest_vix > 25 else ("中等恐慌" if latest_vix > 15 else "正常"),
            "interpretation": "VIX上升表明美股投资者恐慌情绪上升"
        }
    except Exception as e:
        return {"error": str(e)}


# ===== 8. 产业链关键指标映射 =====
def get_industry_linkage_data(industry_name: str, end_date: str) -> Dict:
    """
    根据行业返回产业链关键指标
    
    Args:
        industry_name: 行业名称（如"半导体"、"石油开采"）
    
    Returns:
        {
            "key_commodities": {...},
            "related_indices": {...},
            "news_signals": [...]
        }
    """
    LINKAGE_MAP = {
        "半导体": {
            "commodities": ["DRAM价格", "NAND价格"],  # 需自行维护价格源
            "indices": ["费城半导体指数"],  # ^SOX
            "keywords": ["AI", "ChatGPT", "英伟达", "台积电"]
        },
        "石油开采": {
            "commodities": ["oil_brent", "oil_wti"],
            "indices": [],
            "keywords": ["OPEC", "美伊关系", "中东"]
        },
        "黄金": {
            "commodities": ["gold"],
            "indices": [],
            "keywords": ["避险", "美债收益率"]
        },
        "新能源车": {
            "commodities": ["锂价"],  # 需自行维护
            "indices": ["特斯拉股价"],  # TSLA
            "keywords": ["特斯拉", "新能源补贴"]
        }
    }
    
    if industry_name not in LINKAGE_MAP:
        return {"error": f"行业 {industry_name} 暂无产业链映射"}
    
    config = LINKAGE_MAP[industry_name]
    result = {}
    
    # 获取商品价格
    if config["commodities"]:
        result["key_commodities"] = get_commodity_prices(config["commodities"], end_date)
    
    # 获取相关指数
    if config["indices"]:
        # 需要识别是国内指数还是国际指数
        result["related_indices"] = get_global_indices(config["indices"], end_date)
    
    # 新闻关键词统计（需要结合news_analyst的数据）
    result["keywords_for_news"] = config["keywords"]
    
    return result


# ===== 9. 央行政策数据 =====
@retry_with_timeout()
def get_monetary_policy(end_date: str) -> Dict:
    """
    获取央行政策利率
    
    Returns:
        {
            "mlf_rate": 2.50,
            "lpr_1y": 3.45,
            "lpr_5y": 4.20,
            "change_from_last_month": 0.0,
            "policy_stance": "宽松" / "中性" / "紧缩"
        }
    """
    try:
        # ⚠️ 需查找Tushare或AkShare的MLF/LPR接口
        # 可能需要从中国人民银行网站爬取
        df = ak.macro_china_lpr()  # 占位，需验证
        # 处理...
        return {
            "lpr_1y": 3.45,
            "change_from_last_month": 0.0,
            "policy_stance": "中性"
        }
    except Exception as e:
        return {"error": str(e)}
```

#### 4.1.3 数据压缩策略（降低Token消耗）

```python
def compress_timeseries_data(df: pd.DataFrame, key_column: str = 'close', 
                             keep_days: int = 5) -> Dict:
    """
    压缩时间序列数据，只保留关键信息
    
    策略：
    1. 只保留最近N天的数据点
    2. 计算技术指标（MA/RSI/MACD）替代原始K线
    3. 标注关键转折点（突破/跌破重要均线）
    
    Args:
        df: 原始DataFrame
        key_column: 关键列名（如'close'）
        keep_days: 保留天数
    
    Returns:
        {
            "latest": 3500.0,
            "change_pct_1d": -1.2,
            "change_pct_5d": -3.5,
            "ma5": 3520.0,
            "ma20": 3480.0,
            "ma_signal": "死叉" / "金叉" / "中性",
            "key_events": ["1月10日跌破MA20支撑"]
        }
    """
    if df.empty:
        return {"error": "无数据"}
    
    df = df.sort_values('trade_date').tail(keep_days + 20)  # 多取20天用于计算MA
    
    # 计算技术指标
    df['ma5'] = df[key_column].rolling(5).mean()
    df['ma20'] = df[key_column].rolling(20).mean()
    
    latest = df.iloc[-1]
    prev_5d = df.iloc[-6] if len(df) >= 6 else df.iloc[0]
    
    # 判断均线信号
    ma_signal = "中性"
    if latest['ma5'] > latest['ma20'] and df.iloc[-2]['ma5'] <= df.iloc[-2]['ma20']:
        ma_signal = "金叉"
    elif latest['ma5'] < latest['ma20'] and df.iloc[-2]['ma5'] >= df.iloc[-2]['ma20']:
        ma_signal = "死叉"
    
    # 检测关键事件
    key_events = []
    for i in range(len(df) - 5, len(df)):
        if i < 1:
            continue
        curr = df.iloc[i]
        prev = df.iloc[i-1]
        if prev[key_column] > prev['ma20'] and curr[key_column] < curr['ma20']:
            key_events.append(f"{curr['trade_date']}跌破MA20支撑")
        if prev[key_column] < prev['ma20'] and curr[key_column] > curr['ma20']:
            key_events.append(f"{curr['trade_date']}突破MA20压力")
    
    return {
        "latest": float(latest[key_column]),
        "change_pct_1d": float(latest['pct_chg']),
        "change_pct_5d": float((latest[key_column] - prev_5d[key_column]) / prev_5d[key_column] * 100),
        "ma5": float(latest['ma5']) if pd.notna(latest['ma5']) else None,
        "ma20": float(latest['ma20']) if pd.notna(latest['ma20']) else None,
        "ma_signal": ma_signal,
        "key_events": key_events[-2:],  # 只保留最近2个事件
        "recent_high": float(df[key_column].tail(20).max()),
        "recent_low": float(df[key_column].tail(20).min())
    }
```

---

### 4.2 国际新闻获取增强

修改现有文件：`tradingagents/dataflows/get_news.py`

```python
# 在现有get_news_for_stock函数基础上，新增：

@retry_with_backoff(max_retries=3)
def get_global_news(keywords: List[str], days_back: int = 7) -> List[Dict]:
    """
    获取国际重大新闻
    
    Args:
        keywords: 关键词列表，如 ["美联储", "中东局势", "中美关系"]
        days_back: 回看天数
    
    Returns:
        [
            {
                "title": "美联储维持利率不变",
                "date": "2026-01-15",
                "source": "Reuters",
                "summary": "...",
                "category": "央行政策",
                "relevance_to_a_share": "间接影响"
            },
            ...
        ]
    """
    # ⚠️ 需要找到可靠的国际新闻API
    # 可选方案：
    # 1. AkShare的news_cctv或news_economic（可能只有国内新闻）
    # 2. 第三方API（如NewsAPI.org，需要key）
    # 3. RSS源（路透社/彭博中文）
    
    try:
        # 方案1：使用AkShare（如果有国际新闻接口）
        df = ak.news_economic()  # 占位，需验证
        # 过滤关键词...
        
        # 方案2：使用RSS（更稳定）
        import feedparser
        news_list = []
        rss_sources = {
            "路透中文": "https://cn.reuters.com/rssFeed/CNTopGenNews",
            "华尔街日报中文": "https://cn.wsj.com/xml/rss/3_7087.xml"
        }
        
        for source, url in rss_sources.items():
            feed = feedparser.parse(url)
            for entry in feed.entries[:10]:  # 每个源取10条
                # 关键词匹配
                if any(kw in entry.title or kw in entry.summary for kw in keywords):
                    news_list.append({
                        "title": entry.title,
                        "date": entry.published,
                        "source": source,
                        "summary": entry.summary[:200],
                        "link": entry.link
                    })
        
        return news_list[:15]  # 最多返回15条
    except Exception as e:
        return [{"error": f"国际新闻获取失败: {str(e)}"}]


def categorize_news_impact(news_list: List[Dict], stock_industry: str) -> List[Dict]:
    """
    给新闻打标签：与个股的相关性
    
    逻辑：
    - 美联储政策 → 全市场影响（高相关）
    - 地缘政治 → 能源/军工/黄金（高相关），其他（中等相关）
    - 行业政策 → 对应行业（高相关）
    """
    IMPACT_RULES = {
        "央行政策": {"all": "高"},
        "地缘政治": {"石油开采": "高", "国防军工": "高", "黄金": "高", "default": "中"},
        "中美关系": {"半导体": "高", "科技": "高", "default": "中"}
    }
    
    for news in news_list:
        # 简单规则匹配（实际可用NLP模型）
        category = "其他"
        if any(kw in news["title"] for kw in ["美联储", "加息", "降息"]):
            category = "央行政策"
        elif any(kw in news["title"] for kw in ["中东", "伊朗", "冲突"]):
            category = "地缘政治"
        
        news["category"] = category
        news["relevance"] = IMPACT_RULES.get(category, {}).get(stock_industry, 
                                              IMPACT_RULES.get(category, {}).get("default", "低"))
    
    return news_list
```

---

## 五、Prompt设计

### 5.1 宏观分析师Prompt（增强版）

修改文件：`tradingagents/prompts/zh.py`

```python
macro_system_message = """你是宏观分析师，负责「自上而下」分析中国市场环境。

【核心职责】
1. 评估国内大盘指数走势（沪深300/创业板指/科创50）
2. 判断当前市场是普涨普跌还是结构性行情
3. 分析板块资金流向和轮动方向
4. 评估央行政策对流动性的影响

【分析框架】
一、大盘环境扫描（30字概括）
- 主要指数状态：上涨/下跌/震荡，是否突破关键位置
- 市场情绪温度：涨停/跌停家数比，判断恐慌或亢奋程度

二、央行政策与流动性
- MLF/LPR利率变动（如有）
- 市场流动性是否充裕

三、板块资金流动
- 哪些板块在吸金，哪些在失血
- 是否存在明显的板块轮动信号

四、对个股的影响路径
- 如果大盘处于下跌趋势，个股难以独善其身
- 如果个股所属板块正在失血，需警惕

【输出要求】
- 总字数控制在300字以内
- 必须明确给出"大盘环境"的定性判断（利好/中性/利空）
- 禁止模棱两可的表述，必须给出明确结论

【数据输入格式】
你将收到以下数据：
```json
{
    "domestic_indices": {
        "000300.SH": {"name": "沪深300", "close": 3500, "change_pct": -1.2, "ma5": 3520, "ma20": 3480, "trend": "下跌"},
        ...
    },
    "market_sentiment": {
        "limit_up_count": 15,
        "limit_down_count": 45,
        "up_down_ratio": 0.33,
        "interpretation": "恐慌情绪浓厚"
    },
    "monetary_policy": {
        "lpr_1y": 3.45,
        "change_from_last_month": 0.0,
        "policy_stance": "中性"
    },
    "sector_flows": [...]  # 已有数据
}
```
"""

# ===== 新增：国际市场分析师Prompt =====
global_market_system_message = """你是国际市场分析师，负责评估全球宏观环境对A股的潜在影响。

【核心职责】
1. 扫描主要国际股市（美日韩欧）的走势
2. 分析大类资产（黄金/原油/美债）的关键信号
3. 评估美联储等主要央行的政策周期
4. 判断风险传导路径：国际市场 → A股

【分析框架】
一、全球股市联动性（50字）
- 美股/日经/恒指的最新状态
- 与A股的历史相关性（美股跌A股是否跟跌）

二、大类资产信号（100字）
- 美债收益率：上升→全球流动性收紧→A股承压
- 黄金：上涨→避险情绪→利空风险资产
- 原油：暴跌→通缩预期或需求萎缩

三、央行政策周期（50字）
- 美联储当前处于加息/降息/暂停哪个阶段
- 对新兴市场的传导时滞（通常2-4周）

四、对A股的影响预期（50字）
- 正面/中性/负面
- 具体传导路径（如：美元走强→人民币贬值→外资流出→A股承压）

【输出要求】
- 总字数200-250字
- 必须给出明确的"国际环境对A股影响"结论（利好/中性/利空）
- 如果国际市场数据获取失败，说明"国际数据缺失，无法评估"

【数据输入格式】
```json
{
    "global_indices": {
        "SPX": {"name": "标普500", "close": 4500, "change_pct": -0.8, "ma20": 4520, "trend": "下跌"},
        ...
    },
    "major_assets": {
        "gold": {"close": 2050, "change_pct": 1.5, "trend": "上涨"},
        "oil_brent": {"close": 85, "change_pct": -2.3, "trend": "下跌"},
        "us_10y_yield": {"latest": 4.25, "change_bps": 10, "trend": "上行"},
        "dxy": {"close": 103.5, "change_pct": 0.5, "trend": "走强"}
    },
    "fed_policy": {
        "current_rate": 5.25,
        "market_expectation": "维持不变"
    }
}
```
"""

# ===== 增强：新闻分析师Prompt =====
news_system_message_enhanced = """你是新闻分析师，负责从信息面评估个股风险与机会。

【核心职责】（按优先级排序）
1. 国际重大新闻：美联储政策、地缘政治、中美关系（新增）
2. 行业政策新闻：针对个股所属行业的政策变动（新增）
3. 个股新闻：公司公告、业绩预告、重大事件（已有）
4. A股制度排查：限售解禁、股权质押等（已有）

【分析框架】
一、国际背景（新增，100字）
- 是否有重大国际事件影响全球风险偏好
- 传导路径：国际事件 → A股整体 → 个股
- 示例："美联储维持高利率→美元走强→外资流出A股→科技股承压"

二、行业政策（新增，80字）
- 个股所属行业是否有新政策出台
- 政策是利好还是利空
- 示例："芯片产业扶持政策→利好半导体板块→个股受益"

三、个股新闻（已有，保留原逻辑）
- 公司公告、业绩、重组等

四、制度风险（已有，保留原逻辑）
- 限售解禁、股权质押等

【输出要求】
- 总字数300-400字
- 必须按优先级排序（国际背景 > 行业政策 > 个股新闻 > 制度风险）
- 如果某个维度无重要新闻，简要说明"无重大新闻"即可，不要啰嗦

【数据输入格式】
```json
{
    "global_news": [
        {"title": "...", "date": "...", "category": "央行政策", "relevance": "高"},
        ...
    ],
    "industry_news": [
        {"title": "...", "industry": "半导体", "impact": "利好"},
        ...
    ],
    "stock_news": [...],  # 已有
    "institution_alerts": [...]  # 已有
}
```
"""

# ===== 增强：基本面分析师Prompt =====
fundamentals_system_message_enhanced = """你是基本面分析师，负责评估公司财务质量和产业链环境。

【核心职责】
1. 财务数据分析（已有，保留）
2. 产业链关键指标联动（新增）

【分析框架】
一、财务质量（已有，保留原逻辑）
- ROE、净利润增速、负债率等

二、产业链环境（新增，150字）
- 行业上下游关键指标变化
- 示例1（芯片股）："DRAM价格指数上涨8.5%→存储芯片需求回暖→公司营收有望改善"
- 示例2（石油股）："布伦特原油价格下跌10%→公司营收承压，但下游炼化成本下降"
- 示例3（新能源车）："碳酸锂价格企稳→电池成本可控→利好整车厂"

三、国际对标（新增，如有数据）
- 如果有国际同行业龙头数据（如分析宁德时代看特斯拉）
- 对标公司的股价走势和业绩变化

【输出要求】
- 财务分析部分保持原风格
- 产业链分析必须说明"关键指标变化 → 对公司的影响路径"
- 如果行业无产业链映射，说明"暂无产业链数据"

【数据输入格式】
```json
{
    "financials": {...},  # 已有
    "industry_linkage": {
        "industry_name": "半导体",
        "key_commodities": {
            "DRAM价格指数": {"latest": 105, "change_30d": 8.5, "trend": "上涨"}
        },
        "related_indices": {
            "费城半导体指数": {"close": 3500, "change_pct": 2.1}
        },
        "news_signals": [
            {"keyword": "AI", "mention_count_7d": 150, "sentiment": "积极"}
        ]
    }
}
```
"""

# ===== 增强：情绪分析师Prompt =====
social_media_system_message_enhanced = """你是情绪分析师，负责评估市场情绪和风险偏好。

【核心职责】
1. A股舆情分析（已有，保留）
2. 全球风险偏好（新增）

【分析框架】
一、全球风险偏好（新增，80字）
- VIX恐慌指数状态（<15正常, 15-25中等恐慌, >25高度恐慌）
- 解读：VIX上升→美股投资者恐慌→全球避险情绪→可能传导至A股

二、A股舆情（已有，保留原逻辑）
- 个股在社交媒体的讨论热度和情绪

【输出要求】
- 总字数200字以内
- 先说全球风险偏好，再说A股舆情

【数据输入格式】
```json
{
    "global_risk": {
        "vix_index": {
            "latest": 18.5,
            "level": "中等恐慌",
            "interpretation": "..."
        }
    },
    "a_share_sentiment": {...}  # 已有
}
```
"""
```

---

## 六、分析师节点实现

### 6.1 修改宏观分析师 `tradingagents/agents/analysts/macro_analyst.py`

```python
"""宏观分析师 - 增强版"""

from datetime import datetime, timedelta
from tradingagents.agents.utils.macro_data_tools import (
    get_cn_index_data,
    get_market_sentiment,
    get_monetary_policy
)
from tradingagents.dataflows.sector_flows import get_sector_flows  # 已有
from tradingagents.prompts.zh import macro_system_message

def macro_analyst_node(state: dict) -> dict:
    """
    宏观分析师节点（增强版）
    
    新增数据：
    - 国内大盘指数
    - 市场情绪
    - 央行政策
    """
    stock_code = state["stock_code"]
    curr_date = state.get("current_date", datetime.now().strftime('%Y-%m-%d'))
    
    # 并行获取数据（减少等待时间）
    import concurrent.futures
    
    def fetch_indices():
        return get_cn_index_data(
            index_codes=['000300.SH', '399006.SZ', '000688.SH', '000001.SH', '399001.SZ'],
            end_date=curr_date.replace('-', ''),
            look_back_days=5
        )
    
    def fetch_