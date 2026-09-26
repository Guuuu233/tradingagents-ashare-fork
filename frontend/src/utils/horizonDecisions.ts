/**
 * DAV-1301: per-horizon decision display for dual-horizon reports.
 *
 * A dual-horizon report carries an independent conclusion per horizon
 * (短线/中线). Every surface that shows "the conclusion" must render both
 * instead of the single primary-horizon column value. The API exposes
 * `horizon_decisions` on list/detail rows; live `AnalysisReport` payloads
 * (SSE result) carry the same data inside `short_term` / `medium_term`
 * slices — {@link normalizeHorizonDecisions} accepts both shapes.
 */
import type { AnalysisHorizon, AnalysisReport, HorizonDecision } from '@/types'
import { localizeDirection } from '@/utils/reportText'

export type { HorizonDecision }

export const HORIZON_DECISION_LABELS: Record<string, string> = {
    short: '短线',
    medium: '中线',
}

const HORIZON_ORDER = ['short', 'medium']

export function horizonLabel(horizon?: string): string {
    return HORIZON_DECISION_LABELS[String(horizon || '')] || String(horizon || '档位')
}

const ACTION_LABELS: Record<string, string> = {
    BUY: '买入',
    ADD: '增持',
    SELL: '卖出',
    REDUCE: '减持',
    HOLD: '持有',
    WAIT: '观望',
    NO_TRADE: '不交易',
}

/** 研究经理原结论的中文写法；无记录时返回 null。 */
export function managerActionLabel(action?: string | null): string | null {
    const key = String(action || '').toUpperCase()
    return ACTION_LABELS[key] || null
}

function isDirectional(direction?: string | null): boolean {
    if (!direction) return false
    const d = String(direction).trim().toUpperCase()
    return d !== 'N/A' && d !== 'NA' && d !== '不适用'
}

/**
 * One horizon's display label, e.g. "卖出（看空）" / "不交易（看多）".
 * Status vocabulary per spec:
 *   slice failed            → 未完成
 *   INVALID_RUN/DATA_ERROR  → 无效运行
 *   ABSTAIN                 → 不交易（弃权）
 *   otherwise               → 动作（方向）
 */
export function formatHorizonDecisionLabel(hd: HorizonDecision): string {
    const status = String(hd.status || '').toLowerCase()
    if (status && status !== 'completed' && status !== 'complete') {
        if (status === 'failed' || status === 'error') return '未完成'
    }
    const analysis = String(hd.analysis_status || '').toUpperCase()
    if (analysis === 'INVALID_RUN' || analysis === 'DATA_ERROR') return '无效运行'
    if (analysis === 'ABSTAIN') return '不交易（弃权）'
    const action = String(hd.trade_action || '').toUpperCase()
    const actionLabel = ACTION_LABELS[action]
    const dir = isDirectional(hd.direction) ? localizeDirection(hd.direction) : null
    if (!actionLabel) {
        if (status === 'failed' || status === 'error') return '未完成'
        return dir ? `未记录（${dir}）` : '未记录'
    }
    return dir ? `${actionLabel}（${dir}）` : actionLabel
}

/**
 * Whether this horizon's final action was downgraded by the price-basis gate
 * (post-gate NO_TRADE while the manager had a directional verdict).
 */
export function isGateDowngraded(hd: HorizonDecision): boolean {
    const action = String(hd.trade_action || '').toUpperCase()
    if (!hd.gate_blocked) return false
    if (action !== 'NO_TRADE' && action !== 'WAIT') return false
    // ABSTAIN means adjudication stopped the horizon — never a price-gate
    // downgrade, so the note must not blame the gate.
    if (String(hd.analysis_status || '').toUpperCase() === 'ABSTAIN') return false
    const manager = String(hd.manager_action || '').toUpperCase()
    const directional = manager === 'BUY' || manager === 'SELL' || manager === 'HOLD'
    // 研究经理记录的动作必须与最终动作不同，否则没有降级可讲。
    return directional && (manager as string) !== action
}

/**
 * Downgrade note pinned under one horizon row, reusing the existing
 * WAIT / 价格门 vocabulary. Returns null when nothing was downgraded.
 */
export function buildHorizonDowngradeNote(hd: HorizonDecision): string | null {
    if (!isGateDowngraded(hd)) return null
    const manager = managerActionLabel(hd.manager_action)
    const finalLabel = String(hd.trade_action || '').toUpperCase() === 'WAIT' ? '观望' : '不交易'
    return `研究经理原结论${manager}，因报告中有价格无法核实来源，按规则降级为${finalLabel}`
}

interface SliceLike {
    status?: string | null
    analysis_status?: string | null
    trade_action?: string | null
    direction?: string | null
    decision_status?: {
        analysis_status?: string | null
        trade_action?: string | null
        direction?: string | null
        reason_codes?: string[] | null
        failed_checks?: string[] | null
    } | null
    reason_codes?: string[] | null
    price_basis_gate?: { status?: string } | null
    confidence?: number | null
    target_price?: number | null
    stop_loss_price?: number | null
    pre_gate_trade_action?: string | null
    manager_verdict?: { trade_action?: string; direction?: string } | null
    investment_debate_state?: { manager_verdict?: { trade_action?: string; direction?: string } | null } | null
}

/**
 * DAV-1301 rework: the manager's RECORDED action only —
 * `manager_verdict.trade_action` (slice → investment_debate_state) then
 * `pre_gate_trade_action`. Never inferred from direction/winner/position_pct:
 * a recorded WAIT or adjudication-abstained NO_TRADE would be misreported as
 * a directional action.
 */
function managerActionFromSlice(slice: SliceLike): string | null {
    const mv = slice.manager_verdict ?? slice.investment_debate_state?.manager_verdict
    const recorded = String(mv?.trade_action || '').toUpperCase()
    if (recorded) return recorded
    const pre = String(slice.pre_gate_trade_action || '').toUpperCase()
    if (pre === 'BUY' || pre === 'SELL' || pre === 'HOLD' || pre === 'WAIT' || pre === 'NO_TRADE') {
        return pre
    }
    return null
}

function horizonDecisionFromSlice(horizon: AnalysisHorizon, slice: SliceLike): HorizonDecision | null {
    const ds = slice.decision_status ?? null
    const analysis = String(slice.analysis_status ?? ds?.analysis_status ?? '').toUpperCase() || null
    const action = String(slice.trade_action ?? ds?.trade_action ?? '').toUpperCase() || null
    const direction = ds?.direction ?? slice.direction ?? null
    const reasonCodes = [
        ...(ds?.reason_codes ?? []),
        ...(ds?.failed_checks ?? []),
        ...(slice.reason_codes ?? []),
    ]
    const gateBlocked =
        slice.price_basis_gate?.status === 'blocked'
        || reasonCodes.some(c => c === 'price_basis_gate_blocked')
    const nonExecutable =
        ['INVALID_RUN', 'DATA_ERROR', 'ABSTAIN', 'PARTIAL'].includes(analysis || '')
        || ['NO_TRADE', 'WAIT'].includes(action || '')
    const entry: HorizonDecision = {
        horizon,
        status: slice.status ?? null,
        analysis_status: analysis,
        direction,
        trade_action: action,
        manager_action: managerActionFromSlice(slice),
        reason_codes: reasonCodes.length ? reasonCodes : null,
        gate_blocked: gateBlocked,
        non_executable: nonExecutable,
        confidence: nonExecutable ? null : (slice.confidence ?? null),
        target_price: nonExecutable ? null : (slice.target_price ?? null),
        stop_loss_price: nonExecutable ? null : (slice.stop_loss_price ?? null),
    }
    const hasSignal = Boolean(
        entry.status || entry.analysis_status || entry.trade_action || entry.direction || ds,
    )
    return hasSignal ? entry : null
}

interface HorizonDecisionSource {
    horizon_decisions?: HorizonDecision[] | null
    short_term?: SliceLike | null
    medium_term?: SliceLike | null
    result_data?: AnalysisReport | null
}

/**
 * Normalize any report-shaped object into per-horizon decisions.
 * Prefers the API `horizon_decisions` field; falls back to deriving it from
 * the `short_term` / `medium_term` slices of an AnalysisReport-shaped
 * payload (live SSE result or `ReportDetail.result_data`). Returns null for
 * single-horizon reports.
 */
export function normalizeHorizonDecisions(
    src?: HorizonDecisionSource | AnalysisReport | null,
): HorizonDecision[] | null {
    if (!src || typeof src !== 'object') return null
    const direct = (src as HorizonDecisionSource).horizon_decisions
    if (Array.isArray(direct) && direct.length > 1) {
        return HORIZON_ORDER
            .map(h => direct.find(d => d.horizon === h))
            .filter((d): d is HorizonDecision => Boolean(d))
            .concat(direct.filter(d => !HORIZON_ORDER.includes(String(d.horizon))))
    }
    const rd = (src as HorizonDecisionSource).result_data ?? (src as AnalysisReport)
    const out: HorizonDecision[] = []
    for (const [horizon, key] of [['short', 'short_term'], ['medium', 'medium_term']] as const) {
        const slice = (rd as AnalysisReport)?.[key] as SliceLike | undefined
        if (!slice || typeof slice !== 'object') continue
        const entry = horizonDecisionFromSlice(horizon, slice)
        if (entry) out.push(entry)
    }
    return out.length > 1 ? out : null
}

/** "短线：卖出（看空）｜中线：不交易（看多）" — null when not dual-horizon. */
export function formatDualHorizonDecisionSummary(
    src?: HorizonDecisionSource | AnalysisReport | HorizonDecision[] | null,
): string | null {
    const hds = Array.isArray(src) ? src : normalizeHorizonDecisions(src)
    if (!hds || hds.length < 2) return null
    return hds
        .map(hd => `${horizonLabel(hd.horizon)}：${formatHorizonDecisionLabel(hd)}`)
        .join('｜')
}
