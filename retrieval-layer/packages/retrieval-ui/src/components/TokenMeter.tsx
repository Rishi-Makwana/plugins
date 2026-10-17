import { formatCost, formatTokens, type TurnUsage } from '../logic/usage.ts'
import { cx } from './shared.ts'

export interface TokenMeterProps {
  // Latest turn; omit before the first result arrives.
  turn?: TurnUsage | null
  session: TurnUsage
  className?: string
}

const full = (n: number) => n.toLocaleString('en-US')

function describe(label: string, u: TurnUsage): string {
  const cost = u.costUsd === null ? 'cost unavailable' : `cost ${u.costUsd.toFixed(4)} US dollars`
  return `${label}: ${full(u.inputTokens)} input tokens, ${full(u.outputTokens)} output tokens, ${full(u.cacheReadTokens)} cache read, ${full(u.cacheCreationTokens)} cache write, ${cost}.`
}

function Stat({ name, value }: { name: string; value: string }) {
  return (
    <span className="whitespace-nowrap">
      <span className="text-slate-600 dark:text-slate-400">{name} </span>
      <span className="tabular-nums">{value}</span>
    </span>
  )
}

export function TokenMeter({ turn, session, className }: TokenMeterProps) {
  const label = [turn ? describe('This turn', turn) : '', describe('Session total', session)].join(' ')
  const sessionTokens = session.inputTokens + session.outputTokens + session.cacheReadTokens + session.cacheCreationTokens
  return (
    <div
      role="group"
      aria-label="Token usage"
      className={cx(
        'flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border px-3 py-2 font-mono text-xs',
        'border-slate-300 bg-white text-slate-900 dark:border-slate-600 dark:bg-slate-900 dark:text-slate-100',
        className,
      )}
    >
      <span className="sr-only">{label}</span>
      <span aria-hidden="true" className="flex flex-wrap items-center gap-x-3 gap-y-1">
        {turn && (
          <>
            <Stat name="in" value={formatTokens(turn.inputTokens)} />
            <Stat name="out" value={formatTokens(turn.outputTokens)} />
            <Stat name="cache" value={formatTokens(turn.cacheReadTokens)} />
            <Stat name="cost" value={formatCost(turn.costUsd)} />
            <span className="text-slate-400 dark:text-slate-500">|</span>
          </>
        )}
        <Stat name="session" value={`${formatTokens(sessionTokens)} · ${formatCost(session.costUsd)}`} />
      </span>
    </div>
  )
}
