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

