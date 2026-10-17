import type { DefHit, RecallHit, RetrievalClient, SearchHit } from '../src/types.ts'

const FILES: SearchHit[] = [
  { path: 'src/server/connectHandler.ts', startLine: 10, endLine: 42, symbol: 'connectHandler', score: 4.2, snippet: 'export async function connectHandler(socket: WebSocket) {\n  const token = readAuthToken(socket.request)\n  if (!token) return socket.close(4401, "auth token missing")\n  await registerSession(socket, token)\n  // more below\n}' },
  { path: 'src/server/auth.ts', startLine: 1, endLine: 28, symbol: 'readAuthToken', score: 3.1, snippet: 'export function readAuthToken(req: IncomingMessage): string | null {\n  const header = req.headers["authorization"]\n  return header?.startsWith("Bearer ") ? header.slice(7) : null\n}' },
  { path: 'docs/websocket auth: notes.md', startLine: 4, endLine: 19, symbol: null, score: 1.8, snippet: 'The websocket handshake carries the auth token in the Authorization header.\nTokens rotate every 15 minutes.' },
  { path: 'src/日本語/ソケット.ts', startLine: 3, endLine: 9, symbol: 'openSocket', score: 1.2, snippet: 'export const openSocket = () => new WebSocket(url)' },
  { path: 'src/client/rpc.ts', startLine: 55, endLine: 90, symbol: 'RpcClient', score: 1.1, snippet: 'class RpcClient {\n  send(message: unknown) {}\n}' },
]
const DEFS: DefHit[] = [
  { path: 'src/server/connectHandler.ts', line: 10, signature: 'export async function connectHandler(socket: WebSocket) {' },
  { path: 'src/client/rpc.ts', line: 55, signature: 'class RpcClient {' },
]
const RECALL: RecallHit[] = [
  { sessionId: 'a1b2', turnIndex: 6, role: 'user', createdAt: 0, score: 2, snippet: 'We decided on SQLite over a sidecar service for the index.' },
]

const wait = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms))
const matches = (hit: { path: string; snippet: string }, q: string) => q.toLowerCase().split(/\s+/).some((w) => w && (hit.path + hit.snippet).toLowerCase().includes(w))

// Realistic latency (80-400 ms, so slow responses really do arrive out of order) and one scripted failure: query "boom".
export const fakeClient: RetrievalClient = {
  async search({ query }) {
    await wait(80 + Math.random() * 320)
    if (query.trim().toLowerCase() === 'boom') throw new Error('index is rebuilding, try again in a moment')
    return FILES.filter((f) => matches(f, query))
  },
  async def(symbol) {
    await wait(60 + Math.random() * 120)
    return DEFS.filter((d) => d.signature.toLowerCase().includes(symbol.toLowerCase()))
  },
  async recall({ query }) {
    await wait(100 + Math.random() * 150)
    return RECALL.filter((r) => matches({ path: '', snippet: r.snippet }, query))
  },
}
