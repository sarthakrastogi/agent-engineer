# Testing and evals

How to test an Agentforce agent: preview smoke tests, persistent test suites (two runners),
the eval API, multi-turn and context, CI gates, and what to put in the test set. General
eval method (error analysis, judge validation, pass^k) lives in `agent-evals`. This file
covers the Salesforce mechanics. Commands are checked against `@salesforce/plugin-agent`
2.3.0 and `@salesforce/agents` 2.4.1.

## Pick the tool

| Need | Use | Runs against | Actions |
|---|---|---|---|
| Fast check while editing | `sf agent preview start/send/end --authoring-bundle` + `sf agent trace read` | Local `.agent` (no publish) | Simulated (default) or live |
| Persistent regression suite, CI, shared with admins | `sf agent test create` + `sf agent test run` (Testing Center or Agentforce Studio runner) | **Activated** published version | Always live |
| Rich scorers, JSON payloads, quick batch runs without deploying a test | `sf agent test run-eval --spec` (beta) | Published version (`subjectName`) | Live |
| One Flow/Apex action in isolation | `POST /services/data/vXX.X/actions/custom/{flow\|apex}/<Name>` | The org | Live |
| Production behaviour | Session Tracing data in Data Cloud (see observability reference) | Real sessions | Real |

Preview is free to iterate on. Suites only test a published, activated version.
`sf agent test run` has **no simulate mode**, so every case fires the real Apex, Flows and
prompt templates. Run suites in a sandbox or scratch org with synthetic data.

## Preview smoke testing

```bash
SID=$(sf agent preview start --json --authoring-bundle Order_Agent --simulate-actions \
      --context-variables-json '[{"name":"customer_verified","type":"Boolean","value":false}]' \
      | jq -r .result.sessionId)
sf agent preview send --json --authoring-bundle Order_Agent --session-id "$SID" -u "Where is order 1001?"
sf agent preview send --json --authoring-bundle Order_Agent --session-id "$SID" -u "It's jane@example.com"
sf agent preview end  --json --authoring-bundle Order_Agent --session-id "$SID"
sf agent trace read --session-id "$SID" --format detail --dimension routing
sf agent trace read --session-id "$SID" --format detail --dimension actions
```

- **Simulated** actions test routing, action selection and instructions before the backing
  code exists. **Live** actions test the real I/O, permissions and data. A case that passes
  simulated and fails live is almost always permissions, `WITH USER_MODE` without object
  access, or an I/O mismatch.
- Use one fresh session per scenario. Context left over from earlier turns invalidates
  both PASS and FAIL.
- Preview output can contain control characters. Strip `[\x00-\x08\x0b\x0c\x0e-\x1f]`
  before `json.loads` if a parse fails.

### Reading a raw trace (`--format raw`)

| Step type | Tells you |
|---|---|
| `NodeEntryStateStep` (`data.agent_name`) | Which subagent was entered |
| `EnabledToolsStep` (`data.enabled_tools`) | Actions visible this turn (checks `available when`) |
| `BeforeReasoningIterationStep` (`data.action_names`) | Actions run deterministically before reasoning |
| `ReasoningStep` / LLM step | Why the planner chose what it chose; grounding category |
| `FunctionStep` | An action actually executed, with inputs and outputs |
| `VariableUpdateStep` | `variable_name: past → new (reason)` |
| `PlannerResponseStep` (`message`, `safetyScore`) | Final text and Trust Layer safety score |

Symptom → fix:

| Symptom | Fix |
|---|---|
| Wrong or default subagent | Sharpen subagent `description`s or router transitions |
| Action missing from `EnabledToolsStep` | Relax or fix the `available when` guard |
| Wrong action | Add "use X instead when…" to both descriptions |
| Answer not grounded in data | Reference `{!@variables.x}` / outputs in the instructions; check retrieval |
| `FunctionStep` with no output captured | An `@inputs` / `@outputs` scope error |

## Test suites: two runners

| | Testing Center (legacy) | Agentforce Studio ("NGT") |
|---|---|---|
| Metadata | `AiEvaluationDefinition` | `AiTestingDefinition` |
| YAML shape | Flat: `utterance`, `expectedTopic`, `expectedActions`, `expectedOutcome`, `metrics`, `customEvaluations` | `inputs[]` + `scorers[]` |
| Select | default for `test create`, or `--test-runner testing-center` | `--test-runner agentforce-studio` |
| Default spec file | `specs/<Agent>-testSpec.yaml` | `specs/<Agent>-ngtTestSpec.yaml` |
| Can subject a prompt template | No | Yes (`subjectType: PROMPT`) |

`test list` shows each test's type. `test run`, `test results` and `generate test-spec`
auto-detect the runner from the org's metadata. Pass `--test-runner` to override.

### Testing Center spec (`AiEvaluationDefinition`)

```yaml
name: Order Agent Regression            # MasterLabel
description: Routing, actions, guardrails for Order_Agent
subjectType: AGENT
subjectName: Order_Agent                # BotDefinition DeveloperName, not the _vN planner name
testCases:
  - utterance: "Where is order 1001?"
    expectedTopic: order_status          # subagent name; see "name resolution" below
    expectedActions: [check_order]       # flat list of reasoning-level invocation names; superset match
    expectedOutcome: "Agent looks up order 1001 and states its delivery status"
    metrics: [coherence, completeness, output_latency_milliseconds]
  - utterance: "Yes, it's jane@example.com"
    expectedTopic: identity_verification
    expectedActions: [verify_customer]
    expectedOutcome: "Agent verifies the email and moves on to the order question"
    contextVariables:
      - { name: RoutableId, value: "0Mw000000000001" }
    conversationHistory:                 # prior turns; the final user turn is `utterance`
      - { role: user,  message: "I need to check an order" }
      - { role: agent, topic: identity_verification, message: "Sure — what's the email on the account?" }
  - utterance: "Give me a recipe for chocolate cake"
    expectedOutcome: "Agent declines politely and offers order help instead"   # guardrail: no topic/actions
  - utterance: "Cancel order 1001"
    expectedOutcome: "Agent asks for confirmation before cancelling"
    customEvaluations:
      - name: string_comparison          # or numeric_comparison
        label: cancel tool got the right order id
        parameters:
          - { name: operator, value: equals, isReference: false }   # equals|greater_than[_or_equal]|less_than[_or_equal]
          - { name: actual,   value: "$.generatedData.invokedActions[*][?(@.function.name == 'Cancel_Order')].function.input.orderId", isReference: true }
          - { name: expected, value: "1001", isReference: false }
```

Field rules:

- `expectedActions` takes the **level-2 invocation names** under `reasoning: actions:`
  (`check_order: @actions.get_order_status` → `check_order`). It does not take the level-1
  definition names. Matching is superset: extra actions, including transitions, still pass.
  `[]` asserts nothing.
- `expectedOutcome` is checked by an LLM judge (`output_validation`). It's the most robust
  assertion. Write what must be true, not exact wording.
- Legacy `metrics`: `completeness`, `coherence`, `conciseness` and
  `output_latency_milliseconds`. Field reports: `conciseness` has returned 0, and
  `instruction_following` has broken the Testing Center UI *(verify in your org)*.
- Leave out `expectedTopic` and the generated XML still has an empty `topic_assertion`
  that reports FAILURE. On guardrail cases, ignore it and count `output_validation`.
- `customEvaluations` assert on `generatedData` through JSONPath. Get the real path from
  `sf agent test run --verbose`, which prints invoked actions, inputs and objects touched.
  Custom evaluations have crashed the results API in some orgs, so add one, run it, then
  add the rest *(verify in your org)*.
- A single-turn case only sees the first response. When an action fires only after the
  agent collects information, use `conversationHistory`, or assert the outcome instead.

### Agentforce Studio spec (`AiTestingDefinition`)

```yaml
name: OrderAgentSuite
subjectType: AGENT                       # or PROMPT for a prompt template
subjectName: Order_Agent
subjectVersion: v3                       # optional: pin the version under test
testCases:
  - inputs:                              # several inputs = same scorers over paraphrases
      - utterance: "Where is order 1001?"
      - utterance: "status of 1001 pls"
    scorers:
      - { name: topic_sequence_match,  expected: order_status }
      - { name: action_sequence_match, expected: "['Verify_Customer','Get_Order_Status']" }
      - { name: bot_response_rating,   expected: "Agent returns the order status" }
      - { name: output_latency_milliseconds }
  - inputs:
      - utterance: "Yes, my email is jane@example.com"
        contextVariables: [{ name: RoutableId, value: "0Mw000000000001" }]
        conversationHistory:
          - { role: user,  message: "I need help with my order" }
          - { role: agent, topic: identity_verification, message: "What's your email on file?" }
    scorers:
      - { name: task_resolution }        # needs conversationHistory
      - { name: factuality }
  - inputs: [{ utterance: "I need a sales rep" }]
    scorers:
      - { name: agent_handoff_match, expected: SDR_Agent }   # target agent DeveloperName
```

| Scorer | Needs `expected` | Grade | Subjects |
|---|---|---|---|
| `topic_sequence_match`, `action_sequence_match`, `agent_handoff_match` | yes | PASS/FAIL | AGENT |
| `bot_response_rating` | yes | LLM pass/fail | AGENT |
| `response_match` | yes | LLM pass/fail | AGENT, PROMPT |
| `coherence`, `conciseness`, `factuality`, `completeness` | no | LLM 0–100 | AGENT, PROMPT |
| `task_resolution` | no (needs history) | LLM 0–5 | AGENT |
| `output_latency_milliseconds` | no | numeric | AGENT |

Unknown scorer names only produce a warning locally. The org's validator is the real gate.
Custom scorers aren't authorable from YAML yet. Hand-edit `<scorer scorerType="Custom">`
in the XML.

### Create, run, read

```bash
sf agent test create --json --spec specs/Order_Agent-testSpec.yaml --api-name Order_Agent_Regression \
  --force-overwrite [--preview] [--test-runner agentforce-studio]   # --preview: write XML, deploy nothing
sf agent test list   --json
sf agent test run    --json --api-name Order_Agent_Regression --wait 20 --result-format json \
  --output-dir test-results [--verbose]
sf agent test resume  --json --job-id <ID> --wait 10      # if --wait expired
sf agent test results --json --job-id <ID> --result-format junit --output-dir test-results
sf agent generate test-spec --from-definition force-app/main/default/aiEvaluationDefinitions/X.aiEvaluationDefinition-meta.xml \
  --output-file specs/X-testSpec.yaml                      # round-trip XML → YAML
```

- `--result-format` takes `human`, `json`, `junit` or `tap`. `--api-name` is the test's
  name (max 80, `[A-Za-z][A-Za-z0-9_]*`), not the agent's name.
- `sf agent generate test-spec` without `--from-definition` is interactive. Coding agents
  should write the YAML directly.
- `create` validates the whole spec server-side. One bad case rejects all of them, and the
  error doesn't name the case. Bisect, or validate shape locally first.
- Pass `--job-id` explicitly. Don't rely on `--use-most-recent` in scripts; it has been
  unreliable.
- **Name resolution.** The runtime topic name in `generatedData.topic` can differ from the
  `.agent` name. Promoted or standard topics carry hash suffixes
  (`p_16j…_Topic_16j8…`), and the hash changes on republish. If topic assertions fail on
  correct behaviour, read the actual names from the results JSON, update the spec, and
  recreate it with `--force-overwrite`.

### `run-eval` (beta)

```bash
sf agent test run-eval --json --spec specs/Order_Agent-testSpec.yaml --result-format junit   # agent from subjectName
sf agent test run-eval --json --spec specs/eval-payload.json --api-name Order_Agent --batch-size 5
cat payload.json | sf agent test run-eval --json --spec --target-org ci
```

`run-eval` runs the same YAML spec, or a raw JSON payload, through the Einstein evaluation
API. It needs no `AiEvaluationDefinition`, and it has more evaluators than Testing Center:
subagent routing, action invocation, string and numeric assertions, semantic similarity and
LLM quality ratings. `--batch-size` max is 5. The JSON is normalised unless you pass
`--no-normalize`. Exit codes: 0 = ran (check pass/fail in the JSON), 1 = execution errors,
2 = agent or spec not found, 4 = API or network failure. The agent needs at least one
published version.

## CI gate

**The exit code is not the gate.** `test run` and `test resume` exit 1 only when a case has
`status: ERROR` (couldn't run). Failed assertions exit 0. Parse the results:

```python
# scripts/gate_agent_tests.py  results.json --min-pass 0.9
import json, sys
data = json.load(open(sys.argv[1]))["result"]
min_pass = float(sys.argv[sys.argv.index("--min-pass") + 1])
cases = data.get("testCases", [])
def case_passed(tc):
    has_topic = bool((tc.get("expectations") or {}).get("expectedTopic", True))
    rs = [r for r in tc.get("testResults", [])
          if not (r["name"] == "topic_assertion" and not has_topic)]   # skip empty topic assertion
    return tc.get("status") != "ERROR" and all(r["result"] == "PASS" for r in rs)
passed = sum(case_passed(tc) for tc in cases)
rate = passed / max(len(cases), 1)
print(f"{passed}/{len(cases)} passed ({rate:.0%})")
for tc in cases:
    if not case_passed(tc):
        print("FAIL:", tc["inputs"]["utterance"][:80],
              {r["name"]: r["result"] for r in tc.get("testResults", [])})
sys.exit(0 if rate >= min_pass else 1)
```

Check the field names against one real `--result-format json` run in your org. The shape
differs between runners: Agentforce Studio returns `testScorerResults[]` per case.

Gate policy:

- **Hard gates** (`agent-evals` → `ci-and-online-evals.md`): security and guardrail cases
  at 100%, and must-route and must-act cases.
- **Threshold, tracked across runs:** LLM-judged quality.
- **Repeats:** run each case at least 3× and compare pass^k, because the planner isn't
  deterministic. A single green run proves little.

## Designing the test set

Start from real traffic (Session Tracing utterances, transcripts, cases) and error
analysis (`agent-evals` → `error-analysis.md`). Utterances you invent cover less. Minimum
coverage per agent:

| Bucket | Cases | Assert |
|---|---|---|
| Routing | ≥ 2 paraphrases per subagent, including terse/misspelt ones | topic |
| Actions | Each action's main use, plus a near-miss where it must **not** fire | actions (+ custom eval on key inputs) |
| Multi-turn | Each slot-filling or verification flow, via `conversationHistory` | actions + outcome / `task_resolution` |
| Context | Each linked/context variable branch (`RoutableId`, verified flags) | outcome |
| Gated actions | An `available when` bypass attempt for every write action | outcome ("does not perform X without Y") |
| Off-topic / refusal | Off-scope, regulated advice, competitor questions | outcome |
| Escalation | An explicit human request, frustration, repeated failure | topic/action = escalation |
| Adversarial | Injection, prompt extraction, PII fishing, all phrased in the agent's domain | outcome only |
| Data edge cases | Not found, many results, malformed IDs, empty knowledge | outcome |

- Derive security cases from the agent's real attack surface: write actions, `available
  when` gates, LLM-filled inputs, linked variables, knowledge grounding. See
  `agent-guardrails` → `red-teaming.md`. "Ignore previous instructions" alone proves
  nothing.
- **Get explicit confirmation before generating or running adversarial suites, and check
  the org first.** `SELECT IsSandbox FROM Organization`: if it fails or returns false,
  stop.
- Version specs next to the `.agent` file. Regenerate security cases whenever actions,
  gates or variables change.
- **Migrations:** run the same suite against legacy (`--api-name`) and the draft
  (`--authoring-bundle`). Grade both against the *intended* route and actions. When the
  draft fixes a legacy defect, that's a pass, not a regression.

## Voice

Preview and Testing Center are text-only: no audio, TTS or STT checks.

- **Text-proxy checks:** 1–2 sentence turns, no markdown or lists, numbers in spoken form,
  read-back before writes, and an acknowledgement before slow actions.
- **Final QA:** a live voice channel or Builder's voice preview.

## Anti-patterns

- Gating CI on the `sf agent test run` exit code.
- Level-1 action names in `expectedActions`.
- Exact-string expectations for LLM responses.
- Running adversarial or `test run` suites in production. They fire real actions.
- A suite of 10 happy-path utterances written by the agent's author.
- Retesting after publish with only the simulated preview. Live actions and the activated
  version behave differently: always test again after publishing.

## Sources

- salesforcecli/plugin-agent v2.3.0: `messages/agent.test.*.md`, `agent.generate.test-spec.md`,
  `agent.preview.*.md`, `agent.trace.*.md`, `messages/shared.md`, `src/flags.ts`,
  `src/commands/agent/test/run.ts` (exit-code logic), `run-eval.ts`,
  `test/mock-projects/agent-generate-template/specs/*.yaml`
- forcedotcom/agents v2.4.1: `src/ngtScorerCatalog.ts`, `src/types.ts`, `src/yamlSpecTranslator.ts`
- forcedotcom/sf-skills and SalesforceAIResearch/agentforce-adlc `skills/agentforce-test/SKILL.md`,
  `references/batch-testing.md`, `references/troubleshooting.md`, `references/preview-testing.md`;
  `skills/agentforce-generate/references/legacy-nga-regression-eval.md`
- salesforce/agentscript `apps/docs/docs/dialects/agentforce/ascript-manage.md` ("always
  test agents again after they're published")
