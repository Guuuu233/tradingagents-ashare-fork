# DAV-157 财联社任务环境 preflight

## 目标

为 DAV-126 提供一次精确、只读、窄范围的环境前置核验；不读取 DAV-118/DAV-119 长历史，不修改代码。

## 固定对象

- 目标代码：`agent/1/dav126-security-fix@c9bbe52022805befbce14298cea174280c7eebd2`
- 仓库：target 对应工作组仓库
- 要求环境：目标 checkout 内 `.venv310/bin/python`，Python 3.10.x

## 验收

1. 独立 checkout 精确 SHA，确认 clean tree 和 HEAD 一致；
2. 只检查 `.venv310/bin/python` 是否存在、可执行、版本是否为 Python 3.10.x；
3. 只检查 pytest 是否可导入，不运行完整测试、不访问真实 CLS 接口；
4. 如环境缺失，立即报告 `environment_missing` 并停止；如环境可用，报告路径、版本和 pytest 版本；
5. 不使用系统 Python 冒充，不创建/安装环境，不修改 `.env`、配置、数据库、用户设置、providers、模型绑定或凭据；
6. 不提交、不推送、不重启。

## 交付

只发布脱敏的 checkout SHA、环境路径/版本、pytest 可用性和下一步建议。环境通过后，DAV-126 再单独执行 CLS 真实快照与 manifest 门；本任务不宣称新闻源接入完成。

执行者：代码运维测试员。
