import React, { useState, useEffect, useCallback, useMemo } from 'react'
import {
    X,
    Scale,
    ShieldAlert,
    CheckCircle2,
    AlertTriangle,
    XCircle,
    FileText,
    ChevronDown,
    ChevronRight,
    Search,
    ShieldCheck,
    Layers,
    Sparkles,
    AlertOctagon,
    Target,
    Database,
    Swords,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type {
    ReportDetail,
    InvestmentDebateState,
    HistoricalDebateRoundMessage,
    HistoricalDebateClaim,
    HistoricalDebateManagerVerdict,
    HistoricalDebateEvidenceVerification,
    HistoricalDebateAttempt,
    ProtocolVersion,
    DebateStage,
    Challenge,
    DisputeMapItem,
    ShadowCreditMetrics,
} from '@/types'

const MD_COMPONENTS = {
    table: ({ children }: { children?: React.ReactNode }) => (
        <table className="w-full border-collapse border border-slate-300 dark:border-slate-700 my-2 text-xs">{children}</table>
    ),
    thead: ({ children }: { children?: React.ReactNode }) => (
        <thead className="bg-slate-100 dark:bg-slate-800">{children}</thead>
    ),
    th: ({ children }: { children?: React.ReactNode }) => (
        <th className="border border-slate-300 dark:border-slate-700 px-2 py-1 text-left font-semibold text-slate-700 dark:text-slate-300">{children}</th>
    ),
    td: ({ children }: { children?: React.ReactNode }) => (
        <td className="border border-slate-300 dark:border-slate-700 px-2 py-1 text-slate-600 dark:text-slate-400">{children}</td>
    ),
    tr: ({ children }: { children?: React.ReactNode }) => (
        <tr className="even:bg-slate-50 dark:even:bg-slate-800/40">{children}</tr>
    ),
}

export const BATTLEFIELD_MAP: Record<string, { label: string; icon: string; bg: string; text: string; border: string }> = {
    capital_flow: { label: '资金筹码', icon: '💰', bg: 'bg-amber-500/15', text: 'text-amber-300', border: 'border-amber-500/30' },
    sentiment_theme: { label: '情绪题材', icon: '🔥', bg: 'bg-purple-500/15', text: 'text-purple-300', border: 'border-purple-500/30' },
    price_volume: { label: '量价技术', icon: '📈', bg: 'bg-blue-500/15', text: 'text-blue-300', border: 'border-blue-500/30' },
    macro_policy: { label: '宏观政策', icon: '🏛️', bg: 'bg-emerald-500/15', text: 'text-emerald-300', border: 'border-emerald-500/30' },
    fundamentals: { label: '基本面', icon: '📊', bg: 'bg-cyan-500/15', text: 'text-cyan-300', border: 'border-cyan-500/30' },
}

export function getBattlefieldDisplay(bf?: string | null) {
    if (!bf) return null
    const key = bf.toLowerCase().trim()
    const config = BATTLEFIELD_MAP[key]
    if (config) {
        return {
            key,
            label: config.label,
            fullLabel: `${config.label} (${key})`,
            icon: config.icon,
            bg: config.bg,
            text: config.text,
            border: config.border,
        }
    }
    return {
        key,
        label: bf,
        fullLabel: bf,
        icon: '⚔️',
        bg: 'bg-slate-800',
        text: 'text-slate-300',
        border: 'border-slate-700',
    }
}

/**
 * Hard Gate Check: unsupported / contradicted fatal challenges MUST NOT be marked as penetrated.
 */
export function isChallengePenetrated(
    challenge: Challenge,
    adoptedChallengeIds?: string[],
): boolean {
    const severity = (challenge.severity || '').toLowerCase().trim()
    const isFatal = severity === 'fatal'
    if (!isFatal) return false

    const evStatus = (challenge.evidence_status || '').toLowerCase().trim()
    // HARD GATE: unsupported / contradicted fatal challenge MUST NOT show as "已击穿"
    if (evStatus === 'unsupported' || evStatus === 'contradicted' || evStatus === 'source_unavailable') {
        return false
    }

    const isVerified = evStatus === 'verified'
    const isAdopted = challenge.status === 'adopted' || (adoptedChallengeIds && challenge.challenge_id ? adoptedChallengeIds.includes(challenge.challenge_id) : false)

    return isFatal && isVerified && isAdopted
}

export interface HistoricalDebateDrawerProps {
    isOpen: boolean
    onClose: () => void
    reportData?: ReportDetail | null
}

export function extractDebateState(
    reportData?: ReportDetail | null,
    horizon?: 'short' | 'medium' | 'default',
): {
    debateState: InvestmentDebateState | null
    managerVerdict: HistoricalDebateManagerVerdict | null
    evidenceVerification: HistoricalDebateEvidenceVerification[]
    protocolVersion: ProtocolVersion
    protocolStage: DebateStage
    tiebreakSkipped: boolean
    debateDegenerate: boolean
    challenges: Challenge[]
    disputeMap: DisputeMapItem[]
    challengeVerification: HistoricalDebateEvidenceVerification[]
    shadowCreditMetrics: ShadowCreditMetrics | null
    hasStructuredDebate: boolean
    hasLegacyHistory: boolean
} {
    if (!reportData) {
        return {
            debateState: null,
            managerVerdict: null,
            evidenceVerification: [],
            protocolVersion: 'v1_legacy',
            protocolStage: 'opening',
            tiebreakSkipped: false,
            debateDegenerate: false,
            challenges: [],
            disputeMap: [],
            challengeVerification: [],
            shadowCreditMetrics: null,
            hasStructuredDebate: false,
            hasLegacyHistory: false,
        }
    }

    const resData = reportData.result_data

    let selectedState: InvestmentDebateState | undefined
    let selectedVerdict: HistoricalDebateManagerVerdict | null | undefined
    let selectedEvidence: HistoricalDebateEvidenceVerification[] | undefined

    if (horizon === 'short') {
        selectedState = resData?.short_term?.investment_debate_state
        selectedVerdict = resData?.short_term?.manager_verdict
        selectedEvidence = resData?.short_term?.evidence_verification
    } else if (horizon === 'medium') {
        selectedState = resData?.medium_term?.investment_debate_state
        selectedVerdict = resData?.medium_term?.manager_verdict
        selectedEvidence = resData?.medium_term?.evidence_verification
    }

    const debateState = selectedState
        || resData?.investment_debate_state
        || reportData.investment_debate_state
        || resData?.short_term?.investment_debate_state
        || resData?.medium_term?.investment_debate_state
        || null

    const managerVerdict = selectedVerdict
        || debateState?.manager_verdict
        || resData?.manager_verdict
        || reportData.manager_verdict
        || null

    const evidenceVerification = selectedEvidence
        || debateState?.evidence_verification
        || resData?.evidence_verification
        || reportData.evidence_verification
        || []

    const protocolVersion: ProtocolVersion =
        debateState?.protocol_version
        || resData?.protocol_version
        || reportData.protocol_version
        || (debateState?.round_messages && debateState.round_messages.some(m => m.stage || (m.challenges && m.challenges.length > 0)) ? 'v2_structured_disagreement' : 'v1_legacy')

    const protocolStage: DebateStage =
        debateState?.protocol_stage
        || resData?.protocol_stage
        || reportData.protocol_stage
        || 'opening'

    const tiebreakSkipped = Boolean(
        debateState?.tiebreak_skipped
        ?? resData?.tiebreak_skipped
        ?? reportData.tiebreak_skipped
        ?? false
    )

    const debateDegenerate = Boolean(
        debateState?.debate_degenerate
        ?? resData?.debate_degenerate
        ?? reportData.debate_degenerate
        ?? false
    )

    // Collect challenges from root or messages
    const messageChallenges = debateState?.round_messages?.flatMap(m => m.challenges || []) || []
    const rawChallenges = debateState?.challenges || resData?.challenges || reportData.challenges || []
    const challenges: Challenge[] = rawChallenges.length > 0 ? rawChallenges : messageChallenges

    // Dispute map from manager verdict or state
    const disputeMap: DisputeMapItem[] =
        managerVerdict?.dispute_map
        || debateState?.dispute_map
        || resData?.dispute_map
        || reportData.dispute_map
        || []

    const challengeVerification: HistoricalDebateEvidenceVerification[] =
        debateState?.challenge_verification
        || resData?.challenge_verification
        || evidenceVerification.filter(e => Boolean(e.challenge_id))
        || []

    const shadowCreditMetrics: ShadowCreditMetrics | null =
        debateState?.shadow_credit_metrics
        || resData?.shadow_credit_metrics
        || null

    const hasStructuredDebate = Boolean(
        (debateState?.round_messages && debateState.round_messages.length > 0)
        || (debateState?.claims && debateState.claims.length > 0)
        || (challenges && challenges.length > 0)
        || (disputeMap && disputeMap.length > 0)
        || (debateState?.attempts && debateState.attempts.length > 0)
        || (managerVerdict && (managerVerdict.winner || managerVerdict.direction || managerVerdict.consistency_check_passed !== undefined))
        || (evidenceVerification && evidenceVerification.length > 0)
        || protocolVersion === 'v2_structured_disagreement'
    )

    const hasLegacyHistory = Boolean(
        debateState?.bull_history
        || debateState?.bear_history
        || debateState?.history
        || debateState?.judge_decision
    )

    return {
        debateState,
        managerVerdict,
        evidenceVerification,
        protocolVersion,
        protocolStage,
        tiebreakSkipped,
        debateDegenerate,
        challenges,
        disputeMap,
        challengeVerification,
        shadowCreditMetrics,
        hasStructuredDebate,
        hasLegacyHistory,
    }
}

export default function HistoricalDebateDrawer({
    isOpen,
    onClose,
    reportData,
}: HistoricalDebateDrawerProps) {
    const [activeTab, setActiveTab] = useState<'timeline' | 'claims' | 'verdict' | 'evidence' | 'legacy'>('timeline')
    const [selectedHorizon, setSelectedHorizon] = useState<'short' | 'medium' | 'default'>('default')
    const [expandedMessages, setExpandedMessages] = useState<Record<string, boolean>>({})
    const [expandedAttempts, setExpandedAttempts] = useState<Record<string, boolean>>({})
    const [claimSearch, setClaimSearch] = useState('')
    const [claimFilterStance, setClaimFilterStance] = useState<'all' | 'bull' | 'bear'>('all')
    const [claimFilterStatus, setClaimFilterStatus] = useState<string>('all')
    const [evidenceFilterStatus, setEvidenceFilterStatus] = useState<string>('all')

    // Detect if dual horizons exist in result_data
    const resData = reportData?.result_data
    const hasDualHorizon = Boolean(resData?.short_term || resData?.medium_term)

    useEffect(() => {
        if (hasDualHorizon) {
            if (resData?.short_term?.investment_debate_state) {
                setSelectedHorizon('short')
            } else if (resData?.medium_term?.investment_debate_state) {
                setSelectedHorizon('medium')
            }
        } else {
            setSelectedHorizon('default')
        }
    }, [hasDualHorizon, resData])

    // Escape key to close
    const handleKeyDown = useCallback((e: KeyboardEvent) => {
        if (e.key === 'Escape') onClose()
    }, [onClose])

    useEffect(() => {
        if (isOpen) {
            document.addEventListener('keydown', handleKeyDown)
            return () => document.removeEventListener('keydown', handleKeyDown)
        }
    }, [isOpen, handleKeyDown])

    const {
        debateState,
        managerVerdict,
        evidenceVerification,
        protocolVersion,
        protocolStage,
        tiebreakSkipped,
        debateDegenerate,
        challenges,
        disputeMap,
        hasStructuredDebate,
        hasLegacyHistory,
    } = useMemo(() => {
        return extractDebateState(reportData, selectedHorizon)
    }, [reportData, selectedHorizon])

    const isV2 = protocolVersion === 'v2_structured_disagreement'

    const roundMessages: HistoricalDebateRoundMessage[] = useMemo(() => {
        return debateState?.round_messages || []
    }, [debateState])

    const claims: HistoricalDebateClaim[] = useMemo(() => {
        return debateState?.claims || []
    }, [debateState])

    const attempts: HistoricalDebateAttempt[] = useMemo(() => {
        return debateState?.attempts || []
    }, [debateState])

    // Toggle expand for a message
    const toggleMessageExpand = (key: string) => {
        setExpandedMessages(prev => ({ ...prev, [key]: !prev[key] }))
    }

    // Toggle expand for attempt details
    const toggleAttemptExpand = (key: string) => {
        setExpandedAttempts(prev => ({ ...prev, [key]: !prev[key] }))
    }

    // Expand / Collapse all messages
    const toggleExpandAllMessages = (expand: boolean) => {
        const next: Record<string, boolean> = {}
        roundMessages.forEach((msg, idx) => {
            const key = `msg-${msg.message_index ?? idx}`
            next[key] = expand
        })
        setExpandedMessages(next)
    }

    // Claims filtering
    const filteredClaims = useMemo(() => {
        return claims.filter(c => {
            const text = `${c.claim_id} ${c.claim || c.text || c.content || ''} ${Array.isArray(c.evidence) ? c.evidence.join(' ') : c.evidence || ''} ${c.battlefield || ''}`.toLowerCase()
            if (claimSearch && !text.includes(claimSearch.toLowerCase())) {
                return false
            }
            if (claimFilterStance !== 'all') {
                const stance = (c.stance || c.speaker_key || c.speaker || '').toLowerCase()
                if (claimFilterStance === 'bull' && !stance.includes('bull') && !stance.includes('多')) return false
                if (claimFilterStance === 'bear' && !stance.includes('bear') && !stance.includes('空')) return false
            }
            if (claimFilterStatus !== 'all') {
                if ((c.status || '').toLowerCase() !== claimFilterStatus.toLowerCase()) return false
            }
            return true
        })
    }, [claims, claimSearch, claimFilterStance, claimFilterStatus])

    // Evidence filtering
    const filteredEvidence = useMemo(() => {
        return evidenceVerification.filter(item => {
            if (evidenceFilterStatus === 'all') return true
            if (evidenceFilterStatus === 'fatal') return item.is_fatal === true
            return (item.status || '').toLowerCase() === evidenceFilterStatus.toLowerCase()
        })
    }, [evidenceVerification, evidenceFilterStatus])

    // Evidence stats
    const evidenceStats = useMemo(() => {
        let verified = 0
        let unsupported = 0
        let contradicted = 0
        let sourceUnavailable = 0
        let fatal = 0

        evidenceVerification.forEach(item => {
            if (item.is_fatal) fatal += 1
            const st = (item.status || '').toLowerCase()
            if (st === 'verified') verified += 1
            else if (st === 'unsupported') unsupported += 1
            else if (st === 'contradicted') contradicted += 1
            else if (st === 'source_unavailable') sourceUnavailable += 1
        })

        return {
            total: evidenceVerification.length,
            verified,
            unsupported,
            contradicted,
            sourceUnavailable,
            fatal,
        }
    }, [evidenceVerification])

    if (!isOpen) return null

    return (
        <>
            {/* Backdrop */}
            <div
                className="fixed inset-0 bg-black/50 backdrop-blur-xs z-40 animate-in fade-in duration-200"
                onClick={onClose}
                aria-hidden="true"
            />

            {/* Drawer */}
            <div
                className="fixed top-0 right-0 h-full w-full max-w-[880px] md:w-4/5 lg:w-3/4 dark bg-slate-900 text-slate-100 border-l border-slate-700 shadow-2xl z-50 flex flex-col animate-in slide-in-from-right duration-300"
                role="dialog"
                aria-label="多空辩论与裁决证据"
            >
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-slate-800 bg-slate-900/90 backdrop-blur-sm shrink-0">
                    <div className="flex items-center gap-3">
                        <div className="p-2 rounded-xl bg-amber-500/10 border border-amber-500/20 text-amber-400">
                            <Scale className="w-5 h-5" />
                        </div>
                        <div>
                            <div className="flex items-center gap-2 flex-wrap">
                                <h2 className="text-lg font-bold text-white tracking-tight">多空辩论与裁决证据</h2>
                                <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700">
                                    {reportData?.symbol || '历史审计'}
                                </span>

                                {/* Protocol Version Badge */}
                                {isV2 ? (
                                    <span
                                        className="inline-flex items-center gap-1 text-[11px] px-2.5 py-0.5 rounded-full bg-indigo-500/20 text-indigo-300 border border-indigo-500/40 font-mono font-semibold"
                                        title="v2 结构化非共识协议"
                                    >
                                        <Sparkles className="w-3 h-3 text-indigo-400" />
                                        <span>v2_structured_disagreement</span>
                                    </span>
                                ) : (
                                    <span
                                        className="inline-flex items-center gap-1 text-[11px] px-2.5 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono"
                                        title="v1 传统辩论协议"
                                    >
                                        <span>v1_legacy</span>
                                    </span>
                                )}

                                {/* Degenerate Badge */}
                                {debateDegenerate && (
                                    <span
                                        className="inline-flex items-center gap-1 text-[11px] px-2.5 py-0.5 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 font-semibold animate-pulse"
                                        title="多空双方信念概率未发生实质位移"
                                    >
                                        <AlertTriangle className="w-3 h-3 text-amber-400" />
                                        <span>⚠️ 辩论退化</span>
                                    </span>
                                )}
                            </div>
                            <p className="text-xs text-slate-400 mt-0.5">
                                逐轮多空对抗 · 论点账本 · 研究总监裁决 · 事实确定性核验
                            </p>
                        </div>
                    </div>
                    <div className="flex items-center gap-2">
                        {hasDualHorizon && (
                            <div className="flex items-center bg-slate-800/80 p-0.5 rounded-lg border border-slate-700">
                                <button
                                    onClick={() => setSelectedHorizon('short')}
                                    className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                                        selectedHorizon === 'short'
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'text-slate-400 hover:text-slate-200'
                                    }`}
                                >
                                    短线
                                </button>
                                <button
                                    onClick={() => setSelectedHorizon('medium')}
                                    className={`px-2.5 py-1 text-xs font-medium rounded-md transition-colors ${
                                        selectedHorizon === 'medium'
                                            ? 'bg-blue-600 text-white shadow-sm'
                                            : 'text-slate-400 hover:text-slate-200'
                                    }`}
                                >
                                    中线
                                </button>
                            </div>
                        )}
                        <button
                            onClick={onClose}
                            className="p-1.5 rounded-lg hover:bg-slate-800 text-slate-400 hover:text-white transition-colors"
                            aria-label="关闭抽屉"
                        >
                            <X className="w-5 h-5" />
                        </button>
                    </div>
                </div>

                {/* Tabs Navigation */}
                {hasStructuredDebate ? (
                    <div className="flex items-center gap-2 px-6 pt-3 pb-2 border-b border-slate-800 bg-slate-900/60 overflow-x-auto shrink-0">
                        <button
                            onClick={() => setActiveTab('timeline')}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                                activeTab === 'timeline'
                                    ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                            }`}
                        >
                            <Layers className="w-3.5 h-3.5" />
                            <span>逐轮辩论</span>
                            {roundMessages.length > 0 && (
                                <span className="ml-1 px-1.5 py-0.2 rounded-full bg-slate-800 text-[10px]">
                                    {roundMessages.length}
                                </span>
                            )}
                        </button>
                        <button
                            onClick={() => setActiveTab('claims')}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                                activeTab === 'claims'
                                    ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                            }`}
                        >
                            <Database className="w-3.5 h-3.5" />
                            <span>论点账本 (Claims)</span>
                            {(claims.length > 0 || challenges.length > 0) && (
                                <span className="ml-1 px-1.5 py-0.2 rounded-full bg-slate-800 text-[10px]">
                                    {claims.length + challenges.length}
                                </span>
                            )}
                        </button>
                        <button
                            onClick={() => setActiveTab('verdict')}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                                activeTab === 'verdict'
                                    ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                            }`}
                        >
                            <Target className="w-3.5 h-3.5" />
                            <span>总监裁决</span>
                            {managerVerdict && (
                                <span className={`ml-1 w-2 h-2 rounded-full ${
                                    managerVerdict.consistency_check_passed === false ? 'bg-rose-500' : 'bg-emerald-500'
                                }`} />
                            )}
                        </button>
                        <button
                            onClick={() => setActiveTab('evidence')}
                            className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                                activeTab === 'evidence'
                                    ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                                    : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                            }`}
                        >
                            <ShieldCheck className="w-3.5 h-3.5" />
                            <span>事实核验</span>
                            {evidenceStats.total > 0 && (
                                <span className={`ml-1 px-1.5 py-0.2 rounded-full text-[10px] ${
                                    evidenceStats.fatal > 0 ? 'bg-rose-500/20 text-rose-300 font-bold' : 'bg-slate-800 text-slate-300'
                                }`}>
                                    {evidenceStats.fatal > 0 ? `🚨 ${evidenceStats.fatal}` : evidenceStats.total}
                                </span>
                            )}
                        </button>
                        {hasLegacyHistory && (
                            <button
                                onClick={() => setActiveTab('legacy')}
                                className={`flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium transition-all ${
                                    activeTab === 'legacy'
                                        ? 'bg-blue-500/20 text-blue-400 border border-blue-500/30'
                                        : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                                }`}
                            >
                                <FileText className="w-3.5 h-3.5" />
                                <span>文本归档</span>
                            </button>
                        )}
                    </div>
                ) : null}

                {/* Content Body */}
                <div className="flex-1 min-h-0 overflow-y-auto px-6 py-5">
                    {!hasStructuredDebate ? (
                        /* Graceful Fallback for Legacy Reports without structured debate */
                        <div className="space-y-6">
                            <div className="rounded-2xl border border-amber-500/30 bg-amber-500/10 p-6 text-center">
                                <AlertTriangle className="w-10 h-10 text-amber-400 mx-auto mb-3" />
                                <h3 className="text-base font-bold text-amber-300">
                                    此报告生成时尚未记录结构化辩论
                                </h3>
                                <p className="text-xs text-slate-400 mt-2 max-w-md mx-auto leading-relaxed">
                                    当前报告属于历史版本。系统自 P1 协议起开始持久化逐轮多空对抗、Claims 账本、总监结构化裁决与事实核验硬闸。
                                </p>
                            </div>

                            {hasLegacyHistory && (
                                <div className="space-y-4">
                                    <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                                        历史文本记录
                                    </h4>
                                    {debateState?.bull_history && (
                                        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
                                            <div className="flex items-center gap-2 mb-2 text-emerald-400 font-semibold text-sm">
                                                <span>🐂</span> 多头观点记录
                                            </div>
                                            <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                    {debateState.bull_history}
                                                </ReactMarkdown>
                                            </div>
                                        </div>
                                    )}
                                    {debateState?.bear_history && (
                                        <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-4">
                                            <div className="flex items-center gap-2 mb-2 text-rose-400 font-semibold text-sm">
                                                <span>🐻</span> 空头观点记录
                                            </div>
                                            <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                    {debateState.bear_history}
                                                </ReactMarkdown>
                                            </div>
                                        </div>
                                    )}
                                    {debateState?.judge_decision && (
                                        <div className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-4">
                                            <div className="flex items-center gap-2 mb-2 text-blue-400 font-semibold text-sm">
                                                <span>🏛️</span> 裁判裁决记录
                                            </div>
                                            <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                    {debateState.judge_decision}
                                                </ReactMarkdown>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </div>
                    ) : (
                        <>
                            {/* TAB 1: 逐轮辩论 TIMELINE */}
                            {activeTab === 'timeline' && (
                                <div className="space-y-4">
                                    {/* Degenerate Alert Banner if applicable */}
                                    {debateDegenerate && (
                                        <div className="rounded-xl border border-amber-500/40 bg-amber-950/30 p-4 flex items-start gap-3 shadow-sm">
                                            <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                                            <div>
                                                <div className="flex items-center gap-2">
                                                    <h4 className="text-xs font-bold text-amber-300">辩论退化警告 (Debate Degenerate)</h4>
                                                    <span className="text-[10px] px-2 py-0.2 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 font-mono font-bold">
                                                        静止退化
                                                    </span>
                                                </div>
                                                <p className="text-xs text-slate-300 mt-1 leading-relaxed">
                                                    多空双方在辩论过程中自评胜率未随反方证据调整，信念轨迹保持静止。
                                                </p>
                                            </div>
                                        </div>
                                    )}

                                    {/* Tiebreak Status Banner */}
                                    {tiebreakSkipped ? (
                                        <div className="rounded-xl border border-blue-500/30 bg-blue-950/20 p-3.5 flex items-center gap-3">
                                            <CheckCircle2 className="w-4 h-4 text-blue-400 shrink-0" />
                                            <div className="flex-1">
                                                <div className="flex items-center gap-2">
                                                    <span className="text-xs font-bold text-blue-300">加赛裁决状态 (Tiebreak)</span>
                                                    <span className="text-[10px] px-2 py-0.2 rounded bg-blue-500/20 text-blue-300 border border-blue-500/40 font-mono">
                                                        已跳过
                                                    </span>
                                                </div>
                                                <p className="text-xs text-slate-300 mt-0.5">
                                                    证据足以裁决，未触发加赛
                                                </p>
                                            </div>
                                        </div>
                                    ) : (
                                        roundMessages.some(m => m.stage === 'tiebreak' || m.debate_round === 3) && (
                                            <div className="rounded-xl border border-purple-500/30 bg-purple-950/20 p-3.5 flex items-center gap-3">
                                                <Sparkles className="w-4 h-4 text-purple-400 shrink-0" />
                                                <div className="flex-1">
                                                    <div className="flex items-center gap-2">
                                                        <span className="text-xs font-bold text-purple-300">已执行加赛裁决 (Executed Tiebreak)</span>
                                                        <span className="text-[10px] px-2 py-0.2 rounded bg-purple-500/20 text-purple-300 border border-purple-500/40 font-mono">
                                                            加赛阶段
                                                        </span>
                                                    </div>
                                                    <p className="text-xs text-slate-300 mt-0.5">
                                                        核心分歧点势均力敌，已执行深度加赛问答并记录胜率调整。
                                                    </p>
                                                </div>
                                            </div>
                                        )
                                    )}

                                    <div className="flex items-center justify-between pb-1">
                                        <div className="text-xs text-slate-400">
                                            共 {roundMessages.length} 次正式发言 · 协议阶段: <span className="font-mono text-blue-400">{protocolStage}</span>
                                        </div>
                                        <div className="flex items-center gap-2">
                                            <button
                                                onClick={() => toggleExpandAllMessages(true)}
                                                className="text-xs px-2.5 py-1 rounded bg-slate-800 text-slate-300 hover:bg-slate-700 transition-colors"
                                            >
                                                展开全部
                                            </button>
                                            <button
                                                onClick={() => toggleExpandAllMessages(false)}
                                                className="text-xs px-2.5 py-1 rounded bg-slate-800 text-slate-300 hover:bg-slate-700 transition-colors"
                                            >
                                                折叠全部
                                            </button>
                                        </div>
                                    </div>

                                    {roundMessages.length === 0 ? (
                                        <div className="text-center py-12 text-slate-500 text-sm">
                                            暂无逐轮发言记录
                                        </div>
                                    ) : (
                                        <div className="space-y-4">
                                            {roundMessages.map((msg, idx) => {
                                                const key = `msg-${msg.message_index ?? idx}`
                                                const isExpanded = expandedMessages[key] ?? true // default expanded for rich view or toggleable
                                                const isBull = (msg.speaker_key || msg.speaker || '').toLowerCase().includes('bull') || (msg.speaker || '').includes('多')
                                                const speakerEmoji = isBull ? '🐂' : '🐻'
                                                const speakerLabel = isBull ? '多头研究员' : '空头研究员'
                                                const borderTheme = isBull
                                                    ? 'border-emerald-500/30 hover:border-emerald-500/50'
                                                    : 'border-rose-500/30 hover:border-rose-500/50'
                                                const bgTheme = isBull ? 'bg-emerald-950/20' : 'bg-rose-950/20'
                                                const badgeText = isBull ? 'text-emerald-400' : 'text-rose-400'

                                                // Stage & battlefield
                                                const stage = msg.stage || (msg.debate_round === 1 ? 'opening' : msg.debate_round === 2 ? 'challenge' : msg.debate_round === 3 ? 'tiebreak' : undefined)
                                                const bfDisplay = getBattlefieldDisplay(msg.battlefield)

                                                // Information gain formatting
                                                const infoScore = typeof msg.information_gain_score === 'number'
                                                    ? msg.information_gain_score
                                                    : null
                                                const newEvCount = msg.new_evidence_count ?? 0
                                                const parseStatus = msg.parse_status || 'valid'
                                                const isProtocolValid = parseStatus === 'valid'

                                                // Collect opening claims for this message
                                                const msgOpeningClaims = claims.filter(c =>
                                                    (msg.new_claim_ids && msg.new_claim_ids.includes(c.claim_id)) ||
                                                    (stage === 'opening' && (c.debate_round === msg.debate_round || c.round_index === msg.debate_round) && ((isBull && (c.stance === 'bullish' || c.speaker_key === 'Bull')) || (!isBull && (c.stance === 'bearish' || c.speaker_key === 'Bear'))))
                                                )

                                                // Collect challenges inside this message or associated
                                                const msgChallenges = msg.challenges || challenges.filter(c =>
                                                    c.message_index === msg.message_index ||
                                                    (c.debate_round === msg.debate_round && ((isBull && (c.speaker_key === 'Bull' || c.speaker?.includes('多'))) || (!isBull && (c.speaker_key === 'Bear' || c.speaker?.includes('空')))))
                                                )

                                                // Collect unaccepted attempts for this message
                                                const msgAttempts: HistoricalDebateAttempt[] = [
                                                    ...(msg.attempts || []),
                                                    ...attempts.filter(a =>
                                                        a.accepted === false &&
                                                        (a.message_index === msg.message_index || (a.debate_round === msg.debate_round && a.speaker_key === msg.speaker_key))
                                                    ),
                                                ]
                                                // Deduplicate attempts by error_detail or cleaned_prose
                                                const uniqueAttempts = msgAttempts.filter((att, aIdx, self) =>
                                                    aIdx === self.findIndex(t => (t.error_detail === att.error_detail && t.cleaned_prose === att.cleaned_prose))
                                                )

                                                return (
                                                    <div
                                                        key={key}
                                                        className={`rounded-xl border ${borderTheme} ${bgTheme} transition-all duration-200 overflow-hidden shadow-sm`}
                                                    >
                                                        {/* Header / Summary row */}
                                                        <div
                                                            onClick={() => toggleMessageExpand(key)}
                                                            className="flex flex-col md:flex-row md:items-center justify-between p-4 cursor-pointer hover:bg-slate-800/40 gap-3"
                                                        >
                                                            <div className="flex items-center gap-3">
                                                                <span className="text-xl shrink-0">{speakerEmoji}</span>
                                                                <div>
                                                                    <div className="flex items-center gap-2 flex-wrap">
                                                                        <span className={`font-bold text-sm ${badgeText}`}>
                                                                            {speakerLabel}
                                                                        </span>
                                                                        <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-300 border border-slate-700 font-mono">
                                                                            Round {msg.debate_round || Math.ceil((msg.message_index || 1) / 2)} · #{msg.message_index ?? idx + 1}
                                                                        </span>
                                                                        {stage && (
                                                                            <span className={`text-[11px] px-2 py-0.5 rounded-full font-mono uppercase ${
                                                                                stage === 'opening'
                                                                                    ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                                                                                    : stage === 'challenge'
                                                                                        ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
                                                                                        : 'bg-purple-500/15 text-purple-300 border border-purple-500/30'
                                                                            }`}>
                                                                                {stage === 'opening' ? '立论 (Opening)' : stage === 'challenge' ? '质询 (Challenge)' : '加赛 (Tiebreak)'}
                                                                            </span>
                                                                        )}
                                                                        {bfDisplay && (
                                                                            <span className={`text-[11px] px-2 py-0.5 rounded-full border ${bfDisplay.bg} ${bfDisplay.text} ${bfDisplay.border}`}>
                                                                                {bfDisplay.icon} {bfDisplay.label}
                                                                            </span>
                                                                        )}
                                                                        {isProtocolValid ? (
                                                                            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-emerald-500/15 text-emerald-400 border border-emerald-500/30">
                                                                                <CheckCircle2 className="w-3 h-3" />
                                                                                <span>协议合规</span>
                                                                            </span>
                                                                        ) : (
                                                                            <span className="inline-flex items-center gap-1 text-[11px] px-2 py-0.5 rounded-full bg-rose-500/15 text-rose-400 border border-rose-500/30">
                                                                                <XCircle className="w-3 h-3" />
                                                                                <span>{parseStatus}</span>
                                                                            </span>
                                                                        )}
                                                                    </div>
                                                                    {msg.round_summary && (
                                                                        <p className="text-xs text-slate-300 mt-1 line-clamp-1">
                                                                            {msg.round_summary}
                                                                        </p>
                                                                    )}
                                                                </div>
                                                            </div>

                                                            {/* Metrics chips */}
                                                            <div className="flex items-center gap-2 flex-wrap text-xs">
                                                                {infoScore !== null && (
                                                                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded border ${
                                                                        infoScore >= 0.5
                                                                            ? 'bg-emerald-500/10 text-emerald-300 border-emerald-500/30'
                                                                            : 'bg-amber-500/10 text-amber-300 border-amber-500/30'
                                                                    }`} title={`信息增量评分: ${infoScore}`}>
                                                                        <Sparkles className="w-3 h-3" />
                                                                        <span>增量: {(infoScore * 100).toFixed(0)}%</span>
                                                                    </span>
                                                                )}
                                                                {msg.self_win_prob !== undefined && msg.self_win_prob !== null && (
                                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-indigo-500/15 text-indigo-300 border border-indigo-500/30 font-mono">
                                                                        <span>自评胜率: {(Number(msg.self_win_prob) * 100).toFixed(0)}%</span>
                                                                    </span>
                                                                )}
                                                                {newEvCount > 0 && (
                                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-blue-500/10 text-blue-300 border border-blue-500/30">
                                                                        <span>新证据: {newEvCount}</span>
                                                                    </span>
                                                                )}
                                                                {msg.responded_claim_ids && msg.responded_claim_ids.length > 0 && (
                                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                                                                        <span>回应: {msg.responded_claim_ids.join(', ')}</span>
                                                                    </span>
                                                                )}
                                                                {msg.new_claim_ids && msg.new_claim_ids.length > 0 && (
                                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-amber-500/10 text-amber-300 border border-amber-500/30">
                                                                        <span>提出: {msg.new_claim_ids.join(', ')}</span>
                                                                    </span>
                                                                )}
                                                                {msgChallenges.length > 0 && (
                                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30 font-semibold">
                                                                        <Swords className="w-3 h-3 text-rose-400" />
                                                                        <span>盘问: {msgChallenges.length}</span>
                                                                    </span>
                                                                )}
                                                                {uniqueAttempts.length > 0 && (
                                                                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-rose-500/15 text-rose-300 border border-rose-500/30 font-medium" title={`该轮次有 ${uniqueAttempts.length} 次未采纳重试`}>
                                                                        <ShieldAlert className="w-3 h-3 text-rose-400" />
                                                                        <span>未采纳重试: {uniqueAttempts.length}</span>
                                                                    </span>
                                                                )}
                                                                {isExpanded ? (
                                                                    <ChevronDown className="w-4 h-4 text-slate-400 shrink-0" />
                                                                ) : (
                                                                    <ChevronRight className="w-4 h-4 text-slate-400 shrink-0" />
                                                                )}
                                                            </div>
                                                        </div>

                                                        {/* Expandable Body */}
                                                        {isExpanded && (
                                                            <div className="px-5 py-4 border-t border-slate-800/80 bg-slate-900/90 space-y-4">
                                                                {/* Metadata bar */}
                                                                <div className="flex flex-wrap gap-2 text-xs">
                                                                    {msg.target_claim_ids && msg.target_claim_ids.length > 0 && (
                                                                        <div className="px-2.5 py-1 rounded bg-slate-800/80 border border-slate-700 text-slate-300">
                                                                            <span className="text-slate-500 mr-1">针对论点:</span>
                                                                            <span className="font-mono text-blue-300">{msg.target_claim_ids.join(', ')}</span>
                                                                        </div>
                                                                    )}
                                                                    {msg.duplicate_claim_ids && msg.duplicate_claim_ids.length > 0 && (
                                                                        <div className="px-2.5 py-1 rounded bg-amber-950/40 border border-amber-500/30 text-amber-300">
                                                                            <span className="mr-1">⚠️ 相似/重复论点:</span>
                                                                            <span className="font-mono">{msg.duplicate_claim_ids.join(', ')}</span>
                                                                        </div>
                                                                    )}
                                                                    {msg.round_goal && (
                                                                        <div className="px-2.5 py-1 rounded bg-slate-800/80 border border-slate-700 text-slate-300">
                                                                            <span className="text-slate-500 mr-1">本轮目标:</span>
                                                                            <span>{msg.round_goal}</span>
                                                                        </div>
                                                                    )}
                                                                </div>

                                                                {/* Opening Claims inside this message */}
                                                                {msgOpeningClaims.length > 0 && (
                                                                    <div className="space-y-2.5 pt-1">
                                                                        <div className="flex items-center gap-2 text-xs font-bold text-emerald-300 uppercase tracking-wider">
                                                                            <Database className="w-3.5 h-3.5 text-emerald-400" />
                                                                            <span>立论论点与战场分布 ({msgOpeningClaims.length})</span>
                                                                        </div>
                                                                        <div className="space-y-2.5">
                                                                            {msgOpeningClaims.map((claim, clmIdx) => {
                                                                                const claimBf = getBattlefieldDisplay(claim.battlefield)
                                                                                const claimEvidence = Array.isArray(claim.evidence) ? claim.evidence : claim.evidence ? [claim.evidence] : []
                                                                                const confidence = typeof claim.confidence === 'number' ? claim.confidence : null

                                                                                return (
                                                                                    <div
                                                                                        key={`msg-op-claim-${claim.claim_id || clmIdx}`}
                                                                                        className="p-3.5 rounded-xl border border-slate-800 bg-slate-950/60 space-y-2 text-xs"
                                                                                    >
                                                                                        <div className="flex items-center justify-between gap-2 flex-wrap">
                                                                                            <div className="flex items-center gap-2 flex-wrap">
                                                                                                <span className="font-mono font-bold text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                                                                                                    {claim.claim_id}
                                                                                                </span>
                                                                                                {claimBf && (
                                                                                                    <span className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full border ${claimBf.bg} ${claimBf.text} ${claimBf.border} font-medium`}>
                                                                                                        <span>{claimBf.icon}</span>
                                                                                                        <span>{claimBf.label}</span>
                                                                                                    </span>
                                                                                                )}
                                                                                            </div>
                                                                                            {confidence !== null && (
                                                                                                <span className="text-slate-400 font-mono">
                                                                                                    置信度: {(confidence * 100).toFixed(0)}%
                                                                                                </span>
                                                                                            )}
                                                                                        </div>

                                                                                        <div className="text-slate-100 font-medium leading-relaxed">
                                                                                            {claim.claim || claim.text || claim.content || ''}
                                                                                        </div>

                                                                                        {claimEvidence.length > 0 && (
                                                                                            <div className="space-y-1 bg-slate-900/60 p-2 rounded-lg border border-slate-800/60 text-slate-300">
                                                                                                <span className="text-slate-500 text-[11px] block font-semibold">支撑证据:</span>
                                                                                                <ul className="space-y-0.5">
                                                                                                    {claimEvidence.map((ev, evIdx) => (
                                                                                                        <li key={`ev-${claim.claim_id}-${evIdx}`} className="flex items-start gap-1.5 text-slate-300">
                                                                                                            <span className="text-blue-400">•</span>
                                                                                                            <span>{String(ev)}</span>
                                                                                                        </li>
                                                                                                    ))}
                                                                                                </ul>
                                                                                            </div>
                                                                                        )}

                                                                                        {claim.falsification_conditions && (
                                                                                            <div className="space-y-1 bg-amber-950/20 p-2.5 rounded-lg border border-amber-500/30">
                                                                                                <span className="text-[11px] font-bold text-amber-400 flex items-center gap-1">
                                                                                                    <AlertTriangle className="w-3 h-3" /> 失效条件 (Falsification Conditions)
                                                                                                </span>
                                                                                                <div className="text-xs text-amber-200/90 leading-relaxed">
                                                                                                    {Array.isArray(claim.falsification_conditions)
                                                                                                        ? claim.falsification_conditions.join('；')
                                                                                                        : String(claim.falsification_conditions)}
                                                                                                </div>
                                                                                            </div>
                                                                                        )}
                                                                                    </div>
                                                                                )
                                                                            })}
                                                                        </div>
                                                                    </div>
                                                                )}

                                                                {/* Tiebreak Q&A Block if present */}
                                                                {msg.tiebreak_question && (
                                                                    <div className="bg-purple-950/40 p-3.5 rounded-xl border border-purple-500/30 space-y-2 text-xs">
                                                                        <div className="flex items-center gap-1.5 font-bold text-purple-300">
                                                                            <Sparkles className="w-3.5 h-3.5" />
                                                                            <span>加赛针对性问答 (Tiebreak Q&A)</span>
                                                                        </div>
                                                                        <div className="text-slate-300 bg-slate-900/60 p-2.5 rounded-lg border border-purple-500/20">
                                                                            <span className="text-purple-400 font-semibold mr-1.5">核心争议提问:</span>
                                                                            <span>{msg.tiebreak_question}</span>
                                                                        </div>
                                                                        {msg.tiebreak_answer && (
                                                                            <div className="text-slate-200 bg-slate-900/60 p-2.5 rounded-lg border border-emerald-500/20">
                                                                                <span className="text-emerald-400 font-semibold mr-1.5">回答与自洽论证:</span>
                                                                                <span>{msg.tiebreak_answer}</span>
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                )}

                                                                {/* Challenges inside this round */}
                                                                {msgChallenges.length > 0 && (
                                                                    <div className="space-y-2 pt-1">
                                                                        <div className="flex items-center gap-2 text-xs font-bold text-rose-300 uppercase tracking-wider">
                                                                            <Swords className="w-3.5 h-3.5 text-rose-400" />
                                                                            <span>本轮提出交叉盘问 ({msgChallenges.length})</span>
                                                                        </div>
                                                                        <div className="space-y-2">
                                                                            {msgChallenges.map((ch, chIdx) => {
                                                                                const isFatal = (ch.severity || '').toLowerCase() === 'fatal'
                                                                                const isMajor = (ch.severity || '').toLowerCase() === 'major'
                                                                                const evStatus = (ch.evidence_status || 'unverified').toLowerCase()
                                                                                const isAdopted = managerVerdict?.adopted_challenge_ids?.includes(ch.challenge_id || '') || ch.status === 'adopted'
                                                                                const isRejected = managerVerdict?.rejected_challenge_ids?.includes(ch.challenge_id || '') || ch.status === 'rejected'
                                                                                const penetrated = isChallengePenetrated(ch, managerVerdict?.adopted_challenge_ids)

                                                                                return (
                                                                                    <div
                                                                                        key={`msg-ch-${ch.challenge_id || chIdx}`}
                                                                                        className={`p-3.5 rounded-xl border ${
                                                                                            penetrated
                                                                                                ? 'border-rose-500 bg-rose-950/30 ring-1 ring-rose-500/50'
                                                                                                : 'border-slate-800 bg-slate-950/60'
                                                                                        } space-y-2 text-xs`}
                                                                                    >
                                                                                        <div className="flex items-center justify-between gap-2 flex-wrap">
                                                                                            <div className="flex items-center gap-2 flex-wrap">
                                                                                                <span className="font-mono font-bold text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                                                                                                    {ch.challenge_id || `CH-${chIdx + 1}`}
                                                                                                </span>
                                                                                                <span className="text-slate-400">
                                                                                                    攻击对手: <span className="font-mono text-blue-300 font-bold">{ch.target_claim_id || '-'}</span>
                                                                                                </span>
                                                                                                <span className={`px-2 py-0.5 rounded font-mono font-bold uppercase ${
                                                                                                    isFatal
                                                                                                        ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                                                                                                        : isMajor
                                                                                                            ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                                                                                                            : 'bg-slate-800 text-slate-300 border border-slate-700'
                                                                                                }`}>
                                                                                                    {ch.severity || 'major'}
                                                                                                </span>
                                                                                            </div>

                                                                                            <div className="flex items-center gap-2 flex-wrap">
                                                                                                {/* Evidence status */}
                                                                                                <span className={`px-2 py-0.5 rounded-full font-medium ${
                                                                                                    evStatus === 'verified'
                                                                                                        ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                                                                                                        : evStatus === 'contradicted'
                                                                                                            ? 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                                                                                                            : evStatus === 'unsupported'
                                                                                                                ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
                                                                                                                : 'bg-slate-800 text-slate-400 border border-slate-700'
                                                                                                }`}>
                                                                                                    证据: {evStatus}
                                                                                                </span>

                                                                                                {/* Manager adoption */}
                                                                                                {isAdopted ? (
                                                                                                    <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-semibold">
                                                                                                        总监采纳
                                                                                                    </span>
                                                                                                ) : isRejected ? (
                                                                                                    <span className="px-2 py-0.5 rounded bg-rose-500/15 text-rose-400 border border-rose-500/30 font-semibold">
                                                                                                        总监驳回
                                                                                                    </span>
                                                                                                ) : null}

                                                                                                {/* Penetrated badge - strictly protected by hard gate */}
                                                                                                {penetrated ? (
                                                                                                    <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded bg-rose-600 text-white font-bold text-xs animate-pulse shadow-sm">
                                                                                                        <AlertOctagon className="w-3.5 h-3.5" />
                                                                                                        <span>已击穿</span>
                                                                                                    </span>
                                                                                                ) : isFatal && (evStatus === 'unsupported' || evStatus === 'contradicted') ? (
                                                                                                    <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                                                                                                        未击穿 (证据未通过核验)
                                                                                                    </span>
                                                                                                ) : null}
                                                                                            </div>
                                                                                        </div>

                                                                                        {ch.weakest_point && (
                                                                                            <div className="text-slate-200 font-medium">
                                                                                                <span className="text-rose-400 font-semibold mr-1">攻击弱点:</span>
                                                                                                <span>{ch.weakest_point}</span>
                                                                                            </div>
                                                                                        )}

                                                                                        {ch.evidence && (
                                                                                            <div className="text-slate-400 text-[11px]">
                                                                                                <span className="text-slate-500 mr-1">质询依据:</span>
                                                                                                <span>{Array.isArray(ch.evidence) ? ch.evidence.join('；') : ch.evidence}</span>
                                                                                            </div>
                                                                                        )}
                                                                                    </div>
                                                                                )
                                                                            })}
                                                                        </div>
                                                                    </div>
                                                                )}

                                                                {/* Prose speech */}
                                                                <div className="prose prose-invert prose-sm max-w-none text-slate-200 bg-slate-950/50 p-4 rounded-xl border border-slate-800 leading-relaxed">
                                                                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                                        {msg.cleaned_prose || '无正文记录'}
                                                                    </ReactMarkdown>
                                                                </div>

                                                                {/* Invalid attempts / retry history */}
                                                                {uniqueAttempts.length > 0 && (
                                                                    <div className="mt-3 pt-3 border-t border-slate-800">
                                                                        <button
                                                                            onClick={(e) => {
                                                                                e.stopPropagation()
                                                                                toggleAttemptExpand(`att-${key}`)
                                                                            }}
                                                                            className="flex items-center gap-2 text-xs font-semibold text-rose-400 hover:text-rose-300 transition-colors"
                                                                        >
                                                                            <ShieldAlert className="w-3.5 h-3.5" />
                                                                            <span>
                                                                                未采纳/无效尝试记录 ({uniqueAttempts.length} 次重试)
                                                                            </span>
                                                                            {expandedAttempts[`att-${key}`] ? (
                                                                                <ChevronDown className="w-3.5 h-3.5" />
                                                                            ) : (
                                                                                <ChevronRight className="w-3.5 h-3.5" />
                                                                            )}
                                                                        </button>

                                                                        {expandedAttempts[`att-${key}`] && (
                                                                            <div className="mt-2 space-y-2 pl-2">
                                                                                {uniqueAttempts.map((att, aIdx) => (
                                                                                    <div
                                                                                        key={`attempt-${aIdx}-${att.attempt_index ?? aIdx}`}
                                                                                        className="rounded-lg border border-rose-500/30 bg-rose-950/20 p-3 text-xs space-y-1.5"
                                                                                    >
                                                                                        <div className="flex items-center justify-between text-rose-400 font-medium">
                                                                                            <span>重试尝试 #{aIdx + 1} {att.model_name ? `(${att.model_name})` : ''}</span>
                                                                                            <span className="px-1.5 py-0.5 rounded bg-rose-500/20 text-[10px] font-mono">
                                                                                                {att.parse_status || 'invalid'}
                                                                                            </span>
                                                                                        </div>
                                                                                        {att.error_detail && (
                                                                                            <div className="text-rose-300 font-mono text-[11px] bg-rose-950/40 p-2 rounded border border-rose-500/20">
                                                                                                {att.error_detail}
                                                                                            </div>
                                                                                        )}
                                                                                        {att.cleaned_prose && (
                                                                                            <p className="text-slate-400 text-[11px] line-clamp-3 italic">
                                                                                                &ldquo;{att.cleaned_prose}&rdquo;
                                                                                            </p>
                                                                                        )}
                                                                                    </div>
                                                                                ))}
                                                                            </div>
                                                                        )}
                                                                    </div>
                                                                )}
                                                            </div>
                                                        )}
                                                    </div>
                                                )
                                            })}
                                        </div>
                                    )}

                                    {/* Dispute Map Card in Timeline flow for comprehensive overview */}
                                    {disputeMap.length > 0 && (
                                        <div className="rounded-xl border border-slate-700 bg-slate-900/90 p-5 space-y-4 shadow-md mt-6">
                                            <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                                                <div className="flex items-center gap-2">
                                                    <Scale className="w-4 h-4 text-amber-400" />
                                                    <h4 className="font-bold text-sm text-white">分歧全景图 (Dispute Map)</h4>
                                                </div>
                                                <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono">
                                                    {disputeMap.length} 项核心分歧
                                                </span>
                                            </div>

                                            <div className="overflow-x-auto">
                                                <table className="w-full text-xs text-left border-collapse">
                                                    <thead>
                                                        <tr className="border-b border-slate-800 text-slate-400 bg-slate-950/50">
                                                            <th className="py-2.5 px-3 font-semibold">争议数据点 (Data Point)</th>
                                                            <th className="py-2.5 px-3 font-semibold text-emerald-400">🐂 多头解读 (Bull)</th>
                                                            <th className="py-2.5 px-3 font-semibold text-rose-400">🐻 空头解读 (Bear)</th>
                                                            <th className="py-2.5 px-3 font-semibold text-blue-400">证据裁决 (Decision)</th>
                                                            <th className="py-2.5 px-3 font-semibold text-center">裁决归属</th>
                                                        </tr>
                                                    </thead>
                                                    <tbody className="divide-y divide-slate-800/60">
                                                        {disputeMap.map((item, dIdx) => {
                                                            const w = (item.winner || '').toLowerCase()
                                                            return (
                                                                <tr key={`tl-dm-${dIdx}`} className="hover:bg-slate-800/30 transition-colors">
                                                                    <td className="py-3 px-3 font-medium text-slate-200 align-top max-w-[200px]">
                                                                        {item.data_point || '-'}
                                                                    </td>
                                                                    <td className="py-3 px-3 text-slate-300 align-top bg-emerald-950/10">
                                                                        {item.bull_interpretation || '-'}
                                                                    </td>
                                                                    <td className="py-3 px-3 text-slate-300 align-top bg-rose-950/10">
                                                                        {item.bear_interpretation || '-'}
                                                                    </td>
                                                                    <td className="py-3 px-3 text-blue-300 font-medium align-top bg-blue-950/10">
                                                                        {item.evidence_decision || '-'}
                                                                    </td>
                                                                    <td className="py-3 px-3 text-center align-top whitespace-nowrap">
                                                                        <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold ${
                                                                            w === 'bull'
                                                                                ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                                                                : w === 'bear'
                                                                                    ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                                                                                    : 'bg-slate-800 text-slate-300 border border-slate-700'
                                                                        }`}>
                                                                            {w === 'bull' ? '🐂 多方胜' : w === 'bear' ? '🐻 空方胜' : '⚖️ 势均力敌'}
                                                                        </span>
                                                                    </td>
                                                                </tr>
                                                            )
                                                        })}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* TAB 2: 论点账本 (CLAIMS LEDGER & CHALLENGES) */}
                            {activeTab === 'claims' && (
                                <div className="space-y-6">
                                    {/* Search & Filter Bar */}
                                    <div className="flex flex-col sm:flex-row items-stretch sm:items-center justify-between gap-3 bg-slate-800/40 p-3 rounded-xl border border-slate-800">
                                        <div className="relative flex-1">
                                            <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400" />
                                            <input
                                                type="text"
                                                value={claimSearch}
                                                onChange={e => setClaimSearch(e.target.value)}
                                                placeholder="搜索 Claim ID、论点、战场、失效条件或证据..."
                                                className="w-full pl-9 pr-3 py-1.5 bg-slate-900 border border-slate-700 rounded-lg text-xs text-white placeholder-slate-500 focus:outline-none focus:border-blue-500"
                                            />
                                        </div>
                                        <div className="flex items-center gap-2 shrink-0 flex-wrap">
                                            <div className="flex items-center bg-slate-900 p-0.5 rounded-lg border border-slate-700 text-xs">
                                                <button
                                                    onClick={() => setClaimFilterStance('all')}
                                                    className={`px-2.5 py-1 rounded transition-colors ${
                                                        claimFilterStance === 'all' ? 'bg-blue-600 text-white' : 'text-slate-400 hover:text-slate-200'
                                                    }`}
                                                >
                                                    全部阵营
                                                </button>
                                                <button
                                                    onClick={() => setClaimFilterStance('bull')}
                                                    className={`px-2.5 py-1 rounded transition-colors ${
                                                        claimFilterStance === 'bull' ? 'bg-emerald-600 text-white' : 'text-slate-400 hover:text-slate-200'
                                                    }`}
                                                >
                                                    🐂 多头
                                                </button>
                                                <button
                                                    onClick={() => setClaimFilterStance('bear')}
                                                    className={`px-2.5 py-1 rounded transition-colors ${
                                                        claimFilterStance === 'bear' ? 'bg-rose-600 text-white' : 'text-slate-400 hover:text-slate-200'
                                                    }`}
                                                >
                                                    🐻 空头
                                                </button>
                                            </div>

                                            <select
                                                value={claimFilterStatus}
                                                onChange={e => setClaimFilterStatus(e.target.value)}
                                                aria-label="筛选论点状态"
                                                className="px-2.5 py-1 bg-slate-900 border border-slate-700 rounded-lg text-xs text-slate-300 focus:outline-none focus:border-blue-500"
                                            >
                                                <option value="all">所有状态</option>
                                                <option value="open">待回应 (open)</option>
                                                <option value="resolved">已解决 (resolved)</option>
                                                <option value="unresolved">存争议 (unresolved)</option>
                                                <option value="adopted">已采纳 (adopted)</option>
                                                <option value="rejected">已否决 (rejected)</option>
                                            </select>
                                        </div>
                                    </div>

                                    {/* SECTION 1: Claims Cards */}
                                    <div className="space-y-3">
                                        <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                                            <div className="flex items-center gap-2">
                                                <Database className="w-4 h-4 text-amber-400" />
                                                <h4 className="font-bold text-xs uppercase tracking-wider text-slate-300">
                                                    核心论点账本 (Claims · {filteredClaims.length})
                                                </h4>
                                            </div>
                                        </div>

                                        {filteredClaims.length === 0 ? (
                                            <div className="text-center py-8 text-slate-500 text-xs">
                                                没有找到匹配的 Claim 论点
                                            </div>
                                        ) : (
                                            <div className="space-y-3">
                                                {filteredClaims.map((claim, cIdx) => {
                                                    const isBull = (claim.stance || claim.speaker_key || claim.speaker || '').toLowerCase().includes('bull') || (claim.speaker || '').includes('多')
                                                    const claimText = claim.claim || claim.text || claim.content || '无论点正文'
                                                    const evidenceList = Array.isArray(claim.evidence)
                                                        ? claim.evidence
                                                        : claim.evidence ? [claim.evidence] : []
                                                    const confidence = typeof claim.confidence === 'number' ? claim.confidence : null
                                                    const status = claim.status || 'open'
                                                    const bfDisplay = getBattlefieldDisplay(claim.battlefield)

                                                    // Find if any message responded to this claim
                                                    const responderMsg = roundMessages.filter(m =>
                                                        m.responded_claim_ids?.includes(claim.claim_id) ||
                                                        m.target_claim_ids?.includes(claim.claim_id)
                                                    )

                                                    return (
                                                        <div
                                                            key={`claim-${claim.claim_id || cIdx}`}
                                                            className="rounded-xl border border-slate-800 bg-slate-900/80 p-4 space-y-3 hover:border-slate-700 transition-colors"
                                                        >
                                                            {/* Claim Header */}
                                                            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                                                                <div className="flex items-center gap-2 flex-wrap">
                                                                    <span className="font-mono text-sm font-bold text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                                                                        {claim.claim_id}
                                                                    </span>
                                                                    <span className={`inline-flex items-center gap-1 text-xs font-semibold px-2 py-0.5 rounded ${
                                                                        isBull
                                                                            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                                                                            : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                                                                    }`}>
                                                                        <span>{isBull ? '🐂 多头阵营' : '🐻 空头阵营'}</span>
                                                                    </span>
                                                                    {bfDisplay && (
                                                                        <span className={`inline-flex items-center gap-1 text-xs px-2 py-0.5 rounded-full border ${bfDisplay.bg} ${bfDisplay.text} ${bfDisplay.border}`}>
                                                                            <span>{bfDisplay.icon}</span>
                                                                            <span>{bfDisplay.label}</span>
                                                                        </span>
                                                                    )}
                                                                    {claim.round_index || claim.debate_round ? (
                                                                        <span className="text-xs text-slate-400 font-mono">
                                                                            第 {claim.debate_round || claim.round_index} 轮提出
                                                                        </span>
                                                                    ) : null}
                                                                </div>

                                                                <div className="flex items-center gap-2">
                                                                    {confidence !== null && (
                                                                        <span className="text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700 font-mono">
                                                                            置信度: {(confidence * 100).toFixed(0)}%
                                                                        </span>
                                                                    )}
                                                                    <span className={`text-xs px-2 py-0.5 rounded-full font-medium ${
                                                                        status === 'resolved' || status === 'adopted'
                                                                            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                                                                            : status === 'rejected'
                                                                                ? 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                                                                                : status === 'unresolved'
                                                                                    ? 'bg-purple-500/15 text-purple-400 border border-purple-500/30'
                                                                                    : 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
                                                                    }`}>
                                                                        {status === 'resolved' ? '已解决' :
                                                                         status === 'adopted' ? '总监采纳' :
                                                                         status === 'rejected' ? '总监否决' :
                                                                         status === 'unresolved' ? '争议未决' : '待回应 (open)'}
                                                                    </span>
                                                                </div>
                                                            </div>

                                                            {/* Claim Content */}
                                                            <div className="text-sm font-medium text-slate-100 leading-relaxed">
                                                                {claimText}
                                                            </div>

                                                            {/* Evidence List */}
                                                            {evidenceList.length > 0 && (
                                                                <div className="space-y-1.5 bg-slate-950/40 p-3 rounded-lg border border-slate-800/60">
                                                                    <span className="text-[11px] uppercase tracking-wider font-semibold text-slate-400">
                                                                        支撑证据 ({evidenceList.length})
                                                                    </span>
                                                                    <ul className="space-y-1">
                                                                        {evidenceList.map((ev, evIdx) => (
                                                                            <li key={`ev-${claim.claim_id}-${evIdx}`} className="text-xs text-slate-300 flex items-start gap-1.5">
                                                                                <span className="text-blue-400 mt-0.5">•</span>
                                                                                <span>{String(ev)}</span>
                                                                            </li>
                                                                        ))}
                                                                    </ul>
                                                                </div>
                                                            )}

                                                            {/* Falsification Conditions */}
                                                            {claim.falsification_conditions && (
                                                                <div className="space-y-1 bg-amber-950/20 p-2.5 rounded-lg border border-amber-500/30">
                                                                    <span className="text-[11px] font-bold text-amber-400 flex items-center gap-1">
                                                                        <AlertTriangle className="w-3 h-3" /> 失效条件 (Falsification Conditions)
                                                                    </span>
                                                                    <div className="text-xs text-amber-200/90 leading-relaxed">
                                                                        {Array.isArray(claim.falsification_conditions)
                                                                            ? claim.falsification_conditions.join('；')
                                                                            : String(claim.falsification_conditions)}
                                                                    </div>
                                                                </div>
                                                            )}

                                                            {/* Responded in Rounds */}
                                                            {responderMsg.length > 0 && (
                                                                <div className="flex items-center gap-2 text-xs text-slate-400 pt-1">
                                                                    <span className="text-slate-500">被回应轮次:</span>
                                                                    <div className="flex items-center gap-1.5 flex-wrap">
                                                                        {responderMsg.map(rm => (
                                                                            <span key={`res-${rm.message_index}`} className="px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700 font-mono text-[11px]">
                                                                                {rm.speaker_key || rm.speaker} (#Round {rm.debate_round || Math.ceil((rm.message_index || 1) / 2)})
                                                                            </span>
                                                                        ))}
                                                                    </div>
                                                                </div>
                                                            )}
                                                        </div>
                                                    )
                                                })}
                                            </div>
                                        )}
                                    </div>

                                    {/* SECTION 2: Challenges List */}
                                    {challenges.length > 0 && (
                                        <div className="space-y-3 pt-4 border-t border-slate-800">
                                            <div className="flex items-center justify-between border-b border-slate-800 pb-2">
                                                <div className="flex items-center gap-2">
                                                    <Swords className="w-4 h-4 text-rose-400" />
                                                    <h4 className="font-bold text-xs uppercase tracking-wider text-slate-300">
                                                        交叉盘问与质询清单 (Challenges · {challenges.length})
                                                    </h4>
                                                </div>
                                            </div>

                                            <div className="space-y-3">
                                                {challenges.map((ch, chIdx) => {
                                                    const isBull = (ch.speaker_key || ch.speaker || '').toLowerCase().includes('bull') || (ch.speaker || '').includes('多')
                                                    const isFatal = (ch.severity || '').toLowerCase() === 'fatal'
                                                    const isMajor = (ch.severity || '').toLowerCase() === 'major'
                                                    const evStatus = (ch.evidence_status || 'unverified').toLowerCase()
                                                    const isAdopted = managerVerdict?.adopted_challenge_ids?.includes(ch.challenge_id || '') || ch.status === 'adopted'
                                                    const isRejected = managerVerdict?.rejected_challenge_ids?.includes(ch.challenge_id || '') || ch.status === 'rejected'
                                                    const penetrated = isChallengePenetrated(ch, managerVerdict?.adopted_challenge_ids)

                                                    return (
                                                        <div
                                                            key={`tab-ch-${ch.challenge_id || chIdx}`}
                                                            className={`p-4 rounded-xl border ${
                                                                penetrated
                                                                    ? 'border-rose-500 bg-rose-950/30 ring-1 ring-rose-500/50'
                                                                    : 'border-slate-800 bg-slate-900/80'
                                                            } space-y-3 text-xs`}
                                                        >
                                                            <div className="flex items-center justify-between gap-2 flex-wrap border-b border-slate-800/80 pb-2">
                                                                <div className="flex items-center gap-2 flex-wrap">
                                                                    <span className="font-mono font-bold text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20">
                                                                        {ch.challenge_id || `CH-${chIdx + 1}`}
                                                                    </span>
                                                                    <span className={`px-2 py-0.5 rounded font-semibold ${
                                                                        isBull
                                                                            ? 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
                                                                            : 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
                                                                    }`}>
                                                                        {isBull ? '🐂 多头发起' : '🐻 空头发起'}
                                                                    </span>
                                                                    <span className="text-slate-400">
                                                                        质询目标: <span className="font-mono text-blue-300 font-bold">{ch.target_claim_id || '-'}</span>
                                                                    </span>
                                                                    <span className={`px-2 py-0.5 rounded font-mono font-bold uppercase ${
                                                                        isFatal
                                                                            ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                                                                            : isMajor
                                                                                ? 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                                                                                : 'bg-slate-800 text-slate-300 border border-slate-700'
                                                                    }`}>
                                                                        严厉度: {ch.severity || 'major'}
                                                                    </span>
                                                                </div>

                                                                <div className="flex items-center gap-2 flex-wrap">
                                                                    <span className={`px-2 py-0.5 rounded-full font-medium ${
                                                                        evStatus === 'verified'
                                                                            ? 'bg-emerald-500/15 text-emerald-300 border border-emerald-500/30'
                                                                            : evStatus === 'contradicted'
                                                                                ? 'bg-rose-500/15 text-rose-300 border border-rose-500/30'
                                                                                : evStatus === 'unsupported'
                                                                                    ? 'bg-amber-500/15 text-amber-300 border border-amber-500/30'
                                                                                    : 'bg-slate-800 text-slate-400 border border-slate-700'
                                                                    }`}>
                                                                        证据: {evStatus}
                                                                    </span>

                                                                    {isAdopted ? (
                                                                        <span className="px-2 py-0.5 rounded bg-emerald-500/15 text-emerald-400 border border-emerald-500/30 font-semibold">
                                                                            总监采纳
                                                                        </span>
                                                                    ) : isRejected ? (
                                                                        <span className="px-2 py-0.5 rounded bg-rose-500/15 text-rose-400 border border-rose-500/30 font-semibold">
                                                                            总监驳回
                                                                        </span>
                                                                    ) : (
                                                                        <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                                                                            待裁决
                                                                        </span>
                                                                    )}

                                                                    {/* Penetrated badge - strictly protected by hard gate */}
                                                                    {penetrated ? (
                                                                        <span className="inline-flex items-center gap-1 px-2.5 py-0.5 rounded bg-rose-600 text-white font-bold text-xs animate-pulse shadow-sm">
                                                                            <AlertOctagon className="w-3.5 h-3.5" />
                                                                            <span>已击穿</span>
                                                                        </span>
                                                                    ) : isFatal && (evStatus === 'unsupported' || evStatus === 'contradicted') ? (
                                                                        <span className="px-2 py-0.5 rounded bg-slate-800 text-slate-400 border border-slate-700">
                                                                            未击穿 (证据未通过核验)
                                                                        </span>
                                                                    ) : null}
                                                                </div>
                                                            </div>

                                                            {ch.weakest_point && (
                                                                <div className="text-slate-100 font-medium leading-relaxed">
                                                                    <span className="text-rose-400 font-semibold mr-1.5">质疑弱点:</span>
                                                                    <span>{ch.weakest_point}</span>
                                                                </div>
                                                            )}

                                                            {ch.evidence && (
                                                                <div className="bg-slate-950/40 p-2.5 rounded-lg border border-slate-800/60 text-slate-300 text-xs">
                                                                    <span className="text-slate-500 mr-1.5">质询支撑依据:</span>
                                                                    <span>{Array.isArray(ch.evidence) ? ch.evidence.join('；') : ch.evidence}</span>
                                                                </div>
                                                            )}
                                                        </div>
                                                    )
                                                })}
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* TAB 3: 研究总监裁决 (MANAGER VERDICT) */}
                            {activeTab === 'verdict' && (
                                <div className="space-y-5">
                                    {managerVerdict ? (
                                        <>
                                            {/* Degenerate Alert Banner in Verdict if applicable */}
                                            {debateDegenerate && (
                                                <div className="rounded-xl border border-amber-500/40 bg-amber-950/30 p-4 flex items-start gap-3 shadow-sm">
                                                    <AlertTriangle className="w-5 h-5 text-amber-400 shrink-0 mt-0.5" />
                                                    <div>
                                                        <div className="flex items-center gap-2">
                                                            <h4 className="text-xs font-bold text-amber-300">辩论退化警告 (Debate Degenerate)</h4>
                                                            <span className="text-[10px] px-2 py-0.2 rounded-full bg-amber-500/20 text-amber-300 border border-amber-500/40 font-mono font-bold">
                                                                静止退化
                                                            </span>
                                                        </div>
                                                        <p className="text-xs text-slate-300 mt-1 leading-relaxed">
                                                            多空双方在各轮质询中胜率预估均未随反方新证据调整，辩论信念轨迹趋于静止退化。
                                                        </p>
                                                    </div>
                                                </div>
                                            )}

                                            {/* Winner & Direction Card */}
                                            <div className="rounded-2xl border border-slate-700 bg-gradient-to-br from-slate-800/80 to-slate-900 p-6 space-y-4 shadow-lg">
                                                <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4 border-b border-slate-700/80 pb-4">
                                                    <div className="flex items-center gap-3">
                                                        <div className="p-3 rounded-2xl bg-blue-500/10 border border-blue-500/20 text-blue-400">
                                                            <Scale className="w-8 h-8" />
                                                        </div>
                                                        <div>
                                                            <span className="text-xs uppercase tracking-wider text-slate-400 font-semibold">
                                                                研究总监裁决结果
                                                            </span>
                                                            <div className="flex items-center gap-2 mt-1">
                                                                <h3 className="text-xl font-black text-white tracking-tight">
                                                                    {managerVerdict.winner === 'bull' ? '🐂 多头全面胜出' :
                                                                     managerVerdict.winner === 'bear' ? '🐻 空头全面胜出' :
                                                                     '⚖️ 势均力敌 / 建议中性观望'}
                                                                </h3>
                                                            </div>
                                                        </div>
                                                    </div>

                                                    <div className="flex items-center gap-3 flex-wrap">
                                                        <div className="px-3 py-1.5 rounded-xl bg-slate-800 border border-slate-700 text-center">
                                                            <span className="text-[10px] text-slate-400 block">推荐方向</span>
                                                            <span className="text-sm font-bold text-blue-400">
                                                                {managerVerdict.direction || '中性'}
                                                            </span>
                                                        </div>
                                                        {managerVerdict.position_pct !== undefined && managerVerdict.position_pct !== null && (
                                                            <div className="px-3 py-1.5 rounded-xl bg-slate-800 border border-slate-700 text-center">
                                                                <span className="text-[10px] text-slate-400 block">建议仓位</span>
                                                                <span className="text-sm font-bold text-amber-400">
                                                                    {String(managerVerdict.position_pct).endsWith('%')
                                                                        ? managerVerdict.position_pct
                                                                        : `${Number(managerVerdict.position_pct) * (Number(managerVerdict.position_pct) <= 1 ? 100 : 1)}%`}
                                                                </span>
                                                            </div>
                                                        )}
                                                    </div>
                                                </div>

                                                {/* Trade Parameters Grid */}
                                                <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-6 gap-2 pt-1">
                                                    <div className="bg-slate-900/80 p-3 rounded-xl border border-slate-800">
                                                        <span className="text-[10px] text-slate-400 block">入场参考</span>
                                                        <span className="text-xs font-bold text-slate-200 mt-1 block">
                                                            {managerVerdict.entry ?? '-'}
                                                        </span>
                                                    </div>
                                                    <div className="bg-slate-900/80 p-3 rounded-xl border border-slate-800">
                                                        <span className="text-[10px] text-slate-400 block">目标点位</span>
                                                        <span className="text-xs font-bold text-emerald-400 mt-1 block">
                                                            {managerVerdict.target ?? '-'}
                                                        </span>
                                                    </div>
                                                    <div className="bg-slate-900/80 p-3 rounded-xl border border-slate-800">
                                                        <span className="text-[10px] text-slate-400 block">严格止损</span>
                                                        <span className="text-xs font-bold text-rose-400 mt-1 block">
                                                            {managerVerdict.stop_loss ?? '-'}
                                                        </span>
                                                    </div>
                                                    <div className="bg-slate-900/80 p-3 rounded-xl border border-slate-800">
                                                        <span className="text-[10px] text-slate-400 block">赔率 / 盈亏比</span>
                                                        <span className="text-xs font-bold text-amber-400 mt-1 block">
                                                            {managerVerdict.odds ?? '-'}
                                                        </span>
                                                    </div>
                                                    <div className="bg-slate-900/80 p-3 rounded-xl border border-slate-800">
                                                        <span className="text-[10px] text-slate-400 block">上涨空间</span>
                                                        <span className="text-xs font-bold text-emerald-400 mt-1 block">
                                                            {managerVerdict.upside ?? '-'}
                                                        </span>
                                                    </div>
                                                    <div className="bg-slate-900/80 p-3 rounded-xl border border-slate-800">
                                                        <span className="text-[10px] text-slate-400 block">下行风险</span>
                                                        <span className="text-xs font-bold text-rose-400 mt-1 block">
                                                            {managerVerdict.downside ?? '-'}
                                                        </span>
                                                    </div>
                                                </div>
                                            </div>

                                            {/* Dispute Map (分歧全景图) */}
                                            {disputeMap.length > 0 && (
                                                <div className="rounded-xl border border-slate-700 bg-slate-900/90 p-5 space-y-4 shadow-md">
                                                    <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                                                        <div className="flex items-center gap-2">
                                                            <Scale className="w-4 h-4 text-amber-400" />
                                                            <h4 className="font-bold text-sm text-white">分歧全景图 (Dispute Map)</h4>
                                                        </div>
                                                        <span className="text-xs px-2 py-0.5 rounded-full bg-slate-800 text-slate-400 border border-slate-700 font-mono">
                                                            {disputeMap.length} 项核心分歧
                                                        </span>
                                                    </div>

                                                    <div className="overflow-x-auto">
                                                        <table className="w-full text-xs text-left border-collapse">
                                                            <thead>
                                                                <tr className="border-b border-slate-800 text-slate-400 bg-slate-950/50">
                                                                    <th className="py-2.5 px-3 font-semibold">争议数据点 (Data Point)</th>
                                                                    <th className="py-2.5 px-3 font-semibold text-emerald-400">🐂 多头解读 (Bull)</th>
                                                                    <th className="py-2.5 px-3 font-semibold text-rose-400">🐻 空头解读 (Bear)</th>
                                                                    <th className="py-2.5 px-3 font-semibold text-blue-400">证据裁决 (Decision)</th>
                                                                    <th className="py-2.5 px-3 font-semibold text-center">裁决归属</th>
                                                                </tr>
                                                            </thead>
                                                            <tbody className="divide-y divide-slate-800/60">
                                                                {disputeMap.map((item, dIdx) => {
                                                                    const w = (item.winner || '').toLowerCase()
                                                                    return (
                                                                        <tr key={`dm-${dIdx}`} className="hover:bg-slate-800/30 transition-colors">
                                                                            <td className="py-3 px-3 font-medium text-slate-200 align-top max-w-[200px]">
                                                                                {item.data_point || '-'}
                                                                            </td>
                                                                            <td className="py-3 px-3 text-slate-300 align-top bg-emerald-950/10">
                                                                                {item.bull_interpretation || '-'}
                                                                            </td>
                                                                            <td className="py-3 px-3 text-slate-300 align-top bg-rose-950/10">
                                                                                {item.bear_interpretation || '-'}
                                                                            </td>
                                                                            <td className="py-3 px-3 text-blue-300 font-medium align-top bg-blue-950/10">
                                                                                {item.evidence_decision || '-'}
                                                                            </td>
                                                                            <td className="py-3 px-3 text-center align-top whitespace-nowrap">
                                                                                <span className={`inline-flex items-center px-2 py-0.5 rounded text-[11px] font-bold ${
                                                                                    w === 'bull'
                                                                                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                                                                        : w === 'bear'
                                                                                            ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                                                                                            : 'bg-slate-800 text-slate-300 border border-slate-700'
                                                                                }`}>
                                                                                    {w === 'bull' ? '🐂 多方胜' : w === 'bear' ? '🐻 空方胜' : '⚖️ 势均力敌'}
                                                                                </span>
                                                                            </td>
                                                                        </tr>
                                                                    )
                                                                })}
                                                            </tbody>
                                                        </table>
                                                    </div>
                                                </div>
                                            )}

                                            {/* Consistency Check Hard Gate */}
                                            <div className={`rounded-xl border p-4 ${
                                                managerVerdict.consistency_check_passed === true
                                                    ? 'border-emerald-500/30 bg-emerald-950/20'
                                                    : managerVerdict.consistency_check_passed === false
                                                        ? 'border-rose-500 bg-rose-950/30 ring-1 ring-rose-500/50'
                                                        : 'border-slate-800 bg-slate-900/60'
                                            }`}>
                                                <div className="flex items-center gap-2 mb-2">
                                                    {managerVerdict.consistency_check_passed === true ? (
                                                        <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                                                    ) : managerVerdict.consistency_check_passed === false ? (
                                                        <AlertOctagon className="w-5 h-5 text-rose-400" />
                                                    ) : (
                                                        <ShieldCheck className="w-5 h-5 text-slate-400" />
                                                    )}
                                                    <h4 className="font-bold text-sm text-white">
                                                        裁决逻辑自洽硬闸 (Consistency Hard Gate)
                                                    </h4>
                                                    <span className={`ml-auto text-xs px-2.5 py-0.5 rounded-full font-bold ${
                                                        managerVerdict.consistency_check_passed === true
                                                            ? 'bg-emerald-500/20 text-emerald-300'
                                                            : managerVerdict.consistency_check_passed === false
                                                                ? 'bg-rose-500 text-white animate-pulse'
                                                                : 'bg-slate-800 text-slate-400'
                                                    }`}>
                                                        {managerVerdict.consistency_check_passed === true ? '自洽通过' :
                                                         managerVerdict.consistency_check_passed === false ? '自洽失败' : '未检测'}
                                                    </span>
                                                </div>

                                                {managerVerdict.consistency_check_passed === false && managerVerdict.failed_checks && managerVerdict.failed_checks.length > 0 ? (
                                                    <div className="mt-2 space-y-1.5">
                                                        <p className="text-xs text-rose-300 font-semibold">硬闸拦截原因：</p>
                                                        <ul className="space-y-1">
                                                            {managerVerdict.failed_checks.map((err, errIdx) => (
                                                                <li key={`fail-${errIdx}`} className="text-xs text-rose-200 bg-rose-900/40 px-3 py-1.5 rounded border border-rose-500/30 flex items-start gap-1.5">
                                                                    <span className="text-rose-400 font-bold">•</span>
                                                                    <span>{err}</span>
                                                                </li>
                                                            ))}
                                                        </ul>
                                                    </div>
                                                ) : (
                                                    <p className="text-xs text-slate-300 mt-1">
                                                        裁决方向、胜负归属、仓位上限控制与止损约束均通过确定性一致性验证。
                                                    </p>
                                                )}
                                            </div>

                                            {/* Adopted / Rejected Claims & Challenges */}
                                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                <div className="rounded-xl border border-emerald-500/20 bg-emerald-950/10 p-4 space-y-3">
                                                    <div className="flex items-center gap-2 text-emerald-400 font-semibold text-xs uppercase tracking-wider">
                                                        <CheckCircle2 className="w-4 h-4" />
                                                        <span>总监采纳项 (Adopted)</span>
                                                    </div>
                                                    <div>
                                                        <span className="text-[11px] text-slate-400 block mb-1">采纳论点 (Claims):</span>
                                                        {managerVerdict.adopted_claim_ids && managerVerdict.adopted_claim_ids.length > 0 ? (
                                                            <div className="flex flex-wrap gap-1.5">
                                                                {managerVerdict.adopted_claim_ids.map(cid => (
                                                                    <span key={`ad-claim-${cid}`} className="px-2.5 py-1 rounded bg-emerald-500/15 border border-emerald-500/30 text-emerald-300 text-xs font-mono font-bold">
                                                                        {cid}
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        ) : (
                                                            <p className="text-xs text-slate-400 italic">未指定明确采纳的 Claim ID</p>
                                                        )}
                                                    </div>
                                                    {managerVerdict.adopted_challenge_ids && managerVerdict.adopted_challenge_ids.length > 0 && (
                                                        <div className="pt-2 border-t border-emerald-500/20">
                                                            <span className="text-[11px] text-slate-400 block mb-1">采纳盘问 (Challenges):</span>
                                                            <div className="flex flex-wrap gap-1.5">
                                                                {managerVerdict.adopted_challenge_ids.map(chid => (
                                                                    <span key={`ad-ch-${chid}`} className="px-2.5 py-1 rounded bg-emerald-500/20 border border-emerald-500/40 text-emerald-200 text-xs font-mono font-bold">
                                                                        {chid}
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>

                                                <div className="rounded-xl border border-rose-500/20 bg-rose-950/10 p-4 space-y-3">
                                                    <div className="flex items-center gap-2 text-rose-400 font-semibold text-xs uppercase tracking-wider">
                                                        <XCircle className="w-4 h-4" />
                                                        <span>总监否决/驳回项 (Rejected)</span>
                                                    </div>
                                                    <div>
                                                        <span className="text-[11px] text-slate-400 block mb-1">否决论点 (Claims):</span>
                                                        {managerVerdict.rejected_claim_ids && managerVerdict.rejected_claim_ids.length > 0 ? (
                                                            <div className="flex flex-wrap gap-1.5">
                                                                {managerVerdict.rejected_claim_ids.map(cid => (
                                                                    <span key={`rej-claim-${cid}`} className="px-2.5 py-1 rounded bg-rose-500/15 border border-rose-500/30 text-rose-300 text-xs font-mono font-bold">
                                                                        {cid}
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        ) : (
                                                            <p className="text-xs text-slate-400 italic">无否决 Claim ID 记录</p>
                                                        )}
                                                    </div>
                                                    {managerVerdict.rejected_challenge_ids && managerVerdict.rejected_challenge_ids.length > 0 && (
                                                        <div className="pt-2 border-t border-rose-500/20">
                                                            <span className="text-[11px] text-slate-400 block mb-1">驳回盘问 (Challenges):</span>
                                                            <div className="flex flex-wrap gap-1.5">
                                                                {managerVerdict.rejected_challenge_ids.map(chid => (
                                                                    <span key={`rej-ch-${chid}`} className="px-2.5 py-1 rounded bg-rose-500/20 border border-rose-500/40 text-rose-200 text-xs font-mono font-bold">
                                                                        {chid}
                                                                    </span>
                                                                ))}
                                                            </div>
                                                        </div>
                                                    )}
                                                </div>
                                            </div>

                                            {/* Reason & Final Text */}
                                            {(managerVerdict.reason || debateState?.judge_decision) && (
                                                <div className="rounded-xl border border-slate-800 bg-slate-900/80 p-5 space-y-3">
                                                    <h4 className="text-xs font-semibold uppercase tracking-wider text-slate-400">
                                                        裁决理由与综合逻辑推演
                                                    </h4>
                                                    <div className="prose prose-invert prose-sm max-w-none text-slate-200 leading-relaxed bg-slate-950/40 p-4 rounded-xl border border-slate-800">
                                                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                            {managerVerdict.reason || debateState?.judge_decision || ''}
                                                        </ReactMarkdown>
                                                    </div>
                                                </div>
                                            )}
                                        </>
                                    ) : (
                                        <div className="text-center py-12 text-slate-500 text-sm">
                                            暂无总监裁决结构化数据
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* TAB 4: 事实核验 (EVIDENCE VERIFICATION) */}
                            {activeTab === 'evidence' && (
                                <div className="space-y-4">
                                    {/* Stats KPI bar */}
                                    <div className="grid grid-cols-2 sm:grid-cols-5 gap-2">
                                        <button
                                            onClick={() => setEvidenceFilterStatus('all')}
                                            className={`p-3 rounded-xl border text-left transition-all ${
                                                evidenceFilterStatus === 'all'
                                                    ? 'bg-blue-900/30 border-blue-500 text-white'
                                                    : 'bg-slate-900/80 border-slate-800 text-slate-300 hover:border-slate-700'
                                            }`}
                                        >
                                            <span className="text-[10px] text-slate-400 block">全部核验证据</span>
                                            <span className="text-base font-bold tabular-nums mt-0.5 block">{evidenceStats.total}</span>
                                        </button>
                                        <button
                                            onClick={() => setEvidenceFilterStatus('verified')}
                                            className={`p-3 rounded-xl border text-left transition-all ${
                                                evidenceFilterStatus === 'verified'
                                                    ? 'bg-emerald-900/30 border-emerald-500 text-emerald-200'
                                                    : 'bg-slate-900/80 border-slate-800 text-slate-300 hover:border-slate-700'
                                            }`}
                                        >
                                            <span className="text-[10px] text-emerald-400 block">✅ 事实属实 (Verified)</span>
                                            <span className="text-base font-bold tabular-nums text-emerald-400 mt-0.5 block">{evidenceStats.verified}</span>
                                        </button>
                                        <button
                                            onClick={() => setEvidenceFilterStatus('unsupported')}
                                            className={`p-3 rounded-xl border text-left transition-all ${
                                                evidenceFilterStatus === 'unsupported'
                                                    ? 'bg-amber-900/30 border-amber-500 text-amber-200'
                                                    : 'bg-slate-900/80 border-slate-800 text-slate-300 hover:border-slate-700'
                                            }`}
                                        >
                                            <span className="text-[10px] text-amber-400 block">⚠️ 缺乏依据 (Unsupported)</span>
                                            <span className="text-base font-bold tabular-nums text-amber-400 mt-0.5 block">{evidenceStats.unsupported}</span>
                                        </button>
                                        <button
                                            onClick={() => setEvidenceFilterStatus('contradicted')}
                                            className={`p-3 rounded-xl border text-left transition-all ${
                                                evidenceFilterStatus === 'contradicted'
                                                    ? 'bg-rose-900/30 border-rose-500 text-rose-200'
                                                    : 'bg-slate-900/80 border-slate-800 text-slate-300 hover:border-slate-700'
                                            }`}
                                        >
                                            <span className="text-[10px] text-rose-400 block">❌ 数据冲突 (Contradicted)</span>
                                            <span className="text-base font-bold tabular-nums text-rose-400 mt-0.5 block">{evidenceStats.contradicted}</span>
                                        </button>
                                        <button
                                            onClick={() => setEvidenceFilterStatus('fatal')}
                                            className={`p-3 rounded-xl border text-left transition-all ${
                                                evidenceFilterStatus === 'fatal'
                                                    ? 'bg-rose-600 border-rose-400 text-white shadow-lg'
                                                    : evidenceStats.fatal > 0
                                                        ? 'bg-rose-950/40 border-rose-500/50 text-rose-300'
                                                        : 'bg-slate-900/80 border-slate-800 text-slate-400 hover:border-slate-700'
                                            }`}
                                        >
                                            <span className={`text-[10px] block font-bold ${evidenceStats.fatal > 0 ? 'text-rose-400' : 'text-slate-400'}`}>
                                                🚨 致命错误 (Fatal)
                                            </span>
                                            <span className="text-base font-bold tabular-nums mt-0.5 block">{evidenceStats.fatal}</span>
                                        </button>
                                    </div>

                                    {/* Evidence Items List */}
                                    {filteredEvidence.length === 0 ? (
                                        <div className="text-center py-12 text-slate-500 text-sm">
                                            没有找到匹配的事实核验记录
                                        </div>
                                    ) : (
                                        <div className="space-y-3">
                                            {filteredEvidence.map((ev, evIdx) => {
                                                const isFatal = ev.is_fatal === true
                                                const st = (ev.status || '').toLowerCase()
                                                const isVerified = st === 'verified'
                                                const isUnsupported = st === 'unsupported'
                                                const isContradicted = st === 'contradicted'
                                                const isSourceUnavailable = st === 'source_unavailable'

                                                const borderCls = isFatal
                                                    ? 'border-rose-500 bg-rose-950/40 ring-2 ring-rose-500/40 shadow-rose-950/50'
                                                    : isContradicted
                                                        ? 'border-rose-500/40 bg-rose-950/20'
                                                        : isUnsupported
                                                            ? 'border-amber-500/30 bg-amber-950/20'
                                                            : isSourceUnavailable
                                                                ? 'border-rose-500/30 bg-rose-950/20'
                                                                : 'border-emerald-500/30 bg-emerald-950/15'

                                                return (
                                                    <div
                                                        key={`ev-item-${ev.claim_id || ev.challenge_id || 'ev'}-${evIdx}`}
                                                        className={`rounded-xl border ${borderCls} p-4 space-y-3 transition-all`}
                                                    >
                                                        {/* Top status line */}
                                                        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-800/80 pb-2">
                                                            <div className="flex items-center gap-2">
                                                                {isFatal && (
                                                                    <span className="inline-flex items-center gap-1 text-xs font-black px-2.5 py-0.5 rounded bg-rose-600 text-white animate-pulse">
                                                                        <AlertOctagon className="w-3.5 h-3.5" />
                                                                        <span>FATAL 致命事实错误</span>
                                                                    </span>
                                                                )}
                                                                <span className={`inline-flex items-center gap-1 text-xs font-bold px-2.5 py-0.5 rounded-full ${
                                                                    isVerified
                                                                        ? 'bg-emerald-500/20 text-emerald-300 border border-emerald-500/40'
                                                                        : isContradicted
                                                                            ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                                                                            : isSourceUnavailable
                                                                                ? 'bg-rose-500/20 text-rose-300 border border-rose-500/40'
                                                                                : 'bg-amber-500/20 text-amber-300 border border-amber-500/40'
                                                                }`}>
                                                                    {isVerified && <CheckCircle2 className="w-3.5 h-3.5" />}
                                                                    {isContradicted && <XCircle className="w-3.5 h-3.5" />}
                                                                    {isUnsupported && <AlertTriangle className="w-3.5 h-3.5" />}
                                                                    {isSourceUnavailable && <AlertTriangle className="w-3.5 h-3.5" />}
                                                                    <span>
                                                                        {isVerified ? '事实核验通过 (Verified)' :
                                                                         isContradicted ? '存在事实冲突 (Contradicted)' :
                                                                         isSourceUnavailable ? '数据源不可用 (Source Unavailable)' :
                                                                         '缺乏证据支撑 (Unsupported)'}
                                                                    </span>
                                                                </span>

                                                                {ev.claim_id && (
                                                                    <span className="font-mono text-xs px-2 py-0.5 rounded bg-slate-800 text-slate-300 border border-slate-700">
                                                                        Claim: {ev.claim_id}
                                                                    </span>
                                                                )}
                                                                {ev.challenge_id && (
                                                                    <span className="font-mono text-xs px-2 py-0.5 rounded bg-slate-800 text-amber-300 border border-slate-700">
                                                                        Challenge: {ev.challenge_id}
                                                                    </span>
                                                                )}
                                                            </div>

                                                            {(ev.matched_source || ev.matched_role) && (
                                                                <span className="text-xs text-slate-400">
                                                                    匹配来源: <span className="text-blue-400 font-medium">{ev.matched_source || ev.matched_role}</span>
                                                                </span>
                                                            )}
                                                        </div>

                                                        {/* Evidence Raw Text */}
                                                        <div className="text-sm font-medium text-slate-100 bg-slate-950/60 p-3 rounded-lg border border-slate-800/80">
                                                            <span className="text-xs text-slate-500 block mb-1">证据原文：</span>
                                                            &ldquo;{ev.raw}&rdquo;
                                                        </div>

                                                        {/* Verification Details */}
                                                        {ev.details && (
                                                            <div className="text-xs text-slate-300 leading-relaxed bg-slate-900/60 p-3 rounded-lg border border-slate-800/40">
                                                                <span className="text-slate-400 font-semibold mr-1">核验详情:</span>
                                                                <span>{ev.details}</span>
                                                            </div>
                                                        )}
                                                    </div>
                                                )
                                            })}
                                        </div>
                                    )}
                                </div>
                            )}

                            {/* TAB 5: 历史文本记录 (LEGACY TRANSCRIPT) */}
                            {activeTab === 'legacy' && (
                                <div className="space-y-4">
                                    <div className="text-xs text-slate-400 pb-1">
                                        历史纯文本记录视图
                                    </div>
                                    {debateState?.bull_history && (
                                        <div className="rounded-xl border border-emerald-500/30 bg-emerald-500/5 p-4">
                                            <div className="flex items-center gap-2 mb-2 text-emerald-400 font-semibold text-sm">
                                                <span>🐂</span> 多头观点记录
                                            </div>
                                            <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                    {debateState.bull_history}
                                                </ReactMarkdown>
                                            </div>
                                        </div>
                                    )}
                                    {debateState?.bear_history && (
                                        <div className="rounded-xl border border-rose-500/30 bg-rose-500/5 p-4">
                                            <div className="flex items-center gap-2 mb-2 text-rose-400 font-semibold text-sm">
                                                <span>🐻</span> 空头观点记录
                                            </div>
                                            <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                    {debateState.bear_history}
                                                </ReactMarkdown>
                                            </div>
                                        </div>
                                    )}
                                    {debateState?.judge_decision && (
                                        <div className="rounded-xl border border-blue-500/30 bg-blue-500/5 p-4">
                                            <div className="flex items-center gap-2 mb-2 text-blue-400 font-semibold text-sm">
                                                <span>🏛️</span> 裁判裁决记录
                                            </div>
                                            <div className="prose prose-invert prose-sm max-w-none text-slate-300">
                                                <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                                                    {debateState.judge_decision}
                                                </ReactMarkdown>
                                            </div>
                                        </div>
                                    )}
                                </div>
                            )}
                        </>
                    )}
                </div>
            </div>
        </>
    )
}
