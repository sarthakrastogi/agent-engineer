# AgentCore Memory

Managed conversation history (short-term) plus extracted, searchable knowledge (long-term).
This file covers the AgentCore mechanics. For deciding what to store, when to retrieve it,
and how to keep retrieved memory from bloating context, see `agent-context` →
`references/memory-and-compaction.md`.

## Model

```
Memory resource ─ events (short-term)  keyed by actorId + sessionId (+ branch)
                │      │ async extraction by strategies (max 6 per memory)
                └ records (long-term)   stored under namespaces, semantic search + metadata filters
```

| Need | Use |
|---|---|
| Resume a conversation, or survive a microVM restart | Short-term: `CreateEvent` / `ListEvents` for one actor and session |
| Facts, preferences or summaries across sessions | Long-term strategies + `RetrieveMemoryRecords` |
| Long-term records only, from content you already store elsewhere | `IngestData`. It writes no event, so `ListEvents` can't see the content |
| Store an event but keep it out of extraction | `CreateEvent` with `extractionMode: "SKIP"` |
| Your own extraction model or pipeline | Built-in-with-overrides strategy, or self-managed |
| Fork a conversation (what-if, retry) | `branch: {name, rootEventId}` on `CreateEvent`; `filter.branch` on `ListEvents` |
| React when records change | Record streaming to Kinesis |

## Create

```bash
agentcore add memory --name SupportMemory --strategies SEMANTIC,SUMMARIZATION,USER_PREFERENCE \
  --event-expiry-duration 30
agentcore deploy
```

```bash
aws bedrock-agentcore-control create-memory --name SupportMemory --event-expiry-duration 30 \
  --memory-strategies '[
    {"semanticMemoryStrategy":      {"name":"Facts","namespaceTemplates":["/facts/{actorId}/"]}},
    {"userPreferenceMemoryStrategy":{"name":"Prefs","namespaceTemplates":["/prefs/{actorId}/"]}},
    {"summaryMemoryStrategy":       {"name":"Summ", "namespaceTemplates":["/summaries/{actorId}/{sessionId}/"]}}]'
```

- Always set explicit `namespaceTemplates`. The defaults disagree with each other: semantic
  and user preference use `/strategy/{memoryStrategyId}/actors/{actorId}/` (plural), while
  summary uses `/actor/{actorId}/session/{sessionId}/`.
- Summary strategies need `{sessionId}` in the template. They also write a `FULL_SESSION`
  record per session.
- `eventExpiryDuration` is a number of days, 3–365 (API and CLI; the quotas page says 7 minimum),
  and it is required. Decide these at
  create time: `encryptionKeyArn`, because the KMS key can't be changed later, and
  `indexedKeys`, because they can't be removed.
- 150 memories per Region. Creating one takes about 2–3 minutes; wait for ACTIVE.

## Strategies

| Strategy | Extracts | Notes |
|---|---|---|
| `SEMANTIC` | Facts | |
| `USER_PREFERENCE` | Stated preferences | |
| `SUMMARIZATION` | Running per-session summary | Retrieve with `ListMemoryRecords` on the session namespace |
| `EPISODIC` | Episodes plus cross-episode reflections | Reflections live in a namespace equal to or less nested than episodes. Limit 50K tokens/min per session |
| Built-in with overrides (custom) | Same pipeline with your appended prompt (30 KB) and your Bedrock `modelId` | Runs in your account via `memoryExecutionRoleArn`. Bedrock throttling silently drops extraction; enable log delivery. Records cost $0.25/1K instead of $0.75 |
| Self-managed | Nothing. Triggers (message count, idle, tokens) push to SNS + S3, and you write back with `BatchCreate/Update/DeleteMemoryRecords` | `IngestData` skips self-managed strategies |

- Long-term extraction is **asynchronous**. Records appear seconds to "a minute or more"
  after `CreateEvent`. Never read back your own write in the same turn. In tests, poll
  `ListMemoryRecords` or consume the Kinesis stream.
- The account limit is 150K tokens/min for built-in extraction (adjustable). Overruns create
  failed jobs (`LTM_RATE_EXCEEDED`). Find them with `ListMemoryExtractionJobs` and re-drive
  with `StartMemoryExtractionJob`. Alarm on the `FailedExtraction` metric.

## Write and read

```python
dp = boto3.client("bedrock-agentcore")
dp.create_event(memoryId=MEM, actorId=actor, sessionId=sid, eventTimestamp=datetime.now(timezone.utc),
    payload=[{"conversational": {"role": "USER", "content": {"text": msg}}}],
    clientToken=turn_id)                              # idempotent retries
recs = dp.retrieve_memory_records(memoryId=MEM, namespace=f"/facts/{actor}/",
    searchCriteria={"searchQuery": msg, "topK": 5,
        "metadataFilters": [{"left": {"metadataKey": "priority"}, "operator": "EQUALS_TO",
                             "right": {"metadataValue": {"stringValue": "high"}}}]})
hist = dp.list_events(memoryId=MEM, actorId=actor, sessionId=sid, includePayloads=True, maxResults=100)
```

- Payload types:
  - `conversational` (role + text) is extracted into long-term memory.
  - `json` (≤100 KB per item; for non-conversational events and logs) is extracted.
  - `blob` stays in short-term memory and is never extracted.
- Per event: up to 100 payload items, 100 KB per message and 10 MB in total, plus up to 15
  metadata entries.
- **Event metadata is not encrypted with your CMK.** Keep it free of secrets and PII.
- `actorId` pattern: `[a-zA-Z0-9][a-zA-Z0-9-_/]*(?::[a-zA-Z0-9-_/]+)*...`. It allows no `@`
  and no `.`, so emails fail validation. Use the IdP `sub` or `oid`. `sessionId` is ≤100
  chars.
- `RetrieveMemoryRecords` takes exactly one of `namespace` (exact) or `namespacePath`
  (hierarchy). `searchQuery` is required; `topK` is 1–100; `maxResults` defaults to 20.
- Throughput limits (TPS per account):
  - `RetrieveMemoryRecords` and `ListMemoryRecords`: 30 each;
  - `CreateEvent`: 200, but only **5 per actor per session** for conversational payloads;
  - `ListEvents`: 200 (20 per actor per session);
  - `IngestData`: 10, with 100 KB total per request;
  - most other data-plane APIs: 20.
- Hitting these limits? Batch the turn into a single event, and retrieve once per turn
  rather than once per tool call.

## Namespaces and per-user scoping

- Namespaces are stored normalised with a leading and trailing slash: `/facts/alice/`. Use
  that exact form everywhere:
  - in templates and in Batch-API `namespaces`;
  - in `namespace` / `namespacePath`;
  - in the IAM condition keys `bedrock-agentcore:namespace` and
    `bedrock-agentcore:namespacePath` (`/facts/*` for a hierarchy).

  A missing slash means the IAM condition never matches, so every call gets AccessDenied
  (CLI #1366).
- Built-in variables are `{actorId}`, `{sessionId}` and `{memoryStrategyId}`. For tenants and
  teams, declare `namespaceKeys`:
  - limits: ≤5 per memory, lowercase, ≤32 chars, optional `allowedValues` and `regexPattern`;
  - pass values at write time: `CreateEvent extractionConfig.namespaceVariables`;
  - `UpdateMemory` replaces the whole `namespaceKeys` list.
- **A missing custom variable silently skips extraction** for that strategy. The event
  still succeeds. Watch the `NamespaceResolutionFailure` metric.
- The data plane is **SigV4-only**. Your backend role can read every actor. Isolation is
  whatever `actorId` and namespace your code sends. Derive both from the validated JWT `sub`,
  never from the request body. A harness's `actorId` is caller-overridable per invoke
  (`identity.md` Spoofing).
- To enforce isolation in infrastructure instead of in code:
  - Front Memory with a Gateway **Memory connector** target (`agentcore-memory`) and an OAuth
    inbound authorizer.
  - Attach a policy engine with Cedar, e.g.
    `context.input.actorId == principal.getTag("sub")`. Cedar can't concatenate strings, so
    for namespace isolation have the IdP issue a full-namespace claim.
  - Lock direct access with a Memory resource policy on `aws:SourceArn` = the gateway ARN.
  - Limits of this approach:
    - Batch record APIs aren't Cedar actions; allow or deny them whole, in IAM.
    - A policy that reads a field the operation lacks reaches ACTIVE but returns 403 at
      runtime. Guard every field with `has`, and run `LOG_ONLY` before `ENFORCE`.

## Metadata filters

- Declare up to 10 `indexedKeys` (`STRING` / `STRINGLIST` / `NUMBER`). They can't be removed
  and aren't backfilled.
- Filters run before the vector search. Up to 5 are allowed, combined with AND. Built-in
  timestamp keys are `x-amz-agentcore-memory-createdAt` / `updatedAt`.
- For values your app knows (tenant, department, `agent_id`), use `extractionType:
  STRICTLY_CONSISTENT` (≤3 per strategy). The value is copied verbatim, and records with
  different values never consolidate together. Changing this setting later isolates old
  records from new ones.
- For LLM-inferred keys, add `allowedValues` so you don't get `High`, `high` and `HIGH`.

## Retention, deletion, PII

- Short-term retention is `eventExpiryDuration`. **Long-term records never expire by
  default.** Add `recordExpirySpecification.recordExpiryDurationDays`:
  - minimum 31 days, set per strategy or per Batch request;
  - semantic and user preference must share a value; so must summary and episodic;
  - setting it needs `bedrock-agentcore:UpdateMemoryRecordExpirySpecification` in addition to
    the base action.
- TTL deletion lags by **days**, and expired records stay readable until then. For erasure
  requests, delete explicitly:
  - `DeleteEvent` for each actor and session (use `ListSessions`);
  - `DeleteMemoryRecord` / `BatchDeleteMemoryRecords` for each actor namespace;
  - streamed copies in Kinesis.
  - Beware: the SDK's `delete_all_long_term_memories_in_namespace` deletes only 10 records
    (SDK #727). Page the deletes yourself.
- Extraction stores user facts by design. Before you enable `USER_PREFERENCE` or `SEMANTIC`
  for regulated data, review the PII and retention basis. Use a CMK for the memory.
- Record streaming: `streamDeliveryResources` to Kinesis needs `memoryExecutionRoleArn`. It
  emits `MemoryRecordCreated` / `Updated` / `Deleted` events. `FULL_CONTENT` copies record
  text into the stream; `METADATA_ONLY` doesn't.
- Cross-account access: `PutResourcePolicy` on the memory, and callers pass the full memory
  ARN as `memoryId`. Both policies must allow the call. Delivery to S3, SNS or Kinesis in
  another account goes through the memory execution role.

## Framework integrations

- **Strands:**
  - Wiring: `AgentCoreMemorySessionManager(agentcore_memory_config=AgentCoreMemoryConfig(memory_id, session_id, actor_id, retrieval_config={"/facts/{actorId}/": RetrievalConfig(top_k=5, relevance_score=0.2)}))`,
    passed as `Agent(session_manager=...)`.
  - The `read_only` flag stops writes.
  - Known traps:
    - CLI templates set `relevance_score=0.5` while real scores were 0.34–0.37, so recall
      silently returned nothing (CLI #2706). Start at 0.2, the SDK default, and tune it.
    - Injected `<user_context>` repeats every turn (SDK #420).
    - History is silently dropped when a metadata-filtered `ListEvents` lags (SDK #564).
- **LangGraph:** `langgraph_checkpoint_aws.AgentCoreMemorySaver(MEMORY_ID)` is the
  checkpointer, with `actor_id` + `thread_id` in `RunnableConfig`. `AgentCoreMemoryStore` is
  the long-term store; write and search it from pre-/post-model hooks or from a tool.
- LangChain and LlamaIndex integrations also exist.

## Memory in the harness

- **API `CreateHarness` without `memory` provisions managed memory, which is billed.** CLI
  `agentcore add harness` does the same unless you pass `--memory '{"mode":"disabled"}'` or
  `'{"mode":"existing","arn":...}'`. In the project YAML, omitting `memory:` disables it.
  Check with `GetHarness`, because the paths disagree.
- Turn it off with `update-harness --memory '{"optionalValue":{"disabled":{}}}'`. To use
  existing memory, set `agentCoreMemoryConfiguration.arn`.
- Managed memory has these limits:
  - You can't choose its extraction model or change its KMS key.
  - `DeleteHarness` deletes it unless you pass `deleteManagedMemory=false`.
  - For custom namespaces, a shared memory or your own model, use your own memory instead.
- Retrieval uses `topK=10, relevanceScore=0.2` for each active strategy unless you set
  `retrievalConfig`. If you change strategies on your own memory, call `UpdateHarness` again.
  A YAML reference by ARN can't take `retrievalConfig`; a reference by name can.
- **Terraform bug:** `aws_bedrockagentcore_harness` with memory by ARN and no
  `retrieval_config` fails every apply with "Provider produced inconsistent result after
  apply" (hashicorp #49594 open, fix PR #50099 open).
  - Workaround: attach the memory outside Terraform (CloudFormation or CLI).
  - Alternative (unverified): use a memory with exactly one strategy and declare a matching
    `retrieval_config`.
- The CLI-created default role once didn't match managed-memory ARNs, so the first invoke
  failed with AccessDenied on `ListEvents` (CLI #2642, fixed). Check your role's `memory/`
  resource pattern.

## Pricing (list)

| Item | Price |
|---|---|
| Short-term ingestion | $1.00/GB, **billed 12 KB min and 64 KB max per event** |
| Short-term retrieval | $0.20/GB, same per-event min and max |
| Short-term storage | $0.10/GB-month on actual size, prorated over the event TTL |
| Long-term storage | $0.75 per 1K records/month (built-in); $0.25 (override or self-managed, model cost on you) |
| Long-term retrieval | $0.50 per 1K `RetrieveMemoryRecords` |

Pricing changed from $0.25 per 1K events. The pricing page dates the change 2026-10-06; the
What's New post is 2026-10-12. Many tiny events now cost the 12 KB minimum each, so batch the
messages of a turn into one event. Shorter `eventExpiryDuration` cuts storage.

## Checklist

- [ ] Explicit `namespaceTemplates` with leading and trailing slashes. `actorId` from the
      validated token (no emails).
- [ ] Every custom namespace variable is passed on every `CreateEvent`; a
      `NamespaceResolutionFailure` alarm exists.
- [ ] Code tolerates async extraction; a `FailedExtraction` alarm and a re-drive path exist.
- [ ] Long-term TTL set. Erasure path deletes events, records and streamed copies.
- [ ] Harness memory deliberately managed, your own (by ARN or name), or disabled. Default
      role can reach it.
- [ ] Retrieval threshold tuned against real scores; retrieved context deduplicated per turn.
