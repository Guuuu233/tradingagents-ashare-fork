@代码运维测试员 开工 H1b 样本补齐 Batch5。

授权：用户已确认「A15 后做样本补齐」。
说明：`work/2026-09-03-h1b-sample-fill-batch5-dispatch.md`
主干 tip：`31c32f0f877e86fc3c06eb58a34b4dc08a453044`

要点：一日一标的、避开已占用 18 个交易日、请求级仅 `v2_debate_enabled=true`、保持 3/1、不开加权、不部署。

注意：Cursor 探测 `127.0.0.1:8000/healthz` 当前不可达——先拉起本地 API 再跑队列。跑完复跑 `verify_h1b_gates.py --db-path data/tradingagents.db` 并贴矩阵。
