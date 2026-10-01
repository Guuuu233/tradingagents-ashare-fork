"""Durable name-map storage and an independently complete Sina A-share source.

The archive lives in per-user state, not a release/worktree. It contains public
instrument names only; it never opens the application's business database.
"""
from __future__ import annotations

import json
import logging
import os
import re
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional, Tuple

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)
_SOURCE_ROOT = Path(__file__).resolve().parents[2]
_ARCHIVE_VERSION = 1
_SYMBOL_RE = re.compile(r"[0-9]{6}\.(SH|SZ|BJ)")
_SINA_BASE_URL = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center."
# Sina silently caps larger requested pages at 100; using the actual cap is
# essential to avoid accepting only the first fraction of the stock universe.
_SINA_PAGE_SIZE = 100
_SINA_MIN_STOCK_COUNT = 4000
_SINA_MAX_STOCK_COUNT = 20000
_SINA_REQUEST_TIMEOUT = (5, 10)
# Sina paging jitters often: a read timeout on one page usually recovers on a
# retry, so each request gets a few attempts before the fetch is abandoned.
_SINA_MAX_ATTEMPTS = 3
_SINA_RETRY_BACKOFF_SECONDS = 0.5
# Worst case ~56 pages; measured clean pull is ~66-74s. 240s leaves room for a
# handful of recovered page timeouts plus slow pages, while still aborting the
# run when the overall budget is exhausted.
_SINA_TOTAL_TIMEOUT_SECONDS = 240


def archive_path() -> Path:
    configured = os.getenv("TA_STOCK_MAP_ARCHIVE_PATH", "").strip()
    state_home = Path(os.getenv("XDG_STATE_HOME") or Path.home() / ".local" / "state")
    path = Path(configured).expanduser() if configured else state_home / "tradingagents" / "stock-map.json"
    if not path.is_absolute():
        raise ValueError("StockMap archive path must be absolute and outside releases")
    path = path.resolve()
    if _SOURCE_ROOT in path.parents or "releases" in path.parts:
        raise ValueError("StockMap archive must not live inside a worktree/release")
    return path


def _validate_archive(payload: dict) -> Tuple[Dict[str, str], str]:
    if not isinstance(payload, dict) or payload.get("schema_version") != _ARCHIVE_VERSION:
        raise ValueError("unsupported stock-map archive schema")
    saved_at = payload.get("saved_at")
    if not isinstance(saved_at, str) or datetime.fromisoformat(saved_at).tzinfo is None:
        raise ValueError("archive saved_at must be a timezone-aware ISO timestamp")
    names = payload.get("name_to_code")
    if not isinstance(names, dict) or not names:
        raise ValueError("archive contains no stock names")
    if any(not isinstance(name, str) or not name.strip() or not isinstance(code, str)
           or not _SYMBOL_RE.fullmatch(code) for name, code in names.items()):
        raise ValueError("archive contains invalid names or symbols")
    stock_count, fund_count = payload.get("stock_count"), payload.get("fund_count")
    if (type(stock_count) is not int or type(fund_count) is not int
            or stock_count <= 0 or fund_count < 0 or stock_count + fund_count != len(names)):
        raise ValueError("archive counts do not match its name map")
    return names, saved_at


def read_archive() -> Optional[Tuple[Dict[str, str], str]]:
    try:
        path = archive_path()
        payload = json.loads(path.read_text(encoding="utf-8"))
        names, saved_at = _validate_archive(payload)
    except FileNotFoundError:
        logger.info("[StockMap] No local archive available")
        return None
    except (OSError, ValueError, TypeError, UnicodeError) as exc:
        logger.warning("[StockMap] Cannot read archive: %s", exc)
        return None
    logger.info("[StockMap] Loaded archive: %s names, saved_at=%s, path=%s", len(names), saved_at, path)
    return names, saved_at


def write_archive(names: Dict[str, str], stock_count: int, fund_count: int) -> None:
    """Atomic replacement keeps the last good archive intact on a failed write."""
    temp_path = None
    try:
        payload = {
            "schema_version": _ARCHIVE_VERSION,
            "saved_at": datetime.now(timezone.utc).isoformat(),
            "stock_count": stock_count,
            "fund_count": fund_count,
            "name_to_code": names,
        }
        _validate_archive(payload)
        path = archive_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=path.name + ".", suffix=".tmp", delete=False) as f:
            temp_path = Path(f.name)
            json.dump(payload, f, ensure_ascii=False, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)
        logger.info("[StockMap] Saved archive: %s names, saved_at=%s, path=%s", len(names), payload["saved_at"], path)
    except (OSError, ValueError, TypeError) as exc:
        # A read-only filesystem must not turn a usable live map into failure.
        logger.warning("[StockMap] Cannot write archive: %s", exc)
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink(missing_ok=True)
            except OSError as exc:
                logger.warning("[StockMap] Cannot clean temporary archive: %s", exc)


# The HTTPAdapter Retry only retries HTTP status codes; read timeouts and
# connection-layer drops surface as exceptions and are retried here instead.
_SINA_RETRIABLE_ERRORS = (
    requests.Timeout,
    requests.ConnectionError,
    requests.exceptions.ChunkedEncodingError,
)


def _sina_json(session: requests.Session, endpoint: str, params: dict, deadline: float):
    attempt = 0
    while True:
        if time.monotonic() >= deadline:
            raise requests.Timeout("Sina stock-map total deadline exceeded")
        try:
            response = session.get(_SINA_BASE_URL + endpoint, params=params, timeout=_SINA_REQUEST_TIMEOUT)
            response.raise_for_status()
            return response.json()
        except _SINA_RETRIABLE_ERRORS as exc:
            attempt += 1
            if attempt >= _SINA_MAX_ATTEMPTS:
                raise
            wait = min(_SINA_RETRY_BACKOFF_SECONDS * attempt, max(deadline - time.monotonic(), 0))
            logger.info("[StockMap] Sina %s attempt %d failed (%s); retrying", endpoint, attempt, exc)
            if wait > 0:
                time.sleep(wait)


def _sina_row(row: dict) -> Tuple[str, str]:
    if not isinstance(row, dict):
        raise ValueError("Sina stock row is not an object")
    symbol, code, name = row.get("symbol"), row.get("code"), row.get("name")
    if (not isinstance(symbol, str) or not re.fullmatch(r"(sh|sz|bj)[0-9]{6}", symbol)
            or code != symbol[2:] or not isinstance(name, str) or not name.strip()):
        raise ValueError("Sina stock row has invalid symbol/code/name")
    return name.strip(), f"{code}.{symbol[:2].upper()}"


def fetch_sina_stock_names() -> Dict[str, str]:
    """Read all hs_a pages; reject truncation/duplicates rather than archive them.

    Count and field contract: Sina's public Market_Center API, also used by
    AkShare stock_zh_a_spot. Quote/price fields are deliberately not consumed.
    """
    deadline = time.monotonic() + _SINA_TOTAL_TIMEOUT_SECONDS
    with requests.Session() as session:
        # Domestic public names need no proxy credentials or ambient auth.
        session.trust_env = False
        session.mount("https://", HTTPAdapter(max_retries=Retry(
            total=1, backoff_factor=0.2, status_forcelist=[429, 500, 502, 503, 504],
        )))
        raw_count = _sina_json(session, "getHQNodeStockCount", {"node": "hs_a"}, deadline)
        if not isinstance(raw_count, (str, int)) or isinstance(raw_count, bool):
            raise ValueError("Sina returned an invalid stock count")
        count = int(raw_count)
        if not _SINA_MIN_STOCK_COUNT <= count <= _SINA_MAX_STOCK_COUNT:
            raise ValueError(f"Sina stock count out of range: {count}")
        names: Dict[str, str] = {}
        codes = set()
        for page in range(1, (count + _SINA_PAGE_SIZE - 1) // _SINA_PAGE_SIZE + 1):
            if time.monotonic() >= deadline:
                raise requests.Timeout("Sina stock-map total deadline exceeded")
            rows = _sina_json(session, "getHQNodeData", {
                "node": "hs_a", "page": page, "num": _SINA_PAGE_SIZE, "sort": "symbol", "asc": 1,
            }, deadline)
            expected = min(_SINA_PAGE_SIZE, count - (page - 1) * _SINA_PAGE_SIZE)
            if not isinstance(rows, list) or len(rows) != expected:
                raise ValueError(f"Sina incomplete page {page}; expected {expected} rows")
            for row in rows:
                name, code = _sina_row(row)
                if code in codes or name in names:
                    raise ValueError("Sina returned duplicate stock names/symbols")
                names[name] = code
                codes.add(code)
        if len(names) != count or {code[-2:] for code in codes} != {"SH", "SZ", "BJ"}:
            raise ValueError("Sina did not return a complete Shanghai/Shenzhen/Beijing name map")
    logger.info("[StockMap] Loaded Sina backup: %s A-share names", len(names))
    return names
