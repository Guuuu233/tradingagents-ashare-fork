import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { describe, expect, it, vi } from 'vitest'

import { FetchedModelList } from '@/pages/Settings'

const MODELS = [
    'gpt-5-hydrogen-xhigh',
    'GPT-5-Hydrogen-Low',
    'claude-sonnet-4',
    'hydrogen-mini-xhigh',
    'gemini-2.5-pro',
]

type AnyElement = React.ReactElement<Record<string, unknown>>

function collectElements(node: React.ReactNode, out: AnyElement[] = []): AnyElement[] {
    if (Array.isArray(node)) {
        node.forEach((child) => collectElements(child, out))
        return out
    }
    if (React.isValidElement(node)) {
        const element = node as AnyElement
        out.push(element)
        collectElements(element.props.children as React.ReactNode, out)
    }
    return out
}

function renderTree(query: string, onQueryChange = vi.fn(), onSetQuick = vi.fn(), onSetDeep = vi.fn()) {
    const tree = FetchedModelList({
        models: MODELS,
        sourceUrl: 'http://proxy.local/v1/models',
        query,
        onQueryChange,
        onSetQuick,
        onSetDeep,
    })
    return { elements: collectElements(tree), onQueryChange, onSetQuick, onSetDeep }
}

function renderHtml(query: string, models: string[] = MODELS) {
    return renderToStaticMarkup(
        <FetchedModelList
            models={models}
            sourceUrl="http://proxy.local/v1/models"
            query={query}
            onQueryChange={() => {}}
            onSetQuick={() => {}}
            onSetDeep={() => {}}
        />,
    )
}

function visibleModels(query: string, models: string[] = MODELS): string[] {
    const html = renderHtml(query, models)
    return Array.from(html.matchAll(/<span>([^<]*)<\/span>/g), (match) => match[1]).filter((name) => models.includes(name))
}

describe('FetchedModelList filtering', () => {
    it('returns all models for empty or whitespace-only query', () => {
        expect(visibleModels('')).toEqual(MODELS)
        expect(visibleModels('   ')).toEqual(MODELS)
    })

    it('matches case-insensitive substrings', () => {
        expect(visibleModels('HYDROGEN')).toEqual([
            'gpt-5-hydrogen-xhigh',
            'GPT-5-Hydrogen-Low',
            'hydrogen-mini-xhigh',
        ])
    })

    it('requires every space-separated keyword (AND) and trims surrounding whitespace', () => {
        expect(visibleModels('  hydrogen   xhigh ')).toEqual([
            'gpt-5-hydrogen-xhigh',
            'hydrogen-mini-xhigh',
        ])
        expect(visibleModels('xhigh hydrogen gpt')).toEqual(['gpt-5-hydrogen-xhigh'])
    })

    it('returns an empty list when nothing matches and keeps original order', () => {
        expect(visibleModels('nonexistent-model')).toEqual([])
        expect(visibleModels('x', ['b-x', 'a-x'])).toEqual(['b-x', 'a-x'])
    })
})

describe('FetchedModelList', () => {
    it('shows search input, total count and all models without filter', () => {
        const html = renderHtml('')
        expect(html).toContain('placeholder="搜索模型，如 hydrogen xhigh"')
        expect(html).toContain('成功获取到 5 个模型')
        expect(html).not.toContain('显示')
        MODELS.forEach((model) => expect(html).toContain(`<span>${model}</span>`))
        expect(html).not.toContain('清除搜索')
        expect(html).toContain('max-h-40')
    })

    it('shows filtered count M / N and only matching models', () => {
        const html = renderHtml('hydrogen xhigh')
        expect(html).toContain('显示 2 / 5 个')
        expect(html).toContain('<span>gpt-5-hydrogen-xhigh</span>')
        expect(html).toContain('<span>hydrogen-mini-xhigh</span>')
        expect(html).not.toContain('<span>GPT-5-Hydrogen-Low</span>')
        expect(html).not.toContain('<span>claude-sonnet-4</span>')
        expect(html).toContain('aria-label="清除搜索"')
    })

    it('shows empty-state text when nothing matches', () => {
        const html = renderHtml('nonexistent')
        expect(html).toContain('显示 0 / 5 个')
        expect(html).toContain('没有匹配的模型')
        expect(html).not.toContain('设为常规')
    })

    it('clears the query via the X button and via Escape in the input', () => {
        const { elements, onQueryChange } = renderTree('hydrogen')
        const clearButton = elements.find((el) => el.props['aria-label'] === '清除搜索')
        expect(clearButton).toBeDefined()
        ;(clearButton!.props.onClick as () => void)()
        expect(onQueryChange).toHaveBeenLastCalledWith('')

        const input = elements.find((el) => el.type === 'input')
        expect(input).toBeDefined()
        const preventDefault = vi.fn()
        ;(input!.props.onKeyDown as (e: unknown) => void)({ key: 'Escape', preventDefault })
        expect(onQueryChange).toHaveBeenCalledTimes(2)
        expect(onQueryChange).toHaveBeenLastCalledWith('')
        ;(input!.props.onKeyDown as (e: unknown) => void)({ key: 'a', preventDefault })
        expect(onQueryChange).toHaveBeenCalledTimes(2)

        ;(input!.props.onChange as (e: unknown) => void)({ target: { value: 'claude' } })
        expect(onQueryChange).toHaveBeenLastCalledWith('claude')
    })

    it('keeps set-quick / set-deep actions bound to the filtered model', () => {
        const { elements, onSetQuick, onSetDeep } = renderTree('claude')
        const quick = elements.find((el) => el.props.title === '设为常规模型')
        const deep = elements.find((el) => el.props.title === '设为推理模型')
        ;(quick!.props.onClick as () => void)()
        ;(deep!.props.onClick as () => void)()
        expect(onSetQuick).toHaveBeenCalledWith('claude-sonnet-4')
        expect(onSetDeep).toHaveBeenCalledWith('claude-sonnet-4')
        expect(elements.filter((el) => el.props.title === '设为常规模型')).toHaveLength(1)
    })
})
