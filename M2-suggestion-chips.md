# M2 — Suggestion Chips

**Goal:** when the user's message contains something a plugin can act on (a Jira key, an incident ID, a repo URL), the right plugin appears as a chip above the editor. One click attaches it. No trigger → nothing shown.

**Depends on:** M1.

---

## Step 1 — Contract **[grunt]**

Add to `packages/contracts/src/plugins.ts`:

```ts
export type PluginSuggestion = {
  pluginId: string;
  score: number;        // 0..1
  reason: "trigger" | "search";
  matchedText?: string; // the substring that fired the trigger
};
```

Add to `rpc.ts`:

```ts
"plugins.suggest": { input: { sessionId: string; text: string }; output: PluginSuggestion[] };
"plugins.suggestFeedback": { input: { sessionId: string; pluginId: string; action: "accepted"|"dismissed" }; output: { ok: true } };
```

Add to `settings` contract (wherever user settings live): `pluginSuggestions: boolean` default `true`.

---

## Step 2 — Trigger table **[grunt]**

Create `apps/server/src/services/pluginTriggers.ts`. Static map, plugin id → regex strings. Keep regexes simple and anchored on word boundaries.

```ts
export const PLUGIN_TRIGGERS: Record<string, string[]> = {
  "cdd-atlassian-knowledge-base": ["\\b[A-Z][A-Z0-9]{1,9}-\\d{1,6}\\b"],
  "cdd-ct-wins":                  ["\\bSoS\\b", "\\bwin ticket\\b"],
  "cdd-spectest":                 ["\\b[A-Z][A-Z0-9]{1,9}-\\d{1,6}\\b", "openapi", "swagger"],
  "cdd-servicenow":               ["\\b(INC|CHG|PRB|RITM|REQ)\\d{6,}\\b", "servicenow", "\\bCI\\b"],
  "cdd-pagerduty":                ["pagerduty", "on-call", "oncall", "\\bincident\\b"],
  "cdd-aws":                      ["arn:aws:", "\\b\\d{12}\\b", "\\bs3://", "lambda", "\\bec2\\b", "cloudwatch"],
  "cdd-finops":                   ["\\bcost\\b", "spend", "cloudability", "idle"],
  "cdd-github":                   ["github\\.com/CBA-General/", "\\brepo\\b", "who owns", "ownership"],
  "cdd-sonarqube":                ["sonar", "quality gate", "coverage"],
  "cdd-snyk":                     ["snyk", "dependenc(y|ies) vuln"],
  "cdd-wiz":                      ["\\bwiz\\b", "cloud security"],
  "cdd-opsindex":                 ["opsindex", "change risk", "compliance"],
  "cdd-kubectl":                  ["kubectl", "\\bpod\\b", "namespace", "\\beks\\b", "deployment"],
  "cdd-pccs":                     ["argocd", "argo", "alertmanager", "grafana"],
  "cdd-metrics":                  ["prometheus", "promql", "metrics\\.cba"],
  "cdd-observe":                  ["\\bobserve\\b", "\\bopal\\b", "\\blogs?\\b"],
  "cdd-teamforms":                ["who is", "manager of", "reports to", "org chart", "teamform"],
  "cdd-identity":                 ["access request", "identity\\.cba", "\\brole\\b"],
  "cdd-m365":                     ["\\bteams\\b", "calendar", "meeting", "schedule"],
  "cdd-atlas":                    ["\\.alm\\b", "atlas live monitor"],
  "cdd-pathfinder":               ["lineage", "data element", "where does .* come from"],
  "cdd-api-catalogue":            ["api catalogue", "find (an )?api", "openapi spec"],
  "cdd-artifactory":              ["artifactory", "jfrog"],
  "cdd-vault":                    ["\\bvault\\b", "secret path"],
  "cdd-blazemeter":               ["blazemeter", "performance test", "load test"],
  "cdd-golden-pathway":           ["golden path", "\\bci\\b for", "pipeline"],
  "cdd-sdlchub":                  ["sdlc hub", "evidence", "\\bCR\\b"],
  "cdd-wasp":                     ["\\bwasp\\b", "firewall", "proxy request"],
  "cdd-cmdm-database":            ["cmdm", "\\bsap\\b compar"],
  "cdd-cmdm-hasura":              ["hasura", "graphql"],
  "cdd-sap-local":                ["\\bsap\\b", "business partner", "\\besb\\b"],
  "cdd-launchdarkly":             ["launchdarkly", "feature flag"],
  "cdd-getdx":                    ["\\bdx\\b", "getdx"],
  "cdd-llm-wiki":                 ["wiki"],
  "cdd-rise-generator":           ["\\brise\\b", "security assessment"],
  "cdd-improve-api-spec":         ["fix (the )?spec", "openapi"],
  "cdd-grill-me":                 ["grill me", "stress.?test (my|this) plan"],
  "cdd-cbaspec":                  ["functional design", "\\bbdd\\b"],
  "cdd-customer-journey":         ["customer journey", "gherkin"],
};
```

On sync (M1 Step 3), set `descriptor.triggers = PLUGIN_TRIGGERS[id] ?? []`.

---

## Step 3 — PluginRouter service **[build]**

Create `apps/server/src/services/PluginRouter.ts`.

```ts
export class PluginRouter extends Context.Tag("PluginRouter")<PluginRouter, {
  suggest: (sessionId: string, text: string) => Effect.Effect<PluginSuggestion[]>;
}>() {}
```

Algorithm for `suggest(sessionId, text)`:
1. If settings `pluginSuggestions === false` → `[]`.
2. Load all plugins (cache in memory, refresh after sync).
3. `attached` = `session_plugins` for this session. Exclude these from results.
4. Trigger pass: for each plugin, for each trigger regex (compile once, flag `i`), `exec` on `text`. On match push `{pluginId, score: 1.0, reason:"trigger", matchedText: m[0]}`. One hit per plugin max.
5. If trigger hits ≥ 3 → return top 3 (ties broken by `recent` usage).
6. Search pass: run `PluginRegistry.search(text, 5)`. Normalise bm25 scores to 0..0.6 (so triggers always win). Push as `reason:"search"`. Skip plugins already in list.
7. Drop anything with score < 0.25. Return top 3.
8. For each returned suggestion `recordUsage(pluginId, sessionId, "suggested")`.

Dedupe rule: `cdd-atlassian-knowledge-base` and `cdd-spectest` share the Jira regex. If both fire and the text also contains `test`, keep spectest; otherwise keep atlassian-knowledge-base.

Write `PluginRouter.test.ts`:
- `"look at CDD-1234"` → first suggestion `cdd-atlassian-knowledge-base`
- `"write tests for CDD-1234"` → first `cdd-spectest`
- `"INC0012345 is open"` → `cdd-servicenow`
- `"hello"` → `[]`
- attached plugin never suggested

**Verify:** tests pass.

---

## Step 4 — RPC + feedback **[grunt]**

Add handlers `plugins.suggest` and `plugins.suggestFeedback` (records `accepted`/`dismissed` in `plugin_usage`).

---

## Step 5 — Chips UI **[build]**

Create `apps/desktop/src/components/plugins/SuggestionChips.tsx`.

Behaviour:
- Subscribe to Lexical editor text. Debounce 400ms. Only call `plugins.suggest` if text length ≥ 6 and changed.
- Render in the same row as `AttachedPlugins` (M1), to the right of attached chips, separated by a 1px divider if both present.
- Suggestion chip style: dashed border, `+ cdd-servicenow`, small `×` on hover.
- Click chip → `plugins.attach` + `suggestFeedback accepted` → chip becomes solid (moves to attached).
- Click `×` → `suggestFeedback dismissed`, hide that plugin for the rest of the session (local Set).
- When the user sends the message, clear suggestions.
- Max 3 chips. Row height fixed 32px so the editor never jumps. If no suggestions and no attached, row is `hidden` (0px), not empty space.

Settings toggle in `/settings/plugins`: "Suggest plugins as I type" → `pluginSuggestions`.

**Verify (manual):**
- Type `can you check INC0012345` → `+ cdd-servicenow` appears within ~0.5s
- Type `hello how are you` → nothing
- Click chip → becomes attached, agent uses it
- Toggle off in settings → nothing appears

---

## Step 6 — Review **[build]**

`/code-review` the diff. Commit `M2 complete`.
