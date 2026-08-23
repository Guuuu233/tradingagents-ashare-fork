import { describe, expect, it } from 'vitest'

import {
    REPORT_EXPORT_SECTIONS,
    REPORT_SECTIONS,
    buildReportMarkdown,
} from '@/utils/markdownExport'

describe('REPORT_SECTIONS and REPORT_EXPORT_SECTIONS', () => {
    it('shares identical definition between viewer and export sections', () => {
        expect(REPORT_EXPORT_SECTIONS).toEqual(REPORT_SECTIONS)
    })

    it('contains all 7 analyst section keys in analysis team', () => {
        const analystKeys = [
            'market_report',
            'sentiment_report',
            'news_report',
            'fundamentals_report',
            'macro_report',
            'smart_money_report',
            'volume_price_report',
        ]

        const currentKeys = REPORT_EXPORT_SECTIONS.map((s) => s.key)

        for (const key of analystKeys) {
            expect(currentKeys).toContain(key)
        }

        // Verify analysis team metadata
        const analysisSections = REPORT_SECTIONS.filter((s) => s.team === '分析团队')
        expect(analysisSections.map((s) => s.key)).toEqual(analystKeys)
    })

    it('contains all 3 team decision keys', () => {
        const teamKeys = [
            'investment_plan',
            'trader_investment_plan',
            'final_trade_decision',
        ]

        const currentKeys = REPORT_EXPORT_SECTIONS.map((s) => s.key)

        for (const key of teamKeys) {
            expect(currentKeys).toContain(key)
        }
    })

    it('maintains the exact 10-section sequence: 7 analysts -> 3 team plans', () => {
        const expectedOrder = [
            'market_report',
            'sentiment_report',
            'news_report',
            'fundamentals_report',
            'macro_report',
            'smart_money_report',
            'volume_price_report',
            'investment_plan',
            'trader_investment_plan',
            'final_trade_decision',
        ]

        expect(REPORT_EXPORT_SECTIONS.map((s) => s.key)).toEqual(expectedOrder)
        expect(REPORT_EXPORT_SECTIONS).toHaveLength(10)
    })
})

describe('buildReportMarkdown', () => {
    it('builds full markdown including all 7 analyst sections and 3 team sections', () => {
        const mockReport = {
            market_report: '市场分析正文',
            sentiment_report: '舆情分析正文',
            news_report: '新闻分析正文',
            fundamentals_report: '基本面分析正文',
            macro_report: '宏观板块分析正文',
            smart_money_report: '主力资金分析正文',
            volume_price_report: '量价分析正文',
            investment_plan: '研究团队决策正文',
            trader_investment_plan: '交易团队计划正文',
            final_trade_decision: '最终交易决策正文',
        }

        const md = buildReportMarkdown(mockReport, REPORT_EXPORT_SECTIONS)

        expect(md).toContain('## 市场分析报告\n\n市场分析正文')
        expect(md).toContain('## 舆情分析报告\n\n舆情分析正文')
        expect(md).toContain('## 新闻分析报告\n\n新闻分析正文')
        expect(md).toContain('## 基本面分析报告\n\n基本面分析正文')
        expect(md).toContain('## 宏观板块报告\n\n宏观板块分析正文')
        expect(md).toContain('## 主力资金报告\n\n主力资金分析正文')
        expect(md).toContain('## 量价分析报告\n\n量价分析正文')
        expect(md).toContain('## 研究团队决策\n\n研究团队决策正文')
        expect(md).toContain('## 交易团队计划\n\n交易团队计划正文')
        expect(md).toContain('## 最终交易决策\n\n最终交易决策正文')
    })

    it('skips missing or empty sections and appends footer if provided', () => {
        const partialReport = {
            macro_report: '宏观板块分析正文',
            final_trade_decision: '最终交易决策正文',
            market_report: '',
        }

        const footer = '> 免责声明：测试声明'
        const md = buildReportMarkdown(partialReport, REPORT_EXPORT_SECTIONS, footer)

        expect(md).toContain('## 宏观板块报告\n\n宏观板块分析正文')
        expect(md).toContain('## 最终交易决策\n\n最终交易决策正文')
        expect(md).not.toContain('## 市场分析报告')
        expect(md.endsWith(footer)).toBe(true)
    })

    it('returns empty string for null, undefined, or non-object reports', () => {
        expect(buildReportMarkdown(null, REPORT_EXPORT_SECTIONS)).toBe('')
        expect(buildReportMarkdown(undefined, REPORT_EXPORT_SECTIONS)).toBe('')
        expect(buildReportMarkdown('invalid', REPORT_EXPORT_SECTIONS)).toBe('')
    })
})
