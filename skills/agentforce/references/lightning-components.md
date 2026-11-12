# Lightning components and rich agent UI

There are three ways UI meets an agent:

1. **Custom Lightning Types (CLTs):** replace the default input form or output text for an
   action with your own LWC, inside the agent's chat.
2. **Surfaces that host the agent:** the Lightning Experience panel, mobile, Experience Cloud,
   Enhanced Chat v2, or external React/Angular apps via the conversation client.
3. **Your own LWC calling an agent or prompt** through Apex. This is a custom UI, not the
   standard chat.

Channel setup (Messaging, Embedded Service, Slack, voice) is in `connections-and-channels.md`.

## 1. When to use a CLT

| Need | Use |
|---|---|
| The agent should collect several structured fields at once (form, picklists, dates) | CLT **editor** on the action input |
| The result is a card, table or status block, not prose | CLT **renderer** on the action output |
| Plain text is fine | Nothing. Write a good output `description` and let the LLM phrase it |
| A list of records the default UI already renders well | `lightning__recordInfoType` output, no CLT |
| Reusable visual card across agent and MCP or other surfaces | Widget rendition (`@widget/c/<name>` in `renderer.json`, sf-skills; verify availability in your org) |

A CLT costs you a type bundle, an Apex shape, an LWC, deploy order and per-channel testing. Use
one when the UI clearly beats text, not by default.

## 2. The wiring chain

Three layers must agree on one name (`c__caseInput`, `c__caseResult`):

```text
Agent Script action                Lightning Type bundle               LWC
complex_data_type_name:            lightningTypes/caseInput/            caseInputEditor.js-meta.xml
  "c__caseInput"          ───►       schema.json → @apexClassType/…      target  lightning__AgentforceInput
  is_user_input: True                lightningDesktopGenAi/editor.json   targetType c__caseInput
                                       → c/caseInputEditor
complex_data_type_name:            lightningTypes/caseResult/           caseResultRenderer.js-meta.xml
  "c__caseResult"         ───►       schema.json → @apexClassType/…      target  lightning__AgentforceOutput
  is_displayable: True               lightningDesktopGenAi/renderer.json sourceType c__caseResult
  filter_from_agent: False             → c/caseResultRenderer
```

File tree (recipe `02_actionConfiguration/customLightningTypes`):

```text
classes/CaseSubmissionService.cls            # inner classes CaseInput, CaseResult (+ request/response wrappers)
lightningTypes/caseInput/schema.json
lightningTypes/caseInput/lightningDesktopGenAi/editor.json
lightningTypes/caseResult/schema.json
lightningTypes/caseResult/lightningDesktopGenAi/renderer.json
lwc/caseInputEditor/{html,js,js-meta.xml}
lwc/caseResultRenderer/{html,js,js-meta.xml}
aiAuthoringBundles/CustomLightningTypes/CustomLightningTypes.agent
```

### Agent Script

```agentscript
subagent case_submission:
   description: "Case submission"
   reasoning:
      instructions: ->
         if not @variables.case_submitted:
            | If ACTION_CONFIRM, call {!@actions.submit_case} with the form data. Do NOT output any text.
              Otherwise call {!@actions.submit_case} user_input to show the form. Do NOT output any text.
         else:
            | Case {!@variables.case_number} submitted. Call show_command to display the submit_case case_result output.
      actions:
         submit_case: @actions.submit_case
            with case_data = ...
            set @variables.case_submitted = True
            set @variables.case_number = @outputs.case_number

   actions:
      submit_case:
         description: "Submits a support case"
         inputs:
            case_data: object
               description: "Case details including subject, priority, and description"
               is_required: True
               is_user_input: True                  # render the CLT editor
               complex_data_type_name: "c__caseInput"
         outputs:
            case_number: string
               description: "The case number assigned to the new case"
               filter_from_agent: False
               is_displayable: False
            case_result: object
               description: "The created case details for display"
               complex_data_type_name: "c__caseResult"
               filter_from_agent: False
               is_displayable: True                 # render the CLT renderer
         target: "apex://CaseSubmissionService"
         include_in_progress_indicator: True
         progress_indicator_message: "Submitting your case..."
```

Wording matters (recipe README):
- "call {!@actions.x} … **user_input** tool" prompts the platform to show the editor form.
- The submitted form comes back as an `ACTION_CONFIRM` message. Route it in the start agent too
  ("Call for case-related requests and ACTION_CONFIRM messages").
- "Call **show_command** to display the … output" renders a displayable output.
- "Do NOT output any text" stops the LLM narrating over the card.

### Lightning type bundle

Apex-backed (the simplest, and deploy-safe for nested data):

```json
// lightningTypes/caseInput/schema.json
{ "title": "Case Input", "description": "Support case submission data",
  "lightning:type": "@apexClassType/c__CaseSubmissionService$CaseInput" }
```

```json
// lightningTypes/caseInput/lightningDesktopGenAi/editor.json
{ "editor": { "componentOverrides": { "$": { "definition": "c/caseInputEditor" } } } }
```

```json
// lightningTypes/caseResult/lightningDesktopGenAi/renderer.json
{ "renderer": { "componentOverrides": { "$": { "definition": "c/caseResultRenderer" } } } }
```

JSON-schema-backed (no Apex class):

```json
{
  "title": "Flight Offer",
  "type": "object",
  "lightning:type": "lightning__objectType",
  "unevaluatedProperties": false,
  "required": ["flightId"],
  "properties": {
    "flightId":  { "title": "Flight", "lightning:type": "lightning__textType" },
    "departure": { "title": "Departs", "lightning:type": "lightning__dateTimeType" },
    "price":     { "title": "Price", "lightning:type": "lightning__numberType", "minimum": 0 },
    "stops":     { "title": "Stops", "lightning:type": "lightning__integerType" }
  }
}
```

Schema rules (sf-skills `platform-custom-lightning-type-generate`, validated by the CLT metaschema):

| Rule | Consequence if broken |
|---|---|
| Never include `"$schema"` | Rejected |
| Root: `type: object`, `title`, `lightning:type: lightning__objectType`, `unevaluatedProperties: false` | Rejected |
| No `examples` at the root. No unknown keywords anywhere | Rejected under `unevaluatedProperties: false` |
| Nested objects must not set `lightning__objectType`. Reference another CLT (`c__Other`) or use Apex | Validation error |
| Arrays: avoid `items`. Root arrays use `lightning__listType`. Nested arrays use only `type: array` | Rejected |
| Apex-class CLT: only `title`, `description`, `lightning:type` | Extra keys rejected |
| Apex-class CLT overrides: **no `attributes`** in the root override | `unevaluatedProperties` error (no `properties` to bind) |
| `{!$attrs.<prop>}` only for properties that exist in the schema | Validation error |
| Use `componentOverrides` and `layout`. `propertyRenderers` and `view` are deprecated | `additionalProperties` error |
| `lightning/propertyLayout` accepts only `property` | `additionalProperties` error |
| Deploy referenced CLTs before the CLTs that reference them | Invalid reference |

Primitive `lightning:type`s: `textType` (≤255), `multilineTextType` (≤2000), `richTextType`
(≤100000), `urlType`, `dateType`, `timeType`, `dateTimeType` (`{dateTime, timeZone}`),
`numberType`, `integerType`, `booleanType`, all prefixed `lightning__`. Property-level
built-ins: `es_property_editors/inputText|inputNumber|inputTextarea|inputRichText|inputImage`
and `outputText|outputNumber|outputImage`. Don't use `inputList`.

### Channel folders

The default UI can be overridden separately per channel (LightningTypeBundle metadata docs):

| Folder | Surface |
|---|---|
| `lightningDesktopGenAi/` | Employee agent in Lightning Experience |
| `lightningMobileGenAi/` | Employee agent on mobile, Service agent in Enhanced Chat v2 on mobile |
| `enhancedWebChat/` | Service agent in Enhanced Chat v2 (web) |
| `experienceBuilder/` | Experience Builder (editor only, **no `renderer.json`**) |

A CLT that only has `lightningDesktopGenAi/` falls back to the default UI on other channels.
Add a folder for every channel you deploy to, and test on each.

### Apex shapes

```apex
public with sharing class CaseSubmissionService {
    public class CaseInput {                                 // ← caseInput/schema.json
        @InvocableVariable(label='Subject' description='Case subject' required=true) public String subject;
        @InvocableVariable(label='Priority' description='Case priority') public String priority;
        @InvocableVariable(label='Description' description='Case description') public String description;
    }
    public class CaseResult {                                // ← caseResult/schema.json
        @InvocableVariable(label='Case Number') public String caseNumber;
        @InvocableVariable(label='Status') public String status;
        // …
    }
    public class SubmitCaseRequest  { @InvocableVariable(required=true) public CaseInput case_data; }   // == input name
    public class SubmitCaseResponse { @InvocableVariable public CaseResult case_result;                // == output names
                                      @InvocableVariable public String case_number; }
    @InvocableMethod(label='Submit Case' description='Submits a support case')
    public static List<SubmitCaseResponse> submitCase(List<SubmitCaseRequest> requests) { /* … */ }
}
```

Wrapper field names equal the Agent Script I/O names (`case_data`, `case_result`). The inner
classes are the CLT shapes. The widget-rendition path in sf-skills uses `@AuraEnabled` fields
on the outer class instead. Follow the path you're on (verify in your org).

### LWC editor and renderer

```xml
<!-- caseInputEditor.js-meta.xml -->
<LightningComponentBundle xmlns="http://soap.sforce.com/2006/04/metadata">
    <apiVersion>68.0</apiVersion>
    <isExposed>true</isExposed>
    <targets><target>lightning__AgentforceInput</target></targets>
    <targetConfigs>
        <targetConfig targets="lightning__AgentforceInput">
            <targetType name="c__caseInput" />
        </targetConfig>
    </targetConfigs>
</LightningComponentBundle>
<!-- renderer: target lightning__AgentforceOutput + <sourceType name="c__caseResult"/> -->
```

```js
// caseInputEditor.js: receives `value` + `readOnly`, emits `valuechange`
import { api, LightningElement } from 'lwc';
export default class CaseInputEditor extends LightningElement {
    @api readOnly = false;
    _value = {};
    subject = ''; priority = ''; description = '';
    @api get value() { return this._value; }
    set value(v) {
        this._value = v;
        if (v) { this.subject = v.subject ?? ''; this.priority = v.priority ?? ''; this.description = v.description ?? ''; }
    }
    handleInputChange(event) {
        event.stopPropagation();
        const { name, value } = event.target;
        this[name] = value;
        this.dispatchEvent(new CustomEvent('valuechange', {
            detail: { value: { subject: this.subject, priority: this.priority, description: this.description } }
        }));
    }
}
```

```js
// caseResultRenderer.js: read-only, receives the action output as `value`
import { LightningElement, api } from 'lwc';
export default class CaseResultRenderer extends LightningElement {
    @api value;
    get caseNumber() { return this.value?.caseNumber ?? ''; }
    get status()     { return this.value?.status ?? ''; }
}
```

LWC rules:
- The editor emits the **whole** object on every change (`detail.value`), with keys equal to the
  Apex inner-class field names. Respect `readOnly` (it's set after submit).
- Null-safe getters everywhere. The renderer may get partial data.
- SLDS and base components only. No external fetches. The chat surface is not your page.
- Missing the target in `js-meta.xml` fails the deploy: `Invalid target configuration … must
  include valid target 'lightning__AgentforceOutput/Input'`.

### Known problems

- **Draft preview doesn't render CLT renderers.** Commit and activate the agent version, then
  test in the real surface (recipe README: "Known Platform Bug").
- The user needs **agent access** on their permission set or the agent won't appear in
  Lightning chat (recipe README).
- ADLC (Feb 2026) reported custom Lightning UI not rendering on the newer planner. The Nov 2026
  recipe shows CLTs working after activation, so treat the ADLC report as stale but test early
  on your planner (`planner_type`) and channel.
- Older agents used `GenAiFunction` metadata with `input/schema.json` and `output/schema.json`
  (`copilotAction:isUserInput`, `copilotAction:isDisplayable`). The recipe still ships one and links
  it with `source: "Submit_Case"`. In Agent Script, the `.agent` I/O properties are the source of truth.

## 3. Surfaces that host the agent

| Surface | How | Notes |
|---|---|---|
| Lightning Experience | The Agentforce panel (Employee agents) | User needs agent access through a permission set. CLT folder `lightningDesktopGenAi` |
| Salesforce mobile app | Same agent, mobile panel | CLT folder `lightningMobileGenAi` |
| Experience Cloud site | Embedded Messaging / Enhanced Chat v2 component (Service agent) | CLT folder `enhancedWebChat`. Experience Builder editors use `experienceBuilder` |
| External React or Angular app (UI Bundle) | `<AgentforceConversationClient agentId="0Xx…"/>` (`@salesforce/ui-bundle-template-feature-react-agentforce-conversation-client`) or `<app-agentforce-conversation-client>` (Angular) | Lightning Out 2.0 iframe. Theme **only** with `styleTokens`. Width and height as props. For local dev, add `localhost:<port>` to *Trusted Domains for Inline Frames* and remove it before production |

Resolving the agent ID for the conversation client (`0Xx` prefix, 18 chars; Employee agents
only per sf-skills):

```bash
sf data query --json --query "SELECT BotDefinition.Id, BotDefinition.DeveloperName, Status \
  FROM BotVersion WHERE BotDefinition.AgentType = 'AgentforceEmployeeAgent'"
```

Never build your own chat UI where the conversation client or a standard channel fits. Custom
UIs lose the Trust Layer UX, CLT rendering, streaming and standard escalation (sf-skills
constraints).

## 4. Custom LWC that calls an agent or template

Use this when the agent is a feature *inside* your page (a "summarise this account" button, an
inline assistant), not a chat. Call the agent or prompt from `@AuraEnabled` Apex and render the
result yourself:

```js
import startSession from '@salesforce/apex/AgentforceAgentController.startSession';
import sendMessage from '@salesforce/apex/AgentforceAgentController.sendMessage';
// startSession({ agentId: 'My_Agent' }) → { sessionId, agentGreeting }
// sendMessage({ agentId, sessionId, message }) → reply text
```

The Apex uses `Invocable.Action.createCustomAction('generateAiAgentResponse', agentApiName)`
(`flows-and-apex.md` §5). For one-shot generation, call a prompt template with
`ConnectApi.EinsteinLLM.generateMessagesForPromptTemplate` (`prompt-builder.md` §4). Treat the
reply as **untrusted text**: render it with `lightning-formatted-rich-text` or plain text, never
`innerHTML`, and don't execute anything it suggests without a user click.

## Anti-patterns

- A CLT for output that reads fine as a sentence.
- One channel folder, deployed to three channels.
- `attributes` mapping on an Apex-class CLT override.
- `"$schema"`, `items` or `examples` in `schema.json`.
- Testing CLTs only in Draft Builder preview.
- An editor that emits only the changed field.
- Letting the LLM narrate over a renderer card ("Here are your details: …" + the card).
- A hand-rolled chat widget instead of the conversation client or Enhanced Chat.

## Sources

- `trailheadapps/agent-script-recipes`: `02_actionConfiguration/customLightningTypes` (README with known
  bug and permission note, `.agent`, `CaseSubmissionService.cls` + test, `lightningTypes/*`, `lwc/*`,
  `genAiFunctions/Submit_Case`).
- `salesforce/agentscript`: `dialect/agentforce/src/schema.ts` (`is_user_input`, `is_displayable`,
  `filter_from_agent`, `complex_data_type_name`), `apps/docs/.../reference/ascript-ref-actions.md`.
- `forcedotcom/sf-skills`: `platform-custom-lightning-type-generate` (SKILL.md, `assets/primitive-types-and-constraints.md`),
  `platform-metadata-api-context-get/assets/metadata_api/LightningTypeBundle.json` (channel folders),
  `platform-lightning-type-widget-coordinate` (widget rendition, `@AuraEnabled` shapes),
  `experience-ui-bundle-agentforce-client-generate` (conversation client, agent-id SOQL, constraints).
- `SalesforceAIResearch/agentforce-adlc`: `references/known-issues.md` #8 (rendering on the new planner).
- `SalesforceLabs/Flow-Agentforce-Chat`: `classes/AgentforceAgentController.cls` (LWC → Apex → agent).
