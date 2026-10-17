import { query } from '@anthropic-ai/claude-agent-sdk'
import { execFileSync } from 'node:child_process'
import { mkdirSync, mkdtempSync, readFileSync, appendFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join, resolve } from 'node:path'
import { parseArgs } from 'node:util'
import { fileURLToPath } from 'node:url'
import { createRetrievalMcpServer, RETRIEVAL_SERVER_NAME, RETRIEVAL_TOOL_NAMES } from '../src/agent-tools.ts'
import { createChunker, indexWorkspace, openStore } from '../src/index.ts'

const HERE = dirname(fileURLToPath(import.meta.url))
// Tasks describe a private repo, so the real file is gitignored; tasks.example.json shows the shape.
const DEFAULT_TASKS_FILE = join(HERE, 'tasks.local.json')
const MAX_TURNS = 25
const READ_ONLY_BUILTINS = ['Read', 'Grep', 'Glob']
const FORBIDDEN_TOOLS = ['Write', 'Edit', 'MultiEdit', 'NotebookEdit', 'Bash', 'WebFetch', 'WebSearch', 'Task', 'Agent']
const PROMPT_SUFFIX = '\n\nThis is a read-only question: do not modify anything. Answer concisely, naming files and identifiers.'

type Arm = 'on' | 'off'
interface Task { id: string; prompt: string; expect: string[] }
interface RunRecord {
  task: string; arm: Arm; run: number; model: string; ok: boolean; error?: string
  inputTokens: number; outputTokens: number; cacheReadTokens: number; cacheCreationTokens: number
  costUsd: number | null; turns: number; wallMs: number
  toolCalls: Record<string, number>; initTools: string[]; pass: boolean; answer: string
}

const { values } = parseArgs({
  options: {
    arm: { type: 'string', default: 'both' },
    runs: { type: 'string', default: '1' },
    model: { type: 'string' },
    tasks: { type: 'string', default: 'all' },
    root: { type: 'string' },
    'tasks-file': { type: 'string', default: DEFAULT_TASKS_FILE },
  },
})

const arms: Arm[] = values.arm === 'both' ? ['off', 'on'] : values.arm === 'on' || values.arm === 'off' ? [values.arm] : fatal('--arm must be on|off|both')
const runs = Number(values.runs)
if (!Number.isInteger(runs) || runs < 1) fatal('--runs must be a positive integer')
if (!values.root) fatal('--root <path to the repo the tasks are about> is required')
const root = resolve(values.root)
const allTasks = JSON.parse(readFileSync(resolve(values['tasks-file']!), 'utf8')) as Task[]
const wanted = values.tasks === 'all' ? null : new Set(values.tasks!.split(','))
const tasks = allTasks.filter((task) => wanted === null || wanted.has(task.id))
if (tasks.length === 0) fatal(`no tasks match --tasks ${values.tasks}`)

function fatal(message: string): never {
  console.error(message)
  process.exit(1)
}

function gitStatus(): string | null {
  try {
    return execFileSync('git', ['--no-optional-locks', '-C', root, 'status', '--porcelain'], { encoding: 'utf8' })
  } catch {
    return null
  }
}

const median = (xs: number[]) => {
  if (xs.length === 0) return NaN
  const sorted = [...xs].sort((a, b) => a - b)
  const mid = sorted.length >> 1
  return sorted.length % 2 ? sorted[mid]! : (sorted[mid - 1]! + sorted[mid]!) / 2
}

async function runOne(task: Task, arm: Arm, run: number, mcpServer: ReturnType<typeof createRetrievalMcpServer> | null): Promise<RunRecord> {
  const record: RunRecord = {
    task: task.id, arm, run, model: values.model ?? 'sdk-default', ok: false,
    inputTokens: 0, outputTokens: 0, cacheReadTokens: 0, cacheCreationTokens: 0,
    costUsd: null, turns: 0, wallMs: 0, toolCalls: {}, initTools: [], pass: false, answer: '',
  }
  const started = Date.now()
  try {
    const stream = query({
      prompt: task.prompt + PROMPT_SUFFIX,
      options: {
        cwd: root,
        ...(values.model ? { model: values.model } : {}),
        maxTurns: MAX_TURNS,
        tools: READ_ONLY_BUILTINS,
        allowedTools: arm === 'on' ? [...READ_ONLY_BUILTINS, ...RETRIEVAL_TOOL_NAMES] : READ_ONLY_BUILTINS,
        disallowedTools: FORBIDDEN_TOOLS,
        permissionMode: 'dontAsk',
        // Isolate from the user's own hooks, plugins, MCP servers and CLAUDE.md so both arms see the same environment.
        settingSources: [],
        strictMcpConfig: true,
        persistSession: false,
        ...(mcpServer ? { mcpServers: { [RETRIEVAL_SERVER_NAME]: mcpServer } } : {}),
      },
    })
    for await (const message of stream) {
      if (message.type === 'system' && message.subtype === 'init') {
        record.initTools = message.tools
        if (!values.model) record.model = message.model
        const writable = message.tools.filter((name) => FORBIDDEN_TOOLS.includes(name))
        if (writable.length > 0) throw new Error(`write-capable tools present in init: ${writable.join(', ')}`)
      } else if (message.type === 'assistant') {
        for (const block of message.message.content) {
          if (block.type === 'tool_use') record.toolCalls[block.name] = (record.toolCalls[block.name] ?? 0) + 1
        }
      } else if (message.type === 'result') {
        record.turns = message.num_turns
        record.costUsd = message.total_cost_usd ?? null
        for (const usage of Object.values(message.modelUsage ?? {})) {
          record.inputTokens += usage.inputTokens
          record.outputTokens += usage.outputTokens
          record.cacheReadTokens += usage.cacheReadInputTokens
          record.cacheCreationTokens += usage.cacheCreationInputTokens
        }
        if (message.subtype === 'success' && !message.is_error) {
          record.ok = true
          record.answer = message.result
        } else {
          record.error = message.subtype === 'success' ? message.result : message.subtype
        }
      }
    }
  } catch (error) {
    record.error = error instanceof Error ? error.message : String(error)
  }
  record.wallMs = Date.now() - started
  const answer = record.answer.toLowerCase()
  record.pass = record.ok && task.expect.every((needle) => answer.includes(needle.toLowerCase()))
  return record
}

function summarize(records: RunRecord[]) {
  const rows = (['off', 'on'] as Arm[]).flatMap((arm) => {
    const mine = records.filter((r) => r.arm === arm && r.ok)
    if (mine.length === 0) return []
    const retrievalCalls = (r: RunRecord) =>
      Object.entries(r.toolCalls).filter(([name]) => RETRIEVAL_TOOL_NAMES.includes(name)).reduce((sum, [, n]) => sum + n, 0)
    return [{
      arm,
      runs: mine.length,
      'median output tok': median(mine.map((r) => r.outputTokens)),
      'median input+cache tok': median(mine.map((r) => r.inputTokens + r.cacheReadTokens + r.cacheCreationTokens)),
      'median cost $': Number(median(mine.map((r) => r.costUsd ?? NaN)).toFixed(4)),
      'pass rate': `${mine.filter((r) => r.pass).length}/${mine.length}`,
      'median retrieval calls': median(mine.map(retrievalCalls)),
    }]
  })
  console.table(rows)
}

const statusBefore = gitStatus()
let mcpServer: ReturnType<typeof createRetrievalMcpServer> | null = null
if (arms.includes('on')) {
  const dbDir = mkdtempSync(join(tmpdir(), 'retrieval-bench-'))
  const db = openStore(join(dbDir, 'index.db'))
  const stats = await indexWorkspace(db, root, createChunker())
  console.error(`indexed ${root}: ${stats.filesIndexed} files, ${stats.chunks} chunks, ${stats.ms} ms`)
  mcpServer = createRetrievalMcpServer({ db, root })
}

const outDir = join(HERE, 'results', 'raw')
mkdirSync(outDir, { recursive: true })
const outFile = join(outDir, `${new Date().toISOString().replaceAll(':', '-')}.jsonl`)
const records: RunRecord[] = []
for (const task of tasks) {
  for (let run = 1; run <= runs; run++) {
    for (const arm of arms) {
      const record = await runOne(task, arm, run, arm === 'on' ? mcpServer : null)
      records.push(record)
      appendFileSync(outFile, JSON.stringify(record) + '\n')
      console.error(`${task.id} arm=${arm} run=${run} ok=${record.ok} pass=${record.pass} out=${record.outputTokens} turns=${record.turns} ${record.wallMs}ms${record.error ? ` ERROR: ${record.error}` : ''}`)
      if (!record.ok) {
        summarize(records)
        fatal(`stopping after failed run (results so far in ${outFile})`)
      }
    }
  }
}
summarize(records)
const statusAfter = gitStatus()
console.error(`results: ${outFile}`)
console.error(statusBefore === null ? 'target is not a git repo: unchanged check skipped' : statusBefore === statusAfter ? 'target repo unchanged (git status --porcelain identical)' : 'WARNING: target repo git status changed during the run')
