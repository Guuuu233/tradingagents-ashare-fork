# DAV-1311 逐例审计表

| # | 档位 | 来源 | 标的 | 命中(判定) | 档位判定 | 理由 |
|---|---|---|---|---|---|---|
| 1 | `f4f0cac61c9d40fda65d3d140b7cc550:short_term` | daily | 601398.SH 2026-09-24 | reject_in_partial:INV-5(误)；e04_priced_in(正) | 正确拦截 | E-04裸断言已定价成立 |
| 2 | `fb53ab62e3e740a7a40c379b25f397dd:short_term` | daily | 601318.SH 2026-09-24 | coverage:INV-3(正)；coverage:INV-7(误)；coverage:INV-11(误)；reject_in_partial:INV-1(误)；reject_in_partial:INV-6(误) | 正确拦截 | INV-3含未剔除的未核实原子'18亿' |
| 3 | `dc1dc116b3d34b60ab39b2d2a170f9a3:medium_term` | daily | 601398.SH 2026-09-24 | reject_in_partial:INV-3(误)；reject_in_partial:INV-4(误)；e04_beat(误) | 误拦 | 全部命中为清单归类/情景推演误拦 |
| 4 | `1312d27a84564e8aa136f2f5ce1c1b4f:short_term` | batch | 600036.SH 2026-07-08 | coverage:INV-1(误)；coverage:INV-3(误) | 误拦 | 未核实原子均已剔除，实质等同部分采纳，清单归类误拦 |
| 5 | `84c5dfc87138469b95605a8b2ddf79f3:short_term` | batch | 688981.SH 2026-07-08 | reject_in_partial:INV-7(误) | 误拦 | 清单归类误拦 |
| 6 | `9b70d0a1571b4d6196e29cf7bdaf8d78:short_term` | batch | 000333.SZ 2026-07-08 | coverage:INV-2(误)；coverage:INV-7(误)；e04_priced_in(误) | 误拦 | 两条coverage原子均剔除；E-04为附依据定价推断 |
| 7 | `be99f770ae2d4820b78aed0e3cf6dfe3:short_term` | batch | 601088.SH 2026-07-08 | coverage_text_conflict:INV-3(正) | 正确拦截 | 正文/核验真实冲突 |
| 8 | `be99f770ae2d4820b78aed0e3cf6dfe3:medium_term` | batch | 601088.SH 2026-07-08 | e04_priced_in(误) | 误拦 | E-04为附依据定价推断 |
| 9 | `1827101791cd4a4fa8b951a2e85647cb:short_term` | batch | 601899.SH 2026-07-08 | coverage:INV-3(正)；e04_priced_in(正)；e04_beat(误) | 正确拦截 | 未剔除原子+裸断言均成立 |
| 10 | `1827101791cd4a4fa8b951a2e85647cb:medium_term` | batch | 601899.SH 2026-07-08 | reject_in_partial:INV-8(误)；e04_priced_in(正) | 正确拦截 | E-04裸断言成立 |
| 11 | `d93a03ef6bbd45af96033d941381b012:short_term` | batch | 002594.SZ 2026-07-08 | winner_conflict(误) | 误拦 | 机械词面匹配误拦 |
| 12 | `4198ef36af014cd38a40696c1513255b:medium_term` | batch | 600519.SH 2026-06-10 | coverage:INV-2(误)；e04_priced_in(正)；e04_miss(误) | 正确拦截 | E-04裸断言成立 |
| 13 | `af25901ec32e4a44ad2aef3db8a09487:short_term` | batch | 688981.SH 2026-06-10 | coverage:INV-3(正)；coverage:INV-4(误)；coverage:INV-11(正) | 正确拦截 | 两条claim各含未剔除的未核实原子 |
| 14 | `af25901ec32e4a44ad2aef3db8a09487:medium_term` | batch | 688981.SH 2026-06-10 | coverage:INV-4(误)；coverage:INV-7(误) | 误拦 | 未核实原子全部剔除，清单归类误拦 |
| 15 | `a810f96492cd463bb049cbb572818837:short_term` | batch | 300750.SZ 2026-07-08 | reject_in_partial:INV-6(误)；e04_priced_in(误) | 误拦 | 两条均为机械/归类误拦 |
| 16 | `a810f96492cd463bb049cbb572818837:medium_term` | batch | 300750.SZ 2026-07-08 | reject_in_partial:INV-7(误) | 误拦 | 清单归类误拦 |
| 17 | `edd20aef54f543039806e982abccd6da:medium_term` | batch | 601899.SH 2026-06-10 | reject_in_partial:INV-2(误)；reject_in_partial:INV-5(误)；reject_in_partial:INV-9(误) | 误拦 | 三条均为清单归类误拦 |
| 18 | `d33b28d8c5d14533af206d0be6bd8609:medium_term` | batch | 002594.SZ 2026-06-10 | coverage_floor:INV-4(误) | 误拦 | 清单归类误拦 |
| 19 | `ebc41de27b2f40a9b5c6cecf6e75823f:short_term` | batch | 600900.SH 2026-06-10 | e04_priced_in(正) | 正确拦截 | E-04裸断言成立 |
| 20 | `21e993fdf65b43dbb7622bdcd4aa2860:medium_term` | batch | 001979.SZ 2026-06-10 | reject_in_partial:INV-4(误)；reject_in_partial:INV-9(误) | 误拦 | 清单归类误拦 |
| 21 | `117557c3cd6c4294ae543dd0462f7692:short_term` | batch | 600276.SH 2026-07-22 | coverage:INV-1(误) | 误拦 | 清单归类误拦 |
| 22 | `117557c3cd6c4294ae543dd0462f7692:medium_term` | batch | 600276.SH 2026-07-22 | reject_in_partial:INV-10(误)；e04_beat(误) | 误拦 | 均为误拦 |
| 23 | `fd45ac180656400484378e13d5c4156d:short_term` | batch | 688981.SH 2026-07-22 | coverage:INV-3(误)；coverage:INV-5(误)；coverage:INV-12(误) | 误拦 | 清单归类误拦 |
| 24 | `882c2cbce0fd4e99b683d5156efed12c:short_term` | batch | 000333.SZ 2026-07-22 | coverage:INV-8(误)；e04_double_count(误) | 误拦 | 均为误拦 |
| 25 | `882c2cbce0fd4e99b683d5156efed12c:medium_term` | batch | 000333.SZ 2026-07-22 | reject_in_partial:INV-1(误)；reject_in_partial:INV-10(误) | 误拦 | 清单归类误拦 |
| 26 | `b03c6a97f67e481fa4b40da43df4b106:short_term` | batch | 601088.SH 2026-07-22 | partial_threshold_adopted:INV-1(误) | 误拦 | 清单归类误拦 |
| 27 | `9477ba3641c142ddb640726545f52aab:medium_term` | batch | 601899.SH 2026-07-22 | e04_beat(正) | 正确拦截 | E-04裸断言成立 |
| 28 | `fc17f885cdce4fa0a4dbaeb39a40d03c:medium_term` | batch | 600030.SH 2026-07-22 | e04_priced_in(正) | 正确拦截 | E-04裸断言成立 |
| 29 | `3150f7d0972341b899597c89ba4a83aa:short_term` | batch | 002594.SZ 2026-07-22 | reject_in_partial:INV-3(误)；reject_in_partial:INV-5(误)；reject_in_partial:INV-7(误)；reject_in_partial:INV-11(误)；e04_priced_in(正)；e04_beat(误) | 正确拦截 | E-04裸断言成立 |
| 30 | `9bdef4096bb74122815bef083c0d6150:short_term` | batch | 600900.SH 2026-07-22 | coverage:INV-6(误) | 误拦 | 清单归类误拦 |
| 31 | `9bdef4096bb74122815bef083c0d6150:medium_term` | batch | 600900.SH 2026-07-22 | reject_in_partial:INV-1(误) | 误拦 | 清单归类误拦 |
| 32 | `dfc4662b9cdd47b3b2f32fcb964f569a:medium_term` | batch | 001979.SZ 2026-07-22 | reject_in_partial:INV-5(误)；reject_in_partial:INV-8(误) | 误拦 | 清单归类误拦 |
| 33 | `acbb32d017024400b6bd0e3f96b5cbbf:short_term` | batch | 601088.SH 2026-06-10 | reject_in_partial:INV-2(误)；reject_in_partial:INV-4(误)；reject_in_partial:INV-8(误)；reject_in_partial:INV-9(误)；reject_in_partial:INV-11(正)；e04_priced_in(正)；e04_beat(误) | 正确拦截 | INV-11命题被采纳+裸断言成立 |
| 34 | `0c1d99663a4a48948bcae3bb75abdc3a:short_term` | batch | 601012.SH 2026-07-08 | reject_in_partial:INV-4(误)；reject_in_partial:INV-7(误)；reject_in_partial:INV-11(误) | 误拦 | 清单归类误拦 |
| 35 | `0c1d99663a4a48948bcae3bb75abdc3a:medium_term` | batch | 601012.SH 2026-07-08 | reject_in_adopted:INV-4(正)；reject_in_adopted:INV-7(正)；reject_in_partial:INV-3(误)；reject_in_partial:INV-8(误)；reject_in_partial:INV-12(误)；coverage_text_conflict:INV-8(正)；coverage_text_conflict:INV-12(正) | 正确拦截 | 两条全额采纳reject+两处'证据充分'冲突均成立 |
| 36 | `eb610dafadb241aaa8711aa247baf23d:medium_term` | batch | 600309.SH 2026-07-08 | reject_in_partial:INV-6(误)；e04_priced_in(正) | 正确拦截 | E-04裸断言成立 |
| 37 | `ad72af61833d412dbe4b43c7562c3d45:medium_term` | batch | 002027.SZ 2026-07-08 | reject_in_partial:INV-2(误)；reject_in_partial:INV-10(误) | 误拦 | 清单归类误拦 |
| 38 | `61cffb85ee5d4dbcbd41ebea32ec2f55:short_term` | batch | 603288.SH 2026-06-10 | reject_in_partial:INV-8(误) | 误拦 | 清单归类误拦 |
| 39 | `61cffb85ee5d4dbcbd41ebea32ec2f55:medium_term` | batch | 603288.SH 2026-06-10 | partial_threshold_adopted:INV-4(误) | 误拦 | 清单归类误拦 |
| 40 | `28f6a1e0335646e7b1932cee471b2810:medium_term` | batch | 000063.SZ 2026-06-10 | reject_in_partial:INV-4(正)；reject_in_partial:INV-10(正) | 正确拦截 | 结论直接依赖被否决命题 |
| 41 | `31f3b28981b6415997052f75318c1e81:short_term` | batch | 002027.SZ 2026-06-10 | coverage:INV-3(正)；coverage:INV-11(误)；e04_priced_in(正) | 正确拦截 | 未剔除原子+裸断言成立 |
| 42 | `31f3b28981b6415997052f75318c1e81:medium_term` | batch | 002027.SZ 2026-06-10 | reject_in_partial:INV-1(误)；reject_in_partial:INV-8(误)；e04_priced_in(正)；e04_beat(误) | 正确拦截 | E-04裸断言成立 |
| 43 | `3fc1beba1ee74b53bf634d4a35eddbf6:short_term` | batch | 603288.SH 2026-07-22 | coverage:INV-1(误)；coverage:INV-4(误) | 误拦 | 清单归类误拦 |
| 44 | `8c0285523b26431cac84b5010cc36e64:medium_term` | batch | 600309.SH 2026-07-22 | reject_in_partial:INV-1(误) | 误拦 | 清单归类误拦(边界：claim命题被拒但消费原子已核实) |
| 45 | `fdc9352b89554177bb78ea7a33eac2fd:medium_term` | batch | 600519.SH 2026-05-20 | coverage:INV-4(误) | 误拦 | 核验器伪原子误拦 |
| 46 | `d06b66d264e64f45b5388dace9207f4d:short_term` | batch | 300750.SZ 2026-05-20 | reject_in_adopted:INV-3(正)；reject_in_partial:INV-5(误)；reject_in_partial:INV-9(误)；e04_priced_in(正) | 正确拦截 | 全额采纳reject成立 |
| 47 | `7177a07234794cb5abcb04eac47794f9:medium_term` | batch | 688981.SH 2026-05-20 | e04_priced_in(误) | 误拦 | 明示未知启发式被机械拦截 |

## 汇总

- 档位级：正确拦截 18/47 = 38.3%，Wilson95% [25.8%, 52.6%]；误拦 29；无法判定 0。

### 命中类型 × 判定

| 命中类型 | 命中数 | 正确 | 误拦 | 正确率(Wilson95%) |
|---|---|---|---|---|
| reject_in_partial | 46 | 3 | 43 | 7% [2%,18%] |
| e04 | 25 | 13 | 12 | 52% [33%,70%] |
| coverage_mixed_adopted | 25 | 5 | 20 | 20% [9%,39%] |
| coverage_text_conflict | 3 | 3 | 0 | 100% [44%,100%] |
| winner_conflict | 1 | 0 | 1 | 0% [0%,79%] |
| coverage_floor | 1 | 0 | 1 | 0% [0%,79%] |
| partial_threshold_adopted | 2 | 0 | 2 | 0% [0%,66%] |
| reject_in_adopted | 3 | 3 | 0 | 100% [44%,100%] |

### E-04 四类分法（DAV-1112 口径，按命中计）

| 类别 | 命中数 | 判定 |
|---|---|---|
| 裸断言 | 13 | 正确=13 误拦=0 |
| 条件/情景推演 | 5 | 正确=0 误拦=5 |
| 附可回溯依据的定价推断 | 2 | 正确=0 误拦=2 |
| 否定句机械命中 | 2 | 正确=0 误拦=2 |
| 词面机械命中(非E-04语义) | 1 | 正确=0 误拦=1 |
| 明示未知仅作降权 | 2 | 正确=0 误拦=2 |

核验器伪原子导致的误拦（coverage 子类）：1 例 → [('fdc9352b89554177bb78ea7a33eac2fd:medium_term', 'coverage:INV-4')]
