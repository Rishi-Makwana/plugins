# M4 — Native Cards

**Goal:** when a plugin returns a Jira ticket, ServiceNow record, GitHub PR, PagerDuty incident, or a vulnerability summary, the chat shows one compact card instead of markdown. Collapsed by default. Click to expand. Link opens in the side browser view.

**Depends on:** M3 (link extraction, `link` events).

---

## Step 1 — Card contract **[build]**

Create `packages/contracts/src/cards.ts`:

```ts
export type CardKind = "jira.issue" | "snow.record" | "github.pr" | "pagerduty.incident" | "vuln.summary";

export type CardBase = { kind: CardKind; id: string; url: string; source: string; at: string };

export type JiraIssueCard = CardBase & { kind: "jira.issue";
  key: string; summary: string; status: string; type: string; assignee?: string; priority?: string; updated?: string };

export type SnowRecordCard = CardBase & { kind: "snow.record";
  number: string; table: "incident"|"change_request"|"problem"|"sc_req_item"; shortDescription: string; state: string; priority?: string; assignedTo?: string; ci?: string };

export type GithubPrCard = CardBase & { kind: "github.pr";
  repo: string; number: number; title: string; state: "open"|"closed"|"merged"|"draft"; author: string; checks?: "pending"|"success"|"failure"; reviews?: number };

export type PagerdutyIncidentCard = CardBase & { kind: "pagerduty.incident";
  number: number; title: string; status: "triggered"|"acknowledged"|"resolved"; urgency: "high"|"low"; service: string; assignee?: string };

export type VulnSummaryCard = CardBase & { kind: "vuln.summary";
  tool: "snyk"|"sonarqube"|"wiz"|"opsindex"; target: string;
  counts: { critical: number; high: number; medium: number; low: number }; fixed?: number };

export type Card = JiraIssueCard | SnowRecordCard | GithubPrCard | PagerdutyIncidentCard | VulnSummaryCard;
```

Add `{ kind: "card"; sessionId; source; at; card: Card }` to the `ProgressEvent` union and a `cards: Card[]` array to `ProgressSnapshot` (dedupe by `kind+id`).

Add to chat message contract: a message part type `{ type: "card"; card: Card }` alongside existing text parts.

---

## Step 2 — Card extraction service **[plan]** then **[build]**

Create `apps/server/src/services/CardExtract.ts`. Input: a tool result (string or JSON) + the plugin id that produced it. Output: `Card[]`.

Strategy: structured first, regex fallback.

1. **Structured**: if the tool result is JSON (or has a JSON block), try known shapes:
   - Jira: object with `key` and `fields.summary` → `jira.issue`
   - SNOW: object with `number` and `sys_id` → table from `number` prefix (`INC`→incident, `CHG`→change_request, `PRB`→problem, `RITM`→sc_req_item)
   - GitHub (gh CLI JSON): `number`, `title`, `state`, `author.login`, `url` containing `/pull/` → `github.pr`
   - PagerDuty: `incident_number`, `status`, `urgency`, `service.summary`
   - Snyk/Sonar/Wiz/OpsIndex: look for `severity` counts; map to `vuln.summary`. Each tool differs — read the plugin's SKILL.md for the output shape and write one parser per tool in `apps/server/src/services/cardParsers/<tool>.ts`.
2. **Regex fallback** (markdown/text output): extract URLs matching
   - `https://[^/]+/browse/([A-Z]+-\d+)` → jira.issue with only key+url
   - `https://[^/]+/nav_to.do\?uri=.*?(INC|CHG|PRB|RITM)\d+` or `/incident\.do` → snow.record
   - `https://github\.com/([^/]+/[^/]+)/pull/(\d+)` → github.pr
   - `https://[^.]+\.pagerduty\.com/incidents/(\w+)` → pagerduty.incident
   Partial cards are fine; UI renders what it has.
3. Dedupe by `kind+id`.

Wire into M3 Step 5: on `PostToolUse` for any plugin tool, run `CardExtract`, post a `card` event per card, and also insert a `card` message part into the assistant message at that position.

Write `CardExtract.test.ts` with one fixture per kind (JSON and markdown variants).

**Verify:** tests pass.

---

## Step 3 — Card components **[parallel]** (one subagent per card)

Create `apps/desktop/src/components/cards/`:

- `Card.tsx` — shell. Props `{card: Card}`. Dispatches by `kind`. 
- `JiraIssueCard.tsx`, `SnowRecordCard.tsx`, `GithubPrCard.tsx`, `PagerdutyIncidentCard.tsx`, `VulnSummaryCard.tsx`

Shell spec (identical for all):
- Collapsed: single row, 44px, border, rounded-lg. Left: 16px system icon. Middle: primary text (bold) + secondary text (muted, truncated). Right: status pill + chevron.
- Expanded (click row): shows 2–4 extra fields in a 2-col grid, labels muted, values normal. Max 4 rows.
- Footer link: `Open ↗` → `rpc("browser.open", {url})` (side browser view). If no browser RPC exists yet, add it: `"browser.open": {input:{url:string}; output:{ok:true}}` calling Electron's `WebContentsView.loadURL`.
- Status pill colours: ONLY these: `neutral` (muted bg), `ok` (green), `warn` (amber), `bad` (red). Map per card below.
- Missing fields: omit the row. Never show `undefined` or `—`.

Per-card mapping:

| Card | Primary | Secondary | Pill | Expanded rows |
|------|---------|-----------|------|---------------|
| jira.issue | `key` | `summary` | status → ok if `/done|closed|resolved/i`, warn if `/progress|review/i`, else neutral | Type, Assignee, Priority, Updated |
| snow.record | `number` | `shortDescription` | state → bad if `/new|open/i` and table=incident, ok if `/closed|resolved/i`, else neutral | State, Priority, Assigned to, CI |
| github.pr | `repo#number` | `title` | merged→ok, open→neutral, draft→neutral, closed→bad; checks failure→bad overrides | Author, Checks, Reviews, State |
| pagerduty.incident | `#number` | `title` | triggered→bad, acknowledged→warn, resolved→ok | Service, Urgency, Assignee |
| vuln.summary | `tool` + `target` | `C H M L` counts inline as four tiny pills | bad if critical>0, warn if high>0, else ok | Fixed, Target, Tool |

Icons (lucide or inline SVG, mono, no brand colours): jira→`Ticket`, snow→`ClipboardList`, github→`GitPullRequest`, pagerduty→`Siren`, vuln→`ShieldAlert`.

---

## Step 4 — Render in chat + panel **[build]**

- Chat: in the message renderer, when a part is `{type:"card"}` render `<Card />` inline where it occurred. Max width 560px.
- Progress panel: new section **Links** (rename from M3's link rows) rendering collapsed `<Card />` rows, newest first, max 10, "Show all" link if more.
- If the same card appears in chat and panel, both update on new `card` events (reducer upserts).

**Verify (manual):**
- Attach cdd-atlassian-knowledge-base, ask "show CDD-1234" → one Jira card in chat and in the panel
- Attach cdd-github, ask for a PR → github card with checks pill
- Click `Open ↗` → side browser loads the URL
- Ask for 3 tickets → 3 cards, each collapsed, no markdown table

---

## Step 5 — Review **[build]**

`/code-review`. Commit `M4 complete`.
