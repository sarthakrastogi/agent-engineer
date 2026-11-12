# Gotchas and limits

The limits, costs and sharp edges that bite Agentforce builds, with the fix for each.
Salesforce publishes few hard numbers for Agent Script agents. Each line here is marked
**[doc]** (Salesforce source or docs), **[src]** (CLI or compiler source), **[field]**
(observed and reported by Salesforce's own skill authors), or *(verify in your org)*.
Limits change by release, so re-check before you design around one.

## Limits

| Limit | Value | Basis |
|---|---|---|
| Developer names (agent, action, variable) | Start with a letter; alphanumeric + `_`; no trailing `_` or `__`; ≤ 80 chars | [doc] |
| Agent spec generation | 1–30 topics (`--max-topics`, default 5) | [src] |
| Test API name | ≤ 80 chars, `^[A-Za-z][A-Za-z0-9_]*[A-Za-z0-9]+$` | [src] |
| `run-eval` batch size | ≤ 5 tests per API request | [src] |
| Active versions per agent | 1 | [doc] |
| Agent response size | 1 MB (1,048,576 bytes) | [field] |
| Plan trace in Builder UI | ~1M chars; backend trace ~32k tokens | [field] |
| Active/committed agents per org | 100 | [field] *(verify)* |
| Subagent loop guard | ~3–4 transitions in a loop before the runtime breaks out to the router | [field] |
| Large action outputs | > ~50 KB of output in state → later variable reads return null or stale | [field] |
| Phone messaging channels | One `Phone` channel per org; use `PstnVoice` for voice | [field] |
| Subagents per agent / actions per subagent | No published Agent Script limit found. Keep it small for routing quality | *(verify)* |

Design for routing accuracy long before you hit a platform limit.

- **Few subagents, each with a sharply different description.** The docs say "start with
  essential subagents and add more gradually".
- **Few actions per subagent, made visible per turn with `available when`.**
- See `agent-tools` → tool-count guidance.

### Apex governor limits (each action invocation is an Apex transaction)

| Limit | Synchronous |
|---|---|
| SOQL queries | 100 |
| SOQL rows | 50,000 |
| DML statements / rows | 150 / 10,000 |
| CPU time | 10,000 ms |
| Heap | 6 MB |
| Callouts per transaction | 100 |
| Callout timeout | 120 s max per callout (default 10 s) |

Agents call `@InvocableMethod` with a *list* of requests, so bulkify anyway. Return
small, summarised outputs; don't return raw query results. The agent's own end-to-end
latency budget is much tighter than Apex's CPU limit *(verify the action timeout in your
org)*.

## Cost: Flex Credits

| Item | Price |
|---|---|
| Flex Credits | $500 per 100,000 credits |
| Agentforce action | 20 credits (≈ $0.10) |
| Agentforce Voice action | 30 credits |
| Alternative: per conversation | $2 |
| Agentforce user licence (requires Flex Credits) | $5/user/month |
| Agentforce add-ons | $125/user/month (Industries $150) |

Prices are from salesforce.com/agentforce/pricing (Nov 2026). Data 360 credits are billed
separately.

What's billed, as reported by Salesforce's ADLC skill authors [field]:

| Free | Billed |
|---|---|
| `@utils.transition`, `@utils.setVariables`, `@utils.escalate` | Each Flow, Apex or other action run: 20 credits |
| `if`/`else`, `set`, `before_reasoning`, `after_reasoning` | Prompt template invocations: 2–16 credits by size |
| The reasoning turn itself | |

Confirm in your contract.

**Cost levers.**

- **Deterministic routing and state cost nothing.** Move "always fetch X" into
  `before_reasoning` with `run`. It's still billed per action, but it runs exactly once
  instead of whenever the LLM decides to.
- **Cache action outputs** that later turns need, in variables with a defined reset.
- **Merge chatty actions:** one action returning a summary beats three lookups.
- **Watch prompt-template actions inside loops.**
- **Test suites and live previews spend credits too.** Budget for CI runs × cases × repeats.

## Sharp edges, with fixes

### Authoring and compile

| Symptom | Cause | Fix |
|---|---|---|
| `syntax-error: unexpected 'with …'` under a `run` | A trailing `# comment` on the `run @actions.x` line (SDK 2.10.38; reasoning-action and `if` lines are fine) | Put the comment on its own line above `run` |
| `validate` passes, `publish` fails with "Internal Error, try again later" | Most often: (1) a service agent without a valid Einstein Agent `default_agent_user`, or `AgentforceServiceAgentUser` not assigned; (2) a targeted action with no `outputs:` block; (3) other structural drift. Rarely transient | Check those in order. Diff against a known-good bundle in the same org. Retry once after 60 s only if 1–3 are clean |
| `SyntaxError: Unexpected 'model'` (or `description`, `label`) at agent compile | Reserved word used as an `@InvocableVariable` name. Apex compiles fine | Rename (`vehicle_model`, `issue_description`), redeploy, republish |
| `Duplicate value found: GenAiPluginDefinition` | `start_agent` and a `subagent` share a name | Rename one |
| Deploy fails "content cannot be changed on a locked version" | `<target>` in `bundle-meta.xml` (you retrieved after a publish) | Delete `<target>` and redeploy |
| Warning "…_4.agent returned from org, but not found in the local project" | Server-side version-suffixed filename | Harmless |
| `if` branch silently does nothing | The branch holds only comments | Put at least one executable line (`|`, `run`, `set`, `transition`) in every branch |
| Nested `if` rejected | The Agentforce lint forbids nesting | Flatten with `and` / `else`, or move logic into an action |
| `access:` inside a subagent ignored, with a diagnostic | `access` is top-level only | Move it to the top level |
| `connections:` (plural) syntax error | Wrong block | `connection messaging:` / `connection customer_web_client:` / `connection telephony:` (singular, top level) |
| `outbound_route_name` → `ERROR_HTTP_404` on publish | Bare flow name | `"flow://Route_Flow_Name"`. Route type, name and message are all-or-nothing |
| Builder preview: "Something went wrong" | No `CustomerWebClient` planner surface | Add `connection customer_web_client:` (underscores; `customerwebclient` 404s) |
| `apex://` target "does not exist" in a namespaced org | Namespace resolution at publish | Try `apex://ns__Class`, or wrap it in a flow *(open issue)* |
| Asset Library action inputs fail to parse | Unquoted names such as `Input:query` | `with "Input:query" = …` |
| Migration output unusable from the CLI | Developer name ends in `_<digits>` | Rename before migrating (`Agent_v01`) |

### Runtime behaviour

| Symptom | Cause | Fix |
|---|---|---|
| Whole agent fails with a permission error even though this subagent's actions are fine | The planner checks permissions for **all** actions at startup | Grant every Apex class / flow / object in `<Agent>_Access`; split agents by permission boundary |
| "invocable action … does not exist" at runtime | Class missing from the permission set (auto-generated `NextGen_…` set incomplete) | Use your own permission set with all `classAccesses` |
| Live preview returns empty data, simulated preview is fine | `WITH USER_MODE` + no object access → 0 rows, no error | Grant object and field access to the agent user |
| Grounded answers always "I don't have that information" | Agent user lacks a Data Cloud permset/PSL or Data Space scope | Assign one (`GenieDataPlatformStarterPsl` / `GenieUserEnhancedSecurity` / `DataCloudUser`). Add the data space in Setup (UI only) |
| `set @variables.x = @inputs.y` silently does nothing | `@inputs` exists only inside `with`. `@outputs` exists only in the `set`/`if` right after the `run` | Read outputs straight after `run`. Have the action return what you need |
| `require_user_confirmation: True` never asks the user | Compiles but has no runtime effect for Agent Script | Two-step pattern: the LLM asks, then the action has `available when @variables.user_confirmed == True` |
| Welcome message shows `{!@variables.name}` literally, or loses line breaks | The welcome renders before variables resolve, and newlines are stripped | Static one-line welcome. Personalise in the first turn |
| State lost after a big action | Large outputs pushed earlier state out | Return less; `filter_from_agent: True` on outputs the LLM doesn't need |
| Rich LWC / Custom Lightning Type output shows as plain text | Renderer not supported on that surface/planner | Check the channel supports it; text fallback (see the Lightning components reference) |
| Cross-agent "Node does not have corresponding subagent" | Same-org multi-agent node references | Delegate within one agent, or use escalation or A2A handoff |
| External Service action breaks after a release with no schema change | Stricter OpenAPI validation | Add `info.version`, resolve every `$ref`, drop non-standard `x-` extensions |
| `EinsteinHyperClassifier` router ignores `before_reasoning` / `setVariables` | Not supported on that model | Only `@utils.transition` in a HyperClassifier router. No images |
| Duplicate Hebrew/Indonesian entries in the language picker | UI bug; the second entry fails on save | Pick the first entry |

### CLI and CI

| Symptom | Cause | Fix |
|---|---|---|
| CI green, tests actually failing | `sf agent test run` exits 0 on failed assertions | Parse the results JSON (see `testing-and-evals.md` → CI gate) |
| Deploy hangs for minutes | `AiEvaluationDefinition` files in a `--source-dir` deploy | Deploy by `--metadata`; keep tests out of the main deploy path |
| `Nonexistent flag: --simulate-actions` | CLI < 2.131 | `sf update` |
| `MissingModeFlag` on `preview start` | `--authoring-bundle` without a mode | Add `--simulate-actions` or `--use-live-actions` to `start` only |
| `RequiresProjectError` | Not running in an SFDX project | `cd` to the directory with `sfdx-project.json` |
| `No valid version available` / `has no active version` | Published but not activated | `sf agent activate --api-name X --version N` |
| `sf org create user` fails in a sandbox | Scratch orgs only | `sf org create agent-user`, or `sf data create record --sobject User` |
| `UnsupportedBundleTypeError: AiAuthoringBundle` | Source-tracking push/pull | Explicit `--metadata AiAuthoringBundle:X` |
| JSON parse errors on preview output | Control characters, or `2>&1` merged the update banner | Never merge stderr; strip control chars |
| `sf bot …` not found | Removed in sf v2 | `sf agent …` |
| Topic assertions fail after a republish | Hash-suffixed runtime topic names changed | Re-read actual names from the results; recreate the test |

## Before go-live checklist

- [ ] Every target exists in the prod org. Permission set covers every class, flow and
  object. The agent user is active with the right licence.
- [ ] Every write action is gated (`available when` on verified state) and idempotent.
  Confirmation done by pattern, not `require_user_confirmation`.
- [ ] Action outputs are small. Sensitive outputs are `filter_from_agent: True`.
- [ ] `connection` blocks for each channel. Escalation route flow exists and is tested.
- [ ] Suite (routing, actions, multi-turn, guardrails, adversarial) passes on the
  *activated* version with ≥ 3 repeats. Results parsed, not exit-coded.
- [ ] Credit estimate: actions per conversation × conversations × 20.
- [ ] Rollback version number recorded. Session Tracing / enhanced event logs on
  (`enable_enhanced_event_logs`) so you can debug production.

## Sources

- salesforce.com/agentforce/pricing (Flex Credits, per-action and voice credits, licences; Nov 2026)
- salesforce/agentscript `apps/docs/docs/dialects/agentforce/reference/ascript-ref-{actions,variables}.md`,
  `ascript-blocks.md` (HyperClassifier limits), `patterns/ascript-patterns-topic-selector.md`,
  `dialect/agentforce/src/lint/passes/config-validation.ts`, `src/tests/access.test.ts`
- salesforcecli/plugin-agent v2.3.0 `src/commands/agent/test/{run,run-eval,resume}.ts`,
  `src/flags.ts`, `messages/shared.md`, `messages/agent.generate.agent-spec.md`
- forcedotcom/sf-skills and SalesforceAIResearch/agentforce-adlc `skills/agentforce-generate/references/`:
  `known-issues.md` (Issues 1–21), `production-gotchas.md` (credit table, size limits, loop
  guard, reserved names, `@inputs` scope), `agent-user-setup.md`, `deploy-reference.md`,
  `feature-validity.md`, SKILL.md "Common Issues Quick Reference";
  `skills/agentforce-test/references/troubleshooting.md`
- Apex Developer Guide, "Execution Governors and Limits" (developer.salesforce.com/docs/atlas.en-us.apexcode.meta/apexcode/apex_gov_limits.htm)
