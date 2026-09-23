# DAV-1225 corrected B0 复跑汇总

## 输入校验
- snapshot SHA256 vs manifest：10/10 全匹配
- state 数：50（SHA256 已记录于 out/run_all.json）

## 1. snapshot parser（按 header 取列）

| sample | symbol | 实际表头 | bars | 指标字段 | price_range |
|---|---|---|---|---|---|
| s01 | 600893.SH | `date,low,close,volume,open,high` | 243 | 10 | (31.86, 63.67) |
| s02 | 601899.SH | `date,low,close,volume,open,high` | 243 | 10 | (19.28, 43.74) |
| s03 | 601138.SH | `date,low,close,volume,open,high` | 242 | 10 | (34.51, 83.98) |
| s04 | 300308.SZ | `date,low,close,volume,open,high` | 243 | 10 | (209.53, 1416.88) |
| s05 | 000538.SZ | `date,low,close,volume,open,high` | 243 | 10 | (46.29, 57.89) |
| s06 | 002475.SZ | `date,low,close,volume,open,high` | 243 | 10 | (37.77, 81.58) |
| s07 | 600011.SH | `date,low,close,volume,open,high` | 242 | 10 | (6.31, 9.25) |
| s08 | 300274.SZ | `close,low,high,open,date,volume` | 243 | 10 | (90.12, 207.37) |
| s09 | 000725.SZ | `date,low,close,volume,open,high` | 243 | 10 | (3.76, 9.5) |
| s10 | 300015.SZ | `date,low,close,volume,open,high` | 243 | 10 | (7.73, 13.7) |

缺列自检（fail-closed）：`OK fail-closed: 000001.SZ: stock_data 缺必需列 ['close']（实际表头 ['date', 'open', 'high', 'low', 'volume']），fail-closed`

注：10/10 实际表头为 `date,low,close,volume,open,high` 时——即 DAV-1223 B0 按固定列号解析正是把 low/close/volume 错读为 open/high/low 的根因。

## 2. W0 复现（trunk 原样重跑）

- 总 violation：1613（存档 1613）——逐 run 逐 kind 比对：全部一致

- 逐 kind：{'decision_driving_missing_as_of': 487, 'decision_driving_unspecified_basis': 473, 'cross_basis_coordinate_mix': 556, 'unbacked_executable_level': 39, 'invalid_conversion': 45, 'executable_level_wrong_basis': 13}
- blocked 50/50；每 run min 6 / 中位数 26.0（标准中位数，偶数样本取两中值均值）/ max 91

## 3. 逐条复核

### 3.1 unspecified decision-driving refs（473 条）

- real_coordinate_source_unbacked: 150
- non_price_false_extraction: 136
- derived_valuation_estimate: 66
- real_coordinate_source_backed: 50
- foreign_quote: 44
- ambiguous: 27

### 3.1b 抽样人工复核精度（R1，manual_review.json 入库）

| 类 | 抽样数 | correct | wrong | unsure | 精度 |
|---|---|---|---|---|---|
| ambiguous | 27 | 0 | 24 | 3 | 0.0 |
| derived_valuation_estimate | 30 | 25 | 4 | 1 | 0.833 |
| foreign_quote | 30 | 24 | 1 | 5 | 0.8 |
| non_price_false_extraction | 30 | 30 | 0 | 0 | 1.0 |
| real_coordinate_source_backed | 30 | 28 | 0 | 2 | 0.933 |
| real_coordinate_source_unbacked | 30 | 19 | 7 | 4 | 0.633 |

### 3.2 typed disclosure（111 条）

- verdict: {'false': 97, 'true': 14}
- by_type: block_trade=false:52; block_trade=true:13; dragon_tiger_list=false:3; issuance=false:12; repurchase=false:5; repurchase=true:1; shareholder_decrease=false:21; shareholder_increase=false:4
- 剩余 ambiguous（0 条，逐条列理由）:

### 3.3 cross_basis 556 条重算
- {'all_partners_false_disclosure': 498, 'all_true_disclosure': 9, 'some_false': 49}

### 3.4 invalid_conversion（45 条）
- {'volume_or_atr_arithmetic': 1, 'valuation_arithmetic': 44}

### 3.5 executable level（52 条）
- {'markdown_list_number': 13, 'real_executable_price': 22, 'percentage': 14, 'other_false_positive': 3}

> 口径注记（R4）：中位数一律为标准中位数（偶数样本取两中值均值）。
> W1 层 executable 类计数上升机制：W1 删除伪命中 ref 后，原先命中
> `executable_level_wrong_basis` 的价位失去候选 ref，迁移为
> `unbacked_executable_level`；同时 violation 去重键
> (kind, ref_ids, detail) 中 ref_ids/bases 变化使少量条目重新计数。


## 4. W0–W4 what-if 分层

| 层 | 总 violation | blocked | pass/clean | 分 kind | blocked→pass |
|---|---|---|---|---|---|
| W0 | 1613 | 50 | 0 | {'decision_driving_missing_as_of': 487, 'decision_driving_unspecified_basis': 473, 'cross_basis_coordinate_mix': 556, 'unbacked_executable_level': 39, 'invalid_conversion': 45, 'executable_level_wrong_basis': 13} | — |
| W1 | 749 | 50 | 0 | {'decision_driving_unspecified_basis': 336, 'decision_driving_missing_as_of': 328, 'unbacked_executable_level': 42, 'executable_level_wrong_basis': 19, 'cross_basis_coordinate_mix': 24} | 无 |
| W2 | 631 | 46 | 4 | {'decision_driving_unspecified_basis': 275, 'decision_driving_missing_as_of': 271, 'unbacked_executable_level': 42, 'executable_level_wrong_basis': 19, 'cross_basis_coordinate_mix': 24} | s03__r3, s05__r4, s10__r3, s10__r4 |
| W3 | 597 | 45 | 5 | {'decision_driving_unspecified_basis': 276, 'decision_driving_missing_as_of': 272, 'executable_level_wrong_basis': 19, 'unbacked_executable_level': 6, 'cross_basis_coordinate_mix': 24} | s01__r4 |
| W4 | 486 | 44 | 6 | {'decision_driving_unspecified_basis': 220, 'decision_driving_missing_as_of': 216, 'executable_level_wrong_basis': 19, 'unbacked_executable_level': 6, 'cross_basis_coordinate_mix': 25} | s05__r2 |

### W1 重点样本（s04/s06/s08/s09 各 5 replicate）
- s04__r1: blocked {'decision_driving_unspecified_basis': 6, 'decision_driving_missing_as_of': 4}
- s04__r2: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 5}
- s04__r3: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s04__r4: blocked {'decision_driving_unspecified_basis': 1, 'decision_driving_missing_as_of': 1}
- s04__r5: blocked {'decision_driving_unspecified_basis': 6, 'decision_driving_missing_as_of': 6, 'unbacked_executable_level': 1}
- s06__r1: blocked {'decision_driving_unspecified_basis': 3, 'decision_driving_missing_as_of': 3}
- s06__r2: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06__r3: blocked {'decision_driving_unspecified_basis': 23, 'decision_driving_missing_as_of': 23, 'cross_basis_coordinate_mix': 6, 'unbacked_executable_level': 2}
- s06__r4: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 5, 'cross_basis_coordinate_mix': 10}
- s06__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r2: blocked {'decision_driving_unspecified_basis': 14, 'decision_driving_missing_as_of': 14, 'unbacked_executable_level': 4, 'executable_level_wrong_basis': 3}
- s08__r3: blocked {'decision_driving_unspecified_basis': 3, 'decision_driving_missing_as_of': 3}
- s08__r4: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 5, 'unbacked_executable_level': 1}
- s08__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s09__r1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s09__r2: blocked {'decision_driving_unspecified_basis': 7, 'decision_driving_missing_as_of': 7, 'unbacked_executable_level': 2}
- s09__r3: blocked {'decision_driving_unspecified_basis': 12, 'decision_driving_missing_as_of': 11, 'unbacked_executable_level': 1, 'executable_level_wrong_basis': 1}
- s09__r4: blocked {'decision_driving_unspecified_basis': 9, 'decision_driving_missing_as_of': 9, 'unbacked_executable_level': 4, 'executable_level_wrong_basis': 1}
- s09__r5: blocked {'decision_driving_unspecified_basis': 6, 'decision_driving_missing_as_of': 6, 'unbacked_executable_level': 2}

### W2 重点样本（s04/s06/s08/s09 各 5 replicate）
- s04__r1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 2}
- s04__r2: blocked {'decision_driving_unspecified_basis': 3, 'decision_driving_missing_as_of': 3}
- s04__r3: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s04__r4: blocked {'decision_driving_unspecified_basis': 1, 'decision_driving_missing_as_of': 1}
- s04__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'unbacked_executable_level': 1}
- s06__r1: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s06__r2: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06__r3: blocked {'decision_driving_unspecified_basis': 23, 'decision_driving_missing_as_of': 23, 'cross_basis_coordinate_mix': 6, 'unbacked_executable_level': 2}
- s06__r4: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'cross_basis_coordinate_mix': 10}
- s06__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r2: blocked {'decision_driving_unspecified_basis': 11, 'decision_driving_missing_as_of': 11, 'unbacked_executable_level': 4, 'executable_level_wrong_basis': 3}
- s08__r3: blocked {'decision_driving_unspecified_basis': 1, 'decision_driving_missing_as_of': 1}
- s08__r4: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'unbacked_executable_level': 1}
- s08__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s09__r1: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s09__r2: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 5, 'unbacked_executable_level': 2}
- s09__r3: blocked {'decision_driving_unspecified_basis': 12, 'decision_driving_missing_as_of': 11, 'unbacked_executable_level': 1, 'executable_level_wrong_basis': 1}
- s09__r4: blocked {'decision_driving_unspecified_basis': 8, 'decision_driving_missing_as_of': 8, 'unbacked_executable_level': 4, 'executable_level_wrong_basis': 1}
- s09__r5: blocked {'decision_driving_unspecified_basis': 6, 'decision_driving_missing_as_of': 6, 'unbacked_executable_level': 2}

W2 pass run 的 derived_estimate refs：
- s03__r3（3 条）：
    - pr-145 17.2 [fundamentals_report] - 在利润大幅滑落至190亿元基准下，市场给予制造业防守性估值中枢18-20倍PE，对应合理市值区间约3,420-3,800亿元，折合每股股价支撑位约在17.2-19.1元（较现价
    - pr-146 19.1 [fundamentals_report] - 在利润大幅滑落至190亿元基准下，市场给予制造业防守性估值中枢18-20倍PE，对应合理市值区间约3,420-3,800亿元，折合每股股价支撑位约在17.2-19.1元（较现价
    - pr-148 70.0 [fundamentals_report] - **高增长兑现门槛**：当前市值若要维持在65-70元平台，要求2026年全年净利润需达到450亿元以上（同比增速需维持在28%以上），2027年进一步站上550亿元
- s05__r4（2 条）：
    - pr-122 38.0 [fundamentals_report] - 按历史极端低位估值（12-14倍 PE）推演，市值底部支撑位在300-390亿元之间（对应当前股价折算底线支撑在38-42元区间），极端回撤空间有限，资产具备强反脆弱性
    - pr-123 42.0 [fundamentals_report] - 按历史极端低位估值（12-14倍 PE）推演，市值底部支撑位在300-390亿元之间（对应当前股价折算底线支撑在38-42元区间），极端回撤空间有限，资产具备强反脆弱性
- s10__r3（2 条）：
    - pr-133 6.0 [fundamentals_report] 若市场悲观情绪杀估值至 25 倍，对应极限估值支撑位在 6.00 - 6.50 元区间
    - pr-134 6.5 [fundamentals_report] 若市场悲观情绪杀估值至 25 倍，对应极限估值支撑位在 6.00 - 6.50 元区间
- s10__r4（1 条）：
    - pr-104 6.0 [fundamentals_report] * 对应 2026-08-21 现价 8.33 元（总股本 93.26 亿股，测算总市值约 776.8 亿元），极限极端情景下 PE 估值将被动推升至 57.3 倍，在此极端假设下

### W3 重点样本（s04/s06/s08/s09 各 5 replicate）
- s04__r1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 2}
- s04__r2: blocked {'decision_driving_unspecified_basis': 3, 'decision_driving_missing_as_of': 3}
- s04__r3: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s04__r4: blocked {'decision_driving_unspecified_basis': 1, 'decision_driving_missing_as_of': 1}
- s04__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s06__r1: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s06__r2: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06__r3: blocked {'decision_driving_unspecified_basis': 23, 'decision_driving_missing_as_of': 23, 'cross_basis_coordinate_mix': 6}
- s06__r4: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'cross_basis_coordinate_mix': 10}
- s06__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r2: blocked {'decision_driving_unspecified_basis': 11, 'decision_driving_missing_as_of': 11, 'executable_level_wrong_basis': 3}
- s08__r3: blocked {'decision_driving_unspecified_basis': 1, 'decision_driving_missing_as_of': 1}
- s08__r4: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s09__r1: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s09__r2: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 5}
- s09__r3: blocked {'decision_driving_unspecified_basis': 12, 'decision_driving_missing_as_of': 11, 'executable_level_wrong_basis': 1}
- s09__r4: blocked {'decision_driving_unspecified_basis': 9, 'decision_driving_missing_as_of': 9, 'unbacked_executable_level': 2, 'executable_level_wrong_basis': 2}
- s09__r5: blocked {'decision_driving_unspecified_basis': 7, 'decision_driving_missing_as_of': 7, 'unbacked_executable_level': 1}

W3 pass run 的 derived_estimate refs：
- s01__r4（4 条）：
    - pr-126 1.8 [fundamentals_report] 历史PB最低点在1.8~2.0倍左右，对应极端悲观股价支撑位约为 **27.15 ~ 30.16 元**
    - pr-127 27.15 [fundamentals_report] 历史PB最低点在1.8~2.0倍左右，对应极端悲观股价支撑位约为 **27.15 ~ 30.16 元**
    - pr-128 30.16 [fundamentals_report] 历史PB最低点在1.8~2.0倍左右，对应极端悲观股价支撑位约为 **27.15 ~ 30.16 元**
    - pr-174 27.15 [investment_plan] 在宏观滞胀或海外脱钩极端情景下，航发动力依托 402.10 亿元归母净资产与垄断总装地位，抗压测底线坚固（极值 PB 1.8 倍对应 27.15 元，短线颈线 34.40 元具备极
- s03__r3（3 条）：
    - pr-146 17.2 [fundamentals_report] - 在利润大幅滑落至190亿元基准下，市场给予制造业防守性估值中枢18-20倍PE，对应合理市值区间约3,420-3,800亿元，折合每股股价支撑位约在17.2-19.1元（较现价
    - pr-147 19.1 [fundamentals_report] - 在利润大幅滑落至190亿元基准下，市场给予制造业防守性估值中枢18-20倍PE，对应合理市值区间约3,420-3,800亿元，折合每股股价支撑位约在17.2-19.1元（较现价
    - pr-149 70.0 [fundamentals_report] - **高增长兑现门槛**：当前市值若要维持在65-70元平台，要求2026年全年净利润需达到450亿元以上（同比增速需维持在28%以上），2027年进一步站上550亿元
- s05__r4（2 条）：
    - pr-123 38.0 [fundamentals_report] - 按历史极端低位估值（12-14倍 PE）推演，市值底部支撑位在300-390亿元之间（对应当前股价折算底线支撑在38-42元区间），极端回撤空间有限，资产具备强反脆弱性
    - pr-124 42.0 [fundamentals_report] - 按历史极端低位估值（12-14倍 PE）推演，市值底部支撑位在300-390亿元之间（对应当前股价折算底线支撑在38-42元区间），极端回撤空间有限，资产具备强反脆弱性
- s10__r3（2 条）：
    - pr-135 6.0 [fundamentals_report] 若市场悲观情绪杀估值至 25 倍，对应极限估值支撑位在 6.00 - 6.50 元区间
    - pr-136 6.5 [fundamentals_report] 若市场悲观情绪杀估值至 25 倍，对应极限估值支撑位在 6.00 - 6.50 元区间
- s10__r4（1 条）：
    - pr-105 6.0 [fundamentals_report] * 对应 2026-08-21 现价 8.33 元（总股本 93.26 亿股，测算总市值约 776.8 亿元），极限极端情景下 PE 估值将被动推升至 57.3 倍，在此极端假设下

### W4 重点样本（s04/s06/s08/s09 各 5 replicate）
- s04__r1: blocked {'decision_driving_unspecified_basis': 2}
- s04__r2: blocked {'decision_driving_unspecified_basis': 3, 'decision_driving_missing_as_of': 3}
- s04__r3: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s04__r4: blocked {'decision_driving_unspecified_basis': 1, 'decision_driving_missing_as_of': 1}
- s04__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s06__r1: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s06__r2: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06__r3: blocked {'decision_driving_unspecified_basis': 10, 'decision_driving_missing_as_of': 10, 'cross_basis_coordinate_mix': 7}
- s06__r4: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'cross_basis_coordinate_mix': 10}
- s06__r5: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s08__r1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r2: blocked {'decision_driving_unspecified_basis': 7, 'decision_driving_missing_as_of': 7, 'executable_level_wrong_basis': 3}
- s08__r3: blocked {'decision_driving_unspecified_basis': 1, 'decision_driving_missing_as_of': 1}
- s08__r4: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s08__r5: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s09__r1: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s09__r2: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 5}
- s09__r3: blocked {'decision_driving_unspecified_basis': 12, 'decision_driving_missing_as_of': 11, 'executable_level_wrong_basis': 1}
- s09__r4: blocked {'decision_driving_unspecified_basis': 7, 'decision_driving_missing_as_of': 7, 'unbacked_executable_level': 2, 'executable_level_wrong_basis': 2}
- s09__r5: blocked {'decision_driving_unspecified_basis': 7, 'decision_driving_missing_as_of': 7, 'unbacked_executable_level': 1}

W4 pass run 的 derived_estimate refs：
- s01__r4（4 条）：
    - pr-126 1.8 [fundamentals_report] 历史PB最低点在1.8~2.0倍左右，对应极端悲观股价支撑位约为 **27.15 ~ 30.16 元**
    - pr-127 27.15 [fundamentals_report] 历史PB最低点在1.8~2.0倍左右，对应极端悲观股价支撑位约为 **27.15 ~ 30.16 元**
    - pr-128 30.16 [fundamentals_report] 历史PB最低点在1.8~2.0倍左右，对应极端悲观股价支撑位约为 **27.15 ~ 30.16 元**
    - pr-174 27.15 [investment_plan] 在宏观滞胀或海外脱钩极端情景下，航发动力依托 402.10 亿元归母净资产与垄断总装地位，抗压测底线坚固（极值 PB 1.8 倍对应 27.15 元，短线颈线 34.40 元具备极
- s03__r3（3 条）：
    - pr-146 17.2 [fundamentals_report] - 在利润大幅滑落至190亿元基准下，市场给予制造业防守性估值中枢18-20倍PE，对应合理市值区间约3,420-3,800亿元，折合每股股价支撑位约在17.2-19.1元（较现价
    - pr-147 19.1 [fundamentals_report] - 在利润大幅滑落至190亿元基准下，市场给予制造业防守性估值中枢18-20倍PE，对应合理市值区间约3,420-3,800亿元，折合每股股价支撑位约在17.2-19.1元（较现价
    - pr-149 70.0 [fundamentals_report] - **高增长兑现门槛**：当前市值若要维持在65-70元平台，要求2026年全年净利润需达到450亿元以上（同比增速需维持在28%以上），2027年进一步站上550亿元
- s05__r2（1 条）：
    - pr-106 40.0 [fundamentals_report] - **估值支撑底线**：在 28 亿元的极限悲观净利润假设下，给予白马防御底线 18 倍 PE，对应极限防御市值约 504 亿元，折合股价防御底线在 38~40 元区间
- s05__r4（2 条）：
    - pr-123 38.0 [fundamentals_report] - 按历史极端低位估值（12-14倍 PE）推演，市值底部支撑位在300-390亿元之间（对应当前股价折算底线支撑在38-42元区间），极端回撤空间有限，资产具备强反脆弱性
    - pr-124 42.0 [fundamentals_report] - 按历史极端低位估值（12-14倍 PE）推演，市值底部支撑位在300-390亿元之间（对应当前股价折算底线支撑在38-42元区间），极端回撤空间有限，资产具备强反脆弱性
- s10__r3（2 条）：
    - pr-135 6.0 [fundamentals_report] 若市场悲观情绪杀估值至 25 倍，对应极限估值支撑位在 6.00 - 6.50 元区间
    - pr-136 6.5 [fundamentals_report] 若市场悲观情绪杀估值至 25 倍，对应极限估值支撑位在 6.00 - 6.50 元区间
- s10__r4（1 条）：
    - pr-105 6.0 [fundamentals_report] * 对应 2026-08-21 现价 8.33 元（总股本 93.26 亿股，测算总市值约 776.8 亿元），极限极端情景下 PE 估值将被动推升至 57.3 倍，在此极端假设下

## 5. 已知坏锚逐层结果

- s06 实锚 run：['s06__r1', 's06__r2', 's06__r3', 's06__r4', 's06__r5']
- 全部仍 blocked：True
- b188060f_fixture @ W0: blocked {'decision_driving_missing_as_of': 1, 'cross_basis_coordinate_mix': 1, 'executable_level_wrong_basis': 1}
- b188060f_fixture @ W1: blocked {'decision_driving_missing_as_of': 1, 'cross_basis_coordinate_mix': 1, 'executable_level_wrong_basis': 1}
- b188060f_fixture @ W2: blocked {'decision_driving_missing_as_of': 1, 'cross_basis_coordinate_mix': 1, 'executable_level_wrong_basis': 1}
- b188060f_fixture @ W3: blocked {'decision_driving_missing_as_of': 1, 'cross_basis_coordinate_mix': 1, 'executable_level_wrong_basis': 1}
- b188060f_fixture @ W4: blocked {'decision_driving_missing_as_of': 1, 'cross_basis_coordinate_mix': 1, 'executable_level_wrong_basis': 1}
- s06_real_anchor:s06__r1 @ W0: blocked {'decision_driving_unspecified_basis': 6, 'decision_driving_missing_as_of': 6, 'cross_basis_coordinate_mix': 14, 'invalid_conversion': 2}
- s06_real_anchor:s06__r1 @ W1: blocked {'decision_driving_unspecified_basis': 3, 'decision_driving_missing_as_of': 3}
- s06_real_anchor:s06__r1 @ W2: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s06_real_anchor:s06__r1 @ W3: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s06_real_anchor:s06__r1 @ W4: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}
- s06_real_anchor:s06__r2 @ W0: blocked {'decision_driving_missing_as_of': 6, 'decision_driving_unspecified_basis': 2, 'cross_basis_coordinate_mix': 35}
- s06_real_anchor:s06__r2 @ W1: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06_real_anchor:s06__r2 @ W2: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06_real_anchor:s06__r2 @ W3: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06_real_anchor:s06__r2 @ W4: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2, 'cross_basis_coordinate_mix': 8}
- s06_real_anchor:s06__r3 @ W0: blocked {'decision_driving_unspecified_basis': 22, 'decision_driving_missing_as_of': 22, 'cross_basis_coordinate_mix': 45, 'unbacked_executable_level': 2}
- s06_real_anchor:s06__r3 @ W1: blocked {'decision_driving_unspecified_basis': 23, 'decision_driving_missing_as_of': 23, 'cross_basis_coordinate_mix': 6, 'unbacked_executable_level': 2}
- s06_real_anchor:s06__r3 @ W2: blocked {'decision_driving_unspecified_basis': 23, 'decision_driving_missing_as_of': 23, 'cross_basis_coordinate_mix': 6, 'unbacked_executable_level': 2}
- s06_real_anchor:s06__r3 @ W3: blocked {'decision_driving_unspecified_basis': 23, 'decision_driving_missing_as_of': 23, 'cross_basis_coordinate_mix': 6}
- s06_real_anchor:s06__r3 @ W4: blocked {'decision_driving_unspecified_basis': 10, 'decision_driving_missing_as_of': 10, 'cross_basis_coordinate_mix': 7}
- s06_real_anchor:s06__r4 @ W0: blocked {'decision_driving_missing_as_of': 18, 'decision_driving_unspecified_basis': 10, 'cross_basis_coordinate_mix': 50}
- s06_real_anchor:s06__r4 @ W1: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 5, 'cross_basis_coordinate_mix': 10}
- s06_real_anchor:s06__r4 @ W2: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'cross_basis_coordinate_mix': 10}
- s06_real_anchor:s06__r4 @ W3: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'cross_basis_coordinate_mix': 10}
- s06_real_anchor:s06__r4 @ W4: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4, 'cross_basis_coordinate_mix': 10}
- s06_real_anchor:s06__r5 @ W0: blocked {'decision_driving_unspecified_basis': 5, 'decision_driving_missing_as_of': 8, 'cross_basis_coordinate_mix': 14}
- s06_real_anchor:s06__r5 @ W1: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s06_real_anchor:s06__r5 @ W2: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s06_real_anchor:s06__r5 @ W3: blocked {'decision_driving_unspecified_basis': 4, 'decision_driving_missing_as_of': 4}
- s06_real_anchor:s06__r5 @ W4: blocked {'decision_driving_unspecified_basis': 2, 'decision_driving_missing_as_of': 2}

