## 派工：文件冲突已解除，可以开工

本卡此前因 `tradingagents/dataflows/providers/cn_akshare_provider.py` 与在审候选抢文件而挂起。DAV-944 候选已于本日合入主线（`f125179`），**冲突解除**。

## 版本基线

- 目标主线：`origin/codex/dav-4-p2a-trunk`，当前 tip `331a32284b0b62599f1cf76806d09147fa680a4c`
- 开工与交付前均须 `git ls-remote origin codex/dav-4-p2a-trunk` 复核；**直接父必须等于交付当时的 tip**（主线正在推进，DAV-946 候选 `483a1098` 已通过复审、即将合入，很可能在你交付前再次变动）

## 范围

严格白名单：`tradingagents/dataflows/providers/cn_akshare_provider.py` 及其对应测试文件。若需改动其他文件，先在卡内说明并等确认。

⚠️ 注意 DAV-944 刚改过同一文件（全球指数快照缺少真实时间戳时拒绝历史分析），请先读当前主线版本再动手，**不要基于旧印象修改**，也不要回退它的护栏。

## 核心要求

「同标的重复行不得按返回顺序取数」属**前视偏差红线**：不得因为供应商返回顺序不同而取到不同的值，更不得取到晚于请求日的数据。请明确去重与排序的确定性规则，并说明冲突行（同标的同日不同值）如何处理——是拒绝还是有明确优先级，不得静默取第一条。

数据缺失或冲突时须**显式上报**，不得填 0 或空值掩盖。

## 红队场景（须逐条实跑并贴实际输出）

- RT-1 正常单行数据
- RT-2 同标的重复行、值一致 → 预期行为
- RT-3 同标的重复行、值冲突 → 必须有确定性且可解释的处理，不得取决于返回顺序
- RT-4 供应商返回顺序颠倒 → 结果必须与 RT-3 完全一致（这是本卡的核心验收点）
- RT-5 空数据 / 缺列 / 类型异常 → 显式失败，不得静默
- RT-6 日期越界（含晚于请求日的行）→ 必须拒绝

## 环境与回归

解释器必须是 `/Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`，配 `env -u PYTHONPATH`，报告贴 `-V`（须 `Python 3.10.20`）。`DATABASE_URL` 指向隔离临时库，禁止写 `data/tradingagents.db`。

**不要跑整套 pytest**：主干存在既有死锁（根因见 DAV-979 —— `tests/test_api_smoke.py` 进出 lifespan 关掉了进程级全局 executor，导致 `test_game_theory_integration.py::test_rt7_...` 永久阻塞；修复另由 DAV-992 处理）。请改用分文件对照：`tests/test_*.py` 逐个独立进程执行、每个 120s 看门狗，候选与其直接父两侧相同切分，只看有无**新增**失败。参考基线：`214 OK / 8 个失败文件 / 1 个挂死文件（test_knowledge_rag.py）`。

## 交付

完整 40 位 SHA、直接父、精确远端 ref、白名单清单、`git diff --check`、clean 工作树。不合入、不部署、不重启、不写生产库。写审分离，复审另派代码审核员。

[@高级开发·支援](mention://agent/04cc525b-70a1-44ee-ad8f-2afc0c6d04ff)
