# DAV-218：DAV-214/215 合入部署只读预检

## 固定状态

- 当前远端主干：`7ef89f63662ce01bacdcda9dd0996060ce903c83`
- 当前候选：`7c2f19ab3f5d1176b235306bf3421fdbc14570b5`，后续可能追加仅格式修复SHA
- 当前宿主服务healthz：`7ef89f63`
- 当前active reports应为0

## 只读任务

1. 核验候选祖先关系、合入是否可clean fast-forward；
2. 检查宿主tracked/untracked WIP，尤其`uv.lock`及备份路径；
3. 检查监听8000的PID、cwd、数据库文件、启动环境；
4. 检查`.env`所需配置仅存在性，不输出值；
5. 制定最短部署命令与回滚点；
6. 制定正确账户`davidliu022305@gmail.com`、user_id `429163f7-50b6-4982-8bdf-96ae99506843`的3/3复验步骤；
7. 固定资金流验收：同股同日Tushare-DC 1d值、THS旁证、selection、attempt chain；
8. 输出READY/BLOCKED和证据。

禁止合入、修改代码、重启服务、发起真实报告。审核PASS与格式新SHA确认后才执行写操作。立即开展。