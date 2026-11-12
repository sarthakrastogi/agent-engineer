# Prompt Builder

A prompt template is a versioned prompt (`GenAiPromptTemplate`) with typed inputs, grounding,
a chosen model and an optional response format. It runs through the Einstein Trust Layer.
Agents call templates as actions. Flows, Apex, REST, record pages and LWCs (via Apex) call them
directly. Use a template whenever generated text needs an owner, a version history and a
preview loop. Use a raw Models API call (`flows-and-apex.md` §5) only for one-off code paths.

## 1. Template types

| UI name | Metadata `<type>` | Runs from | Inputs |
|---|---|---|---|
| Flex | `einstein_gpt__flex` | Agent action, Flow, Apex, REST | You define them: objects (`SOBJECT://X`) or text (`primitive://String`). A handful per template (verify the limit in your org) |
| Field Generation | `einstein_gpt__fieldCompletion` | Einstein button on a field (Dynamic Forms), Flow, Apex | Fixed: `RelatedEntity` (the record). Needs `relatedEntity` + `relatedField` (`Object.Field__c`) |
| Record Summary | `einstein_gpt__recordSummary` | Summarize actions, agent, Flow | The record to summarise (Flow/Apex grounding input name `objectToSummarize`) |
| Sales Email | `einstein_gpt__salesEmail` | Einstein sales email drafting | Recipient and sender records |
| Case/Service Email Draft | `einstein_gpt__caseEmailDraft` | Service email drafting | Case context |

"Field Generation" in the UI is `fieldCompletion` in metadata. Only one Field Generation
template can be bound to a field (sf-skills). Match the type to where the template runs. A Flex
template for field population means losing the field's Einstein button and wiring it by hand.

## 2. Metadata shape

```xml
<!-- genAiPromptTemplates/Generate_Personalized_Schedule.genAiPromptTemplate-meta.xml (recipe) -->
<GenAiPromptTemplate xmlns="http://soap.sforce.com/2006/04/metadata">
    <activeVersionIdentifier>ul8eo8Wmi00b…=_1</activeVersionIdentifier>
    <developerName>Generate_Personalized_Schedule</developerName>
    <masterLabel>Generate Personalized Schedule</masterLabel>
    <templateVersions>
        <content>Your name is {!$User.FirstName}. You work in the guest success team at {!$Organization.Name}.
… instructions …
List of activities happening today that match the guest's interests: {!$Apex:PersonalizedGuestExperiences.Prompt}
</content>
        <inputs>
            <apiName>email</apiName>
            <definition>primitive://String</definition>
            <masterLabel>email</masterLabel>
            <referenceName>Input:email</referenceName>      <!-- what callers pass -->
            <required>true</required>
        </inputs>
        <primaryModel>sfdc_ai__DefaultOpenAIGPT4OmniMini</primaryModel>
        <status>Published</status>
        <templateDataProviders>
            <definition>apex://PersonalizedGuestExperiences</definition>
            <parameters>
                <definition>primitive://String</definition>
                <isRequired>true</isRequired>
                <parameterName>email</parameterName>
                <valueExpression>{!$Input:email}</valueExpression>
            </parameters>
            <referenceName>Apex:PersonalizedGuestExperiences</referenceName>
        </templateDataProviders>
        <versionIdentifier>ul8eo8Wmi00b…=_1</versionIdentifier>
    </templateVersions>
    <type>einstein_gpt__flex</type>
    <visibility>Global</visibility>
</GenAiPromptTemplate>
```

| Element | Notes |
|---|---|
| `templateVersions` | One per version. `status` is `Draft` or `Published`. **Published versions are immutable** (UI and Metadata API) |
| `versionIdentifier` / `activeVersionIdentifier` | Platform-generated. **Don't invent them.** Deploy without them, retrieve the generated `versionIdentifier`, set it as `activeVersionIdentifier`, redeploy (sf-skills round trip). `versionNumber` and `activeVersion` stopped working at API 64.0 |
| `inputs.definition` | `SOBJECT://Account`, `SOBJECT://Account/Description`, `primitive://String` |
| `primaryModel` | `sfdc_ai__Default…` standard models, or a configured / bring-your-own model from Einstein Studio |
| `responseFormat` | `JSON` for structured output (pronto `Extract_Menu`). Omit it for text |
| `isCitationEnabled` | Citations for grounded answers |
| `generationTemplateConfigs` | References to generation policies |
| `visibility` | `Global` or `API` |
| `relatedEntity` / `relatedField` | Field Generation binding |
| `overridable` | Whether the template can be cloned and overridden. Seen `false` in samples |

User-created templates **are** retrievable with `sf project retrieve start -m GenAiPromptTemplate:<Name>`.
Managed-package templates are not (sf-skills).

## 3. Grounding: merge fields and data providers

Every merge field other than `$Input`, `$User` and `$Organization` is backed by a
`templateDataProviders` entry. Prompt Builder adds it when you insert the resource. When you
author by hand, the `referenceName` must equal the merge-field prefix.

| Merge field | Data provider `definition` | Use |
|---|---|---|
| `{!$Input:Account.Name}` | none | A field on an input record (relationship traversal allowed) |
| `{!$User.FirstName}`, `{!$Organization.Name}` | none | Context |
| `{!$RelatedList:Input.Reviews__r.Records}` | `invocable://getRelatedList` (params `parentRecordId`, `relatedListName`) | A related list, including `CombinedAttachments` for files (pronto) |
| `{!$Flow:My_Grounding_Flow.Prompt}` | `flow://My_Grounding_Flow` | Logic-heavy declarative grounding (template-triggered prompt flow, below) |
| `{!$Apex:MyGrounding.Prompt}` | `apex://MyGrounding` | Code grounding: queries, callouts, formatting |
| `{!$EinsteinSearch:My_Retriever.results}` | `invocable://getEinsteinRetrieverResults/My_Retriever` (params `searchText`, `outputFieldNames`, `resultCount`) | Data Cloud retriever (RAG over a search index). See `data-cloud.md` |
| `{!$RecordSnapshot:Ticket.Snapshot}` | `invocable://getDataForGrounding` (param `recordId`) | A whole-record or file snapshot. With `SOBJECT://ContentDocument` input this passes an image to a multimodal model (`salesforce/AI-Ticket-Analyzer`) |

**Retriever wiring is a UI step.** Build and activate the search index and retriever first, then
insert the retriever in Prompt Builder. There's no supported deploy path that builds the index
(sf-skills education grounding notes). Verify afterwards with
`GET /services/data/vXX.X/einstein/prompt-templates/<devName>`.

### Apex grounding (a `CapabilityType` invocable)

```apex
public with sharing class PersonalizedGuestExperiences {
    @InvocableMethod(label='Get sessions of interest for today'
                     description='Today\'s sessions that match the guest\'s interests'
                     CapabilityType='FlexTemplate://Generate_Personalized_Schedule') // ties to the template
    public static List<Response> getSessions(List<Request> requests) {
        Id contactId = [SELECT Id FROM Contact WHERE Email = :requests[0].email WITH USER_MODE LIMIT 1].Id;
        Response res = new Response();
        res.Prompt = formatForPrompt(ExperienceController.getExperiences(contactId)); // text, not raw SObject JSON
        return new List<Response>{ res };
    }
    public class Request {                     // names and types == template inputs
        @InvocableVariable(required=true description='The guest\'s email address')
        public String email;
    }
    public class Response {
        @InvocableVariable public String Prompt;   // must be called Prompt
    }
}
```

`CapabilityType` values: `FlexTemplate://<TemplateApiName>` for Flex, and
`PromptTemplateType://einstein_gpt__recordSummary` or `PromptTemplateType://einstein_gpt__fieldCompletion`
for those types. For Record Summary the request field is `objectToSummarize` (typed as the
SObject). For Flex, request field names and types must match the template's inputs. The output
field is always `Prompt` (String). The official recipe omits `CapabilityType` and still
grounds through `apex://`. Both forms appear in the wild (verify in your org).

### Flow grounding (template-triggered prompt flow)

```xml
<processType>PromptFlow</processType>
<start>
    <capabilityTypes>
        <name>PromptTemplateType://einstein_gpt__recordSummary</name>
        <capabilityName>PromptTemplateType://einstein_gpt__recordSummary</capabilityName>
        <inputs>
            <name>objectToSummarize</name>
            <capabilityInputName>objectToSummarize</capabilityInputName>
            <dataType>SOBJECT://Case</dataType>
            <isCollection>false</isCollection>
        </inputs>
    </capabilityTypes>
    <triggerType>Capability</triggerType>
</start>
<assignments>
    <name>Dynamic_Prompt_Grounding</name>
    <elementSubtype>AddPromptInstructions</elementSubtype>   <!-- "Add Prompt Instructions" -->
    <assignmentItems>
        <assignToReference>$Output.Prompt</assignToReference>
        <operator>Add</operator>
        <value><stringValue>&lt;invoicedata&gt;{!BeautifyInvoiceData}&lt;/invoicedata&gt;</stringValue></value>
    </assignmentItems>
</assignments>
```

Flow Builder's Debug button doesn't work for template-triggered prompt flows. Debug by previewing
the template and reading the resolved prompt (community skill notes, citing the GenAI guide;
verify in your org). For flow providers, all flow inputs are required. Changing a flow's inputs
after the template is built breaks the template.

## 4. Calling a template

| From | How | Output |
|---|---|---|
| Agent Script | `target: "generatePromptResponse://<DevName>"` (alias `prompt://`) | `promptResponse` |
| Flow | `<actionType>generatePromptResponse</actionType>`, `<actionName>DevName</actionName>`, inputs named `Input:<apiName>` | `{!Action.promptResponse}` |
| Apex | `ConnectApi.EinsteinLLM.generateMessagesForPromptTemplate(devNameOrId, input)` | `generations[0].text` |
| REST | `POST /services/data/vXX.X/einstein/prompt-templates/<DevName>/generations` | `generations[0].text`, plus the resolved `prompt` |
| Record page | Field Generation template on a Dynamic Forms field | Suggested value |
| LWC | `@AuraEnabled` Apex wrapping the Apex call | — |

### From an agent

```agentscript
subagent scheduling:
    description: "Scheduling requests"
    actions:
       generate_personalized_schedule:
          description: "Generate a personalized schedule for the guest from their Salesforce interests. Requires the guest's email address only. Do not ask about preferences."
          inputs:
             "Input:email": string            # quoted, "Input:" + template input apiName
                description: "Guest's email address"
                is_required: True
          outputs:
             promptResponse: string           # always this single string output
                description: "The generated schedule"
                is_used_by_planner: True
                is_displayable: True          # surface the text directly to the user
          target: "generatePromptResponse://Generate_Personalized_Schedule"

    reasoning:
       actions:
          generate_personalized_schedule: @actions.generate_personalized_schedule
             with "Input:email" = ...
```

Gotchas (ADLC `action-prompt-templates.md`, recipe):
- Input names **must be quoted**: `"Input:email"`. The part after `Input:` must exactly match the
  template input `apiName`.
- An empty `promptResponse` usually means an inactive template or an unbound required input.
- `run @actions.<prompt>` chaining may not map `"Input:X"` parameters. Call the template as a
  primary tool action instead (ADLC known limitation).
- Put the grounding inside the template and pass only the lookup key. The agent shouldn't ask
  for data the template can fetch (recipe instruction: "Never ask about goals, preferences, or
  interests").
- Asset Library actions may ship with unquoted `Input:` names. Quote them yourself (ADLC issue #7).

### From Apex

```apex
public static String runTemplate(String devName, Id accountId) {
    Map<String, ConnectApi.WrappedValue> params = new Map<String, ConnectApi.WrappedValue>();
    ConnectApi.WrappedValue acct = new ConnectApi.WrappedValue();
    acct.value = new Map<String, String>{ 'id' => accountId };     // SObject input = map with id
    params.put('Input:Account', acct);
    // Text input: w.value = 'some string';

    ConnectApi.EinsteinPromptTemplateGenerationsInput input = new ConnectApi.EinsteinPromptTemplateGenerationsInput();
    input.isPreview = false;                          // true = resolve only; some providers won't resolve in preview
    input.inputParams = params;
    input.additionalConfig = new ConnectApi.EinsteinLlmAdditionalConfigInput();
    input.additionalConfig.applicationName = 'PromptTemplateGenerationsInvocable';
    input.additionalConfig.numGenerations = 1;
    // input.additionalConfig.enablePiiMasking = true;   // seen in Salesforce samples

    ConnectApi.EinsteinPromptTemplateGenerationsRepresentation out =
        ConnectApi.EinsteinLLM.generateMessagesForPromptTemplate(devName, input);
    if (out.generations == null || out.generations.isEmpty()) {
        throw new AuraHandledException('No generation: ' + JSON.serialize(out));
    }
    return out.generations[0].text;
}
```

`ConnectApi.EinsteinLLM.getPromptTemplates(query, sortBy, offset, pageLimit, fields, type,
relatedEntity, isActive)` lists templates. Pass the developer name, not a hard-coded record ID.
IDs differ between orgs.

### REST smoke test

```bash
sf api request rest "/services/data/v64.0/einstein/prompt-templates/My_Template/generations" \
  --method POST --target-org "$ORG" \
  --body '{"isPreview":false,"inputParams":{"valueMap":{"Input:Opportunity":{"value":{"id":"006…"}}}},
           "additionalConfig":{"numGenerations":1,"temperature":0,"applicationName":"PromptTemplateGenerationsInvocable"}}'
```

`.generations[0].text` is the output and `.prompt` is the fully resolved prompt, which is the
fastest way to debug grounding. `isPreview:true` can't resolve some flow data providers, so test
with `false` (sf-skills).

## 5. Versions, activation, models

- Edit in a **Draft** version → Save & Preview against **real records** (including sparse ones)
  → **Publish** → **Activate**. Exactly one version is active. Callers always hit the active one.
- Activating without previewing is the main cause of blank or wrong production output. A merge
  field that looks right can resolve to nothing on real data.
- Pin the model per template (`primaryModel`). A change of model is a behaviour change: re-run
  your examples before activating. Bring-your-own models come from Einstein Studio (Model
  Builder) and show up as selectable models.
- Permission sets: authors need Prompt Template Manager, and runtime users (including the agent
  user) need Prompt Template User (verify names in your org).
- Deploy and CI: template metadata deploys fine, but retriever wiring and index builds don't.
  Script the version round-trip (§2) rather than hand-editing identifiers.

## 6. Writing prompts for Prompt Builder

- **Data first, delimited; instructions after.** Wrap each grounding block in tags
  (`<DataSources>…</DataSources>`, `<invoicedata>`), as Salesforce's own Pipeline Management
  templates do.
- **Say what to do when data is missing.** "If the call transcript, email and note are blank,
  return empty values." Merge fields often resolve to empty.
- **Ground from code as compact text.** Have Apex or Flow return only the fields the prompt needs,
  pre-formatted. `JSON.serialize(records)` dumps every field, costs tokens and can expose PII.
- **Structured output:** set `responseFormat` to `JSON`, show the exact schema, state "raw JSON
  only, no code fences", and add length caps per field. Parse defensively in the caller.
  Salesforce's own field-completion template still repeats "no markdown, no ```json".
- **Rich text output** (for rich-text fields or displays): give the exact HTML skeleton (pronto
  review summary).
- **Pass only identity.** Merge only the fields the output needs. The Trust Layer masks PII
  in-flight, but merged data still reaches the model's context.
- **Make blank and invalid outputs testable.** Keep a small set of real record IDs and expected
  properties, run them through the REST endpoint on every change, and diff the results
  (`agent-evals`).

## Anti-patterns

- A Flex template for a field-population use case.
- Hand-written `versionIdentifier` values or `activeVersion` numbers.
- Dumping full SObject JSON into the prompt.
- Agent instructions that ask the user for data the template's grounding already fetches.
- Hard-coded template record IDs in Apex (`'0hfHn…'`).
- Unquoted `Input:x` in Agent Script.
- Testing only in Preview mode when the template uses flow grounding.
- Several templates that differ only by tone. Use one template with a `tone` text input instead.

## Sources

- `trailheadapps/agent-script-recipes`: `02_actionConfiguration/promptTemplateActions` (template XML,
  `PersonalizedGuestExperiences.cls`, `.agent`, README).
- `forcedotcom/sf-skills`: `platform-metadata-api-context-get/assets/metadata_api/GenAiPromptTemplate.json`
  (field reference, type values, version deprecations);
  `sales-agentforce-pipeline-management-configure/assets/field-completion-template.genAiPromptTemplate-meta.xml`
  and `references/field-completion-prompts.md` (Field Generation naming, version round-trip,
  `/generations` endpoint, preview limitation);
  `education-cloud-student-recruitment-agent-configure/references/grounding.md` (retriever merge field,
  data provider, no-deploy path).
- `trailheadapps/pronto`: `genAiPromptTemplates/Extract_Menu`, `Customer_Review_Summary`, `Customer_Review_Summary_Flex`
  (`$RelatedList`, `responseFormat` JSON, Field Generation binding).
- `salesforce/AI-Ticket-Analyzer`: `read_handwriting.genAiPromptTemplate-meta.xml` (`$RecordSnapshot`, image input).
- `SalesforceAIResearch/agentforce-adlc`: `references/action-prompt-templates.md`, `known-issues.md` #7.
- `forcedotcom/apex-language-support`: `StandardApexLibrary/ConnectApi/EinsteinLLM.cls` (method signatures).
- `albarivas/df24-ai-code` (`LLMService.cls`, `AccountPromptController.cls`), `salesforce-misc/DataCloudAndAgentForceForRetail`
  (`PromptUtils.cls`, `enablePiiMasking`): Apex invocation.
- `ShreyasD/TDX24-EinsteinPromptTemplateGenerations-Demo`: `PropertiesInterestedLeadPromptExt.cls`
  (`FlexTemplate://` capability), `Case_Comment_And_Feed_Summary.flow-meta.xml` (PromptFlow `capabilityTypes`).
- `SFDC-Assets/gen-ai-demo`: `Case_Troubleshooter_Grounding.flow-meta.xml` (`AddPromptInstructions`, `$Output.Prompt`).
- `Therciosb/Agentforce-Skills`: `PersonalizedSchedule.flow-meta.xml` (Flow `generatePromptResponse` action).
- `PranavNagrecha/AwesomeSalesforceSkills`: `prompt-builder-templates/references/llm-anti-patterns.md`
  (community, used for the type and surface mapping and the prompt-flow Debug limitation).
