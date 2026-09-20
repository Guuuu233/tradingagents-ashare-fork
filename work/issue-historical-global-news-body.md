## 目标
接入已验证可用的「今日投资（Investoday）」历史宏观/全球新闻，让历史日期分析（例如 2026-08-11）不再被路由层一律拦截，同时严格防止实时新闻混入历史报告。

## 背景与实测证据（Hermes，2026-08-12）
- 当前 `.env` 已配置 `INVESTODAY_API_KEY`（凭据只在宿主机环境中，已被 `.gitignore` 保护；禁止在代码、提交、评论和测试输出中出现明文）。
- 直接调用 `CnInvestodayProvider().get_global_news("2026-08-11", look_back_days=14, limit=5)` 已成功返回 5 条真实历史宏观新闻，窗口为 `2026-07-28` 至 `2026-08-11`。
- 当前 `tradingagents/dataflows/interface.py` 的 `_HISTORICAL_NEAR_WINDOW_NEWS_METHODS` 在 provider 调用前直接拒绝 `get_global_news`，返回“全球快讯为实时直播流……历史日期不可用”，因此已有 Investoday 历史能力根本不会被调用。
- 当前路由链包含 `cn_investoday`，provider 已实现 `get_global_news`；问题主要是历史路由护栏没有区分“实时流 provider”和“可按时间查询的历史 provider”。
- 新浪财经快讯仍然只是实时直播流，历史日期绝不能调用；不能为了让报告有内容而放行新浪实时结果。

## 修复范围
只处理 `get_global_news` 的历史日期路由与数据契约；不要顺带修改个股新闻、雪球热搜、涨停池、模型绑定或其他用户配置。

## 修复要求
1. 历史日期请求优先/仅允许调用明确具备历史时间范围能力的 `cn_investoday`；没有 Key 或请求失败时返回显式数据缺口，禁止回退到新浪实时流、yfinance 当前新闻或任何无法证明 as-of 的源。
2. 保持实时分析行为不变：今天的分析仍可使用现有实时新闻链路。
3. 对 Investoday 返回的每条新闻做日期校验：新闻发布时间必须落在 `[curr_date - look_back_days, curr_date]`，且不得出现晚于分析基准日的条目。不能只相信供应商请求参数。
4. 输出中明确标记来源与数据窗口，例如“来源：今日投资；数据窗口：2026-07-28 至 2026-08-11”；让下游模型知道这是历史宏观新闻，不是实时直播。
5. 无 Key、接口失败、返回结构异常都要显式返回 `【数据获取失败】`，不能返回空字符串、`None` 或把失败伪装成“暂无新闻”。
6. 不要把 Alpha Vantage/GDELT 一并接入本卡；它们另行评估，避免扩大变更范围。

## TDD 验收
- 冻结当前日期后，`route_to_vendor("get_global_news", "2026-08-11", 14, 5)` 在 mock registry 中命中 `cn_investoday`，不调用 `cn_akshare.get_global_news`；返回包含来源和窗口的具体新闻。
- Investoday 返回一条 2026-08-12 新闻、一条 2026-08-11 新闻时，结果必须过滤掉 8/12，只保留不晚于 8/11 的记录。
- Investoday 缺少 API Key 时，历史请求返回显式数据缺口，不得回退实时 provider。
- Investoday 返回非列表/网络失败时，返回显式数据缺口，不得生成成功快照。
- 当前日期 live 路径回归：仍可走现有实时 provider，不能被历史白名单逻辑破坏。
- 全量回归测试 0 新增失败；提供真实环境冒烟结果（只报告“成功/失败、条数、日期窗口”，不要输出 Key）。

## 工程纪律
- 先完整阅读将修改的文件，并 grep 所有调用点；一个 commit 只做本卡一个关注点。
- 不自行修改主干；完成后推送专用分支，附 commit、测试命令和结果，等待 Hermes 验收。
- 不修改 `role_bindings`、`providers`、模型选择等用户配置。
- API Key 只从宿主机 `.env` 读取，绝不能写入代码、测试 fixture、日志、issue 评论或提交历史。
- Python 命令使用 `env -u PYTHONPATH .venv310/bin/python`；国内域名/Investoday 走正确的 `no_proxy` 配置。
