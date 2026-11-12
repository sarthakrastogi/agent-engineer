# Agentforce observability and trace SQL

How to see what a live Agentforce agent did, using SQL over the Session Tracing Data Model
(STDM) and the Einstein Audit & Feedback data in Data 360 (Data Cloud), plus the packaged
Observability apps. For vendor-neutral span design, sampling and redaction, see
`agent-observability`. To turn traces into a failure taxonomy and evals, see `agent-evals` →
`references/error-analysis.md`.

## Core rules

1. **Turn tracing on before launch.** Session tracing and Audit & Feedback are not
   backfilled. Analytics and insights cover only conversations that happen after setup.
2. **Builder and preview traces are for development; STDM is for production.** The live
   Builder/preview trace has about 13 step types with full prompts and variable state, but
   it isn't persisted. STDM has 5 step types, real users and volume, and lands minutes
   later.
3. **Discover before you query.** Check the data space name, that the DMOs exist, and the
   real enum values (`GROUP BY` first). They differ by org and release.
4. **Filter by time first, then by agent.** The agent name is on
   `AiAgentSessionParticipant` and `AiAgentMoment`, not on `AiAgentSession`.
5. **Treat `NOT_SET` as null.** Data 360 writes the string `NOT_SET` for absent values.
   Every error filter needs `IS NOT NULL AND != 'NOT_SET'`.
6. **Don't `JSON.parse` LLM-step I/O.** `LLM_STEP` input and output are Python-dict-style
   strings. Only `ACTION_STEP` input and output are JSON.
7. **Read rates with their denominators.** Deflection, escalation, abandonment and quality
   score each mislead on their own. A 100% error rate on one session is noise.
8. **Tracing costs Data 360 credits.** It averages about 24 records per LLM round trip. Size
   it before you turn it on for a high-volume service agent.
9. **Trace content is sensitive.** Prompts, responses and variable snapshots hold customer
   PII in clear text. Agent prompts aren't masked (see `trust-layer-and-guardrails.md`).
   Restrict who can query these DMOs.

## The four layers

| Layer | What it gives you | Freshness (approx.) | Where |
|---|---|---|---|
| Agentforce Session Tracing (STDM) | Every session, turn, message, step, LLM call ID, variable snapshot and error, as DMOs | ~5–30 min after the turn | Data 360 DMOs; SQL |
| Einstein Audit & Feedback | Full prompt, masked prompt, response, tokens, model, toxicity / PII / prompt-defense / instruction-adherence scores, thumbs up/down | Streams refresh about hourly | `GenAI*__dlm` DMOs; SQL, reports |
| Agent Analytics | Dashboards: sessions, deflection, escalation, abandonment, errors, latency, feedback, knowledge quality, instruction adherence | ~45–60 min | Analytics apps on Tableau Next, per agent type |
| Agent Optimization | Moments (one per user intent), intent clusters, 1–5 quality scores with written reasons | Scores daily; intent tags weekly | Agentforce Studio; `AiAgentMoment` / `AiAgentTag*` DMOs |
| Agent Health Monitoring (AHM) | Alerts on error rate, interaction latency, escalation rate, etc. | ~5 min | Agentforce Studio → alerts; `tableau/dataAlerts` REST |

The freshness figures come from third-party write-ups and live tests (verify in your org).
Coverage differs by agent type. Service and Employee agents get Analytics and Optimization.
Some packaged agents get only one of the two (verify in your org).

## Enable

1. Data 360 is provisioned, and you know the data space. Most orgs have one, `default`.
2. Setup → **Einstein Audit, Analytics, and Monitoring Setup**. Turn on:
   - **Agentforce Session Tracing** (the data model)
   - **Audit and Feedback**, for prompts, responses and Trust Layer scores
   - **Knowledge/RAG quality data**, if the agent grounds on a Data Library
   - **Agentforce Optimization**, for moments and quality scores

   Session tracing is off by default. Orgs with a single data space may get it
   automatically when Agentforce is turned on.
3. Install the analytics app for each agent type you run (Service / Employee Agent
   Analytics). These provision the semantic data models that AHM alerts need.
4. Grant the analysts:
   - Data Cloud User or Data Cloud Architect
   - Access Agentforce Optimization
   - A Tableau Next licence for the dashboards
   - **API Enabled** on the profile. Without it, metric cards fail silently.
5. **Activate** (publish) the agent and have a real conversation.
   - Draft-only preview sessions often create an `AiAgentSession` with no `TURN`
     interactions.
   - Optionally set `enable_enhanced_event_logs: True` in the `config:` block for richer
     conversation logging.
6. Confirm data has landed:

```sql
SELECT ssot__Id__c FROM "ssot__AiAgentSession__dlm" LIMIT 1
```

If the DMO is missing, check Data Cloud → Data Streams for the Agentforce Activity /
`AiAgent*` streams and refresh them.

## The data model

```text
AiAgentSession (1)                       one conversation
├── AiAgentSessionParticipant (N)        user + agent(s); agent API name, type, role
├── AiAgentInteraction (N)               TURN or SESSION_END; subagent (TopicApiName), trace id
│   ├── AiAgentInteractionMessage (N)    Input (user) / Output (agent) text
│   └── AiAgentInteractionStep (N)       TOPIC_STEP, LLM_STEP, ACTION_STEP, TRUST_GUARDRAILS_STEP,
│        │                               SESSION_END, (rare) INTERRUPT_STEP; linked by PrevStepId
│        ├── GenerationId      ──► GenAIGeneration__dlm ──► GenAIContentQuality ──► GenAIContentCategory
│        └── GenAiGatewayRequestId ─► GenAIGatewayRequest__dlm (prompt, tokens, model)
│                                     └── AiRetrieverQualityMetric (RAG scores)
├── AiAgentMoment (N)                    one per intent; LLM summaries; agent API name + version
│   ├── AiAgentMomentInteraction (N)     junction moment ↔ interaction
│   └── AiAgentTagAssociation (N)        quality score + reason ─► AiAgentTag (Value 1–5)
└──                                                                └► AiAgentTagDefinition
```

The table names are `ssot__<Name>__dlm`, with fields `ssot__<Field>__c`. The Audit DMOs
have no `ssot__` prefix and use camelCase fields (`GenAIGeneration__dlm.responseText__c`).
Use `AiAgent`, not `AIAgent`, in field names. Double-quoted identifiers are case-sensitive.

### Key fields

| DMO | Field | Use |
|---|---|---|
| Session | `ssot__Id__c`, `ssot__StartTimestamp__c`, `ssot__EndTimestamp__c` | Window, duration (end may be null) |
| Session | `ssot__AiAgentChannelType__c` | Channel. Seen: `Builder`, `SCRT2 - EmbeddedMessaging`, `LightningDesktopCopilot`, `Voice`, `PSTN`, `E & O` |
| Session | `ssot__AiAgentSessionEndType__c` | How it ended. Values differ by source (`USER_ENDED`/`AGENT_ENDED`, or `Completed`/`Escalated`/`Abandoned`/`Failed`, or `NOT_SET`), so run a `GROUP BY` first |
| Session | `ssot__VariableText__c`, `ssot__RelatedMessagingSessionId__c`, `ssot__RelatedVoiceCallId__c`, `ssot__PreviousSessionId__c` | Final variables; join to the channel record; agent-to-agent chains |
| Participant | `ssot__AiAgentApiName__c`, `ssot__AiAgentVersionApiName__c`, `ssot__AiAgentType__c`, `ssot__AiAgentSessionParticipantRole__c` (`USER`/`AGENT`), `ssot__ParticipantId__c` | Filter by agent. `ParticipantId` is the planner ID (`16j…`) or user (`005…`), stored as either 15 or 18 characters |
| Interaction | `ssot__AiAgentSessionId__c`, `ssot__AiAgentInteractionType__c`, `ssot__TopicApiName__c`, `ssot__TelemetryTraceId__c`, `ssot__PrevInteractionId__c` | One row per turn. `TopicApiName` holds the Agent Script **subagent** |
| Message | `ssot__AiAgentInteractionId__c`, `ssot__AiAgentInteractionMessageType__c` (`Input`/`Output`), `ssot__ContentText__c`, `ssot__MessageSentTimestamp__c` | Transcript |
| Step | `ssot__AiAgentInteractionId__c`, `ssot__AiAgentInteractionStepType__c`, `ssot__Name__c`, `ssot__InputValueText__c`, `ssot__OutputValueText__c`, `ssot__ErrorMessageText__c`, `ssot__PreStepVariableText__c`, `ssot__PostStepVariableText__c`, `ssot__GenerationId__c`, `ssot__GenAiGatewayRequestId__c`, `ssot__PrevStepId__c` | Debugging. Pre and post variables show what each step changed |
| Moment | `ssot__AiAgentSessionId__c`, `ssot__RequestSummaryText__c`, `ssot__ResponseSummaryText__c`, `ssot__AiAgentApiName__c`, `ssot__AiAgentVersionApiName__c` | Intents. Moments link to the **session**; use the junction for turns |
| TagAssociation | `ssot__AiAgentMomentId__c`, `ssot__AiAgentTagId__c`, `ssot__AssociationReasonText__c`, `ssot__IsPassed__c` | Quality score (join to Tag `ssot__Value__c`) and its LLM-written reason |
| AiRetrieverQualityMetric | `ssot__AiGatewayRequestId__c`, `ssot__RetrieverApiName__c`, `ssot__FaithfulnessRelevancyScoreNumber__c`, `ssot__AnswerRelevancyScoreNumber__c`, `ssot__ContextPrecisionScoreNumber__c` | RAG quality (0–1). Empty if the agent has no retrieval |

### Step-type quirks

- **`TRUST_GUARDRAILS_STEP`**
  - Named `InstructionAdherence`.
  - Its output is a dict string such as `{'name': 'InstructionAdherence', 'value': 'HIGH', 'explanation': ...}`.
    Search it for `'value': 'LOW'`.
  - Its error field may hold the literal `"None"`. That isn't a real error, so exclude it
    from error counts.
- **Step names**
  - Action steps are named `<subagent>.<action>`.
  - LLM steps are named after the planner prompt, for example `AiCopilot__ReactValidationPrompt`
    on the older planner.
  - Names differ between the legacy planner and Agent Script runtimes (verify in your org).
- **Ordering:** there's no parent step. Order by `ssot__StartTimestamp__c`, or walk
  `ssot__PrevStepId__c`.

### Audit & Feedback DMOs

| DMO | Join | Holds |
|---|---|---|
| `GenAIGatewayRequest__dlm` | `gatewayRequestId__c` = step `ssot__GenAiGatewayRequestId__c` | `prompt__c`, `maskedPrompt__c`, `model__c`, `provider__c`, `promptTokens__c`, `completionTokens__c`, `totalTokens__c`, `promptTemplateDevName__c`, `enablePiiMasking__c`, safety-scoring flags |
| `GenAIGatewayResponse__dlm` | `generationRequestId__c` = request `gatewayRequestId__c` | Response parameters |
| `GenAIGeneration__dlm` | `generationId__c` = step `ssot__GenerationId__c` | `responseText__c`, `maskedResponseText__c` |
| `GenAIContentQuality__dlm` | `parent__c` = generation `generationId__c` | `isToxicityDetected__c` |
| `GenAIContentCategory__dlm` | `parent__c` = quality `id__c` | `detectorType__c`: `TOXICITY`, `PII`, `PROMPT_DEFENSE`, `InstructionAdherence`; `category__c`; `value__c` |
| `GenAIFeedback__dlm` / `GenAIFeedbackDetail__dlm` | `generationId__c`; detail `parent__c` = `feedbackId__c` | `feedback__c` (`GOOD`/`BAD`), free-text comment |

Detector categories that have been seen:
- `TOXICITY`: `hate`, `identity`, `physical`, `profanity`, `sexual`, `violence`, `toxicity`
  and `safety_score`. For `safety_score`, 1 is safest; for the others, ≥ 0.5 is toxic.
- `PII`: `CREDIT_CARD`, `EMAIL_ADDRESS`, `PERSON`, `US_PHONE_NUMBER`.
- `PROMPT_DEFENSE`: `isPromptAttackDetected` and `aggregatePromptAttackScore`.
- `InstructionAdherence`: `High`, `Low` or `Uncertain`.

`isToxicityDetected = false` doesn't prove there is no toxicity.

## Ways to run SQL

| Surface | When | Shape |
|---|---|---|
| Data Cloud **Query Editor** (Data 360 app) | Ad hoc exploration | Paste SQL, pick the data space |
| **Query SQL API** | Scripts, CI, exports | `POST /services/data/v66.0/ssot/query-sql?dataspace=default` with body `{"sql": "...", "rowLimit": 500}`. The response is `metadata[]` plus `data[][]` (an array of arrays). If `status.completionStatus` is `Running` or `MoreChunksAvailable`, page via `GET …/ssot/query-sql/{queryId}/rows`. Needs v64.0+ |
| `sf api request rest` | From a terminal using the CLI's org auth | `sf api request rest "/services/data/v66.0/ssot/query-sql?dataspace=default" -X POST -H "Content-Type: application/json" -b @body.json -o <org>` |
| Apex `ConnectApi.CdpQuery.queryAnsiSqlV2(input, dataSpace)` | In-org helpers, invocable actions, scheduled jobs | Returns `CdpQueryOutputV2` with `data` rows |
| SOQL on `__dlm` | Simple filters via the standard REST `/query` | `SELECT ssot__Id__c FROM ssot__AiAgentSession__dlm WHERE ssot__StartTimestamp__c = LAST_N_DAYS:4` |
| Session Trace **OTel API** (beta) | Export one session to Datadog, Splunk, Honeycomb or any OTLP collector | `GET /services/data/v66.0/einstein/audit/otel/{stdmSessionId}`. One session per call, sessions ≤ 72 h old. Takes the STDM UUID, not a CRM record ID. Agentforce doesn't push OTLP itself, so poll it |

Find the data space name with `sf api request rest "/services/data/v66.0/ssot/data-spaces" -o <org>`.
Don't pass `--json`, which this beta command rejects. For scheduled exports, use an External
Client App with the client-credentials flow and a run-as user that has Data Cloud and audit
read access.

Write request bodies to a `mktemp` file with mode 0600, not a fixed `/tmp/x.json`. They can
carry org data.

## SQL recipes

Replace `<AGENT>` with the API name from the participant DMO (recipe 1), not the Builder
label. Add a time filter to every query.

**0. What's in this org?** Run these first.

```sql
SELECT ssot__AiAgentSessionEndType__c AS end_type, ssot__AiAgentChannelType__c AS channel, COUNT(*) AS n
FROM "ssot__AiAgentSession__dlm"
WHERE ssot__StartTimestamp__c >= current_date - INTERVAL '7' DAY
GROUP BY 1, 2 ORDER BY n DESC
```

**1. Agents that have produced sessions** (these are the values AHM filters use)

```sql
SELECT ssot__AiAgentApiName__c AS agent, ssot__AiAgentType__c AS agent_type, COUNT(*) AS rows_seen
FROM ssot__AiAgentSessionParticipant__dlm
WHERE ssot__AiAgentSessionParticipantRole__c != 'USER'
GROUP BY 1, 2 ORDER BY agent
```

**2. Recent real sessions for one agent.** Skip sessions with no turns, such as preview
pings.

```sql
SELECT s.ssot__Id__c, s.ssot__StartTimestamp__c, s.ssot__EndTimestamp__c,
       s.ssot__AiAgentChannelType__c, s.ssot__AiAgentSessionEndType__c
FROM "ssot__AiAgentSession__dlm" s
JOIN "ssot__AiAgentSessionParticipant__dlm" p ON p.ssot__AiAgentSessionId__c = s.ssot__Id__c
WHERE p.ssot__AiAgentApiName__c = '<AGENT>'
  AND s.ssot__StartTimestamp__c >= current_date - INTERVAL '7' DAY
  AND s.ssot__Id__c IN (SELECT ssot__AiAgentSessionId__c FROM "ssot__AiAgentInteraction__dlm"
                        WHERE ssot__AiAgentInteractionType__c = 'TURN')
ORDER BY s.ssot__StartTimestamp__c DESC
LIMIT 50
```

**3. Turn-by-turn transcript for a session**

```sql
SELECT i.ssot__StartTimestamp__c AS turn_start, i.ssot__TopicApiName__c AS subagent,
       m.ssot__AiAgentInteractionMessageType__c AS dir, m.ssot__ContentText__c AS text
FROM "ssot__AiAgentInteraction__dlm" i
JOIN "ssot__AiAgentInteractionMessage__dlm" m ON m.ssot__AiAgentInteractionId__c = i.ssot__Id__c
WHERE i.ssot__AiAgentSessionId__c = '<SESSION_ID>'
ORDER BY i.ssot__StartTimestamp__c, m.ssot__MessageSentTimestamp__c
```

**4. What the agent did inside each turn:** steps, timings, I/O, variable changes, errors.

```sql
SELECT i.ssot__TopicApiName__c AS subagent, st.ssot__AiAgentInteractionStepType__c AS step_type,
       st.ssot__Name__c AS step, st.ssot__StartTimestamp__c,
       EXTRACT(EPOCH FROM (st.ssot__EndTimestamp__c - st.ssot__StartTimestamp__c)) AS secs,
       st.ssot__InputValueText__c AS input, st.ssot__OutputValueText__c AS output,
       st.ssot__PreStepVariableText__c AS vars_before, st.ssot__PostStepVariableText__c AS vars_after,
       st.ssot__ErrorMessageText__c AS error
FROM "ssot__AiAgentInteractionStep__dlm" st
JOIN "ssot__AiAgentInteraction__dlm" i ON st.ssot__AiAgentInteractionId__c = i.ssot__Id__c
WHERE i.ssot__AiAgentSessionId__c = '<SESSION_ID>'
ORDER BY st.ssot__StartTimestamp__c
```

**5. Failing actions, ranked**

```sql
SELECT st.ssot__Name__c AS action, COUNT(*) AS failures,
       MIN(st.ssot__ErrorMessageText__c) AS sample_error
FROM "ssot__AiAgentInteractionStep__dlm" st
WHERE st.ssot__AiAgentInteractionStepType__c = 'ACTION_STEP'
  AND st.ssot__ErrorMessageText__c IS NOT NULL
  AND st.ssot__ErrorMessageText__c NOT IN ('NOT_SET', 'None', '')
  AND st.ssot__StartTimestamp__c >= current_date - INTERVAL '7' DAY
GROUP BY 1 ORDER BY failures DESC
```

Divide by total invocations of the same action, which is the same query without the error
filter, to get a failure rate.

**6. Latency by step**

```sql
SELECT st.ssot__AiAgentInteractionStepType__c AS step_type, st.ssot__Name__c AS step, COUNT(*) AS n,
       AVG(EXTRACT(EPOCH FROM (st.ssot__EndTimestamp__c - st.ssot__StartTimestamp__c))) AS avg_secs,
       MAX(EXTRACT(EPOCH FROM (st.ssot__EndTimestamp__c - st.ssot__StartTimestamp__c))) AS max_secs
FROM "ssot__AiAgentInteractionStep__dlm" st
WHERE st.ssot__StartTimestamp__c >= current_date - INTERVAL '7' DAY
GROUP BY 1, 2 HAVING COUNT(*) >= 10 ORDER BY avg_secs DESC LIMIT 25
```

An action over 10 s needs work. Each routing-only hop adds about 3–5 s, so look for an
`entry → hub → real subagent` chain.

**7. Subagent routing distribution and session hopping**

```sql
SELECT ssot__TopicApiName__c AS subagent, COUNT(*) AS turns,
       COUNT(DISTINCT ssot__AiAgentSessionId__c) AS sessions
FROM "ssot__AiAgentInteraction__dlm"
WHERE ssot__AiAgentInteractionType__c = 'TURN'
  AND ssot__StartTimestamp__c >= current_date - INTERVAL '30' DAY
GROUP BY 1 ORDER BY turns DESC
```

- A designed subagent with zero turns is **dead**: the entry agent is answering in its
  place.
- If most turns sit in the router, the entry agent is answering directly instead of
  transitioning.

**8. Low instruction adherence, toxicity and prompt attacks**

```sql
SELECT i.ssot__AiAgentSessionId__c AS session_id, i.ssot__TopicApiName__c AS subagent,
       c.detectorType__c AS detector, c.category__c AS category, c.value__c AS score,
       g.responseText__c AS response
FROM GenAIContentCategory__dlm c
JOIN GenAIContentQuality__dlm q ON c.parent__c = q.id__c
JOIN GenAIGeneration__dlm g ON g.generationId__c = q.parent__c
JOIN ssot__AiAgentInteractionStep__dlm st ON st.ssot__GenerationId__c = g.generationId__c
JOIN ssot__AiAgentInteraction__dlm i ON st.ssot__AiAgentInteractionId__c = i.ssot__Id__c
WHERE (c.detectorType__c = 'InstructionAdherence' AND c.category__c = 'Low')
   OR (c.detectorType__c = 'PROMPT_DEFENSE' AND c.category__c = 'isPromptAttackDetected' AND c.value__c = 'true')
   OR (c.detectorType__c = 'TOXICITY' AND c.category__c NOT IN ('safety_score') AND TRY_CAST(c.value__c AS DECIMAL) >= 0.5)
LIMIT 200
```

Some examples join `ContentCategory.parent__c` straight to the generation. Check the parent
chain in your org with a `LIMIT 5` probe.

**9. The exact prompt the LLM saw** (for LOW-adherence or bad-answer turns)

```sql
SELECT s.ssot__Id__c, s.ssot__Name__c, r.prompt__c, r.maskedPrompt__c, g.responseText__c,
       r.model__c, r.promptTokens__c, r.completionTokens__c
FROM "ssot__AiAgentInteractionStep__dlm" s
LEFT JOIN "GenAIGeneration__dlm" g ON s.ssot__GenerationId__c = g.generationId__c
LEFT JOIN "GenAIGatewayRequest__dlm" r ON s.ssot__GenAiGatewayRequestId__c = r.gatewayRequestId__c
WHERE s.ssot__Id__c IN ('<STEP_ID_1>', '<STEP_ID_2>')
```

The results are null unless Audit & Feedback is on. Check two things: whether the
subagent's instructions were in the prompt at all, and whether the response contradicts
them.

**10. Tokens by model and prompt template**

```sql
SELECT model__c, promptTemplateDevName__c, COUNT(*) AS calls,
       SUM(promptTokens__c) AS in_tok, SUM(completionTokens__c) AS out_tok
FROM GenAIGatewayRequest__dlm
WHERE timestamp__c >= current_date - INTERVAL '7' DAY
GROUP BY 1, 2 ORDER BY in_tok DESC
```

`timestamp__c` on the request DMO has been seen but isn't documented (verify in your org).
Token counts aren't credits; see *Cost* below.

**11. Quality scores and their reasons** (Agent Optimization)

```sql
SELECT m.ssot__AiAgentApiName__c AS agent, m.ssot__RequestSummaryText__c AS intent,
       t.ssot__Value__c AS score, ta.ssot__AssociationReasonText__c AS reason
FROM "ssot__AiAgentTagAssociation__dlm" ta
JOIN "ssot__AiAgentTag__dlm" t ON ta.ssot__AiAgentTagId__c = t.ssot__Id__c
JOIN "ssot__AiAgentMoment__dlm" m ON ta.ssot__AiAgentMomentId__c = m.ssot__Id__c
WHERE m.ssot__StartTimestamp__c >= current_date - INTERVAL '14' DAY
  AND CAST(t.ssot__Value__c AS INTEGER) <= 2
ORDER BY m.ssot__StartTimestamp__c DESC LIMIT 100
```

The scores map to labels: 5 is High, 3–4 Medium, 2 Low, 1 Very Low. The `reason` column
usually names the root cause: a misread intent, a bad action result, or poor phrasing.

**12. RAG quality by subagent and retriever**

```sql
SELECT i.ssot__TopicApiName__c AS subagent, r.ssot__RetrieverApiName__c AS retriever, COUNT(*) AS n,
       AVG(r.ssot__ContextPrecisionScoreNumber__c) AS precision,
       AVG(r.ssot__AnswerRelevancyScoreNumber__c) AS relevancy,
       AVG(r.ssot__FaithfulnessRelevancyScoreNumber__c) AS faithfulness
FROM "ssot__AiRetrieverQualityMetric__dlm" r
JOIN "ssot__AiAgentInteractionStep__dlm" s ON r.ssot__AiGatewayRequestId__c = s.ssot__GenAiGatewayRequestId__c
JOIN "ssot__AiAgentInteraction__dlm" i ON s.ssot__AiAgentInteractionId__c = i.ssot__Id__c
GROUP BY 1, 2 ORDER BY precision ASC
```

Faithfulness below 0.8 suggests hallucination. Relevancy below 0.7 suggests the answer
misses the question. Low precision means poor retrieval or content. See `agent-rag`.

**13. Escalation and abandonment**

The packaged rate is the safest source: `Escalation_Rate_clc` and `Abandonment_Rate_clc` in
the Service Agent Analytics SDM, which you can query through the semantic engine gateway
(`POST /services/data/v66.0/semantic-engine/gateway`). From STDM, the heuristics are:
- the end-type distribution (recipe 0);
- sessions whose steps include an escalation `SESSION_END` or `ACTION_STEP` name;
- sessions under 30 s with 1–2 turns and a null end type, which suggests abandonment.

These are heuristics (verify in your org).

## Reading what you find

| Signal | Likely cause | Fix in the `.agent` file |
|---|---|---|
| `ACTION_STEP` error | Flow/Apex failed, missing permission, bad input | Fix the target, the agent-user permission set, or the `with` binding |
| `TopicApiName` doesn't match the intent | Subagent `description` too broad or narrow | Sharpen descriptions; add `available when` |
| No `ACTION_STEP` where one was expected | Action not exposed in `reasoning.actions`, or the instructions don't name it | Expose it; reference it with `{!@actions.x}` in the instructions |
| Pre-step and post-step variables identical when a value was given | Missing `set` or `setVariables` binding | Add the binding |
| Same subagent for 3+ turns, no progress | No transition out | Add `@utils.transition` and an instruction for what happens next |
| All turns stay in `start_agent` | Router answers directly | Tell the router to route only and always use a transition action |
| LOW adherence | Instructions vague or conflicting, or persona spread across subagents | Rewrite the instructions; keep the persona in `system:` |
| Rich instructions but generic behaviour | Bundle deployed but not published or activated | `sf agent publish authoring-bundle` and then activate |
| Low quality score, many turns per moment | Agent needs many turns per intent | Collect inputs up front; reduce hops |

Reproduce before you fix:
1. Replay the failing turn with `sf agent preview start --authoring-bundle <Name>`, using
   `--simulate-actions` or `--use-live-actions`, 3 times.
2. Read the local trace at `.sfdx/agents/<Name>/sessions/<id>/traces/<planId>.json`. Its
   step types include `NodeEntryStateStep`, `EnabledToolsStep`, `LLMStep`, `FunctionStep`,
   `VariableUpdateStep`, `TransitionStep`, `ReasoningStep` (GROUNDED/UNGROUNDED) and
   `PlannerResponseStep` (safety scores).
3. Classify the result as reproduced 3/3, intermittent, or not reproduced.

Then make one fix per publish and turn each confirmed issue into a Testing Center case (see
`agent-evals`).

## Alerts (Agent Health Monitoring)

- **Create alerts in the UI** (Agentforce Studio → alerts) or through
  `/services/data/v66.0/tableau/dataAlerts` with `dataAlertType: "agenthealthmonitoring"`.
  There's no `sf agent alert` command; use `sf api request rest`.
- **Models:** alerts target only Agentforce Analytics Foundations and the Employee or
  Service Agent Analytics SDMs. Find each SDM's `apiName` by its `label` or `app`, because
  the suffix changes per org.
- **Thresholds are raw 0–1 ratios.** 5% is `"0.05"`, and `"1"` means 100%.
- **Field names differ between POST and GET.** POST uses `utterance` and PascalCase `type`
  values, so a GET response can't be posted back as-is.
- **Read notifications with the header `X-UNS-Type-Filter: all`.** Without it the list is
  always empty. Attribute each notification by `targetPageRef.state.c__alertId`, not by
  `metricId`.
- **Guard against low volume.** Pair each rate condition with an absolute-count condition,
  so one bad session in 15 minutes doesn't page anyone.
- **Starter alerts:**
  - Error rate above baseline + 2σ over 1 h
  - Average interaction latency above your SLO
  - Escalation rate change week over week
  - A daily scheduled job running recipes 5 and 8, which posts to a channel when counts jump

## Cost

- **Agent actions cost Flex Credits:**
  - Flow, Apex and other actions: about 20 credits per run.
  - Prompt templates: about 2–16 credits per call, depending on size.
  - `@utils.*`, `if`/`set`, `before_reasoning` / `after_reasoning` and the reasoning turn
    itself are free (verify against your contract).
- **Tracing and audit cost Data 360 credits:**
  - Ingestion: about 24 records per LLM round trip, which is the main driver.
  - Queries, reports and dashboards: billed per record processed.
  - Storage beyond your allocation.
- **Where to watch:** Digital Wallet shows consumption in near real time, and Observability
  shows credits by agent.
- **Cheaper patterns:**
  - Do deterministic work in `before_reasoning`.
  - Merge chains of small actions into one action.
  - Run filtered extracts with time bounds rather than `SELECT *`.

## Anti-patterns

- Turning on tracing after the incident. There's no backfill.
- `SELECT *` across all of `AiAgentInteractionStep` with no time filter.
- Filtering sessions by the Builder label or `DeveloperName_vN` instead of the participant
  `ssot__AiAgentApiName__c`.
- Counting `TRUST_GUARDRAILS_STEP`, `NOT_SET` or `"None"` as action errors.
- Judging the agent on deflection alone. A politely stonewalling agent "deflects" well.
- Debugging production from Builder traces, or debugging prompts from STDM. Use each where
  it is strong.
- Copying prompts and responses into Slack or a ticket without redaction.
- Relying on the auto-reverse-engineered Builder trace endpoints in scripts. They're
  internal; use `sf agent preview` traces.

## Sources

- `SalesforceAIResearch/agentforce-adlc`:
  - `skills/agentforce-observe/` (SKILL.md, references/stdm-schema.md, stdm-queries.md,
    issue-classification.md, ahm-alerts.md, assets/apex/AgentforceOptimizeService.cls)
  - `skills/agentforce-generate/references/production-gotchas.md`
- `salesforce/agentscript`: `apps/docs/docs/dialects/agentforce/ascript-blocks.md` (`enable_enhanced_event_logs`).
- Salesforce Help:
  - *Data Model for Agentforce Session Tracing* (`ai.generative_ai_session_trace_data_model.htm`)
  - *Set Up Agentforce Session Tracing* (`ai.generative_ai_session_trace_setup.htm`)
  - *Get Insights from Agent Session Tracing Data*
  - *Billing Considerations for Agentforce Session Tracing*
  - *Monitor Your Agent* (`ai.agent_parent_monitor.htm`)
- Salesforce Developers: *Export Agentforce Session Tracing Data (OTel API, beta)*,
  `docs/ai/agentforce/guide/otel-api.html`. Read via search snippets and the HoneyHive
  cookbook (`honeyhiveai/cookbook/salesforce-agentforce-cookbook`), which calls
  `/einstein/audit/otel/{id}` and documents the 72 h window.
- Trailhead:
  - *Agent analytics: Monitor, investigate, and improve agents* (`agent-analytics-and-monitoring`)
  - *The Agentforce Trust Layer* (audit trail)
- Salesforce product page: salesforce.com/agentforce/observability.
- Third-party, cross-checked:
  - `Jaganpro/sf-skills` → `sf-ai-agentforce-observability`. Live-org DMO field
    inventory, detector values, query-sql paging, step taxonomy.
  - salesforcedictionary.com *Agentforce Observability guide* (freshness figures).
  - `PranavNagrecha/AwesomeSalesforceSkills` → `einstein-trust-layer`: ~24 records per
    round trip, hourly refresh.
