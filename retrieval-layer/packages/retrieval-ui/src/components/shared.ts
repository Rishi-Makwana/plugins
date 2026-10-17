export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(' ')

export const FOCUS_RING =
  'focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-blue-700 dark:focus-visible:outline-blue-400'

export function basename(path: string): string {
  const parts = path.split(/[\\/]/).filter(Boolean)
  return parts[parts.length - 1] ?? path
}

export function formatRange(start: number, end?: number): string {
  return end !== undefined && end !== start ? `${start}–${end}` : String(start)
}
