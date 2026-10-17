export function formatDay(date: Date): string {
  return date.toISOString().slice(0, 10)
}

export function daysBetween(a: Date, b: Date): number {
  return Math.round(Math.abs(a.getTime() - b.getTime()) / 86_400_000)
}
