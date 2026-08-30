from api.services import report_service


def test_merge_data_gaps_collects_strict_failure_lines_across_horizons():
    result_data = {
        "news_report": "正常无重大新闻，不代表接口失败。\n- 【数据获取失败】新闻接口超时",
        "smart_money_report": "主力资金数据缺失，但没有严格失败标记。",
        "short_term": {
            "volume_price_report": "1. 【数据获取失败】量价数据结构异常",
        },
        "medium_term": {
            "news_report": "【数据获取失败】新闻接口超时",
        },
        "unknown_nested_field": "【数据获取失败】不应扫描未知字段",
    }

    assert report_service.merge_data_gaps(result_data) == [
        "【数据获取失败】新闻接口超时",
        "【数据获取失败】量价数据结构异常",
    ]


def test_merge_data_gaps_ignores_broad_failure_words_and_deduplicates_llm_items():
    result_data = {
        "market_report": (
            "接口失败但已有历史行情可用。\n"
            "说明：不要把‘【数据获取失败】’模板文字当作实际失败。"
        ),
        "fundamentals_report": "【数据获取失败】财报接口返回结构异常",
    }

    assert report_service.merge_data_gaps(
        result_data,
        llm_data_gaps=["模型识别：新闻数据不完整", "模型识别：新闻数据不完整", None, 42],
    ) == [
        "【数据获取失败】财报接口返回结构异常",
        "模型识别：新闻数据不完整",
    ]


def test_merge_data_gaps_handles_empty_and_non_mapping_report_payloads():
    assert report_service.merge_data_gaps(
        None,
        llm_data_gaps=[None, "  缺少资金流  ", "缺少资金流"],
    ) == ["缺少资金流"]
    assert report_service.merge_data_gaps(
        {"not_applicable": True, "market_report": "本周期无可评估事件。"}
    ) == []


def test_merge_data_gaps_filters_by_gap_class():
    result_data = {
        "market_data_context": {
            "data_failure_ledger": [
                {
                    "source": "northbound_flow",
                    "status": "unavailable",
                    "reason": "data source unavailable",
                    "gap": "【数据获取失败】northbound_flow：data source unavailable",
                    "gap_class": "structural",
                },
                {
                    "source": "share_pledge",
                    "status": "refused",
                    "reason": "data source refused",
                    "gap": "【数据获取失败】share_pledge：data source refused",
                    "gap_class": "structural",
                },
                {
                    "source": "news",
                    "status": "timeout",
                    "reason": "provider timeout",
                    "gap": "【数据获取失败】news：provider timeout",
                    "gap_class": "operational",
                },
            ]
        }
    }

    # Default: merges all
    all_gaps = report_service.merge_data_gaps(result_data)
    assert len(all_gaps) == 3

    # Operational only
    operational_gaps = report_service.merge_data_gaps(result_data, gap_class="operational")
    assert operational_gaps == ["【数据获取失败】news：provider timeout"]

    # Structural only
    structural_gaps = report_service.merge_data_gaps(result_data, gap_class="structural")
    assert structural_gaps == [
        "【数据获取失败】northbound_flow：data source unavailable",
        "【数据获取失败】share_pledge：data source refused",
    ]


def test_merge_data_gaps_scans_social_failure_ledger_top_level():
    result_data = {
        "social_data_context": {
            "status": "failed",
            "data_failure_ledger": [
                {
                    "source": "social_archive",
                    "status": "failed",
                    "reason": "social_archive_missing",
                    "gap": "【数据获取失败】social_archive：social_archive_missing",
                    "gap_class": "operational",
                },
                {
                    "source": "social.xhs",
                    "status": "timeout",
                    "reason": "social_archive_locked",
                    "gap": "【数据获取失败】social.xhs：social_archive_locked",
                    "gap_class": "operational",
                },
                {
                    "source": "social.dy",
                    "status": "refused",
                    "reason": "social_invalid_as_of",
                    "gap": "【数据获取失败】social.dy：social_invalid_as_of",
                    "gap_class": "structural",
                },
            ],
        }
    }

    all_gaps = report_service.merge_data_gaps(result_data)
    assert all_gaps == [
        "【数据获取失败】social_archive：social_archive_missing",
        "【数据获取失败】social.xhs：social_archive_locked",
        "【数据获取失败】social.dy：social_invalid_as_of",
    ]

    operational_gaps = report_service.merge_data_gaps(result_data, gap_class="operational")
    assert operational_gaps == [
        "【数据获取失败】social_archive：social_archive_missing",
        "【数据获取失败】social.xhs：social_archive_locked",
    ]

    structural_gaps = report_service.merge_data_gaps(result_data, gap_class="structural")
    assert structural_gaps == [
        "【数据获取失败】social.dy：social_invalid_as_of",
    ]


def test_merge_data_gaps_ignores_non_failure_social_statuses():
    result_data = {
        "social_data_context": {
            "status": "partial",
            "data_failure_ledger": [
                {
                    "source": "social_archive",
                    "status": "empty",
                    "reason": "social_empty",
                    "gap": "【数据获取失败】social_archive：social_empty",
                    "gap_class": "operational",
                },
                {
                    "source": "social.xhs",
                    "status": "insufficient",
                    "reason": "social_insufficient_coverage",
                    "gap": "【数据获取失败】social.xhs：social_insufficient_coverage",
                    "gap_class": "operational",
                },
                {
                    "source": "social.dy",
                    "status": "not_applicable",
                    "reason": "social_not_applicable",
                    "gap": "【数据获取失败】social.dy：social_not_applicable",
                    "gap_class": "structural",
                },
                {
                    "source": "social_archive",
                    "status": "partial",
                    "reason": "social_platform_partial",
                    "gap": "【数据获取失败】social_archive：social_platform_partial",
                    "gap_class": "operational",
                },
                {
                    "source": "social_archive",
                    "status": "available",
                    "reason": "available",
                    "gap": "【数据获取失败】social_archive：available",
                    "gap_class": "operational",
                },
            ],
        }
    }

    assert report_service.merge_data_gaps(result_data) == []


def test_merge_data_gaps_scans_social_ledger_across_horizons_and_deduplicates():
    result_data = {
        "market_data_context": {
            "data_failure_ledger": [
                {
                    "source": "social_archive",
                    "status": "failed",
                    "reason": "social_archive_missing",
                    "gap": "【数据获取失败】social_archive：social_archive_missing",
                    "gap_class": "operational",
                }
            ]
        },
        "social_data_context": {
            "data_failure_ledger": [
                {
                    "source": "social_archive",
                    "status": "failed",
                    "reason": "social_archive_missing",
                    "gap": "【数据获取失败】social_archive：social_archive_missing",
                    "gap_class": "operational",
                }
            ]
        },
        "short_term": {
            "social_data_context": {
                "data_failure_ledger": [
                    {
                        "source": "social.xhs",
                        "status": "unavailable",
                        "reason": "social_archive_locked",
                        "gap": "【数据获取失败】social.xhs：social_archive_locked",
                        "gap_class": "operational",
                    }
                ]
            }
        },
        "medium_term": {
            "social_data_context": {
                "data_failure_ledger": [
                    {
                        "source": "social.dy",
                        "status": "error",
                        "reason": "social_schema_mismatch",
                        "gap": "【数据获取失败】social.dy：social_schema_mismatch",
                        "gap_class": "operational",
                    }
                ]
            }
        },
        "horizons": {
            "long_term": {
                "social_data_context": {
                    "data_failure_ledger": [
                        {
                            "source": "social.xhs",
                            "status": "unavailable",
                            "reason": "social_archive_locked",
                            "gap": "【数据获取失败】social.xhs：social_archive_locked",
                            "gap_class": "operational",
                        }
                    ]
                }
            }
        },
    }

    gaps = report_service.merge_data_gaps(result_data)
    assert gaps == [
        "【数据获取失败】social_archive：social_archive_missing",
        "【数据获取失败】social.xhs：social_archive_locked",
        "【数据获取失败】social.dy：social_schema_mismatch",
    ]


