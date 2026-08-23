import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import HistoricalDebateDrawer, { extractDebateState } from '@/components/HistoricalDebateDrawer'
import ReportViewer from '@/components/ReportViewer'
import type { ReportDetail } from '@/types'

function makeMockStructuredReport(): ReportDetail {
    return {
        id: 'report-structured-1',
        symbol: '600519.SH',
        trade_date: '2026-08-22',
        status: 'completed',
        result_data: {
            symbol: '600519.SH',
            trade_date: '2026-08-22',
            investment_debate_state: {
                count: 2,
                current_speaker: 'Bear',
                round_messages: [
                    {
                        message_index: 1,
                        debate_round: 1,
                        speaker: 'Bull Researcher',
                        speaker_key: 'Bull',
                        cleaned_prose: '多头论述：Q3在手订单增长50%，业绩高增确定性极高。',
                        parse_status: 'valid',
                        accepted: true,
                        responded_claim_ids: [],
                        new_claim_ids: ['INV-1'],
                        target_claim_ids: [],
                        information_gain_score: 0.95,
                        new_evidence_count: 2,
                        round_summary: '多头阐述在手订单增长核心论据',
                        round_goal: '建立多头核心论点',
                    },
                    {
                        message_index: 2,
                        debate_round: 1,
                        speaker: 'Bear Researcher',
                        speaker_key: 'Bear',
                        cleaned_prose: '空头论述：铜价创季度新高，成本端将直接侵蚀毛利率。',
                        parse_status: 'valid',
                        accepted: true,
                        responded_claim_ids: ['INV-1'],
                        new_claim_ids: ['INV-2'],
                        target_claim_ids: ['INV-1'],
                        information_gain_score: 0.88,
                        new_evidence_count: 1,
                        round_summary: '空头反驳成本端毛利侵蚀',
                        round_goal: '建立空头反驳与成本下杀论点',
                        attempts: [
                            {
                                attempt_index: 1,
                                message_index: 2,
                                debate_round: 1,
                                speaker: 'Bear Researcher',
                                speaker_key: 'Bear',
                                cleaned_prose: '空头重复论述：铜价上涨。',
                                parse_status: 'invalid_protocol',
                                accepted: false,
                                error_detail: '反驳未回应对方核心论据 INV-1，协议校验未通过',
                                model_name: 'claude-3-7-sonnet',
                            },
                        ],
                    },
                ],
                claims: [
                    {
                        claim_id: 'INV-1',
                        speaker: 'Bull Researcher',
                        speaker_key: 'Bull',
                        stance: 'bullish',
                        round_index: 1,
                        debate_round: 1,
                        claim: '在手订单高增50%支撑Q3业绩爆发',
                        evidence: ['Q3在手订单增长50%', '产线满负荷运转'],
                        confidence: 0.9,
                        status: 'adopted',
                        target_claim_ids: [],
                        responded_claim_ids: [],
                    },
                    {
                        claim_id: 'INV-2',
                        speaker: 'Bear Researcher',
                        speaker_key: 'Bear',
                        stance: 'bearish',
                        round_index: 1,
                        debate_round: 1,
                        claim: '铜价持续上涨将显著侵蚀毛利率',
                        evidence: ['铜价创季度新高68000元/吨'],
                        confidence: 0.82,
                        status: 'rejected',
                        target_claim_ids: ['INV-1'],
                        responded_claim_ids: ['INV-1'],
                    },
                ],
                manager_verdict: {
                    winner: 'bull',
                    direction: '看多',
                    reason: '多头订单增长证据确凿，空头原材料压力可被产能规模效应部分对冲。',
                    position_pct: '30%',
                    entry: '1850-1880',
                    target: '2100',
                    stop_loss: '1780',
                    odds: '3.2',
                    upside: '13.5%',
                    downside: '4.2%',
                    adopted_claim_ids: ['INV-1'],
                    rejected_claim_ids: ['INV-2'],
                    consistency_check_passed: true,
                    failed_checks: [],
                },
                evidence_verification: [
                    {
                        raw: 'Q3在手订单增长50%',
                        claim_id: 'INV-1',
                        matched_role: 'news_report',
                        matched_source: 'news',
                        status: 'verified',
                        is_fatal: false,
                        details: '在 news_report 中找到精确匹配事实',
                    },
                    {
                        raw: '铜价创季度新高68000元/吨',
                        claim_id: 'INV-2',
                        matched_role: 'macro_report',
                        matched_source: 'macro',
                        status: 'verified',
                        is_fatal: false,
                        details: '在 macro_report 中验证数值匹配',
                    },
                    {
                        raw: '伪造数据：海外订单激增300%',
                        claim_id: 'INV-1',
                        matched_role: null,
                        matched_source: null,
                        status: 'unsupported',
                        is_fatal: true,
                        details: '未在七份分析师报告或市场数据上下文中找到该事实或数据支撑 (FATAL)',
                    },
                    {
                        raw: '冲突数据：毛利率下降至10%',
                        claim_id: 'INV-2',
                        matched_role: 'fundamentals_report',
                        matched_source: 'fundamentals',
                        status: 'contradicted',
                        is_fatal: false,
                        details: '在 fundamentals_report 中关键词毛利率数据冲突',
                    },
                ],
            },
        },
    }
}

function makeMockFailedVerdictReport(): ReportDetail {
    return {
        id: 'report-failed-verdict-1',
        symbol: '600036.SH',
        trade_date: '2026-08-22',
        status: 'completed',
        result_data: {
            symbol: '600036.SH',
            trade_date: '2026-08-22',
            investment_debate_state: {
                count: 2,
                round_messages: [],
                claims: [],
                manager_verdict: {
                    winner: 'bear',
                    direction: '看多', // Conflicts with bear
                    position_pct: '50%', // > 20%
                    consistency_check_passed: false,
                    failed_checks: [
                        '空头胜裁决下方向不得为看多/买入 (当前: 看多)',
                        '空头胜裁决下建议仓位(50%)过高，不得高于20%',
                    ],
                },
                evidence_verification: [
                    {
                        raw: '违规调用不可用数据源',
                        status: 'source_unavailable',
                        is_fatal: true,
                        details: '数据源已下线 (FATAL)',
                    },
                ],
            },
        },
    }
}

function makeMockLegacyReport(): ReportDetail {
    return {
        id: 'report-legacy-1',
        symbol: '000001.SZ',
        trade_date: '2026-01-10',
        status: 'completed',
        result_data: {
            symbol: '000001.SZ',
            trade_date: '2026-01-10',
            investment_debate_state: {
                bull_history: '旧版多头纯文本观点',
                bear_history: '旧版空头纯文本观点',
                judge_decision: '旧版裁判纯文本决策',
            },
        },
    }
}

function makeMockEmptyReport(): ReportDetail {
    return {
        id: 'report-empty-1',
        symbol: '000002.SZ',
        trade_date: '2025-12-01',
        status: 'completed',
        result_data: {
            symbol: '000002.SZ',
            trade_date: '2025-12-01',
        },
    }
}

describe('HistoricalDebateDrawer', () => {
    it('renders null when isOpen is false', () => {
        const html = renderToStaticMarkup(
            <HistoricalDebateDrawer
                isOpen={false}
                onClose={() => {}}
                reportData={makeMockStructuredReport()}
            />,
        )
        expect(html).toBe('')
    })

    it('renders structured debate with timeline, claims, verdict, and evidence verification tabs', () => {
        const report = makeMockStructuredReport()
        const html = renderToStaticMarkup(
            <HistoricalDebateDrawer
                isOpen={true}
                onClose={() => {}}
                reportData={report}
            />,
        )

        // Header & Title
        expect(html).toContain('多空辩论与裁决证据')
        expect(html).toContain('600519.SH')

        // Tabs
        expect(html).toContain('逐轮辩论')
        expect(html).toContain('论点账本 (Claims)')
        expect(html).toContain('总监裁决')
        expect(html).toContain('事实核验')

        // Round timeline contents
        expect(html).toContain('多头研究员')
        expect(html).toContain('空头研究员')
        expect(html).toContain('Round 1')
        expect(html).toContain('协议合规')
        expect(html).toContain('95%') // 0.95 info gain score
        expect(html).toContain('新证据: 2')
        expect(html).toContain('提出: INV-1')
        expect(html).toContain('回应: INV-1')
        expect(html).toContain('未采纳重试: 1')
    })

    it('renders consistency check failure reasons when hard gate fails', () => {
        const report = makeMockFailedVerdictReport()
        const extract = extractDebateState(report)
        expect(extract.hasStructuredDebate).toBe(true)
        expect(extract.managerVerdict?.consistency_check_passed).toBe(false)
        expect(extract.managerVerdict?.failed_checks?.length).toBe(2)

        const html = renderToStaticMarkup(
            <HistoricalDebateDrawer
                isOpen={true}
                onClose={() => {}}
                reportData={report}
            />,
        )

        // Fatal badge count
        expect(html).toContain('🚨 1')
    })

    it('renders graceful fallback when report lacks structured debate fields', () => {
        const legacyReport = makeMockLegacyReport()
        const html = renderToStaticMarkup(
            <HistoricalDebateDrawer
                isOpen={true}
                onClose={() => {}}
                reportData={legacyReport}
            />,
        )

        expect(html).toContain('此报告生成时尚未记录结构化辩论')
        expect(html).toContain('历史文本记录')
        expect(html).toContain('旧版多头纯文本观点')
        expect(html).toContain('旧版空头纯文本观点')
        expect(html).toContain('旧版裁判纯文本决策')
    })

    it('handles completely empty report gracefully without crashing', () => {
        const emptyReport = makeMockEmptyReport()
        const html = renderToStaticMarkup(
            <HistoricalDebateDrawer
                isOpen={true}
                onClose={() => {}}
                reportData={emptyReport}
            />,
        )

        expect(html).toContain('此报告生成时尚未记录结构化辩论')
        expect(html).not.toContain('undefined')
        expect(html).not.toContain('null')
    })

    it('extracts debate state accurately across dual horizons', () => {
        const dualReport: ReportDetail = {
            id: 'dual-1',
            symbol: 'BABA',
            trade_date: '2026-08-22',
            status: 'completed',
            result_data: {
                symbol: 'BABA',
                trade_date: '2026-08-22',
                mode: 'dual_horizon',
                short_term: {
                    status: 'completed',
                    investment_debate_state: {
                        count: 1,
                        round_messages: [
                            {
                                message_index: 1,
                                debate_round: 1,
                                speaker: 'Bull Researcher',
                                cleaned_prose: '短线多头',
                                parse_status: 'valid',
                            },
                        ],
                    },
                },
                medium_term: {
                    status: 'completed',
                    investment_debate_state: {
                        count: 1,
                        round_messages: [
                            {
                                message_index: 1,
                                debate_round: 1,
                                speaker: 'Bear Researcher',
                                cleaned_prose: '中线空头',
                                parse_status: 'valid',
                            },
                        ],
                    },
                },
            },
        }

        const shortExtract = extractDebateState(dualReport, 'short')
        expect(shortExtract.hasStructuredDebate).toBe(true)
        expect(shortExtract.debateState?.round_messages?.[0].cleaned_prose).toBe('短线多头')

        const mediumExtract = extractDebateState(dualReport, 'medium')
        expect(mediumExtract.hasStructuredDebate).toBe(true)
        expect(mediumExtract.debateState?.round_messages?.[0].cleaned_prose).toBe('中线空头')
    })
})

describe('ReportViewer Debate Integration', () => {
    it('renders debate drawer entry button in historical mode while preserving 10 report sections', () => {
        const report = makeMockStructuredReport()
        report.market_report = '市场报告正文'
        report.sentiment_report = '舆情报告正文'
        report.news_report = '新闻报告正文'
        report.fundamentals_report = '基本面报告正文'
        report.macro_report = '宏观板块报告正文'
        report.smart_money_report = '主力资金报告正文'
        report.volume_price_report = '量价分析报告正文'
        report.investment_plan = '研究团队决策正文'
        report.trader_investment_plan = '交易团队计划正文'
        report.final_trade_decision = '最终交易决策正文'

        const html = renderToStaticMarkup(
            <ReportViewer reportData={report} />,
        )

        // 1. Entry button exists
        expect(html).toContain('多空辩论与裁决证据')

        // 2. All 10 existing report sections are preserved
        expect(html).toContain('市场分析报告')
        expect(html).toContain('舆情分析报告')
        expect(html).toContain('新闻分析报告')
        expect(html).toContain('基本面分析报告')
        expect(html).toContain('宏观板块报告')
        expect(html).toContain('主力资金报告')
        expect(html).toContain('量价分析报告')
        expect(html).toContain('研究团队决策')
        expect(html).toContain('交易团队计划')
        expect(html).toContain('最终交易决策')
    })
})
