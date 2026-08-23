export interface MarkdownSection {
    key: string
    title: string
    team?: string
}

export const REPORT_SECTIONS: MarkdownSection[] = [
    { key: 'market_report', title: '市场分析报告', team: '分析团队' },
    { key: 'sentiment_report', title: '舆情分析报告', team: '分析团队' },
    { key: 'news_report', title: '新闻分析报告', team: '分析团队' },
    { key: 'fundamentals_report', title: '基本面分析报告', team: '分析团队' },
    { key: 'macro_report', title: '宏观板块报告', team: '分析团队' },
    { key: 'smart_money_report', title: '主力资金报告', team: '分析团队' },
    { key: 'volume_price_report', title: '量价分析报告', team: '分析团队' },
    { key: 'investment_plan', title: '研究团队决策', team: '研究团队' },
    { key: 'trader_investment_plan', title: '交易团队计划', team: '交易团队' },
    { key: 'final_trade_decision', title: '最终交易决策', team: '组合管理' },
]

export const REPORT_EXPORT_SECTIONS: MarkdownSection[] = REPORT_SECTIONS

/**
 * Build a markdown document from the string sections of a report. Callers pass
 * their own section list so historical vs live report shapes can differ.
 */
export function buildReportMarkdown(
    report: unknown,
    sections: MarkdownSection[],
    footer?: string,
): string {
    if (!report || typeof report !== 'object') return ''
    const record = report as Record<string, unknown>
    const parts: string[] = []
    for (const section of sections) {
        const content = record[section.key]
        if (typeof content === 'string' && content.length > 0) {
            parts.push(`## ${section.title}\n\n${content}`)
        }
    }
    if (footer) parts.push(footer)
    return parts.join('\n\n---\n\n')
}

/** Trigger a browser download of a markdown string. */
export function downloadMarkdown(filename: string, markdown: string): void {
    const blob = new Blob([markdown], { type: 'text/markdown' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = filename
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
}
