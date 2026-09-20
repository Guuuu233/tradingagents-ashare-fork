# DAV-156 integration 基线失败清单只读整理

## 目标

与 DAV-153 并行，整理 `integration/dav119@5f1d197ce59c9e16e5666534db903cae2ee6d2a3` 相对 target trunk 的测试基线差异，为后续最终回归节省时间；只读，不改代码。

## 固定输入

- integration：`5f1d197ce59c9e16e5666534db903cae2ee6d2a3`
- trunk：`f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef`
- 使用宿主 `.venv310` Python 3.10.x；若 checkout/环境不可用，报告阻塞，不用系统 Python 替代。

## 工作内容

1. 独立 checkout integration SHA，确认 clean tree；
2. 只运行 DAV-151 已识别的失败集合或最小重现，不重复全量回归；
3. 在同一宿主环境对 target trunk 重跑对应集合；
4. 将每个失败分类为：integration 新增、trunk 已有、环境/网络、测试契约冲突；记录测试文件与稳定复现摘要；
5. 特别核对 `tests/test_sina_historical_fund_flow.py` 的 THS 缺日期契约是否仍与当前 fail-closed 规则一致。

## 边界与交付

只读；禁止修改代码、测试、配置、数据库、用户设置、providers、模型绑定、API Key、凭据、主干和服务。不得提交、推送、重启。交付精确 checkout/SHA、命令、分类统计和是否影响 DAV-151 parent gate。