H2.4 缺口（578c01d 基线）与宿主原型已就绪，请按卡面在隔离分支提交。

**仍缺（H2.3 未覆盖）：**
1. `build_weekly_metrics` 聚合后调用 `evaluate_model_bias_and_weights`，写入 `h1b_system_gates_evaluation.model_isolation`
2. `render_weekly_summary_markdown` / `format_cli_text_report` 输出分层隔离、偏置冻结、>50% 全局 Shadow 告警
3. 未达标维度的 **动态 Gap 追踪** 段落（N/60、日历/交易日、T+5 完整率等）
4. 端到端测试：周度复算 CLI stdout 含分层隔离横幅；小样本周 `KEEP_FALSE` + shadow fallback

**顺带修复 DAV-447：** `scripts/recalculate_weekly_metrics.py:group_samples_by_week` return 后死代码删除。

宿主 `.venv310` 证据（未提交）：`pytest tests/test_evaluation_contracts.py tests/test_recalculate_weekly_metrics.py -q` → 53 passed。

禁止改 3/1 / 模型绑定；`credit_weighting_enabled` 保持默认 False。
