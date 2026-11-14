import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import ReasoningEffortSelect from '@/components/ReasoningEffortSelect'
import {
    reconcileReasoningEffort,
    resolveReasoningEffortOptions,
    sanitizeReasoningEffortChoice,
    toReasoningEffortPayload,
    UNSUPPORTED_REASONING_TEXT,
    type ReasoningLevelCatalog,
} from '@/utils/reasoningEffort'
import { api } from '@/services/api'

const catalog: ReasoningLevelCatalog = {
    'multi-model': ['low', 'medium', 'high'],
    'one-level-model': ['high'],
    'plain-model': null,
}

const render = (model: string, value: 'follow' | 'low' | 'medium' | 'high' = 'follow') =>
    renderToStaticMarkup(
        React.createElement(ReasoningEffortSelect, {
            label: '快速模型',
            model,
            value,
            catalog,
            onChange: () => {},
        }),
    )

describe('thinking level: normal save', () => {
    it('lists follow + only the levels the model supports, enabled', () => {
        const opts = resolveReasoningEffortOptions('multi-model', catalog)
        expect(opts).toMatchObject({ adjustable: true, levels: ['low', 'medium', 'high'] })
        const html = render('multi-model', 'medium')
        expect(html).not.toContain('disabled')
        expect(html).toContain('跟随模型（默认）')
        expect(html).toContain('>低<')
        expect(html).toContain('>中<')
        expect(html).toContain('>高<')
    })

    it('maps the choice to the PATCH payload value ("follow" clears to NULL)', () => {
        expect(toReasoningEffortPayload('low')).toBe('low')
        expect(toReasoningEffortPayload('follow')).toBe('follow')
    })

    it('sends quick/deep levels in PATCH /v1/config body', async () => {
        const fetchMock = vi.fn().mockResolvedValue({
            ok: true,
            status: 200,
            headers: new Headers({ 'content-type': 'application/json' }),
            json: async () => ({}),
            text: async () => '{}',
        })
        vi.stubGlobal('fetch', fetchMock)
        vi.stubGlobal('localStorage', { getItem: () => null, setItem: () => {}, removeItem: () => {} })
        try {
            await api.updateConfig({ quick_reasoning_effort: 'low', deep_reasoning_effort: 'follow' })
        } finally {
            vi.unstubAllGlobals()
        }
        const [, init] = fetchMock.mock.calls[0]
        expect(JSON.parse(init.body)).toEqual({ quick_reasoning_effort: 'low', deep_reasoning_effort: 'follow' })
    })
})

describe('thinking level: invalid values are rejected client-side', () => {
    it('coerces anything outside the whitelist to follow', () => {
        for (const bad of ['none', 'xhigh', '', null, undefined, 3, 'HIGH; DROP']) {
            expect(sanitizeReasoningEffortChoice(bad)).toBe('follow')
        }
        expect(sanitizeReasoningEffortChoice('medium')).toBe('medium')
    })

    it('drops a selected level the model does not offer', () => {
        expect(reconcileReasoningEffort('low', 'one-level-model', catalog)).toBe('follow')
        expect(reconcileReasoningEffort('high', 'multi-model', catalog)).toBe('high')
        // catalog unknown: keep the saved value
        expect(reconcileReasoningEffort('high', 'multi-model', null)).toBe('high')
    })
})

describe('thinking level: unsupported models are disabled', () => {
    it.each(['one-level-model', 'plain-model', 'model-not-in-catalog', ''])(
        'disables the select and explains for %j',
        (model) => {
            expect(resolveReasoningEffortOptions(model, catalog).adjustable).toBe(false)
            const html = render(model, 'high')
            expect(html).toContain('disabled')
            expect(html).toContain(UNSUPPORTED_REASONING_TEXT)
            // a stale stored value is never shown as selected
            expect(html).not.toMatch(/<option[^>]*value="high"/)
        },
    )

    it('treats an unloaded catalog as not adjustable', () => {
        expect(resolveReasoningEffortOptions('multi-model', null).adjustable).toBe(false)
    })
})
