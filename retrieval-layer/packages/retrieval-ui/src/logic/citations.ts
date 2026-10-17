import type { Citation } from '../types.ts'

// Formats come from retrieval/src/agent-tools.ts:
//   search: "<path>:<start>-<end>  <symbol|-> (score N)" header, then snippet lines
//   def:    "<path>:<line>  <signature>" per line
//   read:   first line "<path>:<start>-<end>[ (truncated at N lines)]", then file text
//   recall: chat turns only, no file locations
const SEARCH_HEADER = /^(.+?):(\d+)-(\d+) {2}(\S.*?) \(score -?\d+(?:\.\d+)?\)$/
const DEF_LINE = /^(.+?):(\d+) {2}\S/
const READ_HEADER = /^(.+?):(\d+)-(\d+)(?: \(truncated at \d+ lines\))?$/

const TOOL_PREFIX = 'mcp__retrieval__'
// recall indexes chat turns under virtual paths; they are not openable files.
const isVirtualPath = (path: string) => path.startsWith('chat/')

function lines(text: string): string[] {
  return text.split(/\r\n|\n|\r/)
}

function make(path: string, start: string, end?: string, symbol?: string): Citation | null {
  const startLine = Number(start)
  const endLine = end === undefined ? undefined : Number(end)
  if (!path.trim() || isVirtualPath(path) || !Number.isSafeInteger(startLine) || startLine < 1) return null
  if (endLine !== undefined && (!Number.isSafeInteger(endLine) || endLine < startLine)) return null
  const citation: Citation = { path, startLine }
  if (endLine !== undefined) citation.endLine = endLine
  if (symbol && symbol !== '-') citation.symbol = symbol
  return citation
}

export function dedupeCitations(citations: Citation[]): Citation[] {
  const seen = new Set<string>()
  return citations.filter((c) => {
    const key = `${c.path}\0${c.startLine}\0${c.endLine ?? ''}`
    if (seen.has(key)) return false
    seen.add(key)
    return true
  })
}

export function parseCitations(toolName: string, toolResultText: string): Citation[] {
  if (typeof toolResultText !== 'string' || !toolName.startsWith(TOOL_PREFIX)) return []
  const tool = toolName.slice(TOOL_PREFIX.length)
  const all = lines(toolResultText)
  const found: (Citation | null)[] = []
  if (tool === 'search') {
    for (const line of all) {
      const m = SEARCH_HEADER.exec(line)
      if (m) found.push(make(m[1]!, m[2]!, m[3]!, m[4]))
    }
  } else if (tool === 'def') {
    for (const line of all) {
      const m = DEF_LINE.exec(line)
      if (m) found.push(make(m[1]!, m[2]!))
    }
  } else if (tool === 'read') {
    const m = READ_HEADER.exec(all[0] ?? '')
    if (m) found.push(make(m[1]!, m[2]!, m[3]!))
  }
  return dedupeCitations(found.filter((c): c is Citation => c !== null))
}

// Minimal structural slice of Agent SDK SDKAssistantMessage / SDKUserMessage.
export interface TurnMessage {
  type: string
  message?: { content?: unknown }
}

interface ToolUseBlock { type: 'tool_use'; id: string; name: string }
interface ToolResultBlock { type: 'tool_result'; tool_use_id: string; content?: unknown; is_error?: boolean }

const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null

function blocksOf(message: TurnMessage): unknown[] {
  const content = message?.message?.content
  return Array.isArray(content) ? content : []
}

function resultText(content: unknown): string {
  if (typeof content === 'string') return content
  if (!Array.isArray(content)) return ''
  return content
    .map((part) => (isObject(part) && part.type === 'text' && typeof part.text === 'string' ? part.text : ''))
    .join('\n')
}

export function extractCitationsFromTurn(messages: readonly TurnMessage[]): Citation[] {
  const toolNames = new Map<string, string>()
  const found: Citation[] = []
  for (const message of messages) {
    for (const block of blocksOf(message)) {
      if (!isObject(block)) continue
      if (block.type === 'tool_use' && typeof (block as unknown as ToolUseBlock).id === 'string') {
        toolNames.set((block as unknown as ToolUseBlock).id, String((block as unknown as ToolUseBlock).name))
      } else if (block.type === 'tool_result') {
        const result = block as unknown as ToolResultBlock
        const name = toolNames.get(result.tool_use_id)
        if (!name || result.is_error) continue
        found.push(...parseCitations(name, resultText(result.content)))
      }
    }
  }
  return dedupeCitations(found)
}
