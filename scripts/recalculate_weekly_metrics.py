#!/usr/bin/env python3
"""P3-H2.3 周度离线复算工具与看板生成器 (recalculate_weekly_metrics.py).

功能与规范：
1. 纯离线、只读批处理脚本，支持从数据库 (ReportDB/reports) 或 Mock 数据集聚合计算单周与全量累计指标；
2. 聚合计算多空验证率差值 (Δv)、克隆率、Challenge 采纳率、T+5 完整率与命中率，并支持行业、模型、市场状态多维下钻；
3. 严格遵循 P3-H2.0 Schema 契约 (weekly_metrics_v1, weekly_summary_md_v1, evaluation_matrix_v1)；
4. 纯幂等落盘：生成 `work/evaluations/week_{YYYYWW}_metrics.json` 与 `week_{YYYYWW}_summary.md`；
5. 自动原子维护软链/别名 `work/evaluations/latest_metrics.json` 与 `latest_summary.md`；
6. 0 外部网络与 0 外部 API 依赖，只读安全。

CLI 参数：
  --week YYYYWW       指定计算特定周 (例如 202634 或 week_202634)
  --all               全量累计复算 (检测所有周分别生成周报，并生成全量累计指标)
  --dry-run           仅输出打印分析结果，不写磁盘文件
  --output-dir PATH   自定义产物输出路径 (默认 work/evaluations/)
  --db-path PATH      指定 SQLite 数据库路径 (可选)
  --input-file PATH   指定输入 JSON 文件路径 (单个样本集/周指标)
  --input-dir PATH    指定输入 JSON 样本目录路径
  --use-mock          无数据库或输入文件时强制使用合成 60-sample Mock 数据集
  --format {text,json,markdown} 输出格式 (默认 text)
"""

from __future__ import annotations

import argparse
from datetime import date, datetime, timezone
import json
import logging
import os
from pathlib import Path
import re
import shutil
import sys
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

# Ensure project root is in sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from tradingagents.agents.utils.evaluation_schemas import (
    EVALUATION_MATRIX_SCHEMA_VERSION,
    WEEKLY_METRICS_SCHEMA_VERSION,
    WEEKLY_SUMMARY_MD_SCHEMA_VERSION,
    EvaluationMetricMatrix,
    WeeklyMetricsJSON,
    WeeklyMetricsJSONModel,
    WeeklySummaryMDModel,
    build_evaluation_metric_matrix,
    build_weekly_metrics,
    calculate_drilldown_by_industry,
    calculate_drilldown_by_model,
    calculate_drilldown_by_regime,
    render_weekly_summary_markdown,
    validate_evaluation_matrix,
    validate_weekly_metrics,
    validate_weekly_summary_md,
)
from tests.mock_evaluations.mock_scenarios import (
    generate_60_sample_weekly_dataset,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("recalculate_weekly_metrics")


def normalize_week_identifier(raw_week: Optional[str]) -> Optional[str]:
    """Normalize user week inputs (e.g. '202634', 'week_202634', '2026-W34') to 'week_YYYYWW'."""
    if not raw_week:
        return None
    s = str(raw_week).strip().lower()
    match = re.search(r"(\d{4})\D*(\d{2})", s)
    if match:
        year, week_num = match.group(1), match.group(2)
        return f"week_{year}{week_num}"
    if s.startswith("week_"):
        return s
    return f"week_{s}"


def extract_week_from_date(date_str: Optional[str]) -> str:
    """Extract week_YYYYWW from an ISO date string (YYYY-MM-DD)."""
    if not date_str:
        today = date.today()
        y, w, _ = today.isocalendar()
        return f"week_{y}{w:02d}"
    try:
        clean_d = str(date_str)[:10]
        dt = date.fromisoformat(clean_d)
        y, w, _ = dt.isocalendar()
        return f"week_{y}{w:02d}"
    except (ValueError, TypeError):
        today = date.today()
        y, w, _ = today.isocalendar()
        return f"week_{y}{w:02d}"


def extract_sample_trade_date(sample: Mapping[str, Any]) -> str:
    """Extract normalized YYYY-MM-DD trade date from a report or evaluation matrix."""
    q1 = sample.get("quadrant_1_protocol_metadata")
    if isinstance(q1, Mapping) and q1.get("trade_date"):
        return str(q1.get("trade_date"))[:10]
    if sample.get("trade_date"):
        return str(sample.get("trade_date"))[:10]
    if sample.get("date"):
        return str(sample.get("date"))[:10]
    if sample.get("created_at"):
        return str(sample.get("created_at"))[:10]
    return date.today().isoformat()


def load_reports(
    *,
    db_path: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
    use_mock: bool = False,
) -> List[Dict[str, Any]]:
    """Load reports from input-file, input-dir, database, golden files, or mock datasets."""
    reports: List[Dict[str, Any]] = []

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
                    w_id = data.get("week_identifier")
                    if "samples" in data and isinstance(data["samples"], list):
                        logger.info("从 JSON 文件 %s 中加载了 %d 份样本 (WeeklyMetrics 结构)", p.name, len(data["samples"]))
                        res = []
                        for s in data["samples"]:
                            if isinstance(s, dict):
                                item = dict(s)
                                if w_id:
                                    item["_source_week_identifier"] = w_id
                                res.append(item)
                        return res
                    reports.append(data)
                elif isinstance(data, list):
                    reports.extend([dict(s) for s in data if isinstance(s, dict)])
                logger.info("从 JSON 文件 %s 中加载了 %d 份报告样本", p.name, len(reports))
                return reports
            except Exception as e:
                logger.warning("读取 input-file %s 失败: %s", p, e)

    # 2. Explicit input directory
    if input_dir:
        p_dir = Path(input_dir)
        if not p_dir.is_absolute():
            p_dir = Path(project_root) / p_dir
        if p_dir.exists() and p_dir.is_dir():
            for fpath in sorted(p_dir.glob("*.json")):
                try:
                    with open(fpath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        w_id = data.get("week_identifier")
                        if "samples" in data and isinstance(data["samples"], list):
                            for s in data["samples"]:
                                if isinstance(s, dict):
                                    item = dict(s)
                                    if w_id:
                                        item["_source_week_identifier"] = w_id
                                    reports.append(item)
                        else:
                            reports.append(data)
                    elif isinstance(data, list):
                        reports.extend([dict(s) for s in data if isinstance(s, dict)])
                except Exception as e:
                    logger.debug("读取 input-dir 文件 %s 失败: %s", fpath.name, e)
            if reports:
                logger.info("从目录 %s 中加载了 %d 份样本", p_dir.name, len(reports))
                return reports

    # 3. Database (ReportDB / SQLite)
    try:
        from api.database import get_db_ctx, ReportDB
        with get_db_ctx() as db:
            db_query = db.query(ReportDB)
            # Fetch completed reports
            db_reports = db_query.filter(ReportDB.status == "completed").all()
            for r in db_reports:
                data = r.to_dict()
                res_data = data.get("result_data") or {}
                if isinstance(res_data, dict):
                    merged = {**res_data, **data}
                    reports.append(merged)
                else:
                    reports.append(data)
        if reports:
            logger.info("从 SQLite 数据库中检索到 %d 份已完成报告", len(reports))
            return reports
    except Exception as exc:
        logger.debug("从数据库加载报告失败 (正常离线/无DB环境): %s", exc)

    # 4. Fallback search: work/evaluations/mocks or tests/mock_evaluations or golden audit
    fallback_candidates = [
        Path(project_root) / "work" / "evaluations" / "mocks" / "mock_weekly_dataset_60_samples.json",
        Path(project_root) / "tests" / "mock_evaluations" / "mock_weekly_dataset_60_samples.json",
    ]
    for cand in fallback_candidates:
        if cand.exists():
            try:
                with open(cand, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and "samples" in d and isinstance(d["samples"], list):
                    w_id = d.get("week_identifier", "week_202634")
                    logger.info("从 Mock 基准数据集 %s 加载了 %d 份样本", cand.name, len(d["samples"]))
                    res = []
                    for s in d["samples"]:
                        if isinstance(s, dict):
                            item = dict(s)
                            item["_source_week_identifier"] = w_id
                            res.append(item)
                    return res
            except Exception as e:
                logger.debug("读取 Mock 候选文件 %s 失败: %s", cand, e)

    # 5. Golden audit reports
    golden_dir = Path(project_root) / "tests" / "golden" / "audit_20260823"
    if golden_dir.exists():
        for fpath in sorted(golden_dir.glob("*_result_data.json")):
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        reports.append(data)
            except Exception as e:
                logger.debug("读取 golden 报告 %s 失败: %s", fpath.name, e)
        if reports:
            logger.info("从 Golden 审计目录中加载了 %d 份报告", len(reports))
            return reports

    # 6. Synthesize 60-sample mock dataset if use_mock or nothing found
    if use_mock or not reports:
        logger.info("未检索到外部报告记录，自动生成 60 局全功能 Mock 基准评测集...")
        synthetic_weekly = generate_60_sample_weekly_dataset()
        w_id = synthetic_weekly.get("week_identifier", "week_202634")
        samples = synthetic_weekly.get("samples") or []
        res = []
        for s in samples:
            if isinstance(s, dict):
                item = dict(s)
                item["_source_week_identifier"] = w_id
                res.append(item)
        return res

    return reports


def group_samples_by_week(
    samples: Sequence[Mapping[str, Any]],
    *,
    force_by_date: bool = False,
) -> Dict[str, List[Dict[str, Any]]]:
    """Group report samples by week_YYYYWW."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in samples:
        src_w = item.get("_source_week_identifier") if not force_by_date else None
        if src_w:
            w_id = normalize_week_identifier(str(src_w)) or str(src_w)
        else:
            t_date = extract_sample_trade_date(item)
            w_id = extract_week_from_date(t_date)
        groups.setdefault(w_id, []).append(dict(item))
    return groups
    """Group report samples by week_YYYYWW."""
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for item in samples:
        t_date = extract_sample_trade_date(item)
        w_id = extract_week_from_date(t_date)
        groups.setdefault(w_id, []).append(dict(item))
    return groups


def compute_week_date_range(samples: Sequence[Mapping[str, Any]], week_id: str) -> Tuple[str, str]:
    """Compute (start_date, end_date) string pair for a week's sample set."""
    trade_dates = []
    for s in samples:
        d = extract_sample_trade_date(s)
        if d:
            trade_dates.append(d)

    if trade_dates:
        return min(trade_dates), max(trade_dates)

    # Fallback to ISO calendar bounds
    match = re.search(r"(\d{4})(\d{2})", week_id)
    if match:
        try:
            year, week_num = int(match.group(1)), int(match.group(2))
            mon = date.fromisocalendar(year, week_num, 1).isoformat()
            sun = date.fromisocalendar(year, week_num, 7).isoformat()
            return mon, sun
        except ValueError:
            pass

    today = date.today().isoformat()
    return today, today


def recalculate_week(
    samples_for_week: Sequence[Mapping[str, Any]],
    week_identifier: str,
    *,
    historical_sample_ids: Optional[Sequence[str]] = None,
) -> Tuple[WeeklyMetricsJSON, str]:
    """Execute pure offline recalculation for a single week and return (WeeklyMetricsJSON, SummaryMD)."""
    start_d, end_d = compute_week_date_range(samples_for_week, week_identifier)

    # Build WeeklyMetricsJSON
    weekly_metrics = build_weekly_metrics(
        samples_for_week,
        week_identifier=week_identifier,
        start_date=start_d,
        end_date=end_d,
        historical_sample_ids=historical_sample_ids,
    )

    # Validate against Pydantic schema
    validate_weekly_metrics(weekly_metrics)

    # Render Markdown summary dashboard
    summary_md = render_weekly_summary_markdown(weekly_metrics)

    # Validate Markdown schema
    validate_weekly_summary_md(
        {
            "schema_version": WEEKLY_SUMMARY_MD_SCHEMA_VERSION,
            "week_identifier": week_identifier,
            "title": f"周度评测与离线复算看板报告 ({week_identifier})",
            "markdown_content": summary_md,
        }
    )

    return weekly_metrics, summary_md


def save_weekly_artifacts(
    weekly_metrics: WeeklyMetricsJSON,
    summary_md: str,
    output_dir: Union[str, Path],
    *,
    is_latest: bool = True,
) -> Dict[str, str]:
    """Persist weekly metrics JSON and summary MD with absolute idempotency and latest symlinks."""
    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)

    week_id = str(weekly_metrics.get("week_identifier") or "week_unknown")
    json_filename = f"{week_id}_metrics.json"
    md_filename = f"{week_id}_summary.md"

    target_json_path = out_path / json_filename
    target_md_path = out_path / md_filename

    # 1. Idempotent write of weekly JSON
    tmp_json = out_path / f".{json_filename}.tmp"
    with open(tmp_json, "w", encoding="utf-8") as f:
        json.dump(weekly_metrics, f, ensure_ascii=False, indent=2)
    os.replace(tmp_json, target_json_path)

    # 2. Idempotent write of summary Markdown
    tmp_md = out_path / f".{md_filename}.tmp"
    with open(tmp_md, "w", encoding="utf-8") as f:
        f.write(summary_md)
    os.replace(tmp_md, target_md_path)

    # 3. Maintain atomic symlinks for latest_metrics.json and latest_summary.md
    latest_json_path = out_path / "latest_metrics.json"
    latest_md_path = out_path / "latest_summary.md"

    if is_latest:
        # Atomic symlink update for JSON
        try:
            tmp_symlink = out_path / f".latest_metrics_{os.getpid()}.tmp"
            if tmp_symlink.is_symlink() or tmp_symlink.exists():
                tmp_symlink.unlink()
            tmp_symlink.symlink_to(json_filename)
            tmp_symlink.replace(latest_json_path)
        except (OSError, NotImplementedError):
            shutil.copy2(target_json_path, latest_json_path)

        # Atomic symlink update for MD
        try:
            tmp_symlink_md = out_path / f".latest_summary_{os.getpid()}.tmp"
            if tmp_symlink_md.is_symlink() or tmp_symlink_md.exists():
                tmp_symlink_md.unlink()
            tmp_symlink_md.symlink_to(md_filename)
            tmp_symlink_md.replace(latest_md_path)
        except (OSError, NotImplementedError):
            shutil.copy2(target_md_path, latest_md_path)

    return {
        "metrics_json": str(target_json_path.resolve()),
        "summary_md": str(target_md_path.resolve()),
        "latest_metrics_json": str(latest_json_path.resolve()),
        "latest_summary_md": str(latest_md_path.resolve()),
    }


def format_cli_text_report(weekly_metrics: WeeklyMetricsJSON, artifacts: Optional[Dict[str, str]] = None) -> str:
    """Format an informative, publication-grade text banner for terminal output."""
    week_id = weekly_metrics.get("week_identifier")
    start_d = weekly_metrics.get("start_date")
    end_d = weekly_metrics.get("end_date")
    total_samples = weekly_metrics.get("sample_count", 0)
    aggs = weekly_metrics.get("weekly_aggregate") or {}
    overview = aggs.get("overview") or {}
    quality = aggs.get("quality_aggregates") or {}
    gaps = aggs.get("data_gaps_aggregates") or {}
    t5 = aggs.get("t5_calibration") or {}
    h1b = aggs.get("h1b_system_gates_evaluation") or {}
    drill_ind = aggs.get("drilldown_by_industry") or {}
    drill_model = aggs.get("drilldown_by_model") or {}

    passed = h1b.get("passed", False)
    rec = h1b.get("recommendation", "KEEP_FALSE")

    def _pct(v: Optional[float]) -> str:
        return f"{v * 100:.1f}%" if v is not None else "N/A"

    def _num(v: Optional[float], d: int = 2) -> str:
        return f"{v:.{d}f}" if v is not None else "N/A"

    lines = [
        "=" * 82,
        f"📊  周度离线复算看板与指标聚合报告  [{week_id}]",
        "=" * 82,
        f"评测周期: {start_d} 至 {end_d} | 样本总量: {total_samples} 局 | 覆盖标的: {overview.get('unique_symbols', 0)} 支 ({overview.get('unique_industries', 0)} 个行业)",
        f"多空分布: 多头 {overview.get('bull_decisions', 0)} ({_pct(overview.get('bull_decision_ratio'))}) / 空头 {overview.get('bear_decisions', 0)} / 平局 {overview.get('hold_decisions', 0)}",
        "-" * 82,
        "【核心评测质量 KPI】",
        f"  • 多空核验率 (Bull/Bear/Δv) : 多 {_pct(quality.get('avg_bull_verified_rate'))} | 空 {_pct(quality.get('avg_bear_verified_rate'))} | Δv = {_pct(quality.get('delta_verified_rate'))} (门槛 <= 18.0%)",
        f"  • 证据复用克隆率 (Clone)    : {_pct(quality.get('avg_clone_rate'))} (门槛 <= 5.0%)",
        f"  • 挑战采纳率 (Challenge)    : {_pct(quality.get('avg_challenge_adoption_rate'))}",
        f"  • 字段完整率 (Completeness) : {_pct(quality.get('avg_field_completeness_rate'))} (标准 100.0%)",
        f"  • 自洽硬闸触发率            : {_pct(quality.get('consistency_gate_trigger_rate'))} (门槛 <= 5.0%)",
        f"  • 数据源 Gaps 分流          : 结构性 {gaps.get('total_structural_gaps', 0)} 起 | 运行性 {gaps.get('total_operational_gaps', 0)} 起 (常驻故障: {gaps.get('resident_fault_count', 0)})",
        f"  • T+5 胜率校准              : 命中率 {_pct(t5.get('direction_accuracy_rate'))} | 数据完整率 {_pct(t5.get('completeness_rate'))} | 平均收益 {_num(t5.get('avg_t5_return_pct'))}%",
        "-" * 82,
        "【H1b 信用加权 7 维激活门槛离线复算结论】",
        f"  • 系统级门槛状态 : {'✅ PASS (7 维全部达标)' if passed else '❌ FAIL (存在未达标项)'}",
        f"  • 决策建议       : {rec} ({'允许开启特征开关' if passed else '禁止开启，保持默认 credit_weighting_enabled=False'})",
    ]

    if drill_ind:
        lines.append("-" * 82)
        lines.append("【行业多维下钻透视 (Top Industries)】")
        for ind_name, st in sorted(drill_ind.items())[:6]:
            lines.append(
                f"  [{ind_name:<6}] 样本:{st.get('sample_count', 0):>2} | 多空平: {st.get('bull_count', 0)}/{st.get('bear_count', 0)}/{st.get('hold_count', 0)} "
                f"| Δv: {_pct(st.get('delta_verified_rate'))} | 克隆: {_pct(st.get('avg_clone_rate'))} | T+5命中: {_pct(st.get('t5_accuracy_rate'))}"
            )

    if drill_model:
        lines.append("-" * 82)
        lines.append("【模型参与辩论下钻 (Model Breakdown)】")
        for m_name, st in sorted(drill_model.items()):
            lines.append(
                f"  [{m_name:<14}] 参与:{st.get('total_debates', 0):>2} | 胜局(多/空): {st.get('bull_wins', 0)}/{st.get('bear_wins', 0)} "
                f"| 胜率: {_pct(st.get('win_rate'))} | 核验率: {_pct(st.get('avg_verified_rate'))} | 挑战采纳: {_pct(st.get('avg_challenge_adoption_rate'))}"
            )

    if artifacts:
        lines.append("-" * 82)
        lines.append("【产物生成状态】")
        lines.append(f"  • 结构化 JSON : {artifacts.get('metrics_json')}")
        lines.append(f"  • 看板摘要 MD : {artifacts.get('summary_md')}")
        lines.append(f"  • 软链指向   : {artifacts.get('latest_metrics_json')} -> {Path(artifacts.get('metrics_json', '')).name}")

    lines.append("=" * 82)
    return "\n".join(lines)


def run_recalculate(
    *,
    week: Optional[str] = None,
    all_weeks: bool = False,
    dry_run: bool = False,
    output_dir: str = "work/evaluations",
    db_path: Optional[str] = None,
    input_file: Optional[str] = None,
    input_dir: Optional[str] = None,
    use_mock: bool = False,
    output_format: str = "text",
) -> Dict[str, Any]:
    """Execute complete recalculation workflow according to CLI specifications."""
    # 1. Load reports
    reports = load_reports(
        db_path=db_path,
        input_file=input_file,
        input_dir=input_dir,
        use_mock=use_mock,
    )

    if not reports:
        logger.error("未找到可用评测样本，退出。")
        return {"success": False, "error": "No reports found", "weeks_processed": []}

    # 2. Group by week
    grouped_weeks = group_samples_by_week(reports)
    all_week_ids = sorted(grouped_weeks.keys())

    target_weeks: List[str] = []
    normalized_target_week = normalize_week_identifier(week)

    if normalized_target_week:
        if normalized_target_week in grouped_weeks:
            target_weeks = [normalized_target_week]
        else:
            # If target week specified but not in grouped weeks, check if we should map all reports to that week
            logger.info("指定周 %s 未在原始 trade_date 自动推导中命中，将当前加载的 %d 份样本作为该周样本进行复算", normalized_target_week, len(reports))
            grouped_weeks[normalized_target_week] = reports
            target_weeks = [normalized_target_week]
    elif all_weeks:
        target_weeks = all_week_ids
    else:
        # Default to latest detected week
        target_weeks = [all_week_ids[-1]] if all_week_ids else []

    if not target_weeks:
        logger.error("无法确定目标复算周，退出。")
        return {"success": False, "error": "No target weeks identified", "weeks_processed": []}

    results: Dict[str, Any] = {
        "success": True,
        "dry_run": dry_run,
        "output_dir": str(Path(output_dir).resolve()),
        "weeks_processed": [],
    }

    latest_week = target_weeks[-1]

    for w_id in target_weeks:
        w_samples = grouped_weeks.get(w_id, [])
        logger.info("开始复算周 %s (样本量: %d)...", w_id, len(w_samples))

        weekly_json, summary_md = recalculate_week(w_samples, w_id)

        artifacts = None
        if not dry_run:
            is_lat = bool(w_id == latest_week)
            artifacts = save_weekly_artifacts(
                weekly_json,
                summary_md,
                output_dir,
                is_latest=is_lat,
            )

        week_summary = {
            "week_identifier": w_id,
            "sample_count": len(w_samples),
            "weekly_metrics": weekly_json,
            "summary_md": summary_md,
            "artifacts": artifacts,
        }
        results["weeks_processed"].append(week_summary)

        # Print outputs
        if output_format == "json":
            print(json.dumps(weekly_json, ensure_ascii=False, indent=2))
        elif output_format == "markdown":
            print(summary_md)
        else:
            print(format_cli_text_report(weekly_json, artifacts))

    return results


def main() -> int:
    parser = argparse.ArgumentParser(
        description="P3-H2.3 周度离线复算工具与标准化看板生成器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--week",
        type=str,
        default=None,
        help="指定计算特定周 (例如 202634 或 week_202634)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        dest="all_weeks",
        help="全量累计复算 (检测所有周并依次生成看板)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="仅输出打印分析结果，不写磁盘文件",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="work/evaluations",
        help="自定义产物输出路径 (默认 work/evaluations/)",
    )
    parser.add_argument(
        "--db-path",
        type=str,
        default=None,
        help="指定 SQLite 数据库路径 (可选)",
    )
    parser.add_argument(
        "--input-file",
        type=str,
        default=None,
        help="指定输入 JSON 文件路径",
    )
    parser.add_argument(
        "--input-dir",
        type=str,
        default=None,
        help="指定输入 JSON 样本目录路径",
    )
    parser.add_argument(
        "--use-mock",
        action="store_true",
        help="强制使用 60-sample Mock 基准评测集",
    )
    parser.add_argument(
        "--format",
        type=str,
        choices=["text", "json", "markdown"],
        default="text",
        help="控制台输出格式 (text, json, markdown)",
    )

    args = parser.parse_args()

    res = run_recalculate(
        week=args.week,
        all_weeks=args.all_weeks,
        dry_run=args.dry_run,
        output_dir=args.output_dir,
        db_path=args.db_path,
        input_file=args.input_file,
        input_dir=args.input_dir,
        use_mock=args.use_mock,
        output_format=args.format,
    )

    return 0 if res.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
