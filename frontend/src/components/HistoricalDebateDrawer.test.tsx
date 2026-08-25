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

function makeMockV2StructuredReport(overrides?: {
    tiebreak_skipped?: boolean
    debate_degenerate?: boolean
    withExecutedTiebreak?: boolean
}): ReportDetail {
    const isTiebreakSkipped = overrides?.tiebreak_skipped ?? (overrides?.withExecutedTiebreak ? false : true)
    const isDegenerate = overrides?.debate_degenerate ?? false

    const messages = [
        {
            message_index: 1,
            debate_round: 1,
            stage: 'opening',
            speaker: 'Bull Researcher',
            speaker_key: 'Bull',
            battlefield: 'capital_flow',
            cleaned_prose: '多头立论：主力大单逆势净流入1.29亿元，资金面持续向好。',
            parse_status: 'valid',
            accepted: true,
            responded_claim_ids: [],
            new_claim_ids: ['INV-1'],
            target_claim_ids: [],
            information_gain_score: 0.92,
            new_evidence_count: 2,
            round_summary: '多头建立资金面核心立论',
            round_goal: '确立多头主导优势',
        },
        {
            message_index: 2,
            debate_round: 1,
            stage: 'opening',
            speaker: 'Bear Researcher',
            speaker_key: 'Bear',
            battlefield: 'macro_policy',
            cleaned_prose: '空头立论：北向资金单日流出2.46亿元，宏观政策存不确定性。',
            parse_status: 'valid',
            accepted: true,
            responded_claim_ids: [],
            new_claim_ids: ['INV-4'],
            target_claim_ids: [],
            information_gain_score: 0.89,
            new_evidence_count: 1,
            round_summary: '空头建立宏观流出核心立论',
            round_goal: '确立空头反脆弱防线',
        },
        {
            message_index: 3,
            debate_round: 2,
            stage: 'challenge',
            speaker: 'Bull Researcher',
            speaker_key: 'Bull',
            cleaned_prose: '多头盘问：空头全单净流出未区分主力与散户，实际机构在大举吸筹。',
            parse_status: 'valid',
            accepted: true,
            responded_claim_ids: ['INV-4'],
            new_claim_ids: [],
            target_claim_ids: ['INV-4'],
            information_gain_score: 0.94,
            new_evidence_count: 1,
            round_summary: '多头质疑空头资金口径',
            round_goal: '击穿空头资金流论据',
            challenges: [
                {
                    challenge_id: 'CH-1',
                    target_claim_id: 'INV-4',
                    weakest_point: '空头混淆主力与散户资金流向',
                    severity: 'major',
                    evidence: ['主力大单实为净买入1.29亿'],
                    evidence_status: 'verified',
                    status: 'adopted',
                    speaker: 'Bull Researcher',
                    speaker_key: 'Bull',
                },
                {
                    challenge_id: 'CH-3',
                    target_claim_id: 'INV-4',
                    weakest_point: '致命击穿对手宏观流出断言',
                    severity: 'fatal',
                    evidence: ['发改委重大投资审批清单落地'],
                    evidence_status: 'verified',
                    status: 'adopted',
                    speaker: 'Bull Researcher',
                    speaker_key: 'Bull',
                },
            ],
        },
        {
            message_index: 4,
            debate_round: 2,
            stage: 'challenge',
            speaker: 'Bear Researcher',
            speaker_key: 'Bear',
            cleaned_prose: '空头盘问：多头净流入为短期假象，近5日已转向净流出。',
            parse_status: 'valid',
            accepted: true,
            responded_claim_ids: ['INV-1'],
            new_claim_ids: [],
            target_claim_ids: ['INV-1'],
            information_gain_score: 0.85,
            new_evidence_count: 1,
            round_summary: '空头质疑多头流入持续性',
            round_goal: '瓦解多头短期反弹逻辑',
            challenges: [
                {
                    challenge_id: 'CH-2',
                    target_claim_id: 'INV-1',
                    weakest_point: '伪造数据攻击多头主力流向',
                    severity: 'fatal',
                    evidence: ['伪造资金流断崖数据'],
                    evidence_status: 'unsupported',
                    status: 'rejected',
                    speaker: 'Bear Researcher',
                    speaker_key: 'Bear',
                },
            ],
        },
    ]

    if (overrides?.withExecutedTiebreak) {
        messages.push(
            {
                message_index: 5,
                debate_round: 3,
                stage: 'tiebreak',
                speaker: 'Bull Researcher',
                speaker_key: 'Bull',
                cleaned_prose: '多头加赛陈述：高频微观逐笔大单数据显示机构持续点火。',
                parse_status: 'valid',
                accepted: true,
                responded_claim_ids: ['INV-4'],
                new_claim_ids: [],
                target_claim_ids: ['INV-4'],
                information_gain_score: 0.96,
                new_evidence_count: 1,
                round_summary: '多头加赛终局论述',
                round_goal: '锁定胜局',
                tiebreak_question: '核心争议：主力大单是否真实锁定筹码？',
                tiebreak_answer: '多头回答：逐笔成交明细确凿显示机构锁仓，换手率降至0.8%。',
                self_win_prob: 0.78,
            } as any,
            {
                message_index: 6,
                debate_round: 3,
                stage: 'tiebreak',
                speaker: 'Bear Researcher',
                speaker_key: 'Bear',
                cleaned_prose: '空头加赛陈述：盘口大单存在对倒嫌疑，尾盘资金流出加速。',
                parse_status: 'valid',
                accepted: true,
                responded_claim_ids: ['INV-1'],
                new_claim_ids: [],
                target_claim_ids: ['INV-1'],
                information_gain_score: 0.86,
                new_evidence_count: 1,
                round_summary: '空头加赛防守',
                round_goal: '争取中性裁决',
                tiebreak_question: '核心争议：主力大单是否真实锁定筹码？',
                tiebreak_answer: '空头回答：尾盘集合竞价存在大单撤单异动，对倒诱多风险极大。',
                self_win_prob: 0.35,
            } as any,
        )
    }

    return {
        id: 'report-v2-structured-1',
        symbol: '000333.SZ',
        trade_date: '2026-08-25',
        status: 'completed',
        result_data: {
            symbol: '000333.SZ',
            trade_date: '2026-08-25',
            protocol_version: 'v2_structured_disagreement',
            protocol_stage: 'manager',
            tiebreak_skipped: isTiebreakSkipped,
            debate_degenerate: isDegenerate,
            investment_debate_state: {
                count: messages.length,
                protocol_version: 'v2_structured_disagreement',
                protocol_stage: 'manager',
                tiebreak_skipped: isTiebreakSkipped,
                debate_degenerate: isDegenerate,
                round_messages: messages,
                claims: [
                    {
                        claim_id: 'INV-1',
                        speaker: 'Bull Researcher',
                        speaker_key: 'Bull',
                        stance: 'bullish',
                        round_index: 1,
                        debate_round: 1,
                        battlefield: 'capital_flow',
                        claim: '主力大单逆势净流入1.29亿元',
                        evidence: ['东财主力净流入1.29亿元'],
                        confidence: 0.85,
                        falsification_conditions: ['若主力资金连续3日净流出超5000万则论点失效'],
                        status: 'adopted',
                        target_claim_ids: [],
                        responded_claim_ids: [],
                    },
                    {
                        claim_id: 'INV-4',
                        speaker: 'Bear Researcher',
                        speaker_key: 'Bear',
                        stance: 'bearish',
                        round_index: 1,
                        debate_round: 1,
                        battlefield: 'macro_policy',
                        claim: '北向资金单日大幅流出2.46亿元',
                        evidence: ['北向资金流出2.46亿元'],
                        confidence: 0.80,
                        falsification_conditions: ['北向资金连续2日净回流则论点失效'],
                        status: 'rejected',
                        target_claim_ids: [],
                        responded_claim_ids: [],
                    },
                ],
                challenges: [
                    {
                        challenge_id: 'CH-1',
                        target_claim_id: 'INV-4',
                        weakest_point: '空头混淆主力与散户资金流向',
                        severity: 'major',
                        evidence: ['主力大单实为净买入1.29亿'],
                        evidence_status: 'verified',
                        status: 'adopted',
                        speaker: 'Bull Researcher',
                        speaker_key: 'Bull',
                    },
                    {
                        challenge_id: 'CH-2',
                        target_claim_id: 'INV-1',
                        weakest_point: '伪造数据攻击多头主力流向',
                        severity: 'fatal',
                        evidence: ['伪造资金流断崖数据'],
                        evidence_status: 'unsupported',
                        status: 'rejected',
                        speaker: 'Bear Researcher',
                        speaker_key: 'Bear',
                    },
                    {
                        challenge_id: 'CH-3',
                        target_claim_id: 'INV-4',
                        weakest_point: '致命击穿对手宏观流出断言',
                        severity: 'fatal',
                        evidence: ['发改委重大投资审批清单落地'],
                        evidence_status: 'verified',
                        status: 'adopted',
                        speaker: 'Bull Researcher',
                        speaker_key: 'Bull',
                    },
                ],
                dispute_map: [
                    {
                        data_point: '主力资金净流入1.29亿 / 北向流出2.46亿',
                        bull_interpretation: '机构借北向调整逆势吸筹',
                        bear_interpretation: '外资避险出逃，买盘枯竭',
                        evidence_decision: '多方资金流向证据更具确定性',
                        winner: 'bull',
                    },
                ],
                manager_verdict: {
                    winner: 'bull',
                    direction: '看多',
                    reason: '多头资金与政策证据确凿，空头致命盘问缺乏证据支撑被驳回。',
                    position_pct: '40%',
                    entry: '84.0-84.5',
                    target: '95.0',
                    stop_loss: '81.0',
                    odds: '3.67',
                    upside: '13.1%',
                    downside: '3.6%',
                    adopted_claim_ids: ['INV-1'],
                    rejected_claim_ids: ['INV-4'],
                    adopted_challenge_ids: ['CH-1', 'CH-3'],
                    rejected_challenge_ids: ['CH-2'],
                    dispute_map: [
                        {
                            data_point: '主力资金净流入1.29亿 / 北向流出2.46亿',
                            bull_interpretation: '机构借北向调整逆势吸筹',
                            bear_interpretation: '外资避险出逃，买盘枯竭',
                            evidence_decision: '多方资金流向证据更具确定性',
                            winner: 'bull',
                        },
                    ],
                    consistency_check_passed: true,
                    failed_checks: [],
                },
                evidence_verification: [
                    {
                        raw: '东财主力净流入1.29亿元',
                        claim_id: 'INV-1',
                        matched_role: 'smart_money_report',
                        matched_source: 'smart_money',
                        status: 'verified',
                        is_fatal: false,
                        details: '精确匹配主力资金流入数据',
                    },
                ],
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

    describe('v2 Structured Disagreement Protocol', () => {
        it('renders protocol version badge for v2 and v1', () => {
            const v2Report = makeMockV2StructuredReport()
            const v2Html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v2Report}
                />,
            )
            expect(v2Html).toContain('v2_structured_disagreement')

            const v1Report = makeMockStructuredReport()
            const v1Html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v1Report}
                />,
            )
            expect(v1Html).toContain('v1_legacy')
        })

        it('renders opening stage with battlefield, claim, evidence, confidence, and falsification conditions', () => {
            const v2Report = makeMockV2StructuredReport()
            const html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v2Report}
                />,
            )

            // Battlefield badges
            expect(html).toContain('资金筹码')
            expect(html).toContain('宏观政策')

            // Opening Claims
            expect(html).toContain('主力大单逆势净流入1.29亿元')
            expect(html).toContain('北向资金单日大幅流出2.46亿元')

            // Falsification conditions
            expect(html).toContain('失效条件')
            expect(html).toContain('若主力资金连续3日净流出超5000万则论点失效')
        })

        it('renders challenges with target claim, weakest point, severity, evidence status, and manager adoption', () => {
            const v2Report = makeMockV2StructuredReport()
            const html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v2Report}
                />,
            )

            // Challenges list & badges
            expect(html).toContain('CH-1')
            expect(html).toContain('空头混淆主力与散户资金流向')
            expect(html).toContain('INV-4')
            expect(html).toContain('major')
            expect(html).toContain('CH-2')
            expect(html).toContain('CH-3')
            expect(html).toContain('fatal')
            expect(html).toContain('总监驳回')
        })

        it('enforces hard gate: unsupported/contradicted fatal challenge must NOT show as 已击穿', () => {
            const v2Report = makeMockV2StructuredReport()
            const html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v2Report}
                />,
            )

            // CH-2 is fatal with unsupported evidence -> MUST NOT show as 已击穿
            // We verify that the string "CH-2" is not accompanied by "已击穿"
            expect(html).not.toMatch(/CH-2[^<]*已击穿/)
            // And verified adopted fatal CH-3 CAN show as 已击穿
            expect(html).toContain('已击穿')
        })

        it('renders tiebreak skipped banner when tiebreak_skipped is true', () => {
            const v2Report = makeMockV2StructuredReport({ tiebreak_skipped: true })
            const html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v2Report}
                />,
            )

            expect(html).toContain('证据足以裁决，未触发加赛')
        })

        it('renders executed tiebreak Q&A when tiebreak is executed', () => {
            const v2Report = makeMockV2StructuredReport({ withExecutedTiebreak: true })
            const html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v2Report}
                />,
            )

            expect(html).not.toContain('证据足以裁决，未触发加赛')
            expect(html).toContain('加赛')
            expect(html).toContain('核心争议：主力大单是否真实锁定筹码？')
            expect(html).toContain('逐笔成交明细确凿显示机构锁仓')
        })

        it('renders dispute map with data point, interpretations, decision, and winner', () => {
            const v2Report = makeMockV2StructuredReport()
            const html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={v2Report}
                />,
            )

            expect(html).toContain('分歧全景')
            expect(html).toContain('主力资金净流入1.29亿 / 北向流出2.46亿')
            expect(html).toContain('机构借北向调整逆势吸筹')
            expect(html).toContain('外资避险出逃，买盘枯竭')
            expect(html).toContain('多方资金流向证据更具确定性')
        })

        it('renders degenerate flag when debate_degenerate is true', () => {
            const degenerateReport = makeMockV2StructuredReport({ debate_degenerate: true })
            const html = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={degenerateReport}
                />,
            )

            expect(html).toContain('辩论退化')

            const normalReport = makeMockV2StructuredReport({ debate_degenerate: false })
            const normalHtml = renderToStaticMarkup(
                <HistoricalDebateDrawer
                    isOpen={true}
                    onClose={() => {}}
                    reportData={normalReport}
                />,
            )
            expect(normalHtml).not.toContain('⚠️ 辩论退化')
        })

        it('does not crash on legacy reports missing v2 protocol fields', () => {
            const legacy = makeMockLegacyReport()
            const empty = makeMockEmptyReport()

            expect(() => {
                renderToStaticMarkup(
                    <HistoricalDebateDrawer isOpen={true} onClose={() => {}} reportData={legacy} />,
                )
            }).not.toThrow()

            expect(() => {
                renderToStaticMarkup(
                    <HistoricalDebateDrawer isOpen={true} onClose={() => {}} reportData={empty} />,
                )
            }).not.toThrow()
        })
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
