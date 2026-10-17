export const DEFAULT_PORT = 4317
export const MAX_SOCKETS = 64

export function loadConfig(env: Record<string, string | undefined>) {
  return {
    port: Number(env.PORT ?? DEFAULT_PORT),
    maxSockets: Number(env.MAX_SOCKETS ?? MAX_SOCKETS),
    dataDir: env.DATA_DIR ?? './data',
  }
}
