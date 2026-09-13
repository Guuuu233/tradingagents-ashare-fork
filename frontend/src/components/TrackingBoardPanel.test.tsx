import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import { DetailedTrackingRow } from '@/components/TrackingBoardPanel'
import type { TrackingBoardAnalysis, TrackingBoardItem } from '@/types'

describe('DetailedTrackingRow direction localization and business logic (P1-C)', () => {
    function createTestItem(analysis?: TrackingBoardAnalysis | null): TrackingBoardItem {
        return {
            symbol: '600519.SH',
            name: '贵州茅台',
            current_position: 100,
            available_position: 100,
            average_cost: 1600,
            market_value: 165000,
            live_price: 1650,
            price_change_pct: 1.5,
            floating_pnl: 5000,
            floating_pnl_pct: 3.12,
            analysis,
        }
    }

    it('localizes legacy English directions (BULLISH, LEAN_BEARISH, NEUTRAL, CAUTIOUS) to Chinese', () => {
        const cases = [
            { raw: 'BULLISH', expected: '看多' },
            { raw: 'LEAN_BULLISH', expected: '偏多' },
            { raw: 'BEARISH', expected: '看空' },
            { raw: 'LEAN_BEARISH', expected: '偏空' },
            { raw: 'NEUTRAL', expected: '中性' },
            { raw: 'CAUTIOUS', expected: '谨慎' },
        ]

        for (const tc of cases) {
            const item = createTestItem({
                report_id: 'rep-1',
                trade_date: '2026-09-01',
                is_previous_trade_day: false,
                direction: tc.raw,
            })

            const html = renderToStaticMarkup(
                <DetailedTrackingRow item={item} onAnalyze={() => {}} onOpenReport={() => {}} />,
            )

            expect(html).toContain(tc.expected)
            expect(html).not.toContain(`>${tc.raw}<`)
        }
    })

    it('renders current Chinese directions as-is', () => {
        const chineseDirections = ['看多', '偏多', '中性', '偏空', '看空', '增持', '减持']

        for (const dir of chineseDirections) {
            const item = createTestItem({
                report_id: 'rep-2',
                trade_date: '2026-09-01',
                is_previous_trade_day: false,
                direction: dir,
            })

            const html = renderToStaticMarkup(
                <DetailedTrackingRow item={item} onAnalyze={() => {}} onOpenReport={() => {}} />,
            )

            expect(html).toContain(dir)
        }
    })

    it('falls back to analysis.decision when direction is null or empty', () => {
        const itemWithDecision = createTestItem({
            report_id: 'rep-3',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            direction: null,
            decision: 'BUY',
        })

        const html = renderToStaticMarkup(
            <DetailedTrackingRow item={itemWithDecision} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )

        expect(html).toContain('BUY')
    })

    it('falls back to "待定" when both direction and decision are absent', () => {
        const itemEmpty = createTestItem({
            report_id: 'rep-4',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            direction: null,
            decision: null,
        })

        const html = renderToStaticMarkup(
            <DetailedTrackingRow item={itemEmpty} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )

        expect(html).toContain('待定')
    })

    it('renders unknown direction values as-is', () => {
        const itemUnknown = createTestItem({
            report_id: 'rep-5',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            direction: 'CUSTOM_DIR',
        })

        const html = renderToStaticMarkup(
            <DetailedTrackingRow item={itemUnknown} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )

        expect(html).toContain('CUSTOM_DIR')
    })

    it('preserves business judgment for decisionToneClass regardless of display localization', () => {
        // 1. Chinese direction '增持' triggers rose tone (buy/add)
        const itemAdd = createTestItem({
            report_id: 'rep-10',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            direction: '增持',
        })
        const htmlAdd = renderToStaticMarkup(
            <DetailedTrackingRow item={itemAdd} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )
        expect(htmlAdd).toMatch(/<span class="[^"]*bg-rose-50[^"]*">增持<\/span>/)

        // 2. Chinese direction '减持' triggers emerald tone (sell/reduce)
        const itemReduce = createTestItem({
            report_id: 'rep-11',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            direction: '减持',
        })
        const htmlReduce = renderToStaticMarkup(
            <DetailedTrackingRow item={itemReduce} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )
        expect(htmlReduce).toMatch(/<span class="[^"]*bg-emerald-50[^"]*">减持<\/span>/)

        // 3. English decision 'BUY' triggers rose tone, even with legacy direction 'BULLISH'
        const itemBuyBullish = createTestItem({
            report_id: 'rep-12',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            decision: 'BUY',
            direction: 'BULLISH',
        })
        const htmlBuyBullish = renderToStaticMarkup(
            <DetailedTrackingRow item={itemBuyBullish} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )
        expect(htmlBuyBullish).toMatch(/<span class="[^"]*bg-rose-50[^"]*">看多<\/span>/)

        // 4. English decision 'SELL' triggers emerald tone, even with legacy direction 'BEARISH'
        const itemSellBearish = createTestItem({
            report_id: 'rep-13',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            decision: 'SELL',
            direction: 'BEARISH',
        })
        const htmlSellBearish = renderToStaticMarkup(
            <DetailedTrackingRow item={itemSellBearish} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )
        expect(htmlSellBearish).toMatch(/<span class="[^"]*bg-emerald-50[^"]*">看空<\/span>/)

        // 5. Decision 'HOLD' with direction 'BULLISH':
        // Display renders '看多', but tone remains default slate (not rose),
        // proving display localization does NOT change the business tone logic.
        const itemHoldBullish = createTestItem({
            report_id: 'rep-14',
            trade_date: '2026-09-01',
            is_previous_trade_day: false,
            decision: 'HOLD',
            direction: 'BULLISH',
        })
        const htmlHoldBullish = renderToStaticMarkup(
            <DetailedTrackingRow item={itemHoldBullish} onAnalyze={() => {}} onOpenReport={() => {}} />,
        )
        expect(htmlHoldBullish).toMatch(/<span class="[^"]*bg-slate-100[^"]*">看多<\/span>/)
        expect(htmlHoldBullish).not.toMatch(/<span class="[^"]*bg-rose-50[^"]*">看多<\/span>/)
        expect(htmlHoldBullish).not.toMatch(/<span class="[^"]*bg-emerald-50[^"]*">看多<\/span>/)
    })
})
