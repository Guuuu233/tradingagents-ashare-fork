# TradingAgents-AShare 阶段二施工计划

**制定日期**：2026-08-20  
**执行团队**：Multica "专业团队"  
**项目主管**：David Liu  
**技术总监**：Hermes Agent

---

## 📋 总体目标

实现**产业链数据层 MVP**，让分析师能真正"看到"产业链指标：
- 覆盖 2 个核心行业（消费电子、新能源车）
- 接入免费数据源（LME铜价、Yahoo Finance 三星电子股价）
- 允许"数据缺失"标注（如碳酸锂价格暂无 API）

**预计总工时**：34小时（4.25个工作日）

---

## 🎯 里程碑（Milestones）

### M1：数据结构定义（4小时）✅ 必须先完成
### M2：数据采集器实现（12小时）→ 依赖 M1
### M3：DataCollector 集成（4小时）→ 依赖 M2
### M4：分析师 Prompt 注入（4小时）→ 依赖 M3
### M5：单元测试（6小时）→ 依赖 M4
### M6：端到端验证（4小时）→ 依赖 M5

---

## 📐 M1：数据结构定义（4小时）

### 目标
创建 `tradingagents/dataflows/industry_linkage.py`，定义：
1. `IndustryLinkageIndicator` 类（单个指标）
2. `IndustryLinkage` 类（某个行业的完整映射）
3. `INDUSTRY_LINKAGE_MAP` 字典（5个行业配置，但只实现2个）

### 验收标准
- [x] 文件存在：`tradingagents/dataflows/industry_linkage.py`
- [x] 类定义完整：`IndustryLinkageIndicator` + `IndustryLinkage`
- [x] `INDUSTRY_LINKAGE_MAP` 包含 2 个行业：
  - `"消费电子"`：上游（LME铜价）、下游（全球智能手机出货量，标注"手动"）、国际对标（三星电子股价）
  - `"新能源车"`：上游（碳酸锂价格，标注"待接入API"）、下游（新能源车渗透率，标注"手动"）、国际对标（特斯拉交付量，标注"手动"）
- [x] 所有字段都有类型注解（使用 `pydantic.BaseModel`）
- [x] 文件顶部有清晰的 docstring 说明用途

### 参考代码
见 **项目加强方案 §4.4 子任务1**（第 246-274 行）

### 负责人
**资深开发1**

### 产物
- `tradingagents/dataflows/industry_linkage.py`（新文件）
- 自测：`python3 -c "from tradingagents.dataflows.industry_linkage import INDUSTRY_LINKAGE_MAP; print(INDUSTRY_LINKAGE_MAP.keys())"` → 输出 `dict_keys(['消费电子', '新能源车'])`

---

## 🔌 M2：数据采集器实现（12小时）

### 目标
创建 `tradingagents/dataflows/providers/industry_linkage_provider.py`，实现：
1. `IndustryLinkageProvider` 类
2. `get_industry_linkage(industry)` 方法（带缓存）
3. `_fetch_indicator(config)` 方法（根据配置采集单个指标）

### 核心逻辑
```python
def _fetch_indicator(self, config: IndustryLinkageIndicator) -> Dict:
    if config.name == "LME铜价":
        # 调用 akshare.futures_lme_daily(symbol="铜")
        # 计算月环比、季度环比、趋势
    elif config.name == "三星电子股价":
        # 调用 yfinance.Ticker("005930.KS").history(period="3mo")
        # 计算月环比、季度环比、趋势
    elif config.name == "碳酸锂价格":
        # 返回 {"trend": "数据缺失", "confidence": "低（待接入API）"}
    else:
        # 返回 {"trend": "数据缺失", "confidence": "低（待实现）"}
```

### 验收标准
- [x] 文件存在：`tradingagents/dataflows/providers/industry_linkage_provider.py`
- [x] `IndustryLinkageProvider` 类实现完整
- [x] **LME铜价**能成功采集（通过 akshare）
- [x] **三星电子股价**能成功采集（通过 yfinance）
- [x] 碳酸锂价格返回"数据缺失"（不报错）
- [x] 缓存机制工作（TTL=1小时）
- [x] 所有异常被捕获，返回结构化错误（不中断分析）

### 参考代码
见 **项目加强方案 §4.4 子任务2**（第 275-380 行）

### 负责人
**资深开发1**（继续）

### 产物
- `tradingagents/dataflows/providers/industry_linkage_provider.py`（新文件）
- 自测脚本：
```python
from tradingagents.dataflows.providers.industry_linkage_provider import IndustryLinkageProvider
provider = IndustryLinkageProvider()
data = provider.get_industry_linkage("消费电子")
print(data["upstream_cost"][0]["name"])  # 应输出 "LME铜价"
print(data["upstream_cost"][0]["current_value"])  # 应输出实际数字（如 9123.5）
```

---

## 🔗 M3：DataCollector 集成（4小时）

### 目标
修改 `tradingagents/graph/data_collector.py`：
1. 导入 `IndustryLinkageProvider`
2. 在 `__init__` 中初始化 `self.industry_linkage_provider`
3. 在 `_fetch_all` 中调用 `_map_stock_to_industry` + `get_industry_linkage`
4. 实现 `_map_stock_to_industry` 方法（硬编码 10 只股票映射）

### 关键修改点
```python
# 第 50 行附近，新增导入
from tradingagents.dataflows.providers.industry_linkage_provider import IndustryLinkageProvider

# 第 260 行附近（_fetch_all 函数内），新增产业链数据采集
def _fetch_all(ticker: str, trade_date: str) -> Dict[str, Any]:
    # ... 现有的宏观数据、个股数据采集 ...
    
    # 新增：根据个股所属行业，采集产业链联想数据
    industry = _map_stock_to_industry(ticker)
    if industry:
        provider = IndustryLinkageProvider()
        result["industry_linkage"] = provider.get_industry_linkage(industry)
    else:
        result["industry_linkage"] = None
    
    return result

def _map_stock_to_industry(ticker: str) -> Optional[str]:
    """根据股票代码映射到核心行业（硬编码，MVP）"""
    industry_map = {
        "000725.SZ": "消费电子",  # 京东方A
        "000100.SZ": "消费电子",  # TCL科技
        "300750.SZ": "新能源车",  # 宁德时代
        "002594.SZ": "新能源车",  # 比亚迪
        # ... 其余 6 只股票
    }
    return industry_map.get(ticker)
```

### 验收标准
- [x] `data_collector.py` 成功导入 `IndustryLinkageProvider`（无 ImportError）
- [x] `_fetch_all` 返回的 dict 包含 `"industry_linkage"` 键
- [x] 分析 **京东方A (000725.SZ)** 时，`result["industry_linkage"]["industry_name"]` == `"消费电子/半导体显示"`
- [x] 分析 **宁德时代 (300750.SZ)** 时，`result["industry_linkage"]["industry_name"]` == `"新能源车/动力电池"`
- [x] 分析 **招商银行 (600036.SH)** 时，`result["industry_linkage"]` == `None`（未映射行业）

### 参考代码
见 **项目加强方案 §4.4 子任务3**（第 381-445 行）

### 负责人
**资深开发2**（与 M2 并行，但必须等 M2 完成后才能测试）

### 产物
- 修改 `tradingagents/graph/data_collector.py`
- 自测：
```python
from tradingagents.graph.data_collector import _fetch_all
result = _fetch_all("000725.SZ", "20260820")
print(result["industry_linkage"]["industry_name"])  # 应输出 "消费电子/半导体显示"
```

---

## 📝 M4：分析师 Prompt 注入（4小时）

### 目标
修改分析师调用逻辑，将 `industry_linkage` 数据格式化成可读文本，注入到 prompt

### 关键修改点
1. 创建格式化函数 `format_industry_linkage_for_prompt(industry_linkage)`
2. 在宏观分析师、基本面分析师调用时注入

### 验收标准
- [x] 宏观分析师报告中出现 `【产业链联想数据】：消费电子/半导体显示` 段落
- [x] 段落包含：上游成本端指标、下游需求端指标、国际对标指标、政策催化关键词
- [x] 数据缺失时显示 `【数据缺失】碳酸锂价格：待接入API`

### 参考代码
见 **项目加强方案 §4.4 子任务4**（第 446-512 行）

### 负责人
**资深开发2**（继续）

### 产物
- 修改相关 Agent 调用逻辑
- 自测：分析京东方A，检查宏观分析师报告开头是否有产业链数据段落

---

## 🧪 M5：单元测试（6小时）

### 目标
创建 `tests/test_industry_linkage.py`，覆盖：
1. `test_get_industry_linkage_consumer_electronics()`：测试消费电子行业数据采集
2. `test_get_industry_linkage_new_energy()`：测试新能源车行业数据采集
3. `test_cache()`：测试缓存机制
4. `test_unknown_industry()`：测试未配置行业
5. `test_data_collector_integration()`：测试 DataCollector 集成

### 验收标准
- [x] 文件存在：`tests/test_industry_linkage.py`
- [x] 至少 5 个测试用例
- [x] 运行 `pytest tests/test_industry_linkage.py -v` → 全部通过（0 failed）
- [x] 网络失败时测试能优雅降级（mock 或标记为 skip）

### 参考代码
见 **项目加强方案 §4.4 子任务5**（第 513-570 行）

### 负责人
**代码运维测试员**

### 产物
- `tests/test_industry_linkage.py`（新文件）
- 测试运行日志

---

## ✅ M6：端到端验证（4小时）

### 目标
选择 **京东方A (000725.SZ)** 作为测试标的，执行完整分析并验证：

### 验收标准（定性）
- [x] 宏观分析师报告中出现 `【产业链联想数据】` 段落
- [x] 段落包含 LME铜价实际数据（如"9123.50 美元/吨，月环比+2.3%"）
- [x] 段落包含三星电子股价实际数据（如"52000 韩元，月环比-1.5%"）
- [x] 基本面分析师报告中引用了产业链指标（如"根据宏观分析师报告，LME铜价月环比+2.3%"）
- [x] 基本面分析师报告中出现量化传导分析（如"铜价上涨2.3% → 原材料成本承压约0.1个百分点"）

### 验收标准（定量）
对比 **DAV-191（当前主干）vs DAV-196（本轮完成后）**：

| 指标 | DAV-191 基线 | DAV-196 目标 | 实际 | 达标 |
|:---|:---:|:---:|:---:|:---:|
| 基本面分析师报告字数 | ~3000字符 | 3500-4000字符 | _____ | ☐ |
| 基本面"产业链"段落字数 | ~200字符 | 800-1000字符 | _____ | ☐ |
| 宏观分析师报告出现"传导"关键词次数 | 5-8次 | 12-15次 | _____ | ☐ |
| 基本面分析师报告出现"LME铜价"或"三星显示"等具体指标 | 0次 | ≥2次 | _____ | ☐ |

### 负责人
**代码审核员**（独立验证）

### 产物
- 验收报告：`work/2026-08-20-dav-196-acceptance-report.md`
- 京东方A 完整分析报告（保存到 `work/dav196-validation-京东方A.json`）

---

## 🚧 施工纪律（AGENTS.md 规范）

### 代码规范
1. **按列名取数，禁止位置切片**
2. **失败时返回明确提示，禁止返回 None**
3. **所有修改必须有单元测试**
4. **不自行提交主分支，展示 diff 等 David 确认**

### Git 规范
1. 每个 Milestone 单独分支：`feature/dav-196-m1-data-structure`
2. Commit message 中文，说明为什么改
3. 只修改应该修改的文件（`git diff --name-only` 检查）
4. 推送到远端后 @项目调度助手 汇报

### 测试规范
1. 单元测试必须能在 CI 环境运行（不依赖外部服务）
2. 网络调用必须有 mock 或 `@pytest.mark.network`
3. 测试用例命名清晰：`test_<功能>_<场景>_<预期结果>`

---

## 📊 进度跟踪

### M1：数据结构定义
- [ ] 资深开发1：开始实现
- [ ] 资深开发1：自测通过
- [ ] 代码审核员：review 通过
- [ ] 推送到远端分支
- [ ] @项目调度助手

### M2：数据采集器实现
- [ ] 资深开发1：开始实现
- [ ] 资深开发1：自测通过（LME铜价、三星电子股价）
- [ ] 代码审核员：review 通过
- [ ] 推送到远端分支
- [ ] @项目调度助手

### M3：DataCollector 集成
- [ ] 资深开发2：开始实现
- [ ] 资深开发2：自测通过（京东方A、宁德时代）
- [ ] 代码审核员：review 通过
- [ ] 推送到远端分支
- [ ] @项目调度助手

### M4：分析师 Prompt 注入
- [ ] 资深开发2：开始实现
- [ ] 资深开发2：自测通过（宏观分析师报告出现产业链数据）
- [ ] 代码审核员：review 通过
- [ ] 推送到远端分支
- [ ] @项目调度助手

### M5：单元测试
- [ ] 代码运维测试员：编写测试
- [ ] 代码运维测试员：全部测试通过
- [ ] 独立代码审核员：review 测试质量
- [ ] 推送到远端分支
- [ ] @项目调度助手

### M6：端到端验证
- [ ] 代码审核员：执行完整分析
- [ ] 代码审核员：填写验收标准表格
- [ ] 代码审核员：撰写验收报告
- [ ] @项目主管（David）最终确认

---

## 🎯 成功标准

**阶段二算完成**当且仅当：
1. ✅ 所有 6 个 Milestone 都通过验收
2. ✅ 京东方A 分析报告中真实出现 LME铜价、三星电子股价等产业链指标
3. ✅ 基本面分析师能基于这些指标做量化传导分析
4. ✅ 全量自动化测试 100% 通过
5. ✅ David 最终确认：报告质量明显提升

**不算完成**的情况：
- ❌ 只是写了代码，但测试失败
- ❌ 测试通过，但分析报告中看不到产业链数据
- ❌ 能看到数据，但分析师没有引用（说明 Prompt 注入失败）
- ❌ 能引用数据，但传导分析仍是空谈（说明 Prompt 指令不够强）

---

**施工计划制定人**：Hermes Agent  
**批准人**：David Liu  
**执行团队**：Multica "专业团队"  
**开始日期**：2026-08-20  
**预计完成日期**：2026-08-26（5个工作日）
