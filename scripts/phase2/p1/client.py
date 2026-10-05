"""Tushare gateway client with resumable off-repo caching.

Reads TUSHARE_TOKEN and TUSHARE_API_URL from the project .env only into the
process environment; never prints or writes credentials. Each unique (api,
frozen params, fields) call is cached as a pickle file under the off-repo cache
directory so a long fetch can be interrupted and resumed without re-pulling.
A manifest.json records per-file sha256/rows for D-036 reproducibility.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

import pandas as pd
import requests

_CACHE_ROOT = Path(
    os.getenv(
        "P1_CACHE_DIR",
        os.path.expanduser("~/Documents/TradingAgents-AShare-cache/phase2"),
    )
)
_ENV_PATH = Path(
    os.getenv(
        "P1_ENV_PATH",
        os.path.expanduser("~/Documents/TradingAgents-AShare/.env"),
    )
)
_PAGE_LIMIT = 6000  # Tushare single-call row cap observed ~4000-6000; keep headroom


def _load_env() -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not _ENV_PATH.exists():
        return out
    for raw in _ENV_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        out[k.strip()] = v.strip()
    return out


def _ensure_credentials() -> None:
    env = _load_env()
    for k in ("TUSHARE_TOKEN", "TUSHARE_API_URL", "TUSHARE_BASE_URL"):
        if not os.getenv(k) and env.get(k):
            os.environ[k] = env[k]


def _token() -> str:
    _ensure_credentials()
    tok = os.getenv("TUSHARE_TOKEN", "").strip()
    if not tok:
        raise RuntimeError("TUSHARE_TOKEN not available in env or .env")
    return tok


def _url() -> str:
    _ensure_credentials()
    return (
        os.getenv("TUSHARE_API_URL", "").strip()
        or os.getenv("TUSHARE_BASE_URL", "").strip()
        or "https://api.tushare.pro"
    )


def _cache_dir() -> Path:
    d = _CACHE_ROOT / "api_cache"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _manifest_path() -> Path:
    _CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    return _CACHE_ROOT / "manifest.json"


def _load_manifest() -> Dict[str, Any]:
    p = _manifest_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {"files": {}}
    return {"files": {}}


def _save_manifest(m: Dict[str, Any]) -> None:
    _manifest_path().write_text(json.dumps(m, indent=1, ensure_ascii=False))


def _cache_key(api: str, params: Dict[str, Any], fields: str) -> str:
    blob = json.dumps(
        {"api": api, "params": params, "fields": fields}, sort_keys=True
    )
    return hashlib.sha1(blob.encode()).hexdigest()[:24]


class TushareClient:
    """Single-entry cached caller. All requests block to completion."""

    def __init__(self, session: Optional[requests.Session] = None) -> None:
        self._s = session or requests.Session()

    def _post(self, api: str, params: Dict[str, Any], fields: str) -> pd.DataFrame:
        payload = {
            "api_name": api,
            "token": _token(),
            "params": params,
            "fields": fields,
        }
        r = self._s.post(_url(), json=payload, timeout=60)
        j = r.json()
        if j.get("code") != 0:
            raise RuntimeError(f"{api} error {j.get('code')}: {j.get('msg','')[:120]}")
        data = j.get("data") or {}
        cols = data.get("fields") or []
        items = data.get("items") or []
        return pd.DataFrame(items, columns=cols)

    def call(
        self,
        api: str,
        params: Optional[Dict[str, Any]] = None,
        fields: str = "",
        paginate: bool = False,
        resume: bool = True,
    ) -> pd.DataFrame:
        """Call api with params; if paginate=True, page until a short page.

        Results cached per (api, params-minus-limit/offset, fields). A full
        paginated result set is stored under the same cache key so resuming a
        partially-pulled api restarts that api from scratch (safe, idempotent).
        """
        params = dict(params or {})
        params.pop("limit", None)
        params.pop("offset", None)
        key = _cache_key(api, params, fields)
        fpath = _cache_dir() / f"{api}_{key}.pkl"
        if resume and fpath.exists():
            return pd.read_pickle(fpath)

        if not paginate:
            df = self._post(api, params, fields)
        else:
            frames = []
            offset = 0
            while True:
                p = dict(params)
                p["limit"] = _PAGE_LIMIT
                p["offset"] = offset
                chunk = self._post(api, p, fields)
                if chunk.empty:
                    break
                frames.append(chunk)
                if len(chunk) < _PAGE_LIMIT:
                    break
                offset += _PAGE_LIMIT
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

        df.to_pickle(fpath)
        m = _load_manifest()
        m["files"][fpath.name] = {
            "api": api,
            "params": params,
            "fields": fields,
            "rows": int(len(df)),
            "sha256": hashlib.sha256(fpath.read_bytes()).hexdigest(),
            "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _save_manifest(m)
        return df


def load_cached(api: str, params: Optional[Dict[str, Any]] = None, fields: str = "") -> Optional[pd.DataFrame]:
    params = dict(params or {})
    params.pop("limit", None)
    params.pop("offset", None)
    key = _cache_key(api, params, fields)
    fpath = _cache_dir() / f"{api}_{key}.pkl"
    if fpath.exists():
        return pd.read_pickle(fpath)
    return None
