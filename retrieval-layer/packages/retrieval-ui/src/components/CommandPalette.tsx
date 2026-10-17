import { Dialog } from '@base-ui/react/dialog'
import { useEffect, useId, useRef, useState, type KeyboardEvent } from 'react'
import { createPaletteController, highlightSegments, IDLE_STATE, moveIndex, type PaletteController, type PaletteItem, type PaletteState } from '../logic/palette.ts'
import type { OpenLocation, RetrievalClient } from '../types.ts'
import { cx, FOCUS_RING, formatRange } from './shared.ts'

export interface CommandPaletteProps {
  client: RetrievalClient
  onOpenLocation: OpenLocation
  // Called when a recall (chat history) result is chosen; recall results are not file locations.
  onOpenRecall?: (hit: { sessionId: string; turnIndex: number }) => void
  // Letter pressed together with Cmd/Ctrl. Pass null to disable the global shortcut.
  hotkey?: string | null
  // Controlled open state; omit for uncontrolled.
  open?: boolean
  onOpenChange?: (open: boolean) => void
  // The built-in trigger button is the focus-return target when used. Hide it if the host has its own.
  hideTrigger?: boolean
  className?: string
}

const SNIPPET_PREVIEW_LINES = 4
const MODE_LABEL = { search: 'Search', def: 'Definitions', recall: 'Chat recall' } as const

function Highlighted({ text, term }: { text: string; term: string }) {
  return (
    <>
      {highlightSegments(text, term).map((s, i) =>
        s.match ? (
          <mark key={i} className="rounded-sm bg-yellow-200 px-0.5 text-slate-900 dark:bg-yellow-300">
            {s.text}
          </mark>
        ) : (
          <span key={i}>{s.text}</span>
        ),
      )}
    </>
  )
}

function Snippet({ text, term }: { text: string; term: string }) {
  const all = text.replace(/\r\n?/g, '\n').replace(/\n+$/, '').split('\n')
  const shown = all.slice(0, SNIPPET_PREVIEW_LINES)
  return (
    <pre className="mt-1 max-w-full overflow-x-auto whitespace-pre font-mono text-xs leading-5 text-slate-700 dark:text-slate-300">
      <code>
        {shown.map((line, i) => (
          <span key={i} className="block">
            <Highlighted text={line === '' ? ' ' : line} term={term} />
          </span>
        ))}
        {all.length > shown.length && <span className="block text-slate-600 dark:text-slate-400">{`… ${all.length - shown.length} more lines`}</span>}
      </code>
    </pre>
  )
}

function ItemHeader({ item, term }: { item: PaletteItem; term: string }) {
  if (item.kind === 'recall') {
    return <span className="font-mono text-sm [overflow-wrap:anywhere]">{`${item.role} · session ${item.sessionId} · turn ${item.turnIndex}`}</span>
  }
  return (
    <span className="flex flex-wrap items-baseline gap-x-2">
      <span className="font-mono text-sm font-medium [overflow-wrap:anywhere]">
        <Highlighted text={`${item.path}:${formatRange(item.line, item.endLine)}`} term={term} />
      </span>
      {item.symbol && <span className="rounded bg-slate-200 px-1.5 font-mono text-xs text-slate-900 dark:bg-slate-700 dark:text-slate-100">{item.symbol}</span>}
    </span>
  )
}

export function CommandPalette({ client, onOpenLocation, onOpenRecall, hotkey = 'k', open: openProp, onOpenChange, hideTrigger = false, className }: CommandPaletteProps) {
  const [openState, setOpenState] = useState(false)
  const open = openProp ?? openState
  const [value, setValue] = useState('')
  const [state, setState] = useState<PaletteState>(IDLE_STATE)
  const [active, setActive] = useState(-1)
  const inputRef = useRef<HTMLInputElement>(null)
  const controllerRef = useRef<PaletteController | null>(null)
  const uid = useId()
  const listId = `${uid}-list`
  const optionId = (i: number) => `${uid}-opt-${i}`

  const setOpen = (next: boolean) => {
    if (openProp === undefined) setOpenState(next)
    onOpenChange?.(next)
  }
  const setOpenRef = useRef(setOpen)
  setOpenRef.current = setOpen
  const openRef = useRef(open)
  openRef.current = open

  useEffect(() => {
    const controller = createPaletteController(client, setState)
    controllerRef.current = controller
    return () => controller.dispose()
  }, [client])

  useEffect(() => {
    if (hotkey === null) return
    const onKeyDown = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === hotkey.toLowerCase()) {
        e.preventDefault()
        setOpenRef.current(!openRef.current)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [hotkey])

  useEffect(() => {
    if (!open) {
      setValue('')
      controllerRef.current?.setInput('')
    }
  }, [open])

  useEffect(() => {
    setActive(state.items.length > 0 ? 0 : -1)
  }, [state.items])

  useEffect(() => {
    if (active >= 0) document.getElementById(optionId(active))?.scrollIntoView({ block: 'nearest' })
  }, [active])

  const choose = (item: PaletteItem) => {
    if (item.kind === 'location') {
      setOpen(false)
      onOpenLocation(item.path, item.line)
    } else if (onOpenRecall) {
      setOpen(false)
      onOpenRecall({ sessionId: item.sessionId, turnIndex: item.turnIndex })
    }
  }

  const onInputKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.nativeEvent.isComposing) return
    if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => moveIndex(i, e.key === 'ArrowDown' ? 1 : -1, state.items.length))
    } else if (e.key === 'Enter') {
      const item = state.items[active]
      if (item) {
        e.preventDefault()
        choose(item)
      }
    }
  }

  const canRecall = typeof client.recall === 'function'
  let announcement = ''
  if (state.status === 'loading') announcement = 'Searching'
  else if (state.status === 'error') announcement = 'Search failed'
  else if (state.status === 'ready') announcement = state.items.length === 0 ? 'No results' : `${state.items.length} result${state.items.length === 1 ? '' : 's'}`

  return (
    <Dialog.Root open={open} onOpenChange={setOpen}>
      {!hideTrigger && (
        <Dialog.Trigger
          className={cx(
            'inline-flex min-h-11 items-center gap-2 rounded-lg border px-3 text-sm',
            'border-slate-300 bg-white text-slate-900 hover:bg-slate-100 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100 dark:hover:bg-slate-800',
            FOCUS_RING,
            className,
          )}
        >
          <span>Search workspace</span>
          {hotkey !== null && (
            <kbd className="hidden rounded border border-slate-300 px-1.5 font-mono text-xs text-slate-700 sm:inline dark:border-slate-600 dark:text-slate-300">
              {`⌘/Ctrl ${hotkey.toUpperCase()}`}
            </kbd>
          )}
        </Dialog.Trigger>
      )}
      <Dialog.Portal>
        <Dialog.Backdrop className="fixed inset-0 z-40 bg-slate-950/50 motion-safe:transition-opacity data-[ending-style]:opacity-0 data-[starting-style]:opacity-0" />
        <Dialog.Popup
          initialFocus={inputRef}
          className={cx(
            'fixed left-1/2 top-[8vh] z-50 flex max-h-[84vh] w-[min(42rem,calc(100vw-1rem))] -translate-x-1/2 flex-col overflow-hidden rounded-xl border shadow-2xl',
            'border-slate-300 bg-white text-slate-900 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100',
            'motion-safe:transition-[opacity,transform] data-[ending-style]:opacity-0 data-[starting-style]:opacity-0 motion-safe:data-[ending-style]:scale-98 motion-safe:data-[starting-style]:scale-98',
          )}
        >
          <Dialog.Title className="sr-only">Search workspace</Dialog.Title>
          <Dialog.Description className="sr-only">
            {`Type to search code and docs. Start with @ to find a definition${canRecall ? ' or # to search earlier chats' : ''}. Use up and down arrows to choose, Enter to open, Escape to close.`}
          </Dialog.Description>

          <div className="border-b border-slate-300 p-2 dark:border-slate-600">
            <input
              ref={inputRef}
              type="text"
              role="combobox"
              aria-label="Search workspace"
              aria-expanded={state.items.length > 0}
              aria-controls={listId}
              aria-autocomplete="list"
              aria-activedescendant={active >= 0 ? optionId(active) : undefined}
              autoComplete="off"
              spellCheck={false}
              placeholder={`Search code and docs, @ for definitions${canRecall ? ', # for chats' : ''}`}
              value={value}
              onChange={(e) => {
                setValue(e.target.value)
                controllerRef.current?.setInput(e.target.value)
              }}
              onKeyDown={onInputKeyDown}
              className={cx(
                'min-h-11 w-full rounded-lg bg-transparent px-3 text-base placeholder:text-slate-600 dark:placeholder:text-slate-400',
                FOCUS_RING,
              )}
            />
            {state.term !== '' && (
              <p className="px-3 pt-1 text-xs text-slate-600 dark:text-slate-400" aria-hidden="true">
                {MODE_LABEL[state.mode]}
                {state.status === 'loading' ? ' · searching…' : ''}
              </p>
            )}
          </div>

          <div role="status" aria-live="polite" className="sr-only">
            {announcement}
          </div>

          <div className="min-h-0 flex-1 overflow-y-auto">
            {state.status === 'error' && (
              <p role="alert" className="m-3 rounded-lg border border-red-700 bg-red-50 p-3 text-sm text-red-900 dark:border-red-400 dark:bg-red-950 dark:text-red-100">
                {`Search failed: ${state.error}`}
              </p>
            )}
            {state.status === 'ready' && state.items.length === 0 && (
              <p className="p-4 text-sm text-slate-700 dark:text-slate-300">{`No results for “${state.term}”.`}</p>
            )}
            {state.status === 'idle' && <p className="p-4 text-sm text-slate-700 dark:text-slate-300">{`Type to search. Prefix @ for definitions${canRecall ? ', # for earlier chats' : ''}.`}</p>}
            {state.status === 'loading' && state.items.length === 0 && <p className="p-4 text-sm text-slate-700 dark:text-slate-300">Searching{'…'}</p>}
            <ul id={listId} role="listbox" aria-label="Results" className={cx('m-0 list-none p-1', state.items.length === 0 && 'hidden')}>
              {state.items.map((item, i) => {
                const disabled = item.kind === 'recall' && !onOpenRecall
                return (
                  <li
                    key={item.key}
                    id={optionId(i)}
                    role="option"
                    aria-selected={i === active}
                    aria-disabled={disabled || undefined}
                    onMouseMove={() => i !== active && setActive(i)}
                    onClick={() => choose(item)}
                    className={cx(
                      'min-h-11 cursor-pointer rounded-lg px-3 py-2',
                      i === active ? 'bg-blue-100 dark:bg-blue-950' : 'bg-transparent',
                      i === active && 'outline-2 -outline-offset-2 outline-blue-700 dark:outline-blue-400',
                      disabled && 'cursor-default',
                    )}
                  >
                    <ItemHeader item={item} term={state.term} />
                    <Snippet text={item.snippet} term={state.term} />
                  </li>
                )
              })}
            </ul>
          </div>

          <div className="flex items-center justify-between gap-2 border-t border-slate-300 px-3 py-2 text-xs text-slate-700 dark:border-slate-600 dark:text-slate-300">
            <span>{'↑↓ navigate · Enter open · Esc close'}</span>
          </div>
        </Dialog.Popup>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
