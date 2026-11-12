# Coding-assistant tooling

What a coding agent (Claude Code, Codex, Cursor, Agentforce Vibes) uses to build Agentforce
agents, and the loop and limits it should follow. The CLI details are in
`dev-lifecycle-and-cli.md` and testing is in `testing-and-evals.md`.

## Toolchain at a glance

| Tool | What it gives the coding agent | Needs an org? |
|---|---|---|
| `sf` CLI + `@salesforce/plugin-agent` (core plugin; 2.3.0 as of Nov 2026) | generate / validate / preview / publish / activate / test / trace / adl / mcp | Yes |
| `@salesforce/plugin-org` | `sf org create agent-user`, `sf org open agent`, `sf org open authoring-bundle` | Yes |
| Agent Script language server (`@agentscript/lsp-server`, the "Agent Script" VS Code extension) | Diagnostics, completion, hover, go-to-definition, rename, semantic tokens for `.agent` | No |
| Agentforce DX VS Code extension (`forcedotcom/vscode-agents`) | Author, preview and test in the IDE. Installs the Agent Script extension | Yes, for preview |
| Agent Script SDK `@sf-agentscript/agentforce` (npm) | `parse()` → diagnostics, `emit()` formatting, mutate components, lint. Pure TS, offline | No |
| `salesforce/agentscript` source | Compiler, dialect schemas (`agentforce`, `agentfabric`), `SPEC.md`, docs, a playground UI | No |
| Salesforce skills (`forcedotcom/sf-skills`) | Official skills: `agentforce-generate`, `-test`, `-observe`, `-bot-upgrade`, plus Flow, Apex, LWC, Data 360 skills | — |
| Salesforce DX MCP server (`@salesforce/mcp`) | **End of life 2 Nov 2026.** Archived. Pinned `0.30.15` still installs. Don't build new workflows on it | — |
| Agentforce Vibes | Salesforce's coding agent: a web IDE plus a VS Code extension. Auto-installs and updates the sf-skills | Yes |

**Version check before you start.**

```bash
sf --version && sf plugins --core | grep -E "plugin-agent|plugin-org"   # want plugin-agent ≥ 2.2 for typed context vars
sf update                                                               # if older
```

Version floors:

| Feature | Needs |
|---|---|
| `preview start --simulate-actions` | CLI ≥ 2.131 (plugin-agent ≥ 1.32.16) |
| `agent trace …` | plugin-agent ≥ 1.38 |
| `--context-variables` | plugin-agent ≥ 1.43 |
| Agentforce Studio test runner | plugin-agent ≥ 1.41 / 1.43 |

## Local validation (no org)

`sf agent validate authoring-bundle` needs an org, because it calls the compile API. Get a
fast offline check first from the SDK:

```js
// scripts/lint-agent.mjs — node scripts/lint-agent.mjs path/to/Agent.agent
import { readFileSync } from 'node:fs';
import { parse } from '@sf-agentscript/agentforce';
const doc = parse(readFileSync(process.argv[2], 'utf8'));
for (const d of doc.diagnostics) console.log(JSON.stringify(d));
process.exit(doc.hasErrors ? 1 : 0);
```

- Install it as a dev dependency or into a tool cache, not globally into the user's project
  without asking.
- **npm lags the source repo** (npm `2.10.38`, Aug 2026, vs repo `2.13.x`). If the SDK flags
  syntax that the docs say is valid, try a source build of `salesforce/agentscript`. Then
  confirm against the org's `validate`; the org decides what it accepts.
- The SDK is still evidence, not proof. **Report which validator ran:** SDK version, org
  validate, or "compiler not used".
- The `agentforce` CLI in the agentscript docs (`lint`, `compile`, `simulate`, `tools`,
  `query`) isn't published on npm today *(verify)*. Prefer the SDK and `sf`.

Two validation layers, and what each misses:

| Check | Catches | Misses |
|---|---|---|
| SDK / LSP / `sf agent validate` | Syntax, block placement, references, lint (e.g. nested `if`, `access` inside a subagent, config rules) | Missing `outputs:` on targeted actions, bad `default_agent_user`, licences, permission gaps, wrong I/O names |
| `sf agent publish` (server compile) | Target existence, agent user, server-only rules | Runtime I/O type mismatches, permissions on live calls |
| Live preview / tests | Everything that actually runs | — |

## The loop a coding agent should follow

```text
0. Orient     sf config get target-org --json; SELECT IsSandbox FROM Organization; read sfdx-project.json
1. Spec       write/confirm the agent's job, subagents, actions, success cases (agent-design); get approval
2. Discover   find existing Flows/Apex/prompt templates to reuse (sf project retrieve, org queries)
3. Scaffold   sf agent generate authoring-bundle --json --no-spec ...; stub missing @InvocableMethod / flows
4. Author     edit .agent; SDK lint after every edit; LSP diagnostics clean
5. Validate   sf agent validate authoring-bundle --json --api-name X
6. Simulate   preview start --simulate-actions → send N utterances → end → trace read (routing, actions)
7. Implement  real Apex (+tests) / flows / templates; deploy with --metadata (never the bundle by accident)
8. Live       preview start --use-live-actions (sandbox, synthetic data); fix perms/I-O from traces
9. Suite      write specs/X-testSpec.yaml; test create --preview to check shape
10. STOP      show diff, test results, target org; ask before publish/activate
11. Release   publish → activate → test create/run → gate on parsed results → record version for rollback
```

Rules inside the loop:

- **`--json` first on every `sf` call.** Read stdout as JSON and stderr as text. Never
  `2>&1`. On `status: 1`, read the whole object, because the error data can sit at the top
  level.
- **Space-separate `--metadata` types and quote wildcards:**
  `--metadata ApexClass Flow`, `"AiAuthoringBundle:X_*"`.
- **Never use the interactive forms:** `sf agent preview` (bare), `generate test-spec`
  without `--from-definition`, or `generate authoring-bundle` without `--name` and
  `--no-spec`/`--spec`. They hang.
- **Make one hypothesis-driven change per iteration,** then re-preview the failing case
  *and* a few passing ones (`agent-accuracy`).
- **Deploy Apex stubs one at a time when they fail.** The first failing class can mask the
  others.
- **Call Salesforce REST through `sf api request rest`** (beta, no `--json` flag; strip ANSI
  codes). Don't pull access tokens into shell variables: they end up in transcripts and
  traces.

## What a coding agent must not do without explicit approval

| Action | Why |
|---|---|
| `publish` / `activate` / `deactivate` | Creates a version or changes what live users get. It's a release, not a build step |
| Anything against an org where `IsSandbox = false` (or the query fails) | Production. Fail closed |
| `--use-live-actions`, `sf agent test run`, direct `actions/custom/...` calls | Real DML, emails, callouts and Flex Credit spend |
| `migrateAgentToNga`, `sf agent adl create/upload`, `sf agent mcp create` | Mutating org resources |
| Deploying `AiAuthoringBundle` while someone edits in Builder | Deploy and retrieve are silent one-way overwrites |
| Adversarial / security test generation or runs | Sends attack traffic. Confirm scope and org first |
| Changing `access`, sharing, permission sets or the agent user | Changes what data the agent can reach |
| Using real customer PII in preview or tests | Traces and transcripts persist locally and in Data Cloud |

When in doubt, finish the draft, show the evidence (diff, validate output, trace
summaries, test results), and hand the release command to the human.

## Salesforce's official skills

`forcedotcom/sf-skills` is now where Salesforce ships agent tooling, replacing the DX MCP
server.

```text
# Claude Code
/plugin marketplace add forcedotcom/sf-skills
/plugin install agentforce-adlc@salesforce     # agentforce-generate, -test, -observe, -bot-upgrade, ...
# any skills-compatible tool
npx skills add forcedotcom/sf-skills
```

- **Use them alongside this skill:** they're detailed, procedure-heavy runbooks. This skill
  is the design and decision layer above them.
- **They change often,** and their docs can lag the CLI. Example: they still say preview
  can't inject context variables. Re-check flags with `--help`.

## Builder vs code: collaboration

- **Builder** (Agentforce Studio) has canvas and script views over the same `.agent`. Admins
  edit the DRAFT there. Pro-code deploys and retrieves the same bundle.
- **There's no merge.** Agree an owner per agent, retrieve before you edit, and deploy
  soon after.
- **Canvas edits can rewrite script structure.** One reported case: adding an action in
  Canvas turned subagent-as-tool references into handoff transitions. Diff after every
  Builder round-trip.

## Anti-patterns

- Hand-writing XML for `bots/` or `genAiPlannerBundles/` instead of editing `.agent` and
  publishing.
- Trusting "it compiled" as "it works". Simulated preview, live preview and an activated
  test are three different gates.
- Building workflows on `@salesforce/mcp` after its EOL.
- Pasting full preview JSON or migration responses into context. Summarise with
  `trace read --format summary`, or keep only the fields you need (`agent-context`).

## Sources

- salesforcecli/plugin-agent (README, `messages/`, `CHANGELOG.md` v1.32–2.3.0); salesforcecli/plugin-org
- salesforce/agentscript: `README.md`, `packages/agentforce/README.md`, `packages/vscode/README.md`,
  `apps/docs/docs/packages/{cli,tooling,vscode-extension}.md`; npm registry `@sf-agentscript/agentforce`
- forcedotcom/agents `ai-docs/downstream-consumers.md` (vscode-agents = `salesforcedx-vscode-agents`)
- salesforcecli/mcp README and forcedotcom/mcp issue #46 (EOL 2 Nov 2026 → use sf-skills)
- forcedotcom/sf-skills README, `plugins/builder/agentforce-adlc/README.md`,
  `skills/agentforce-generate/SKILL.md` (Rules That Always Apply),
  `references/salesforce-cli-for-agents.md`, `references/agentscript-toolchain.md`,
  `references/upgrade-legacy-agent-to-agentscript.md` (sf api request rest guidance)
- salesforce.com/agentforce/developers/vibe-coding (Agentforce Vibes: web IDE + VS Code extension)
