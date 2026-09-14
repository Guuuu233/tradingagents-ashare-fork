# DAV-910 / P2-65：生产 Compose 测试与脚本挂载收口

## 结论

DAV-910 已完成实施、代码审核和线性合入。当前目标分支的最新代码提交为
`2daad463ff004dac97986e39983100be03599521`；线上服务没有重启，仍运行代码 SHA
`6cc4e15227efcb602d63f1ec9a49a4d7ca7cc8e1`。本次只改变生产 Compose 配置，不代表该配置已经在运行中的服务上生效。

## 变更与门禁

- 开工时目标分支：`origin/codex/dav-4-p2a-trunk`
- 候选提交：`2daad463ff004dac97986e39983100be03599521`
- 候选直接父：`1e634c1f91f8f8b05b6a5754c1549c1d47030059`
- 候选分支：`origin/agent/2/01a09f42`
- 实施卡：DAV-910，由 `资深开发2` 完成
- 同 SHA 审查：**代码审核员**完成只读 PASS；审查候选与父提交、完整 diff 和 Compose 解析均为上述 SHA
- 实际文件清单：

  ```text
  M       docker-compose.yml
  ```

- 实际变更：删除 `./tests:/app/tests` 与 `./scripts:/app/scripts`；保留
  `./data:/app/data`、`./api:/app/api`、`./tradingagents:/app/tradingagents`。
- `docker compose config --quiet`：退出码 `0`；解析后的运行挂载只有上述三项，没有
  `/app/tests` 或 `/app/scripts`。
- `docker-compose.split.yml`、`Dockerfile`、`docker-entrypoint.py` 均与父提交逐字一致：

  ```text
  docker-compose.split.yml  634124cbd3c7575d910550f7edb4a1596e94f800ab3e893ff43f550b757d8e9f
  Dockerfile                171a045ebcb25ce869e64c62e976d77d0305ecf8a529a57cbb88a223c814dc23
  docker-entrypoint.py      fb85a73c77eac2b893b8ff6589bfb4c71eaeb8f1e1d15db93c41389488ee031f
  ```

## 合入

在干净 detached 集成树中确认候选直接父等于目标分支 tip 后执行 `git merge --ff-only`，无合并提交；随后推送并回读：

```text
target before:  1e634c1f91f8f8b05b6a5754c1549c1d47030059
candidate:      2daad463ff004dac97986e39983100be03599521
remote after:   2daad463ff004dac97986e39983100be03599521
```

## 未执行事项

- 未构建 Docker 镜像。
- 未启动、停止或重启线上服务，未执行部署切换。
- 未写入生产数据库，未运行真实分析，未采集真实社交数据或调用真实 Fuyao 天梯上游。
- 未修改 `docker-compose.split.yml`、镜像构建文件或业务代码。

验证过程中发现本机 `~/.docker/cli-plugins/docker-compose` 原来指向已不存在的
AppTranslocation 路径。为执行本卡规定的配置校验，验证环境将该链接指向现有 Docker.app
内的 Compose v5.3.1 插件；这是本机验证环境修复，不属于仓库提交，也没有改变生产服务。

## 后续边界

P2-65 只收口生产 Compose 的源码暴露面；前端仍跟踪的 `frontend/.vade-report` 是另一个独立
工件清理项（P2-54），不得混入本提交。DAV-808、真实业务证据、社交 Gate 和 V-03a 到期门禁均不受本卡解锁。
