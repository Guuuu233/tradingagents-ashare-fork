import { describe, expect, it } from 'vitest'

import {
    buildPriceGateDowngradeNote,
    resolvePriceGateDowngrade,
    resolveReportListDecision,
} from '@/utils/reportText'
import { formatAnalysisCompleteMessage } from '@/components/ChatCopilotPanel'

// Mirrors production report de6cbbc1 (000657.SZ): pre-gate SELL at top level,
// post-gate NO_TRADE inside short_term with the gate-blocked reason code.
const GATE_BLOCKED_RESULT = {
    decision: 'SELL',
    trade_action: 'SELL',
    analysis_status: 'VALID',
    decision_status: {
        analysis_status: 'VALID',
        trade_action: 'SELL',
        direction: 'BEAR',
        reason_codes: ['manager_terminal', 'risk_verdict:pass'],
    },
    short_term: {
        trade_action: 'NO_TRADE',
        analysis_status: 'VALID',
        decision_status: {
            analysis_status: 'VALID',
            direction: 'BEAR',
            trade_action: 'NO_TRADE',
            reason_codes: ['manager_terminal', 'risk_verdict:pass', 'price_basis_gate_blocked'],
            failed_checks: ['price_basis_gate_blocked'],
        },
        price_basis_gate: {
            status: 'blocked',
            violations: [
                {
                    kind: 'decision_driving_unspecified_basis',
                    source: 'investment_plan',
                    detail: '决策驱动价格 50.0(pr-131) basis 无法归因，禁止消费',
                },
                {
                    kind: 'decision_driving_missing_as_of',
                    source: 'investment_plan',
                    detail: '决策驱动价格 42.5 缺少 as_of',
                },
            ],
        },
    },
}

describe('resolveReportListDecision (DAV-1283 D3)', () => {
    it('renders truly empty decision columns as 未记录, not 观望', () => {
        expect(resolveReportListDecision(undefined, {})).toEqual({
            action: 'none',
            label: '未记录',
        })
        expect(
            resolveReportListDecision(null as unknown as undefined, {
                trade_action: null,
                analysis_status: 'VALID',
            }),
        ).toEqual({ action: 'none', label: '未记录' })
    })

    it('labels a gate-blocked NO_TRADE as 不交易（价格未通过）', () => {
        expect(
            resolveReportListDecision('NO_TRADE', {
                trade_action: 'NO_TRADE',
                reason_codes: ['manager_terminal', 'price_basis_gate_blocked'],
            }),
        ).toEqual({ action: 'no_trade', label: '不交易（价格未通过）' })
    })

    it('keeps plain NO_TRADE without the gate code as 不交易', () => {
        expect(
            resolveReportListDecision('NO_TRADE', {
                trade_action: 'NO_TRADE',
                reason_codes: ['risk_verdict:reject'],
            }),
        ).toEqual({ action: 'no_trade', label: '不交易' })
    })

    it('still maps executable actions', () => {
        expect(resolveReportListDecision('BUY', { trade_action: 'BUY' })).toEqual({
            action: 'add',
            label: '增持',
        })
        expect(resolveReportListDecision('WAIT', { trade_action: 'WAIT' })).toEqual({
            action: 'watch',
            label: '观望',
        })
    })
})

describe('resolvePriceGateDowngrade / buildPriceGateDowngradeNote (DAV-1283 D4)', () => {
    it('detects a SELL→NO_TRADE gate downgrade and samples up to 3 prices', () => {
        const downgrade = resolvePriceGateDowngrade(GATE_BLOCKED_RESULT)
        expect(downgrade?.managerAction).toBe('SELL')
        expect(downgrade?.prices).toEqual(['50.0', '42.5'])
    })

    it('builds the human note', () => {
        const note = buildPriceGateDowngradeNote(GATE_BLOCKED_RESULT)
        expect(note).toBe(
            '研究团队建议卖出，但报告中有价格无法核实来源（如：50.0、42.5 元），按规则不执行，最终动作为不交易。',
        )
    })

    it('returns null when the post-gate action is executable', () => {
        const src = {
            ...GATE_BLOCKED_RESULT,
            short_term: {
                ...GATE_BLOCKED_RESULT.short_term,
                trade_action: 'BUY',
            },
        }
        expect(resolvePriceGateDowngrade(src)).toBeNull()
    })

    it('returns null when NO_TRADE was not caused by the price gate', () => {
        const src = {
            trade_action: 'NO_TRADE',
            reason_codes: ['risk_verdict:reject'],
            decision: 'SELL',
        }
        expect(resolvePriceGateDowngrade(src)).toBeNull()
    })

    it('prefers pre_gate_trade_action for new (post-D5) payloads', () => {
        const src = {
            trade_action: 'NO_TRADE',
            pre_gate_trade_action: 'BUY',
            reason_codes: ['price_basis_gate_blocked'],
            short_term: {
                trade_action: 'NO_TRADE',
                price_basis_gate: { status: 'blocked', violations: [] },
            },
        }
        expect(buildPriceGateDowngradeNote(src)).toBe(
            '研究团队建议买入，但报告中有价格无法核实来源，按规则不执行，最终动作为不交易。',
        )
    })

    it('appends the note to the 分析完成 message', () => {
        const msg = formatAnalysisCompleteMessage('BEAR', 'NO_TRADE', undefined, GATE_BLOCKED_RESULT)
        expect(msg).toContain('按规则不执行，最终动作为不交易')
        expect(msg).toContain('**分析完成**')
    })
})
