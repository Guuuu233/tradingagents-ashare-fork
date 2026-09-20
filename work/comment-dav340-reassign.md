worker1 连续3次 stream disconnected（运行时故障，非代码问题）。泳道DE改派：[@高级开发·支援](mention://agent/04cc525b-70a1-44ee-ad8f-2afc0c6d04ff) 请接手 DAV-340 泳道DE任务。

执行纪律不变：
- 基线 b26d040，分支 fix/ta-audit-de
- D1→D2→E1 同分支串行（都动 api/main.py）
- llm_call_logs 只向前修复，不回填历史 2962 行
- 宿主 .venv310 跑测试（env -u PYTHONPATH）
- 完成后推远端回报精确 SHA 等复审
- 禁碰其他泳道文件

任务正文见本卡 description。
