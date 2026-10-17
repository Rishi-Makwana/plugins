import { Tooltip } from '@base-ui/react/tooltip'
import { useState } from 'react'
import type { Citation, OpenLocation } from '../types.ts'
import { basename, cx, FOCUS_RING, formatRange } from './shared.ts'

export interface CitationChipsProps {
  citations: readonly Citation[]
  onOpenLocation: OpenLocation
  // Chips shown before the rest collapse into "+N more".
  maxVisible?: number
  className?: string
}

const chipClass = cx(
  'inline-flex min-h-11 max-w-full items-center rounded-lg border px-3 py-1 text-left font-mono text-sm',
  'border-slate-300 bg-slate-100 text-slate-900 hover:bg-slate-200',
  'dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:hover:bg-slate-700',
  'motion-safe:transition-colors',
  FOCUS_RING,
)

export function CitationChips({ citations, onOpenLocation, maxVisible = 4, className }: CitationChipsProps) {
  const [expanded, setExpanded] = useState(false)
  if (citations.length === 0) return null
  const limit = Math.max(1, maxVisible)
  const collapsible = citations.length > limit
  const visible = collapsible && !expanded ? citations.slice(0, limit) : citations
  const hidden = citations.length - visible.length

  return (
    <Tooltip.Provider delay={300}>
      <ul aria-label={`Sources (${citations.length})`} className={cx('m-0 flex list-none flex-wrap gap-2 p-0', className)}>
        {visible.map((c) => {
          const range = formatRange(c.startLine, c.endLine)
          return (
            <li key={`${c.path}:${c.startLine}:${c.endLine ?? ''}`} className="min-w-0 max-w-full">
              <Tooltip.Root>
                <Tooltip.Trigger
                  className={chipClass}
                  aria-label={`Open ${c.path} line ${range.replace('–', ' to ')}`}
                  onClick={() => onOpenLocation(c.path, c.startLine)}
                >
                  <span className="[overflow-wrap:anywhere]">{`${basename(c.path)}:${range}`}</span>
                </Tooltip.Trigger>
                <Tooltip.Portal>
                  <Tooltip.Positioner sideOffset={6} className="z-[60]">
                    <Tooltip.Popup className="max-w-[min(32rem,90vw)] rounded-md bg-slate-900 px-2 py-1 font-mono text-xs text-slate-50 [overflow-wrap:anywhere] motion-safe:transition-opacity data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 dark:bg-slate-100 dark:text-slate-900">
                      {c.path}:{range}
                      {c.symbol ? ` · ${c.symbol}` : ''}
                    </Tooltip.Popup>
                  </Tooltip.Positioner>
                </Tooltip.Portal>
              </Tooltip.Root>
            </li>
          )
        })}
        {collapsible && (
          <li>
            <button type="button" className={cx(chipClass, 'font-sans')} aria-expanded={expanded} onClick={() => setExpanded((v) => !v)}>
              {expanded ? 'Show fewer' : `+${hidden} more`}
            </button>
          </li>
        )}
      </ul>
    </Tooltip.Provider>
  )
}
