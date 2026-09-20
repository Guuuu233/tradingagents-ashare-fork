# 卡 1：Tushare 全球指数正式接入

方案全文与证据：`work/2026-09-19-global-indices-data-source-audit.md`，**施工前必须通读**，
尤其是「卡 1」整节（修复契约 1–8、RED 用例 G1–G8、两个警告块）、§3.1–3.6 缺陷定位、
以及**第三部分 14 条被推翻的判断**（防止从历史对话捡到作废结论）。
派工边界与坑：`work/2026-09-19-global-indices-dispatch-brief.md` §3.1、§4、§5、§6、§7。

基线 SHA（开工前自查是否仍为此值）：`7a988197ef982fae95b6c9669234348e0632216a`
（远端 `codex/dav-4-p2a-trunk` 与本地 HEAD 一致，工作区干净）。

## 白名单（只能动这些）

- 新增正式 Tushare provider 文件（实现 `get_global_indices`，不只是 helper）
- `tradingagents/dataflows/providers/registry.py`（注册）
- `tradingagents/default_config.py:49`（`macro_market_data` 改为 `tushare,cn_akshare`）
- `tradingagents/dataflows/macro_market_utils.py`（新鲜度闸 + 逐指数 `actual_as_of` 渲染）
- 对应测试

**不得**碰 `interface.py` 的路由逻辑本身。

## 必须同时处理的语义契约

- `HSTECH` → `HKTECH`（Tushare 下 HSTECH 返回空）
- 「纳斯达克综合」vs「纳斯达克100」语义混用：`global_targets` 的 `ak_name` 为「纳斯达克」，
  而 `_fetch_from_snapshots` 另有 `纳斯达克100` 分支，两者混在同一 key 上
- `DJI`（非 `DJIA`）
- 每个指数必须保存真实 `source`（hist / snapshot / tushare）与 `as_of`

## 修复契约（8 条，缺一不可）

1. Tushare 适配器按真实字段直取，**不做任何缩放猜测**（100 倍成因未定位，禁止 `/100`）。
2. `calculate_series_metrics` 增加新鲜度闸：`actual_as_of` 与请求 `as_of` 相差超过 N 个
   交易日返回 `None`，走【数据缺失】。N 按标的类型定（境外指数建议 1–2 交易日）。
3. `build_global_indices_markdown` 必须**逐指数**渲染各自 `actual_as_of`（当前渲染的是
   聚合最大值 `max(valid_dates)`，见 `macro_market_utils.py:193-196`，陈旧项被掩盖）。
4. AkShare 兜底增加值域/字段一致性校验，异常拒绝而非放行。
   ⚠️ 值域校验必须在**知道标的身份的那一层**做：通用 `calculate_series_metrics()` 只拿到
   裸 DataFrame，不知道 `767728` 属于标普。二选一：适配器按 `SPX` 等标的契约校验，或给
   通用函数显式传入 instrument contract。**不得只写一个裸数字的测试。**
5. 完整性标记：部分成功必须标 `partial` 并记录「N/M 项成功」，不得标 `available/verified`。
6. 逐指数 `source` 与 `as_of` 必须落到渲染层，不得只给聚合最大日期。
7. **通用函数调用方影响审计**：`calculate_series_metrics` 被 `get_major_assets`
   （黄金/原油/美元指数/美债10年/LME铜）等多处共用。施工前必须 grep 全部调用点并逐个
   评估阈值适用性（境外指数与大宗商品/美债的新鲜度窗口可能不同）。
8. **`get_major_assets` 不得被配置切换误伤**。

⚠️ **`VendorRefuse` 语义极易写反（实测 `interface.py:554-568`，本任务最大坑）**：

```python
if isinstance(result, VendorRefuse):
    if result.allow_peers:
        peer_allowlist = set(result.allow_peers); break   # 带 peers → 链路继续
    return result.to_prompt()                              # 裸 refuse → 链路终止
```

**裸 `VendorRefuse` 终止整条链，不回退到 akshare。** 新 provider 若不实现
`get_major_assets`，正确做法三选一：
- **不定义该方法**，让路由自然跳过（推荐）；
- 抛 `NotImplementedError` / 返回 `VendorFail`；
- 若确需 `VendorRefuse`，**必须带 `allow_peers=("cn_akshare",)`**。

写反的后果：`get_major_assets` 被一枪打死，且是**新引入的回归**。审查会专门核这一点。
必须补测试断言：切换 `macro_market_data` 后 `get_major_assets` 结果不退化。

## RED 用例（先在直接父版本红，再 GREEN）

| 编号 | 输入 | 期望 |
|---|---|---|
| G1 | 100 倍样本，**带标的身份**（`ts_code=SPX` + 值 767,728） | 落入【数据缺失】，**不得**自动除以 100 |
| G2 | 任意查询 | 返回项 `actual_as_of` **不得晚于**请求日 |
| G3 | 序列末尾停在 08-21，以 09-10 查询（超新鲜度阈值） | **拒绝**并落入【数据缺失】 |
| G4 | 10 项中仅 6 项成功 | 标 `partial` 且记录缺失项，不得标 `verified` |
| G5 | `HSTECH` 与 `HKTECH` | 前者空、后者有数据，映射正确 |
| G6 | 正常新鲜序列 | **仍**成功返回，不得因加闸误杀 |
| G7 | 渲染层：一项 09-10、一项 08-21 | 逐指数显示各自 `as_of`，**不得**只显示聚合最大值 |
| G8 | 人为构造两个不同日期、不同值的 fixture | 两个基准日返回各自对应的值 |

⚠️ **断言纪律**：不得用「两个日期点位必须不同」证明无陈旧——两个交易日收盘合法地可能
相同。判定陈旧必须依据 `actual_as_of` 与新鲜度阈值；比较点位必须用人为构造 fixture（G8）。

## 证据门（D-012 / D-013 / D-014）

1. 先 RED 后 GREEN，关注点分 commit。
2. RT-FULL 无筛选全量对照：候选与直接父同解释器同命令，只承认**零新增失败**；
   定向绿不替代全量；全量上限不低于 45 分钟；见高 CPU 长时间不退出（baostock EOF 忙循环）立即中止。
3. 交付须含：完整候选 SHA、直接父、白名单 diff、`git diff --check`、逐场景实测命令与输出、
   全量基线对照、明确的「未合入未上线」状态。
4. **不得合入主干、不得部署、不得重启服务**（服务当前停机，拉起是独立部署动作，不在本卡）。

## 环境铁律

- `env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python`
  （实测 3.10.20），报告须贴 `-V`。禁用任务工作区 `.venv` 与系统解释器作门禁证据。
- 所有测试 `DATABASE_URL` 指向隔离临时库。
- Tushare 凭据从 `.env` 读（`TUSHARE_TOKEN`），复用既有封装
  `industry_linkage_provider._query_tushare_api`，不另起一套。
  **不得把 token 打印/写日志/提交/贴进卡片。**
- `tests/test_global_indices_fallback.py` 当前 **16 passed + 3 subtests**，是不得退化的基线。
- 只读生产库用 `file:...?immutable=1`（`mode=ro` 会因缺 `-wal/-shm` 报 unable to open (14)），
  **不得对生产库开可写连接**。

## 交回时请报告

候选完整 SHA 与直接父、白名单 diff、RED 在父版本失败输出、GREEN 输出、RT-FULL 候选与父
完整计数对照（失败集合逐项比对）、明确未合入未部署。完成后**主动精确 mention `项目调度助手`**
推进同 SHA 只读复审（审查员：`代码审核员`，按 D-014；不派「独立代码审核员」）。
