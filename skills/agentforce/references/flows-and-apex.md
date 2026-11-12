# Flows and Apex as agent actions

An Agent Script action is a typed contract (`inputs`, `outputs`, `target`) over something the
platform already runs. This file covers the two workhorse targets, `flow://` and `apex://`. It also
covers going the other way (flows and Apex that call an agent or a model) and how to test both.
Prompt templates are in `prompt-builder.md`, rich UI in `lightning-components.md`, and
Integration Procedures and External Services in their own files.

## 1. Pick the target

| The action needs to… | Use | Why |
|---|---|---|
| CRUD on a few records, simple branching, admin-maintained | `flow://` (autolaunched) | Declarative, admins can own it, no test-coverage gate |
| Callouts with auth, retries, parsing, or anything you'd unit-test | `apex://` | Full control, real tests, `WITH USER_MODE` |
| Many records, heavy logic, aggregation, or complex return shapes | `apex://` | Bulk-safe and fast. Inner classes become typed outputs |
| Return a generated text (summary, draft, recommendation) | `generatePromptResponse://` | The template owns the wording and grounding (see `prompt-builder.md`) |
| An OmniStudio data pipeline that already exists | `integrationProcedureAction://` | Reuse it, don't rewrite it |
| Call a REST API that has an OpenAPI spec | `externalService://` | No code. Named Credential auth |
| A built-in platform action (send email, etc.) | `standardInvocableAction://` | Already exists. The only target that allows `async_mode` |
| Logic is pure branching on known variables | Neither: use `if` / `available when` in the script | Deterministic, free, no action call |

Rules of thumb:

- **Do the deterministic work in code, not in the LLM.** If a step must always happen, `run` it
  from the reasoning logic or `before_reasoning`. Don't hope the planner calls it.
- **One action, one business task.** Merge calls that always run in sequence (look up the
  contact, then fetch their orders) into one flow or one Apex class.
- **Return the minimum.** Large payloads bloat context and have been reported to drop
  earlier state (ADLC known issue #9). Summarise in code, and set `filter_from_agent: True` on
  anything the LLM doesn't need.
- **Stubs:** when the implementation doesn't exist yet, stub it as invocable Apex that
  returns fixed values. Don't hand-craft Flow XML (ADLC guidance).

Valid target schemes, from the Agentforce dialect lint:
`flow apex apexRest auraEnabled standardInvocableAction quickAction api externalService
externalConnector slack prompt generatePromptResponse serviceCatalog createCatalogItemRequest
cdpMlPrediction decisionTableAction namedQuery integrationProcedureAction executeIntegrationProcedure
mcpTool platformMcpTool expressionSet runExpressionSet retriever placeholder`. The compiler maps
aliases: `prompt`→`generatePromptResponse`, `integrationProcedureAction`→`executeIntegrationProcedure`,
`expressionSet`→`runExpressionSet`, `serviceCatalog`→`createCatalogItemRequest`. A
`placeholder://` target compiles with a warning, which helps when sketching an agent before its
actions exist.

## 2. Wiring contract (both targets)

```agentscript
subagent weather_lookup:
   description: "Weather lookup"
   actions:                                  # Level 1: definition (typed contract)
      get_current_weather:
         description: "Retrieves current weather conditions for a specified location"
         inputs:
            city_name: string                # == flow variable name / @InvocableVariable field
               description: "The name of the city to get weather for"
               is_required: True
            units: string
               description: "Temperature units (fahrenheit, celsius, or kelvin)"
         outputs:
            temperature: number
               description: "Current temperature in the specified units"
            weather_condition: string
               description: "Description of current weather (e.g., sunny, cloudy)"
         target: "flow://GetCurrentWeather"
         include_in_progress_indicator: True
         progress_indicator_message: "Checking the weather..."

   reasoning:
      actions:                               # Level 2: expose as a tool + bind
         get_current_weather: @actions.get_current_weather
            with city_name=...               # LLM slot-fills from the conversation
            with units="fahrenheit"          # fixed value, LLM can't change it
            set @variables.current_temperature = @outputs.temperature
            set @variables.conditions = @outputs.weather_condition
```

| Rule | Detail |
|---|---|
| Names match exactly | Input and output names must equal the flow variable API names or the `@InvocableVariable` field names, **case included** (`orderAmount` ≠ `order_amount`) |
| Always declare `outputs:` | An action with a `target:` and no `outputs:` publishes as "Internal Error". LSP and CLI validation still pass (ADLC issue #15). Declaring a subset of outputs is fine |
| Descriptions are behaviour | The planner reads each action and parameter `description`. Write what, when and format ("ISO date YYYY-MM-DD") |
| Bind instead of slot-fill when you can | `with x=@variables.y` for known values, `...` only for what the user must supply. Never make the LLM re-type an ID it already has |
| Gate writes | `available when @variables.validation_passed` on the tool, plus a guard inside the flow or Apex. Don't rely on `require_user_confirmation`: it compiles, but ADLC reports no confirmation dialog at runtime (issue #6). Use a two-step confirm with a variable |
| Hide internals | `filter_from_agent: True` keeps an output out of LLM context. Pair it with `is_used_by_planner: True` to route on a value the LLM can't echo (verify in your org) |
| Deterministic call | `run @actions.x` inside `instructions: ->` logic, or `before_reasoning` / `after_reasoning`, runs every time |
| Chaining | Add `run @actions.next` under a tool's `set` lines to run a follow-up action straight after it |

### Types and `complex_data_type_name`

| Data | Agent Script I/O type | `complex_data_type_name` | Seen in |
|---|---|---|---|
| Text, boolean, date, id | `string` / `boolean` / `date` / `id` | none | recipes |
| One SObject record (flow `SObject` var) | `object` | `lightning__recordInfoType` | ADLC, dialect docs |
| SObject collection (flow `SObject`, `isCollection`) | `list[object]` | `lightning__recordInfoType` | `SearchHotels` recipe |
| Text collection | `list[string]` | `lightning__textType` | `GetWeatherForecast` recipe |
| Apex inner class | `object` / `list[object]` | `@apexClassType/c__Outer$Inner` | ADLC (`c__PropertyQueryService$PropertyInfo`) |
| Custom Lightning Type | `object` | `c__<typeName>` | CLT recipe |
| Rich text | `string`/`object` | `lightning__richTextType` | recipes |
| Integer | see note | `lightning__numberType` (flow) / `lightning__integerType` (Apex) | ADLC |

**Number caveat.** Official recipes and the dialect reference use bare `number` in action I/O,
and those recipes deploy. ADLC reports bare `number` failing at publish in some orgs. If
publish fails on a numeric parameter, switch it to `object` plus `lightning__numberType`
(flow target) or `lightning__integerType` (Apex target). The two are not interchangeable
(verify in your org).

Common mistakes:
- `lightning__recordInfoType` on an `apex://` output. Apex returns its own class, so use `@apexClassType/...`.
- Outer class only (`@apexClassType/c__PropertyQueryService`). Include `$Inner`.
- `apex://ns__Class` vs `apex://Class` in namespaced orgs is unresolved: publish may say
  "invocable action does not exist". Workaround: wrap the Apex in a flow and target `flow://`
  (ADLC issue #2).

## 3. Autolaunched flows as actions

Requirements (ADLC, recipes):

1. `<processType>AutoLaunchedFlow</processType>` with no trigger. Screen flows,
   record-triggered flows and scheduled flows are not valid targets. An invalid target can pass
   validation and simulated preview, then fail at publish or in live mode (ADLC).
2. `<status>Active</status>`.
3. Inputs are `isInput=true` ("Available for input"). Outputs are `isOutput=true`
   ("Available for output").
4. The agent user can run the flow and read the objects it touches (see §6).

```xml
<!-- GetCurrentWeather.flow-meta.xml (agent-script-recipes, trimmed) -->
<Flow xmlns="http://soap.sforce.com/2006/04/metadata">
    <apiVersion>68.0</apiVersion>
    <label>Get Current Weather</label>
    <processType>AutoLaunchedFlow</processType>
    <status>Active</status>
    <variables>
        <name>city_name</name>            <!-- == action input name -->
        <dataType>String</dataType>
        <isCollection>false</isCollection>
        <isInput>true</isInput>
        <isOutput>false</isOutput>
    </variables>
    <variables>
        <name>temperature</name>          <!-- == action output name -->
        <dataType>Number</dataType>
        <isCollection>false</isCollection>
        <isInput>false</isInput>
        <isOutput>true</isOutput>
        <scale>2</scale>
    </variables>
</Flow>
```

SObject collection output (maps to `list[object]` + `lightning__recordInfoType`):

```xml
<variables>
    <name>hotels</name>
    <dataType>SObject</dataType>
    <isCollection>true</isCollection>
    <isInput>false</isInput>
    <isOutput>true</isOutput>
    <objectType>ASR_Hotel__c</objectType>
</variables>
```

**Fault paths: return an error, don't throw it.** An unhandled fault surfaces as the agent's
generic `system.messages.error`, and the LLM learns nothing. Give every DML, lookup and callout
element a `faultConnector` to an assignment that fills `success=false` and `errorMessage`.
The agent can then explain the failure or recover.

```xml
<recordUpdates>
    <name>Update_From</name>
    <!-- … -->
    <faultConnector><targetReference>Assign_Failure</targetReference></faultConnector>
</recordUpdates>
<assignments>
    <name>Assign_Failure</name>
    <assignmentItems>
        <assignToReference>success</assignToReference>
        <operator>Assign</operator>
        <value><booleanValue>false</booleanValue></value>
    </assignmentItems>
    <assignmentItems>
        <assignToReference>errorMessage</assignToReference>
        <operator>Assign</operator>
        <value><elementReference>$Flow.FaultMessage</elementReference></value>
    </assignmentItems>
</assignments>
```

Then branch on it in the script, so the next turn is deterministic:

```agentscript
execute_transfer: @actions.execute_transfer
   available when @variables.validation_passed
   with from_account=@variables.source_account
   with amount=@variables.transfer_amount
   set @variables.transfer_ok = @outputs.success
   set @variables.last_error = @outputs.errorMessage
```

Translate raw `$Flow.FaultMessage` into a user-safe message before it reaches a Service
agent. Raw fault text can leak object and field names.

Flow checklist:
- Inputs validated at the top (decision → fault-style output), not assumed.
- No DML or Get Records inside loops. Agent calls are one request at a time, but the same
  flow is often reused by bulk automation.
- Output text fields are short and human-readable. Return IDs separately, filtered from the
  agent, for later binding.
- The flow `<description>` and variable descriptions are filled in. The Builder UI uses them
  when it generates an action from the flow.

## 4. Invocable Apex as actions

Shape (CLT and actionDefinitions recipes, ADLC template):

```apex
public with sharing class CalculateDiscountAction {
    public class Request {
        @InvocableVariable(label='Order Amount' description='Order total before discount' required=true)
        public Decimal orderAmount;                 // action input `orderAmount`
        @InvocableVariable(label='Customer Tier' description='Bronze, Silver, Gold or Platinum' required=true)
        public String customerTier;
    }
    public class Result {
        @InvocableVariable(label='Is Success' description='Whether the calculation succeeded')
        public Boolean isSuccess;
        @InvocableVariable(label='Error Message' description='Why it failed, safe to show the user')
        public String errorMessage;
        @InvocableVariable(label='Final Amount' description='Amount after discount')
        public Decimal finalAmount;
    }

    @InvocableMethod(label='Calculate Discount'
                     description='Calculates the discount for an order from the customer tier')
    public static List<Result> execute(List<Request> requests) {
        List<Result> results = new List<Result>();
        for (Request req : requests) {               // one Result per Request, same order
            Result r = new Result();
            try {
                r.finalAmount = req.orderAmount * (1 - tierDiscount(req.customerTier) / 100);
                r.isSuccess = true;
            } catch (Exception e) {
                r.isSuccess = false;
                r.errorMessage = 'Could not calculate the discount: ' + e.getMessage();
            }
            results.add(r);
        }
        return results;
    }
    private static final Map<String, Decimal> TIER_DISCOUNT =
        new Map<String, Decimal>{ 'Bronze' => 5, 'Silver' => 10, 'Gold' => 15, 'Platinum' => 20 };
    private static Decimal tierDiscount(String tier) {
        return TIER_DISCOUNT.containsKey(tier) ? TIER_DISCOUNT.get(tier) : 0;
    }
}
```

```agentscript
calculate_discount:
   description: "Calculates the discount for an order from the customer's tier"
   inputs:
      orderAmount: number
         description: "Order total before discount"
         is_required: True
      customerTier: string
         description: "Bronze, Silver, Gold or Platinum"
         is_required: True
   outputs:
      isSuccess: boolean
         description: "Whether the calculation succeeded"
         filter_from_agent: True
         is_used_by_planner: True
      errorMessage: string
         description: "Reason for failure, safe to show"
      finalAmount: number
         description: "Amount after discount"
   target: "apex://CalculateDiscountAction"
```

| Rule | Why |
|---|---|
| Use wrapper classes. A bare `List<String>` signature won't bind | Agent Script binds by `@InvocableVariable` field name. Bare signatures also trigger the "Internal Error" publish failure (ADLC) |
| One `@InvocableMethod` per class | Apex platform rule. One class per action |
| `public with sharing` + `WITH USER_MODE` (static SOQL) or `AccessLevel.USER_MODE` (dynamic) | Enforces the running user's CRUD, FLS and sharing. `WITH USER_MODE` inside a dynamic SOQL string throws `unexpected token: 'WITH'` |
| Return `isSuccess` + `errorMessage`, don't throw | An exception becomes the agent's generic error. A structured result lets the script branch |
| Bulkify: collect IDs, query once, map results | The same invocable may be called from flows with many records |
| `label` and `description` on the method and every variable | They seed the Builder UI and the planner's understanding |
| Supported variable types: primitives, `Id`, `Date`/`DateTime`/`Time`, SObjects, Apex-defined classes, `List<>` of these. No `Map` | Use inner classes for structure and reference them with `@apexClassType/c__Outer$Inner` |
| Callouts: Named Credential, timeout set, no DML before the callout | Avoids "uncommitted work pending" errors and leaked secrets |
| Keep payloads small: select only the fields you need | Large outputs eat context (ADLC issue #9) |

**Silent-empty trap.** `WITH USER_MODE` on an object the agent user can't read returns
**0 rows, no error**. If live preview returns nothing but simulated preview works, check the
agent user's object permissions (ADLC `agent-user-setup.md`).

## 5. Flows and Apex that call an agent or a model

**Invoke an agent from a flow.** The Flow action type is `generateAiAgentResponse`, and its
`actionName` is the agent's API name. It takes `userMessage` and an optional `sessionId` for a
follow-up turn. It returns `agentResponse` and `sessionId`.

```xml
<actionCalls>
    <name>Search_CV</name>
    <actionName>Agentforce_Recruitment_Agent_Invokable</actionName>
    <actionType>generateAiAgentResponse</actionType>
    <flowTransactionModel>CurrentTransaction</flowTransactionModel>
    <inputParameters>
        <name>userMessage</name>
        <value><stringValue>{!EmailString}</stringValue></value>
    </inputParameters>
    <storeOutputAutomatically>true</storeOutputAutomatically>
</actionCalls>
<!-- 2nd turn: pass <name>sessionId</name> = {!Search_CV.sessionId} -->
```

Use it from record-triggered flows on an **asynchronous path**. The agent call does model and
callout work, so keep it out of the save transaction. Which agent types can be invoked this way
depends on the release; Employee agents are the common case (verify in your org).

**Invoke an agent from Apex:** the same invocable action.

```apex
Invocable.Action action = Invocable.Action.createCustomAction('generateAiAgentResponse', 'My_Agent_Api_Name');
action.setInvocationParameter('userMessage', message);
if (sessionId != null) action.setInvocationParameter('sessionId', sessionId);
Invocable.Action.Result res = action.invoke()[0];
if (!res.isSuccess()) throw new AuraHandledException(res.getErrors()[0].getMessage());
String reply = (String) res.getOutputParameters().get('agentResponse');
```

This runs as the current user, with no Connected App needed (SalesforceLabs Flow-Agentforce-Chat).
For calls from outside Salesforce, use the Agent API (see `connections-and-channels.md`).

**Call a model directly from Apex (Models API).** Use this for one-off generation where no agent
or template is needed. It goes through the Einstein Trust Layer.

```apex
aiplatform.ModelsAPI.createChatGenerations_Request req = new aiplatform.ModelsAPI.createChatGenerations_Request();
req.modelName = 'sfdc_ai__DefaultGPT4OmniMini';          // fully qualified sfdc_ai__… name
aiplatform.ModelsAPI_ChatGenerationsRequest body = new aiplatform.ModelsAPI_ChatGenerationsRequest();
aiplatform.ModelsAPI_ChatMessageRequest msg = new aiplatform.ModelsAPI_ChatMessageRequest();
msg.role = 'user'; msg.content = prompt;
body.messages = new List<aiplatform.ModelsAPI_ChatMessageRequest>{ msg };
req.body = body;
try {
    aiplatform.ModelsAPI.createChatGenerations_Response r = new aiplatform.ModelsAPI().createChatGenerations(req);
    return r.Code200.generationDetails.generations[0].content;
} catch (aiplatform.ModelsAPI.createChatGenerations_ResponseException e) {
    return 'Model call failed: ' + e.responseCode;      // e.responseCode = HTTP status
}
```

It also offers `createGenerations` (single prompt → `Code200.generation.generatedText`) and
`createEmbeddings`. Run it from a `Queueable, Database.AllowsCallouts` for batches. The running
user needs the Einstein generative AI permission set. **Prefer a prompt template** whenever an
admin should own the wording, or when you want grounding, versioning and Prompt Builder preview.

## 6. Permissions (the most common runtime failure)

- **Service agents** run as the **agent user** (`default_agent_user`). Give that user the
  `AgentforceServiceAgentUser` permission set **plus your own** `{Agent}_Access` set with
  `<classAccesses>` for **every** Apex class in **every** subagent, and the object/field
  permissions those classes query. The auto-generated `NextGen_{Agent}_Permissions` set is
  often incomplete (ADLC).
- **Employee agents** run as the logged-in user: assign the same custom set to the users.
- If the user lacks permission for *any* action, ADLC reports the *whole* agent can fail at
  startup (issue #10). Split agents by permission boundary if you can't grant broadly.
- `"invocable action does not exist"` at runtime usually means a missing class grant, not a
  missing class.

## 7. Testing

| Layer | How |
|---|---|
| Flow | Apex test: `new Flow.Interview.GetCurrentWeather(inputs).start()`, then `getVariableValue('temperature')` |
| Apex | `@IsTest`: single, bulk (2+ requests) and defaults or error cases. Assert `isSuccess` and `errorMessage` |
| Prompt and agent calls | Mock the boundary: `Test.isRunningTest() ? mockResults : action.invoke()` (Flow-Agentforce-Chat pattern). Keep model calls out of unit tests |
| Agent wiring | `sf agent preview` live mode (not simulated) as the agent user, then Testing Center or `sf agent test` specs (see `testing-and-evals.md`) |
| Permissions | Run the preview as the real agent user. Simulated mode hides permission and empty-result bugs |

```apex
@IsTest
static void transferFailsCleanlyOnBadAccount() {
    Map<String, Object> inputs = new Map<String, Object>{
        'from_account' => 'NOPE', 'to_account' => 'ACC-2', 'amount' => 10 };
    Flow.Interview.ExecuteTransfer fi = new Flow.Interview.ExecuteTransfer(inputs);
    Test.startTest(); fi.start(); Test.stopTest();
    Assert.areEqual(false, (Boolean) fi.getVariableValue('success'));
    Assert.isNotNull(fi.getVariableValue('errorMessage'), 'agent needs a reason to relay');
}
```

## Anti-patterns

- One action per CRUD operation, so the planner has to orchestrate five calls. Write one task-level action instead.
- Exceptions or unhandled faults as the error channel.
- Returning whole SObjects or `JSON.serialize(records)` with every field.
- `snake_case` script names over `camelCase` Apex fields. They don't bind.
- Screen or record-triggered flows as targets.
- Trusting `require_user_confirmation` for destructive writes.
- `without sharing` or system-mode queries in a Service agent action, which leaks across customers.
- Testing only in simulated preview.

## Sources

- `trailheadapps/agent-script-recipes`: `02_actionConfiguration/actionDefinitions`
  (`GetCurrentWeather.flow-meta.xml`, `WeatherAlertService.cls`, `ActionDefinitionsFlowTest.cls`),
  `04_architecturalPatterns/errorHandling`, `multiSubagentNavigation/flows/SearchHotels.flow-meta.xml`,
  `externalAPIIntegration/classes/PaymentGatewayController.cls`.
- `salesforce/agentscript`: `dialect/agentforce/src/lint/passes/action-target.ts` (valid schemes),
  `packages/compiler/src/nodes/action-types.ts` (aliases), `dialect/agentforce/src/schema.ts`
  (I/O properties, `async_mode`), `apps/docs/docs/dialects/agentforce/reference/ascript-ref-actions.md`.
- `SalesforceAIResearch/agentforce-adlc`: `skills/agentforce-generate/references/actions-reference.md`,
  `complex-data-types.md`, `known-issues.md` (#2, #6, #9, #10, #15), `agent-user-setup.md`,
  `assets/invocable-apex-template.cls`, `agent-design-and-spec-creation.md`.
- `SalesforceLabs/Flow-Agentforce-Chat`: `classes/AgentforceAgentController.cls` (Apex agent invocation).
- `McForce/TheRealRecruit`: `flows/AI_Grounding_RTF.flow-meta.xml` (`generateAiAgentResponse` in Flow).
- `albarivas/df24-ai-code`: `classes/LLMService.cls` (Models API from Apex).
- `forcedotcom/sf-skills`: `platform-models-api-configure` (model naming, Models API auth).
