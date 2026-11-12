# Agent Script architecture patterns

Pick the smallest structure that meets the success criteria, then add determinism where a
test shows the model can't be trusted. Snippets use real Agent Script shapes; full
deployable versions live in `trailheadapps/agent-script-recipes` and the ADLC
`assets/patterns/` folder.

## Choosing a shape

| Situation | Pattern |
|---|---|
| One domain, one set of actions and rules | **Single scope**: `start_agent` does the work, zero subagents |
| Several domains that differ in objective, actions, authority or escalation | **Router-first**: `start_agent` routes, one subagent per domain |
| Sensitive data, payments, PII behind identity | **Verification gate** (deterministic transition) |
| Several protected subagents share one auth step and must resume afterwards | **Open gate** |
| Specialist work, then back to the caller who answers | **Delegation** (`@subagent.X` as a tool) |
| Hand the conversation over for good | **Transition** (`@utils.transition to`) |
| Ordered steps where each later action needs an earlier success | **Multi-step workflow** (output-gated actions) |
| Long interview where the next question depends on the last answer | **Step variable** driving the router |
| Data the model needs before it speaks | **Fetch before reasoning** (`run` at the top) |
| Validation, limits, business rules | **Deterministic validation** + gated write |
| Human handoff | **Escalation** (`connection messaging` + `@utils.escalate`) |
| Destructive actions | **Safety state machine** (confirm → check → act) |
| Same agent, different tone or rules per area | **System override** per subagent |

Add a subagent only when the boundary changes at least one of: objective, instructions,
available actions, authority, escalation behaviour. Fewer subagents means clearer routing.

## Single scope

```agentscript
start_agent product_qa:
    description: "Answer questions about products, pricing and availability"
    actions:
        get_product_info:
            description: "Look up a product's details, price and stock"
            inputs:
                product_name: string
                    is_required: True
            outputs:
                product_details: string
            target: "flow://GetProductInfo"
    reasoning:
        instructions: ->
            | Answer product questions. For a specific product use {!@actions.lookup}.
              If you don't know, say so.
        actions:
            lookup: @actions.get_product_info
                with product_name=...
```

Don't add a router that only transitions to one subagent: it's an extra LLM hop.

## Router-first

```agentscript
start_agent agent_router:
    description: "Route each message to one domain"
    model_config:
        model: "model://sfdc_ai__DefaultEinsteinHyperClassifier"   # fast, transition-only
    reasoning:
        instructions: |
            Select exactly one route. Do not answer the user.
        actions:
            go_to_orders: @utils.transition to @subagent.Orders
                description: "Order status, delivery and changes. Not returns."
            go_to_returns: @utils.transition to @subagent.Returns
                description: "Returns and refunds within the 60-day window"
            go_to_billing: @utils.transition to @subagent.Billing
                description: "Invoices, charges and payment methods"
```

- Routing contract = the transition **descriptions**. Keep them distinct, in users' words,
  with exclusions. Name transitions `go_to_<x>`.
- Keep routing in `start_agent`. A separate "main_menu" subagent duplicates the router and
  adds latency.
- Subagent → subagent transitions when the workflow continues naturally; back to the
  router only when reclassification is needed (it happens automatically next turn anyway).
- Multi-intent message: route the first domain; tell the model to pick up the other from
  history afterwards. No queue variable needed.
- If routing lands on the wrong subagent, fix descriptions first, then `available when`,
  then deterministic transitions.

**EinsteinHyperClassifier** (`model://sfdc_ai__DefaultEinsteinHyperClassifier`): Salesforce's
routing model, the default router for Service Agent templates. Faster and better at
negative instructions. Restrictions (compile errors): only `@utils.transition` tools, no
`before_reasoning` / `after_reasoning`, no `setVariables`, no images, can't answer or ask
clarifying questions. If the entry node must converse, use a normal model.

## Verification gate (required workflow)

```agentscript
start_agent agent_router:
    description: "Route verified users; send everyone else to verification"
    reasoning:
        instructions: ->
            if @variables.verified == False:
                transition to @subagent.Identity     # no LLM call, no bypass
            | Select the best tool for the user's request.
        actions:
            go_to_orders: @utils.transition to @subagent.Orders
                description: "Order lookup and updates"
```

`available when` filtering **removes** options but doesn't force a step — the model can
still pick an unprotected route. For prerequisites, a top-of-instructions conditional
transition is the only guarantee. Also gate inside each protected subagent
(`before_reasoning: if @variables.verified is not True: transition to @subagent.Identity`)
so a direct subagent-to-subagent transition can't skip it. Set `verified` only from a
trusted action output, never from `setVariables`.

## Open gate (shared auth with resume)

When several protected subagents need one auth subagent and the user should land back where
they started:

```agentscript
variables:
    open_gate: mutable string = ""
        description: "Subagent currently holding the conversation; router bypasses classification while set"
    next_subagent: mutable string = ""
        description: "Where to resume after authentication"

start_agent agent_router:
    description: "Gate-aware router"
    reasoning:
        instructions: ->
            if @variables.open_gate == "product_return":
                transition to @subagent.product_return
            if @variables.open_gate == "customer_authentication":
                transition to @subagent.customer_authentication
            | Route to the matching subagent. Don't ask for authentication here.
        actions:
            start_return: @utils.transition to @subagent.product_return
                description: "Product return, exchange or refund"

subagent product_return:
    description: "Process returns for authenticated customers"
    reasoning:
        instructions: ->
            if @variables.authenticated == False:
                set @variables.next_subagent = "product_return"
                transition to @subagent.customer_authentication
            set @variables.open_gate = "product_return"
            | Ask for the order ID and reason.

subagent customer_authentication:
    description: "Verify identity"
    # ... verify tool sets @variables.authenticated from a flow output ...
    after_reasoning:
        if @variables.authenticated == True and @variables.next_subagent == "product_return":
            set @variables.open_gate = "product_return"
            transition to @subagent.product_return
```

Clear `open_gate` when the task ends (`set @variables.open_gate = ""`) and offer an exit
transition, or the user is trapped. Don't use a gate just to keep follow-up turns in a
subagent; history already does that.

## Transition vs delegation

| | `@utils.transition to @subagent.X` | `@subagent.X` as a tool |
|---|---|---|
| Control | One-way; X produces the reply | X runs, control returns; caller synthesises |
| Prompt | Caller's prompt discarded | Caller keeps reasoning, can call more tools |
| Use for | Handoffs, checkout, terminal states, escalation subagent | Expert consultation, sub-task with its own actions |

```agentscript
subagent general_support:
    description: "Account questions; consults billing for complex issues"
    reasoning:
        instructions: ->
            if @variables.specialist_findings:
                | Summarise the specialist's findings using only: {!@variables.specialist_findings}
            else:
                | Collect the customer ID and issue with {!@actions.collect_issue_details},
                  then consult {!@actions.consult_billing_specialist}.
        actions:
            collect_issue_details: @utils.setVariables
                description: "Store customer ID and a short issue description"
                with customer_id=...
                with issue_description=...
            consult_billing_specialist: @subagent.billing_specialist
                description: "Analyse a complex billing issue"
                available when @variables.customer_id != "" and @variables.issue_description != ""
```

The delegated subagent writes results to variables and has no transition back. Re-check
the script after Canvas edits; Canvas has been reported to turn delegations into
transitions.

**Bidirectional with transitions**: if the specialist must own the reply for a while,
transition there and transition back explicitly (`back_to_main: @utils.transition to
@subagent.main`). The target restarts from its top. Store a return-address variable only if
one specialist serves several callers.

## Multi-step workflow (output-gated)

```agentscript
reasoning:
    actions:
        check_dependencies: @actions.check_dependencies
            available when @variables.delete_state == "check_dependencies" and @variables.user_confirmed_deletion == True
            with record_id = @variables.record_id
            set @variables.dependency_count = @outputs.count
            set @variables.delete_state = "check_dependencies_ran"
        delete_record: @actions.delete_record
            available when @variables.delete_state == "check_dependencies_ran" and @variables.dependency_count == 0
            with record_id = @variables.record_id
            set @variables.delete_succeeded = @outputs.success
            set @variables.delete_state = "delete_ran"
```

Each step's tool becomes available only after the previous step's **output** says it
succeeded. Step numbers in prompt text ("Step 4: submit") don't enforce anything — the model
may see step 4 without steps 1–3. When order is fixed and needs no judgment, chain with
`run` instead.

## Step variable (multi-turn interview)

```agentscript
start_agent agent_router:
    description: "Drive the screening interview"
    reasoning:
        instructions: ->
            if @variables.currentInterviewStep == "Permission":
                transition to @subagent.permission
            else if @variables.currentInterviewStep == "Eligibility":
                transition to @subagent.eligibility
            else if @variables.currentInterviewStep == "End":
                transition to @subagent.end_interview

subagent permission:
    description: "Confirm the candidate's right to work"
    reasoning:
        instructions: ->
            | Ask whether they have the right to work. If yes, call {!@actions.setStep}
              with "Eligibility"; if no, with "End". Ask follow-ups until the answer is clear.
        actions:
            setStep: @utils.setVariables
                description: "Set the next interview step"
                with currentInterviewStep = ...
```

One responsibility per subagent; the subagent owns its completion test. For lists of
questions, pair a `list[string]` with an index variable and increment it — there's no
`for` loop.

## Fetch before reasoning

```agentscript
reasoning:
    instructions: ->
        if @variables.order_summary == "":
            run @actions.lookup_current_order
                with member_email=@variables.member_email
                set @variables.order_summary=@outputs.order_summary
        | Show the user their order: {!@variables.order_summary}
```

Always guard with an "already fetched?" check. Several `run`s in sequence is the "advanced
reasoning" pattern: gather profile, orders, cases and scores deterministically, then give
the model one synthesised prompt. Refresh after writes (re-`run` the load under the write
tool) so state matches the org.

## Deterministic validation and error handling

```agentscript
reasoning:
    instructions: ->
        if @variables.transfer_amount > @variables.transfer_limit:
            set @variables.validation_passed = False
            | The amount exceeds the {!@variables.transfer_limit} limit. Offer the maximum,
              a split transfer, or support. Don't transfer.
        else if @variables.source_balance < @variables.transfer_amount:
            set @variables.validation_passed = False
            | Insufficient funds (available {!@variables.source_balance}). Offer a smaller amount.
        else:
            set @variables.validation_passed = True
    actions:
        execute_transfer: @actions.execute_transfer
            available when @variables.validation_passed == True
            with from_account=@variables.source_account
            with to_account=@variables.destination_account
            with amount=@variables.transfer_amount
```

- Comparisons that must not vary go in `if`, not in prompt text.
- Actions return a `success` boolean + an `error_message`; branch on them and tell the
  model what to say. `system.messages.error` is the fallback for platform errors.
- Count failures (`failed_attempts`) and escalate after N.
- Avoid transition loops (A→B→A); the runtime cuts off after a few loops and returns to
  the router.

## Escalation to a human

```agentscript
connection messaging:
    escalation_message: "Connecting you with a member of our team."
    outbound_route_type: "OmniChannelFlow"
    outbound_route_name: "flow://Route_To_Support_Queue"

subagent general_support:
    description: "Answer questions; hand off when asked or stuck"
    reasoning:
        instructions: ->
            | Help the user. Save a one-line summary with {!@actions.record_issue}.
              If the user asks for a person, use {!@actions.note_human_request}.
            if @variables.user_requested_human == True or @variables.failed_attempts > 1:
                | Confirm the issue as "{!@variables.issue_summary}", then use {!@actions.escalate_to_human}.
        actions:
            record_issue: @utils.setVariables
                description: "Save a one-line summary of the issue"
                with issue_summary = ...
            note_human_request: @utils.setVariables
                description: "Record that the user asked for a human"
                with user_requested_human = True
            escalate_to_human: @utils.escalate
                description: "Hand the conversation to a human agent"
                available when @variables.user_requested_human == True or @variables.failed_attempts > 1
```

Routing config (where) and the gate (when) are separate. The Omni-Channel flow must exist
and the agent must be deployed on a messaging channel; escalation can't be tested in a
plain preview without it. Channel setup is in the connections reference.

## Safety, personas, external APIs

- Destructive actions: confirmation variable → check action → write action, each gated on
  the previous output (multi-step pattern above).
- Personas or conflicting rules: a subagent-level `system: instructions:` **replaces** the
  global instructions for that subagent.
- Authorisation is never the prompt's call: `available when` on trusted state, plus
  sharing/permission checks in the Flow/Apex.
- External APIs: Flow (HTTP Callout / External Services) or Apex behind Named Credentials,
  exposed as `flow://` / `apex://` / `externalService://`. Return `success`,
  `error_message` and small typed fields; no keys or URLs in the script.

## Models

```agentscript
model_config:                                        # agent default
    model: "model://sfdc_ai__DefaultGPT4OmniMini"

subagent billing_inquiry:
    description: "Billing questions with sensitive data"
    model_config:                                    # subagent override
        model: "model://sfdc_ai__DefaultBedrockAnthropicClaude45Haiku"
```

- Precedence: subagent > agent > org default (Setup → Einstein/Agentforce model selection).
- Model names are `sfdc_ai__Default…` aliases. Seen in Salesforce sources:
  `DefaultGPT4OmniMini`, `DefaultGPT41`, `DefaultGPT41Mini`, `DefaultGPT5`, `DefaultGPT5Mini`,
  `DefaultGPT54`, `DefaultO4Mini`, `DefaultBedrockAnthropicClaude45Haiku`,
  `DefaultBedrockAnthropicClaude45Sonnet`, `DefaultBedrockAnthropicClaude46Sonnet`,
  `DefaultBedrockAnthropicClaude45Opus`, `DefaultVertexAIGemini25Flash001`,
  `DefaultBedrockAmazonNovaPro`, plus `DefaultEinsteinHyperClassifier`. Check the current
  supported-models list for your org's release before using one.
- Bedrock-hosted Anthropic models run inside the Salesforce trust boundary; use them for
  subagents handling regulated data.
- Route cheap/fast models to high-volume FAQ subagents and stronger models to multi-step
  troubleshooting. Test each subagent on its model: Salesforce warns some models don't suit
  some tools.
- `params:` passes model parameters (e.g. `temperature`, `max_tokens`); support varies by
  model.
- Bring-your-own LLM: models configured in Einstein Studio (Model Builder) appear as
  configured models with their own API names. Whether they're selectable in
  `model_config` and under what URI depends on release and license (verify in your org).

## Language settings

```agentscript
language:
    default_locale: "en_US"
    additional_locales: "es_MX,fr"
connection messaging:
    adaptive_response_allowed: True    # reply in the user's language
```

Test every locale in preview. Voice agents need the locale inside `modality voice:` too.
Field reports flag duplicate Hebrew/Indonesian entries in the Builder dropdown.

## Legacy (topic-based) agents → Agent Script

| Legacy builder (`GenAiPlannerBundle`) | Agent Script (`AiAuthoringBundle`) |
|---|---|
| Agent / planner bundle | `.agent` file; compiles to a new `GenAiPlannerBundle` version |
| Topic (`GenAiPlugin`) | `subagent` block |
| Topic classification description + scope | `subagent.description` (+ transition `description` in the router) |
| Topic instructions (list) | `reasoning.instructions` (`|` text plus `->` logic) |
| Topic actions (`GenAiFunction`) | `subagent.actions` definitions + `reasoning.actions` tools |
| Implicit LLM topic classifier | Explicit `start_agent` router (optionally HyperClassifier) |
| Context variables / custom variables | `variables` (`linked` for session context, `mutable` for state) |
| Filters on topics/actions | `available when` |
| Planner `AiCopilot__ReAct` | New planner (`Atlas__ConcurrentMultiAgentOrchestration`) |

Migration: Salesforce exposes `POST /services/data/v67.0+/connect/migrateAgentToNga` with a
`botVersionId`; it creates a new Agent Script bundle and returns `conversionWarnings`. It
is a mechanical transform: it doesn't add gates, variables or deterministic logic. Legacy
versions stay in the old builder. Then: retrieve the bundle, read the warnings, move
"must" rules from instructions into `if` / `available when`, add verification and
escalation patterns, and regression-test against the legacy version. Einstein Bots
(`BotDefinition`) are a separate upgrade path.

## Anti-patterns

- A router plus one subagent for a one-domain agent.
- Many overlapping subagents ("Orders", "Order Help", "Order Status").
- Prompt-text-only gates ("Only refund if verified") with no `if` or `available when`.
- `available when` used as a substitute for a required workflow.
- Step numbers or "continue to the next step" in conditionally included prompt text.
- Latches that pin the user in a subagent with no exit transition.
- Transition loops between two subagents.
- Personalising `system.messages.welcome` with mutable variables.
- One model for everything without measuring cost, latency and accuracy per subagent.

## Sources

- `salesforce/agentscript` → `apps/docs/docs/dialects/agentforce/patterns/` (topic-selector,
  required-flow, filtering, transitions, multi-turn, fetch-data, conditionals,
  system-overrides, variables), `ascript-blocks.md` (model_config, HyperClassifier),
  `ascript-flow.md`, `reference/ascript-ref-tools.md`
- `salesforce/agentscript` → `dialect/agentforce/src/schema.ts` (`ModelConfigBlock`,
  `planner_type` values), `dialect/agentforce/src/lint/passes/hyperclassifier.ts`
- `eksido/AgentScriptDocs` → `agent-script/ascript-model.md` (model precedence, supported models);
  `agent-script/patterns/ascript-patterns-var-list.md`
- `salesforcecli/sf-grid-mcp` → `src/lib/model-map.ts` (`sfdc_ai__` model aliases)
- `trailheadapps/agent-script-recipes` → `force-app/main/04_architecturalPatterns/`
  (`openGateRouter`, `subagentDelegation`, `errorHandling`, `escalationPatterns`,
  `safetyAndGuardrails`, `simpleQA`, `modelConfig`, `complexStateManagement`,
  `advancedReasoningPatterns`, `externalAPIIntegration`), `01_languageEssentials/conditionalLogicPatterns`
- `SalesforceAIResearch/agentforce-adlc` → `skills/agentforce-generate/references/architecture-patterns.md`,
  `production-gotchas.md` (supervision vs handoff, loop guardrail), `common-control-flow-pitfalls.md`,
  `assets/patterns/{bidirectional-routing,open-gate-routing,multi-step-workflow}.agent`
- `forcedotcom/sf-skills` → `skills/agentforce-generate/SKILL.md` and
  `references/upgrade-legacy-agent-to-agentscript.md` (`migrateAgentToNga`),
  `skills/agentforce-bot-upgrade/SKILL.md`
