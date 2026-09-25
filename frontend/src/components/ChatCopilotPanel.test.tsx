import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import ChatCopilotPanel, {
    formatAnalysisCompleteMessage,
    formatAnalysisRecoveryMessage,
    formatAnalysisNotificationBody,
} from '@/components/ChatCopilotPanel'

describe('ChatCopilotPanel horizon selector (DAV-1288)', () => {
    it('renders the analysis horizon selector in the chat panel with short as default', () => {
        const html = renderToStaticMarkup(
            <ChatCopilotPanel onSymbolDetected={() => {}} />,
        )
        expect(html).toContain('analysis-horizon-selector')
        expect(html).toContain('data-testid="horizon-short"')
        expect(html).toContain('data-testid="horizon-medium"')
        expect(html).toContain('data-testid="horizon-dual"')
        expect(html).toContain('分析档位')
        // 默认选中短线（active 按钮带 aria-pressed=true）
        expect(html).toMatch(/data-testid="horizon-short"[^>]*aria-pressed="true"/)
    })
})

describe('ChatCopilotPanel direction localization (DAV-914)', () => {
    describe('formatAnalysisCompleteMessage', () => {
        it('localizes legacy English directions to Chinese labels', () => {
            const testCases = [
                { raw: 'BULLISH', expected: '看多' },
                { raw: 'LEAN_BULLISH', expected: '偏多' },
                { raw: 'BEARISH', expected: '看空' },
                { raw: 'LEAN_BEARISH', expected: '偏空' },
                { raw: 'NEUTRAL', expected: '中性' },
                { raw: 'CAUTIOUS', expected: '谨慎' },
                { raw: 'BULL', expected: '看多' },
                { raw: 'BEAR', expected: '看空' },
                { raw: 'N/A', expected: '不适用' },
                { raw: 'NA', expected: '不适用' },
            ]

            for (const tc of testCases) {
                const msg = formatAnalysisCompleteMessage(tc.raw, 'BUY')
                expect(msg).toContain(`方向倾向：**${tc.expected}**`)
                expect(msg).toContain('执行动作：**BUY**')
                expect(msg).not.toContain(`**${tc.raw}**`)
            }
        })

        it('handles case-insensitivity for legacy English directions', () => {
            expect(formatAnalysisCompleteMessage('bullish', 'BUY')).toContain('方向倾向：**看多**')
            expect(formatAnalysisCompleteMessage('Lean_Bearish', 'SELL')).toContain('方向倾向：**偏空**')
            expect(formatAnalysisCompleteMessage('Cautious', 'HOLD')).toContain('方向倾向：**谨慎**')
        })

        it('renders current Chinese directions unchanged', () => {
            const chineseCases = ['看多', '偏多', '中性', '偏空', '看空', '谨慎', '增持', '减持']
            for (const dir of chineseCases) {
                const msg = formatAnalysisCompleteMessage(dir, 'HOLD')
                expect(msg).toContain(`方向倾向：**${dir}**`)
            }
        })

        it('falls back to "未知" when direction is null, undefined, or empty string', () => {
            expect(formatAnalysisCompleteMessage(null, 'BUY')).toContain('方向倾向：**未知**')
            expect(formatAnalysisCompleteMessage(undefined, 'SELL')).toContain('方向倾向：**未知**')
            expect(formatAnalysisCompleteMessage('', 'HOLD')).toContain('方向倾向：**未知**')
        })

        it('falls back to "HOLD" when decision is missing or empty', () => {
            expect(formatAnalysisCompleteMessage('BULLISH', null)).toContain('执行动作：**HOLD**')
            expect(formatAnalysisCompleteMessage('BULLISH', undefined)).toContain('执行动作：**HOLD**')
            expect(formatAnalysisCompleteMessage('BULLISH', '')).toContain('执行动作：**HOLD**')
        })

        it('passes through unknown direction values', () => {
            const msg = formatAnalysisCompleteMessage('CUSTOM_SIGNAL', 'BUY')
            expect(msg).toContain('方向倾向：**CUSTOM_SIGNAL**')
        })

        it('does not mutate input parameters (display layer only)', () => {
            const rawDir = 'BULLISH'
            const rawDecision = 'BUY'
            const msg = formatAnalysisCompleteMessage(rawDir, rawDecision)
            expect(rawDir).toBe('BULLISH')
            expect(rawDecision).toBe('BUY')
            expect(msg).toContain('方向倾向：**看多**')
            expect(msg).toContain('执行动作：**BUY**')
        })
    })

    describe('formatAnalysisRecoveryMessage', () => {
        it('localizes legacy English directions for recovered jobs', () => {
            const testCases = [
                { raw: 'BULLISH', expected: '看多' },
                { raw: 'LEAN_BEARISH', expected: '偏空' },
                { raw: 'NEUTRAL', expected: '中性' },
            ]

            for (const tc of testCases) {
                const msg = formatAnalysisRecoveryMessage(tc.raw, 'BUY')
                expect(msg).toContain('**分析完成（已从中断连接恢复）**')
                expect(msg).toContain(`方向倾向：**${tc.expected}**`)
                expect(msg).toContain('执行动作：**BUY**')
                expect(msg).not.toContain(`**${tc.raw}**`)
            }
        })

        it('renders current Chinese directions unchanged for recovered jobs', () => {
            const msg = formatAnalysisRecoveryMessage('看多', 'BUY')
            expect(msg).toContain('**分析完成（已从中断连接恢复）**')
            expect(msg).toContain('方向倾向：**看多**')
        })

        it('falls back to "未知" and "HOLD" when values are absent', () => {
            const msg = formatAnalysisRecoveryMessage(null, null)
            expect(msg).toContain('**分析完成（已从中断连接恢复）**')
            expect(msg).toContain('方向倾向：**未知**')
            expect(msg).toContain('执行动作：**HOLD**')
        })

        it('passes through unknown direction values', () => {
            const msg = formatAnalysisRecoveryMessage('UNKNOWN_DIR', 'HOLD')
            expect(msg).toContain('方向倾向：**UNKNOWN_DIR**')
        })
    })

    describe('formatAnalysisNotificationBody', () => {
        it('localizes legacy English directions in notification body', () => {
            expect(formatAnalysisNotificationBody('BULLISH', 'BUY')).toBe('方向：看多 · 动作：BUY')
            expect(formatAnalysisNotificationBody('LEAN_BEARISH', 'SELL')).toBe('方向：偏空 · 动作：SELL')
            expect(formatAnalysisNotificationBody('NEUTRAL', 'HOLD')).toBe('方向：中性 · 动作：HOLD')
            expect(formatAnalysisNotificationBody('CAUTIOUS', 'HOLD')).toBe('方向：谨慎 · 动作：HOLD')
        })

        it('passes through current Chinese directions in notification body', () => {
            expect(formatAnalysisNotificationBody('看多', 'BUY')).toBe('方向：看多 · 动作：BUY')
            expect(formatAnalysisNotificationBody('看空', 'SELL')).toBe('方向：看空 · 动作：SELL')
        })

        it('falls back to "点击查看完整报告" when direction is null, empty, or undefined', () => {
            expect(formatAnalysisNotificationBody(null, 'BUY')).toBe('点击查看完整报告')
            expect(formatAnalysisNotificationBody(undefined, 'SELL')).toBe('点击查看完整报告')
            expect(formatAnalysisNotificationBody('', 'HOLD')).toBe('点击查看完整报告')
        })

        it('defaults decision to "HOLD" when decision is missing but direction is present', () => {
            expect(formatAnalysisNotificationBody('BULLISH', null)).toBe('方向：看多 · 动作：HOLD')
            expect(formatAnalysisNotificationBody('BULLISH', undefined)).toBe('方向：看多 · 动作：HOLD')
            expect(formatAnalysisNotificationBody('BULLISH', '')).toBe('方向：看多 · 动作：HOLD')
        })

        it('passes through unknown direction values', () => {
            expect(formatAnalysisNotificationBody('CUSTOM_SIGNAL', 'BUY')).toBe('方向：CUSTOM_SIGNAL · 动作：BUY')
        })
    })
})
