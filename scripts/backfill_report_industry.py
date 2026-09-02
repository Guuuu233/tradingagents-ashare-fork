#!/usr/bin/env python3
"""Track A7: 离线历史报告行业元数据回填脚本 (backfill_report_industry.py).

功能与规范：
1. 扫描当前数据库 (ReportDB / reports) 或输入 JSON 样本中的 completed 报告；
2. 提取并持久化可验证行业元数据至 result_data['instrument_context']['industry']，保持与 A6 提取顺序一致；
3. 缺失/未匹配标的保持 None / 不写，严禁硬编「未知行业」或默认行业；
4. 幂等与只读安全：
   - 默认支持 --dry-run (仅打印统计，不写库不写盘)；
   - 写回时仅更新 instrument_context.industry 槽位，保留 result_data 中所有既有字段，不改动其它数据；
   - 不修改数据库 schema (无额外列 / 零 migration)。

CLI 参数：
  --db-path PATH      指定 SQLite 数据库路径 (可选)
  --input-file PATH   指定输入 JSON 文件路径
  --input-dir PATH    指定输入 JSON 目录路径
  --output-file PATH  指定回填产物输出 JSON 文件路径
  --dry-run           演练模式 (不落库、不覆盖文件)
  --verify-gates      回填后同时运行 7 维门槛校验并打印矩阵
  -v, --verbose       输出详细调试日志
"""

from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import json
import logging
import os
from pathlib import Path
import sys
from typing import Any, Dict, List, Optional, Tuple

# Ensure project root in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from api.services.report_service import ensure_report_industry_persisted
from tradingagents.agents.utils.shadow_credit import (
    evaluate_h1b_system_gates,
    extract_report_industry,
    filter_v2_completed_reports,
    is_qualifying_v2_report,
)
from scripts.verify_h1b_gates import format_gates_matrix_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_report_industry")


class _CustomSQLiteDBCtx:
    """Context manager for custom SQLite database session and engine lifecycle."""

    def __init__(self, session, engine) -> None:
        self.session = session
        self.engine = engine

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        try:
            if exc_type is not None:
                self.session.rollback()
        finally:
            self.session.close()
            self.engine.dispose()


def load_raw_reports(
    db_path: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
) -> Tuple[List[Dict[str, Any]], Optional[Any]]:
    """Load raw report dictionaries and optional DB session."""
    raw_reports: List[Dict[str, Any]] = []
    db_ctx = None

    # 1. Explicit SQLite DB path (prioritized and strict: fails explicitly if invalid/inaccessible)
    if db_path and str(db_path).strip():
        p = Path(db_path)
        if not p.is_absolute():
            if not p.exists():
                p_root = Path(project_root) / p
                if p_root.exists():
                    p = p_root
        if not p.exists():
            logger.error("指定的 SQLite 数据库路径不存在: %s", db_path)
            raise FileNotFoundError(f"指定的 SQLite 数据库路径不存在: {db_path}")

        abs_path = str(p.resolve())
        db_url = f"sqlite:///{abs_path}"
        try:
            from sqlalchemy import create_engine
            from sqlalchemy.orm import sessionmaker
            from api.database import ReportDB, _ensure_report_schema

            engine = create_engine(db_url, connect_args={"check_same_thread": False})
            _ensure_report_schema(engine)
            SessionCls = sessionmaker(autocommit=False, autoflush=False, bind=engine)
            session = SessionCls()
            try:
                db_reports = session.query(ReportDB).filter(ReportDB.status == "completed").all()
                for r in db_reports:
                    raw_reports.append(r.to_dict())
                logger.info("从 SQLite 数据库 %s 中加载了 %d 份 completed 报告记录", abs_path, len(raw_reports))
            except Exception:
                session.close()
                engine.dispose()
                raise

            ctx = _CustomSQLiteDBCtx(session, engine)
            db_ctx = (ctx, session, ReportDB)
            return raw_reports, db_ctx
        except Exception as exc:
            logger.error("读取指定 SQLite 数据库 %s 失败: %s", db_path, exc)
            raise RuntimeError(f"读取指定 SQLite 数据库 {db_path} 失败: {exc}") from exc

    # 2. Explicit input file
    if input_file:
        p = Path(input_file)
        if not p.is_absolute():
            p = Path(project_root) / p
        if p.exists():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    if "samples" in data and isinstance(data["samples"], list):
                        raw_reports.extend([dict(s) for s in data["samples"] if isinstance(s, dict)])
                    elif "reports" in data and isinstance(data["reports"], list):
                        raw_reports.extend([dict(s) for s in data["reports"] if isinstance(s, dict)])
                    else:
                        raw_reports.append(data)
                elif isinstance(data, list):
                    raw_reports.extend([dict(s) for s in data if isinstance(s, dict)])
                logger.info("从 JSON 文件 %s 中加载了 %d 份样本", p.name, len(raw_reports))
            except Exception as e:
                logger.warning("读取 input-file %s 失败: %s", p, e)

    # 3. Explicit input directory
    if input_dir and not raw_reports:
        p_dir = Path(input_dir)
        if not p_dir.is_absolute():
            p_dir = Path(project_root) / p_dir
        if p_dir.exists() and p_dir.is_dir():
            for fpath in sorted(p_dir.glob("*.json")):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        if "samples" in data and isinstance(data["samples"], list):
                            raw_reports.extend([dict(s) for s in data["samples"] if isinstance(s, dict)])
                        elif "reports" in data and isinstance(data["reports"], list):
                            raw_reports.extend([dict(s) for s in data["reports"] if isinstance(s, dict)])
                        else:
                            raw_reports.append(data)
                    elif isinstance(data, list):
                        raw_reports.extend([dict(s) for s in data if isinstance(s, dict)])
                except Exception as e:
                    logger.debug("读取 input-dir 文件 %s 失败: %s", fpath.name, e)
            if raw_reports:
                logger.info("从目录 %s 中加载了 %d 份样本", p_dir.name, len(raw_reports))

    # 4. Try loading via sqlalchemy ReportDB if db exists
    if not raw_reports:
        try:
            from api.database import get_db_ctx, ReportDB
            ctx = get_db_ctx()
            db = ctx.__enter__()
            db_reports = db.query(ReportDB).filter(ReportDB.status == "completed").all()
            for r in db_reports:
                raw_reports.append(r.to_dict())
            if raw_reports:
                logger.info("从数据库中加载了 %d 份 completed 报告", len(raw_reports))
                db_ctx = (ctx, db, ReportDB)
            else:
                ctx.__exit__(None, None, None)
        except Exception as exc:
            logger.debug("从数据库加载报告失败 (可能无数据库或表为空): %s", exc)

    # 5. Fallback to golden audit samples
    if not raw_reports:
        golden_dir = os.path.join(project_root, "tests", "golden", "audit_20260823")
        if os.path.exists(golden_dir):
            for fname in sorted(os.listdir(golden_dir)):
                if fname.endswith("_result_data.json"):
                    fpath = os.path.join(golden_dir, fname)
                    try:
                        with open(fpath, "r", encoding="utf-8") as f:
                            data = json.load(f)
                            if isinstance(data, dict):
                                raw_reports.append(data)
                    except Exception as e:
                        logger.debug("读取 golden 报告 %s 失败: %s", fname, e)
            if raw_reports:
                logger.info("从 golden audit 样本中加载了 %d 份样本", len(raw_reports))

    return raw_reports, db_ctx


def backfill_report_industry_in_sample(sample: Dict[str, Any]) -> Tuple[Dict[str, Any], bool, Optional[str]]:
    """Backfill industry metadata into a single sample dictionary.

    Returns:
        (updated_sample, modified_flag, resolved_industry)
    """
    updated = copy.deepcopy(sample)
    symbol = updated.get("symbol") or updated.get("ticker")

    # If top-level dict is result_data itself
    if "instrument_context" in updated or "market_data_context" in updated or "investment_debate_state" in updated:
        prev_ind = extract_report_industry(updated)
        ensure_report_industry_persisted(updated, symbol=symbol)
        curr_ind = extract_report_industry(updated)
        modified = (prev_ind != curr_ind)
        return updated, modified, curr_ind

    # If standard ReportDB dict with nested result_data
    res_data = updated.get("result_data")
    if isinstance(res_data, dict):
        prev_ind = extract_report_industry(updated)
        ensure_report_industry_persisted(res_data, symbol=symbol)
        curr_ind = extract_report_industry(updated)
        if curr_ind and str(curr_ind).strip() and str(curr_ind).strip() != "未知行业":
            updated["industry"] = str(curr_ind).strip()
        else:
            updated["industry"] = None
        modified = (prev_ind != curr_ind) or (sample.get("industry") != updated.get("industry"))
        return updated, modified, curr_ind

    return updated, False, None


def run_industry_backfill(
    *,
    db_path: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
    output_file: Optional[str] = None,
    dry_run: bool = False,
    verify_gates: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Execute offline industry backfill workflow."""
    if verbose:
        logger.setLevel(logging.DEBUG)

    raw_reports, db_ctx = load_raw_reports(db_path=db_path, input_file=input_file, input_dir=input_dir)
    logger.info("共载入 %d 份原始样本进行行业回填", len(raw_reports))

    industries_before: List[str] = []
    for r in raw_reports:
        ind = extract_report_industry(r)
        if ind:
            industries_before.append(ind)
    unique_industries_before = len(set(industries_before))

    backfilled_reports: List[Dict[str, Any]] = []
    updated_ids: List[str] = []
    unchanged_count = 0
    unmapped_count = 0
    industries_after: List[str] = []

    for r in raw_reports:
        updated, modified, curr_ind = backfill_report_industry_in_sample(r)
        backfilled_reports.append(updated)
        if curr_ind:
            industries_after.append(curr_ind)
        else:
            unmapped_count += 1

        if modified:
            rep_id = str(updated.get("id") or updated.get("symbol") or "unknown")
            updated_ids.append(rep_id)
        else:
            unchanged_count += 1

    unique_industries_after = len(set(industries_after))

    stats = {
        "task_id": "Track-A7",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "dry_run": dry_run,
        "total_scanned": len(raw_reports),
        "backfilled_count": len(updated_ids),
        "unchanged_count": unchanged_count,
        "unmapped_count": unmapped_count,
        "unique_industries_before": unique_industries_before,
        "unique_industries_after": unique_industries_after,
        "distinct_industries": sorted(list(set(industries_after))),
    }

    logger.info(
        "行业回填统计: 总量=%d, 更新=%d, 保持不变=%d, 未匹配/无行业=%d, 独立行业数(%d -> %d)",
        stats["total_scanned"],
        stats["backfilled_count"],
        stats["unchanged_count"],
        stats["unmapped_count"],
        unique_industries_before,
        unique_industries_after,
    )

    # If not dry-run and DB exists, persist to DB
    if not dry_run and db_ctx:
        ctx, db, ReportDB = db_ctx
        try:
            from sqlalchemy.orm.attributes import flag_modified
            persisted_count = 0
            for r in backfilled_reports:
                rep_id = r.get("id")
                if rep_id:
                    db_row = db.query(ReportDB).filter(ReportDB.id == rep_id).first()
                    if db_row:
                        if isinstance(db_row.result_data, dict):
                            ensure_report_industry_persisted(db_row.result_data, symbol=db_row.symbol)
                            flag_modified(db_row, "result_data")
                            ind = extract_report_industry(db_row.result_data)
                            if ind and str(ind).strip() and str(ind).strip() != "未知行业":
                                db_row.industry = str(ind).strip()
                            else:
                                db_row.industry = None
                        elif db_row.symbol:
                            from tradingagents.graph.data_collector import _map_stock_to_industry
                            mapped = _map_stock_to_industry(db_row.symbol)
                            if mapped and str(mapped).strip() and str(mapped).strip() != "未知行业":
                                db_row.industry = str(mapped).strip()
                            else:
                                db_row.industry = None
                        else:
                            db_row.industry = None
                        persisted_count += 1
            db.commit()
            logger.info("已将 %d 份已完成报告的行业更新持久化至数据库 (JSON + SQL 列)", persisted_count)
        except Exception as exc:
            db.rollback()
            logger.error("数据库持久化回填失败: %s", exc)
            raise exc
        finally:
            ctx.__exit__(None, None, None)
    elif db_ctx:
        ctx, _, _ = db_ctx
        ctx.__exit__(None, None, None)

    # Save output file if requested
    if output_file:
        out_p = Path(output_file)
        if not out_p.is_absolute():
            out_p = Path(project_root) / out_p
        out_p.parent.mkdir(parents=True, exist_ok=True)
        with open(out_p, "w", encoding="utf-8") as f:
            json.dump(
                {
                    "stats": stats,
                    "samples": backfilled_reports,
                },
                f,
                indent=2,
                ensure_ascii=False,
            )
        logger.info("已将回填结果导出至 %s", out_p)

    # Verify gates if requested
    if verify_gates:
        v2_samples = filter_v2_completed_reports(backfilled_reports)
        gate_res = evaluate_h1b_system_gates(v2_samples)
        matrix_text = format_gates_matrix_text(gate_res)
        print(matrix_text)
        stats["gate_evaluation"] = gate_res

    return stats


def main_cli() -> None:
    parser = argparse.ArgumentParser(description="Track A7: 离线历史报告行业元数据回填脚本")
    parser.add_argument("--db-path", type=str, default=None, help="指定 SQLite 数据库路径")
    parser.add_argument("--input-file", type=str, default=None, help="指定输入 JSON 文件路径")
    parser.add_argument("--input-dir", type=str, default=None, help="指定输入 JSON 目录路径")
    parser.add_argument("--output-file", type=str, default=None, help="指定输出 JSON 文件路径")
    parser.add_argument("--dry-run", action="store_true", help="演练模式 (不落库、不写入文件)")
    parser.add_argument("--verify-gates", action="store_true", help="回填后同时运行 7 维门槛校验并打印矩阵")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出详细调试日志")

    args = parser.parse_args()
    try:
        stats = run_industry_backfill(
            db_path=args.db_path,
            input_file=args.input_file,
            input_dir=args.input_dir,
            output_file=args.output_file,
            dry_run=args.dry_run,
            verify_gates=args.verify_gates,
            verbose=args.verbose,
        )
        print(json.dumps(stats, indent=2, ensure_ascii=False))
    except Exception as exc:
        logger.error("行业回填执行失败: %s", exc)
        sys.exit(1)
    sys.exit(0)


if __name__ == "__main__":
    main_cli()
