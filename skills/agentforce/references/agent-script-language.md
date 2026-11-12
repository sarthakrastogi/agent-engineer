# Agent Script language

Agent Script is Salesforce's compiled, whitespace-sensitive DSL for Agentforce agents. One
`.agent` file holds the whole agent: config, state, routing, subagents, actions and the
deterministic logic around the LLM. Verify syntax against the open-source compiler
(`salesforce/agentscript`) before relying on anything here; the language moves monthly.

## Mental model

| Concept | What it is |
|---|---|
| **Agentforce Builder** (new) | Authoring UI in Agentforce Studio. **Canvas** view (doc-style, expandable blocks), **Script** view (the raw `.agent` with highlighting), and a chat assistant (Agent Optimizer) that turns plain-language requests into edits. Preview shows an interaction summary and trace; batch tests run from the builder. |
| **Agent Script** | The text behind every new-builder agent. Canvas and Script are two views of the same file. |
| **Subagent** | A job the agent can do: description (routing signal), instructions, actions. Was **topic** until April 2026; `topic` still parses but is deprecated. |
| **`start_agent`** | The entry subagent ("Agent Router" in Canvas). **Every utterance starts here.** Exactly one per file. |
| **Hybrid reasoning** | `->` logic runs deterministically in the runtime; `|` text becomes the LLM prompt. You pick, line by line, what the model decides and what code decides. |
| **Compiled** | Saving or publishing compiles the script to Agent JSON / `GenAiPlannerBundle` metadata that the Atlas reasoning engine executes. |
| **AiAuthoringBundle** | The metadata type: `aiAuthoringBundles/<Name>/<Name>.agent` + `<Name>.bundle-meta.xml` (`<bundleType>AGENT</bundleType>`). |

## How a turn executes

1. Utterance arrives → runtime enters `start_agent`.
2. `before_reasoning` runs (logic only).
3. `reasoning.instructions` resolve **top to bottom**: `if` branches are evaluated, `run`
   actions execute, `set` mutates state, `|` lines are appended to the prompt. The LLM has
   not been called yet.
4. A `transition to` anywhere in that walk **discards the prompt built so far** and starts
   resolving the target subagent from its top. One-way.
5. The resolved prompt + the subagent's `reasoning.actions` (tools) go to the LLM. The
   model may call tools (including `@utils.transition` tools) in a loop.
6. `after_reasoning` runs (logic only). It is **skipped** if the subagent transitioned away
   mid-reasoning.
7. Response returned. Next utterance starts again at `start_agent`.

Consequences: put deterministic transitions **first** (anything before them is wasted
latency and, for actions, credits). State that must survive turns lives in variables, not
in "the model will remember".

## Block reference

Top-level blocks (any order): `system`, `config`, `access`, `language`, `variables`,
`model_config`, `connection <surface>`, `knowledge`, `modality voice`, `start_agent <name>`
(exactly one), `subagent <name>` (zero or more). Optional first-10-lines pin:
`# @dialect: agentforce=2.2`.

### system

```agentscript
system:
    instructions: "You are a concise, friendly support agent for Acme."
    messages:
        welcome: "Hi! I can help with orders and returns."
        error: "Sorry, something went wrong. Please try again."
```

- `welcome` and `error` are required.
- Welcome/error templates may only reference **linked** (or externally visible) variables
  (`system-message-mutable-variable` error). Mutable values aren't set yet at welcome
  time; personalise in the first subagent instead.
- Keep welcome messages single-line; line breaks have been reported stripped.
- A subagent may declare its own `system: instructions:` to **override** the global one for
  that subagent only. Use it to resolve conflicting rules or change persona.

### config

| Field | Notes |
|---|---|
| `developer_name` | API name, ≤ 80 chars, starts with a letter, alphanumeric + `_`, no trailing `_`, no `__`. Unique in org. Also the bundle directory name. |
| `agent_label` | UI label; defaults from `developer_name`. |
| `description` | Internal description of purpose. (`agent_description` is the prompt/routing variant.) |
| `agent_type` | `AgentforceServiceAgent` (default; customer-facing, needs an agent user) or `AgentforceEmployeeAgent` (runs as the logged-in user). Compiler also accepts other backend types (e.g. `EinsteinSDR`, `SalesEinsteinCoach`, `GoalBasedAgent`) — set by templates. |
| `default_agent_user` | **Deprecated here → `access.default_agent_user`.** Required for Service agents; ignored for Employee agents. |
| `company`, `role` | Optional context strings. |
| `enable_enhanced_event_logs` | `True` to record conversation logs for debugging/analytics. |
| `file_upload.mode` | `auto` / `managed` / `disabled` / `error` (newer; verify in your org). |
| `billing.mode` | `ACTION_BASED` (default) / `OUTCOME_BASED`; Service agents only (newer). |
| `user_locale` | Deprecated; use `language`. |

### access and language

```agentscript
access:                                  # newer; replaces config.default_agent_user
    default_agent_user: "agentuser@acme.com"
language:
    default_locale: "en_US"
    additional_locales: "es_MX,fr"
    all_additional_locales: False
```

`access` also takes `sharing_policy` and `verified_customer_record_access` (allowed per
agent type; verify in your org). For reply-language matching per channel, set
`adaptive_response_allowed: True` in the `connection` block.

### variables

```agentscript
variables:
    verified: mutable boolean = False
        description: "Whether identity verification succeeded"
    order_summary: mutable string = ""
        description: "Summary of the customer's current order"
        label: "Order Summary"
    member_email: mutable string            # no default: must be supplied
    questions: mutable list[string] = []
    RoutableId: linked string
        description: "Messaging session ID"
        source: @MessagingSession.Id
    ContactId: linked string
        source: @MessagingEndUser.ContactId
```

| Kind | Rules |
|---|---|
| `mutable` | Read/write. Omit `mutable` for a constant. May have a default. Can't have `source`. |
| `linked` | Read-only, value from `source`. **No default, can't be list or object, can't be set.** Sources validated: `@MessagingSession.{Id, MessagingEndUserId, EndUserLanguage}`, `@MessagingEndUser.ContactId`, `@VoiceCall.Id`. |
| system | `@system_variables.user_input` = latest utterance only. `@system_variables.uploaded_files` when file upload is on (index it, don't reference bare). |

Types: `string`, `number` (IEEE double), `boolean` (`True`/`False`, capitalised), `object`,
`date`, `id`, `list[<type>]`. Lists are zero-indexed: `@variables.q[@variables.i]`.

- Same naming rules as `developer_name`.
- `description` matters: it's how the LLM knows what to put there when slot-filling.
- Initialise flags (`= False`) and strings (`= ""`) so conditions behave; `is None` checks
  "never assigned", `== ""` checks "assigned empty".
- Values in variables are **not** visible to the model unless interpolated with `{!...}`.

### start_agent and subagent

All subagent fields (`start_agent` takes the same ones):

```agentscript
subagent Order_Management:
    description: "Order management"
    label: "Order Management"
    description: "Handles order lookup, status, delivery and updates"
    system:
        instructions: "Never expose internal record IDs."
    model_config:
        model: "model://sfdc_ai__DefaultBedrockAnthropicClaude45Haiku"
    before_reasoning:
        if @variables.verified is not True:
            transition to @subagent.Identity
    actions:
        Lookup_Order:
            description: "Retrieve order details by order number"
            inputs:
                order_number: string
                    description: "The order number"
                    is_required: True
            outputs:
                status: string
                    description: "Order status"
            target: "flow://Lookup_Order"
    reasoning:
        instructions: ->
            | Ask for the order number and look it up with {!@actions.lookup_order}.
        actions:
            lookup_order: @actions.Lookup_Order
                with order_number=...
                set @variables.status = @outputs.status
    after_reasoning:
        set @variables.request_count = @variables.request_count + 1
```

- Subagent name: `snake_case` or `Pascal_Snake`, no spaces; must not equal the
  `start_agent` name (publish fails with `duplicate value found: GenAiPluginDefinition`).
- `description` is the **routing signal** — specific, distinct, in users' words.
- Two `actions` blocks: `subagent.actions` = **definitions** (what exists, target, I/O);
  `subagent.reasoning.actions` = **tools** the LLM may call (bindings, gating). An action
  is callable by the model only if it appears in `reasoning.actions`. See `actions.md`.
- Actions are per-subagent; subagents don't share them.

### reasoning.instructions: `|` vs `->`

| Form | Meaning |
|---|---|
| `instructions: |` then text | Whole block is prompt text. |
| `instructions: ->` then statements | Procedure: `if`, `run`, `set`, `transition to`, and `|` lines that append prompt text. |

```agentscript
instructions: ->
    if @variables.order_summary == "":
        run @actions.lookup_current_order
            with member_email=@variables.member_email
            set @variables.order_summary=@outputs.order_summary
    | Greet {!@variables.member_name} and show their order: {!@variables.order_summary}.
      If they want a past order, ask for the order ID and use {!@actions.lookup_order}.
```

- One `|` per contiguous paragraph; indented continuation lines belong to it. Repeating `|`
  on every line is legal but noisy.
- Indentation **inside** prompt text is prose, not scope. "Call X" inside `|` is a
  suggestion to the model, not an execution.
- Shorter instructions are more reliable (Salesforce's own guidance).

### before_reasoning / after_reasoning

Logic only, written **directly** under the block (no `instructions:` wrapper, no `|`).
Run every time the subagent runs. Use `transition to` (no `@utils.`) here. Not allowed on a
router that uses the HyperClassifier model.

### Statements (inside `->`, `before_reasoning`, `after_reasoning`)

```agentscript
set @variables.count = @variables.count + 1
run @actions.Get_Tier
    with email = @variables.email
    set @variables.tier = @outputs.tier
transition to @subagent.wrap_up
if @variables.tier == "Gold":
    | Thank them for being Gold.
else if @variables.tier == "Platinum":
    | Thank them for being Platinum.
else:
    | Offer the loyalty programme.
```

- `if / else if / else` chains are supported (grammar `else_if_clause`; older docs say
  otherwise). `elif` is not the Agentforce form.
- **Nested `if` is a compile error** (`unsupported-nested-if`). Combine with `and`/`or` or
  split state into a helper variable. (An `if` inside a `run` body, testing `@outputs`, is
  allowed.)
- A comment-only `if` body compiles to a no-op; always put a statement in each branch.
- Code after `transition to` / escalate is unreachable (warning).
- `ask for @variables.X` + `instructions:` gathers one field over turns (newest syntax;
  verify in your org before using).

### Expressions

| Category | Syntax |
|---|---|
| Literals | `"str"`, `42`, `3.14`, `True`/`False`, `None`, `[1,2]`, `{"k":"v"}`, `2025-09-04`, `...` (LLM fills) |
| Compare | `==` `!=` `<` `<=` `>` `>=` `is` `is not` |
| Logic | `and` `or` `not` |
| Arithmetic | `+` `-` `*` `/` |
| Ternary | `a if cond else b` |
| Access | `@variables.customer.address.city`, `@variables.items[0]` |
| Functions | `len()` (documented); compiler also recognises `max`, `min`, `lower`, `upper`, `concat`, `to_json`, `from_json`, `json_path` (verify runtime support) |
| Interpolation | `{!@variables.x}`, `{!@actions.tool}`, `{!len(@variables.q)}`, `{!@variables.i + 1}` inside `|` text |

`true`, `false`, `none`, `null` are errors — use `True`, `False`, `None`.

### References

| Ref | Use |
|---|---|
| `@variables.x` | State (logic); `{!@variables.x}` in prompt text |
| `@actions.x` | Action definition (in `run`) or tool name (in `{!...}`) |
| `@outputs.x` | Only in `set`/`if` directly after the `run` or tool binding that produced it |
| `@inputs.x` | Only inside `with` during invocation; using it in a later `set` silently drops |
| `@subagent.x` | Transition target, or delegation when used as a tool |
| `@utils.transition to` / `@utils.setVariables` / `@utils.escalate` | Built-in tools |
| `@system_variables.user_input` | Last utterance |

### Utils

```agentscript
reasoning:
    actions:
        go_to_returns: @utils.transition to @subagent.Returns     # one-way handoff
            description: "Return or refund requests within 60 days"
        consult_billing: @subagent.billing_specialist             # delegation, returns
            description: "Analyse a complex billing issue"
        capture_name: @utils.setVariables                         # slot filling
            description: "Store the user's first and last name"
            with first_name = ...
            with last_name = ...
        escalate_to_human: @utils.escalate                        # needs connection messaging
            description: "Hand off to a human agent"
            available when @variables.user_requested_human == True
```

- **Transition**: one-way, target restarts from its top; only `description` and
  `available when` are valid on it. **Delegation** returns control to the caller (see
  `architecture-patterns.md`).
- **setVariables**: `...` = LLM extracts the value; targets must be `mutable`; chained
  `run`/`transition` on it is an error.
- **escalate**: needs `connection messaging` with route type + name; `escalate` is a
  reserved name.

### model_config

`model_config: model: "model://sfdc_ai__…"` (+ optional `params:`) at top level or in any
subagent; subagent > agent > org default. See `architecture-patterns.md` → Models.

### connection, knowledge, modality

`connection <surface>:` (`messaging`, `customer_web_client`, `telephony`, `slack`, …; one
block per surface, no `connections:` wrapper) sets channel and escalation routing;
`outbound_route_type` + `outbound_route_name` (`"flow://Route_Name"`) go together.
`knowledge:` sets `citations_enabled`, `citations_url`, `rag_feature_config_id`.
`modality voice:` sets voice persona and locale. Details are in their own references.

## Complete example (annotated)

```agentscript
# Order-support Service agent: verification gate, router, one domain, escalation.
config:
    developer_name: "Order_Support"
    agent_label: "Order Support"
    agent_type: "AgentforceServiceAgent"
    description: "Verifies customers, answers order questions, escalates to humans"

access:
    default_agent_user: "order.agent@acme.com"

system:
    instructions: "You are Acme's order assistant. Be brief. Never reveal record IDs."
    messages:
        welcome: "Hi! I can help with your orders."
        error: "Sorry, something went wrong. Please try again."

language:
    default_locale: "en_US"

variables:
    verified: mutable boolean = False
        description: "True once the verification flow succeeds"
    customer_id: mutable string = ""
        description: "Verified customer ID from the verification action"
    order_summary: mutable string = ""
        description: "Current order summary"
    failed_attempts: mutable number = 0
        description: "Times the agent could not resolve the request"
    ContactId: linked string
        source: @MessagingEndUser.ContactId

connection messaging:
    escalation_message: "Connecting you with a member of our team."
    outbound_route_type: "OmniChannelFlow"
    outbound_route_name: "flow://Route_To_Support_Queue"

start_agent agent_router:
    description: "Route each message to the right subagent"
    reasoning:
        instructions: ->
            # Deterministic gate first: no LLM call while unverified.
            if @variables.verified == False:
                transition to @subagent.Identity
            | Select the tool that best matches the user's message.
        actions:
            go_to_orders: @utils.transition to @subagent.Orders
                description: "Order status, delivery dates, order contents"
            go_to_human: @utils.transition to @subagent.Escalation
                description: "The user asks for a person or is clearly stuck"

subagent Identity:
    description: "Verify the customer with email and a one-time code"
    actions:
        verify_code:
            description: "Check the one-time code the customer received"
            inputs:
                email: string
                    description: "Customer email"
                    is_required: True
                code: string
                    description: "Six-digit code from the email"
                    is_required: True
            outputs:
                is_valid: boolean
                    description: "Whether the code matched"
                customer_id: string
                    description: "Verified customer ID"
                    filter_from_agent: True        # model never sees the ID
            target: "flow://Verify_Customer_Code"
    reasoning:
        instructions: ->
            | Ask for the customer's email and the code we sent, then use {!@actions.verify}.
        actions:
            verify: @actions.verify_code
                with email=...
                with code=...
                set @variables.verified = @outputs.is_valid
                set @variables.customer_id = @outputs.customer_id
    after_reasoning:
        if @variables.verified == True:
            transition to @subagent.Orders

subagent Orders:
    description: "Answer questions about the verified customer's orders"
    actions:
        get_current_order:
            description: "Fetch the customer's most recent order summary"
            inputs:
                customer_id: string
                    is_required: True
            outputs:
                order_summary: string
                    description: "Status, items and delivery date"
            target: "apex://OrderSummaryAction"
    reasoning:
        instructions: ->
            if @variables.order_summary == "":
                run @actions.get_current_order
                    with customer_id=@variables.customer_id
                    set @variables.order_summary=@outputs.order_summary
            | Answer using only this order data: {!@variables.order_summary}
              If you can't answer from it, say so and offer a person.

subagent Escalation:
    description: "Hand the conversation to a human agent"
    reasoning:
        instructions: |
            Summarise the issue in one line, then use the handoff tool.
        actions:
            handoff: @utils.escalate
                description: "Transfer to the support queue"
```

## Compile, lint, diagnose

- Builder compiles on save; CLI: `sf agent validate authoring-bundle --api-name <Name>`
  then `sf agent publish authoring-bundle --api-name <Name>` (see the lifecycle reference).
- Open-source toolchain (`salesforce/agentscript`): `agentforce lint -f <file>`,
  `agentforce compile -f <file>`, and a VS Code extension / LSP with diagnostics.
- Diagnostics include "did you mean?" suggestions. Server-side publish can still fail after
  local validation passes (e.g. missing `outputs:` → "Internal Error").

## Common errors

| Symptom | Cause | Fix |
|---|---|---|
| Parse error on indent | Mixed tabs and spaces, or inconsistent depth | One style for the whole file |
| `syntax-error: unexpected 'with …'` under a `run` | A trailing `# comment` on the `run @actions.x` line (SDK 2.10.38; reasoning-action and `if` lines are fine) | Put the comment on its own line above `run` |
| `unsupported-nested-if` | `if` inside `if` | `else if` chain, `and`/`or`, or a helper variable |
| `identifier-confusable-boolean` | `true`/`false`/`none` | `True`/`False`/`None` |
| `undefined-reference` | Typo, or `@actions.x` defined in another subagent | Define the action in this subagent |
| Model ignores state | Bare `@variables.x` in `|` text | `{!@variables.x}` |
| "Prompt-only" branch logic flaky | `| If {!@variables.s} == "blocked"…` asks the model to compare | Use a runtime `if` |
| Tool never offered | Defined in `subagent.actions` only | Add it to `reasoning.actions` |
| `set` after `run` silently drops | Used `@inputs.x` | Set from `@outputs`, or keep the value in a variable you bound |
| Welcome shows `{!...}` literally | Mutable var in welcome | Static welcome; personalise in a subagent |
| `duplicate value found: GenAiPluginDefinition` | `start_agent` and a `subagent` share a name | Rename one |
| `before_reasoning` with `instructions:` | Wrapper not allowed | Put statements directly under the block |
| `hyperclassifier-non-transition` | Router on HyperClassifier has a non-transition tool | Move the tool to a subagent |
| Last line errors in Builder | Builder quirk | Add a trailing blank line or comment |

## Sources

- `salesforce/agentscript` → `apps/docs/docs/dialects/agentforce/` (mirrors the Agent
  Script section of developer.salesforce.com/docs/ai/agentforce/guide): `agent-script.md`,
  `ascript-lang.md`, `ascript-blocks.md`, `ascript-flow.md`, `reference/*.md`, `patterns/*.md`
- `salesforce/agentscript` → `apps/docs/docs/language/{syntax,expressions,blocks}.mdx`
- `salesforce/agentscript` → `dialect/agentforce/src/schema.ts` (config, access, context,
  connection, knowledge, model_config fields), `dialect/agentforce/src/schema-serialize.ts`
  (diagnostic catalog), `dialect/agentforce/src/lint/agent-types.ts`,
  `packages/parser-tree-sitter/grammar.js` (`else_if_clause`),
  `packages/language/src/lint/unsupported-conditionals.ts`,
  `packages/language/src/lint/function-catalog.ts`, `packages/language/src/core/statements.ts`
  (`ask for`)
- `eksido/AgentScriptDocs` → `agent-script/patterns/ascript-patterns-var-list.md` (list variables, `len()`)
- `trailheadapps/agent-script-recipes` → `force-app/main/01_languageEssentials/*`
- `SalesforceAIResearch/agentforce-adlc` → `skills/agentforce-generate/references/`
  `known-issues.md`, `common-control-flow-pitfalls.md`, `production-gotchas.md`,
  `agent-metadata-and-lifecycle.md`, `assets/bundle-meta.xml`
- https://www.salesforce.com/agentforce/agent-builder/ (Canvas, Script view, Agent Optimizer, batch tests)
