import { createHmac, timingSafeEqual } from 'node:crypto'

const SECRET = process.env.TOKEN_SECRET ?? 'dev-secret'

export function issueToken(userId: string): string {
  const signature = createHmac('sha256', SECRET).update(userId).digest('hex')
  return `${userId}.${signature}`
}

export function verifyToken(token: string): string | null {
  const [userId, signature] = token.split('.')
  if (!userId || !signature) return null
  const expected = createHmac('sha256', SECRET).update(userId).digest('hex')
  const ok = signature.length === expected.length && timingSafeEqual(Buffer.from(signature), Buffer.from(expected))
  return ok ? userId : null
}
