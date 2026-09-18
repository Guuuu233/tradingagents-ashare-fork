# a290f18 ABSTAIN 修复生效性受控验证（2026-09-19）

## 范围（按授权）
- 单账户 `429163f7-50b6-4982-8bdf-96ae99506843`、单标的 `600036.SH / 2026-09-18`、一次 v2 分析
- `selected_analysts=["market"]`、`horizons=["short"]`、`config_overrides.v2_debate_enabled=true`
- 不开社交 active、不开 H1b 加权、不批量刷样本

## 新报告 `c21456dd9c614b3587192cd7ad629de7`
- status=completed、decision=NO_TRADE、direction=N/A
- analysis_status=**ABSTAIN**（被真闸拦，非误杀）
- trade_action=NO_TRADE、risk_status=BLOCKED

## 旧闸未再拦（修复生效的直接证据）
reason_codes：`fund_flow_guard:not_checked`、`direction_evidence_blocked`、`fund_flow_consensus_guard`、`risk_verdict:blocked`
- **无 `unadjudicated_material_claims_adopt`** → DAV-1068 去重裁决态缺陷未触发
- **无 E-04 priced-in / 超预期拦截** → DAV-1071 引述/条件豁免生效

## cohort（D-009 §5）
`decision_model.v1` / `evidence_contract.v1` / `price_basis.unspecified`，`generated_by_commit_sha=a290f18`，`qualifying_v2=True`，非 legacy_unversioned。

## 完整性
- reports 1735→1736（+1 新报告），completed 977→978，failed 758 不变
- `quick_check=ok`；`/healthz` 回读 `a290f18`、status ok
- 生产库主文件除新增该报告外无其他写入

## 边界
一次样本只证明 a290f18 这条路径上两个旧闸不再误杀；不能据此证明 ABSTAIN 整体下降。真实下降率须看后续批次中 A/E-04 签名命中占比是否回落。
