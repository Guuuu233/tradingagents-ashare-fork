# DAV-235 P0：资金流窗口校验fail-closed返修

## 精确基线
- `target/codex/dav-4-p2a-trunk@394e3efe08fef60f728345cc8eb9300c8ad0d693`
- DAV-230只读审核已复现：`selected_window_days=0`整数被`int(selected_window_days or 1)`转为1后接受，而字符串`"0"`被拒绝。

## 独立worktree
必须先`multica repo checkout https://github.com/Guuuu233/1.git --ref 394e3ef...`，cd进入返回路径；禁止编辑宿主主干。若checkout失败则blocked停止。

## 唯一关注点
仅修`api/services/report_service.py`的fund-flow selected window校验及`tests/test_report_service_fund_flow.py`。

## TDD必测
1. `selected_window_days=0`（int/str）、负数、非数字均拒绝；None仅在兼容旧报告有明确规则时允许默认1，并测试。
2. 5d声明但截至selected_as_of不足5个不同有效日期必须拒绝，不能把3日当5日。
3. 同源同字段同日期重复记录：相同值和冲突值都必须有明确fail-closed规则，禁止重复求和。
4. 非ISO/不可解析日期必须拒绝，不能用字符串排序。
5. 1d只匹配selected_as_of当天；5d严格选截至日最近5个不同日期；未来行不进入窗口。
6. direction按实际窗口总值验证。

## 边界
- 不改provider、collector、analyst、Prompt、用户配置/Key、api/main、数据库。
- 不合入、不部署、不跑真实报告。

## 交付
新独立远端分支/SHA，父为394e3ef；RED→GREEN；资金流相关测试；`TUSHARE_TOKEN=''`全量；compileall；diff-check；明确未合入/未上线。