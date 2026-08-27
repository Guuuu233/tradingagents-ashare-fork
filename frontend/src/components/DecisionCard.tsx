import { useState } from 'react'
import { TrendingUp, TrendingDown, Target, Shield, ChevronDown, ChevronUp, Info, Ban, AlertTriangle } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { AnalysisReport } from '@/types'
import {
    sanitizeReportMarkdown,
    localizeDirection,
    parseDecisionAction,
    type DecisionAction,
} from '@/utils/reportText'

interface DecisionCardProps {
    symbol: string
    name?: string
    decision?: DecisionAction
    direction?: string
    confidence?: number | null
    targetPrice?: number | null
    targetChange?: number
    stopLoss?: number | null
    stopLossChange?: number
    reasoning?: string
    riskLevel?: 'low' | 'medium' | 'high'
    analysisStatus?: string | null
    tradeAction?: string | null
    report?: AnalysisReport
}

const decisionConfig: Record<DecisionAction, { label: string; color: string; icon: typeof TrendingUp }> = {
    buy: { label: '买入', color: 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-500/30', icon: TrendingUp },
    sell: { label: '卖出', color: 'bg-green-100 dark:bg-green-500/20 text-green-700 dark:text-green-400 border-green-200 dark:border-green-500/30', icon: TrendingDown },
    hold: { label: '持有', color: 'bg-blue-100 dark:bg-blue-500/20 text-blue-700 dark:text-blue-400 border-blue-200 dark:border-blue-500/30', icon: Shield },
    add: { label: '增持', color: 'bg-red-100 dark:bg-red-500/20 text-red-700 dark:text-red-400 border-red-200 dark:border-red-500/30', icon: TrendingUp },
    reduce: { label: '减持', color: 'bg-orange-100 dark:bg-orange-500/20 text-orange-700 dark:text-orange-400 border-orange-200 dark:border-orange-500/30', icon: TrendingDown },
    watch: { label: '观望', color: 'bg-slate-100 dark:bg-slate-700/50 text-slate-700 dark:text-slate-400 border-slate-200 dark:border-slate-600', icon: Info },
    no_trade: { label: '不交易', color: 'bg-amber-100 dark:bg-amber-500/20 text-amber-800 dark:text-amber-300 border-amber-200 dark:border-amber-500/30', icon: Ban },
    invalid: { label: '无效运行', color: 'bg-rose-100 dark:bg-rose-500/20 text-rose-800 dark:text-rose-300 border-rose-200 dark:border-rose-500/30', icon: AlertTriangle },
}

function resolveDecision(
    propDecision: DecisionAction | undefined,
    tradeAction: string | null | undefined,
    analysisStatus: string | null | undefined,
    report: AnalysisReport | undefined,
): DecisionAction | undefined {
    const status = (analysisStatus || report?.analysis_status || '').toString().toUpperCase()
    if (status === 'INVALID_RUN' || status === 'DATA_ERROR') return 'invalid'
    if (status === 'ABSTAIN') return 'no_trade'
    if (status === 'PARTIAL') return 'watch'
    const fromTrade = parseDecisionAction(tradeAction || report?.trade_action || null)
    if (fromTrade) return fromTrade
    if (propDecision) return propDecision
    return parseDecisionAction(report?.decision || report?.final_trade_decision)
}

export default function DecisionCard({
    symbol,
    name = symbol,
    decision: propDecision,
    direction,
    confidence,
    targetPrice,
    targetChange,
    stopLoss,
    stopLossChange,
    reasoning,
    riskLevel,
    analysisStatus,
    tradeAction,
    report,
}: DecisionCardProps) {
    const [expanded, setExpanded] = useState(false)

    const decision = resolveDecision(propDecision, tradeAction, analysisStatus, report)
    const config = decision ? decisionConfig[decision] : null
    const DecisionIcon = config?.icon
    const nonExecutable = decision === 'invalid' || decision === 'no_trade' || decision === 'watch'
    const showPrices = !nonExecutable && (targetPrice != null || stopLoss != null)
    const showConfidence = confidence != null && !nonExecutable

    const riskLabels: Record<string, string> = { low: '低', medium: '中等', high: '高' }
    const riskColors: Record<string, string> = {
        low: 'text-green-600 dark:text-green-400',
        medium: 'text-yellow-600 dark:text-yellow-400',
        high: 'text-red-600 dark:text-red-400',
    }

    return (
        <div className="card overflow-hidden" data-testid="decision-card" data-decision={decision || ''}>
            {/* 头部 */}
            <div className="flex items-center justify-between mb-4">
                <div className="flex items-center gap-3">
                    <div className="w-10 h-10 rounded-xl bg-gradient-to-br from-blue-500 to-cyan-400 flex items-center justify-center">
                        <TrendingUp className="w-5 h-5 text-white" />
                    </div>
                    <div>
                        <h3 className="font-semibold text-slate-900 dark:text-slate-100">{name}</h3>
                        <p className="text-sm text-slate-500">{symbol}</p>
                        {direction && (
                            <p className="text-xs text-slate-400 mt-0.5">方向：{localizeDirection(direction)}</p>
                        )}
                    </div>
                </div>
                {config && DecisionIcon ? (
                    <div className={`px-4 py-2 rounded-full border font-medium flex items-center gap-1.5 ${config.color}`}>
                        <DecisionIcon className="w-4 h-4" />
                        {config.label}
                    </div>
                ) : (
                    <div className="px-4 py-2 rounded-full border font-medium text-slate-400 border-slate-200 dark:border-slate-700">
                        等待裁决
                    </div>
                )}
            </div>

            {/* 置信度 */}
            {showConfidence && (
                <div className="mb-4">
                    <div className="flex items-center justify-between mb-2">
                        <span className="text-sm text-slate-500">置信度</span>
                        <span className="text-sm font-medium text-slate-700 dark:text-slate-300">{confidence}%</span>
                    </div>
                    <div className="h-2 bg-slate-200 dark:bg-slate-700 rounded-full overflow-hidden">
                        <div
                            className="h-full bg-gradient-to-r from-blue-500 to-cyan-400 rounded-full transition-all duration-1000"
                            style={{ width: `${confidence}%` }}
                        />
                    </div>
                </div>
            )}

            {/* 目标价和止损价 */}
            {showPrices ? (
            <div className="grid grid-cols-2 gap-3 mb-4">
                <div className="p-3 rounded-xl bg-red-50 dark:bg-red-500/10 border border-red-100 dark:border-red-500/20">
                    <div className="flex items-center gap-1.5 mb-1">
                        <Target className="w-4 h-4 text-red-600 dark:text-red-400" />
                        <span className="text-xs text-slate-500">目标价</span>
                    </div>
                    <p className="text-xl font-bold text-red-600 dark:text-red-400">
                        {targetPrice != null ? `¥${targetPrice}` : '--'}
                    </p>
                    {targetChange != null && (
                        <p className="text-sm text-red-600 dark:text-red-400">
                            {targetChange >= 0 ? '+' : ''}{targetChange.toFixed(1)}%
                        </p>
                    )}
                </div>
                <div className="p-3 rounded-xl bg-green-50 dark:bg-green-500/10 border border-green-100 dark:border-green-500/20">
                    <div className="flex items-center gap-1.5 mb-1">
                        <Shield className="w-4 h-4 text-green-600 dark:text-green-400" />
                        <span className="text-xs text-slate-500">止损价</span>
                    </div>
                    <p className="text-xl font-bold text-green-600 dark:text-green-400">
                        {stopLoss != null ? `¥${stopLoss}` : '--'}
                    </p>
                    {stopLossChange != null && (
                        <p className="text-sm text-green-600 dark:text-green-400">
                            {stopLossChange >= 0 ? '+' : ''}{stopLossChange.toFixed(1)}%
                        </p>
                    )}
                </div>
            </div>
            ) : nonExecutable ? (
                <div className="mb-4 rounded-xl border border-amber-200 dark:border-amber-500/30 bg-amber-50 dark:bg-amber-500/10 px-3 py-2 text-sm text-amber-800 dark:text-amber-200">
                    非可执行状态：不展示目标价 / 止损 / 置信度交易参数。
                </div>
            ) : null}

            {riskLevel && (
                <div className="mb-4 text-sm">
                    风险等级：
                    <span className={`font-medium ${riskColors[riskLevel] || ''}`}>
                        {riskLabels[riskLevel] || riskLevel}
                    </span>
                </div>
            )}

            {reasoning && (
                <div className="border-t border-slate-100 dark:border-slate-800 pt-3">
                    <button
                        type="button"
                        onClick={() => setExpanded((v) => !v)}
                        className="flex items-center gap-1 text-sm text-slate-500 hover:text-slate-700 dark:hover:text-slate-300"
                    >
                        {expanded ? <ChevronUp className="w-4 h-4" /> : <ChevronDown className="w-4 h-4" />}
                        裁决摘要
                    </button>
                    {expanded && (
                        <div className="mt-2 prose prose-sm dark:prose-invert max-w-none text-slate-600 dark:text-slate-300">
                            <ReactMarkdown remarkPlugins={[remarkGfm]}>
                                {sanitizeReportMarkdown(reasoning)}
                            </ReactMarkdown>
                        </div>
                    )}
                </div>
            )}
        </div>
    )
}
