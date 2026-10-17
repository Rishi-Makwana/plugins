import { verifyToken } from '../auth/token.ts'
import { MAX_SOCKETS } from '../config.ts'

interface WebSocketServer {
  clients: Set<unknown>
  on(event: string, handler: (...args: any[]) => void): void
}
declare const WebSocketServer: new (options: { port: number }) => WebSocketServer

export function createSocketServer(port: number) {
  const wss = new WebSocketServer({ port })
  wss.on('connection', handleConnect)
  return wss
}

export function handleConnect(socket: any, request: { url?: string }) {
  const url = new URL(request.url ?? '/', 'http://localhost')
  const token = url.searchParams.get('token')
  const userId = token ? verifyToken(token) : null
  if (!userId) {
    socket.close(4401, 'invalid auth token')
    return
  }
  socket.userId = userId
  socket.on('message', (data: Buffer) => socket.send(data))
}

export function countOpenSockets(wss: WebSocketServer): number {
  return Math.min(wss.clients.size, MAX_SOCKETS)
}
