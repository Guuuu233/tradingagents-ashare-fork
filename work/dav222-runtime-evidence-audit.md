# DAV-222 只读审计：dd2d报告真实辩论是否执行

## 固定对象

- SHA：`394e3efe08fef60f728345cc8eb9300c8ad0d693`
- Report ID：`dd2d5cf259db490d946f18ed38704718`
- 账户：`429163f7-50b6-4982-8bdf-96ae99506843`
- 请求临时3/3，但result_data两类debate count均为0。

## 只读任务

1. 对齐报告UTC created/updated时间与服务日志本地时间。
2. 查询该report对应的LLM调用、prompt injection、debate token/message、graph node和final-state日志。
3. 明确结论：
   - 辩论节点实际0次执行；或
   - 实际执行6/9次但状态在某层丢失/覆盖；或
   - config_overrides未生效。
4. 给出精确文件:函数:行号的数据流和最小修复建议。
5. 检查验收脚本`work/verify_dav213_cmb_analysis.py`的schema漂移，列出正确当前列；只读，不修改。
6. 输出可复现命令和证据表。

禁止改代码、提交、部署、重启、发起新报告。立即审计。