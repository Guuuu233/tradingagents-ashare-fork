import { describe, expect, it } from 'vitest'

import {
    buildWaitDowngradeExplanation,
    substituteUpstreamBlockedPlaceholder,
} from '@/utils/reportText'
import { REPORT_SECTIONS } from '@/utils/markdownExport'
import { formatAnalysisCompleteMessage } from '@/components/ChatCopilotPanel'

describe('game_theory_report section wiring (DAV-1271 G2)', () => {
    it('REPORT_SECTIONS includes game_theory_report with the Chinese title', () => {
        const section = REPORT_SECTIONS.find(s => s.key === 'game_theory_report')
        expect(section).toBeTruthy()
        expect(section?.title).toBe('博弈论与对手盘分析')
        expect(section?.team).toBe('分析团队')
    })
})

describe('buildWaitDowngradeExplanation (DAV-1271 W1)', () => {
    const partialBearish = {
        analysis_status: 'VALID',
        trade_action: 'WAIT',
        direction: '偏空',
        confirmation_state: 'PARTIAL',
        reason_codes: ['inv2_rejected', 'inv4_rejected'],
    }

    it('explains the downgrade when VALID + WAIT + bearish lean + PARTIAL', () => {
        const text = buildWaitDowngradeExplanation(partialBearish)
        expect(text).toBe('研究团队倾向看空，但核心论据只部分核实（部分确认），按规则不给出买卖指令，执行动作为观望。')
    })

    it('explains the downgrade for bullish leans too', () => {
        const text = buildWaitDowngradeExplanation({ ...partialBearish, direction: '偏多' })
        expect(text).toContain('研究团队倾向看多')
        expect(text).toContain('执行动作为观望')
    })

    it('localizes legacy English directions', () => {
        const text = buildWaitDowngradeExplanation({ ...partialBearish, direction: 'LEAN_BEARISH' })
        expect(text).toContain('倾向看空')
    })

    it('says 尚未核实 only when confirmation_state is UNRESOLVED', () => {
        const text = buildWaitDowngradeExplanation({ ...partialBearish, confirmation_state: 'UNRESOLVED' })
        expect(text).toContain('核心论据尚未核实')
        expect(text).not.toContain('部分确认')
    })

    it('stays neutral when confirmation_state is CONFIRMED — no verification claim', () => {
        const text = buildWaitDowngradeExplanation({ ...partialBearish, confirmation_state: 'CONFIRMED' })
        expect(text).toBe('研究团队倾向看空，但本次执行动作为观望（WAIT），未给出买卖指令。')
        expect(text).not.toContain('未核实')
        expect(text).not.toContain('未完全核实')
        expect(text).not.toContain('部分确认')
    })

    it('stays neutral when confirmation_state is missing or unknown', () => {
        for (const conf of [undefined, null, '', 'SOMETHING_ELSE']) {
            const text = buildWaitDowngradeExplanation({ ...partialBearish, confirmation_state: conf as string | undefined })
            expect(text).toBe('研究团队倾向看空，但本次执行动作为观望（WAIT），未给出买卖指令。')
            expect(text).not.toContain('未核实')
            expect(text).not.toContain('未完全核实')
        }
    })

    it('produces a short variant for placeholder cards', () => {
        const text = buildWaitDowngradeExplanation(partialBearish, { short: true })
        expect(text).toBe('研究团队倾向看空，但核心论据只部分确认，按规则保持观望。')
    })

    it('short variant: UNRESOLVED vs CONFIRMED wording split', () => {
        expect(buildWaitDowngradeExplanation({ ...partialBearish, confirmation_state: 'UNRESOLVED' }, { short: true }))
            .toBe('研究团队倾向看空，但核心论据尚未确认，按规则保持观望。')
        const neutral = buildWaitDowngradeExplanation({ ...partialBearish, confirmation_state: 'CONFIRMED' }, { short: true })
        expect(neutral).toBe('研究团队倾向看空，但本次执行动作为观望（WAIT）。')
        expect(neutral).not.toContain('未核实')
        expect(neutral).not.toContain('未确认')
    })

    it.each([
        { analysis_status: 'INVALID_RUN' },
        { analysis_status: 'PARTIAL' },
        { analysis_status: 'VALID', trade_action: 'BUY' },
        { analysis_status: 'VALID', trade_action: 'HOLD' },
        { analysis_status: 'VALID', trade_action: 'WAIT', direction: '中性' },
        { analysis_status: 'VALID', trade_action: 'WAIT', direction: null },
        { analysis_status: 'VALID', trade_action: 'WAIT', direction: 'N/A' },
        null,
        undefined,
    ])('does not trigger outside VALID+WAIT+directional: %j', (ctx) => {
        const base = { direction: '偏空', trade_action: 'WAIT', analysis_status: 'VALID' }
        const merged = ctx && typeof ctx === 'object' ? { ...base, ...ctx } : ctx
        expect(buildWaitDowngradeExplanation(merged as never)).toBeNull()
    })

    it('returns null for an empty context object', () => {
        expect(buildWaitDowngradeExplanation({})).toBeNull()
    })
})

describe('substituteUpstreamBlockedPlaceholder (DAV-1271 W1)', () => {
    const ctx = {
        analysis_status: 'VALID',
        trade_action: 'WAIT',
        direction: '偏空',
        confirmation_state: 'PARTIAL',
    }
    const placeholder = '上游决策状态为 VALID/WAIT：不得生成方向性交易计划；保持 NO_TRADE / 观望，禁止输出目标价、止损或仓位。'

    it('replaces trader placeholder text with the short explanation', () => {
        expect(substituteUpstreamBlockedPlaceholder('trader_investment_plan', placeholder, ctx))
            .toBe('研究团队倾向看空，但核心论据只部分确认，按规则保持观望。')
    })

    it('CONFIRMED + WAIT + 偏空 placeholder shows neutral wording — never claims 未核实', () => {
        const out = substituteUpstreamBlockedPlaceholder('trader_investment_plan', placeholder, {
            ...ctx,
            confirmation_state: 'CONFIRMED',
        })
        expect(out).toBe('研究团队倾向看空，但本次执行动作为观望（WAIT）。')
        expect(out).not.toContain('未核实')
        expect(out).not.toContain('未完全核实')
        expect(out).not.toContain('上游决策状态为')
    })

    it('replaces risk/final-decision placeholder text too', () => {
        const risk = '上游决策状态为 VALID/WAIT：风险层不得批准任何方向性交易；最终动作保持 NO_TRADE。'
        const out = substituteUpstreamBlockedPlaceholder('final_trade_decision', risk, ctx)
        expect(out).toContain('观望')
        expect(out).not.toContain('上游决策状态为')
    })

    it('leaves non-placeholder sections untouched', () => {
        expect(substituteUpstreamBlockedPlaceholder('market_report', placeholder, ctx)).toBe(placeholder)
    })

    it('leaves real directional plans untouched', () => {
        const real = '买入计划：目标价 12.3，止损 10.1'
        expect(substituteUpstreamBlockedPlaceholder('trader_investment_plan', real, ctx)).toBe(real)
    })

    it('keeps the original placeholder when no downgrade context matches', () => {
        expect(substituteUpstreamBlockedPlaceholder('trader_investment_plan', placeholder, {
            analysis_status: 'INVALID_RUN', trade_action: 'NO_TRADE', direction: 'N/A',
        })).toBe(placeholder)
    })

    it('handles empty content', () => {
        expect(substituteUpstreamBlockedPlaceholder('trader_investment_plan', '', ctx)).toBe('')
        expect(substituteUpstreamBlockedPlaceholder('trader_investment_plan', null, ctx)).toBe('')
    })
})

describe('formatAnalysisCompleteMessage W1 note (DAV-1271)', () => {
    it('appends the downgrade explanation when ctx triggers', () => {
        const msg = formatAnalysisCompleteMessage('偏空', 'WAIT', {
            analysis_status: 'VALID',
            trade_action: 'WAIT',
            direction: '偏空',
            confirmation_state: 'PARTIAL',
        })
        expect(msg).toContain('方向倾向：**偏空**')
        expect(msg).toContain('执行动作：**WAIT**')
        expect(msg).toContain('说明：研究团队倾向看空，但核心论据只部分核实（部分确认），按规则不给出买卖指令，执行动作为观望。')
    })

    it('does not append anything without a matching ctx', () => {
        const msg = formatAnalysisCompleteMessage('偏多', 'BUY')
        expect(msg).not.toContain('说明：')
        const msg2 = formatAnalysisCompleteMessage('偏空', 'WAIT', {
            analysis_status: 'VALID',
            trade_action: 'BUY',
            direction: '偏空',
        })
        expect(msg2).not.toContain('说明：')
    })
})
