[@资深开发工程师2](mention://agent/5fd6e9a0-8540-40ea-a9d6-e358ab37a0fc) 返修 P2-T12 quality gate。

起点 SHA：`f1c73e7f0b1650fc654e63fdc92f40a2876855fc`（不准予合入）。
按 `work/issue-p2-t12-quality-gate-repair.md` / 本卡 description：修正 `evaluate_social_depth` 对 disabled/legacy 的误杀，并把 `sentiment_report` 接入 `apply_report_quality_gate`。TDD；推送后 `in_review` 并交独立审核员。

禁止 FF、部署、删 legacy、@调度助手。
