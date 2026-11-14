import {
    REASONING_EFFORT_LABELS,
    resolveReasoningEffortOptions,
    sanitizeReasoningEffortChoice,
    type ReasoningEffortChoice,
    type ReasoningLevelCatalog,
} from '@/utils/reasoningEffort'

interface Props {
    label: string
    model: string
    value: ReasoningEffortChoice
    catalog: ReasoningLevelCatalog | null
    onChange: (value: ReasoningEffortChoice) => void
    disabled?: boolean
}

// DAV-2453: thinking-level selector. Disabled (and explained) when the model
// cannot be adjusted; only the levels the model supports are listed.
export default function ReasoningEffortSelect({ label, model, value, catalog, onChange, disabled }: Props) {
    const opts = resolveReasoningEffortOptions(model, catalog)
    const choices: ReasoningEffortChoice[] = ['follow', ...opts.levels]
    const shown: ReasoningEffortChoice = opts.adjustable ? value : 'follow'
    return (
        <div>
            <label className="block text-sm font-medium text-slate-600 dark:text-slate-400 mb-2">{label}</label>
            <select
                className="input w-full"
                data-testid={`reasoning-effort-${label}`}
                value={shown}
                disabled={disabled || !opts.adjustable}
                onChange={(e) => onChange(sanitizeReasoningEffortChoice(e.target.value))}
            >
                {choices.map((c) => (
                    <option key={c} value={c}>
                        {REASONING_EFFORT_LABELS[c]}
                    </option>
                ))}
            </select>
            {opts.hint && <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">{opts.hint}</p>}
        </div>
    )
}
