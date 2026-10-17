import { createSdkMcpServer, tool } from '@anthropic-ai/claude-agent-sdk'
import type { CallToolResult } from '@modelcontextprotocol/sdk/types.js'
import type { DatabaseSync } from 'node:sqlite'
import { z } from 'zod'
import { def, readRange, READ_MAX_LINES, recall, search } from './search.ts'

export const RETRIEVAL_SERVER_NAME = 'retrieval'

// The SDK exposes in-process MCP tools to the model as mcp__<server>__<tool>.
export const RETRIEVAL_TOOL_NAMES = ['search', 'def', 'read', 'recall'].map((name) => `mcp__${RETRIEVAL_SERVER_NAME}__${name}`)

const SEARCH_DESCRIPTION =
  'Search the current workspace by keywords or identifiers. Returns ranked passages with file path and line range. Prefer this over Grep/Glob/Read for finding code or docs in this workspace; then call read for the exact lines you need.'
const DEF_DESCRIPTION = 'Find where a function, class, method or type is defined. Returns file path, line and signature.'
const READ_DESCRIPTION =
  'Read an exact line range (max 200 lines) from a workspace file. Use after search or def instead of reading whole files.'

const RECALL_DESCRIPTION =
  'Search earlier conversations in this workspace for decisions, facts or context the user discussed before. Returns matching past messages with session and turn.'

const ok = (text: string): CallToolResult => ({ content: [{ type: 'text', text }] })
const fail = (text: string): CallToolResult => ({ isError: true, content: [{ type: 'text', text }] })

// Handlers must never throw: the agent gets an error result it can react to.
async function guarded(run: () => string): Promise<CallToolResult> {
  try {
    return ok(run())
  } catch (error) {
    return fail(error instanceof Error ? error.message : String(error))
  }
}

export function createRetrievalTools({ db, root }: { db: DatabaseSync; root: string }) {
  const searchTool = tool(
    'search',
    SEARCH_DESCRIPTION,
    {
      query: z.string().describe('Keywords or identifiers, e.g. "websocket auth token"'),
      k: z.number().int().optional().describe('Number of hits, 1-10 (default 8)'),
      kinds: z.array(z.enum(['code', 'doc'])).optional().describe('Restrict to code or doc passages'),
      pathPrefix: z.string().optional().describe('Only files whose workspace-relative path starts with this'),
    },
    (args) =>
      guarded(() => {
        const hits = search(db, args)
        if (hits.length === 0) return 'No results.'
        return hits
          .map((hit) => `${hit.path}:${hit.startLine}-${hit.endLine}  ${hit.symbol ?? '-'} (score ${hit.score.toFixed(1)})\n${hit.snippet}`)
          .join('\n\n')
      }),
    { annotations: { readOnlyHint: true } },
  )

  const defTool = tool(
    'def',
    DEF_DESCRIPTION,
    { symbol: z.string().describe('Function, class, method or type name') },
    (args) =>
      guarded(() => {
        const hits = def(db, args.symbol)
        if (hits.length === 0) return 'No results.'
        return hits.map((hit) => `${hit.path}:${hit.line}  ${hit.signature}`).join('\n')
      }),
    { annotations: { readOnlyHint: true } },
  )

  const readTool = tool(
    'read',
    READ_DESCRIPTION,
    {
      path: z.string().describe('Workspace-relative file path'),
      startLine: z.number().int().describe('First line, 1-based'),
      endLine: z.number().int().describe('Last line, inclusive'),
    },
    (args) =>
      guarded(() => {
        const text = readRange(root, args.path, args.startLine, args.endLine)
        if (text === '') return `${args.path}: no lines in range ${args.startLine}-${args.endLine}.`
        const shownEnd = Math.min(args.endLine, args.startLine + READ_MAX_LINES - 1)
        const truncated = shownEnd < args.endLine ? ` (truncated at ${READ_MAX_LINES} lines)` : ''
        return `${args.path}:${args.startLine}-${shownEnd}${truncated}\n${text}`
      }),
    { annotations: { readOnlyHint: true } },
  )

  const recallTool = tool(
    'recall',
    RECALL_DESCRIPTION,
    {
      query: z.string().describe('Keywords describing what was discussed, e.g. "database choice postgres"'),
      k: z.number().int().optional().describe('Number of hits, 1-10 (default 8)'),
      sessionId: z.string().optional().describe('Only search this conversation session'),
    },
    (args) =>
      guarded(() => {
        const hits = recall(db, args)
        if (hits.length === 0) return 'No results.'
        return hits
          .map(
            (hit) =>
              `session ${hit.sessionId} turn ${hit.turnIndex}  ${hit.role} ${new Date(hit.createdAt).toISOString()} (score ${hit.score.toFixed(1)})\n${hit.snippet}`,
          )
          .join('\n\n')
      }),
    { annotations: { readOnlyHint: true } },
  )

  return [searchTool, defTool, readTool, recallTool]
}

export function createRetrievalMcpServer(deps: { db: DatabaseSync; root: string }) {
  return createSdkMcpServer({ name: RETRIEVAL_SERVER_NAME, tools: createRetrievalTools(deps) })
}
