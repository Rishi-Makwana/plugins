// Structural slice of Agent SDK SDKResultMessage (verified against claude-agent-sdk 0.3.302 sdk.d.ts):
//  - usage: main-loop tokens for this result (per-turn in streaming-input sessions)
//  - total_cost_usd: CUMULATIVE for the query() call, so per-turn cost is the difference between results
export interface ResultMessageLike {
  type?: string
  usage?: {
    input_tokens?: number | null
    output_tokens?: number | null
    cache_read_input_tokens?: number | null
    cache_creation_input_tokens?: number | null
  } | null
  total_cost_usd?: number | null
}

export interface TurnUsage {
  inputTokens: number
  outputTokens: number
  cacheReadTokens: number
  cacheCreationTokens: number
  costUsd: number | null
}

export interface UsageSummary {
  turns: TurnUsage[]
  session: TurnUsage
}

const count = (n: unknown): number => (typeof n === 'number' && Number.isFinite(n) && n > 0 ? n : 0)
const cost = (n: unknown): number | null => (typeof n === 'number' && Number.isFinite(n) && n >= 0 ? n : null)

export function accumulateUsage(resultMessages: readonly ResultMessageLike[]): UsageSummary {
  const turns: TurnUsage[] = []
  let previousCumulative = 0
  for (const message of resultMessages) {
    if (message?.type !== undefined && message.type !== 'result') continue
    const usage = message?.usage
    const cumulative = cost(message?.total_cost_usd)
    let costUsd: number | null = null
    if (cumulative !== null) {
      // A lower cumulative means the SDK reset (/clear): treat it as a fresh baseline.
      costUsd = cumulative >= previousCumulative ? cumulative - previousCumulative : cumulative
      previousCumulative = cumulative
    }
    turns.push({
      inputTokens: count(usage?.input_tokens),
      outputTokens: count(usage?.output_tokens),
      cacheReadTokens: count(usage?.cache_read_input_tokens),
      cacheCreationTokens: count(usage?.cache_creation_input_tokens),
      costUsd,
    })
  }
  const session = turns.reduce<TurnUsage>(
    (sum, t) => ({
      inputTokens: sum.inputTokens + t.inputTokens,
      outputTokens: sum.outputTokens + t.outputTokens,
      cacheReadTokens: sum.cacheReadTokens + t.cacheReadTokens,
      cacheCreationTokens: sum.cacheCreationTokens + t.cacheCreationTokens,
      costUsd: t.costUsd === null ? sum.costUsd : (sum.costUsd ?? 0) + t.costUsd,
    }),
    { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheCreationTokens: 0, costUsd: null },
  )
  return { turns, session }
}

export function formatTokens(n: number): string {
  if (!Number.isFinite(n) || n <= 0) return '0'
  if (n < 1000) return String(Math.round(n))
  const k = n / 1e3
  const shownK = k < 100 ? Math.round(k * 10) / 10 : Math.round(k)
  if (shownK < 1000) return `${shownK}k`
  const m = n / 1e6
  return `${m < 100 ? Math.round(m * 10) / 10 : Math.round(m)}M`
}

export function formatCost(costUsd: number | null): string {
  if (costUsd === null) return '—'
  return costUsd < 0.01 && costUsd > 0 ? '<$0.01' : `$${costUsd.toFixed(2)}`
}
