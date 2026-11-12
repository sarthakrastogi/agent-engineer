# Multi-agent on AgentCore, A2A and AWS Agent Registry

First decide whether you need more than one agent: `agent-design` → `references/multi-agent.md`
(most tasks don't; each agent adds cost, latency and a handoff to get wrong). This file is how to
build it on AgentCore once that's justified.

## Pick the wiring

| Situation | Pattern |
|---|---|
| Child task, same tools and trust, no separate deploy | In-process: Strands agent-as-tool, or harness built-in `subagent` tool (child gets the parent's tools minus `subagent`) |
| Workers owned by other teams, independently deployed | Each worker its own Runtime; orchestrator calls them (below) |
| Worker should look like a tool to any MCP client, with Policy per caller | Worker as MCP server on Runtime → Gateway `mcpServer` target |
| Peer agents or third-party agents speaking A2A | Runtime with `--protocol A2A`; front with Gateway passthrough (`protocolType: A2A`) or Runtime target |
| Fixed sequence, retries, waits for humans | Step Functions (`InvokeHarness` / Runtime tasks, or the `states` Gateway connector), not an LLM orchestrator |
| Harness as orchestrator | Harness can't host a custom orchestrator or routing-mode multi-agent; use `subagent`, or tools that call workers. See `harness.md` |

## Calling a worker

Options, from simplest to most governed:

1. **Direct `InvokeAgentRuntime`** from orchestrator code (SigV4 or bearer). Simple, but every
   orchestrator needs IAM on every worker and there is no central policy or audit.
2. **Gateway HTTP Runtime target** (`http.agentcoreRuntime`, gateway without `protocolType: MCP`):
   orchestrator POSTs `/{targetName}/invocations`. Central inbound auth, interceptors, rules,
   rate limits, observability. Path-routed, **not** listed as an MCP tool.
3. **Gateway MCP server target** pointing at a Runtime that serves MCP: the worker's tools appear
   in the aggregated `tools/list`, filtered per caller by Policy, findable by semantic search.
   Outbound SigV4 with `iamCredentialProvider.service: "bedrock-agentcore"`, or OAuth.

To expose a whole agent (not its tools) to a tool-calling orchestrator, wrap it: an MCP server
with one tool `ask_<worker>` that calls the agent, or a Lambda target that calls
`InvokeAgentRuntime` / `InvokeHarness`. No harness-specific Gateway target type is documented
(unverified); invoke harnesses via `InvokeHarness`.

Lock workers so they can't be called around the gateway: IAM runtimes get a resource policy
allowing only the gateway role; JWT runtimes set
`customJWTAuthorizer.allowedWorkloadConfiguration.hostingEnvironments: [{arn: <gatewayArn>}]`
(the only supported hosting environment is a Gateway, so "only callable by orchestrator X"
requires a gateway in between). Details: `gateway.md`, `identity.md`.

## Sessions across agents

- Each `runtimeSessionId` (33-256 chars; the CLI accepts 33-100) gets its own microVM and context. The orchestrator owns
  the mapping: derive a deterministic ID per (task, worker), e.g. `f"{task_id}-{worker}"`
  padded to 33+, so retries reuse the worker's context instead of cold-starting a new one.
- Fan-out multiplies sessions, concurrency quota use and cost; cap parallel workers.
- A second call into a session that is provisioning returns `RetryableConflictException`
  (A2A: JSON-RPC `-32054`, HTTP 409). Retry with backoff.
- Workers must not report `HealthyBusy` forever or set `time_of_last_update` on every `/ping`:
  sessions never idle out and run to `maxLifetime` (8 h). `serve_a2a` with Strands A2A
  streaming was reported to never idle out (sdk-python #583, open): watch `ActiveSessionCount`.
- Shared state goes in Memory or S3 keyed by task, not in a worker's session filesystem.

## Identity propagation

| Hop | What carries identity | Notes |
|---|---|---|
| Gateway → Runtime → Gateway | Platform-managed `X-Amz-Bedrock-AgentCore-Identity-WAT` (workload identity chain) | Docs: don't set, read or strip it. SDK docstring says raw-HTTP MCP calls from agent code to a gateway must attach it manually: conflict, test your path |
| User → orchestrator → worker (OAuth) | User JWT | Gateway `JWT_PASSTHROUGH` to Runtime is for testing only; downstream SaaS calls use OBO (`TOKEN_EXCHANGE`) |
| Agent code needs the user token | `--request-header-allowlist Authorization` on the Runtime | Read it from the request context; validate audience before reuse |
| SigV4 caller with app-managed user IDs | `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` | Unverified opaque value; requires `bedrock-agentcore:InvokeAgentRuntimeForUser`. Grant to one trusted principal, derive the value from the authenticated caller, deny it elsewhere |

- With SigV4 between agents, the worker sees the orchestrator's role, not the user. Per-user
  authz at the worker then needs a token (JWT/OBO) or a verified user ID, never a prompt field.
- Per-agent tool scoping: one gateway, one policy engine, a principal per agent role; each
  agent's `tools/list` contains only what its principal may call (`policy-and-guardrails.md`).
- A worker's output is untrusted input to the orchestrator: don't let it trigger write tools
  without the orchestrator's own checks (`agent-guardrails`).

## A2A on Runtime

- Contract: JSON-RPC 2.0 at `POST /` on port 9000 (override with `protocolConfiguration`),
  Agent Card at `GET /.well-known/agent-card.json`, `GET /ping`, ARM64 container, host `0.0.0.0`.
  Payloads pass through unchanged; AgentCore adds `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`.
- Deploy (CLI v1):
  ```bash
  agentcore add runtime --name Worker --language python --protocol A2A \
    --authorizer-type CUSTOM_JWT --authorizer-configuration '{"customJwtAuthorizer":{...}}' \
    --request-header-allowlist Authorization
  agentcore dev            # local on :9000
  agentcore deploy
  ```
  ```python
  from strands.multiagent.a2a.executor import StrandsA2AExecutor
  from bedrock_agentcore.runtime import serve_a2a
  serve_a2a(StrandsA2AExecutor(agent_factory=lambda ctx_id: build_agent()))
  ```
  `serve_a2a` serves the card and `/ping`, reads `AGENTCORE_RUNTIME_URL`, propagates headers.
- Deployed card URL:
  HTTPS `bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded ARN>/invocations/.well-known/agent-card.json`.
  The card's `url` must be the Runtime URL, not `localhost`; hand-rolled servers must set it.
- Errors come back with real HTTP status codes (400/404/409/429/424/500) and a JSON-RPC error
  body (403 is a plain HTTP error), unlike the A2A convention of HTTP 200. Parse the body on non-2xx.
- Versions: the Runtime tutorial pins `a2a-sdk[all]~=0.3.26` with `bedrock-agentcore[a2a]`; the
  SDK also ships an `a2a-v1` extra for a2a-sdk 1.x. Older SDKs broke on a2a-sdk 1.0
  (`No module named 'a2a.server.apps'`, #438). Pin both together.
- a2a-sdk 1.x follow-up messages on an `input-required` task ran with the first request's
  context, i.e. the first caller's token (#690, fixed): upgrade before multi-turn A2A with auth.
- Gateway rule `matchPaths` reserves `/a2a`; name A2A targets something else.

## AWS Agent Registry

A governed catalog of agents, MCP servers, skills and custom resources. It is discovery and
governance only: a record grants no access to the agent behind it; callers still need that
agent's auth.

**Namespace.** GA 2026-08-31 in its own namespace: CLI `aws agent-registry-control` (control)
and `aws agent-registry` (discovery), endpoints
`agent-registry.<region>.api.aws` / `agent-registry-control.<region>.api.aws`, IAM prefix
`agent-registry:`, principal `agent-registry.amazonaws.com`, managed policy
`AgentRegistryFullAccess`, CloudWatch `AWS/AgentRegistry`, EventBridge source
`aws.agent-registry`. The preview `bedrock-agentcore` registry namespace shut down
**2026-10-30** with its data; anything still calling it is broken. Identity resources (workload
identities, credential providers) stay in `bedrock-agentcore`.

**APIs.** Control: `CreateRegistry`, `Get/Update/Delete/ListRegistr(y|ies)`,
`CreateRegistryRecord`, `Get/Update/Delete/ListRegistryRecord(s)`,
`SubmitRegistryRecordForApproval`, `UpdateRegistryRecordStatus`. Discovery (approved only):
`SearchDiscoverableRegistryRecords`, `ListDiscoverableRegistryRecords`,
`GetDiscoverableRegistryRecord`, `BatchGetDiscoverableRegistryRecord`, `InvokeRegistryMcp`.
`SearchRegistryRecords` is the **preview** name (replaced by `SearchDiscoverableRegistryRecords`);
the MCP tool `search_registry_records` became `search_discoverable_registry_records`.

**Records.** Required `name` (dedup key; `name`+`recordVersion` unique) and `recordType`
(`AGENT`, `MCP`, `SKILL`, `CUSTOM`). One primary descriptor: AGENT `a2aAgentCard` (validated
against the A2A schema; `dataSchemaVersion` is `0.3`, not `0.3.0`), MCP `mcpServer`, SKILL
`agentSkillsDefinition`, or `custom`. `source.fromUrl` syncs MCP/AGENT records from a live
endpoint (with a credential provider if needed); SKILL records can't sync.

**Search.** `searchQuery` 1-256 chars, `registryIds` exactly one registry, `maxResults` 1-20
(default 10), `filters` with `$eq`/`$ne`/`$in`, one level of `$and`/`$or`, <=10 conditions,
fields `name`, `recordType`, `recordVersion`, `customMetadata.<field>`. Hybrid semantic plus
keyword. Indexing is eventually consistent: newly approved records appear in discovery after a
delay; control-plane `GetRegistryRecord` sees them immediately.

**Governance.**
- Lifecycle: DRAFT → `SubmitRegistryRecordForApproval` → PENDING_APPROVAL → curator
  `UpdateRegistryRecordStatus` → APPROVED / REJECTED; DEPRECATED is terminal from any state.
- Editing an APPROVED record makes a new DRAFT revision; the approved one stays discoverable
  until the new one is approved. Reject to hide temporarily; deprecate only to retire.
- `approvalConfiguration.autoApprovalRules: ["APPROVE_ALL"]` auto-approves; `[]` or omitted
  means manual. Use manual for prod registries; auto for dev.
- Inbound auth (IAM or JWT) is fixed at create, and a JWT `discoveryUrl` can't be edited.
  Console search works only for IAM-auth registries.
- Organizations auto-detection: one org-scoped registry per Region (delegated admin account)
  catalogs member-account Runtimes and Gateways; detected records still go through approval,
  and refreshes create new DRAFT revisions.
- RAM sharing: default `AWSRAMDefaultPermissionAgentRegistryReadOnly` (no MCP invoke);
  `...ForConsumer` (adds MCP endpoint), `...ForPublisher`, `...ForAdmin` (approve/reject).
  CFN in a consumer account needs Publisher or Admin. EventBridge events per state change: wire
  curator notifications and CI checks to `Registry Record State changed to Pending Approval`.

**IaC.** CloudFormation `AWS::AgentRegistry::Registry`, `AWS::AgentRegistry::RegistryRecord`;
Terraform `aws_agentregistry_registry` (no record resource found). Quotas: 10 registries per
account per Region, search 10 TPS. Price: 5,000 records/month free then $0.40/1k; 1M searches
free then $0.02/1k; Get+List 2M free then $0.004/1k.

**Agents that pick tools from the registry at runtime** are loading third-party tools
dynamically: apply MCP trust rules (pinning, provenance, tool poisoning) from `agent-guardrails`
→ `approvals-and-permissions.md`, and restrict the registry they search to an approved,
manually curated one.

## Checklist

- [ ] Multi-agent justified per `agent-design` → `multi-agent.md`; single agent tried first
- [ ] Workers behind a Gateway and locked to it (resource policy or `allowedWorkloadConfiguration`)
- [ ] Deterministic per-worker session IDs; parallelism capped; idle timeouts actually fire
- [ ] User identity reaches workers as a validated token or verified user ID, never a prompt field
- [ ] A2A cards advertise the Runtime URL; a2a-sdk and `bedrock-agentcore` extras pinned together
- [ ] Registry calls use the agent-registry endpoints and `SearchDiscoverableRegistryRecords`
- [ ] Prod registry has manual approval and an EventBridge-driven review step
