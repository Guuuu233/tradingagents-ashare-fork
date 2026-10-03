import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'
import { reconcilePersistedChatMessages } from '@/utils/chatRecovery'
import type {
    Agent,
    JobStatus,
    AnalysisReport,
    LogEntry,
    AgentStatusEvent,
    AgentReportEvent,
    AgentSnapshotEvent,
    ReportChunkEvent,
    AgentMilestoneEvent,
    AgentTokenEvent,
    StreamingSectionState,
    MilestoneMessage,
    RiskItem,
    KeyMetric,
    DebateMessage,
} from '@/types'

export interface ChatMessage {
    id: string
    role: 'user' | 'assistant' | 'system' | 'report'
    content: string
    timestamp: string
    agent?: string      // The name of the agent who sent the message
    section?: string    // only for role='report'
    complete?: boolean  // completed report or agent message
}

const createInitialChatMessages = (): ChatMessage[] => [
    {
        id: 'init',
        role: 'assistant',
        content: '我是你的 A 股多智能体投研助手。直接告诉我你想分析的标的和日期。',
        timestamp: new Date().toISOString(),
    },
]

interface AnalysisState {
    // Current Job
    currentJobId: string | null
    currentSymbol: string
    jobStatus: JobStatus | null

    // Agents
    agents: Agent[]

    // Report
    report: AnalysisReport | null

    // Structured data from job.completed SSE event (LLM-extracted)
    riskItems: RiskItem[]
    keyMetrics: KeyMetric[]
    jobConfidence: number | null
    jobTargetPrice: number | null
    jobStopLoss: number | null

    // Streaming Report State (for typewriter effect)
    streamingSections: Record<string, StreamingSectionState>

    // Milestones for chat display
    milestones: MilestoneMessage[]

    // Debate messages (transient, for battle view)
    debateMessages: Record<string, DebateMessage[]>
    debateScrollTick: number

    // Chat messages (persisted across route changes)
    chatMessages: ChatMessage[]

    // Logs (kept for system messages only)
    logs: LogEntry[]

    // Loading States
    isAnalyzing: boolean
    isConnected: boolean
    analysisRunState: 'idle' | 'running' | 'completed' | 'failed'
    analysisRunError: string | null
    analysisOvertimeNotice: string | null

    // Current analysis horizon (for badge display)
    currentHorizon: string | null

    // DAV-1429: horizons observed in the running task ("dual" from the shared
    // analyst stage, plus "short"/"medium" from the per-horizon runs).
    seenHorizons: string[]
    // DAV-1429: true once the running task is known to cover both horizons.
    isDualHorizon: boolean

    // Actions
    setCurrentJobId: (jobId: string | null) => void
    setCurrentSymbol: (symbol: string) => void
    setJobStatus: (status: JobStatus | null) => void
    updateAgentStatus: (event: AgentStatusEvent) => void
    updateAgentSnapshot: (event: AgentSnapshotEvent) => void
    addAgentReport: (event: AgentReportEvent) => void
    addReportChunk: (event: ReportChunkEvent) => void
    // DAV-1429: the same report sections split per horizon, so a dual run can be
    // displayed per horizon while it streams (the merged buffers interleave both
    // horizons and cannot be attributed after the fact).
    streamingHorizonSections: Record<string, Partial<Record<string, string>>>
    addAgentToken: (event: AgentTokenEvent) => void
    addMilestone: (event: AgentMilestoneEvent) => void
    addLog: (log: LogEntry) => void
    setReport: (report: AnalysisReport | null) => void
    setStructuredData: (data: {
        riskItems?: RiskItem[]
        keyMetrics?: KeyMetric[]
        confidence?: number | null
        targetPrice?: number | null
        stopLoss?: number | null
    }) => void
    setIsAnalyzing: (isAnalyzing: boolean) => void
    setIsConnected: (isConnected: boolean) => void
    setAnalysisRunState: (state: 'idle' | 'running' | 'completed' | 'failed', error?: string | null) => void
    setAnalysisOvertimeNotice: (notice: string | null) => void
    setCurrentHorizon: (horizon: string | null) => void
    noteHorizonEvent: (horizon: string | null) => void
    resetHorizonTracking: () => void
    addChatMessage: (message: ChatMessage) => void
    appendToChatMessage: (id: string, chunk: string) => void
    setMessageContent: (id: string, content: string) => void
    markAgentMessagesComplete: (msgIds?: string[]) => void
    addDebateMessage: (msg: DebateMessage) => void
    appendDebateToken: (debate: string, agent: string, round: number, token: string, horizon?: string) => void
    clearChatMessages: () => void
    clearSession: () => void
    reset: () => void
}

const initialAgents: Agent[] = [
    // Analyst Team
    { id: 'market', name: 'Market Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'social', name: 'Social Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'news', name: 'News Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'fundamentals', name: 'Fundamentals Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'macro', name: 'Macro Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'smart_money', name: 'Smart Money Analyst', team: 'Analyst Team', status: 'pending' },
    { id: 'volume_price', name: 'Volume Price Analyst', team: 'Analyst Team', status: 'pending' },

    // Research Team
    { id: 'bull', name: 'Bull Researcher', team: 'Research Team', status: 'pending' },
    { id: 'bear', name: 'Bear Researcher', team: 'Research Team', status: 'pending' },
    { id: 'research_manager', name: 'Research Manager', team: 'Research Team', status: 'pending' },

    // Trading Team
    { id: 'trader', name: 'Trader', team: 'Trading Team', status: 'pending' },

    // Risk Management
    { id: 'aggressive', name: 'Aggressive Analyst', team: 'Risk Management', status: 'pending' },
    { id: 'conservative', name: 'Conservative Analyst', team: 'Risk Management', status: 'pending' },
    { id: 'neutral', name: 'Neutral Analyst', team: 'Risk Management', status: 'pending' },

    // Portfolio Management
    { id: 'portfolio_manager', name: 'Portfolio Manager', team: 'Portfolio Management', status: 'pending' },
]

// Debounced localStorage storage to avoid blocking the main thread on every token
function createDebouncedStorage(delay = 800) {
    let pending: [string, string] | null = null
    let timer: ReturnType<typeof setTimeout> | null = null
    return {
        getItem: (name: string) => localStorage.getItem(name),
        setItem: (name: string, value: string) => {
            pending = [name, value]
            if (timer) clearTimeout(timer)
            timer = setTimeout(() => {
                if (pending) { localStorage.setItem(pending[0], pending[1]); pending = null }
                timer = null
            }, delay)
        },
        removeItem: (name: string) => {
            pending = null
            if (timer) { clearTimeout(timer); timer = null }
            localStorage.removeItem(name)
        },
    }
}
const debouncedStorage = createDebouncedStorage()

// ─── DAV-1429 dual-horizon bookkeeping ──────────────────────────────────────
// The collaboration header/cards used to read the last `agent.horizon_start`
// value, which a dual run overwrites (short then medium, same second) and which
// is cleared at job.completed — so a dual task ended up labelled/valued 中线.
// The store now latches the SET of horizons a run covers so display never
// depends on event order. Everything below is runtime-only (not persisted).

const KNOWN_HORIZONS = ['short', 'medium'] as const
type KnownHorizon = (typeof KNOWN_HORIZONS)[number]
const isKnownHorizon = (value: string): value is KnownHorizon =>
    (KNOWN_HORIZONS as readonly string[]).includes(value)

// `dual` is the shared-analyst-stage marker; `all` is the shared method that
// fans out to both horizons. Either one alone means "this task is dual".
const DUAL_MARKERS = ['dual', 'all'] as const

/** Read the horizon values an SSE payload carries, if any (all optional). */
function horizonsOf(event: unknown): Array<string | null | undefined> {
    if (!event || typeof event !== 'object') return []
    const record = event as Record<string, unknown>
    const values: Array<string | null | undefined> = [
        record.horizon as string | null | undefined,
        typeof record.horizons === 'string' ? (record.horizons as string) : undefined,
    ]
    if (Array.isArray(record.horizons)) {
        values.push(...(record.horizons as Array<string | null | undefined>))
    }
    return values
}

function mergeHorizons(
    state: Pick<AnalysisState, 'seenHorizons' | 'isDualHorizon'>,
    values: Array<string | null | undefined>,
): Partial<AnalysisState> {
    const next = new Set(state.seenHorizons)
    let isDualHorizon = state.isDualHorizon
    let changed = false

    for (const raw of values) {
        const value = typeof raw === 'string' ? raw.trim().toLowerCase() : ''
        if (!value) continue
        if (DUAL_MARKERS.includes(value as (typeof DUAL_MARKERS)[number])) {
            if (!isDualHorizon) {
                isDualHorizon = true
                changed = true
            }
            continue
        }
        if (!isKnownHorizon(value)) continue
        if (!next.has(value)) {
            next.add(value)
            changed = true
        }
    }

    // Both per-horizon passes observed => the task is dual even if no marker
    // arrived (e.g. the shared stage was skipped).
    let short = false
    let medium = false
    for (const value of next) {
        if (value === 'short') short = true
        else if (value === 'medium') medium = true
    }
    if (short && medium && !isDualHorizon) {
        isDualHorizon = true
        changed = true
    }

    if (!changed) return {}
    return { seenHorizons: Array.from(next), isDualHorizon }
}

/** Horizon latch for the events the store receives directly (DAV-1429). */
function noteHorizonsFromEvent(
    state: Pick<AnalysisState, 'seenHorizons' | 'isDualHorizon'>,
    event: unknown,
): Partial<AnalysisState> {
    return mergeHorizons(state, horizonsOf(event))
}

/** DAV-1429: does this finished report cover both horizons? */
export function isDualHorizonReport(report: AnalysisReport | null | undefined): boolean {
    if (!report) return false
    if (report.mode === 'single_horizon') return false
    if (report.mode === 'dual_horizon') return true
    if (Array.isArray(report.requested_horizons) && report.requested_horizons.length > 1) return true
    if (report.horizon_status) {
        const requested = Object.keys(report.horizon_status).filter(isKnownHorizon)
        if (requested.length > 1) return true
    }
    return false
}

export const useAnalysisStore = create<AnalysisState>()(persist((set) => ({
    currentJobId: null,
    currentSymbol: '000001.SH',
    jobStatus: null,
    agents: initialAgents,
    report: null,
    riskItems: [],
    keyMetrics: [],
    jobConfidence: null,
    jobTargetPrice: null,
    jobStopLoss: null,
    streamingSections: {},
    streamingHorizonSections: {},
    milestones: [],
    debateMessages: {},
        debateScrollTick: 0,
    chatMessages: createInitialChatMessages(),
    logs: [],
    isAnalyzing: false,
    isConnected: false,
    analysisRunState: 'idle',
    analysisRunError: null,
    analysisOvertimeNotice: null,
    currentHorizon: null,
    seenHorizons: [],
    isDualHorizon: false,

    setCurrentJobId: (jobId) => set({ currentJobId: jobId }),

    setCurrentSymbol: (symbol) => set({ currentSymbol: symbol }),

    setJobStatus: (status) => set({ jobStatus: status }),

    updateAgentStatus: (event) => set((state) => ({
        ...noteHorizonsFromEvent(state, event),
        agents: state.agents.map(agent => {
            if (agent.name !== event.agent) return agent
            const updates: Partial<Agent> = { status: event.status }
            if (event.status === 'in_progress' && !agent.startedAt) updates.startedAt = Date.now()
            if ((event.status === 'completed' || event.status === 'skipped') && !agent.finishedAt) updates.finishedAt = Date.now()
            return { ...agent, ...updates }
        })
    })),

    updateAgentSnapshot: (event) => set((state) => {
        const agentMap = new Map(event.agents.map(a => [a.agent, a.status]))
        return {
            ...noteHorizonsFromEvent(state, event),
            agents: state.agents.map(agent => ({
                ...agent,
                status: agentMap.get(agent.name) || agent.status
            }))
        }
    }),

    addAgentReport: (event) => set((state) => ({
        report: {
            ...state.report,
            [event.section]: event.content
        } as AnalysisReport
    })),

    // 处理报告分片（支持打字机效果）
    addReportChunk: (event) => set((state) => {
        const { section, chunk, is_complete } = event
        // DAV-1429: dual-horizon runs tag every chunk with its horizon ("dual" for
        // the shared analyst stage), so this is where a task is recognised as dual
        // before any report exists.
        const horizonUpdate = noteHorizonsFromEvent(state, event)
        // `ReportChunkEvent` does not declare `horizon` yet, but dual runs emit it.
        const rawHorizon = (event as unknown as { horizon?: unknown }).horizon
        const horizonKey = typeof rawHorizon === 'string' ? rawHorizon.trim().toLowerCase() : ''
        const horizonSections = horizonKey && chunk
            ? {
                ...state.streamingHorizonSections,
                [section]: {
                    ...state.streamingHorizonSections[section],
                    [horizonKey]: (state.streamingHorizonSections[section]?.[horizonKey] ?? '') + chunk,
                },
            }
            : state.streamingHorizonSections
        const current = state.streamingSections[section] || {
            buffer: '',
            displayed: '',
            isTyping: false,
            isComplete: false
        }

        if (is_complete) {
            // 完成时，确保显示完整内容
            return {
                ...horizonUpdate,
                streamingHorizonSections: horizonSections,
                streamingSections: {
                    ...state.streamingSections,
                    [section]: {
                        ...current,
                        buffer: current.buffer,
                        displayed: current.buffer,
                        isTyping: false,
                        isComplete: true
                    }
                }
            }
        }

        // 追加到缓冲区
        const newBuffer = current.buffer + chunk
        return {
            ...horizonUpdate,
            streamingHorizonSections: horizonSections,
            streamingSections: {
                ...state.streamingSections,
                [section]: {
                    ...current,
                    buffer: newBuffer,
                    displayed: newBuffer, // 直接显示完整缓冲区（打字机效果由组件控制）
                    isTyping: true,
                    isComplete: false
                }
            }
        }
    }),

    addAgentToken: (event) => set((state) => {
        const { report: section, token } = event
        if (!section) return state

        const current = state.streamingSections[section] || {
            buffer: '',
            displayed: '',
            isTyping: false,
            isComplete: false
        }

        const newBuffer = current.buffer + token
        return {
            streamingSections: {
                ...state.streamingSections,
                [section]: {
                    ...current,
                    buffer: newBuffer,
                    displayed: newBuffer,
                    isTyping: true,
                    isComplete: false
                }
            }
        }
    }),

    // 添加里程碑消息（用于对话框显示）
    addMilestone: (event) => set((state) => {
        const milestone: MilestoneMessage = {
            id: `${Date.now()}-${Math.random()}`,
            stage: event.stage,
            title: event.title,
            summary: event.summary,
            timestamp: event.timestamp
        }
        return {
            milestones: [...state.milestones, milestone]
        }
    }),

    // 添加聊天记录（持久化）
    addChatMessage: (message) => set((state) => ({
        chatMessages: [...state.chatMessages, message]
    })),

    // 追加内容到已有消息（用于流式报告 chunk 更新）
    appendToChatMessage: (id, chunk) => set((state) => ({
        chatMessages: state.chatMessages.map(m =>
            m.id === id ? { ...m, content: m.content + chunk } : m
        )
    })),

    setMessageContent: (id, content) => set((state) => ({
        chatMessages: state.chatMessages.map(m =>
            m.id === id ? { ...m, content } : m
        )
    })),

    // 批量标记 agent 消息为已完成；不传 msgIds 则标记所有 agent assistant 消息
    markAgentMessagesComplete: (msgIds?: string[]) => set((state) => ({
        chatMessages: state.chatMessages.map(m => {
            if (m.role !== 'assistant' || !m.agent || m.complete) return m
            if (msgIds && !msgIds.includes(m.id)) return m
            return { ...m, complete: true }
        })
    })),

    // upsert: 同 agent+round 则替换（流式结束时用完整内容覆盖）
    addDebateMessage: (msg) => set((state) => {
        const key = msg.debate
        const existing = state.debateMessages[key] || []
        const idx = existing.findIndex(m => m.agent === msg.agent && m.round === msg.round)
        const updated = idx >= 0
            ? existing.map((m, i) => i === idx ? msg : m)
            : [...existing, msg]
        return {
            debateMessages: { ...state.debateMessages, [key]: updated }
        }
    }),

    // 流式 token 追加：找到已有消息则追加 content，否则创建新消息
    appendDebateToken: (debate, agent, round, token, horizon) => set((state) => {
        const key = debate
        const tick = state.debateScrollTick + 1
        const existing = state.debateMessages[key] || []
        const idx = existing.findIndex(m => m.agent === agent && m.round === round)
        if (idx >= 0) {
            const updated = existing.map((m, i) =>
                i === idx ? { ...m, content: m.content + token } : m
            )
            return { debateMessages: { ...state.debateMessages, [key]: updated }, debateScrollTick: tick }
        }
        const isVerdict = round === -1
        return {
            debateMessages: {
                ...state.debateMessages,
                [key]: [...existing, { debate: debate as 'research' | 'risk', agent, round, content: token, isVerdict, horizon }],
            },
            debateScrollTick: tick,
        }
    }),

    // 清空聊天记录
    clearChatMessages: () => set({
        chatMessages: createInitialChatMessages()
    }),

    clearSession: () => set({
        currentJobId: null,
        currentSymbol: '000001.SH',
        jobStatus: null,
        agents: initialAgents.map(a => ({ ...a, status: 'pending' })),
        report: null,
        riskItems: [],
        keyMetrics: [],
        jobConfidence: null,
        jobTargetPrice: null,
        jobStopLoss: null,
        streamingSections: {},
        streamingHorizonSections: {},
        debateMessages: {},
        debateScrollTick: 0,
        milestones: [],
        chatMessages: createInitialChatMessages(),
        logs: [],
        isAnalyzing: false,
        isConnected: false,
        analysisRunState: 'idle',
        analysisRunError: null,
        analysisOvertimeNotice: null,
        currentHorizon: null,
        seenHorizons: [],
        isDualHorizon: false,
    }),

    addLog: (log) => set((state) => ({
        logs: [log, ...state.logs].slice(0, 100)
    })),

    setReport: (report) => set((state) => ({
        report,
        currentSymbol: report?.symbol || state.currentSymbol,
        // DAV-1429: a finished dual report doubles as the record that this task
        // covered two horizons, so the header/cards stay correct after the run's
        // horizon events are gone (and after the report is reopened).
        ...(isDualHorizonReport(report)
            ? { seenHorizons: state.seenHorizons.includes('dual') ? state.seenHorizons : ['dual', ...state.seenHorizons], isDualHorizon: true }
            : null),
    })),

    setStructuredData: (data) => set({
        riskItems: data.riskItems ?? [],
        keyMetrics: data.keyMetrics ?? [],
        jobConfidence: data.confidence ?? null,
        jobTargetPrice: data.targetPrice ?? null,
        jobStopLoss: data.stopLoss ?? null,
    }),

    setIsAnalyzing: (isAnalyzing) => set({ isAnalyzing }),

    setIsConnected: (isConnected) => set({ isConnected }),

    setAnalysisRunState: (analysisRunState, error = null) => set({
        analysisRunState,
        analysisRunError: analysisRunState === 'failed' ? error : null,
        ...(analysisRunState === 'running' ? {} : { analysisOvertimeNotice: null }),
    }),

    setAnalysisOvertimeNotice: (analysisOvertimeNotice) => set({ analysisOvertimeNotice }),

    // DAV-1429: `currentHorizon` is last-write-wins (a dual run emits
    // short-then-medium inside one second), so it is no longer used for display.
    // Every horizon the run reports is latched separately instead.
    setCurrentHorizon: (horizon) => set((state) => ({
        currentHorizon: horizon,
        ...mergeHorizons(state, [horizon]),
    })),

    // DAV-1429: the header must stop depending on the LAST agent.horizon_start.
    // A dual run reports horizon "dual" for the shared analyst stage and then
    // fires short/medium within the same second, so latch the fact that more
    // than one horizon is in play and never let event order decide the label.
    noteHorizonEvent: (horizon) => set((state) => mergeHorizons(state, [horizon])),

    resetHorizonTracking: () => set({ seenHorizons: [], isDualHorizon: false }),

    reset: () => set((state) => ({
        currentJobId: null,
        currentSymbol: state.currentSymbol,
        jobStatus: null,
        agents: initialAgents.map(a => ({ ...a, status: 'pending' })),
        report: null,
        riskItems: [],
        keyMetrics: [],
        jobConfidence: null,
        jobTargetPrice: null,
        jobStopLoss: null,
        streamingSections: {},
        streamingHorizonSections: {},
        debateMessages: {},
        debateScrollTick: 0,
        milestones: [],
        // 注意：reset时不清空chatMessages，保持对话历史
        logs: [],
        isAnalyzing: false,
        isConnected: false,
        analysisRunState: 'idle',
        analysisRunError: null,
        analysisOvertimeNotice: null,
        currentHorizon: null,
        seenHorizons: [],
        isDualHorizon: false,
    }))
}), {
    name: 'tradingagents-analysis',
    version: 1,
    storage: createJSONStorage(() => debouncedStorage),
    partialize: (state) => ({
        currentSymbol: state.currentSymbol,
        report: state.report,
        riskItems: state.riskItems,
        keyMetrics: state.keyMetrics,
        jobConfidence: state.jobConfidence,
        jobTargetPrice: state.jobTargetPrice,
        jobStopLoss: state.jobStopLoss,
        // Drop transient indicators and pure agent placeholders.  Preserve any
        // partial agent content, but mark it complete so hydration cannot show
        // a stale "still writing" state.
        chatMessages: reconcilePersistedChatMessages(
            state.chatMessages.filter(m => !m.content.startsWith('__')),
        ),
    }),
    merge: (persistedState, currentState) => {
        const persisted = (persistedState ?? {}) as Partial<AnalysisState>
        const restoredChatMessages = reconcilePersistedChatMessages(persisted.chatMessages ?? [])
        return {
            ...currentState,
            ...persisted,
            currentJobId: null,
            jobStatus: null,
            agents: initialAgents.map(a => ({ ...a, status: 'pending' })),
            streamingSections: {},
            streamingHorizonSections: {},
            debateMessages: {},
            debateScrollTick: 0,
            milestones: [],
            logs: [],
            isAnalyzing: false,
            isConnected: false,
            analysisRunState: 'idle',
            analysisRunError: null,
            analysisOvertimeNotice: null,
            chatMessages: restoredChatMessages.length ? restoredChatMessages : currentState.chatMessages,
        }
    },
}))
