# 凭据暴露事件记录（2026-09-20）

状态：**未闭环**。旧 key 仍在公开仓库历史中，吊销状态未证实，经用户裁定作为残余风险接受。

本文件不记录任何凭据值。发现方法见 §5，可随时复跑。

## 1. 摘要

在为数据库备份清理补齐 dangling object 扫描时，对全部 5602 个 Git 对象（含不可达）做凭据扫描，
发现一个 LLM 网关凭据以明文存在于**公开仓库 `origin/main` 的可达历史**中，自 2026-07-28 起至今。

| 项 | 结论 |
|---|---|
| 凭据类型 | `TA_API_KEY` —— CLIProxyAPI（LLM 网关）出站凭据，见 `tradingagents/default_config.py:15` |
| 仓库可见性 | **公开**（未认证 `api.github.com` 请求返回 200） |
| 是否在当前 HEAD | 否。HEAD 对该 key 0 命中 |
| 是否在可达历史 | **是**，`origin/main` 及数百个 `origin/agent/*` 分支 |
| 是否已吊销 | **未证实**，见 §4 |
| 其他真实凭据 | 除本条外，全对象扫描未发现新的真实凭据 |

## 2. 暴露清单

### 2.1 旧 TA_API_KEY（同一个值，三处）

| 路径 | 行 | 可达性 |
|---|---|---|
| `README_本地部署.md` | 52, 91 | **可达**，提交 `0fefaa1`（2026-07-28）与 `b131f4d`（2026-08-03），均为 `origin/main` 祖先 |
| `.env.bak-docker` | 3 | 不可达，本地对象库残留（仓库有 6 个 stash，疑为来源） |
| `work/analysis_runs/run_full_analysis.py` | 13 | 不可达，硬编码为 `os.environ.get` 的默认值 |

`README_本地部署.md:91` 原文注明该 key 曾通过 `http://localhost:8317/v1/models` 验证有效，
说明它在写入时是可用凭据，不是占位符。该文件当前 HEAD 版本已不含 key。

### 2.2 Linggo API key

用户指出该凭据曾在 2026-09-20 会话中贴出。值不记录。若尚未销毁，应按已暴露凭据撤销或轮换。

## 3. 排除项（非凭据）

全对象扫描共 33 个 blob 命中，除 §2.1 三处外全部为非凭据：

- 占位符：`.env.example` 1-2、`Docker-readme.md` 150/151/181、`README.md` 120、`skills/tradingagents-analysis/SKILL.md` 多处，均为 `your_..._here` 形态
- 源码常量：`api/services/auth_service.py` 35/36 行的默认开发密钥，本就是公开源码的默认值
- 测试夹具：`tests/` 下 5 个文件 15 处，含多条「key 不得泄进错误信息」的负向断言
- 正则误命中：`announcements.json:4` 是一个 `"id"` 字段，值形如 `2026-03-17-ta` + `sk-` + `persistence-and-recovery`。
  `sk-[A-Za-z0-9_-]{16,}` 会从 `task-` 中间咬进去。同类还有 npm 包名 `…-ta`+`sk-list-item` 与大量 `ri`+`sk-` 前缀。
  本行刻意拆写，避免本文件自身触发扫描。

## 4. 已验证与未验证

**已验证**：该 key 与 `.env` 当前 `TA_API_KEY` 值不同；当前 HEAD 全仓库对该 key 0 命中。

**未验证——吊销状态**。对两个网关端点各做三组探测，结果：

| 端点 | 旧 key | 当前 key | 明显无效 key |
|---|---|---|---|
| `100.65.130.33:8317/v1/models` | 401 | 401 | 401 |
| `100.67.61.23:8317/v1/models` | 401 | 401 | 401 |

**阳性对照失败**：当前 key 同样返回 401，三者同码，该探测不具判别力。
原因是账户实际使用的 key 存放在 `user_llm_configs.api_key_encrypted`（见 `AGENTS.md` §10.2），
`.env` 的 `TA_API_KEY` 只是兜底默认值，其自身不被网关接受属正常。

**因此不得声称旧 key 已吊销。** 要确认只能在 CPA 服务端核对已配置 key 列表，那是独立主机，本次未触碰。

## 5. 复现方法

```sh
# 1. 枚举全部对象（含不可达/dangling）
git cat-file --batch-all-objects --batch-check='%(objecttype) %(objectname)' \
  | awk '$1=="blob"{print $2}' > /tmp/all_blobs.txt

# 2. 逐 blob 扫描（模式表见下），只输出命中的 blob sha
cat /tmp/all_blobs.txt | xargs -P 8 -I{} sh -c \
  'git cat-file blob {} 2>/dev/null | grep -qE -f /tmp/credpat.txt && echo {}'

# 3. 解析可达路径与归属提交
git rev-list --objects --all > /tmp/reach.txt
git log --all --oneline --find-object=<blob>
git merge-base --is-ancestor <commit> origin/main   # 判断是否已公开
```

模式表覆盖：`sk-`、`ghp_`/`gho_`/`ghs_`、`AKIA`、`xox[baprs]-`、`AIza`、JWT、PEM 私钥块，
以及 `TOKEN|SECRET|PASSWORD|API_KEY` 后跟 16 位以上值。

**已知陷阱**：`git log -S` 搜的是精确字符串。本次最初用 `-S'TA_API_KEY=sk-'` 得到 0 个提交，
是假阴性——文件里写的是 `TA_API_KEY: sk-…`（冒号）。改用 `-S'sk-<前缀>'` 才命中。
**不要用带分隔符的模式去证明凭据不存在。**

## 6. 用户裁定（2026-09-20）

1. 当前 key 只保留在 `.env` 等被忽略文件中，**不得**写入 README、脚本、日志或提交。
2. 旧 key 已在公开 `origin/main` 历史中，不得再称「未上传」；吊销状态未知，**继续保留属于明确的残余风险接受**。
3. 暂不执行 `git filter-repo` 历史重写。确认仓库是否应公开、通知协作者后再议。
4. 不自行吊销、不改仓库权限。

## 7. 未执行声明

本次未重写历史、未删除分支、未更改仓库可见性、未吊销任何凭据、未修改数据库备份或 git worktree。
对网关的探测为只读 `GET /v1/models`。`README_本地部署.md` 的 HEAD 版本未被改动。
