import { ReactFlowProvider } from '@xyflow/react'
import { ArrowBigUp, Brain } from 'lucide-react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { AgentNodeComponent, VERDICT_COLORS } from '@/components/AgentCollaboration'
import type { Verdict } from '@/utils/reportText'

describe('AgentCollaboration direction localization and verdict styling (DAV-914)', () => {
    function renderAgentNode(verdict: Verdict | null, status: 'pending' | 'in_progress' | 'completed' = 'completed') {
        const data = {
            id: 'node-test',
            meta: {
                name: 'Research Manager',
                label: '研究总监',
                goal: '综合多空论据形成投资计划',
                section: 'investment_plan',
                debate: 'research' as const,
                Icon: Brain,
                badgeBg: 'bg-indigo-100 dark:bg-indigo-500/20',
                badgeText: 'text-indigo-600 dark:text-indigo-400',
            },
            status,
            verdict,
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

    it('localizes legacy English directions (BULLISH, LEAN_BULLISH, BEARISH, LEAN_BEARISH, NEUTRAL, CAUTIOUS) in verdict tag', () => {
        const testCases = [
            { raw: 'BULLISH', expected: '看多', expectedColorKey: '看多' },
            { raw: 'LEAN_BULLISH', expected: '偏多', expectedColorKey: '偏多' },
            { raw: 'BEARISH', expected: '看空', expectedColorKey: '看空' },
            { raw: 'LEAN_BEARISH', expected: '偏空', expectedColorKey: '偏空' },
            { raw: 'NEUTRAL', expected: '中性', expectedColorKey: '中性' },
            { raw: 'CAUTIOUS', expected: '谨慎', expectedColorKey: '谨慎' },
            { raw: 'BULL', expected: '看多', expectedColorKey: '看多' },
            { raw: 'BEAR', expected: '看空', expectedColorKey: '看空' },
        ]

        for (const tc of testCases) {
            const verdict: Verdict = {
                direction: tc.raw,
                reason: `论据分析理由-${tc.raw}`,
            }

            const html = renderAgentNode(verdict)

            // Verdict label shows Chinese localized direction
            expect(html).toContain(tc.expected)
            expect(html).not.toContain(`>${tc.raw}<`)

            // Color lookup uses localized semantic tone
            const expectedClasses = VERDICT_COLORS[tc.expectedColorKey]
            expect(html).toContain(expectedClasses)

            // Reason is rendered
            expect(html).toContain(`论据分析理由-${tc.raw}`)
        }
    })

    it('renders current Chinese directions as-is with appropriate color class', () => {
        const chineseCases = [
            { dir: '看多', colorKey: '看多' },
            { dir: '偏多', colorKey: '偏多' },
            { dir: '中性', colorKey: '中性' },
            { dir: '偏空', colorKey: '偏空' },
            { dir: '看空', colorKey: '看空' },
            { dir: '谨慎', colorKey: '谨慎' },
        ]

        for (const tc of chineseCases) {
            const verdict: Verdict = {
                direction: tc.dir,
                reason: `中文理由-${tc.dir}`,
            }

            const html = renderAgentNode(verdict)

            expect(html).toContain(tc.dir)
            expect(html).toContain(VERDICT_COLORS[tc.colorKey])
            expect(html).toContain(`中文理由-${tc.dir}`)
        }
    })

    it('handles unknown direction by rendering as-is and falling back to default color', () => {
        const verdict: Verdict = {
            direction: 'CUSTOM_DIR',
            reason: '未知方向理由',
        }

        const html = renderAgentNode(verdict)

        expect(html).toContain('CUSTOM_DIR')
        expect(html).toContain(VERDICT_COLORS._default)
        expect(html).toContain('未知方向理由')
    })

    it('does NOT mutate the input verdict object (display layer only)', () => {
        const originalVerdict: Verdict = {
            direction: 'BULLISH',
            reason: '多头主升浪确立',
        }

        const html = renderAgentNode(originalVerdict)

        expect(html).toContain('看多')
        expect(originalVerdict.direction).toBe('BULLISH')
        expect(originalVerdict.reason).toBe('多头主升浪确立')
    })

    it('preserves completed node status when verdict is absent', () => {
        const html = renderAgentNode(null, 'completed')

        expect(html).toContain('完成')
        expect(html).not.toContain('研判中...')
    })

    it('preserves in_progress state animation without rendering verdict', () => {
        const verdict: Verdict = {
            direction: 'BULLISH',
            reason: '不会渲染',
        }

        const html = renderAgentNode(verdict, 'in_progress')

        expect(html).toContain('研判中...')
        expect(html).not.toContain('看多')
    })
})
