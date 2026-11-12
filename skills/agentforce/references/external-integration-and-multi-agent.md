# External integration and multi-agent

How an Agentforce agent reaches systems outside Salesforce (outbound), how outside agents and apps
reach Salesforce (inbound), and how agents delegate to other agents. Tool design rules from
`agent-tools` apply to every action here; permissions and injection rules from `agent-guardrails`
apply to every write.

## 1. Pick the path

| Need | Path | Agent Script target |
|---|---|---|
| One REST API with an OpenAPI spec | **External Service** → generated invocable operations | `externalService://<Service>.<operationId>` |
| A REST call plus Salesforce logic, admin-owned | **Flow** with an HTTP Callout action | `flow://<Flow>` |
| Complex auth, parsing, retries, unit tests with mocks | **Apex `@InvocableMethod`** doing `callout:` | `apex://<Class>` |
| OmniStudio org, multi-source orchestration | **Integration Procedure** HTTP Action (`integration-procedures.md`) | `integrationProcedureAction://<IP>` |
| An API managed in MuleSoft Anypoint | **API Catalog** (Anypoint source) → External Service | `externalService://…` |
| A third-party or internal **MCP server** | Register in API Catalog / Agentforce Registry, allowlist tools | `mcpTool://<Server>/<tool>` |
| A Salesforce-hosted MCP server's tools | Same, platform server | `platformMcpTool://<Server>/<tool>` |
| Code on Heroku | **Heroku AppLink** (registers as External Service, provider `Heroku`) | `externalService://…` (verify in your org) |
| Another Agentforce agent in the same org | **Connected subagent** | `connected_subagent X: target: "agent://<Agent>"` |
| An agent on another platform/org | **A2A**, usually via MuleSoft Agent Broker + gateway | see §8 |
| Outside app → your agent | **Agent API** (headless sessions) | `connections-and-channels.md` |
| Outside AI client (Claude, ChatGPT, Cursor) → your Salesforce data/logic | **Salesforce Hosted MCP Servers** | §6 |

Default to the **least powerful** path that works: External Service or Flow before custom Apex.
Prefer narrow, task-shaped operations over a generic "call API" or "run SOQL" tool.

## 2. Named Credentials + External Credentials

Every outbound call goes through a Named Credential. Never put endpoints, keys or tokens in Flow,
Apex, IP properties or prompt text.

```text
External Credential  — how to authenticate
  ├─ protocol: OAuth 2.0 (client credentials / browser / JWT bearer), JWT, JWT token exchange,
  │            AWS Signature V4, Custom (headers/API key), No Auth
  └─ principals: Named Principal (one shared identity)  |  Per-User Principal (each user's own token)
Named Credential     — where: base URL + reference to the External Credential
Permission set       — External Credential Principal Access → assigned to whoever runs the call
```

- **Agent runtime identity decides which principal applies.** A *service* agent runs as its
  Einstein Agent User (`default_agent_user`), so give that user the permission set with principal
  access, almost always a **Named Principal**. An *employee* agent runs as the signed-in user, so
  per-user principals are possible: each user must have authenticated, otherwise the action fails
  at runtime (verify in your org).
- The missing principal-access grant is the most common cause of "works in Flow debug as admin,
  fails in the agent". Debug the Flow *as the agent user*.
- Apex: `req.setEndpoint('callout:Billing_API/v1/accounts/' + EncodingUtil.urlEncode(id, 'UTF-8'));`
  The auth header is injected; custom headers can use `{!$Credential.<ExternalCredential>.<param>}`.
- Least privilege: one External Credential per downstream system and permission scope, with OAuth
  scopes trimmed. Read-only agents get a read-only principal.
- Separate credentials per environment (sandbox, prod). The metadata deploys, but **secrets are
  entered per org** (UI or Connect API), never committed.

## 3. External Services (OpenAPI → actions)

Register an OpenAPI 2.0/3.0 spec against a Named Credential (Setup → External Services, or the
`ExternalServiceRegistration` metadata). Each operation becomes an invocable action usable in
Flow, Apex (`ExternalService.<Name>`), and as an agent action.

```agentscript
subagent network_support:
    description: "Network support requests"
    actions:
        check_outage:
            description: "Checks for a known network outage at a street address. Use before troubleshooting a customer's connection. Read-only."
            inputs:
                address: string
                    description: "Street address with suburb and city, e.g. '12 Queen St, Auckland Central, Auckland'"
                    is_required: True
            outputs:
                outageActive: boolean
                estimatedRestore: string
                    description: "ISO timestamp, empty if unknown"
            target: "externalService://NetworkStatus.getOutageByAddress"
```

Shape the spec for the model, not just for the API:
- `operationId` reads as a task (`getOutageByAddress`, not `get_v2_res_3`).
- Give every operation and parameter a `description`. Mark required fields. Use enums for fixed
  values and `format` for dates.
- Expose only the operations the agent needs. Register a trimmed spec, not the vendor's 400
  endpoints (`agent-tools` rule 1).
- Keep responses small. If the API returns a huge object, put a Flow or Apex wrapper in front that
  returns the five fields that matter.

Supported: JSON request/response, path/query/header params, `$ref`. Limited: multipart uploads,
streaming, very large or exotic schemas. `registrationProviderType` shows the origin: `Standard`,
`Anypoint` (MuleSoft API Catalog), `Heroku` (AppLink), `ApexRest`, `AuraEnabled`, `NamedQuery`,
`ExternalConnector`, and from API 66.0 `AgentToAgent`.

## 4. Apex callouts behind an action

Use Apex when you need auth flows, pagination, retries, aggregation or response trimming:

```apex
public with sharing class OutageLookup {
    public class Req { @InvocableVariable(required=true description='Street address incl. suburb and city') public String address; }
    public class Res {
        @InvocableVariable public Boolean outageActive;
        @InvocableVariable public String estimatedRestore;
        @InvocableVariable public String errorMessage;   // always set on failure; never throw to the agent
    }
    @InvocableMethod(label='Check Outage' description='Known outage at an address. Read-only.')
    public static List<Res> run(List<Req> reqs) {
        List<Res> out = new List<Res>();
        for (Req r : reqs) {
            Res res = new Res();
            HttpRequest req = new HttpRequest();
            req.setEndpoint('callout:Network_Status/outages?address=' + EncodingUtil.urlEncode(r.address, 'UTF-8'));
            req.setMethod('GET');
            req.setTimeout(10000);   // keep well under the agent's patience; ms
            try {
                HttpResponse resp = new Http().send(req);
                if (resp.getStatusCode() == 200) {
                    Map<String, Object> b = (Map<String, Object>) JSON.deserializeUntyped(resp.getBody());
                    res.outageActive = (Boolean) b.get('active');
                    res.estimatedRestore = (String) b.get('restoreAt');
                } else if (resp.getStatusCode() == 404) {
                    res.errorMessage = 'Address not recognised. Ask the customer to confirm suburb and city.';
                } else {
                    res.errorMessage = 'Outage service unavailable (' + resp.getStatusCode() + '). Offer to raise a case.';
                }
            } catch (CalloutException e) {
                res.errorMessage = 'Outage service timed out. Offer to raise a case.';
            }
            out.add(res);
        }
        return out;
    }
}
```

Platform rules that bite:
- Agents call actions with one request, but code bulk-safe anyway.
- Standard callout limits apply: about 100 callouts per transaction, a per-callout timeout of up
  to 120 s, and a total-time cap.
- DML before a callout in the same transaction fails ("uncommitted work pending"). Call first,
  then write, or go async.
- Return the error as data. An unhandled exception shows the agent a generic failure it can't
  recover from.
- Writes are idempotent: pass an idempotency key or external reference so a retried turn doesn't
  double-charge or double-create.
- Use Queueable / Platform Events for fire-and-forget. The agent should confirm *dispatch*, not
  *completion*, and return a reference ID.

## 5. MuleSoft

- **API Catalog with Anypoint source:** APIs governed in Anypoint Exchange can be brought into the
  Salesforce API Catalog and registered as External Services, so the agent calls the governed API
  and MuleSoft handles legacy systems, transformation and policy. "MuleSoft for Agentforce" also
  lets you annotate API specs with agent-facing topic/action metadata (verify current naming and
  steps in your org).
- **MuleSoft MCP servers** wrap legacy or proprietary systems as MCP tools (consumed per §6).
- **Gateways:** MuleSoft's gateway (Omni Gateway / AI Gateway in current docs) is the enforcement
  point for MCP, A2A and API traffic: auth, rate and spike limits, PII detection, schema
  validation, logging. Salesforce's architecture guidance puts it **in front of any inbound A2A**
  and any unmediated external MCP. *Trusted Agent Identity* / OAuth OBO policies carry the end
  user's identity across agent chains.

## 6. MCP

### Agentforce as MCP client (agent calls external tools)

1. **Register the server** in the API Catalog / Agentforce Registry: Setup UI, or
   `sf agent mcp create -n MyServer --server-url https://… [--auth-type OAUTH --identity-provider … --client-id … --client-secret - --scope …] -o org --json`.
   The `sf agent mcp` commands are **developer preview**, so flags and output may change. Pipe
   secrets through stdin.
2. **Fetch and allowlist assets**: `sf agent mcp fetch -i <0Le…> --json`, then
   `sf agent mcp asset replace -i <0Le…> --assets '{"assets":[{"name":"McpTool__getTickets","active":true}]}'`.
   Replace is a **full replacement**: anything omitted is deactivated. Review each tool one by one
   and flag destructive or broad tools.
3. **Add the tool as an agent action** (Builder: add MCP tool action; Script:
   `target: "mcpTool://MyServer/getTickets"`; Salesforce-hosted servers use `platformMcpTool://`).
   Write a proper `description` and declare inputs/outputs.

Rules:
- Tool descriptions are the agent's only basis for choosing, so make them intent-based ("find a
  driver's unpaid tickets"), not "calls endpoint".
- Servers often don't return output schemas or annotations. Don't rely on them, and declare
  outputs yourself.
- Only connect remote, vetted servers (no local binaries). Put a gateway in front of third-party
  servers. Treat every tool result as **untrusted input** (`agent-guardrails`).
- Setup also offers server risk scoring and out-of-sync handling. Re-fetch after the server
  changes its tools.

### Salesforce Hosted MCP Servers (outside AI clients call Salesforce)

GA. Lets Claude, ChatGPT, Cursor, Postman and other clients call org data and logic. **No LLM runs
on the Salesforce side**: the server is deterministic.

| Topic | Fact |
|---|---|
| Standard servers | `platform/sobject-reads` (read-only, start here), `platform/sobject-all` (CRUD + prompts), others listed in the developer servers reference |
| Custom servers | `McpServerDefinition` metadata. Tools are backed by global Apex `@InvocableMethod` (`aa:apex-<Class>`), autolaunched Flows (`fa:flow-<Flow>`), Prompt Builder templates (`pr:<Template>`), Named Queries, Apex REST. Set `readOnly`/`destructive`/`idempotent`/`openWorld` honestly |
| Enablement | Every server is **disabled by default**; enable per server in Setup → API Catalog → MCP Servers (≤ 2 min to take effect) |
| Auth | **External Client App only** (no Connected Apps); OAuth **authorization code + PKCE**; scopes `mcp_api` + `refresh_token`; no client credentials, no service accounts, no DCR |
| Identity | The client acts **as the authenticated user**: CRUD, FLS, sharing, audit trail all apply |
| URLs | `https://api.salesforce.com/platform/mcp/v1/<server>`; sandbox `…/v1/sandbox/<server>`; Experience Cloud domains not yet supported |
| Gotchas | ECA propagation up to 30 min; `mcp-remote` bridge unsupported; tool calls consume org API quota; ECAs can't be created in scratch orgs (package them from a Dev Hub) |

To give an outside agent *less* than the user's access: use the read-only server, a custom server
with **Named Query** tools, narrow Apex/Flow tools, and ECA → permission set assignment. Use one
ECA per client.

## 7. Agent API (inbound, brief)

External systems create a session, send messages (sync or SSE stream) and end the session. They
authenticate with OAuth client credentials against My Domain. Pass context as **session
variables at session creation**, not inside message text, and always close sessions (concurrency
limits). Details are in `connections-and-channels.md`.

## 8. Multi-agent

### Same org: connected subagents (SOMA)

Salesforce calls this Single-Org Multi-Agent Orchestration. An orchestrator agent delegates to
other Agentforce agents declared as `connected_subagent`:

```agentscript
connected_subagent Billing_Agent:
    target: "agent://Billing_Agent"          # agentforce:// accepted for backwards compatibility
    label: "Billing Agent"
    description: "Handles invoices, payment plans and billing disputes"   # routing contract
    loading_text: "Connecting you to billing..."
    delegate_escalation: True     # True (default): escalate via the subagent's outbound flow/message; False: orchestrator's
    inputs:
        customer_id: string = @variables.customer_id
    after_response: ->
        set @variables.billing_done = True

start_agent triage:
    description: "Triage"
    reasoning:
        actions:
            to_billing: @connected_subagent.Billing_Agent
                description: "Billing questions"
```

- Guidance: up to **~7 connected subagents** and **one delegation layer** (orchestrator →
  subagent). Deeper chains make latency untenable.
- Only the orchestrator escalates to humans. `delegate_escalation` picks whose routing applies.
- Bound and LLM-filled inputs on connected agents are only partly supported; compiler fixtures
  still mark `with` bindings as TODO (verify in your org).
- A HyperClassifier router can't delegate to connected agents.
- Prefer a **single agent with more subagents** until domains genuinely need separate owners,
  permissions or release cycles (`agent-design` → `references/multi-agent.md`).

### Cross-platform: A2A

- **Outbound** (Agentforce calls a remote A2A agent): External Services has an `AgentToAgent`
  registration type (API 66.0+), and connected agents default their invocation type to
  `externalService`. Native A2A support has been pilot/beta, so confirm availability and setup in
  your org.
- **Inbound** (outside agents call Agentforce):
  - Same org: no protocol needed; use a connected subagent.
  - Cross-org or external caller: put a **MuleSoft Agent Broker** behind a gateway that verifies
    the caller (JWT/OAuth), rate-limits and validates payloads. The Agent API is the lower-level
    alternative.
- A2A tasks have a lifecycle (submitted, working, completed, failed). Use async state updates
  instead of holding connections open for long tasks.

### MuleSoft Agent Fabric (agent networks)

Agent Fabric is MuleSoft's cross-vendor control plane:
- **Registry** of agents, MCP servers and LLMs.
- **Brokers** that orchestrate them over A2A.
- Governance through the gateway.

Brokers are written in the **`agentfabric` dialect of Agent Script**. The dialect docs say beta;
Agent Network 2.0 projects are described as GA on A2A v1.0 (verify for your Anypoint version).

```text
my-network/
  agent-network.yaml    # agentNetwork: 2.0.0 — registry (agents/mcps/llms), connections
  exchange.json         # classifier "agentic-network"
  brokers/<name>.agent  # Agent Script, AGENTFABRIC dialect
```

```agentscript
# @dialect: AGENTFABRIC=1.0
system:
  instructions: "You coordinate IT incident investigations."
config:
  agent_name: "it-investigation"
  default_llm: @llm.fast
llm:
  fast:
    target: "llm://openai_connection"
    kind: "OpenAI"
    model: "gpt-5-mini"
    reasoning_effort: "LOW"
actions:
  hr_agent:
    target: "a2a://hr_agent_connection"
    kind: "a2a:send_message"
  post_slack:
    target: "mcp://slack_mcp_connection"
    kind: "mcp:tool"
    tool_name: "send-message"
    inputs:
      channel: string = "it-incidents"
      message: string
trigger inbound:
  kind: "a2a"
  target: "brokers://it-investigation/a2a"
  # on_message must be a fixed transition
  on_message: -> transition to @router.classify
```

| Node | LLM? | Use |
|---|---|---|
| `subagent` | yes, with tools | Reasoning / classification with actions; human-in-the-loop |
| `orchestrator` | yes, with tools | ≥ 2 actions that jointly serve one outcome |
| `generator` | yes, no tools | One-shot generation / structured output |
| `router` | no | Deterministic branching on known values (`routes` + `otherwise`) |
| `executor` | no | Run one tool with known args (`do:`) |
| `echo` | no | Emit A2A status/artifact events to the caller |

- Put hard constraints in **routers**, not prompt sentences.
- Always set `reasoning.max_number_of_loops` (default 25), `max_consecutive_errors` and
  `task_timeout_secs`.
- Tooling: the Anypoint CLI Agent Fabric plugin (`agent-network project create`, validate,
  publish, deploy) or the MuleSoft MCP server.

### AgentExchange

Salesforce's marketplace for partner agents, actions, templates and MCP servers. Treat installs as
third-party code and tools:
- Review the permission sets they add and the data they reach.
- Allowlist their tools explicitly.
- Run your own evals before adding them to a production agent.

## 9. Security checklist for every integration

- [ ] Named/External Credential, least-privilege scopes, per-environment secrets.
- [ ] Principal access granted to the agent's *runtime* user, and nothing broader.
- [ ] Read vs write separated. Writes are idempotent, narrow, and confirmation-gated
      (`require_user_confirmation: True`) where the risk warrants it.
- [ ] Every action returns explicit success/failure with a recoverable message.
- [ ] Timeouts set below the conversational budget; long work is async with a reference ID.
- [ ] Tool results, retrieved text and inbound A2A payloads treated as untrusted. Check the lethal
      trifecta (private data + untrusted content + exfiltration channel) per `agent-guardrails`.
- [ ] Invocations logged with session ID, sanitised inputs and outcome.
- [ ] Gateway in front of third-party MCP and inbound A2A.

## Anti-patterns

- A generic `http_request`, `run_soql` or "call any MCP tool" action.
- Registering a vendor's full OpenAPI spec so the agent sees hundreds of operations.
- Secrets in Flow variables, custom labels, prompt text or Agent Script.
- Testing callouts only as an admin, then finding the agent user lacks principal access.
- `sf agent mcp asset replace` with a partial list, silently deactivating tools.
- Multi-layer agent chains (orchestrator → agent → agent) for latency-sensitive chat.
- Treating dispatch of an async action as proof the work completed.

## Sources

- `salesforce/agentscript`:
  - `dialect/agentforce/src/lint/passes/action-target.ts` (valid schemes incl. `externalService`, `mcpTool`, `platformMcpTool`, `api`, `apexRest`).
  - `dialect/agentscript/src/schema.ts` `ConnectedSubagentBlock` (`target`, `delegate_escalation`, `after_response`, `loading_text`, `inputs`), `dialect/agentforce/src/lint/passes/connected-agents/target-validation.ts`.
  - `packages/compiler/src/nodes/compile-connected-agent-node.ts`; fixtures `connected_subagent_tool.agent`, `delegate_escalation.agent`, `edge_action_mcp_targets.agent`, `edge_action_api.agent`.
  - `apps/docs/docs/dialects/agentfabric/index.md` (AGENTFABRIC dialect, llm/a2a/mcp actions, triggers, node types).
- `SalesforceAIResearch/agentforce-adlc` → `references/mcp-management-reference.md` (`sf agent mcp` dev preview, allowlist full replace, ID prefixes), `references/actions-reference.md` (API action architecture, Named Credentials), `references/architecture-patterns.md` (HyperClassifier can't delegate).
- `trailheadapps/agent-script-recipes` → `04_architecturalPatterns/externalAPIIntegration` (Apex/Flow callout actions with `success`/`error_message` outputs).
- `forcedotcom/sf-skills`:
  - `integration-connectivity-generate/references/named-credentials-guide.md`, `external-services-guide.md`, `assets/external-credentials/*`.
  - `experience-app-mcp-generate/references/mcp-objects.md` (`McpServerDefinition`, `aa:`/`fa:`/`pr:` bindings).
  - Metadata API reference JSON `ExternalServiceRegistration.json` (`registrationProviderType` incl. `AgentToAgent` API 66.0, `Anypoint`, `Heroku`, `NamedQuery`) and `GenAiFunction.json` (`invocationTargetType` values).
- `forcedotcom/mcp-hosted` README + wiki (FAQ, Security-and-Permissions, Known-Limitations, Quick-Start-Guide): GA, ECA-only, auth code + PKCE, `mcp_api` scope, disabled by default, user-context permissions, URLs, limits.
- `exuSalesforce/agent-broker-skills` → `build-agent-broker-project` SKILL and `references/gotchas.md` (Agent Network V2 layout, node types, loop/timeout params, auth kinds, Anypoint CLI plugin).
- Salesforce Architects, *Agentic Integration Patterns* (sequential/parallel/async actions, MCP, A2A, SOMA ~7 subagents and single delegation layer, gateway for inbound A2A, Trusted Agent Identity, two-tier credentials): https://architect.salesforce.com/docs/architect/fundamentals/guide/agentic-integration-patterns
- `pozil/agent-api-node-client` README (Agent API session/stream flow); `mvrzan/salesforce-agentforce-a2a-wrapper` README (native A2A described as pilot at the time).
