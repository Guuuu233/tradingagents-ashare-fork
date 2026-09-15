import { describe, expect, it } from 'vitest'

import { getDashboardDecisionDisplay } from '@/pages/Dashboard'

describe('getDashboardDecisionDisplay', () => {
    it.each([
        ['BUY', '增持'],
        ['SELL', '减持'],
        ['HOLD', '持有'],
        ['WAIT', '观望'],
        ['NO_TRADE', '不交易'],
    ])('localizes %s without exposing the raw action', (decision, label) => {
        expect(getDashboardDecisionDisplay({ decision }).label).toBe(label)
    })

    it('prioritizes INVALID_RUN and recognizes the localized invalid state', () => {
        expect(getDashboardDecisionDisplay({
            decision: 'HOLD',
            analysis_status: 'INVALID_RUN',
        }).label).toBe('无效运行')
        expect(getDashboardDecisionDisplay({ decision: '无效运行' }).label).toBe('无效运行')
    })

    it('preserves unknown values but does not fabricate an action for missing values', () => {
        expect(getDashboardDecisionDisplay({ decision: 'CUSTOM_ACTION' }).label).toBe('CUSTOM_ACTION')
        expect(getDashboardDecisionDisplay({ decision: '' }).label).toBe('—')
        expect(getDashboardDecisionDisplay({ decision: null }).label).toBe('—')
        expect(getDashboardDecisionDisplay({}).label).toBe('—')
    })
})
