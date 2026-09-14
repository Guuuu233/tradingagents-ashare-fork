"""Regression tests for name-based financial table cleaning (commit 5)."""

from __future__ import annotations

import pandas as pd

from tradingagents.dataflows.utils import (
    MISSING_VALUE_MARKER,
    missing_core_financial_fields,
    shrink_table,
)


def test_shrink_table_reorders_by_name_and_drops_nan():
    """Shuffled columns + NaNs → name-selected core cols, no bare nan."""
    df = pd.DataFrame(
        [
            {
                "垃圾列A": float("nan"),
                "存货": 1.0,
                "归属于母公司所有者的净利润": 9.0,
                "无关噪声": "x",
                "资产总计": 100.0,
                "负债合计": 40.0,
                "归属于母公司股东权益合计": 60.0,
                "报告日": 20260331,
                "几乎全空": float("nan"),
            },
            {
                "垃圾列A": float("nan"),
                "存货": 2.0,
                "归属于母公司所有者的净利润": 8.0,
                "无关噪声": "y",
                "资产总计": 110.0,
                "负债合计": 45.0,
                "归属于母公司股东权益合计": 65.0,
                "报告日": 20251231,
                "几乎全空": float("nan"),
            },
        ]
    )
    # Add a >80% null column that should be dropped.
    df["超空列"] = [float("nan"), float("nan")]

    text = shrink_table(df, max_rows=5, table_kind="balance", require_core_fields=True)
    assert "【数据获取失败】" not in text
    assert "nan" not in text.lower()
    # All residual nulls become an explicit marker; this input has none.
    assert MISSING_VALUE_MARKER not in text
    # Core columns present by name, not first-N positional.
    assert "资产总计" in text
    assert "负债合计" in text
    assert "归属于母公司股东权益合计" in text
    assert "归属于母公司所有者的净利润" in text or "报告日" in text
    # Fully empty column dropped.
    assert "超空列" not in text
    assert "垃圾列A" not in text


def test_shrink_table_fails_when_two_core_fields_missing():
    df = pd.DataFrame(
        [
            {"报告日": 20260331, "货币资金": 1.0, "资产总计": 100.0},
            {"报告日": 20251231, "货币资金": 2.0, "资产总计": 110.0},
        ]
    )
    # Missing 总负债 / 净资产 / 归母净利润 → ≥2 core missing.
    missing = missing_core_financial_fields(df.columns)
    assert len(missing) >= 2
    text = shrink_table(df, table_kind="balance", require_core_fields=True)
    assert text.startswith("【数据获取失败】关键财务字段缺失：")
    assert "总负债" in text or "净资产" in text or "归母净利润" in text
    # Must not return a partial markdown table.
    assert "| 资产总计 |" not in text


def test_shrink_table_marks_residual_nulls_explicitly():
    df = pd.DataFrame(
        [
            {
                "报告日": 20260331,
                "资产总计": 100.0,
                "负债合计": float("nan"),
                "归属于母公司股东权益合计": 60.0,
                "归属于母公司所有者的净利润": 9.0,
                "货币资金": None,
            }
        ]
    )
    text = shrink_table(df, table_kind="balance", require_core_fields=True)
    assert "nan" not in text.lower()
    assert MISSING_VALUE_MARKER in text


def test_shrink_table_explicit_truncate_notice():
    # Wide table with many filled non-core columns to force char budget cut.
    row = {
        "报告日": 20260331,
        "资产总计": 1.0,
        "负债合计": 2.0,
        "归属于母公司股东权益合计": 3.0,
        "归属于母公司所有者的净利润": 4.0,
    }
    for i in range(40):
        row[f"扩展字段{i:02d}"] = 1000000.123456 + i
    df = pd.DataFrame([row, dict(row, **{"报告日": 20251231})])
    text = shrink_table(
        df,
        table_kind="balance",
        require_core_fields=True,
        max_prompt_chars=500,
    )
    assert "【已截断，保留核心字段】" in text
    assert "资产总计" in text
    assert "nan" not in text.lower()


def test_provider_wrapper_returns_string_not_dataframe():
    from tradingagents.dataflows.providers.cn_akshare_provider import CnAkshareProvider

    df = pd.DataFrame(
        [
            {
                "报告日": 20260331,
                "资产总计": 100.0,
                "负债合计": 40.0,
                "归属于母公司股东权益合计": 60.0,
                "归属于母公司所有者的净利润": 9.0,
                "zzz_last": float("nan"),
            }
        ]
    )
    out = CnAkshareProvider._shrink_table(
        df, max_rows=5, max_cols=2, table_kind="balance", require_core_fields=True
    )
    assert isinstance(out, str)
    # max_cols must NOT cause positional cut of core columns.
    assert "资产总计" in out
    assert "负债合计" in out
    assert "nan" not in out.lower()


def test_shrink_table_retains_latest_row_when_dates_out_of_order():
    """Requirement 1: 乱序 报告期/报告日/公告日期 输入在 max_rows=1 时保留最新日期。"""
    # 1. 乱序 报告日
    df_report_date = pd.DataFrame(
        [
            {"报告日": "2024-12-31", "资产总计": 100.0, "负债合计": 40.0, "归属于母公司股东权益合计": 60.0, "归属于母公司所有者的净利润": 9.0},
            {"报告日": "2026-06-30", "资产总计": 120.0, "负债合计": 50.0, "归属于母公司股东权益合计": 70.0, "归属于母公司所有者的净利润": 12.0},
            {"报告日": "2025-06-30", "资产总计": 110.0, "负债合计": 45.0, "归属于母公司股东权益合计": 65.0, "归属于母公司所有者的净利润": 10.0},
        ]
    )
    text_rd = shrink_table(df_report_date, max_rows=1, table_kind="balance", require_core_fields=True)
    assert "2026-06-30" in text_rd
    assert "2024-12-31" not in text_rd
    assert "2025-06-30" not in text_rd

    # 2. 乱序 报告期
    df_report_period = pd.DataFrame(
        [
            {"报告期": "2023-12-31", "营业收入": 50.0, "营业利润": 5.0},
            {"报告期": "2025-09-30", "营业收入": 90.0, "营业利润": 15.0},
            {"报告期": "2024-06-30", "营业收入": 70.0, "营业利润": 8.0},
        ]
    )
    text_rp = shrink_table(df_report_period, max_rows=1, table_kind="generic")
    assert "2025-09-30" in text_rp
    assert "2023-12-31" not in text_rp
    assert "2024-06-30" not in text_rp

    # 3. 乱序 公告日期
    df_ann_date = pd.DataFrame(
        [
            {"公告日期": "2023-04-15", "事项": "旧公告"},
            {"公告日期": "2026-04-20", "事项": "最新公告"},
            {"公告日期": "2025-08-10", "事项": "中间公告"},
        ]
    )
    text_ad = shrink_table(df_ann_date, max_rows=1, table_kind="generic")
    assert "2026-04-20" in text_ad
    assert "最新公告" in text_ad
    assert "2023-04-15" not in text_ad
    assert "旧公告" not in text_ad


def test_shrink_table_order_descending_ascending_stable_and_generic():
    """Requirement 2: 已经降序、升序和稳定相同日期输入均有明确断言；没有日期列的 generic 表保持现有输入顺序。"""
    # 1. 已经降序
    df_desc = pd.DataFrame(
        [
            {"报告日": "2026-06-30", "tag": "row_newest"},
            {"报告日": "2025-12-31", "tag": "row_mid"},
            {"报告日": "2024-12-31", "tag": "row_oldest"},
        ]
    )
    text_desc = shrink_table(df_desc, max_rows=3, table_kind="generic")
    pos_newest = text_desc.find("row_newest")
    pos_mid = text_desc.find("row_mid")
    pos_oldest = text_desc.find("row_oldest")
    assert -1 < pos_newest < pos_mid < pos_oldest

    # 2. 升序输入 → 降序输出
    df_asc = pd.DataFrame(
        [
            {"报告日": "2024-12-31", "tag": "row_oldest"},
            {"报告日": "2025-12-31", "tag": "row_mid"},
            {"报告日": "2026-06-30", "tag": "row_newest"},
        ]
    )
    text_asc = shrink_table(df_asc, max_rows=3, table_kind="generic")
    pos_newest = text_asc.find("row_newest")
    pos_mid = text_asc.find("row_mid")
    pos_oldest = text_asc.find("row_oldest")
    assert -1 < pos_newest < pos_mid < pos_oldest

    # 3. 稳定相同日期输入 (stable sort)
    df_same = pd.DataFrame(
        [
            {"报告日": "2026-03-31", "seq": "first_entry"},
            {"报告日": "2026-03-31", "seq": "second_entry"},
        ]
    )
    text_same = shrink_table(df_same, max_rows=2, table_kind="generic")
    pos_first = text_same.find("first_entry")
    pos_second = text_same.find("second_entry")
    assert -1 < pos_first < pos_second

    # 4. 没有日期列的 generic 表保持现有输入顺序
    df_generic_no_date = pd.DataFrame(
        [
            {"项目": "主营收入", "数值": 100},
            {"项目": "主营成本", "数值": 60},
            {"项目": "净利润", "数值": 40},
        ]
    )
    text_generic = shrink_table(df_generic_no_date, max_rows=3, table_kind="generic")
    pos_item1 = text_generic.find("主营收入")
    pos_item2 = text_generic.find("主营成本")
    pos_item3 = text_generic.find("净利润")
    assert -1 < pos_item1 < pos_item2 < pos_item3


def test_shrink_table_unparseable_dates_and_failure_semantics():
    """Requirement 3: 日期无法解析的行不得填当前日期或其他默认日期；测试明确其不会伪装成最新报告，且缺失值仍以既有显式标记/失败语义呈现。"""
    # 1. 含有无法解析日期的行不得排在有效最新日期前（不可伪装成最新报告）
    df_unparseable = pd.DataFrame(
        [
            {"报告日": "未知日期/待公布", "指标": "未披露", "资产总计": 100.0, "负债合计": 40.0, "归属于母公司股东权益合计": 60.0, "归属于母公司所有者的净利润": 9.0},
            {"报告日": "2025-12-31", "指标": "确切年报", "资产总计": 110.0, "负债合计": 45.0, "归属于母公司股东权益合计": 65.0, "归属于母公司所有者的净利润": 8.0},
            {"报告日": None, "指标": "空日期", "资产总计": 120.0, "负债合计": 50.0, "归属于母公司股东权益合计": 70.0, "归属于母公司所有者的净利润": 10.0},
        ]
    )
    text_single = shrink_table(df_unparseable, max_rows=1, table_kind="balance", require_core_fields=True)
    # max_rows=1 必须取有效最新报告期 2025-12-31，绝不得把 "未知日期/待公布" 或 None 伪装成最新报告
    assert "2025-12-31" in text_single
    assert "确切年报" in text_single
    assert "未知日期/待公布" not in text_single
    assert "空日期" not in text_single

    # 2. 缺失值与无法解析日期不得填入假定的当前日期或默认时间戳
    text_all = shrink_table(df_unparseable, max_rows=3, table_kind="balance", require_core_fields=True)
    # 原有的 None 应显示为 MISSING_VALUE_MARKER
    assert MISSING_VALUE_MARKER in text_all
    # 不得伪造 1970 或当前系统年份
    assert "1970-01-01" not in text_all
    # "未知日期/待公布" 保持原样文本，未被强转为虚假日期
    assert "未知日期/待公布" in text_all

    # 3. 既有失败语义不退化
    text_empty = shrink_table(pd.DataFrame(), table_kind="balance")
    assert text_empty == "【数据获取失败】表格为空，本项不可用。"


def test_shrink_table_budget_truncation_preserves_latest_date():
    """Budget clipping drops oldest rows (bottom) after date sort, preserving newest date."""
    # Build wide rows with earlier date at index 0 and newer date at index 1 in raw df
    row_old = {
        "报告日": "2024-12-31",
        "资产总计": 100.0,
        "负债合计": 40.0,
        "归属于母公司股东权益合计": 60.0,
        "归属于母公司所有者的净利润": 9.0,
    }
    row_new = {
        "报告日": "2026-06-30",
        "资产总计": 110.0,
        "负债合计": 45.0,
        "归属于母公司股东权益合计": 65.0,
        "归属于母公司所有者的净利润": 11.0,
    }
    for i in range(30):
        row_old[f"冗余字段{i:02d}"] = 99999.1234 + i
        row_new[f"冗余字段{i:02d}"] = 88888.5678 + i
    # raw order has 2024 first, 2026 second
    df = pd.DataFrame([row_old, row_new])
    text = shrink_table(
        df,
        table_kind="balance",
        require_core_fields=True,
        max_prompt_chars=200,
    )
    # Even though row_old was first in input df, sorting places 2026 first;
    # budget truncation drops row_old (oldest) and retains 2026-06-30 (newest).
    assert "2026-06-30" in text
    assert "2024-12-31" not in text
    assert "【已截断，保留核心字段】" in text
