import { beforeEach, describe, expect, it } from 'vitest'
import type { AnalysisReport } from '@/types'
import { isDualHorizonReport, useAnalysisStore } from '@/stores/analysisStore'
import { getDualHorizonSummary } from '@/utils/reportDualHorizon'
import { resolveCollaborationHorizonKey, resolveHorizonViewKey } from '@/utils/reportText'

const report = (fields: Record<string, unknown> = {}): AnalysisReport => ({
    symbol: '600900.SH', trade_date: '2026-08-25', ...fields,
}) as AnalysisReport
const dual = report({ id: 'dual', requested_horizons: ['short', 'medium'] })

beforeEach(() => useAnalysisStore.getState().reset())

describe('DAV-1444 N1: mode is not evidence of two horizons', () => {
    it.each(['short', 'medium'] as const)('keeps a mode-only %s run single and agrees with the report summary', horizon => {
        const single = report({ mode: 'dual_horizon', requested_horizons: null })
        useAnalysisStore.getState().setReport(single)
        useAnalysisStore.getState().noteHorizonEvent(horizon)
        const state = useAnalysisStore.getState()
        expect(getDualHorizonSummary(single)).toBeNull()
        expect(isDualHorizonReport(single)).toBe(false)
        expect(state.isDualHorizon).toBe(false)
        expect(resolveHorizonViewKey(null, single.mode, [horizon])).toBe(horizon)
        expect(resolveCollaborationHorizonKey({ report: single, seenHorizons: state.seenHorizons })).toBe(horizon)
        // A stale/legacy boolean cannot override the actually observed set.
        expect(resolveCollaborationHorizonKey({ mode: single.mode, isDualHorizon: true, seenHorizons: [horizon] })).toBe(horizon)
    })

    it.each([
        [{ mode: 'dual_horizon' }, false],
        [{ requested_horizons: ['short', 'medium'] }, true],
        [{ horizon_status: { short: 'completed', medium: 'failed' } }, true],
        [{ falsification_conditions_by_horizon: { short: [] } }, true],
        [{ not_applicable_by_horizon: { medium: false } }, true],
        [{ short_term: { status: 'completed' }, medium_term: { status: 'completed' } }, true],
        [{ mode: 'single_horizon', requested_horizons: ['short', 'medium'] }, false],
    ] as const)('uses the existing summary detector for %j', (fields, expected) => {
        const stored = report(fields)
        expect(Boolean(getDualHorizonSummary(stored))).toBe(expected)
        expect(isDualHorizonReport(stored)).toBe(expected)
        useAnalysisStore.getState().setReport(stored)
        expect(useAnalysisStore.getState().isDualHorizon).toBe(expected)
        expect(resolveCollaborationHorizonKey({ report: stored }) === 'dual').toBe(expected)
    })

    it.each(['dual', 'all'])('recognises an observed shared-stage %s marker, not a mode string', marker => {
        useAnalysisStore.getState().noteHorizonEvent(marker)
        const state = useAnalysisStore.getState()
        expect(state.isDualHorizon).toBe(true)
        expect(resolveCollaborationHorizonKey({ seenHorizons: state.seenHorizons })).toBe('dual')
    })

    it('uses the observed set before a conflicting single requested horizon', () => {
        expect(resolveCollaborationHorizonKey({ requestedHorizons: ['short'], seenHorizons: ['medium'] })).toBe('medium')
    })

    it.each(['short', 'medium'] as const)('reopens a legacy %s slice without counting the empty other slice', horizon => {
        const other = horizon === 'short' ? 'medium' : 'short'
        const single = report({
            mode: 'dual_horizon', requested_horizons: null,
            [`${horizon}_term`]: { horizon, company_of_interest: '600900.SH', trade_date: '2026-08-25' },
            [`${other}_term`]: { horizon: other, company_of_interest: '', trade_date: '' },
        })
        expect(getDualHorizonSummary(single)).toBeNull()
        expect(resolveCollaborationHorizonKey({ report: single })).toBe(horizon)
    })

    it('reopens a legacy top-level medium report', () => {
        expect(resolveCollaborationHorizonKey({ report: report({ horizon: 'medium' }) })).toBe('medium')
    })
})

describe('DAV-1444 N2: report replacement is authoritative', () => {
    it.each(['short', 'medium'] as const)('falls back from dual to a new %s report of the same symbol', horizon => {
        const store = useAnalysisStore.getState()
        store.setReport(dual)
        store.addReportChunk({ section: 'investment_plan', chunk: 'old dual verdict', horizon: 'medium', is_complete: false } as never)
        store.setReport(report({ id: 'single', horizon }))
        const state = useAnalysisStore.getState()
        expect(state.isDualHorizon).toBe(false)
        expect(state.seenHorizons).toEqual([])
        expect(state.streamingSections).toEqual({})
        expect(state.streamingHorizonSections).toEqual({})
        expect(resolveCollaborationHorizonKey({ report: state.report, seenHorizons: state.seenHorizons })).toBe(horizon)
    })

    it('recalculates even when result_data has no report id', () => {
        const store = useAnalysisStore.getState()
        store.setReport(dual)
        store.setReport(report({ symbol: '000725.SZ', horizon: 'short' }))
        expect(useAnalysisStore.getState().isDualHorizon).toBe(false)
    })

    it('clears tracking and streams on setReport(null)', () => {
        const store = useAnalysisStore.getState()
        store.setReport(dual)
        store.setCurrentHorizon('medium')
        store.addReportChunk({ section: 'market_report', chunk: 'old', horizon: 'dual' } as never)
        store.setReport(null)
        const state = useAnalysisStore.getState()
        expect(state.isDualHorizon).toBe(false)
        expect(state.seenHorizons).toEqual([])
        expect(state.currentHorizon).toBeNull()
        expect(state.streamingHorizonSections).toEqual({})
    })

    it.each(['reset', 'clearSession'] as const)('does not carry horizons or streams into the next task after %s', action => {
        const store = useAnalysisStore.getState()
        store.setReport(dual)
        store.addReportChunk({ section: 'investment_plan', chunk: 'old', horizon: 'medium' } as never)
        store[action]()
        store.noteHorizonEvent('short')
        const state = useAnalysisStore.getState()
        expect(state.report).toBeNull()
        expect(state.isDualHorizon).toBe(false)
        expect(state.seenHorizons).toEqual(['short'])
        expect(state.streamingHorizonSections).toEqual({})
    })

    it('does not persist runtime horizon tracking or stream buffers and resets them on hydration', () => {
        const store = useAnalysisStore.getState()
        store.noteHorizonEvent('dual')
        const { partialize, merge } = useAnalysisStore.persist.getOptions()
        const saved = partialize!(useAnalysisStore.getState())
        for (const key of ['seenHorizons', 'isDualHorizon', 'streamingHorizonSections']) {
            expect(saved).not.toHaveProperty(key)
        }
        const restored = merge!(saved, useAnalysisStore.getState())
        expect(restored.isDualHorizon).toBe(false)
        expect(restored.seenHorizons).toEqual([])
        expect(restored.streamingHorizonSections).toEqual({})
    })
})
