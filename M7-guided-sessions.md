# M7 — Guided Sessions (Stepper)

**Goal:** interview-style and multi-phase plugins run as a guided session: a phase bar, one question at a time, approval gates as buttons. Start with `cdd-grill-me`, then `cdd-rise-generator`, then `cdd-engineering-autopilot`.

**Depends on:** M5 (approval cards), M6 (progress MCP for phases).

---

## Step 1 — Which plugins are guided **[grunt]**

Add to `PluginDescriptor` (contracts + migration column `guided TEXT`):

```ts
guided: null | {
  mode: "interview" | "phases" | "agents";
  phases?: string[];          // ordered labels, for mode phases/agents
  gates?: string[];           // phase ids that require approval before proceeding
};
```

Static config in `apps/server/src/services/guidedPlugins.ts`:

```ts
export const GUIDED: Record<string, PluginDescriptor["guided"]> = {
  "cdd-grill-me":       { mode: "interview" },
  "cdd-cbaspec":        { mode: "interview" },
  "cdd-customer-journey": { mode: "interview" },
  "cdd-interview":      { mode: "interview" },
  "cdd-rise-generator": { mode: "phases", phases: ["Scope","Assets","Threats","Controls","Gaps","Remediation","Report"], gates: ["Report"] },
  "cdd-engineering-autopilot": { mode: "agents", phases: ["Planner","Architect","Developer","Reviewer"], gates: ["Planner","Architect","Developer"] },
};
```

Apply during sync.

---

## Step 2 — Contract **[build]**

`packages/contracts/src/guided.ts`:

```ts
export type GuidedState = {
  sessionId: string;
  pluginId: string;
  mode: "interview"|"phases"|"agents";
  phases: Array<{ id: string; label: string; status: "pending"|"running"|"done"|"failed"; gate: boolean }>;
  currentPhase: string | null;
  question: null | {
    id: string;
    text: string;
    options?: Array<{ id: string; label: string; recommended?: boolean }>;
    allowFreeText: boolean;
  };
  startedAt: string;
  finishedAt: string | null;
};
```

RPC:
```ts
"guided.state":  { input: { sessionId: string }; output: GuidedState | null };
"guided.answer": { input: { sessionId: string; questionId: string; optionId?: string; text?: string }; output: { ok: true } };
"guided.exit":   { input: { sessionId: string }; output: { ok: true } };
```
WS push `"guided.state"` on every change.

Add two MCP tools to `cdd-progress` (M6):

| Tool | Input | Effect |
|------|-------|--------|
| `guided_ask` | `{ id, text, options?: [{id,label,recommended?}], allowFreeText?: boolean }` | sets `question`, **blocks** until `guided.answer` arrives, returns `{optionId?, text?}` |
| `guided_phase` | `{ id, status }` | updates phase; if phase is a gate and status="done", triggers an ApprovalRequest (M5) labelled `Proceed past <phase>` and blocks until approved |

Descriptions:
- `guided_ask`: "Ask the user one question and wait for the answer. Use this instead of writing the question in chat when running an interview or stress-test. Provide 2–4 options when you can, mark one recommended."
- `guided_phase`: "Mark a phase of a multi-phase workflow as running or done."

---

## Step 3 — Server **[plan]** then **[build]**

`apps/server/src/services/GuidedSessions.ts`:
- `Map<sessionId, GuidedState>` + table `guided_sessions(session_id PK, plugin_id, state JSON, updated_at)` for persistence.
- Start: when a guided plugin is attached **and** the user's next message mentions the plugin's command (or the plugin is the only attached one), create `GuidedState` from the descriptor and push.
- `guided_ask` handler: set `question`, push state, `Deferred.await` keyed by `questionId`, 30-minute timeout → return `{text:"(no answer)"}`.
- `guided.answer`: resolve the deferred, clear `question`, push state.
- `guided_phase`: update, push. If gate + done → create ApprovalRequest via M5 service with `label: "Proceed past <phase>"`, `risk: "low"`, preview = phase summary; await resolution; on reject return `{proceed:false, note}` to the tool.
- `guided.exit`: finishedAt = now, push, and send a user message to the agent "Stop the guided session and summarise what we have so far."
- On session end: resolve all deferreds with timeout values.

Also instruct the agent: when a guided plugin is attached, append to the session system prompt (via SDK `appendSystemPrompt` or equivalent): "This session has a guided UI. Ask questions only via `guided_ask`. Report phases via `guided_phase`. Do not write numbered question lists in chat."

---

## Step 4 — Stepper UI **[build]**

`apps/desktop/src/components/guided/`:

- `GuidedBar.tsx` — mounted between the chat and the editor when `guided.state` is non-null and `finishedAt` is null. Height 48px.
  ```
  cdd-rise-generator   ● Scope ─ ● Assets ─ ○ Threats ─ ○ Controls ─ ○ Gaps ─ ○ Remediation ─ ○ Report🔒      Exit
  ```
  - For `interview` mode: no phase dots, just `cdd-grill-me · Question 4` and Exit.
  - Dots: done=filled, running=filled+pulse, pending=hollow, gate=lock glyph after label.
- `QuestionCard.tsx` — replaces the editor area while `question` is set.
  ```
  Question 4
  Is the PPD client retried on 5xx?
  ( ) Yes, with backoff          ← recommended
  ( ) Yes, immediate retry
  ( ) No
  [ Or type an answer…                         ]
                                     [ Answer → ]
  ```
  - Options as radio rows, recommended one pre-selected and tagged `Recommended`.
  - Free text shown only if `allowFreeText`.
  - `⏎` answers. `Esc` focuses the free text.
  - Editor is hidden, not disabled, while a question is pending; returns on answer.
- Progress panel: for guided sessions, the Tasks section is replaced by the phase list (same rows, from `guided.state.phases`).
- Gate approvals reuse `ApprovalCard` (M5); appears inline + in panel.

Agents mode extra (`cdd-engineering-autopilot`): GuidedBar shows 4 lanes as the phase dots; the chat shows a small lane label `[Planner]` prefix on each assistant message while that phase is running (server tags messages with current phase).

**Verify (manual):**
- Attach cdd-grill-me, say "grill me on this plan" + paste a plan → QuestionCard appears, editor hidden
- Pick recommended → next question; free text → accepted
- Exit → bar disappears, agent summarises
- Attach cdd-rise-generator, run → phase dots advance; at Report a `Proceed past Report` approval appears
- Run cdd-engineering-autopilot → gates after Planner/Architect/Developer each require Approve

---

## Step 5 — Update the 3 plugins **[parallel]** (Haiku)

For each of `cdd-grill-me`, `cdd-rise-generator`, `cdd-engineering-autopilot`: edit SKILL.md to say "If the `guided_ask` tool is available, ask every question through it and report phases through `guided_phase`. Otherwise ask in chat as before." Open PRs.

---

## Step 6 — Review **[build]**

`/code-review`. Specifically check: question deferreds never leak; Exit mid-question resolves the deferred. Commit `M7 complete`.
