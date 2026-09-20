## Cursor 验收：只读审计成立，暂不立项实现

对照主干 `e10b106df9d3173258b0a3fefc90ba7f3559f109`，Cursor 抽核：

- `cn_akshare_provider` 日 K 通道 `adjust="qfq"`
- `format_hist_csv` / baostock 将 `Dividends`、`Stock Splits` 写成 `0.0`
- `backtest_service` 默认 `price_basis = analysis.get("price_basis") or "raw"`（标签与数据口径不一致）
- 代码中无 `adj_factor` / PIT 公司行动引擎

**审计结论接受。** 路径 B 正确，但 **现在不拆实现卡**：等 DAV-595 合入、DAV-601 cohort 隔离落地后再立项 RAW 正名与 PIT。禁止把当前前复权当成 PIT。
