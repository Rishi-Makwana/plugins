import { createServer } from 'node:http'

export function createHttpServer(port: number) {
  const server = createServer((req, res) => {
    if (req.url === '/health') {
      res.writeHead(200).end('ok')
      return
    }
    res.writeHead(404).end()
  })
  return server.listen(port)
}
