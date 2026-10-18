# CDD plugin table (seed for `pluginCategories.ts`)

Source: marketplace overview, Oct 2026. If `plugin.json` has a `category`, that wins.

| id | category | one-liner |
|----|----------|-----------|
| cdd-aws | Platform | Query and browse AWS resources in DHP and OneCloud |
| cdd-kubectl | Platform | Inspect and manage Kubernetes resources in OneCloud CMDM EKS clusters |
| cdd-metrics | Platform | Run Prometheus queries against metrics.cba (Grafana) |
| cdd-observe | Platform | Query logs, metrics, and events in Observe using OPAL |
| cdd-pagerduty | Platform | Look up incidents, on-call schedules, and team rosters |
| cdd-pccs | Platform | Access ArgoCD, Grafana, Prometheus, and Alertmanager on CDD CMDM clusters |
| cdd-platform-essentials | Platform | Installs the core platform tools in one go |
| cdd-finops | Platform | CMDM cloud cost reports from Apptio Cloudability, with idle/waste detection |
| cdd-launchdarkly | Platform | Install and authenticate the LaunchDarkly CLI (ldcli) |
| cdd-websearch | Platform | Local web search via a self-hosted SearXNG instance |
| cdd-opsindex | Platform | Read-only OpsIndex dashboard: vulns, incidents, change risk, compliance, ownership |
| cdd-api-catalogue | Search & Discovery | Find APIs in the internal API Catalogue and fetch OpenAPI specs |
| cdd-artifactory | Search & Discovery | Search JFrog Artifactory instances |
| cdd-vault | Search & Discovery | Browse Vault secret paths and namespaces (never shows secret values) |
| cdd-sonarqube | Search & Discovery | Fetch SonarQube reports: quality gates, vulns, coverage |
| cdd-snyk | Search & Discovery | Fetch Snyk dependency and code vulnerability reports |
| cdd-getdx | Search & Discovery | Read DX (getdx.com) dashboard data |
| cdd-api-requester-local | CDD Data Access | Live authenticated calls to CDD party data services (PPD, PRDDO, LDM, etc.) |
| cdd-api-requester-mcp | CDD Data Access | MCP server with direct tool access to 9 CDD party data services in dev01 |
| cdd-cmdm-hasura | CDD Data Access | GraphQL queries against the CMDM Hasura endpoint (sit only) |
| cdd-cmdm-database | CDD Data Access | Read-only CMDM data investigation in non-prod; prod hard-blocked |
| cdd-sap-local | CDD Data Access | Send SOAP requests to the SAP ESB for business partner operations |
| cdd-sap-mcp | CDD Data Access | MCP server with direct tool access to the SAP ESB (dev01) |
| cdd-atlas | CDD Data Access | Convert Atlas Live Monitor .alm exports to readable JSON |
| cdd-pathfinder | CDD Data Governance | Trace a data element's lineage across CBA-General repos (Mermaid + XLSX) |
| cdd-atlassian-knowledge-base | Design & Specs | Create and manage Jira tickets across 16 CDD boards |
| cdd-ct-wins | Design & Specs | Create CT LT SoS "Win" Jira tickets |
| cdd-cbaspec | Design & Specs | Domain-expert interview producing functional design docs with BDD |
| cdd-customer-journey | Design & Specs | Author PLM customer journey specs with state machines and Gherkin |
| cdd-grill-me | Design & Specs | Stress-test plans one question at a time |
| cdd-improve-api-spec | Design & Specs | Iteratively fix OpenAPI specs until test agents succeed |
| cdd-rise-generator | Design & Specs | Generate RISE v3 security assessment docs via 7-phase workflow |
| cdd-spectest | CI/CD | Generate Playwright UI and API tests from Jira tickets and OpenAPI specs |
| cdd-blazemeter | CI/CD | Manage BlazeMeter v4 performance tests |
| cdd-golden-pathway | CI/CD | Set up Golden Path CI for .NET and Go APIs |
| cdd-legacy-apps-goldenpathway | CI/CD | Golden Pathway CI/CD for legacy ESD .NET Framework + Node.js apps |
| cdd-servicenow | Compliance | Look up ServiceNow CIs, incidents, changes, problems, request items |
| cdd-sdlchub | Compliance | Manage SDLC Hub deployments, evidence uploads, CI components, CRs |
| cdd-wiz | Security | Read-only Wiz cloud security queries via browser session |
| cdd-wasp | Security | Look up and draft WASP firewall/proxy requests; never submits |
| cdd-github | Git & GitHub | Search CBA-General repos and find repo ownership |
| cdd-identity | People & Identity | Check Identity.CBA access requests and search roles |
| cdd-interview | People & Identity | Run a CDD hiring interview and produce feedback |
| cdd-teamforms | People & Identity | Read-only TeamForm directory |
| cdd-m365 | People & Identity | Teams chats, calendar, meeting scheduling, M365 Copilot |
| cdd-artifacts | Claude & Meta | Create shareable static HTML pages on genai.customer.cba |
| cdd-bounty | Claude & Meta | Manage plugin bounties on the marketplace repo |
| cdd-ding | Claude & Meta | Play a sound when Claude stops or needs input |
| cdd-skill-creator | Claude & Meta | Draft, test, and refine Claude Code skills |
| cdd-marketplace-feedback | Claude & Meta | File marketplace bugs or ideas as GitHub issues |
| cdd-otel | Claude & Meta | Enable CDD telemetry in ~/.claude/settings.json |
| cdd-llm-wiki | Claude & Meta | Build a personal markdown wiki from sources |
| cdd-commsee-dev | Claude & Meta | Figma-to-PR pipeline for CommSee 2 React/Next.js apps |
| cdd-engineering-autopilot | Claude & Meta | 4-agent Planner → Architect → Developer → Reviewer delivery with approval gates |

## System aliases (for `systems[]`)

```
atlassian-knowledge-base → jira
ct-wins → jira
spectest → jira, playwright, openapi
m365 → teams, outlook, calendar
pccs → argocd, grafana, prometheus, alertmanager
metrics → prometheus, grafana
opsindex → vulns, incidents, compliance
api-requester-local, api-requester-mcp → ppd, prddo, ldm
sap-local, sap-mcp → sap, esb
cmdm-database, cmdm-hasura → cmdm
golden-pathway, legacy-apps-goldenpathway → ci, pipeline
```

## Retire into GUI features (M8, not in scope yet)
cdd-ding, cdd-otel, cdd-platform-essentials, cdd-marketplace-feedback, cdd-bounty, cdd-artifacts, cdd-websearch, cdd-skill-creator
