#!/usr/bin/env python3
"""DAV-1729 — M1 base-rate bridge: join P1 chunk stock rows with PIT
``p_base_rel_t40`` industry cells → ``m1_eval`` input frame.

This is glue only, NOT an evaluation-criteria change. It was the
workstation bridge used in DAV-1706; this commit lands it in the repo so the
``m1_eval_dav1706_input`` signal frames can be regenerated and fingerprinted
deterministically.

Mapping (per row, after column canonicalisation):
  q = p_m{m} at (signal_date, sw_l1_code) for the chosen (m, W); NaN when
      no matured history (n=0) or industry missing → m1_eval counts it as
      excl_bad_prob (honest denominator, nothing silently dropped).
  r = r_rel (decimal stock-minus-SW-index, T+1 open → actual-exit window);
      NaN where the P1 label is not yet matured → excl_immature_label.
  No timing_class / input_pit_status columns are emitted: m1_eval defaults
      them to F0 / VERIFIED (formal queue).

Determinism contract: the emitted frame is sorted by (signal_date, symbol)
and carries exactly five columns in fixed order — (signal_date, symbol,
version_key, q, r). signal_date is the canonical YYYYMMDD string, symbol is
the ts_code string, q and r are float64. The ``fingerprint`` below hashes
those four data columns (version_key excluded — it is a label, not signal
data) as ``str.cat('|')`` strings / ``repr``-formatted floats over the
canonical sort, so the same inputs always produce the same digest.

Usage:
  python scripts/phase2/m1_base_rate_bridge.py \
      --chunks-dir <P1 chunks> --base-rate <p_base_rel_t40.parquet> \
      --m 200 --W 0 --version-key p_base_m200_W0 \
      --out-dir data/phase2/m1_eval_dav1706_input
  python scripts/phase2/m1_base_rate_bridge.py --all9 ...
  python scripts/phase2/m1_base_rate_bridge.py --selftest
"""

from __future__ import annotations

import argparse
import hashlib
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_CHUNKS = Path(
    "/Users/davidliu/multica_workspaces_steer/davidsworks-d70c6ff76b54/"
    "dav-1547-cfa02881b296/workdir/tradingagents-ashare-fork/"
    "data/phase2/chunks")
DEFAULT_BASE = Path(
    "data/phase2/m1_base_rate_dav1706/p_base_rel_t40.parquet")
DEFAULT_OUTDIR = Path("data/phase2/m1_eval_dav1706_input")

# Sensitivity grid kept in lock-step with DAV-1706 (9 (m, W) combos).
MS = (50, 200, 500)
WS = (0, 250, 500)

# canonical output schema — order fixed so fingerprint is byte-stable
OUT_COLS = ["signal_date", "symbol", "version_key", "q", "r"]


def log(msg: str) -> None:
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _build_qmap(base: pd.DataFrame, m: int, W: int) -> dict:
    """(signal_date, l1) -> p_m{m} lookup restricted to window W."""
    col = f"p_m{m}"
    sub = base.loc[base["W"] == W, ["signal_date", "l1", col]]
    keys = zip(sub["signal_date"].astype(str).to_numpy(),
               sub["l1"].astype(str).to_numpy())
    return dict(zip(keys, sub[col].astype(float).to_numpy()))


def fingerprint(df: pd.DataFrame) -> str:
    """Canonical sha256 of the signal frame, row-order independent.

    Sorts by (signal_date, symbol) then hashes four payload columns:
    signal_date / symbol as '|'-joined strings; q / r as float64 bytes
    via ``numpy.tobytes()`` after ``nan_to_num(nan=-9999.0)`` (a stable
    byte form — float64 little-endian — not text repr). version_key is
    excluded — it is a run label, not signal data, so frames produced
    with different version_keys over the same (m, W) still fingerprint
    identically. This digest matches the fingerprints registered for the
    DAV-1706 ``m1_eval_dav1706_input`` parquets.
    """
    d = df.sort_values(["signal_date", "symbol"], kind="mergesort")
    h = hashlib.sha256()
    h.update(d["signal_date"].astype(str).str.cat(sep="|").encode())
    h.update(d["symbol"].astype(str).str.cat(sep="|").encode())
    for col in ("q", "r"):
        a = np.nan_to_num(d[col].to_numpy(dtype=float), nan=-9999.0)
        h.update(a.tobytes())
    return h.hexdigest()


def build_signals(chunks_dir: Path, base_rate_path: Path,
                  m: int, W: int, version_key: str) -> pd.DataFrame:
    """Join every P1 chunk row to its (signal_date, l1) p_m{m} cell.

    Streams partitions one at a time (read-only, slim columns only — the
    chunks are never copied); concatenates then sorts canonically so the
    result is independent of partition/row order.
    """
    base = pd.read_parquet(base_rate_path)
    qmap = _build_qmap(base, m, W)
    del base

    parts = sorted(chunks_dir.glob("year=*/data.parquet"))
    if not parts:
        raise FileNotFoundError(
            f"no year=*/data.parquet under {chunks_dir}")
    frames = []
    for pq in parts:
        # read-only: only the 4 columns needed, nothing wider
        df = pd.read_parquet(
            pq, columns=["ts_code", "signal_date", "sw_l1_code", "r_rel"])
        df["signal_date"] = df["signal_date"].astype(str)
        df["l1"] = df["sw_l1_code"].fillna("").astype(str)
        df["q"] = [
            qmap.get(k, np.nan)
            for k in zip(df["signal_date"].to_numpy(),
                         df["l1"].to_numpy())
        ]
        df["r"] = pd.to_numeric(df["r_rel"], errors="coerce").astype(float)
        df["symbol"] = df["ts_code"].astype(str)
        df["version_key"] = version_key
        frames.append(df[OUT_COLS])
        del df
    out = pd.concat(frames, ignore_index=True)
    del frames
    out = (out.sort_values(["signal_date", "symbol"], kind="mergesort")
              .reset_index(drop=True))
    return out


def emit(out: pd.DataFrame, version_key: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"signals_{version_key}.parquet"
    out.to_parquet(path, index=False)
    n_q = int(out["q"].notna().sum())
    n_r = int(out["r"].notna().sum())
    n_both = int((out["q"].notna() & out["r"].notna()).sum())
    log(f"[{version_key}] rows={len(out):,} q_valid={n_q:,} "
        f"r_valid={n_r:,} both={n_both:,} "
        f"sha256={fingerprint(out)} -> {path}")
    return path


# ---------------------------------------------------------------------------
# Self-test (synthetic only — proves join + canonical fingerprint stability)
# ---------------------------------------------------------------------------

def _mini_panel():
    return pd.DataFrame(
        [{"signal_date": "20200102", "mat": "20200102", "l1": "801010.SI",
          "y": 1.0},
         {"signal_date": "20200103", "mat": "20200103", "l1": "801010.SI",
          "y": 0.0},
         {"signal_date": "20200103", "mat": "20200103", "l1": "801780.SI",
          "y": 1.0}])


def selftest(tmp: Path) -> dict:
    import m1_base_rate as m1  # sibling module, same dir
    panel = _mini_panel()
    base, _ = m1.build_pit_base_rates(panel, shrink_ms=(50, 200),
                                      window_days=(0,))
    # fabricate a chunk partition keyed the same way
    chunk = pd.DataFrame(
        [{"ts_code": "000001.SZ", "signal_date": "20200103",
          "sw_l1_code": "801010.SI", "r_rel": 0.02},
         {"ts_code": "000002.SZ", "signal_date": "20200103",
          "sw_l1_code": "801780.SI", "r_rel": -0.01},
         {"ts_code": "000003.SZ", "signal_date": "20200103",
          "sw_l1_code": "", "r_rel": 0.005}])   # no industry line
    cdir = tmp / "chunks" / "year=2020"
    cdir.mkdir(parents=True)
    chunk.to_parquet(cdir / "data.parquet", index=False)
    bp = tmp / "p_base.parquet"
    base.to_parquet(bp, index=False)

    a = build_signals(cdir.parent, bp, m=200, W=0, version_key="v")
    assert list(a.columns) == OUT_COLS
    # canonical sort: by (signal_date, symbol)
    assert a["signal_date"].is_monotonic_increasing
    same = a.sort_values(["signal_date", "symbol"], kind="mergesort")
    assert a.reset_index(drop=True).equals(same.reset_index(drop=True))
    # the industry-less row gets q=NaN (no cell) but keeps its r
    miss = a[a["symbol"] == "000003.SZ"].iloc[0]
    assert pd.isna(miss["q"]) and miss["r"] == 0.005
    # fingerprint is stable across a reshuffle and a vkey relabel
    b = (build_signals(cdir.parent, bp, m=200, W=0,
                       version_key="v_relabelled")
         .sample(frac=1.0, random_state=0).reset_index(drop=True))
    assert fingerprint(a) == fingerprint(b)
    print(f"[selftest] PASS fp={fingerprint(a)[:16]}…", flush=True)
    return {"fp": fingerprint(a)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--chunks-dir", type=Path, default=DEFAULT_CHUNKS)
    ap.add_argument("--base-rate", type=Path, default=DEFAULT_BASE)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUTDIR)
    ap.add_argument("--m", type=int, default=200)
    ap.add_argument("--W", type=int, default=0)
    ap.add_argument("--version-key", default="p_base_m200_W0")
    ap.add_argument("--all9", action="store_true",
                    help="emit all 9 (m ∈ {50,200,500} × W ∈ {0,250,500}) "
                         "frames, named signals_p_base_m{m}_W{W}.parquet")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--selftest-dir", type=Path, default=None,
                    help="scratch dir for --selftest (default: mktemp)")
    a = ap.parse_args()

    if a.selftest:
        import tempfile
        root = a.selftest_dir or Path(tempfile.mkdtemp(prefix="m1bridge_"))
        selftest(root)
        return 0

    if a.all9:
        for W in WS:
            for m in MS:
                vk = f"p_base_m{m}_W{W}"
                emit(build_signals(a.chunks_dir, a.base_rate, m, W, vk),
                     vk, a.out_dir)
    else:
        emit(build_signals(a.chunks_dir, a.base_rate, a.m, a.W,
                           a.version_key), a.version_key, a.out_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
