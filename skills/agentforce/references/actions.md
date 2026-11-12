# Agent Script actions

An action is how an Agentforce agent touches the world: a Flow, Apex, prompt template,
Integration Procedure, external API or standard action. Every action has two halves in
Agent Script: a **definition** (what it is) and, optionally, a **tool binding** (whether
and how the LLM may call it). Building the target itself (the Flow, Apex, IP or template)
is covered in the platform-specific references.

## Definition vs tool vs deterministic run

| Where | What it means | Who decides to call it |
|---|---|---|
| `subagent.actions.<name>` | Definition: description, inputs, outputs, `target` | Nobody yet |
| `subagent.reasoning.actions.<tool>: @actions.<name>` | Tool exposed to the LLM, with bindings and gating | The LLM, at reasoning time |
| `run @actions.<name>` in `instructions: ->`, `before_reasoning`, `after_reasoning`, or chained under a tool | Deterministic call during prompt resolution | Your code, every time the line is reached |

A definition alone does nothing for the model. The LLM can call **any** tool in
`reasoning.actions` that is currently available, even if the instructions never mention it.

```agentscript
subagent verify:
    description: "Verify the member's identity"
    actions:                                    # 1. definitions
        send_code:
            description: "Email a one-time verification code to the member"
            inputs:
                email: string
                    description: "Member's email address"
                    is_required: True
            outputs:
                sent: boolean
                    description: "Whether the code was sent"
            target: "flow://Send_Verification_Code"
    reasoning:
        instructions: ->
            if @variables.member_email != "" and @variables.code_sent == False:
                # 2. deterministic
                run @actions.send_code
                    with email=@variables.member_email
                    set @variables.code_sent=@outputs.sent
            | Ask for the code. If they didn't get it, resend with {!@actions.resend_code}.
        actions:
            resend_code: @actions.send_code             # 3. tool for the LLM
                with email=@variables.member_email
                set @variables.code_sent=@outputs.sent
```

## Definition properties

| Property | Notes |
|---|---|
| name (key) | Developer-name rules: letter first, alphanumeric + `_`, no `__`, no trailing `_`, ≤ 80. `snake_case`. |
| `description` | What the LLM reads to choose the action. Use `|` for multi-line. |
| `target` | Required. `"<scheme>://<DeveloperName>"`. |
| `inputs` / `outputs` | Typed parameters (below). **Always include `outputs:`** — target-backed actions without it have failed publish with "Internal Error". You may declare a subset of the target's outputs. |
| `label` | UI label; defaults from the name. |
| `include_in_progress_indicator` + `progress_indicator_message` | Spinner and text while running. |
| `require_user_confirmation` | Compiles; field reports say the confirmation UI doesn't fire for Agent Script agents. Enforce confirmation yourself (see below). |
| `cache: expiration: <seconds>` | Cache results for this definition (newer; verify in your org). |
| `async_mode: True` | Only for `standardInvocableAction://` targets; error elsewhere. |
| `source` | Links to an existing action asset (e.g. `"Submit_Case"` from the Asset Library). |

## Parameters

```agentscript
inputs:
    order_number: string
        label: "Order Number"
        description: "Order number as printed on the receipt, e.g. A-10442"
        is_required: True
        is_user_input: True          # LLM may pull it from what the user said
    start_date: object
        complex_data_type_name: "lightning__dateType"
        is_required: True
outputs:
    status: string
        description: "Fulfilment status: pending, shipped, delivered, cancelled"
        is_displayable: True
    order_id: id
        description: "Internal record ID"
        filter_from_agent: True      # kept out of the model's context
    lines: list[object]
        description: "Order lines with product name and quantity"
        complex_data_type_name: "lightning__recordInfoType"
```

Types: `string`, `number`, `integer`, `long`, `boolean`, `object`, `date`, `datetime`,
`time`, `currency`, `id`, `list[<type>]`. Parameter names may be quoted when they contain
special characters: `"Input:email": string`.

| Property | On | Effect |
|---|---|---|
| `description` | in/out | Read by the LLM. Auto-generated from the name if omitted — don't omit. |
| `is_required` | in | Must have a value. Required inputs with no default and no `with` are LLM-filled automatically. |
| `is_user_input` | in | Value comes from the conversation. |
| `complex_data_type_name` | in/out | Required for complex types (below). |
| `filter_from_agent` | in/out | `True` = excluded from the agent's context. Use for IDs, PII, raw payloads. |
| `is_displayable` | in/out | `False` = may not be shown to the user. |
| `is_used_by_planner` | in/out | Whether the planner can read the value. |
| `developer_name` | out | Override the parameter's developer name. |

Outputs are remembered **for the whole session** unless filtered. Big payloads crowd out
state; return summaries, not records.

### complex_data_type_name

| Data | Value |
|---|---|
| sObject record(s) | `lightning__recordInfoType` |
| Date | `lightning__dateType` |
| Datetime | `lightning__dateTimeStringType` (docs also show `lightning__dateTimeType`; verify) |
| Integer (Apex target) | `lightning__integerType` |
| Integer (Flow target) | `lightning__numberType` |
| Decimal | `lightning__doubleType` |
| Currency | `lightning__currencyType` |
| Generic object | `lightning__objectType` (default for `object`) |
| Apex inner class | `@apexClassType/<ns>__Class__Inner` |
| Custom Lightning Type | `c__<typeName>` (e.g. `c__caseResult`) |

Field reports say bare `number` in action I/O can fail at publish when the target expects
an integer — use `object` + the matching complex type. When unsure, build the action in
Agentforce Assets, pick the type from the dropdown, and copy what the Builder writes.

## Target schemes

Validated by the compiler's `actionTargetSchemeRule`. Aliases compile to a canonical type.

| Scheme | Calls | Notes |
|---|---|---|
| `flow://Api_Name` | Autolaunched Flow | Most common. Flow variables marked available for input/output. |
| `apex://ClassName` | `@InvocableMethod` | Needs `@InvocableVariable` request/response wrappers; I/O names must match field names exactly. |
| `prompt://Template` = `generatePromptResponse://Template` | Prompt Builder template | Inputs are `"Input:<name>"`; output is `promptResponse`. |
| `standardInvocableAction://name` | Built-in invocable action (e.g. `sendEmail`) | Only scheme allowing `async_mode`. |
| `externalService://Name` | External Service (OpenAPI-registered) | Re-validate schemas after releases; validation has tightened. |
| `integrationProcedureAction://Type_SubType` = `executeIntegrationProcedure://` | OmniStudio Integration Procedure | Check the IP naming format in your org. |
| `apexRest://`, `auraEnabled://` | `@RestResource` / `@AuraEnabled` Apex | |
| `quickAction://` | Quick action | |
| `api://`, `externalConnector://` | Generic API / prebuilt connector | |
| `mcpTool://`, `platformMcpTool://` | MCP tool registered in the org | See MCP reference. |
| `retriever://` | Data Cloud / knowledge retriever | See RAG reference. |
| `cdpMlPrediction://` | Data Cloud ML prediction | |
| `namedQuery://` | Named query | |
| `expressionSet://` = `runExpressionSet://`, `decisionTableAction://` | Business rules engine | |
| `serviceCatalog://` = `createCatalogItemRequest://` | Service Catalog item | |
| `slack://` | Slack action | |
| `placeholder://name` | Stub; compiles with a warning | Use while designing; never ship. |

Publish validates that every target resolves to a registered invocable action in the org.
Deploy the Flow/Apex/template first. In namespaced orgs, `apex://` resolution has been
flaky; wrapping the Apex in a Flow is the known workaround.

### Apex target shape

```apex
public with sharing class OrderSummaryAction {
    public class Request {
        @InvocableVariable(required=true description='Verified customer ID')
        public String customerId;
    }
    public class Response {
        @InvocableVariable(description='Status, items and delivery date')
        public String orderSummary;
    }
    @InvocableMethod(label='Get Order Summary' description='Most recent order for a customer')
    public static List<Response> run(List<Request> requests) { /* bulk-safe */ }
}
```

```agentscript
get_summary:
    description: "Fetch the customer's most recent order summary"
    inputs:
        customerId: string
            is_required: True
    outputs:
        orderSummary: string
    target: "apex://OrderSummaryAction"
```

Bare `List<String>` invocables can't be bound. Don't name `@InvocableVariable`s `model`,
`description` or `label` (compile error on the agent, not on the Apex).

### Prompt template target shape

```agentscript
generate_schedule:
    description: "Build a personalised schedule from the guest's stored preferences. Needs only the email."
    inputs:
        "Input:email": string
            description: "Guest's email address"
            is_required: True
    outputs:
        promptResponse: string
            description: "The generated schedule"
            is_used_by_planner: True
            is_displayable: True
    target: "generatePromptResponse://Generate_Personalized_Schedule"
# tool binding:
#   generate_schedule: @actions.generate_schedule
#       with "Input:email" = ...
```

## Tool bindings

```agentscript
reasoning:
    actions:
        lookup_order: @actions.Lookup_Order
            description: "Look up a past order when the user gives an order number"   # overrides definition text
            available when @variables.verified == True
            with order_number=...                      # LLM fills from conversation
            with customer_id=@variables.customer_id    # bound from state
            with channel="web"                         # literal
            set @variables.order_status=@outputs.status
            set @variables.order_id=@outputs.order_id
```

| Binding | Use when |
|---|---|
| `with p=...` | The user supplies it in conversation (slot filling). Only valid on the tool itself, not on chained `run`s. |
| `with p=@variables.x` | Trusted state (IDs from verification, prior outputs). |
| `with p="literal"` | Fixed parameters the model shouldn't choose. |
| no `with` | Required inputs auto-fill by the LLM; optional inputs stay unset. |

- **Bind sparingly.** A tool is offered when the agent has all its inputs; binding many
  inputs to variables can make selection inconsistent. Bind IDs and anything security-
  relevant; let the model fill what the user says.
- `set` captures outputs into variables **only** directly under the binding/run that
  produced them. `@outputs` is out of scope anywhere else.
- `description` on the tool overrides the definition's description for that subagent.
  Use it to describe the same target differently per context (the
  `actionDescriptionOverrides` recipe defines one Flow three ways).
- `execution_mode: fire_and_forget` returns dispatch success, not the result; outputs are
  unusable (newer; verify in your org).

### Chaining

```agentscript
get_order: @actions.GetOrderByOrderNumber
    with orderNumber = ...
    set @variables.orderDetails = @outputs.orderDetails
    # runs right after, deterministically
    run @actions.ScheduleDelivery
        with orderDetails = @variables.orderDetails
        set @variables.deliveryDate = @outputs.deliveryDate
    transition to @subagent.confirmation            # optional handoff after the chain
```

Chained actions can't slot-fill (`...`). Chained runs with prompt-template `Input:` names
have been reported not to map; bind from variables.

### Gating with `available when`

```agentscript
create_return: @actions.create_return
    available when @variables.return_eligible == True and @variables.order_id != ""
complete_task: @actions.complete_task
    available when len(@variables.tasks) > 0
```

`available when` removes the tool from the model's tool list. It is the enforcement point
for anything a user could talk the model into. It filters options; it doesn't force a
step — for "must happen first" use a deterministic `transition to` or `run`.

### Referencing tools in instructions

`{!@actions.tool_name}` in `|` text names the tool explicitly. The model usually picks
correctly without it; add references when a subagent has many tools or similar ones.
Reference the **tool** name (the `reasoning.actions` key), not the definition, when they
differ.

## Confirmation and side effects

`require_user_confirmation` isn't reliable at runtime. For writes:

```agentscript
variables:
    user_confirmed: mutable boolean = False
reasoning:
    instructions: ->
        | Summarise the refund and ask the user to confirm. If they confirm, use {!@actions.record_confirmation}.
    actions:
        record_confirmation: @utils.setVariables
            description: "Record that the user explicitly confirmed the refund"
            with user_confirmed = True
        issue_refund: @actions.issue_refund
            available when @variables.user_confirmed == True
            with order_id=@variables.order_id
            set @variables.refund_id=@outputs.refund_id
```

Make the Flow/Apex idempotent (key on order + amount) because the model can call it twice.
Reset `user_confirmed` after use if another write can follow.

## Writing descriptions the planner uses

| Do | Don't |
|---|---|
| Say what it does, when to use it, what it needs, what it returns | "Gets data" |
| Use the user's words ("track my parcel") alongside system terms | Internal jargon only |
| State exclusions: "Not for returns — use start_return" | Overlapping descriptions across tools |
| One consistent noun per concept across the agent ("customer" everywhere) | "client" in one tool, "customer" in another |
| Describe each input with format and example | Untyped `query` / `data` inputs |
| Describe outputs so the model can explain them | Leave outputs undescribed |

If two tools are confused in preview, rewrite both descriptions, then gate with
`available when`, then merge.

## Cost and size

- Each Flow, Apex or other action execution consumes Flex Credits per action; prompt
  templates bill separately. Transitions, `setVariables`, `if`/`set` logic are free
  (from field reports; confirm against your contract). Avoid re-running fetches:
  `if @variables.x == "":` before `run`.
- Return small, summarised payloads; responses over ~50 KB have been reported to evict
  earlier state. Filter IDs and raw JSON with `filter_from_agent: True`.

## Anti-patterns

- Defining an action and expecting the model to call it without a `reasoning.actions` entry.
- Exposing write actions with no `available when` gate.
- Binding every input to variables "for safety", then wondering why the tool is never chosen.
- Relying on `require_user_confirmation` for destructive actions.
- `set @variables.x = @inputs.y` after a run (silently drops).
- One giant "do_everything" Flow with a mode string; or one tool per CRUD endpoint.
- Returning whole records to the model when one field is needed.
- Shipping a `placeholder://` target.

## Sources

- `salesforce/agentscript` → `apps/docs/docs/dialects/agentforce/reference/ascript-ref-actions.md`,
  `reference/ascript-ref-tools.md`, `reference/ascript-ref-utils.md`,
  `patterns/ascript-patterns-action-chaining.md`, `patterns/ascript-patterns-filtering.md`,
  `patterns/ascript-patterns-variables.md`, `patterns/ascript-patterns-resource-references.md`
- `salesforce/agentscript` → `dialect/agentforce/src/lint/passes/action-target.ts` (scheme list),
  `packages/compiler/src/nodes/action-types.ts` (aliases), `packages/compiler/src/nodes/compile-actions.ts`
  (`async_mode`, auto-fill of required inputs), `dialect/agentforce/src/schema.ts` (`AFActionBlock`,
  input/output properties, `execution_mode`, `cache`)
- `trailheadapps/agent-script-recipes` → `force-app/main/02_actionConfiguration/`
  (`advancedInputBindings`, `actionDescriptionOverrides`, `promptTemplateActions`, `customLightningTypes`),
  `04_architecturalPatterns/{externalAPIIntegration,complexStateManagement,safetyAndGuardrails}`
- `SalesforceAIResearch/agentforce-adlc` → `skills/agentforce-generate/references/actions-reference.md`,
  `complex-data-types.md`, `feature-validity.md`, `known-issues.md` (Issues 2, 6, 7, 9, 14, 15),
  `production-gotchas.md` (credits, `@inputs` scope, reserved `@InvocableVariable` names)
