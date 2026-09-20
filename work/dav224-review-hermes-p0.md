# DAV-224 独立终审：Hermes P0资金流窗口落库校验提交

## 精确对象

- 主干提交：`394e3efe08fef60f728345cc8eb9300c8ad0d693`
- 父提交：`6a8a896bfaa00e0a42d5ef3543aeb4b1d58e88d2`
- 变更文件：
  - `api/services/report_service.py`
  - `tests/test_report_service_fund_flow.py`
- 生产触发报告：`a4f8533f6f80479185833ae8622649bd`
- 原错误：`selected fund-flow value does not match records`
- 提交后报告`dd2d5cf259db490d946f18ed38704718`已completed，但辩论状态为0；该新问题属于DAV-221，不能用来替代本提交审核。

## 审核要求

只读审查，不修改代码：

1. 核验1d selection是否只匹配selected_as_of当天记录；
2. 核验5d窗口是否严格选择截至selected_as_of最近5个有效日期；
3. 日期缺失、窗口不足、重复日期、非法window、非有限值是否仍fail-closed；
4. 是否可能因字符串日期排序、无效日期、重复同日记录导致漏检或重复求和；
5. 检查新增测试是否覆盖生产形状，并补列缺失测试；
6. 独立重跑：定向测试、资金流相关测试、全量测试（显式隔离TUSHARE_TOKEN，避免生产密钥污染fixture）、compileall、diff-check；
7. 输出PASS或打回，附文件:行号、严重等级、可复现证据；
8. 若打回，将DAV-224标blocked并给DAV-221/单独返修建议；不得直接改代码。

说明：该提交由Hermes直接编写，必须按与团队提交相同标准审查，不得降低门槛。