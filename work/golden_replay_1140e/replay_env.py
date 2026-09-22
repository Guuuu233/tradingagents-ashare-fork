"""DAV-1179：replay artifact 运行身份采集与 NO_MATCH 归因。

所有 replay/验收脚本共用：禁止硬编码候选 SHA，运行身份一律实读。
"""
from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def _git(repo_root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=True, capture_output=True, text=True,
    ).stdout.strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_run_identity(
    repo_root: Path,
    *,
    baseline_ref: str | None = None,
    corpus_path: Path | None = None,
    manifest_path: Path | None = None,
) -> dict:
    """采集运行身份。candidate 与 baseline 字段语义分离：

    - candidate_head_sha: 运行时实读 git HEAD（40 位）；
    - baseline_ref / baseline_sha: baseline 是显式指定的引用（默认 HEAD^，
      即直接父提交）及其解析结果，与 candidate 分开记录；
    - worktree_dirty: 是否有未提交改动（脏树上重放必须可识别）；
    - corpus_manifest_sha256 / corpus_path: 冻结语料指纹与路径；
    - run_at_utc: 运行时间。
    """
    repo_root = Path(repo_root)
    head = _git(repo_root, "rev-parse", "HEAD")
    ref = baseline_ref or "HEAD^"
    try:
        baseline_sha = _git(repo_root, "rev-parse", f"{ref}^{{commit}}")
    except subprocess.CalledProcessError:
        baseline_sha = None
    identity = {
        "candidate_head_sha": head,
        "baseline_ref": ref,
        "baseline_sha": baseline_sha,
        "worktree_dirty": bool(_git(repo_root, "status", "--porcelain")),
        "run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python_version": sys.version.split()[0],
        "python_executable": sys.executable,
        "platform": platform.platform(),
    }
    if corpus_path is not None:
        corpus_path = Path(corpus_path)
        identity["corpus_path"] = str(corpus_path)
        identity["corpus_exists"] = corpus_path.exists()
    if manifest_path is not None:
        manifest_path = Path(manifest_path)
        identity["corpus_manifest_path"] = str(manifest_path)
        identity["corpus_manifest_sha256"] = (
            sha256_file(manifest_path) if manifest_path.exists() else None
        )
    return identity


def classify_no_match(old: dict, new_evs_by_claim: dict) -> str:
    """归因一条 NO_MATCH：区分 claim 缺失、记录拆分/合并与 raw key 改写。

    - claim_absent_in_replay: 该 claim_id 在新重放输出中完全消失；
    - record_split_or_merge: 同 claim 下存在与旧 raw 互为子串的新 raw
      （claim 拆分/合并导致的 key 漂移）；
    - raw_key_mismatch: 同 claim 有新记录但 raw 文本被改写。
    """
    claim_id = old.get("claim_id")
    raw = str(old.get("raw", ""))
    same_claim = new_evs_by_claim.get(claim_id) or []
    if not same_claim:
        return "claim_absent_in_replay"
    for ev in same_claim:
        new_raw = str(ev.get("raw", ""))
        if raw and new_raw and (raw in new_raw or new_raw in raw):
            return "record_split_or_merge"
    return "raw_key_mismatch"


def index_by_claim(new_evs) -> dict:
    by_claim: dict = {}
    for ev in new_evs or []:
        if isinstance(ev, dict):
            by_claim.setdefault(ev.get("claim_id"), []).append(ev)
    return by_claim
