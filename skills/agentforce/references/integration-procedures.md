# OmniStudio Integration Procedures as agent actions

An Integration Procedure (IP) is OmniStudio's **server-side, declarative orchestration**:
- **Steps:** an ordered list of actions (Data Mapper reads/writes/transforms, HTTP callouts, Apex
  Remote Actions, nested IPs).
- **Logic:** blocks for conditions, loops, caching and try/catch.
- **Data:** everything passes through one JSON document, the "Data JSON".

IPs are common in Industries clouds (Communications, Financial Services, Insurance, Health), and
Agentforce can call an active IP directly as an agent action.

## 1. When to use an IP vs Flow vs Apex

| Situation | Choose |
|---|---|
| The org already runs OmniStudio and the logic exists as an IP (or is reused by OmniScripts/FlexCards) | **IP action**: one implementation for UI and agent |
| Multi-step read/transform/call orchestration with JSON shaping, mixed Salesforce + REST sources, caching | **IP** |
| Simple CRUD or business rules, admin-owned, no OmniStudio licence | **Autolaunched Flow** (`flow://`) |
| Complex logic, heavy computation, strict typing, unit-testable with mocks | **Apex `@InvocableMethod`** (`apex://`) |
| One REST API with an OpenAPI spec | **External Service** (`externalService://`) |
| Just reading or shaping Salesforce data for a UI | A single **Data Mapper**, no IP |

IPs are a good fit for agents because they return **one shaped JSON payload** from many calls,
in a single action call, with no extra LLM turns. Keep the payload small and named for the model.

## 2. Building blocks

### Data Mappers (formerly DataRaptors), metadata `OmniDataTransform`

| Type | Does | Notes |
|---|---|---|
| Extract | SOQL read with relationships → JSON | Set filters + limits; an empty result is **not** an error unless you make it one |
| Turbo Extract | Fast read-only single-object extract | No formula fields or related lists |
| Transform | Reshape JSON → JSON, no DB access | Rename fields to model-friendly keys here |
| Load | Write JSON → records (DML) | Always follow it with an error check |

Data Mappers respect FLS when configured. Check the "check field-level security" settings, and
build and activate them **before** the IP that calls them.

### IP elements

| Element | Use | Key properties |
|---|---|---|
| Set Values | Constants, input normalisation, computed fields | Downstream refs need the step prefix: `%SetInputs:customerId%` |
| Data Mapper Extract / Transform / Load Action | Call a Data Mapper | `bundle`, Additional Input |
| HTTP Action | REST callout | Path + method, **Named Credential**, timeout; status lands in `<Element>Info` |
| Remote Action | Call Apex (`omnistudio.VlocityOpenInterface2` or `Callable`) | `remoteClass`, `remoteMethod` |
| Integration Procedure Action | Call a nested IP | `Type_SubType` key; no cycles |
| Response Action | Shape and return the final output, end early | Can terminate the IP; use one per exit path |
| Conditional Block | Run children only if a formula is true | Use paired blocks for if/else |
| Loop Block | Iterate an array; each iteration sees one item | Keep the body small; watch CPU |
| Try-Catch Block | Catch failures of its **direct children** | Doesn't protect steps after it |
| Cache Block | Cache a group of read-only steps | Separate TTLs per block |

Common step properties:
- Execution and failure: **Execution Conditional Formula**, **Failure Conditional Formula**,
  **Fail On Step Error**, **Failure Response**.
- Payload shaping: **Send/Response JSON Path & Node**, **Additional Input/Output**, **Send Only
  Additional Input** (fully isolates the child from the parent's data).

### Naming

The IP key is `Type_SubType` (the one underscore is the separator). The Agentforce configuration
only allows **alphanumeric keys** in its input and output JSON. Practitioner reports say Type and
SubType must also be alphanumeric with no underscores, so use `CustomerGetBillingSummary`, not
`Get_Billing` (verify in your org). Version with SubType or VersionNumber; only one version per
Type/SubType is active.

## 3. Exposing an IP to an agent

**Native path (Agentforce Builder / Setup):**

1. Prerequisites: OmniStudio permission sets and **OmniStudio Metadata API support enabled**.
2. Open the IP → **Preview** → **Configure Agentforce**.
3. Paste sample **input JSON** and **output JSON** (flat, alphanumeric keys) → **Verify Data** →
   save. This rewrites the IP's data JSON into an agent-compatible schema.
4. **Activate** the IP.
5. In the agent: subagent → add action → **Add from Asset Library** → pick the IP. Write the
   action and parameter descriptions as for any tool.

You can't configure file-based or out-of-the-box (managed) IPs this way. Clone them, or wrap them
(below).

**Agent Script target:**

```agentscript
subagent billing:
    description: "Billing requests"
    actions:
        get_billing_summary:
            description: "Returns the customer's current bill, due date and last three payments. Use when the customer asks what they owe or whether a payment was received. Read-only."
            inputs:
                accountNumber: string
                    description: "Billing account number, digits only, e.g. 4401928374"
                    is_required: True
            outputs:
                amountDue: number
                    description: "Amount currently due in NZD"
                    is_displayable: True
                dueDate: string
                    description: "ISO date the amount is due"
                lastPayments: list[object]
                    description: "Up to 3 most recent payments with date and amount"
                errorMessage: string
                    description: "Empty on success; a plain-language reason on failure"
                    filter_from_agent: False
            target: "integrationProcedureAction://CustomerGetBillingSummary"
```

`integrationProcedureAction://` is an alias the compiler rewrites to
`executeIntegrationProcedure://`; both are valid schemes. The path is the IP's API name/key.
Confirm the exact name the asset library shows and the declared I/O names; the compiler's test
fixtures only prove the scheme, not the naming (verify in your org). Input and output names must
match the verified JSON keys exactly.

**Wrapper path** (for managed IPs, needing Flow features, or full control of the contract): call
the IP from an Apex `@InvocableMethod` and expose that with `apex://`:

```apex
public with sharing class BillingSummaryAction {
    public class Req { @InvocableVariable(required=true description='Billing account number') public String accountNumber; }
    public class Res {
        @InvocableVariable public Decimal amountDue;
        @InvocableVariable public String dueDate;
        @InvocableVariable public String errorMessage;
    }
    @InvocableMethod(label='Get Billing Summary' description='Current bill and due date for an account')
    public static List<Res> run(List<Req> reqs) {
        List<Res> out = new List<Res>();
        for (Req r : reqs) {   // agents send one request; still bulk-safe
            ConnectApi.IntegrationProcedureServiceRunInputRepresentation input =
                new ConnectApi.IntegrationProcedureServiceRunInputRepresentation();
            input.input = new List<String>{ JSON.serialize(new Map<String, Object>{ 'accountNumber' => r.accountNumber }) };
            ConnectApi.IntegrationProcedureServiceRunOptionsRepresentation opts =
                new ConnectApi.IntegrationProcedureServiceRunOptionsRepresentation();
            opts.isDebug = false;
            input.options = opts;
            Res res = new Res();
            try {
                ConnectApi.IntegrationProcedureServiceRunOutputRepresentation o =
                    ConnectApi.OmniDesignerConnect.integrationProcedureExecute('CustomerGetBillingSummary', input);
                Map<String, Object> m = (Map<String, Object>) JSON.deserializeUntyped(o.response[0]);
                Map<String, Object> result = (Map<String, Object>) m.get('result');
                res.amountDue = (Decimal) result.get('amountDue');
                res.dueDate = (String) result.get('dueDate');
                res.errorMessage = (String) result.get('errorMessage');
            } catch (Exception e) {
                res.errorMessage = 'Billing system unavailable; offer to create a case.';
            }
            out.add(res);
        }
        return out;
    }
}
```

`ConnectApi.OmniDesignerConnect.integrationProcedureExecute` is the Summer '25+ replacement for
`IntegrationProcedureService.runIntegrationService`. The legacy method still works but is
deprecated. Help states the IP invocation **ignores sharing rules and custom permissions**, so
check record access in the wrapper or the IP itself.

## 4. Designing the IP contract for an LLM

The agent reads only the action description and parameter names. Shape the IP for that:

- **Flat, named output.** Use a Response Action or a final Transform to return
  `{amountDue, dueDate, lastPayments, errorMessage}`, not the raw nested Data JSON with
  `<Element>Info`, IDs and nulls.
- **Bounded lists.** Use Extract limits and return the top N with a total count.
- **Explicit success/failure.** Return `errorMessage` (or `success` + `errorMessage`) on every
  path:
  - Map a Data Mapper Extract that found nothing to a "not found" message with a **Failure
    Conditional Formula**; by default it isn't an error.
  - Check HTTP `statusCode` in a Conditional Block, and set Failure Response values.
  - Wrap DML (Load) and callouts in **Try-Catch**.
- **Write actions are idempotent.** Accept an external reference/idempotency key; agent retries
  happen.
- **Validate inputs at the boundary.** Check the format with a Set Values or Conditional step,
  then return a correcting message rather than failing deep inside.
- **IDs the model shouldn't see:** set `filter_from_agent: True` on outputs that are only for
  chaining, or keep them in variables.

## 5. Named Credentials, caching, long-running

- **HTTP Actions use a Named Credential.** Never put tokens or keys in properties. The agent's
  runtime user needs the permission set with **External Credential Principal Access** (see
  `external-integration-and-multi-agent.md`).
- **Caching:**
  - **Org cache** is shared across users, so use it for non-sensitive reference data only.
  - **Session cache** is per user.
  - **Top-level cache** is set in Procedure Configuration with *Salesforce Platform Cache Type*
    and *Time To Live In Minutes*.
  - **Cache Blocks** cache a group of steps with their own TTL.
  - Never cache steps that do DML, or user-specific data in org cache.
- **Long-running work:**
  - Settings: `chainable` / `queueableChainable`, or `useFuture` / `useQueueable` for
    fire-and-forget.
  - Agent turns can't wait minutes. Return an acknowledgement + reference ID and finish
    asynchronously (the asynchronous action pattern).

## 6. Debugging and testing

1. **Preview tab** with real input JSON. Read every step's output and the Browser/Server/Apex CPU
   timings.
2. Enable **error logging** → failures go to `OmniComponentErrorLog`.
3. Test each Data Mapper alone, then the IP, then the agent action in Builder preview, then an
   eval set of utterances.
4. Run as the **agent user**: Data Mapper FLS and Named Credential access differ from an admin's.
5. Apex unit tests can call IPs, but HTTP mocking isn't possible in the core runtime and real
   callouts happen. Put callouts behind Named Credentials pointing at a stub in sandboxes.

Practitioner gotchas (not in official docs; verify in your org):
- In formulas, `OR()` / `AND()` with 3+ arguments behave unexpectedly.
- Booleans from Salesforce can arrive as the string `"true"`.
- An empty Extract list can silently end the IP.
- With Send Only Additional Input, child steps can't see parent variables.
- A Response Action with "return only additional output" always ends the IP.
- Descriptions over 255 characters fail deployment.

## 7. Metadata and deployment

| Component | Metadata type | Query |
|---|---|---|
| Integration Procedure | `OmniIntegrationProcedure` | `SELECT Id,Name,Type,SubType,IsActive FROM OmniProcess WHERE IsIntegrationProcedure = true` |
| OmniScript | `OmniScript` | same `OmniProcess` table, `IsIntegrationProcedure = false` |
| Data Mapper | `OmniDataTransform` (+ `OmniDataTransformItem`) | `SELECT Id,Name,Type FROM OmniDataTransform` |
| FlexCard | `OmniUiCard` | — |

```bash
sf project retrieve start -m OmniIntegrationProcedure:CustomerGetBillingSummary -o dev
sf project deploy start -m OmniDataTransform:CustomerBillingExtract -o qa --dry-run
sf project deploy start -m OmniDataTransform:CustomerBillingExtract -m OmniIntegrationProcedure:CustomerGetBillingSummary -o qa
```

- **Deploy order:** Data Mappers → Apex for Remote Actions → nested IPs → parent IP → agent
  bundle. Then **activate** (`IsActive = true`). Deploys and DataPacks can leave components
  inactive.
- Orgs on the managed package (`vlocity_cmt`/`vlocity_ins`) use DataPacks (`vlocity` build tool)
  and different property names than core OmniStudio. Confirm which runtime the org uses first.
- The agent action references the IP by name. Re-verify the Agentforce I/O configuration after
  changing the IP's input or output JSON.

## 8. Where OmniScripts and FlexCards fit

- **OmniScripts** are guided, multi-step UIs for humans that call IPs for data. **FlexCards** are
  contextual cards whose data source or actions are IPs.
- The IP is the shared back end. One IP can serve a FlexCard, an OmniScript **and** an agent
  action, which is the main reason to expose IPs to agents instead of re-implementing in Flow.
- Agents don't run OmniScripts. When a step needs structured human input (forms, e-signature),
  hand off to the OmniScript in the UI, or collect the input conversationally and call the IP.

## Anti-patterns

- Exposing the raw Data JSON (nested element nodes, `Info` blocks) as the action output.
- Treating "no records" as success and letting the model invent an answer.
- DML or callouts without Try-Catch and an `errorMessage`.
- User-specific data in org cache, or caching anything that writes.
- Assuming sharing is enforced when the IP is invoked from Apex.
- Hard-coded record IDs or endpoints in element properties.
- Circular IP calls, or deep nesting that blows CPU limits mid-conversation.
- Deploying the IP before its Data Mappers, or forgetting to activate after deploy.

## Sources

- Salesforce Help: *Integrate Integration Procedures with Agentforce* (`xcloud.os_integrate_integration_procedures_with_agentforce.htm`): Configure Agentforce, alphanumeric keys, Asset Library, Metadata API prerequisite.
- Trailhead, *Integration Procedure Fundamentals → Work with Other Omnistudio Components and Agentforce*: https://trailhead.salesforce.com/content/learn/modules/omnistudio-integration-procedure-fundamentals/work-with-other-omnistudio-components-and-agentforce (file-based / OOTB IPs excluded).
- `salesforce/agentscript`: `dialect/agentforce/src/lint/passes/action-target.ts` (valid schemes) and `packages/compiler/src/nodes/action-types.ts` (`integrationProcedureAction` → `executeIntegrationProcedure` alias); fixture `edge_action_alias_targets.agent`.
- `forcedotcom/sf-skills`: `omnistudio-integration-procedure-generate` (elements, PropertySetConfig, `OmniProcess`/`IsIntegrationProcedure`, deploy order, guardrails) and `omnistudio-datamapper-generate` (types, `OmniDataTransform`).
- `slorenzot/context7-llm-omnistudio-integration-procedures`: help-distilled pages on actions, blocks, error handling, caching, long-running, invocation, Summer '25 Connect API (`ConnectApi.OmniDesignerConnect.integrationProcedureExecute`, sharing note); practitioner gotchas files (naming, runtime gotchas).
- Salesforce Architects, *Agentic Integration Patterns* (idempotency, explicit failure, async acknowledgement): https://architect.salesforce.com/docs/architect/fundamentals/guide/agentic-integration-patterns
