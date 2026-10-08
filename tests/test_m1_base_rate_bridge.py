"""DAV-1729 — tests for scripts/phase2/m1_base_rate_bridge.py.

Two layers:

1. Self-contained synthetic tests (always run): the join produces the
   canonical 5-column frame, the fingerprint is row-order / version_key
   stable, and an industry-less row keeps r but gets q=NaN.

2. Fingerprint-equality proof against the DAV-1706 registered frames:
   when the real P1 chunks and the m1_base_rate parquet are present (the
   DAV-1547 / DAV-1706 workstation paths), rebuild the signal frame for
   each (m, W) and assert ``fingerprint()`` equals the sha256 registered
   under ``data/phase2/m1_eval_dav1706_input/signals_p_base_m{m}_W{W}``
   in that run. Skipped on machines without those artifacts.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts" / "phase2"))
import m1_base_rate as m1            # noqa: E402
import m1_base_rate_bridge as bridge  # noqa: E402

# DAV-1706 workstation artifact roots (read-only if present; skipped else).
DAV1547_CHUNKS = Path(
    "/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/"
    "dav-1547-cfa02881b296/workdir/tradingagents-ashare-fork/"
    "data/phase2/chunks")
DAV1706_BASE = Path(
    "/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/"
    "dav-1706-489b2d268fd7/workdir/tradingagents-ashare-fork/"
    "data/phase2/m1_base_rate_dav1706/p_base_rel_t40.parquet")

# sha256 fingerprints registered for signals_p_base_m{m}_W{W}.parquet in
# data/phase2/m1_eval_dav1706_input (DAV-1706). Computed by
# bridge.fingerprint() over the canonical (signal_date, symbol)-sorted frame
# on the delivered parquets; version_key-independent.
REGISTERED_FP = {
    (50, 0):   "cbac075fd41d285569fca5cc1fbf7fde5a3e5f1798d161bb9c6933da59b9a56f",
    (200, 0):  "ddab16a4d9b465b8de25371efe2cf00cfde49ec728bc0c6eb35c0f23d081b6c9",
    (500, 0):  "6545dc94ed658203dbc6151cb1edf160bdacb935484a043ce46233f5ffa40555",
    (50, 250): "889b559a1763502af7d58d9100a65eb55f2adfbefba21c25de4d4b7375f6d436",
    (200, 250): "2e0be110653878716dfc21177d0e2155980d1621d85533b54af48722f57ceee1",
    (500, 250): "4ec8103b2294aba17d69e2aa694262abdbb4e167aff0f638449a6b47dd4a2844",
    (50, 500): "20c7d3ae63a6e929828e7517c76e155bc3409308f888ee77b524f58ca21342f1",
    (200, 500): "78efe709ce317acd2365a45d7f5f59f0ea23c864a14f67bd249528ade1c3fd27",
    (500, 500): "9bc371145fabcc4b6f6f97b8e340a6ee407850df44581270e7b5f72c3913e037",
}

# The alias file signals_p_base_m200_W0_expanding.parquet registered the
# same canonical fingerprint as m200_W0 (version_key relabel only).
ALIAS_FP = {"p_base_m200_W0_expanding": REGISTERED_FP[(200, 0)]}


def _mini_panel():
    return pd.DataFrame(
        [{"signal_date": "20200102", "mat": "20200102", "l1": "801010.SI",
          "y": 1.0},
         {"signal_date": "20200103", "mat": "20200103", "l1": "801010.SI",
          "y": 0.0},
         {"signal_date": "20200103", "mat": "20200103", "l1": "801780.SI",
          "y": 1.0}])


def _mini_chunks(tmp: Path) -> Path:
    df = pd.DataFrame(
        [{"ts_code": "000001.SZ", "signal_date": "20200103",
          "sw_l1_code": "801010.SI", "r_rel": 0.02},
         {"ts_code": "000002.SZ", "signal_date": "20200103",
          "sw_l1_code": "801780.SI", "r_rel": -0.01},
         {"ts_code": "000003.SZ", "signal_date": "20200103",
          "sw_l1_code": "", "r_rel": 0.005}])   # industry-less row
    cdir = tmp / "chunks" / "year=2020"
    cdir.mkdir(parents=True)
    df.to_parquet(cdir / "data.parquet", index=False)
    return cdir.parent


def _mini_base(tmp: Path) -> Path:
    base, _ = m1.build_pit_base_rates(_mini_panel(),
                                      shrink_ms=(50, 200), window_days=(0,))
    p = tmp / "p_base.parquet"
    base.to_parquet(p, index=False)
    return p


class TestBridgeSchema:
    def test_canonical_columns_and_sort(self, tmp_path):
        out = bridge.build_signals(_mini_chunks(tmp_path),
                                   _mini_base(tmp_path), 200, 0, "v")
        assert list(out.columns) == bridge.OUT_COLS
        # frame is canonically sorted by (signal_date, symbol)
        expected = (out.sort_values(["signal_date", "symbol"],
                                    kind="mergesort")
                       .reset_index(drop=True))
        assert out.reset_index(drop=True).equals(expected)

    def test_industryless_row_keeps_r_but_q_nan(self, tmp_path):
        out = bridge.build_signals(_mini_chunks(tmp_path),
                                   _mini_base(tmp_path), 200, 0, "v")
        row = out[out["symbol"] == "000003.SZ"].iloc[0]
        assert pd.isna(row["q"]) and row["r"] == pytest.approx(0.005)

    def test_q_is_base_rate_cell(self, tmp_path):
        out = bridge.build_signals(_mini_chunks(tmp_path),
                                   _mini_base(tmp_path), 200, 0, "v")
        r = out[out["symbol"] == "000001.SZ"].iloc[0]
        # 801010.SI matured n=2 s=1.0 (two matured labels for that industry);
        # market matured n=3 s=2.0 → p_mkt = 2/3.
        # p_m200 = (s + m·p_mkt) / (n + m) = (1 + 200·(2/3)) / 202.
        p_mkt = 2.0 / 3.0
        assert r["q"] == pytest.approx((1.0 + 200 * p_mkt) / 202.0)


class TestFingerprint:
    def test_row_order_and_vkey_invariant(self, tmp_path):
        chunks, base = _mini_chunks(tmp_path), _mini_base(tmp_path)
        a = bridge.build_signals(chunks, base, 200, 0, "v1")
        b = (bridge.build_signals(chunks, base, 200, 0, "v2")
                  .sample(frac=1.0, random_state=7).reset_index(drop=True))
        assert bridge.fingerprint(a) == bridge.fingerprint(b)

    def test_fingerprint_changes_with_q(self, tmp_path):
        chunks, base = _mini_chunks(tmp_path), _mini_base(tmp_path)
        a = bridge.build_signals(chunks, base, 200, 0, "v")
        c = a.copy()
        c.loc[c.index[0], "q"] = 0.12345
        assert bridge.fingerprint(a) != bridge.fingerprint(c)


class TestDAV1706FingerprintMatch:
    """Rebuild each (m, W) frame from the real artifacts and prove the
    committed bridge reproduces the registered sha256 fingerprint."""

    @pytest.mark.skipif(
        not (DAV1547_CHUNKS.is_dir() and DAV1706_BASE.is_file()),
        reason="DAV-1547 chunks / DAV-1706 base-rate parquet not present")
    @pytest.mark.parametrize("m,W,expected", [
        (m, W, fp) for (m, W), fp in REGISTERED_FP.items()])
    def test_registered_fingerprint(self, m, W, expected):
        out = bridge.build_signals(DAV1547_CHUNKS, DAV1706_BASE,
                                   m, W, f"p_base_m{m}_W{W}")
        assert bridge.fingerprint(out) == expected

    @pytest.mark.skipif(
        not (DAV1547_CHUNKS.is_dir() and DAV1706_BASE.is_file()),
        reason="DAV-1547 chunks / DAV-1706 base-rate parquet not present")
    def test_alias_vkey_same_fingerprint(self):
        # version_key relabel alone must not change the fingerprint
        m, W = 200, 0
        out = bridge.build_signals(DAV1547_CHUNKS, DAV1706_BASE,
                                   m, W, "p_base_m200_W0_expanding")
        assert bridge.fingerprint(out) == ALIAS_FP[
            "p_base_m200_W0_expanding"]
