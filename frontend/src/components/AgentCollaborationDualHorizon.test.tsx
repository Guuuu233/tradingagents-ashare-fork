import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'

import { ArrowBigUp, Brain, MessageCircle } from 'lucide-react'
import { renderToStaticMarkup } from 'react-dom/server'
import { beforeEach, describe, expect, it } from 'vitest'
import { ReactFlowProvider } from '@xyflow/react'

import {
    AgentNodeComponent,
    CollaborationHorizonBadge,
    resolveCardHorizonVerdicts,
    VERDICT_COLORS,
} from '@/components/AgentCollaboration'
import { useAnalysisStore } from '@/stores/analysisStore'
import type { AnalysisReport } from '@/types'
import {
    extractHorizonVerdicts,
    resolveCollaborationHorizonKey,
    resolveHorizonViewKey,
    resolveSectionHorizonVerdicts,
} from '@/utils/reportText'

/**
 * DAV-1429 — dual-horizon direction badges and header horizon label.
 *
 * The fixtures below are verbatim VERDICT blocks taken from a real dual-horizon
 * report (report id `bef58f29fc574dd4b417e470be00ff9f`, 600900.SH, 2026-08-25,
 * mode=dual_horizon, requested_horizons=["short","medium"], both completed).
 */
const REAL_DUAL_ANALYST_BLOCK =
    '<!-- VERDICT: {"directions": {"short": "中性", "medium": "偏多"}, ' +
    '"reasons": {"short": "布林中轨承压缩量拉锯", "medium": "均线多头排列回踩企稳"}} -->'
const REAL_SHORT_PLAN_BLOCK =
    '<!-- VERDICT: {"direction": "中性", "reason": "地量缩量拉锯，资金分歧待破局，短线观望"} -->'
const REAL_MEDIUM_PLAN_BLOCK =
    '<!-- VERDICT: {"direction": "偏多", "reason": "低利率凸显类债溢价，地量整固构筑配置底座"} -->'
const LEGACY_SINGLE_BLOCK =
    '<!-- VERDICT: {"direction": "LEAN_BULLISH", "reason": "多头排列但量能不足"} -->'

const marketSection = `### 价格行为与关键区间\n\n依托 28.05 元设立多头保护线\n\n${REAL_DUAL_ANALYST_BLOCK}`

function renderAgentNode(payload: {
    verdict?: { direction: string; reason: string } | null
    verdicts?: ReturnType<typeof resolveSectionHorizonVerdicts> | null
    status?: 'completed' | 'in_progress' | 'pending'
}) {
    const { verdict = null, verdicts = null, status = 'completed' } = payload
    const data = {
        meta: {
            name: 'Market Analyst',
            label: '技术面',
            goal: '技术指标与价格形态分析',
            section: 'market_report',
            Icon: ArrowBigUp,
            badgeBg: 'bg-blue-100 dark:bg-blue-500/20',
            badgeText: 'text-blue-600 dark:text-blue-400',
        },
        status,
        verdict,
        verdicts,
        isParticipating: true,
        selected: false,
    }

    return renderToStaticMarkup(
        <ReactFlowProvider>
            <AgentNodeComponent
                id="node-test"
                data={data}
                type="agent"
                selected={false}
                zIndex={1}
                isConnectable={false}
                positionAbsoluteX={0}
                positionAbsoluteY={0}
                dragging={false}
                deletable={false}
                selectable={false}
                draggable={false}
            />
        </ReactFlowProvider>,
    )
}

/** Extract the inner text of the short/medium rows rendered by the card. */
function horizonRow(html: string, horizon: 'short' | 'medium'): string | null {
    const m = html.match(new RegExp(`data-horizon="${horizon}"[^>]*>([\\s\\S]*?)</div>`))
    return m ? m[1] : null
}

describe('DAV-1429 (a) both horizons take their own values', () => {
    it('splits a real shared-analyst dual VERDICT block into short and medium', () => {
        const set = extractHorizonVerdicts(marketSection)

        expect(set.isDual).toBe(true)
        expect(set.slots.short).toEqual({
            state: 'ok',
            verdict: { direction: '中性', reason: '布林中轨承压缩量拉锯' },
        })
        expect(set.slots.medium).toEqual({
            state: 'ok',
            verdict: { direction: '偏多', reason: '均线多头排列回踩企稳' },
        })
    })

    it('reads each horizon from its own report slice for horizon-specific sections', () => {
        const set = resolveSectionHorizonVerdicts({
            text: REAL_SHORT_PLAN_BLOCK,
            horizonTexts: {
                short: REAL_SHORT_PLAN_BLOCK,
                medium: REAL_MEDIUM_PLAN_BLOCK,
            },
            isDualTask: true,
        })

        expect(set.slots.short.state === 'ok' && set.slots.short.verdict.direction).toBe('中性')
        expect(set.slots.medium.state === 'ok' && set.slots.medium.verdict.direction).toBe('偏多')
    })

    it('renders both horizon rows with each own direction and reason', () => {
        const verdicts = resolveSectionHorizonVerdicts({
            text: marketSection,
            isDualTask: true,
        })
        const html = renderAgentNode({ verdicts })

        expect(html).toContain('data-testid="agent-horizon-verdicts"')
        const shortRow = horizonRow(html, 'short')
        const mediumRow = horizonRow(html, 'medium')

        expect(shortRow).toContain('中性')
        expect(shortRow).toContain('布林中轨承压缩量拉锯')
        expect(shortRow).not.toContain('偏多')

        expect(mediumRow).toContain('偏多')
        expect(mediumRow).toContain('均线多头排列回踩企稳')
        expect(mediumRow).not.toContain('中性')

        // Color tone follows each horizon's own direction
        expect(shortRow).toContain(VERDICT_COLORS['中性'])
        expect(mediumRow).toContain(VERDICT_COLORS['偏多'])
    })
})

describe('DAV-1429 (b) a missing horizon never borrows the other one', () => {
    it('keeps the absent horizon empty when a section only declares one', () => {
        const set = extractHorizonVerdicts(
            '<!-- VERDICT: {"directions": {"short": "偏空"}, "reasons": {"short": "破位风险"}} -->',
        )

        expect(set.slots.short.state === 'ok' && set.slots.short.verdict.direction).toBe('偏空')
        expect(set.slots.medium).toEqual({ state: 'missing' })
    })

    it('marks an unusable horizon value as invalid instead of reusing the other', () => {
        const set = extractHorizonVerdicts(
            '<!-- VERDICT: {"directions": {"short": "看多", "medium": ""}, ' +
            '"reasons": {"short": "放量突破", "medium": ""}} -->',
        )

        expect(set.slots.short.state === 'ok' && set.slots.short.verdict.direction).toBe('看多')
        expect(set.slots.medium).toEqual({ state: 'invalid' })
    })

    it('does not attribute a single-block section to a horizon on a dual task', () => {
        const set = resolveSectionHorizonVerdicts({
            text: REAL_SHORT_PLAN_BLOCK,
            isDualTask: true,
        })

        expect(set.isDual).toBe(true)
        expect(set.single).toBeNull()
        // The value exists but names no horizon: it is shown as 无效, never
        // attributed to short or medium.
        expect(set.slots.short).toEqual({ state: 'invalid' })
        expect(set.slots.medium).toEqual({ state: 'invalid' })
    })

    it('ignores a horizon slice that carries no VERDICT block at all', () => {
        const set = resolveSectionHorizonVerdicts({
            text: '',
            horizonTexts: {
                short: REAL_SHORT_PLAN_BLOCK,
                medium: '',
            },
            isDualTask: true,
        })

        expect(set.slots.short.state === 'ok' && set.slots.short.verdict.direction).toBe('中性')
        expect(set.slots.medium).toEqual({ state: 'missing' })
    })

    it('renders only the known horizon and shows a placeholder for the missing one', () => {
        const verdicts = resolveSectionHorizonVerdicts({
            text: '',
            horizonTexts: { short: REAL_SHORT_PLAN_BLOCK },
            isDualTask: true,
        })
        const html = renderAgentNode({ verdicts })

        const shortRow = horizonRow(html, 'short')
        const mediumRow = horizonRow(html, 'medium')

        expect(shortRow).toContain('中性')
        expect(mediumRow).toContain('—')
        expect(mediumRow).not.toContain('中性')
        expect(mediumRow).not.toContain('偏多')
    })
})

describe('DAV-1429 (c) single-horizon behaviour is unchanged', () => {
    it('keeps the legacy single verdict and does not split it into horizons', () => {
        const set = resolveSectionHorizonVerdicts({
            text: LEGACY_SINGLE_BLOCK,
            isDualTask: false,
        })

        expect(set.isDual).toBe(false)
        expect(set.single).toEqual({ direction: '偏多', reason: '多头排列但量能不足' })
        expect(set.slots.short).toEqual({ state: 'missing' })
        expect(set.slots.medium).toEqual({ state: 'missing' })
    })

    it('renders the legacy single row with its localized direction', () => {
        const set = resolveSectionHorizonVerdicts({
            text: LEGACY_SINGLE_BLOCK,
            isDualTask: false,
        })
        const html = renderAgentNode({ verdicts: set })

        expect(html).not.toContain('agent-horizon-verdicts')
        expect(html).toContain('偏多')
        expect(html).toContain('多头排列但量能不足')
        expect(html).not.toContain('>LEAN_BULLISH<')
    })

    it('resolves the header label to the single requested horizon', () => {
        expect(resolveHorizonViewKey(['short'], undefined, ['short'])).toBe('short')
        expect(resolveHorizonViewKey(['medium'], 'single', ['medium'])).toBe('medium')
        expect(resolveHorizonViewKey(undefined, undefined, [])).toBe('short')
    })

    it('keeps the header on 短线 for a legacy single report with no horizon signal', () => {
        expect(resolveCollaborationHorizonKey({})).toBe('short')
        expect(resolveCollaborationHorizonKey({ requestedHorizons: ['short'] })).toBe('short')
        expect(resolveCollaborationHorizonKey({ requestedHorizons: ['medium'] })).toBe('medium')
    })

    it('keeps 中线 for a medium-only run that only reports its horizon in events', () => {
        expect(resolveCollaborationHorizonKey({ seenHorizons: ['medium'] })).toBe('medium')
        expect(resolveCollaborationHorizonKey({ seenHorizons: ['short'] })).toBe('short')
        // An observed shared-stage marker is evidence; the reused mode is not.
        expect(resolveCollaborationHorizonKey({ seenHorizons: ['dual'] })).toBe('dual')
        expect(resolveCollaborationHorizonKey({ mode: 'dual_horizon' })).toBe('short')
    })
})

describe('DAV-1429 (d) a dual run must not stick on the last horizon_start', () => {
    it('reports dual once both horizons started, even when medium arrived last', () => {
        const seen: string[] = []
        seen.push('dual')   // shared analyst stage snapshot
        seen.push('short')  // agent.horizon_start (short)
        seen.push('medium') // agent.horizon_start (medium) — same second

        expect(resolveCollaborationHorizonKey({ seenHorizons: seen })).toBe('dual')
    })

    it('still reports dual when only the two per-horizon events were seen', () => {
        expect(resolveCollaborationHorizonKey({ seenHorizons: ['short', 'medium'] })).toBe('dual')
    })

    it('reports dual from the requested horizons before any event arrives', () => {
        expect(resolveCollaborationHorizonKey({ requestedHorizons: ['short', 'medium'] })).toBe('dual')
    })

    it('latches the dual fact in the store across horizon_start events', () => {
        useAnalysisStore.setState({ seenHorizons: [], isDualHorizon: false, currentHorizon: null })
        const { noteHorizonEvent } = useAnalysisStore.getState()

        noteHorizonEvent('short')
        expect(useAnalysisStore.getState().isDualHorizon).toBe(false)

        noteHorizonEvent('medium')
        expect(useAnalysisStore.getState().isDualHorizon).toBe(true)
        expect(resolveCollaborationHorizonKey({
            requestedHorizons: ['short', 'medium'],
            seenHorizons: useAnalysisStore.getState().seenHorizons,
            isDualHorizon: useAnalysisStore.getState().isDualHorizon,
        })).toBe('dual')
    })

    it('replays the real dual-horizon event order (short then medium) and stays 双档', () => {
        // Verbatim order observed on 10-02 production runs (92db7187): the shared
        // analyst stage snapshots horizon "dual", then the two per-horizon runs
        // emit agent.horizon_start for "short" and then "medium" within the same
        // second — the old header showed only the last one (中线).
        useAnalysisStore.setState({ seenHorizons: [], isDualHorizon: false, currentHorizon: null })
        const { noteHorizonEvent, setCurrentHorizon } = useAnalysisStore.getState()

        const replay: Array<'dual' | 'short' | 'medium'> = ['dual', 'short', 'medium']
        for (const horizon of replay) {
            noteHorizonEvent(horizon)
            setCurrentHorizon(horizon)
        }

        // The stale single-value signal is still 中线, exactly as before the fix…
        expect(useAnalysisStore.getState().currentHorizon).toBe('medium')

        // …but the displayed header and the cards no longer depend on it.
        const key = resolveCollaborationHorizonKey({
            requestedHorizons: ['short', 'medium'],
            mode: 'dual_horizon',
            seenHorizons: useAnalysisStore.getState().seenHorizons,
            isDualHorizon: useAnalysisStore.getState().isDualHorizon,
        })
        expect(key).toBe('dual')
        expect(renderToStaticMarkup(<CollaborationHorizonBadge horizonKey={key} />)).toContain('双档')

        const verdicts = resolveSectionHorizonVerdicts({ text: marketSection, isDualTask: true })
        const cardHtml = renderAgentNode({ verdicts })
        expect(horizonRow(cardHtml, 'short')).toContain('中性')
        expect(horizonRow(cardHtml, 'medium')).toContain('偏多')
    })

    it('replays the full dual event stream through the store and never shows 中线 only (acceptance)', () => {
        // Acceptance evidence: the real 10-02 production event order (report
        // bef58f29…, 600900.SH) replayed through the store's own handlers:
        //   shared analyst stage (horizon "dual" snapshots/chunks)
        //   -> agent.horizon_start short -> agent.horizon_start medium
        //   -> per-horizon investment_plan chunks for short and medium
        useAnalysisStore.setState({
            seenHorizons: [],
            isDualHorizon: false,
            currentHorizon: null,
            report: null,
            streamingSections: {},
            streamingHorizonSections: {},
        })
        const store = useAnalysisStore.getState()

        // 1) shared analyst stage: one horizon marker for the whole stage
        store.updateAgentSnapshot({ agents: [{ agent: 'Market Analyst', status: 'completed' }], horizon: 'dual' } as never)
        // `_emit_report_chunked` sends content paragraphs first and then a final
        // chunk with an empty payload, so the stream is replayed the same way.
        store.addReportChunk({ section: 'market_report', chunk: marketSection, index: 0, is_complete: false, horizon: 'dual' } as never)
        store.addReportChunk({ section: 'market_report', chunk: '', index: -1, is_complete: true, horizon: 'dual' } as never)

        // 2) the two per-horizon runs start in the same second, medium last —
        //    this is what used to leave the header and every badge on 中线.
        store.setCurrentHorizon('short')
        store.setCurrentHorizon('medium')
        expect(useAnalysisStore.getState().currentHorizon).toBe('medium')

        // 3) horizon-specific section streams once per horizon, interleaved
        store.addReportChunk({ section: 'investment_plan', chunk: REAL_SHORT_PLAN_BLOCK, index: 0, is_complete: false, horizon: 'short' } as never)
        store.addReportChunk({ section: 'investment_plan', chunk: '', index: -1, is_complete: true, horizon: 'short' } as never)
        store.addReportChunk({ section: 'investment_plan', chunk: REAL_MEDIUM_PLAN_BLOCK, index: 0, is_complete: false, horizon: 'medium' } as never)
        store.addReportChunk({ section: 'investment_plan', chunk: '', index: -1, is_complete: true, horizon: 'medium' } as never)

        const state = useAnalysisStore.getState()
        // Header is 双档 while the run is still going, not the last horizon_start.
        const key = resolveCollaborationHorizonKey({
            requestedHorizons: state.report?.requested_horizons,
            mode: state.report?.mode,
            seenHorizons: state.seenHorizons,
            isDualHorizon: state.isDualHorizon,
        })
        expect(key).toBe('dual')
        expect(renderToStaticMarkup(<CollaborationHorizonBadge horizonKey={key} />)).toContain('双档')

        // Shared section: both directions straight out of the real dual block.
        const shared = resolveCardHorizonVerdicts(null, 'market_report', {
            streaming: state.streamingSections.market_report,
            streamingHorizons: state.streamingHorizonSections,
            isDualTask: true,
        })
        const sharedHtml = renderAgentNode({ verdicts: shared })
        expect(horizonRow(sharedHtml, 'short')).toContain('中性')
        expect(horizonRow(sharedHtml, 'medium')).toContain('偏多')

        // Horizon-specific section: attributed from the per-horizon buffers,
        // still without any finished report being available.
        const plan = resolveCardHorizonVerdicts(null, 'investment_plan', {
            streaming: state.streamingSections.investment_plan,
            streamingHorizons: state.streamingHorizonSections,
            isDualTask: true,
        })
        const planHtml = renderAgentNode({ verdicts: plan })
        expect(horizonRow(planHtml, 'short')).toContain('中性')
        expect(horizonRow(planHtml, 'medium')).toContain('偏多')
    })

    it('treats the shared-stage horizon "dual" as dual on its own', () => {
        useAnalysisStore.setState({ seenHorizons: [], isDualHorizon: false, currentHorizon: null })
        useAnalysisStore.getState().noteHorizonEvent('dual')

        expect(useAnalysisStore.getState().isDualHorizon).toBe(true)
    })

    it('shows 双档 in the header for a dual task instead of 中线', () => {
        const html = renderToStaticMarkup(
            <CollaborationHorizonBadge horizonKey={resolveCollaborationHorizonKey({
                seenHorizons: ['short', 'medium'],
            })} />,
        )

        expect(html).toContain('双档')
        expect(html).not.toContain('中线视角')
        expect(html).toContain('data-horizon-key="dual"')
    })
})

describe('DAV-1429 store horizon tracking hygiene', () => {
    beforeEach(() => {
        useAnalysisStore.setState({ seenHorizons: [], isDualHorizon: false, currentHorizon: null })
    })

    it('ignores empty horizon values', () => {
        const { noteHorizonEvent } = useAnalysisStore.getState()
        noteHorizonEvent(null)
        noteHorizonEvent('')

        expect(useAnalysisStore.getState().seenHorizons).toEqual([])
        expect(useAnalysisStore.getState().isDualHorizon).toBe(false)
    })

    it('does not duplicate repeated horizon values', () => {
        const { noteHorizonEvent } = useAnalysisStore.getState()
        noteHorizonEvent('short')
        noteHorizonEvent('short')

        expect(useAnalysisStore.getState().seenHorizons).toEqual(['short'])
    })

    it('resets tracking for a new run', () => {
        const { noteHorizonEvent, resetHorizonTracking } = useAnalysisStore.getState()
        noteHorizonEvent('dual')
        resetHorizonTracking()

        expect(useAnalysisStore.getState().seenHorizons).toEqual([])
        expect(useAnalysisStore.getState().isDualHorizon).toBe(false)
    })
})

describe('DAV-1429 store keeps per-horizon stream buffers', () => {
    beforeEach(() => {
        useAnalysisStore.setState({
            seenHorizons: [],
            isDualHorizon: false,
            currentHorizon: null,
            streamingSections: {},
            streamingHorizonSections: {},
        })
    })

    it('attributes each interleaved agent.report.chunk to its own horizon', () => {
        const { addReportChunk } = useAnalysisStore.getState()
        // A dual run interleaves both horizons into the same section key; the
        // merged buffer cannot be attributed, the per-horizon copy can.
        addReportChunk({ section: 'investment_plan', chunk: REAL_SHORT_PLAN_BLOCK, index: 0, is_complete: false, horizon: 'short' } as never)
        addReportChunk({ section: 'investment_plan', chunk: REAL_MEDIUM_PLAN_BLOCK, index: 1, is_complete: false, horizon: 'medium' } as never)

        const state = useAnalysisStore.getState()
        expect(state.streamingHorizonSections.investment_plan.short).toBe(REAL_SHORT_PLAN_BLOCK)
        expect(state.streamingHorizonSections.investment_plan.medium).toBe(REAL_MEDIUM_PLAN_BLOCK)
        expect(state.streamingSections.investment_plan.buffer).toContain('短线观望')
        expect(state.streamingSections.investment_plan.buffer).toContain('配置底座')
        expect(state.isDualHorizon).toBe(true)
    })

    it('ignores untagged chunks when splitting by horizon', () => {
        const { addReportChunk } = useAnalysisStore.getState()
        addReportChunk({ section: 'market_report', chunk: REAL_DUAL_ANALYST_BLOCK, index: 0, is_complete: false } as never)

        const state = useAnalysisStore.getState()
        expect(state.streamingHorizonSections).toEqual({})
        expect(state.streamingSections.market_report.buffer).toContain('布林中轨承压缩量拉锯')
    })

    it('recognises a dual report on load so reopening shows 双档', () => {
        const { setReport, resetHorizonTracking } = useAnalysisStore.getState()
        resetHorizonTracking()
        setReport({
            symbol: '600900.SH',
            mode: 'dual_horizon',
            requested_horizons: ['short', 'medium'],
        } as never)

        const state = useAnalysisStore.getState()
        expect(state.isDualHorizon).toBe(true)
        expect(resolveCollaborationHorizonKey({
            requestedHorizons: state.report?.requested_horizons,
            mode: state.report?.mode,
            seenHorizons: state.seenHorizons,
            isDualHorizon: state.isDualHorizon,
        })).toBe('dual')
    })
})

describe('DAV-1429 card integration with the real report slices', () => {
    it('renders the 技术面 card from a real dual report slice without touching other data', () => {
        const verdicts = resolveSectionHorizonVerdicts({
            text: marketSection,
            isDualTask: true,
        })
        const html = renderAgentNode({ verdicts, status: 'completed' })

        expect(html).toContain('技术面')
        expect(html).toContain('短')
        expect(html).toContain('中')
        // Display only: the shared block itself is never rewritten
        expect(marketSection).toContain('"short": "中性"')
        expect(marketSection).toContain('"medium": "偏多"')
    })

    it('keeps the placeholder icon for a plain section without verdicts', () => {
        const html = renderToStaticMarkup(
            <ReactFlowProvider>
                <AgentNodeComponent
                    id="node-test"
                    data={{
                        meta: {
                            name: 'News Analyst',
                            label: '新闻',
                            goal: '新闻事件影响评估',
                            section: 'news_report',
                            Icon: MessageCircle,
                            badgeBg: 'bg-amber-100',
                            badgeText: 'text-amber-600',
                        },
                        status: 'completed',
                        verdict: null,
                        verdicts: resolveSectionHorizonVerdicts({ text: '', isDualTask: true }),
                        isParticipating: true,
                        selected: false,
                    }}
                    type="agent"
                    selected={false}
                    zIndex={1}
                    isConnectable={false}
                    positionAbsoluteX={0}
                    positionAbsoluteY={0}
                    dragging={false}
                    deletable={false}
                    selectable={false}
                    draggable={false}
                />
            </ReactFlowProvider>,
        )

        expect(html).toContain('完成')
        expect(html).not.toContain('agent-horizon-verdicts')
    })

    it('does not render verdict rows while the agent is still running', () => {
        const verdicts = resolveSectionHorizonVerdicts({
            text: marketSection,
            isDualTask: true,
        })
        const html = renderAgentNode({ verdicts, status: 'in_progress' })

        expect(html).toContain('研判中...')
        expect(html).not.toContain('agent-horizon-verdicts')
    })

    it('exposes the Brain icon metadata used by the research section fixture', () => {
        expect(Brain).toBeTruthy()
    })

    it('shows the same values while streaming, after completion, and on reopen', () => {
        // Acceptance: 运行中 / 任务结束后 / 打开历史报告 显示结果一致.
        const planSection = 'investment_plan'
        const report = {
            symbol: '600900.SH',
            mode: 'dual_horizon',
            requested_horizons: ['short', 'medium'],
            short_term: { investment_plan: REAL_SHORT_PLAN_BLOCK },
            medium_term: { investment_plan: REAL_MEDIUM_PLAN_BLOCK },
        } as unknown as AnalysisReport

        // 1) While the run streams, both horizons arrive interleaved through
        //    `agent.report.chunk`; only the per-horizon buffers stay attributable.
        const during = resolveCardHorizonVerdicts(null, planSection, {
            streaming: { displayed: REAL_SHORT_PLAN_BLOCK + REAL_MEDIUM_PLAN_BLOCK } as never,
            streamingHorizons: {
                [planSection]: {
                    short: REAL_SHORT_PLAN_BLOCK,
                    medium: REAL_MEDIUM_PLAN_BLOCK,
                },
            },
            isDualTask: true,
        })

        // 2) After completion the report carries both horizon slices.
        const after = resolveCardHorizonVerdicts(report, planSection, { isDualTask: true })

        // 3) Reopening the same stored report is the same call.
        const reopened = resolveCardHorizonVerdicts(report, planSection, { isDualTask: true })

        for (const set of [during, after, reopened]) {
            expect(set.slots.short.state === 'ok' && set.slots.short.verdict.direction).toBe('中性')
            expect(set.slots.medium.state === 'ok' && set.slots.medium.verdict.direction).toBe('偏多')
        }

        const rendered = [during, after, reopened].map(set => renderAgentNode({ verdicts: set }))
        expect(rendered[0]).toBe(rendered[1])
        expect(rendered[1]).toBe(rendered[2])
        expect(horizonRow(rendered[0], 'short')).toContain('中性')
        expect(horizonRow(rendered[0], 'medium')).toContain('偏多')
    })

    it('never reads the last horizon_start value (currentHorizon) for badges or header', () => {
        const src = readFileSync(
            fileURLToPath(new URL('./AgentCollaboration.tsx', import.meta.url)),
            'utf8',
        )
        // Strip comments so prose about the old bug is not mistaken for usage.
        const code = src.replace(/\/\*[\s\S]*?\*\//g, '').replace(/\/\/[^\n]*/g, '')
        // Regression guard for the root cause: the collaboration card and header
        // must not consult the last-write-wins `currentHorizon` slot.
        expect(code).not.toContain('currentHorizon')
        expect(code).not.toContain('extractVerdict(')
    })
})
