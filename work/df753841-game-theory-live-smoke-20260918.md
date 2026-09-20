# df753841 production Game Theory live smoke

## Scope

- Runtime: `df7538413ba7bb55593b1757feaf90b0bd514d1c`
- Endpoint: `POST /v1/analyze`
- Auth identity: `davidliu022305@gmail.com` (`429163f7-50b6-4982-8bdf-96ae99506843`)
- Symbol: `600519.SH`
- Trade date: `2026-09-17`
- Selected analysts: `['market']`
- Horizons: `['short']`
- `dry_run`: `false`
- Job/report id: `f8c59465b4934717ac3327c7abfc70af`

## Result

- Job completed in about 180 seconds; no job error.
- Decision: `NO_TRADE`
- `analysis_status`: `ABSTAIN`
- `trade_action`: `NO_TRADE`
- `risk_status`: `BLOCKED`
- `game_theory_report`: present, 895 characters.
- Persisted report field equals the same field in `result_data`.
- `game_theory_signals`: present and read back from `result_data`.
- Persisted trace includes `game_theory_analyst` with:
  - `source_mode=deterministic_game_theory`
  - `bundle_id=game_theory_v1`
  - `source_status=available`
  - evidence refs for `scale_metrics`, `fund_flow_individual`, `lhb`, `margin_trading`, `shareholder_count`, and `zt_pool`

## Runtime/database evidence

- Reports before: `completed=793`, `failed=616`, total `1409`.
- Reports after: `completed=794`, `failed=616`, total `1410`.
- The authenticated user's report count increased from `318` to `319`.
- SQLite `quick_check`: `ok`.
- `/healthz` still reports the exact `df753841` SHA and `executor_queued=0`.

## Boundary and caveat

- This proves the production graph reachability, deterministic Game Theory trace, report persistence, and readback path.
- It does not prove every upstream data source is available. The report recorded eight explicit data gaps; the Game Theory report stated 3/4 core data items were usable and kept the final action at `NO_TRADE`.
- No code, deployment, personal model settings, production data cleanup, social collection, H1b activation, or permanent API token was changed in this smoke.
