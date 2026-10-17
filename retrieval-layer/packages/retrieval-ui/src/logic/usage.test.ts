import assert from 'node:assert/strict'
import { test } from 'node:test'
import { accumulateUsage, formatCost, formatTokens } from './usage.ts'

const result = (i: number, o: number, cr: number, cc: number, cost?: number) => ({
  type: 'result',
  usage: { input_tokens: i, output_tokens: o, cache_read_input_tokens: cr, cache_creation_input_tokens: cc },
  ...(cost === undefined ? {} : { total_cost_usd: cost }),
})

test('per-turn cost is the delta of the cumulative total; session sums tokens', () => {
  const { turns, session } = accumulateUsage([result(100, 50, 10, 5, 0.1), result(200, 80, 20, 0, 0.25)])
  assert.equal(turns.length, 2)
  assert.deepEqual(turns[0], { inputTokens: 100, outputTokens: 50, cacheReadTokens: 10, cacheCreationTokens: 5, costUsd: 0.1 })
  assert.ok(Math.abs(turns[1]!.costUsd! - 0.15) < 1e-9)
  assert.deepEqual({ ...session, costUsd: Math.round(session.costUsd! * 100) / 100 }, { inputTokens: 300, outputTokens: 130, cacheReadTokens: 30, cacheCreationTokens: 5, costUsd: 0.25 })
})

test('a cumulative cost that drops (/clear) restarts the baseline instead of going negative', () => {
  const { turns } = accumulateUsage([result(1, 1, 0, 0, 1), result(1, 1, 0, 0, 0.2)])
  assert.equal(turns[1]!.costUsd, 0.2)
})

test('missing usage, missing fields and missing cost degrade to zero tokens and null cost', () => {
  const { turns, session } = accumulateUsage([{ type: 'result' }, { type: 'result', usage: { input_tokens: 5 } }, { type: 'result', usage: null, total_cost_usd: Number.NaN }])
  assert.deepEqual(turns[0], { inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheCreationTokens: 0, costUsd: null })
  assert.equal(turns[1]!.inputTokens, 5)
  assert.equal(session.costUsd, null)
  assert.equal(session.inputTokens, 5)
})

test('empty input and non-result messages', () => {
  assert.deepEqual(accumulateUsage([]).turns, [])
  assert.equal(accumulateUsage([]).session.costUsd, null)
  assert.equal(accumulateUsage([{ type: 'assistant' }, result(1, 2, 3, 4, 0)]).turns.length, 1)
})

test('negative and non-numeric token counts are ignored', () => {
  const { turns } = accumulateUsage([{ type: 'result', usage: { input_tokens: -5, output_tokens: '7' as never } }])
  assert.equal(turns[0]!.inputTokens, 0)
  assert.equal(turns[0]!.outputTokens, 0)
})

test('formatTokens', () => {
  const cases: [number, string][] = [[0, '0'], [-3, '0'], [Number.NaN, '0'], [999, '999'], [1000, '1k'], [12_345, '12.3k'], [99_949, '99.9k'], [123_456, '123k'], [999_499, '999k'], [999_500, '1M'], [1_500_000, '1.5M']]
  for (const [n, expected] of cases) assert.equal(formatTokens(n), expected, String(n))
})

test('formatCost', () => {
  assert.equal(formatCost(null), '—')
  assert.equal(formatCost(0), '$0.00')
  assert.equal(formatCost(0.004), '<$0.01')
  assert.equal(formatCost(1.234), '$1.23')
})
