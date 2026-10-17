# Retrieval token benchmark

Compares the Claude agent with and without the retrieval tools (`search`, `def`, `read`) on read-only code-understanding questions.

```sh
node bench/run.ts --root <repo> --arm both --runs 3 --tasks all
# options: --root <path> (required)  --arm on|off|both  --runs N  --model <id>  --tasks <id,id|all>  --tasks-file <path>
```

- Tasks come from `bench/tasks.local.json` by default. It is gitignored on purpose: tasks and answers describe a private repo, and this repo may be public. Copy `tasks.example.json` (written for `test/fixtures/repo`) and write 5 tasks for your own repo, with `expect` substrings you verified by reading the code.
- Pick a repo the model has not memorised and whose main language has a grammar in the chunker (TS/JS/Python/Rust/Go); otherwise arm `on` is handicapped (see Known limits).
- Auth: uses the logged-in Claude Code CLI session. No `ANTHROPIC_API_KEY` is read or set by the harness.
- Both arms: built-in tools limited to Read, Grep, Glob; Write/Edit/MultiEdit/NotebookEdit/Bash/WebFetch/WebSearch/Task/Agent disallowed; `permissionMode: dontAsk`; `settingSources: []` so user hooks, plugins and CLAUDE.md files do not leak in. Arm `on` adds the three `mcp__retrieval__*` tools.
- The run aborts at the first failed run (auth error, max turns) to avoid burning money.
- Raw results: `bench/results/raw/<timestamp>.jsonl` (gitignored; contains answers about the target repo, never commit it), one JSON object per task x arm x run, including the final answer and per-tool call counts.

## Summary columns

Medians over successful runs per arm.

| Column | Meaning |
|---|---|
| median output tok | Output tokens summed over all model calls (`modelUsage`), includes thinking |
| median input+cache tok | Uncached input + cache-read + cache-creation tokens, summed over all turns |
| median cost $ | `total_cost_usd` reported by the SDK |
| pass rate | Runs whose final answer contains every `expect` substring (case-insensitive) |
| median retrieval calls | Calls to `mcp__retrieval__*` tools (0 in arm `off`) |

`pass` is a substring check, not a grader: it can miss a correct paraphrase or credit a keyword dump. Read a few answers in the jsonl.

## Cost warning

Every run is a real multi-turn model call billed to the logged-in account. The smoke run (1 task, 1 run per arm) cost about $0.06 per run; `--runs 3 --tasks all --arm both` is 30 runs, roughly $2-6 depending on task difficulty. Do not loop it casually.

## Known limits

- C# files have no tree-sitter grammar in the chunker, so they are indexed as 60-line windows: `def` returns nothing for C# symbols and `search` has no symbol column weighting for them.
- Prompt caching, model sampling and the CLI's system prompt make single runs noisy; use `--runs 3` or more before reading differences of under ~20%.
