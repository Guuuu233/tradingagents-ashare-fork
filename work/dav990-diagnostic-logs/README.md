# DAV-990 诊断日志归档

2026-09-20 从 `/private/tmp/dav990-*` 四个目录抽取，原目录随磁盘清理删除。

## 内容

- 四个子目录：`run.log` / `monitor.log` / `faulthandler.log` / `nodes.log` 及各时长 `*.sample.log`
- `dav990_probe.py`：探针源码。原本四个目录各有一份 `plugin/dav990_probe.py`，
  md5 全部为 `f88ceff32913cb0509a017a3b41bd1dd`，此处存一份即可。

## 刻意未归档

四个巨型采样文件共 284 MiB，随原目录删除：

| 文件 | 大小 |
|---|---|
| `dav990-probe-current3/at-cpu.sample.log` | 75 MiB |
| `dav990-rt16-control/at-cpu.sample.log` | 72 MiB |
| `dav990-single-consumption/first.sample.log` | 70 MiB |
| `dav990-no-api-control/at-cpu.sample.log` | 67 MiB |

## 为什么用 git add -f

`.gitignore:256` 的 `*.log` 会吞掉本目录每一个文件，导致 `git ls-files` 与
`git status` 双双为空——一次 `git clean -fX` 即无声销毁。故强制入库。
