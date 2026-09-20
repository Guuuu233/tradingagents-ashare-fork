[@项目主管](mention://agent/4503de74-0fb3-457d-88a7-3db8db04ff8e) [@资深开发1](mention://agent/6050b57e-f551-4756-8ad9-3af522d7d4e3) [@项目调度助手](mention://agent/826abb3f-34c0-4d9a-afff-2a0b1a002221)

DAV-119 当前仍为 in_progress；DAV-136 已验收关闭，但 DAV-119 尚未完成。请立即启动唯一剩余实现门 DAV-139：基线 target/codex/dav-4-p2a-trunk @ f1f55d144f15fa54157ee5e67cbde2f3b57ec0ef，只改 data_collector.py + 一个既有测试文件，处理真实 manual_calibration_gap 的 collector failure ledger/provenance 传播。不要读取父历史，不复用长上下文 run，不改 provider/配置/用户设置/凭据/主干。

DAV-139 交付新远端 branch/SHA、.venv310 精确测试（缺失则如实报告）、compileall、diff-check 和结构化 reason/source/status 证据；新 SHA 与只读复审前不解锁 DAV-122 或 DAV-120/121/123/124/125。