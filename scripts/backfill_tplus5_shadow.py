#!/usr/bin/env python3
"""Track A5: v2 Winner -> T+5 Shadow 离线回填脚本 (backfill_tplus5_shadow.py).

功能与规范：
1. 扫描当前数据库 (ReportDB / reports) 或输入 JSON 样本，严格限定 completed + 合格 v2 辩论报告 (A6 同口径)；
2. 基于 A 股交易日历严格执行 T+5 回填（禁止缩短 hold 窗口）；
3. 状态分类：
   - 到期评估 (due_and_evaluated): 成功获取 T+5 价格，结合 manager_verdict.winner (bull↑/bear↓/tie±3%) 判定 hit;
   - 未到期 (pending_due): T+5 尚未到达，保持 None，不计入 due 分母；
   - 停牌 (suspension): 标的在 T+5 停牌，不计入 due 分母；
   - 行情缺失 (data_missing): 到期但无法获取价格，计入 due 分母但 hit=None (降低完整率)；
4. 幂等与只读安全：
   - 默认支持 --dry-run (仅打印统计，不写库不写盘)；
   - 写回时保留 result_data 中所有既有字段，不丢失任何上下文与分析产物；
   - 默认保持生产 flag credit_weighting_enabled=False。

CLI 参数：
  --db-path PATH      指定 SQLite 数据库路径 (可选)
  --input-file PATH   指定输入 JSON 文件路径
  --input-dir PATH    指定输入 JSON 目录路径
  --output-file PATH  指定回填产物输出 JSON 文件路径
  --as-of YYYY-MM-DD  指定评估基准日期 (默认今天)
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
from typing import Any, Dict, List, Optional

# Ensure project root in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tradingagents.agents.utils.shadow_credit import (
    backfill_tplus5_shadow_for_report,
    backfill_tplus5_shadow_for_reports,
    evaluate_h1b_system_gates,
    filter_v2_completed_reports,
    is_qualifying_v2_report,
)
from scripts.verify_h1b_gates import format_gates_matrix_text

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("backfill_tplus5_shadow")


def load_raw_reports(
    db_path: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
) -> tuple[List[Dict[str, Any]], Optional[Any]]:
    """Load raw report dictionaries and optional DB session."""
    raw_reports: List[Dict[str, Any]] = []
    db_ctx = None

    # 1. Explicit input file
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

    # 2. Explicit input directory
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
                        else:
                            raw_reports.append(data)
                    elif isinstance(data, list):
                        raw_reports.extend([dict(s) for s in data if isinstance(s, dict)])
                except Exception as e:
                    logger.debug("读取 input-dir 文件 %s 失败: %s", fpath.name, e)
            if raw_reports:
                logger.info("从目录 %s 中加载了 %d 份样本", p_dir.name, len(raw_reports))

    # 3. Try loading via sqlalchemy ReportDB if db exists
    if not raw_reports:
        try:
            from api.database import get_db_ctx, ReportDB
            ctx = get_db_ctx()
            db = ctx.__enter__()
            db_reports = db.query(ReportDB).filter(ReportDB.status == "completed").all()
            for r in db_reports:
                data = r.to_dict()
                raw_reports.append(data)
            if raw_reports:
                logger.info("从数据库中加载了 %d 份 completed 报告记录", len(raw_reports))
                db_ctx = (ctx, db, ReportDB)
            else:
                ctx.__exit__(None, None, None)
        except Exception as exc:
            logger.debug("从数据库加载报告失败 (可能无数据库或表为空): %s", exc)

    # 4. Fallback to golden audit reports
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
                logger.info("从 golden audit 中加载了 %d 份样本", len(raw_reports))

    return raw_reports, db_ctx


def format_backfill_summary_text(stats: Dict[str, Any], dry_run: bool = False) -> str:
    """Format backfill statistics into clear terminal summary."""
    hit_rate_str = f"{stats['hit_rate']:.1%}" if stats.get("hit_rate") is not None else "N/A"
    lines = [
        "=" * 80,
        "P3-H1b / Track A5: v2 Winner -> T+5 Shadow 离线回填汇总",
        "=" * 80,
        f"总扫描样本数        : {stats.get('total_scanned', 0)}",
        f"合格 v2 样本数      : {stats.get('qualifying_v2_count', 0)} "
        f"(已剔除非 completed/无 v2 winner 样本: {stats.get('skipped_non_qualifying', 0)})",
        f"T+5 到期样本数 (Due): {stats.get('due_count', 0)}",
        f"成功评估命中 (Hit)  : {stats.get('hit_count', 0)}",
        f"未命中样本 (Miss)   : {stats.get('miss_count', 0)}",
        f"行情缺失 (Missing)  : {stats.get('data_missing_count', 0)}",
        f"停牌剔除 (Suspend)  : {stats.get('suspension_count', 0)} (不计入 due 分母)",
        f"未到期窗口 (Pending): {stats.get('pending_due_count', 0)} (不计入 due 分母)",
        "-" * 80,
        f"T+5 完整率 (Completeness) : {stats.get('completeness_rate', 0.0):.1%} (门槛要求 >= 95.0%)",
        f"T+5 命中率 (Hit Rate)     : {hit_rate_str}",
        f"执行模式                  : {'DRY-RUN (演练模式，未修改数据)' if dry_run else 'ACTIVE (已落盘/已写库)'}",
        "=" * 80,
    ]
    return "\n".join(lines)


def run_backfill(
    *,
    db_path: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
    output_file: Optional[str] = None,
    as_of: Optional[str] = None,
    dry_run: bool = False,
    verify_gates: bool = False,
) -> Dict[str, Any]:
    """Execute T+5 shadow backfill pipeline."""
    raw_reports, db_ctx = load_raw_reports(
        db_path=db_path,
        input_file=input_file,
        input_dir=input_dir,
    )

    updated_reports, stats = backfill_tplus5_shadow_for_reports(
        raw_reports,
        as_of=as_of,
    )

    print(format_backfill_summary_text(stats, dry_run=dry_run))

    if not dry_run:
        # 1. Update database if loaded via DB
        if db_ctx:
            ctx, db, ReportDB = db_ctx
            try:
                for rep in updated_reports:
                    rep_id = rep.get("id")
                    if not rep_id:
                        continue
                    db_row = db.query(ReportDB).filter(ReportDB.id == rep_id).first()
                    if db_row and "result_data" in rep:
                        db_row.result_data = rep["result_data"]
                        db_row.updated_at = datetime.now(timezone.utc)
                db.commit()
                logger.info("[写库成功] 已更新数据库中 %d 条报告的 result_data", len(updated_reports))
            except Exception as exc:
                db.rollback()
                logger.error("[写库失败] 回填数据提交数据库失败: %s", exc)
            finally:
                ctx.__exit__(None, None, None)

        # 2. Write to output file or input file
        target_out = output_file or input_file
        if target_out:
            p_out = Path(target_out)
            if not p_out.is_absolute():
                p_out = Path(project_root) / p_out
            os.makedirs(p_out.parent, exist_ok=True)
            with open(p_out, "w", encoding="utf-8") as f:
                json.dump({"samples": updated_reports, "backfill_stats": stats}, f, ensure_ascii=False, indent=2)
            print(f"[产物保存] 已将回填样本数据写入: {p_out}")
    else:
        if db_ctx:
            ctx, db, ReportDB = db_ctx
            ctx.__exit__(None, None, None)

    if verify_gates:
        print("\n" + "-" * 80)
        print("【联动校验】执行 7 维门槛校验矩阵...")
        gate_res = evaluate_h1b_system_gates(updated_reports)
        print(format_gates_matrix_text(gate_res))

    return {
        "stats": stats,
        "sample_count": len(updated_reports),
        "qualifying_count": stats.get("qualifying_v2_count", 0),
        "dry_run": dry_run,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Track A5: v2 Winner -> T+5 Shadow 离线回填工具")
    parser.add_argument("--db-path", type=str, default=None, help="SQLite 数据库路径")
    parser.add_argument("--input-file", type=str, default=None, help="输入 JSON 文件路径")
    parser.add_argument("--input-dir", type=str, default=None, help="输入 JSON 目录路径")
    parser.add_argument("--output-file", type=str, default=None, help="输出 JSON 文件路径")
    parser.add_argument("--as-of", type=str, default=None, help="评估基准日期 (YYYY-MM-DD)")
    parser.add_argument("--dry-run", action="store_true", default=False, help="演练模式，不写库不写盘")
    parser.add_argument("--verify-gates", action="store_true", default=False, help="回填后同时运行门槛校验")
    parser.add_argument("-v", "--verbose", action="store_true", default=False, help="详细日志")

    args = parser.parse_args()
    if args.verbose:
        logger.setLevel(logging.DEBUG)

    run_backfill(
        db_path=args.db_path,
        input_file=args.input_file,
        input_dir=args.input_dir,
        output_file=args.output_file,
        as_of=args.as_of,
        dry_run=args.dry_run,
        verify_gates=args.verify_gates,
    )
    sys.exit(0)
