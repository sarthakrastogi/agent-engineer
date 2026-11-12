# Connections and channels

An activated agent reaches nobody until a channel points at it. The script declares the
**surfaces** it supports (`connection <type>:` blocks), and how it should sound on them. The
org config (Embedded Service, Messaging channel, Omni-Channel flow, Slack, telephony, External
Client App) wires real users to it. You need both halves. Setup steps marked "(verify in your
org)" come from secondary sources or older docs; UI paths move between releases.

## Pick the agent type first

The channel decides the agent type, and the agent type decides what the script may contain.

| | `AgentforceServiceAgent` | `AgentforceEmployeeAgent` |
|---|---|---|
| Audience | Customers, guests, unauthenticated users | Logged-in Salesforce users |
| Runs as | `access.default_agent_user` (Einstein Agent User licence) | The logged-in user, with their sharing |
| Channels | Enhanced Chat / Messaging for In-App and Web, enhanced messaging (WhatsApp, SMS, Messenger, Apple), voice, service email, Agent API (client credentials) | Lightning Experience panel, Salesforce mobile, Slack, Agent API (named-user token) |
| `connection` blocks | `messaging`, `customer_web_client`, `telephony`, `service_email` | `slack` only |
| `@utils.escalate` | Yes, via `connection messaging` + Omni-Channel | No: transition to a help subagent or create a Case |
| Linked `@MessagingSession.*` vars | Yes | No |
| Access | Channel deployment + agent user perms | Permission set with `<agentAccesses>` |

Hard rules from the compiler and field reports:

- Setting `default_agent_user` on an employee agent makes publish/preview fail with an
  unhelpful "unknown error" / "Internal Error". Remove it.
- A `connection slack:` on a non-employee agent compiles with a warning, "Slack connection is
  only supported for Employee agent types".
- Don't add `connection messaging`, MessagingSession linked vars or `@utils.escalate` to an
  employee agent; they fail silently or at publish.

## The `connection` block

The keyword is singular, one top-level block per surface, placed after `system`. A plural
`connections:` wrapper is invalid. Each block compiles to a `plannerSurfaces` entry in the
generated `GenAiPlannerBundle`.

### Surface types

The compiler maps these names; any other name passes through as a custom surface.

| Block | Compiles to | Use for | Allowed fields |
|---|---|---|---|
| `connection messaging:` | `Messaging` | Enhanced Chat, In-App/Web, enhanced messaging channels; **the** human-escalation route | All. Route name + type must be paired; `inputs` warned as unused |
| `connection customer_web_client:` | `CustomerWebClient` | Enhanced Chat v2 client, Agent Builder Preview, Agent API testing, browser voice | All; `inputs` warned as unused |
| `connection telephony:` | `Telephony` | Agentforce Voice over Service Cloud Voice (phone) | All; response formats need no `inputs` |
| `connection service_email:` | `service_email` | Email channel | No `escalation_message`; route name + type paired; at least one field |
| `connection slack:` | `slack` | Employee agent in Slack | **Only** `adaptive_response_allowed` and `additional_system_instructions` |

`customerwebclient` without underscores doesn't exist (publish returns `ERROR_HTTP_404`). There
is no `connection voice:` for authoring: voice is `telephony` and/or `customer_web_client` plus
a `modality voice:` block. The compiler does map the name `voice`, but no documented
deployment uses it.

### Fields (ConnectionBlock schema)

| Field | Type | Meaning |
|---|---|---|
| `label`, `description` | string | Display and purpose. A messaging block needs at least one field, so `description` is the cheapest |
| `source` | string | Surface reference, e.g. `"connection://SurfaceAction__Messaging"`; usually omitted |
| `escalation_message` | template string | Said at handoff. Supports `{!...}` interpolation |
| `outbound_route_type` | string | `"OmniChannelFlow"`, the only validated value. `"queue"`, `"skill"`, `"agent"` fail validation |
| `outbound_route_name` | string | Omni-Channel flow API name; `flow://Name` and `Name` both work (the compiler strips the scheme). Must exist in the org |
| `adaptive_response_allowed` | boolean | Lets the planner pick rich response formats for this surface. When `False`, the compiler drops the block's `reasoning`/`response_actions` as inert |
| `additional_system_instructions` | template string | Appended to global `system.instructions` on this surface only |
| `inputs` | `string`/`number`/`boolean` only, `description` only | Surface parameters set by the connection owner, referenced as `{!@inputs.x}`. No lists |
| `reasoning.instructions` | template | Surface-level guidance on when to use which format |
| `reasoning.response_actions` | map | `name: @response_formats.<format>`; nothing else is accepted |
| `response_formats` | map | Rich formats: `target: "system://MessagingChoices"` etc., or `apex://`, `flow://`, `prompt://`, `standardInvocableAction://`, `generatePromptResponse://` |

Known `system://` formats: `MessagingRichLink`, `MessagingChoices`,
`MessagingChoicesWithImages`, `MessagingTimePicker`, `ESTypeMessage`, `MessagingAudio`,
`ClientAction`. Any other `system://` name gets a warning that it may not resolve.

### Minimal and full forms

```agentscript
# Service agent on web chat that can hand off to a human
connection messaging:
   description: "Routes escalated conversations to a human support queue"
   escalation_message: "I am connecting you with a human agent who can help you further."
   outbound_route_type: "OmniChannelFlow"
   outbound_route_name: "AgentSupportFlow"
   adaptive_response_allowed: True

# Needed for Builder Preview / Agent API / browser voice on service agents
connection customer_web_client:
   adaptive_response_allowed: True
```

```agentscript
# Channel-specific behaviour without forking the agent
connection service_email:
   label: "Email Connection"
   description: "Connection for email channels"
   outbound_route_type: "OmniChannelFlow"
   outbound_route_name: "flow://Route"
   inputs:
      legal_disclosure: string = "this is a disclosure"
         description: "Legal disclosure message"
   additional_system_instructions: |
      Use recipient name if provided
   reasoning:
      instructions: |
         Always append {!@inputs.legal_disclosure}
         Use {!@response_actions.choices} for multiple choice responses
      response_actions:
         choices: @response_formats.email_choices
   response_formats:
      email_choices:
         description: "Multiple choice format"
         source: "response_format://SurfaceAction__EmailTextChoices"
```

Rich-format rule of thumb, taken from the compiler's messaging fixture:
- Use `MessagingChoices` for 2–10 options that the user picks from.
- Use plain text for binary Yes/No.
- Use `MessagingRichLink` for one item that has both a URL and an image.
- Use `MessagingTimePicker` only when times are full ISO 8601 with an offset.

Write these conditions in `reasoning.instructions`; the planner won't infer them.

## Channel context → linked variables

Linked variables are read-only, typed (`string`/`number`/`boolean`/`date`/`id`; no list,
object or default), and filled by the platform at session start. They compile to
`context_variables` with a `field_mapping`.

| Variable (exact name) | `source:` | Notes |
|---|---|---|
| `EndUserId` | `@MessagingSession.MessagingEndUserId` | Runtime reads it by name; linter warns if you delete it |
| `RoutableId` | `@MessagingSession.Id` | The MessagingSession record; pass to actions that update it |
| `ContactId` | `@MessagingEndUser.ContactId` | Empty for anonymous users. Never treat it as proof of identity |
| `EndUserLanguage` | `@MessagingSession.EndUserLanguage` | Drives the reply language |
| `ChannelType` | `@MessagingSession.ChannelType` | Branch on channel |
| `VoiceCallId` | `@VoiceCall.Id` | Required whenever `modality voice:` is present |
| any | `@MessagingSession.<Custom__c>` | Custom MessagingSession fields work (`field_mapping: MessagingSession.X__c`) |

Allowed namespaces are `MessagingSession`, `MessagingEndUser` and `VoiceCall`, and they are
case-sensitive. `@messagingSession.userID` in the `future_recipes` folder is not valid today.

**Pass web-page context into the agent:**
1. On the page, set hidden pre-chat fields in `onEmbeddedMessagingReady`.
2. In Setup, map each hidden field to a Messaging Channel custom parameter, then to an
   Omni-Channel flow input variable. (verify in your org)
3. In the flow, write each value to a custom `MessagingSession` field before routing to the
   agent. (verify in your org)
4. In the script, declare `linked` variables sourced from those fields. Salesforce's own
   Commerce shopper agent works this way.

```js
window.addEventListener('onEmbeddedMessagingReady', () => {
  embeddedservice_bootstrap.prechatAPI.setHiddenPrechatFields({
    Currency: 'NZD', DomainUrl: location.origin, UsId: shopperId   // values must be strings
  });
});
```

```agentscript
variables:
   Currency: linked string
      source: @MessagingSession.Currency__c
      description: "Shopper's preferred currency, from the web page"
   UsId: linked string
      source: @MessagingSession.UsId__c
      description: "Shopper session id, from the web page"
```

`sf agent preview` can't inject MessagingSession context, so linked vars are empty in
preview. Guard every branch for the empty case. Recent `@salesforce/agents` versions pass a
`variables` array on preview session start; check `sf agent preview start --help` for a
`--context-variables` flag before relying on it.

## Surfaces

### Enhanced Chat / Messaging for In-App and Web (service agents)

Setup chain (verify in your org):
1. Enable Omni-Channel and Messaging.
2. Create the Messaging Channel (type Messaging for In-App and Web).
3. Create an **Omni-Channel flow** that routes inbound work to the agent: a Route Work action
   with the Agentforce Service Agent as the target.
4. Create an Embedded Service Deployment (Messaging for In-App and Web), pick the channel, and
   publish.
5. Add every embedding origin to the deployment's CORS allowlist and to CSP Trusted Sites.
6. Paste the snippet, or drop the Embedded Messaging component into Experience Builder.

The snippet is real code from a production deployment, with IDs changed:

```html
<script>
function initEmbeddedMessaging() {
  try {
    embeddedservice_bootstrap.settings.language = 'en_US';
    embeddedservice_bootstrap.init(
      '00Dxx0000000000',                 // org id
      'Web_Messaging_Deployment',        // deployment API name
      'https://acme.my.site.com/ESWWebMessagingDeployme1716235390398',
      { scrt2URL: 'https://acme.my.salesforce-scrt.com' }
    );
  } catch (err) { console.error('Error loading Embedded Messaging: ', err); }
}
</script>
<script src="https://acme.my.site.com/ESWWebMessagingDeployme1716235390398/assets/js/bootstrap.min.js"
        onload="initEmbeddedMessaging()"></script>
```

- **Visible pre-chat:**
  `prechatAPI.setVisiblePrechatFields({"_email": {"value": v, "isEditableByEndUser": false}})`.
  `_firstName`, `_lastName` and `_email` are the standard keys.
- **Hidden pre-chat:** `prechatAPI.setHiddenPrechatFields({...})`. Each key must exist as a
  hidden pre-chat field on the deployment.
- **Verified users:** the `userVerificationAPI` (JWT-based) gives a real identity. Without it,
  every chat is anonymous and `ContactId` is only a match on what the user typed. Gate
  account-changing actions on verification (`agent-guardrails`).
- **Lifecycle events:** `onEmbeddedMessagingReady`, `onEmbeddedMessagingConversationStarted`
  (carries `conversationId`), `onEmbeddedMessagingWindowMaximized`.
- **Gotchas:**
  - A missing CORS origin fails silently in Salesforce; you only see it in the browser console.
  - The widget can take minutes to propagate after you publish. (verify in your org)
  - Test on the real surface: Builder Preview never exercises the channel, the Omni flow or the
    linked vars.

### Enhanced messaging channels: WhatsApp, SMS, Facebook Messenger, Apple Messages for Business

These use the same `connection messaging:` surface and the same Omni-Channel flow → agent
routing; only the channel record differs (verify in your org). Branch on `ChannelType` for:
- SMS: short and plain, no rich formats or links.
- WhatsApp: 24-hour session windows and template messages for outbound. (verify in your org)
- Apple and Messenger: support some rich formats; test each `system://` format per channel.

### Lightning Experience panel and Salesforce mobile (employee agents)

- Users need `CopilotSalesforceUser`, plus a permission set with an `<agentAccesses>` entry.
  `agentName` must equal `developer_name` exactly.
- Symptoms:

| Symptom | Fix |
|---|---|
| No Agentforce icon | Assign `CopilotSalesforceUser` |
| Icon, but the agent isn't listed | Add `<agentAccesses>` |
| Agent listed but errors | Agent not active: `sf agent activate` |

- The agent runs as the user, so actions see only that user's data and sharing. That's the
  point; don't elevate in Apex.
- Mobile uses the same agent and access model. (verify in your org)

```xml
<PermissionSet xmlns="http://soap.sforce.com/2006/04/metadata">
    <agentAccesses><agentName>Case_Assist</agentName><enabled>true</enabled></agentAccesses>
    <hasActivationRequired>false</hasActivationRequired>
    <label>Case Assist Agent Access</label>
</PermissionSet>
```

### Slack (employee agents)

- Script side: `connection slack:` with only `adaptive_response_allowed` and/or
  `additional_system_instructions` (e.g. "Format with Slack mrkdwn; no tables"). Anything else
  is a compile error.
- Org side: connect the Salesforce org to the Slack workspace and install the
  Salesforce-provided app. A Slack admin must approve it. Then assign the agent and set where
  it's available (DM / channel mention). (verify in your org)
- Users map to Salesforce users, so sharing still applies. An unmapped Slack user has no data
  access. (verify in your org)

### Experience Cloud sites

- For guests or customers, use a **service agent** through the Embedded Messaging component
  in Experience Builder, on the same Embedded Service Deployment. Republish the site after
  you add it.
- Authenticated community users still go through the agent user. Pass identity through user
  verification, not pre-chat fields the user can edit.

### Voice: Agentforce Voice

A voice agent needs four pieces:
- a top-level `language:` block;
- a voice surface: `connection telephony:` for phone (Service Cloud Voice), and/or
  `connection customer_web_client:` for browser voice and Preview;
- a `modality voice:` block;
- a `VoiceCallId` linked variable.

Add `connection messaging:` only if the agent escalates. This needs a voice-provisioned org;
Developer Edition and Trailhead Playgrounds can't. Phone number / SIP / Amazon Connect or
partner-telephony attachment is done in the UI only.

```agentscript
language:
   default_locale: "en_US"

connection telephony:
   adaptive_response_allowed: True

modality voice:
   language:
      default_locale: "en_US"          # REQUIRED, or persona_id is silently ignored
   outbound:
      persona_id: "74752e92d40e"       # tied to one (model, locale) in the voice catalog
      model:
         id: "eleven_flash_v2"         # eleven_v3 default; eleven_flash_v2_5 non-English; kotoba ja
   inbound:
      filler_words_detection: True
```

**Modality fields:**

| Field(s) | Notes |
|---|---|
| `inbound.model`, `inbound.keywords`, `inbound.filler_words_detection` | Speech-to-text side |
| `outbound.model`, `outbound.persona_id`, `outbound.filler_sentences` (`waiting:` list), `outbound.pronunciations` (`grapheme`, `phoneme`, `type: IPA\|CMU`) | Text-to-speech side |
| `session_language_switching` | `Monolingual` (default) or `Multilingual` |
| `language_settings` | Per-locale overrides. Voice locales must be a subset of the text `language` block (`voice-language-not-declared`) |
| `additional_configs.speak_up_config` | 10 000–300 000 ms, plus a message |
| `additional_configs.endpointing_config.max_wait_time_ms` | 500–60 000 |
| `additional_configs.beepboop_config` | — |
| Flat legacy form: `voice_id`, `outbound_speed` (0.5–2), `outbound_style_exaggeration` (0–1), deprecated `outbound_stability` / `outbound_similarity` | Still accepted but can't pick a model. Don't mix it with the nested form (lint rule `voice-version-mixing`) |

**Voice authoring rules:**
- Branch on `@system_variables.current_modality` (`"voice"` / `"text"` / None) instead of
  forking agents.
- Keep turns to 1–2 sentences, with no markdown, URLs or lists over three items.
- Speak numbers, prices and IDs in words.
- Read back critical values before acting.
- Say a filler line before any action over about 800 ms.
- Expect speech-to-text errors on domain terms: add `inbound.keywords` and a "quietly repair
  mishears" instruction.
- Move writes off the live turn (Queueable or Platform Event). Every second over about 2 s
  feels broken.
- Keep the routing tree flat; each subagent hop costs a planner turn.
- Action descriptions get narrated, so write them in plain English, with no `__c` names or
  SOQL.

### Service email

`connection service_email:` routes inbound email cases to the agent and back through an
Omni-Channel flow. There's no `escalation_message` (it's a compile error); escalation is the
route itself. Use `inputs` for owner-set values such as disclosures. (verify setup in your
org)

## Headless: Agent API

Use it for your own web or mobile UI, IVRs, backend automation, A2A wrappers and eval harnesses.
- Base URL: `https://api.salesforce.com/einstein/ai-agent/v1`. This is global, **not** your
  My Domain. My Domain goes in the body.
- Only **active** agents.

**Auth setup** (verify in your org):
1. Create an External Client App (or Connected App) with OAuth scopes `api`,
   `refresh_token offline_access`, `chatbot_api` and `sfap_api`. `sfap_api` is the one people
   forget.
2. Enable Client Credentials Flow, with a Run-As user.
3. Issue JWT-based access tokens.
4. Add the app to the agent's API connection in Agentforce Builder / Setup.
5. Get the agent ID (`0Xx…`): `sf data query -q "SELECT Id FROM BotDefinition WHERE
   DeveloperName='My_Agent'"`.

```bash
# 1. Token: client credentials, against MY DOMAIN
curl -s -X POST "https://$MY_DOMAIN/services/oauth2/token" \
  -d grant_type=client_credentials -d client_id="$CLIENT_ID" -d client_secret="$CLIENT_SECRET"
# → access_token, api_instance_url

# 2. Start session
curl -s -X POST "https://api.salesforce.com/einstein/ai-agent/v1/agents/$AGENT_ID/sessions" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -d '{
    "externalSessionKey": "'"$(uuidgen)"'",
    "instanceConfig": { "endpoint": "https://'"$MY_DOMAIN"'" },
    "streamingCapabilities": { "chunkTypes": ["Text"] },
    "bypassUser": true,
    "variables": [
      { "name": "$Context.EndUserLanguage", "type": "Text", "value": "en_US" },
      { "name": "customer_tier", "type": "Text", "value": "premium" }
    ]}'
# → { "sessionId": "...", "messages": [ { "type": "Inform", "message": "<welcome>" } ] }

# 3a. Sync message
curl -s -X POST "https://api.salesforce.com/einstein/ai-agent/v1/sessions/$SID/messages" \
  -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" -H "Accept: application/json" \
  -d '{ "message": { "sequenceId": 1, "type": "Text", "text": "Where is my order?" }, "variables": [] }'

# 3b. Streaming (SSE): same body, Accept: text/event-stream
#     POST .../sessions/$SID/messages/stream

# 4. Feedback on a turn (feedbackId comes from the Inform message)
#     POST .../sessions/$SID/feedback  {"feedbackId":"...","feedback":"GOOD","text":"optional"}

# 5. End: always, or sessions leak against concurrency limits
curl -s -X DELETE "https://api.salesforce.com/einstein/ai-agent/v1/sessions/$SID" \
  -H "Authorization: Bearer $TOKEN" -H "x-session-end-reason: UserRequest"
```

**Response message types:**

| Type | Meaning |
|---|---|
| `Inform` | A reply. Carries `message`, `feedbackId`, `planId` (pass it to the trace API), `isContentSafe`, `citedReferences`, `result` |
| `Confirm` | The agent wants confirmation before a write |
| `Escalation` | A handoff was requested. Your app must route to a human; there's no Omni-Channel here |
| `Failure` | An action failed: check the Flow/Apex |
| `SessionEnded` | The session is over |

Streaming SSE events: `INFORM`, `TEXT_CHUNK`, `PROGRESS_INDICATOR`, `END_OF_TURN`, `ERROR`.
Concatenate the chunks; treat `Inform` as the final text.

**Agent API rules:**
- **`bypassUser`**:
  - Service agents: `true`, so the run is as the agent user.
  - Employee agents: `false`, with a **named-user** token. They reject `bypassUser: true` with
    "400 Invalid user ID".
  - Client credentials therefore suits service agents; employee agents need a per-user OAuth
    flow.
- **`sequenceId`**: strictly increasing per session. Salesforce's own clients use
  `Date.now()`.
- **`variables`**:
  - Context fields use `$Context.<Field>`. Drop the `__c` from custom fields, e.g.
    `$Context.Conversation_Key`.
  - Custom agent variables use their bare name.
  - Types are `Text`, `Number`, `Boolean`, … .
  - Only variables marked as settable by API are accepted. Context variables are fixed after
    session start, except `EndUserLanguage`. (verify in your org)
- **Rate limits** (around 10 concurrent sessions, about 15-minute idle timeout in some orgs)
  aren't officially documented here. Measure in your org, and always DELETE sessions.
- **Markdown**: Salesforce's CLI sends `x-attributed-client: no-builder` to suppress markdown
  in replies. Use it when your UI renders plain text. (undocumented header)
- **Don't put an `@RestResource` Apex proxy in front of the agent.** It skips the session
  model and the Trust Layer.

## Human handoff

1. **Where:** `connection messaging` declares the Omni-Channel flow; `@utils.escalate` takes
   no target. `escalate` is a reserved name.
2. **When:** gate it with state, not model judgement:
   `available when @variables.user_requested_human == True or @variables.failed_attempts > 1`.
   You can also gate on business hours.
3. **Context:** before escalating, set an `issue_summary` variable and have the agent restate
   it, so it's in the transcript the human inherits.
   - For skills-based routing, write the summary and category to `MessagingSession` fields
     through an action first, so the Omni flow can read them. (verify in your org)
4. **Prerequisites:** Omni-Channel enabled, the routing flow deployed, the messaging channel
   active, a queue with staffed reps. Preview shows the `escalation_message` but can't
   complete the route.
5. **Elsewhere:** on the Agent API, handle `Escalation` yourself. On employee agents, create a
   Case or transition to a help subagent.

## Choosing a channel

| Need | Use |
|---|---|
| Customer chat on your website or app | Service agent + `connection messaging` + Enhanced Chat (Embedded Service) |
| Customers on WhatsApp/SMS/Messenger/Apple | Same, with an enhanced messaging channel; branch on `ChannelType` |
| Phone line | Service agent + `connection telephony` + `modality voice` + Service Cloud Voice |
| Internal users inside Salesforce | Employee agent + `<agentAccesses>` permission set |
| Internal users in Slack | Employee agent + `connection slack` |
| Customer portal | Service agent through the Embedded Messaging component on Experience Cloud |
| Your own UI, backend jobs, IVR, other agent frameworks, eval harness | Agent API |
| One agent, many surfaces | One script with several `connection` blocks and per-surface `additional_system_instructions`; test each surface separately |

## Anti-patterns

- Treating Builder Preview as channel testing. It skips the channel, the Omni flow, the linked
  vars and user verification.
- Trusting `ContactId` or pre-chat fields as identity on an unauthenticated chat.
- Inventing `connection voice:`, `connections:`, `customerwebclient`, or
  `outbound_route_type: "queue"`.
- Escalation offered on every turn, with no gate and no summary.
- Service-agent constructs on an employee agent, or `default_agent_user` on one.
- The same long, markdown-heavy replies on SMS, voice and web.
- Agent API sessions that are never DELETEd; reusing a `sequenceId`.
- Client-credentials tokens used to impersonate employees through an employee agent.
- Deleting a "unused" `EndUserId` / `RoutableId` / `ContactId` / `EndUserLanguage`; the
  runtime reads them by name.

## Sources

- `salesforce/agentscript`:
  - `dialect/agentforce/src/schema.ts`: ConnectionBlock, linked `source` namespaces,
    globalScopes.
  - `dialect/agentforce/src/voice-schema.ts`.
  - Lint passes: `connection-validation.ts`, `variable-validation.ts`,
    `voice-language-validation.ts`, `index.ts` (required platform variables).
  - `packages/compiler/src/surfaces/compile-surfaces.ts`.
  - Tests: `packages/compiler/test/context-variables.test.ts`,
    `test/fixtures/scripts/edge_conn_customizable.agent`, `byon_node_accesstoken.agent`.
  - Docs: `apps/docs/docs/dialects/agentforce/ascript-blocks.md`,
    `reference/ascript-ref-utils.md`, `reference/ascript-ref-variables.md`.
- `trailheadapps/agent-script-recipes`:
  - `04_architecturalPatterns/voiceAgent`, `04_architecturalPatterns/escalationPatterns`.
  - `force-app-service/` and `scripts/setup-service-agent.js` (`sf org create agent-user`).
  - `future_recipes/contextHandling`.
- `SalesforceAIResearch/agentforce-adlc`, `skills/agentforce-generate/references/`:
  `actions-reference.md`, `voice-modality-reference.md`, `voice-latency-heuristics.md`,
  `agent-access-guide.md`, `agent-script-core-language.md`, `known-issues.md` (Issues 17–18),
  `salesforce-cli-for-agents.md`, `org-setup-for-adl.md`.
- `forcedotcom/agents`, `src/agents/productionAgent.ts`: session body, `bypassUser` rule for
  employee agents, `variables`, `x-session-end-reason`, `x-attributed-client`.
- `pozil/agent-api-node-client` (Salesforce DevRel): token, sync/stream/feedback/close calls,
  SSE events.
- `misu007/agentforce-headless-agent-nextjs-demo`, `src/utils/agentforce.ts`: streaming
  endpoint, `api_instance_url`.
- `SalesforceCommerceCloud/pwa-kit`, `.../shopper-agent/index.jsx`: hidden pre-chat →
  MessagingSession fields, `userVerificationAPI`.
- `openstax/accounts`, `app/views/layouts/_chat.html.erb`: production Enhanced Chat snippet,
  visible/hidden pre-chat.
- Secondary, used only where marked "(verify in your org)":
  `PranavNagrecha/AwesomeSalesforceSkills` (agent-channel-deployment) and
  `amanpraaj/sf-skill-hub` (agent-api-reference.md) for setup steps, scopes, message types
  and rate limits.
- help.salesforce.com articles cited in the official docs: `service.miaw_intro_landing.htm`,
  `ai.service_agent_escalation.htm`, `service.voice_agentforce.htm`. developer.salesforce.com
  returned 403 to automated fetches.
