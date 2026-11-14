// DAV-2453: user-selectable thinking level (quick / deep).
// "follow" = follow the model: backend stores NULL and sends no effort parameter.

export type ReasoningEffort = 'low' | 'medium' | 'high'
export type ReasoningEffortChoice = 'follow' | ReasoningEffort

export const REASONING_EFFORTS: ReasoningEffort[] = ['low', 'medium', 'high']

export const REASONING_EFFORT_LABELS: Record<ReasoningEffortChoice, string> = {
    follow: '跟随模型（默认）',
    low: '低',
    medium: '中',
    high: '高',
}

export const UNSUPPORTED_REASONING_TEXT = '当前模型不支持调节'

// /v1/models catalog: model id -> selectable levels; null = entry has no readable levels.
export type ReasoningLevelCatalog = Record<string, string[] | null>

export interface ReasoningEffortOptions {
    adjustable: boolean
    levels: ReasoningEffort[]
    // Shown next to a disabled selector.
    hint: string | null
}

const isEffort = (v: unknown): v is ReasoningEffort =>
    typeof v === 'string' && (REASONING_EFFORTS as string[]).includes(v)

/**
 * Selectable levels for one model.
 * - catalog not loaded / model missing / no levels / only one level / not readable
 *   -> not adjustable (no "looks adjustable but is not" option).
 */
export function resolveReasoningEffortOptions(
    model: string,
    catalog: ReasoningLevelCatalog | null | undefined,
): ReasoningEffortOptions {
    const raw = catalog && model ? catalog[model] : undefined
    const levels = Array.isArray(raw) ? REASONING_EFFORTS.filter((lv) => raw.includes(lv)) : []
    if (levels.length >= 2) return { adjustable: true, levels, hint: null }
    return { adjustable: false, levels: [], hint: UNSUPPORTED_REASONING_TEXT }
}

/** Coerce an untrusted value (API response / DOM value) to a valid choice. */
export function sanitizeReasoningEffortChoice(value: unknown): ReasoningEffortChoice {
    return isEffort(value) ? value : 'follow'
}

/**
 * Keep the current choice only if the model still offers it; otherwise fall back
 * to "follow". Leaves the choice alone while the catalog is unknown (null) so a
 * saved value is not wiped just because the catalog has not loaded yet.
 */
export function reconcileReasoningEffort(
    current: ReasoningEffortChoice,
    model: string,
    catalog: ReasoningLevelCatalog | null | undefined,
): ReasoningEffortChoice {
    if (catalog == null || current === 'follow') return current
    const opts = resolveReasoningEffortOptions(model, catalog)
    return opts.adjustable && opts.levels.includes(current) ? current : 'follow'
}

/** Value for PATCH /v1/config ("follow" clears the column back to NULL). */
export function toReasoningEffortPayload(choice: ReasoningEffortChoice): ReasoningEffortChoice {
    return sanitizeReasoningEffortChoice(choice)
}
