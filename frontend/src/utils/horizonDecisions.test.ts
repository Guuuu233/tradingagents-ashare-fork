import { describe, expect, it } from 'vitest'

import {
    buildHorizonDowngradeNote,
    formatDualHorizonDecisionSummary,
    formatHorizonDecisionLabel,
    isGateDowngraded,
    managerActionLabel,
    normalizeHorizonDecisions,
} from '@/utils/horizonDecisions'
import type { HorizonDecision } from '@/types'

const shortSell: HorizonDecision = {
    horizon: 'short',
    status: 'completed',
    analysis_status: 'VALID',
    direction: '看空',
    trade_action: 'SELL',
    manager_action: 'SELL',
    reason_codes: ['manager_terminal'],
    gate_blocked: false,
    non_executable: false,
    confidence: 70,
    target_price: 55.0,
    stop_loss_price: 53.3,
}

const mediumNoTradeDowngraded: HorizonDecision = {
    horizon: 'medium',
    status: 'completed',
    analysis_status: 'VALID',
    direction: '看多',
    trade_action: 'NO_TRADE',
    manager_action: 'BUY',
    reason_codes: ['price_basis_gate_blocked'],
    gate_blocked: true,
    non_executable: true,
    confidence: null,
    target_price: null,
    stop_loss_price: null,
}

describe('normalizeHorizonDecisions', () => {
    it('uses the API horizon_decisions field verbatim', () => {
        const src = { horizon_decisions: [shortSell, mediumNoTradeDowngraded] }
        expect(normalizeHorizonDecisions(src)).toHaveLength(2)
    })

    it('derives horizon decisions from AnalysisReport slices', () => {
        const report = {
            short_term: {
                status: 'completed',
                trade_action: 'SELL',
                analysis_status: 'VALID',
                direction: 'BEAR',
                confidence: 70,
                manager_verdict: { trade_action: 'SELL', direction: '偏空' },
            },
            medium_term: {
                status: 'completed',
                trade_action: 'NO_TRADE',
                analysis_status: 'VALID',
                direction: 'BULL',
                price_basis_gate: { status: 'blocked' },
                decision_status: { reason_codes: ['price_basis_gate_blocked'] },
                manager_verdict: { trade_action: 'BUY', direction: '偏多' },
            },
        }
        const hds = normalizeHorizonDecisions(report as never)
        expect(hds).toHaveLength(2)
        const [st, mt] = hds!
        expect(st.trade_action).toBe('SELL')
        expect(st.manager_action).toBe('SELL')
        expect(st.confidence).toBe(70)
        expect(mt.trade_action).toBe('NO_TRADE')
        expect(mt.manager_action).toBe('BUY')
        expect(mt.gate_blocked).toBe(true)
        expect(mt.non_executable).toBe(true)
        // 非可执行档位不得带出价格/置信度数值
        expect(mt.confidence).toBeNull()
    })

    it('reads the recorded manager action nested in investment_debate_state', () => {
        const report = {
            short_term: { status: 'completed', trade_action: 'BUY', direction: 'BULL' },
            medium_term: {
                status: 'completed',
                trade_action: 'HOLD',
                direction: 'NEUTRAL',
                investment_debate_state: { manager_verdict: { trade_action: 'WAIT' } },
            },
        }
        const hds = normalizeHorizonDecisions(report as never)
        expect(hds![1].manager_action).toBe('WAIT')
    })

    it('never infers the manager action from direction (recorded WAIT wins)', () => {
        // 返修口径：direction=偏多 但记录动作是 WAIT 时不得推算成 BUY。
        const report = {
            short_term: { status: 'completed', trade_action: 'SELL', direction: 'BEAR' },
            medium_term: {
                status: 'completed',
                trade_action: 'WAIT',
                direction: 'BULL',
                price_basis_gate: { status: 'blocked' },
                decision_status: { reason_codes: ['price_basis_gate_blocked'] },
                manager_verdict: { trade_action: 'WAIT', direction: '偏多' },
            },
        }
        const hds = normalizeHorizonDecisions(report as never)
        const mt = hds![1]
        expect(mt.manager_action).toBe('WAIT')
        expect(mt.gate_blocked).toBe(true)
        // 方向看多但经理给观望——不是降级，不得出降级说明。
        expect(isGateDowngraded(mt)).toBe(false)
        expect(buildHorizonDowngradeNote(mt)).toBeNull()
    })

    it('returns null for single-horizon reports', () => {
        expect(normalizeHorizonDecisions({
            short_term: { status: 'completed', trade_action: 'BUY', direction: 'BULL' },
        } as never)).toBeNull()
        expect(normalizeHorizonDecisions({ horizon_decisions: [shortSell] })).toBeNull()
        expect(normalizeHorizonDecisions(null)).toBeNull()
        expect(normalizeHorizonDecisions(undefined)).toBeNull()
    })
})

describe('formatHorizonDecisionLabel status vocabulary', () => {
    it('renders 卖出（看空） / 不交易（看多） for opposite dual conclusions', () => {
        expect(formatHorizonDecisionLabel(shortSell)).toBe('卖出（看空）')
        expect(formatHorizonDecisionLabel(mediumNoTradeDowngraded)).toBe('不交易（看多）')
    })

    it('renders 不交易（弃权） for ABSTAIN', () => {
        expect(formatHorizonDecisionLabel({
            horizon: 'medium',
            status: 'completed',
            analysis_status: 'ABSTAIN',
            trade_action: 'NO_TRADE',
            direction: 'N/A',
        })).toBe('不交易（弃权）')
    })

    it('renders 无效运行 for INVALID_RUN / DATA_ERROR', () => {
        for (const analysis_status of ['INVALID_RUN', 'DATA_ERROR']) {
            expect(formatHorizonDecisionLabel({
                horizon: 'short',
                status: 'completed',
                analysis_status,
                trade_action: 'NO_TRADE',
            })).toBe('无效运行')
        }
    })

    it('renders 未完成 for a failed horizon', () => {
        expect(formatHorizonDecisionLabel({
            horizon: 'medium',
            status: 'failed',
        })).toBe('未完成')
    })

    it('omits the direction parens when direction is N/A', () => {
        expect(formatHorizonDecisionLabel({
            horizon: 'short',
            status: 'completed',
            analysis_status: 'VALID',
            trade_action: 'NO_TRADE',
            direction: 'N/A',
        })).toBe('不交易')
    })
})

describe('formatDualHorizonDecisionSummary', () => {
    it('joins both horizons per spec', () => {
        expect(formatDualHorizonDecisionSummary([shortSell, mediumNoTradeDowngraded]))
            .toBe('短线：卖出（看空）｜中线：不交易（看多）')
    })

    it('returns null for single-horizon input', () => {
        expect(formatDualHorizonDecisionSummary([shortSell])).toBeNull()
        expect(formatDualHorizonDecisionSummary(null)).toBeNull()
    })
})

describe('gate downgrade per horizon', () => {
    it('flags the downgraded horizon and names the manager action', () => {
        expect(isGateDowngraded(mediumNoTradeDowngraded)).toBe(true)
        expect(isGateDowngraded(shortSell)).toBe(false)
        expect(buildHorizonDowngradeNote(mediumNoTradeDowngraded)).toContain('研究经理原结论买入')
        expect(buildHorizonDowngradeNote(mediumNoTradeDowngraded)).toContain('不交易')
        expect(buildHorizonDowngradeNote(shortSell)).toBeNull()
    })

    it('does not flag a plain NO_TRADE without gate signal', () => {
        expect(isGateDowngraded({
            horizon: 'medium',
            trade_action: 'NO_TRADE',
            manager_action: 'BUY',
            gate_blocked: false,
        })).toBe(false)
    })

    it('does not flag an ABSTAIN horizon even when the gate blocked', () => {
        // 裁决自洽检查拦下（ABSTAIN）不是价格门降级。
        expect(isGateDowngraded({
            horizon: 'medium',
            analysis_status: 'ABSTAIN',
            trade_action: 'NO_TRADE',
            manager_action: 'BUY',
            gate_blocked: true,
        })).toBe(false)
        expect(buildHorizonDowngradeNote({
            horizon: 'medium',
            analysis_status: 'ABSTAIN',
            trade_action: 'NO_TRADE',
            manager_action: 'BUY',
            gate_blocked: true,
        })).toBeNull()
    })

    it('does not flag when the recorded manager action equals the final action', () => {
        expect(isGateDowngraded({
            horizon: 'short',
            analysis_status: 'VALID',
            trade_action: 'WAIT',
            manager_action: 'WAIT',
            gate_blocked: true,
        })).toBe(false)
    })
})

describe('managerActionLabel', () => {
    it('maps canonical actions to Chinese', () => {
        expect(managerActionLabel('BUY')).toBe('买入')
        expect(managerActionLabel('SELL')).toBe('卖出')
        expect(managerActionLabel('HOLD')).toBe('持有')
        expect(managerActionLabel(null)).toBeNull()
    })
})
