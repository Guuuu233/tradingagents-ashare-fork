# 日期护栏分类卡：`cn_akshare.get_cninfo_announcement_content`

**性质：只读分类，不改代码。** 本卡不授权修复、FF、部署。先定分类，再决定是否开实现卡。

## 事实（已只读核实）

`tradingagents/dataflows/providers/cn_akshare_provider.py:7185`

```python
get_cninfo_announcement_content = qualify_cninfo_content   # 类级别别名
```

`inspect.signature` 实测：

```
(record: CninfoDisclosureRecord, *, content_bytes: bytes | None = None,
 fetch_fn: Optional[Any] = None, timeout: float = 10.0,
 cutoff: str | None = None) -> CninfoDisclosureRecord
```

定义在 `cn_akshare_provider.py:7159`（另有同名函数在 `tradingagents/dataflows/cninfo_disclosure.py:668`）。

## 护栏为何判违规

`tests/test_provider_date_guards.py:20-28`：

```python
DATE_PARAM_NAMES = {"curr_date","date","start_date","end_date","trade_date","begin_date","as_of"}
```

判定规则为「参数名在集合内 **或** 以 `_date` 结尾」。`cutoff` 两者都不满足，故判缺日期参数。

## 已推翻的错误结论

> ~~「生产函数缺日期参数，是 C-05 引入的真实 PIT 缺陷」~~ —— **不成立。**

两点都错：

1. 该函数**有**日期语义参数 `cutoff`；
2. 它不是时间序列查询。它接收**已取得**的 `CninfoDisclosureRecord`，对正文做截止日资格判断，返回 record。护栏前提「时间敏感的 `get_*` 必须带日期参数以约束拉取窗口」不适用于这种资格判定函数。

## 待定分类（择一，须审核员判定）

- **(a) 护栏词表不全** — `cutoff` 是本仓已在用的日期语义参数名，应加入 `DATE_PARAM_NAMES`。
  风险：放宽词表会同时豁免其它含 `cutoff` 的方法，需先 grep 全量影响面。
- **(b) 显式白名单** — 加入 `TIMELESS_GET_METHODS`，附理由注释（同 `get_sina_global_news` 的处理方式）。
  风险：语义上不准确，该函数并非 date-blind，而是日期语义不同。
- **(c) 别名命名不当** — 一个职责为 `qualify_*` 的函数用 `get_*` 别名暴露，本身就会被结构护栏误捕。改名或移除别名可根治。
  风险：别名可能有调用方，须先 grep；改名属行为变更，需独立卡。

## 影响面实测（2026-09-08，只读，registry 反射）

命令见文末。三项结果：

**含 `cutoff` 的 `get_*` 方法（方案 a 影响面）—— 5 个**

| 方法 | 其它日期参数 | 当前是否已过护栏 |
|---|---|---|
| `cn_akshare.get_cninfo_announcement_content` | 无 | ❌ 唯一违规项 |
| `cn_akshare.get_cninfo_announcements` | `start_date`/`end_date` | ✅ |
| `cn_akshare.get_cninfo_disclosure_relation` | `start_date`/`end_date` | ✅ |
| `cn_akshare.get_cninfo_disclosure_report` | `start_date`/`end_date` | ✅ |
| `cn_akshare.get_cninfo_ir_surveys` | `start_date`/`end_date` | ✅ |

→ 方案 a 今天只改变 1 个方法的判定；另 4 个本就靠 `start_date`/`end_date` 通过。**当前影响面极小，但风险是前瞻性的**：`cutoff` 一旦入白名单，未来任何「只带 `cutoff` 的真拉取方法」都会静默豁免，而 `cutoff` 约束的是资格判定、不是拉取窗口。护栏本意正是防这个。

**`get_*` 名实不符的别名 —— 3 个，全部零调用方**

| 别名（`cn_akshare_provider.py`） | 实际函数 | 调用方 |
|---|---|---|
| `get_cninfo_announcement_content`:7185 | `qualify_cninfo_content` | 0（含 tests） |
| `get_cninfo_disclosure_report`:7188 | `get_cninfo_announcements` | 0 |
| `get_cninfo_disclosure_relation`:7189 | `get_cninfo_ir_surveys` | 0 |

## 推荐：方案 (c)，收窄到单条

**理由：**

1. 违规项是**唯一**一个把 `qualify_*`（资格判定）用 `get_*`（取数）暴露的别名。另两个是 `get_*` → `get_*` 改名，语义无冲突，没触发护栏。
2. 该别名**零调用方**——删除或改名的影响面为 0，无需兼容层。
3. 方案 a / b 都是改护栏去迁就一个命名错误：a 削弱护栏的前瞻能力，b 在 `TIMELESS_GET_METHODS` 里写一个「其实不是 date-blind」的条目，注释会自相矛盾。护栏判断没错，被判的名字错了。

**建议动作（须另开实现卡，本卡不执行）：** 移除 `cn_akshare_provider.py:7185` 的别名，调用方按需直接用 `qualify_cninfo_content`。护栏词表与 `TIMELESS_GET_METHODS` 均不动。

**附带发现（不并入本卡）：** 7188/7189 两个别名同样零调用。按 AGENTS.md 铁律 3（删掉死代码）应一并清理，但那是独立关注点，需单独 commit。

## 复现命令

```bash
cd /Users/davidliu/Documents/TradingAgents-AShare-dav744
env -u PYTHONPATH /Users/davidliu/Documents/TradingAgents-AShare/.venv310/bin/python -c "
import inspect
from tradingagents.dataflows.providers.registry import build_default_registry
reg=build_default_registry()
for n in reg.list_names():
    pr=reg.get(n)
    for a in dir(pr):
        if not a.startswith('get_'): continue
        f=getattr(pr,a)
        if not callable(f): continue
        try: ps=list(inspect.signature(f).parameters)
        except Exception: ps=[]
        real=getattr(f,'__name__',a)
        if real!=a or 'cutoff' in ps: print(f'{pr.name}.{a} -> {real} ({ps})')
"
grep -rn "get_cninfo_announcement_content" --include="*.py" .
```

## 关联

- 基线：`work/2026-09-08-full-suite-baseline.md` 组 2
- 引入批次：C-05 巨潮公告（DAV-659/663 附近，具体 commit 待核）
