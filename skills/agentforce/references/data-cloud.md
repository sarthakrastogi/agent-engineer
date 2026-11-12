# Data Cloud (Data 360) for agent builders

Data 360 (formerly Data Cloud, "DC" in APIs) is where Agentforce gets **unified customer facts**
and **retrieval over unstructured content**. It is also a hard dependency of Agentforce itself:
the Trust Layer audit trail, agent event logs, session tracing and consumption billing all land
there. Read `agent-rag` for retrieval method; this file is the Salesforce mechanics.

## 1. The objects you will touch

| Concept | Suffix / form | What it is | Agent relevance |
|---|---|---|---|
| Data stream | `<Name>_Home` (CRM), any name | A connector feed (CRM, S3, Snowflake, Ingestion API, SharePoint …) | Freshness of everything downstream |
| Data Lake Object (DLO) | `__dll` | Raw landed table, one per stream | Rarely queried by agents |
| Data Model Object (DMO) | `__dlm` (standard: `ssot__Individual__dlm`) | Canonical model; DLO fields are **mapped** onto it | What retrievers, data graphs and SQL read |
| Unstructured DLO / DMO (UDLO/UDMO) | `__dll` / `__dlm` | Files (PDF, HTML, TXT…) landed as rows with a file path | Source for a search index |
| Chunk DMO / index DMO | `<Index>_chunk__dlm`, `<Index>_index__dlm` | Created by a search index: text chunks, and vectors + scores | Joined in vector/hybrid SQL |
| Identity resolution ruleset | Unified DMOs, e.g. `UnifiedssotIndividualMain__dlm` | Match + reconciliation rules → one unified profile | "Who is this customer" across systems |
| Calculated insight | `__cio` | SQL-defined metric (LTV, churn risk, tier) | Verified facts to pre-load into the agent |
| Segment / activation | — | Audience + push to a target | Marketing; agents rarely need it |
| Data graph | API name | Pre-joined, denormalised JSON of a root DMO + related DMOs | Fast single-record context for prompts/agents |
| Data space | `default`, custom | Logical partition with its own permissions | Agent user must be scoped to it |
| Zero Copy / data federation | — | Query Snowflake, Databricks, BigQuery, Redshift in place | Ground on lakehouse data without copying |

Field types matter in mapping: the CRM connector lands Boolean as **Text** and Date as
**DateTime** on the DLO, so DLO→DMO mapping silently leaves those fields unmapped until you add
a formula/transform. Verify `isMapped: true` on the DMO fields before building anything on top.

## 2. Pick the grounding mechanism

| Need | Use | Why |
|---|---|---|
| Answer questions from docs, PDFs, Knowledge articles, quickly | **Agentforce Data Library (ADL)** + `AnswerQuestionsWithKnowledge` | Provisions stream → DLO → DMO → index → retriever for you |
| Same, but you need control of chunking, fields, filters, embeddings, many sources | **Custom search index + Einstein Studio retriever**, wrapped in an ADL of type RETRIEVER or called from a prompt template | ADL hides the knobs |
| Facts about *this* customer (tier, open orders, churn risk) | Calculated insights / unified profile fetched by a Flow or Apex action, stored in variables **before reasoning** | Facts must be looked up, not retrieved by similarity or inferred |
| Many related records about one entity, low latency | **Data graph** | One pre-joined JSON read instead of several lookups |
| Search across several indexes with one ranking | **Ensemble retriever** | Parallel search + cross-source rerank; costs more compute |
| React to data changes (cart abandoned, usage drop) | **Data Cloud-triggered flow** that starts an agent or action | Autonomous, event-driven agent runs |
| External vector DB | Flow or Apex action calling it via Named Credential | Not a native retriever |

Rule: **structured facts → actions and variables; unstructured knowledge → retrievers.** Don't
vectorise a customer table so the LLM can "find" an account balance.

## 3. Agentforce Data Library (fast path)

Three source types (`sf agent adl create --source-type …`, CLI ≥ 2.139.6):

| Source | Use when | Provisioning |
|---|---|---|
| `sfdrive` (File) | Uploading PDF/TXT/HTML (≤ 100 MB each, ≤ 1000 files per library) | Upload → index → READY, 2–10 min |
| `knowledge` | The org has Salesforce Knowledge articles | Indexes from `Knowledge__kav`, 2–10 min; **two primary index fields are immutable after create** |
| `retriever` | You already built an active custom retriever | Ready immediately |

The Setup UI also offers a web-search source (verify in your org).

Stages to watch (`sf agent adl status`): `DATA_LAKE_OBJECT → DATA_MODEL_OBJECT → SEARCH_INDEX →
INDEXING → RETRIEVER` (Knowledge adds `DATA_STREAM` first). Treat **`retrieverId` non-null** as
the readiness gate; the top-level status can lag 10–30 min. For Knowledge, wait another ~10 min
and send a real query: the library can show READY with zero chunks on day 0.

File libraries can use **Intelligent Context** (`--index-mode enhanced`): LLM-based processing
of tables, images and layout. It is substantially more expensive per file. Use it only if the
corpus really has complex tables or figures; otherwise use `basic` ("Text Only").

### Wiring an ADL into Agent Script

```agentscript
knowledge:
    rag_feature_config_id: "ARFPC_1JDxx0000000001AAA"   # "ARFPC_" + libraryId, NOT the raw id
    citations_enabled: True
    citations_url: ""

subagent product_faq:
    description: "Answer product, policy and how-to questions from the knowledge base"
    reasoning:
        instructions: ->
            | For every substantive question, call AnswerQuestionsWithKnowledge before answering.
              Answer only from the returned knowledge summary. If it is empty, say the knowledge
              base has no answer and offer a case. Cite only returned sources.
        actions:
            AnswerQuestionsWithKnowledge: @actions.AnswerQuestionsWithKnowledge
                with query = ...
                with citationsUrl = ...
                with ragFeatureConfigId = ...
                with citationsEnabled = ...
    actions:
        AnswerQuestionsWithKnowledge:
            description: "Answers questions about policies, procedures and products by searching knowledge."
            inputs:
                query: string
                    is_required: True
                    is_user_input: True
                citationsUrl: string = @knowledge.citations_url
                ragFeatureConfigId: string = @knowledge.rag_feature_config_id
                citationsEnabled: boolean = @knowledge.citations_enabled
            outputs:
                knowledgeSummary: object
                    complex_data_type_name: "lightning__richTextType"
                    is_displayable: True
                citationSources: object
                    complex_data_type_name: "@apexClassType/AiCopilot__GenAiCitationInput"
                    is_displayable: False
            target: "standardInvocableAction://streamKnowledgeSearch"
            source: "EmployeeCopilot__AnswerQuestionsWithKnowledge"
```

The `knowledge:` block must come before `language:`. Without it, `@knowledge.*` references fail
to compile. For the full verbatim action block, take it from `forcedotcom/sf-skills` →
`skills/agentforce-generate/references/data-library-reference.md`, not from memory.

## 4. Custom search index + retriever (control path)

### Search index

| Setting | Options / default | Guidance |
|---|---|---|
| Search type | `VECTOR`, `HYBRID` (Easy Setup defaults to HYBRID) | Default to **HYBRID**. Dense-only misses SKUs, error codes and names (`agent-rag` rule 5) |
| Chunking | `passage_extraction` (e.g. `max_tokens` 512, `strip_html`), section-aware for documents | Tune on a labelled set; chunk size is a hyperparameter |
| Embedding model | `e5_large_v2` (1024 dims, 512-token limit) is the common default; others exist (verify in your org) | Changing the model means a full re-index |
| Index / similarity | HNSW, COSINE | Leave defaults |
| **Filtering (prefilter) fields** | Chosen at creation | **Can't be added after save.** Pick every field you'll filter on (language, product, region, data source, visibility) up front |
| Ranking configurations | Optional | Recency or field boosts |

Easy Setup in the UI creates the chunk DMO and index DMO for you. A raw `POST /ssot/search-index`
exists, but it needs those DMOs and configs to already exist. Check readiness with
`GET /services/data/vXX.0/ssot/search-index/<developerName>` until `runtimeStatus == "READY"`
(uppercase).

### Retriever

Built in **Einstein Studio → Retrievers → New → Individual Retriever** (or Ensemble):
- Source: DMO + search index, plus data space.
- Filters: static (`Is Active = TRUE`) or **dynamic** (`Learning Program Id = {!$Input_Var}`).
- Results: number of results, return fields (including related-DMO lookups) and citations.
- Then **Activate**.

- API names are **auto-generated and non-deterministic**. The parent is `<Name>_1Cx_<hash>`; the
  version config is `_1Cy_…`. Read the parent name back from the org; never hard-code it across
  orgs.
- API surface: Salesforce's own sample kits create retrievers with
  `POST /services/data/v63.0/ssot/machine-learning/retrievers`. Another official skill reports
  that the retriever routes 404. Treat retriever creation as UI-first and **verify the API in your
  org**. A retried POST creates a duplicate.
- One default retriever is created per index; build custom ones when you need filters or specific
  return fields.

### Consuming a retriever

| Consumer | How |
|---|---|
| Agent, simplest | Wrap the retriever in an ADL (`--source-type retriever`) → `knowledge:` block + `AnswerQuestionsWithKnowledge` |
| Prompt template | Insert Resource → retriever. This produces `{!$EinsteinSearch:<RetrieverApiName>.results}` in the content plus a `templateDataProviders` entry `invocable://getEinsteinRetrieverResults/<RetrieverApiName>` with `searchText`, `outputFieldNames` and result count. The agent calls the template via `prompt://` (see `prompt-builder.md`) |
| Agent Script action directly | `target: "retriever://<RetrieverApiName>"` is a valid scheme in the compiler (verify the I/O contract in your org) |
| SQL / Apex / external | `vector_search()` / `hybrid_search()` SQL (section 6) |

Deploying grounding is mostly not metadata-driven: data libraries, search indexes and retrievers
generally can't be built by `sf project deploy` (`DataObjectSearchIndexConf` exists in the
Metadata API, but it doesn't build the index). Plan each org's grounding as scripted
CLI/REST steps plus UI steps, and keep prompt templates org-agnostic with **placeholder retriever
names you substitute at deploy**.

## 5. Customer context, data graphs and events

**Verified customer context injection** (Salesforce's architect guidance): pre-load *specific*
attributes as variables or action inputs instead of letting the LLM infer them.

```agentscript
variables:
    contact_id: linked string
        source: @MessagingEndUser.ContactId
    customer_tier: mutable string = ""
    churn_risk: mutable string = ""

subagent retention:
    description: "Retention"
    reasoning:
        instructions: ->
            if @variables.customer_tier == "":
                # Flow/Apex reading unified profile + CIs
                run @actions.get_customer_profile
                    with contact_id = @variables.contact_id
                    set @variables.customer_tier = @outputs.tier
                    set @variables.churn_risk = @outputs.churn_risk
            | The customer is {!@variables.customer_tier} tier with {!@variables.churn_risk} churn risk.
```

- Only inject the attributes the subagent needs. The whole profile is noise, and PII exposure.
- A profile is only as good as identity resolution. Handle `null` explicitly and don't let the
  model guess.
- Session-start loading is fast but goes stale. Re-fetch with an action when a value can change
  mid-conversation.
- **Data graphs** flatten a root DMO and its related objects into one JSON document. Use them when
  a prompt or action needs many related records for one entity. Field and relationship selection
  is fixed at definition time.
- **Data Cloud-triggered flows** run on DMO or calculated-insight changes. Use them for autonomous
  agents (cart abandonment, payment failure). The payload must carry enough context to act, every
  write must be idempotent (at-least-once delivery), and the flow needs a fault path and a
  dead-letter route.
- **Data actions** push Data Cloud events to targets (webhook, Platform Event, Marketing Cloud).
  **`cdpMlPrediction://`** calls an Einstein Studio predictive model as an agent action (verify
  the I/O in your org).

## 6. Querying Data Cloud (SQL)

Data Cloud SQL is ANSI-style, **not SOQL**. Double-quote table and column names.

```bash
# Sync SQL through the core REST API (CLI handles auth)
cat > q.json <<'JSON'
{ "sql": "SELECT \"ssot__Id__c\", \"ssot__FirstName__c\" FROM \"ssot__Individual__dlm\" LIMIT 10" }
JSON
sf api request rest "/services/data/v66.0/ssot/query-sql?dataspace=default" \
  -X POST -H "Content-Type: application/json" -b @q.json -o <org>
```

`/ssot/query` (older) and `/ssot/query-sql` both exist. Use paginated or async query for large
result sets. Search an index directly with SQL table functions:

```sql
SELECT c."Chunk__c", v."hybrid_score__c", v."SourceRecordId__c"
FROM hybrid_search(TABLE("Product_Docs_index__dlm"), 'reset the router', 'Region__c=''NZ''', 10) AS v
JOIN "Product_Docs_chunk__dlm" AS c ON v."SourceRecordId__c" = c."RecordId__c"
ORDER BY v."hybrid_score__c" DESC LIMIT 10
```

(`vector_search(TABLE(...), 'query', '', k)` has the same shape and returns `score__c`.) Prefilter
only works on fields configured as filter fields at index creation. Use this to **build and
measure a labelled retrieval set** (recall@k per `agent-rag`) outside the agent. Agent trace
tables (`AiAgentSession__dlm` and the rest) are covered in `observability-and-trace-sql.md`.

Inspect schemas with `GET /services/data/vXX.0/ssot/data-model-objects/<name>` and
`/ssot/data-lake-objects/<name>`. Prefer describe over guessing column names.

## 7. Permissions: the silent-empty-results trap

Grounded answers come back **empty, with no error**, if the agent's runtime user lacks any layer:

1. **Data Cloud permission set / PSL** on the Einstein Agent User. The name varies by org shape:
   `DataCloudUser`, `GenieUserEnhancedSecurity`, `GenieDataPlatformStarterPsl`.
2. **Data space scope** on that permission set:
   ```xml
   <dataspaceScopes>
       <dataspaceScope>default</dataspaceScope>
       <dataAccessLevel>ALL</dataAccessLevel>            <!-- NONE | CONTROLLED_BY_PARENT | ALL -->
       <objectAccessLevel>BY_POLICY</objectAccessLevel>  <!-- or ALL_IN_DATASPACE (only with CONTROLLED_BY_PARENT) -->
   </dataspaceScopes>
   ```
   PermissionSet deploys are a **full replace**, so retrieve and edit; don't overwrite.
3. **Object + field read** on every source field. For a Knowledge library that is `Knowledge__kav`
   read and FLS on every indexed and content field, plus the *Allow View Knowledge* permission
   (agent users can't hold a Knowledge licence).
4. **Language match.** Knowledge retrieval filters by article language, so `en_US` articles return
   nothing to an `en_GB` user context.
5. **Data governance policies** (ABAC at object, field and row level) apply to retrieval. Use
   metadata prefilters to keep users out of chunks they shouldn't see. Enforce permissions in the
   index and filter, never in the prompt.

Debug order: run the backing Flow "as another user" (the agent user) → run the index query in SQL
→ check the retriever in Prompt Builder preview → check the agent trace.

## 8. RAG quality, mapped to `agent-rag`

| `agent-rag` concept | Data Cloud lever |
|---|---|
| Labelled retrieval set, recall@k | `hybrid_search()` SQL over the index; 20–50 real questions |
| Hybrid + rerank | `HYBRID` index; ensemble retriever for cross-source rerank |
| Chunking as a hyperparameter | `max_tokens`, section-aware chunking, Intelligent Context for tables |
| Contextual headers | Add title/section fields to the chunked text; return them as fields |
| Metadata filters | Filter fields at index creation + dynamic retriever filters |
| Freshness | Stream schedule, ADL re-index on update; agree a staleness SLA |
| Jargon / renamed products | Map old→new terms before retrieval (Salesforce's own pattern: fetch a term map once into a variable) |
| Empty retrieval | Instruct "say you don't know"; treat low-relevance hits as harmful noise |
| Untrusted retrieved text | Indexed content can carry prompt injection; restrict what gets indexed and what actions run after retrieval (`agent-guardrails`) |

## 9. Cost

Ingestion, unified profiles, search indexing, retrieval and Intelligent Context consume **Data
360 / Flex Credits**. Index and retrieve only what agents use. Prefer a data graph or one action
over several retrievers per turn. Check billing considerations for ADL and the Help Agent
before large uploads.

## Anti-patterns

- Vectorising structured customer records so the model can "look up" facts.
- Building an index without the filter fields you'll need (you can't add them later).
- Hard-coding `_1Cx_…` retriever names or `ARFPC_…` ids across sandboxes and prod.
- Declaring ADL success on `status=READY` without a test query.
- Blaming the LLM for empty answers before checking agent-user Data Cloud permissions, data
  space scope, FLS and language.
- Injecting entire unified profiles into the prompt.
- Org cache, or wide data spaces, holding user-specific data.

## Sources

- Salesforce ADLC skill: `SalesforceAIResearch/agentforce-adlc` → `skills/agentforce-generate/references/data-library-reference.md` (ADL CLI, stages, `ARFPC_`, wiring block, permissions, pitfalls).
- `forcedotcom/sf-skills` → `education-cloud-student-recruitment-agent-configure/references/grounding.md` (Easy Setup hybrid index, E5 Large V2, filter fields immutable, retriever names, `{!$EinsteinSearch…}` and `getEinsteinRetrieverResults` wiring, no deploy path for grounding); `platform-dataspace-access-configure` (`dataspaceScopes`); `agentforce-observe/references/ahm-alerts.md` (`/ssot/query-sql`); `data360-schema-get`.
- `salesforce-misc/Data360AgentforceSolutionKitRetail` → `.claude/skills/create-individual-retrievers`, `prompt-template-add-retriever`, `intelligent-context` (retriever REST, `1Cx`/`1Cy` names, placeholder pattern).
- `Jaganpro/sf-cli-plugin-data360` `src/shared/data360/sql.ts` and `sf-datacloud-*` community skills (vector/hybrid SQL, search-index JSON, prefilter rule).
- `salesforce/agentscript` → `dialect/agentforce` schema (`knowledge:` block, `retriever://`, `cdpMlPrediction://`), docs example `ascript-example-rag-jargon.md`, pattern `ascript-patterns-fetch-data.md`.
- Salesforce Architects, *Agentic Integration Patterns* (knowledge grounding, verified customer context, event-driven execution): https://architect.salesforce.com/docs/architect/fundamentals/guide/agentic-integration-patterns
- Trailhead, *Data 360-Powered Agentforce*: https://trailhead.salesforce.com/content/learn/modules/data-cloud-powered-agentforce/enable-trusted-agents-with-data-cloud
- Help: *Retrieval Augmented Generation* (`ai.generative_ai_rag.htm`) and *Agentforce Data Library* (`ai.data_library_parent.htm`) article index.
