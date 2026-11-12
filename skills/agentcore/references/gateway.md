# AgentCore Gateway

One managed endpoint in front of tools, other agents and models, with inbound auth, outbound
credential injection, Policy, interceptors, rate limits and WAF. Tool design itself is
`agent-tools`; this file is how to wire it on AgentCore.

## Use it or not

| Situation | Then |
|---|---|
| Several agents or clients share tools; you want one auth, audit and policy point | Gateway, MCP targets |
| Per-user OAuth to SaaS (3LO/OBO) without writing token plumbing | Gateway + Identity credential provider |
| Deterministic per-tool authz (Cedar) or Bedrock Guardrails on tool traffic | Gateway; Policy only enforces on Gateway traffic |
| One agent, two or three in-process tools, no shared auth | Native tools in the agent; skip Gateway |
| Front an existing Runtime agent with central auth/observability | HTTP Runtime target |
| One endpoint for Bedrock/OpenAI/Anthropic with fallback and TPM caps | Inference targets |
| Agent must stream bidirectionally or hold WebSocket | Call Runtime directly; Gateway targets are request/response or SSE |

Each hop adds latency and a failure mode (see Gotchas). Don't add Gateway only for "future reuse".

## Gateway shape

- Create: `name`, `roleArn` (service role the gateway assumes for outbound), `authorizerType`.
- `protocolType: MCP` = aggregation mode, MCP targets only. **Omit it** if you need HTTP
  (Runtime, passthrough) or inference targets. Semantic search forces `MCP`.
- URL: HTTPS `{gatewayId}.gateway.bedrock-agentcore.{region}.amazonaws.com` + `/mcp`,
  `/{targetName}/...` (HTTP targets) or `/inference/...`.
- `UpdateGateway` is full replacement: resend name, role, authorizer, interceptors, policy
  engine, WAF config, `domainOverride`. Omitted fields are removed.
- CLI v1: `agentcore add gateway --name G [--protocol-type MCP] [--enable-semantic-search]
  --authorizer-type CUSTOM_JWT --authorizer-configuration '{"customJwtAuthorizer":{...}}'`,
  then `agentcore deploy`. **CLI default authorizer is NONE.** Interceptors are not in the
  project config; add them in `agentcore/cdk/lib/cdk-stack.ts` (`LambdaInterceptor.forRequest`).
- Key casing differs: CLI project JSON `customJwtAuthorizer`; API/AWS CLI `customJWTAuthorizer`.

## Targets

Tool names are `${targetName}___${toolName}` (three underscores). Target names allow only
alphanumerics and single hyphens. Bedrock Converse caps tool names at 64 chars
(`[a-zA-Z0-9_-]`), so keep `target___tool` within 64 when the model is on Bedrock.

| Category | Target (`targetConfiguration`) | Notes |
|---|---|---|
| MCP | `mcp.lambda` (`lambdaArn`, `toolSchema` inline or S3) | Outbound: gateway role only |
| MCP | `mcp.openApiSchema` / `mcp.apiGateway` (REST stage, same account+Region, public endpoint) | OpenAPI 3.0/3.1; needs `operationId`; no `oneOf/anyOf/allOf`; spec-level security schemes ignored |
| MCP | `mcp.smithyModel` | Gateway role or OAuth 2LO |
| MCP | `mcp.mcpServer` (`endpoint`, `listingMode`, `mcpToolSchema`) | Protocol 2026-07-28, 2025-11-25, 2025-06-18, 2025-03-26 |
| MCP | `mcp.connector` (built-ins below) and integration-provider templates | |
| HTTP | `http.agentcoreRuntime` (`arn`, `qualifier`, optional `schema`) | Path `/{targetName}/invocations`; SSE ok |
| HTTP | `http.passthrough` (`endpoint`, `protocolType` MCP/A2A/INFERENCE/CUSTOM) | Path `/{targetName}/{path}`; no translation |
| Inference | `inference.connector` (`bedrock-mantle`, `bedrock-runtime`, `openai`, `anthropic`) or `inference.provider` | `/inference/v1/chat/completions`, `/v1/responses`, `/v1/messages`, `/model/{m}/invoke` |

HTTP targets get no aggregation, no sync, no semantic search; interceptors buffered only.

### Lambda contract

The event is the tool **arguments only**. Metadata is in `context.client_context.custom`:
`bedrockAgentCoreToolName` (prefixed), `...GatewayId`, `...TargetId`, `...AwsRequestId`,
`...McpMessageId`, `...MessageVersion`. No caller identity arrives; pass it via an interceptor
(rewrite arguments) if the Lambda needs it. Return JSON.

```python
def handler(event, context):
    full = context.client_context.custom["bedrockAgentCoreToolName"]
    tool = full.split("___", 1)[1]   # the doc sample mixes toolName/tool_name; this is correct
    if tool == "get_order":
        return {"order": lookup(event["order_id"])}
    raise ValueError(f"unknown tool {tool}")
```

### MCP server targets

- `listingMode: DEFAULT` (cached catalog): Create/Update sync implicitly; call
  `SynchronizeGatewayTargets` (async, 202, poll `GetGatewayTarget`) whenever the server's tools,
  prompts or resources change. Stale catalog = agents see old tools.
- `DYNAMIC`: forwards list calls live; incompatible with semantic search and 3LO. Use it with
  OBO when the IdP has no client-credentials grant (sync has no user token).
- `mcpToolSchema` (static) disables sync; remove it to sync again.
- 3LO at create: target sits in `CREATE_PENDING_AUTH` until an admin completes consent; you
  can't update/delete/sync in `*_PENDING_AUTH`. Auth URL valid 10 min.
- SigV4 outbound needs a SigV4-verifying host (AgentCore Runtime/Gateway, API Gateway, Lambda
  URL); set `iamCredentialProvider.service` (`bedrock-agentcore`, `execute-api`, `lambda`).
  ALB/EC2 can't verify it: use OAuth or API key.
- Runtime-hosted MCP on <=2025-11-25: enable gateway MCP sessions so it isn't re-initialised
  per call. 2026-07-28 is stateless.

## Built-in connectors (`mcp.connector.source.connectorId`)

| Connector | Tools | Notes |
|---|---|---|
| `bedrock-knowledge-bases` | `Retrieve`, `AgenticRetrieveStream` | Managed KBs only. Per-user ACL needs `userContext`, which the gateway does **not** fill from identity: set it in an interceptor, never from the model |
| `web-search` (default 1.3.0) | `WebSearch` | Query <=200 chars, `maxResults` 1-25; target-level domain lists hidden from agent; role needs `bedrock-agentcore:InvokeWebSearch` on `arn:aws:bedrock-agentcore:<region>:aws:tool/web-search.v1` |
| `web-answer` (us-east-1 only) | `WebAnswer` | One call returns cited markdown; `InvokeWebAnswer` on `tool/web-answer.v1` |
| `states` (Step Functions) | 10 ops | Pin `stateMachineArn` on Start/StartSync/List/Describe; `SendTask*` can't be resource-scoped in IAM, so task tokens are secrets |
| `agentcore-memory` | 12 Memory ops as Cedar actions | OAuth to Memory and per-actor FGAC; see `memory.md` |

- Pin `source.version`; update without a version keeps the old one (sticky).
- Web Search/Answer terms: you must display the returned citations and links to end users.
- CLI: `agentcore add gateway-connector --gateway G --connector web-search|bedrock-knowledge-bases`.

## Inbound auth

| `authorizerType` | Use |
|---|---|
| `CUSTOM_JWT` | `discoveryUrl` + `allowedAudience` / `allowedClients` / `allowedScopes` / `customClaims` (`EQUALS`, `CONTAINS`, `CONTAINS_ANY`). Returns RFC 9728 metadata and `WWW-Authenticate` with scopes |
| `AWS_IAM` | Callers need `bedrock-agentcore:InvokeGateway` on the gateway ARN |
| `AUTHENTICATE_ONLY` | SigV4 checked, no authz: any signed principal gets through |
| `NONE` | Anyone. Only with Policy/interceptor authz; deny via `bedrock-agentcore:GatewayAuthorizerType` SCP |

- JWT `sub` lands in CloudTrail: use pairwise IDs, not emails.
- Behind CloudFront/custom domain set `domainOverride` and have the proxy **overwrite**
  `x-amzn-bedrock-agentcore-gateway-domain`.
- Private IdP: `customJWTAuthorizer.privateEndpoint` (VPC Lattice).
Depth, Entra quirks and OBO: `identity.md`.

## Outbound auth (`credentialProviderConfigurations`)

| Target | Gateway role | Caller IAM | OAuth 2LO | 3LO | OBO (`TOKEN_EXCHANGE`) | JWT passthrough | API key |
|---|---|---|---|---|---|---|---|
| Lambda | yes | - | - | - | - | - | - |
| API Gateway | yes | - | - | - | - | - | yes |
| MCP server / OpenAPI | yes | - | yes | yes | yes | - | yes |
| Smithy | yes | - | yes | - | - | - | - |
| Runtime (HTTP) | yes | yes | yes | - | - | yes | - |

Prefer OBO over `JWT_PASSTHROUGH` (passthrough replays one token to two audiences). Lock the
Runtime to the gateway: resource policy on the gateway role (IAM) or
`allowedWorkloadConfiguration.hostingEnvironments` (JWT). Details: `identity.md`.

## Header propagation

- Per target `metadataConfiguration.allowedRequestHeaders` / `allowedResponseHeaders` /
  `allowedQueryParameters` (max 10 each). `Authorization`, `X-Amzn-*` (except
  `X-Amzn-Bedrock-AgentCore-Runtime-Custom-*`) and ~100 standard headers can't be allowlisted.
- **Allowlisted headers are client-controlled.** A client can send `x-user-id: someone-else`.
  Identity-bearing headers must be set by a REQUEST interceptor from validated claims; the
  interceptor value wins over the client's.

## Interceptors

One REQUEST and one RESPONSE Lambda per gateway (`interceptorConfigurations`); the Lambda's
output must carry `interceptorOutputVersion: "1.0"`. Uses: per-tool authz, argument/identity injection,
`tools/list` filtering, redaction, routing (rewrite `model`).

- Input: `mcp.gatewayRequest` (path, method, body; `headers` only if `passRequestHeaders: true`)
  plus `rawGatewayRequest`. There is no parsed claims object: decode the already-validated
  bearer yourself. HTTP/inference targets use an `http` key with base64 bodies.
- Return `transformedGatewayRequest` (may set `Authorization`, forwarded even though not
  allowlistable) or `transformedGatewayResponse` to short-circuit. MCP: RESPONSE still runs
  after a short-circuit. HTTP: it doesn't.
- Streaming on: RESPONSE runs once per JSON-RPC event; only the first can change headers/status;
  progress and log notifications bypass it.
- 6 MB Lambda sync limit: `payloadFilter.exclude: [{"field":"RESPONSE_BODY"}]` on HTTP targets.
- Gateway may retry interceptors: make them idempotent. Don't log `Authorization`.
- What the gateway does when the interceptor Lambda errors or times out (fail open or closed) is
  undocumented (unverified). Deny by returning `transformedGatewayResponse`, never by raising.

## Semantic tool search

- `protocolConfiguration.mcp.searchType: "SEMANTIC"` at create (CLI `--enable-semantic-search`).
  **Can't be disabled later.** 18 Regions.
- Agent calls tool `x_amz_bedrock_agentcore_search` with `{"query": ...}`; quota **25 searches/min**
  (adjustable), $0.025/1k, indexing $0.02/100 tools/month.
- Two search-enabled gateways on one agent = duplicate tool name. Old `@aws/agentcore-cdk`
  emitted invalid `searchType: NONE` (fixed 0.24.2).
- Pattern: search at task start, then load only matched tools; don't search per turn.

## Rules, routing, A/B, fallback

- `CreateGatewayRule`: priority 1-1,000,000 (lower wins, per action type), max 20 rules,
  3 conditions (`matchPrincipals` IAM ARNs, `matchPaths` `/<httpTarget>/*`, `matchModels` glob),
  2 actions (`configurationBundle` static/weighted override; `routeToTarget` static/weighted, 2-way).
- No JWT-claim condition in rules: route by claim with an interceptor.
- Model fallback: `routeToTarget.staticRoute.fallback` with `targetFallback` (1-2),
  `triggerOn.statusCodes`/`errors` (`CONNECTION_ERROR`, `FIRST_BYTE_TIMEOUT`, `UNROUTABLE`),
  `firstByteTimeoutMilliseconds`, circuit breaker on by default. No fallback after a stream
  starts. Every attempt bills as an invocation.
- Unqualified model IDs matching several targets go to Bedrock if present, else random
  (connector doc) or round-robin (provider doc): use `target/model` or a rule. A/B experiments: `evaluations-and-optimization.md`.

## Rate limits

- API: `CreateGatewayRateLimit` / Get / Update / Delete / `ListGatewayRateLimits` /
  `BatchPutGatewayRateLimits` (API reference, AWS CLI `create-gateway-rate-limit`). Ignore the
  `CreateRateLimit`/`BatchPutRateLimitEntries` names in the release notes; no such API pages exist.
- `dimensionKeys` (1-10, immutable): `targetName`, `toolName`, `qualifiedModelId`,
  `$.context.jwt.<claim>`, `$.context.iam.principal`, `$.context.iam.sourceIdentity`, and
  header/body selectors (require an identity co-dimension). Entries use `*` defaults (trailing only).
- Metrics: `requests` (second/minute), `tokens` (minute, inference only), `connections` (second).
  `rate: 0` blocks. 50 limits/gateway, 30 s propagation, evaluated before rules.
- **Fail-open:** if the limiter is unavailable *or a dimension can't be resolved* (missing
  claim, non-tool request) the limit is skipped. Not a security boundary. Always add a `*` default.
- Inference streams have no service cap on size/duration: set a `tokens` limit per `jwt.sub`.

## WAF, sessions, Tasks, debug

- WAF: regional web ACL, one per gateway, `aws wafv2 associate-web-acl --resource-arn <gatewayArn>`.
  `wafConfiguration.failureMode` defaults `FAIL_CLOSE`. Disassociate before deleting the gateway.
- MCP sessions: `protocolConfiguration.mcp.sessionConfiguration.sessionTimeoutInSeconds`
  (900-28,800, default 3,600, absolute from `initialize`), bound to `sub`/IAM ARN; unauthenticated
  sessions are hijackable. Elicitation/sampling also need `streamingConfiguration.enableResponseStreaming`.
- MCP Tasks (2026-07-28, polling): long tools return a task ID instead of hitting the 15-min
  timeout. Targets must set `ttlMs` and keep task IDs <=1,024 bytes; the gateway never expires tasks.
- `exceptionLevel: DEBUG` shows real target errors (console turns it on by default). Turn off in prod.

## Policy and Responses API

- Attach a Cedar engine with `policyEngineConfiguration`; with Policy, `tools/list` shows only
  permitted tools. Modes, bypasses, guarding `UpdateGateway`: `policy-and-guardrails.md`.
- Bedrock Responses API server-side tools: `{"type":"mcp","server_label":"x",
  "connector_id":"<gatewayArn>","require_approval":"never"}`. **IAM-auth gateways only**, and
  `require_approval` must be `"never"`: no human approval, so gate writes with Policy. Works on
  the `bedrock-mantle` endpoint with any model it serves the Responses API for; the
  `bedrock-runtime` `/openai/v1` Responses API has no server-side tools. Bedrock's service role
  needs `bedrock-agentcore:InvokeGateway`.

## Gotchas

- Outbound MCP initialize sends JSON-only `Accept`; strict Streamable-HTTP servers (GitHub MCP)
  return 400, surfaced as "internal error" unless DEBUG (agentcore-cli #2550, open).
- Intermittent hung `tools/call` under concurrency (#1388, open): set client timeouts.
- Short-circuit body dropped in RESPONSE phase in some Regions (sdk-python #409, closed): test.
- CLI owns the whole gateway; adding a target to a shared gateway isn't supported (#2541).
- CLI-created OAuth providers outside CloudFormation leaked on teardown, hitting the 50-provider
  quota (#1673, closed).
- Targets added with the AWS CLI outside `agentcore.json` aren't managed by `agentcore deploy`:
  delete them before removing the gateway.
- Quotas: 100 targets/gateway, 1,000 tools/target, 200 TPS list+call per gateway and per
  account, 5,000 connections, 6 MB payload, 15-min invocation, 5 concurrent target ops.
- Passthrough `staticQueryParameterConflictResolution` defaults to `CLIENT_OVERRIDE`.

## Checklist

- [ ] Authorizer is JWT or IAM; `NONE`/`AUTHENTICATE_ONLY` only with Policy or interceptor authz
- [ ] `protocolType` matches the target mix; semantic search decided at create
- [ ] `target___tool` names <=64 chars; Lambda strips the prefix
- [ ] Identity headers and KB `userContext` set by interceptor, not client or model
- [ ] OBO (not passthrough) for user-scoped downstream calls; Runtime locked to the gateway
- [ ] `SynchronizeGatewayTargets` in the MCP server's release pipeline
- [ ] Rate limits with `*` defaults and token caps per user; WAF attached
- [ ] DEBUG off in prod; `UpdateGateway` calls resend every field
