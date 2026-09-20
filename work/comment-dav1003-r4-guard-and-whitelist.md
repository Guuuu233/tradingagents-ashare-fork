## 静态守卫补三处 + 白名单台账更正

**本条是返修派工**，基于对返修中工作树（`9667fbb` + 未提交改动）的只读复核。已写的 `test_static_guard_no_direct_baostock_import_outside_accessor` 方向正确，但**三处可被绕过**。

### ① 扫描范围漏掉生产目录 `scheduler/`

当前 `scan_dirs = [tradingagents, api]`。实际含 `.py` 的目录：

```
api/          25 个
scheduler/     2 个    ← 生产目录，漏扫
scripts/      12 个    ← 建议纳入或书面说明排除理由
tradingagents/ 121 个
```

`scheduler/` 目前虽无违规，但它是生产路径，必须纳入扫描，否则下一个洞会开在那里。

### ② 放行粒度错误：按整文件放行

```python
allowed_files = {(repo_root / "tradingagents" / "dataflows" / "providers" / "cn_baostock_provider.py").resolve()}
```

将来在**同一文件里**新增一个旁路函数直接 `import baostock`，测试照样绿——而那正是最容易发生的位置。

**要求**：改为按**允许的函数作用域**放行。只允许硬化实现体 / 访问器那几个函数内部出现 baostock 导入；该文件内其他位置一律视为违规。

### ③ 逐行正则覆盖不足（已实测）

对当前 pattern `^\s*(?:import\s+baostock\b|from\s+baostock\b)` 的实测结果：

```
拦截 | import baostock as bs
拦截 |     import baostock
拦截 | from baostock.util import socketutil
漏过 | import os, baostock
漏过 | bs = importlib.import_module("baostock")
漏过 | bs = __import__("baostock")
漏过 | getattr(__import__("baostock.util", fromlist=["x"]), "x", None)
```

**要求**：改用 **AST**：

- 遍历 `ast.Import` / `ast.ImportFrom`，判断模块名等于 `baostock` 或以 `baostock.` 开头
  （`ast.Import` 天然覆盖 `import os, baostock` 这类多名导入）
- 额外检测**动态导入**：`importlib.import_module(...)` 与 `__import__(...)` 的字面量实参含 `baostock`

### 白名单台账更正（新复审卡的判定依据）

本轮新增修改了：

```
tradingagents/eval/v03_return_measure.py
```

**已越出原 10 文件白名单。** 这是修第二入口所**必需的合理扩围**，予以认可——但新候选**必须在交付报告中显式将其纳入白名单并说明理由，白名单更新为 11 个文件**。

否则新复审卡按旧白名单核验时应**直接打回**（越界改动是硬门禁，不接受事后口头解释）。

### 其余不变

- 加严 1：同一显式 loop、五步两断点、③⑤ 各加 `is prior_executor` 与 `_shutdown is False`
- 加严 2：真实 provider 链路、禁 `side_effect=` 注入、`bs.login()` 零调用、refusal 非终态且 `total_scanned > 0`
- 阻断 3：硬化覆盖所有 baostock 使用点、RT-NETWORK 跑到自然结束
- 新 SHA 重跑 RT-FULL-OFFLINE + RT-NETWORK，走新开复审卡；主线 `5a0320f` 继续 HOLD

（已同步 steer 至运行中的 run `01a0aa41`，送达成功；此评论为双发留档。）
