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

describe('DecisionCard dual-horizon rendering (DAV-1301)', () => {
    const dual = [
        {
            horizon: 'short',
            status: 'completed',
            analysis_status: 'VALID',
            direction: '看空',
            trade_action: 'SELL',
            manager_action: 'SELL',
            confidence: 70,
            target_price: 55.0,
            stop_loss_price: 53.3,
        },
        {
            horizon: 'medium',
            status: 'completed',
            analysis_status: 'VALID',
            direction: '看多',
            trade_action: 'NO_TRADE',
            manager_action: 'BUY',
            reason_codes: ['price_basis_gate_blocked'],
            gate_blocked: true,
            non_executable: true,
        },
    ]

    it('renders one row per horizon with its own action and direction', () => {
        const html = renderToStaticMarkup(
            <DecisionCard
                symbol="600276.SH"
                name="恒瑞医药"
                decision="sell"
                direction="看空"
                horizonDecisions={dual}
            />,
        )
        expect(html).toContain('短线')
        expect(html).toContain('卖出（看空）')
        expect(html).toContain('中线')
        expect(html).toContain('不交易（看多）')
    })

    it('marks the gate-downgraded horizon with its original manager action', () => {
        const html = renderToStaticMarkup(
            <DecisionCard symbol="600276.SH" horizonDecisions={dual} />,
        )
        expect(html).toContain('研究经理原结论买入')
        expect(html).toContain('降级为不交易')
    })

    it('hangs numerics under the horizon row, never on the card top level', () => {
        const html = renderToStaticMarkup(
            <DecisionCard
                symbol="600276.SH"
                confidence={99}
                targetPrice={999}
                stopLoss={111}
                horizonDecisions={dual}
            />,
        )
        // Top-level numerics suppressed in dual mode; the short horizon keeps
        // its own values inside its row.
        expect(html).not.toContain('99%')
        expect(html).not.toContain('999')
        expect(html).toContain('置信度 70%')
        expect(html).toContain('目标价 ¥55')
        expect(html).toContain('止损价 ¥53.3')
    })

    it('renders 未完成 for a failed horizon', () => {
        const html = renderToStaticMarkup(
            <DecisionCard
                symbol="600276.SH"
                horizonDecisions={[dual[0], { horizon: 'medium', status: 'failed' }]}
            />,
        )
        expect(html).toContain('短线')
        expect(html).toContain('卖出（看空）')
        expect(html).toContain('中线')
        expect(html).toContain('未完成')
    })
})
