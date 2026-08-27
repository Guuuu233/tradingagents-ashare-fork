import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it } from 'vitest'

import DecisionCard from '@/components/DecisionCard'
import { parseDecisionAction } from '@/utils/reportText'

describe('parseDecisionAction status vocabulary', () => {
    it('maps NO_TRADE / WAIT / INVALID without collapsing to hold', () => {
        expect(parseDecisionAction('NO_TRADE')).toBe('no_trade')
        expect(parseDecisionAction('WAIT')).toBe('watch')
        expect(parseDecisionAction('INVALID_RUN')).toBe('invalid')
        expect(parseDecisionAction('ABSTAIN')).toBe('no_trade')
        expect(parseDecisionAction('BUY')).toBe('buy')
        expect(parseDecisionAction('HOLD')).toBe('hold')
    })
})

describe('DecisionCard production status rendering', () => {
    it('renders 无效运行 for INVALID_RUN and does not show 持有', () => {
        const html = renderToStaticMarkup(
            <DecisionCard
                symbol="300433.SZ"
                name="蓝思科技"
                analysisStatus="INVALID_RUN"
                tradeAction="NO_TRADE"
                direction="N/A"
                confidence={25}
                targetPrice={28.6}
                stopLoss={24.8}
                reasoning="分析报告生成失败"
            />,
        )
        expect(html).toContain('无效运行')
        expect(html).not.toContain('>持有<')
        expect(html).toContain('非可执行状态')
        expect(html).toContain('data-decision="invalid"')
    })

    it('renders 不交易 for ABSTAIN/NO_TRADE', () => {
        const html = renderToStaticMarkup(
            <DecisionCard
                symbol="300433.SZ"
                analysisStatus="ABSTAIN"
                tradeAction="NO_TRADE"
                direction="N/A"
            />,
        )
        expect(html).toContain('不交易')
        expect(html).toContain('data-decision="no_trade"')
        expect(html).not.toContain('>持有<')
    })

    it('renders 观望 for WAIT without falling back to hold', () => {
        const html = renderToStaticMarkup(
            <DecisionCard
                symbol="600519.SH"
                decision="watch"
                tradeAction="WAIT"
            />,
        )
        expect(html).toContain('观望')
        expect(html).toContain('data-decision="watch"')
    })

    it('still renders 买入 for VALID BUY', () => {
        const html = renderToStaticMarkup(
            <DecisionCard
                symbol="600519.SH"
                analysisStatus="VALID"
                tradeAction="BUY"
                decision="buy"
                confidence={70}
                targetPrice={10}
            />,
        )
        expect(html).toContain('买入')
        expect(html).toContain('data-decision="buy"')
        expect(html).toContain('70%')
    })
})
