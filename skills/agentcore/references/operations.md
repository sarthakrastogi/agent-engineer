# AgentCore operations: metrics, alarms, quotas, cost, troubleshooting

Telemetry setup is in `observability.md`. SLOs, retries, rollouts and incident process are
vendor-neutral and live in `agent-production`. This file covers AgentCore's numbers and
failure modes.

## Metrics that matter

Namespace `AWS/Bedrock-AgentCore` unless noted. Metrics are published in 1-minute batches.

| Service | Metric | Notes |
|---|---|---|
| Runtime | `Invocations`, `Throttles`, `SystemErrors`, `UserErrors`, `Latency` | `Latency` is end-to-end, to the last token |
| Runtime | `OverheadLatency` | V2 only. Platform share of time to first byte; compare with `Latency` to split cold-start blame |
| Runtime | `SessionCount` | New sessions (a counter, not a gauge) |
| Runtime and tools | `ActiveSessionCount` | Gauge per account; dimension `Service` = `AgentCore.Runtime`, `AgentCore.CodeInterpreter` or `AgentCore.Browser`. Compare with the active-session quota |
| Runtime | `ActiveStreamingConnections`, `Inbound/OutboundStreamingBytesProcessed` | WebSocket only |
| Runtime and tools | `CPUUsed-vCPUHours`, `MemoryUsed-GBHours` | Dimensions `Service`; `Service,Resource`; `Service,Resource,Name` (`Name` = `Agent::Endpoint`). Up to 60 min late, so not for paging |
| Gateway | `Invocations`, `Throttles`, `SystemErrors` (5xx), `UserErrors` (4xx except 429), `Latency` (first byte), `Duration`, `TargetExecutionTime`, `TargetType` | Dimensions `Operation`, `Protocol`, `Method` (e.g. `tools/call`), `Resource` (gateway ARN), `Name` (tool) |
| Memory | `Invocations`, `Errors`, `SystemErrors`, `UserErrors`, `Throttles`, `Latency`, `CreationCount` | LTM extraction `TokenCount` is in namespace **`Bedrock-AgentCore`** (no `AWS/`) |
| Identity | `WorkloadAccessTokenFetchFailures`, `ResourceAccessTokenFetchFailures`, `ApiKeyFetchFailures` (+ `…Success`, `…Throttles`) | Has an `ExceptionType` dimension; per-account `CallCount`/`ThrottleCount` are in `AWS/Usage` |
| Policy | `DenyDecisions`, `NoDeterminingPolicies`, `MismatchErrors`, `LogOnlyDecisionFlips`, `GuardrailLatency` | Dimensions `PolicyEngine`, `Policy`, `TargetResource`, `ToolName`, `Mode` |
| Evaluations | One metric per evaluator score | Namespace `Bedrock-AgentCore/Evaluations`, or your `metricsNamespace` |

The docs don't list the dimension sets for Runtime invocation metrics. Discover them with
`aws cloudwatch list-metrics --namespace AWS/Bedrock-AgentCore --metric-name Invocations`
before writing alarms. Runtime error types (`error_type`), all prefixed `InvocationError.`:
`Validation` (400), `ResourceNotFound` (404), `AccessDenied` (403), `Conflict` (409),
`Internal` (500), `Throttling` (429), `ServiceQuota` (402).

## Recommended alarms

| Alarm | Signal | Start threshold |
|---|---|---|
| Platform errors | Runtime `SystemErrors` Sum | > 0 for 3 of 5 min (page) |
| Client or auth breakage | Runtime `UserErrors` / `Invocations` | > 5% for 10 min (a deploy or auth change usually) |
| Throttling | Runtime and Gateway `Throttles` | > 0 sustained 5 min → raise the quota or smooth the load |
| Session capacity | `ActiveSessionCount` (`Service=AgentCore.Runtime`) | > 80% of the active-session quota |
| Slow turns | Runtime `Latency` p95 / p99 | Your SLO; pair with `OverheadLatency` p95 |
| Tool backend | Gateway `TargetExecutionTime` p95, `SystemErrors` by `Name` | Per-tool SLO |
| Credential failures | `ResourceAccessTokenFetchFailures`, `WorkloadAccessTokenFetchFailures` | > 0 (expired secrets, revoked consent) |
| Policy regressions | `NoDeterminingPolicies`, `MismatchErrors`, `LogOnlyDecisionFlips` | Any jump after a policy change |
| Quality | Evaluation score metric (e.g. `GoalSuccessRate`) | Drop vs. 7-day baseline; validate the judge first (`evaluations-and-optimization.md`) |
| Spend | AWS Budgets on AgentCore + Bedrock + CloudWatch | Monthly forecast |

Gateway example: `aws cloudwatch put-metric-alarm --namespace AWS/Bedrock-AgentCore
--metric-name SystemErrors --dimensions Name=Resource,Value=<gateway-arn> --statistic Sum
--period 300 --evaluation-periods 1 --threshold 5 --comparison-operator GreaterThanThreshold`.

## Quotas that bite (defaults, per account per Region)

| Quota | Default | Adjustable |
|---|---|---|
| Runtime data-plane TPS: **one shared pool** for `InvokeAgentRuntime`, `…Command`, `…WithWebSocketStream`, `…CommandShell`, `StopRuntimeSession`, `GetAgentCard` | **1,000** | Yes |
| New Runtime sessions, **shared by all agents, endpoints and harnesses** | **25 TPS** | Yes |
| Active sessions | 5,000 (us-east-1, us-west-2), 2,500 elsewhere | Yes |
| Control plane mutate / Get / List (shared) | 50 / 150 / 25 TPS | No |
| Per session | 2 vCPU / 8 GB, 1 GB session storage, 250 concurrent connections (V2) | No |
| Sync request / streaming / async job | 15 min / 60 min / 8 h (microVM), 14 d (Instances) | No |
| Payload / stream chunk / WebSocket frame | 100 MB / 10 MB / 64 KB at 250 frames/s | No |
| Env vars per agent | 4 KB total | No |
| Gateway `tools/call` + `tools/list` | 200 TPS per gateway **and** 200 TPS per account; 5,000 concurrent connections | Yes |
| Gateway search-based tool call | 25 per **minute** | Yes |
| Gateway invocation timeout / payload | 15 min / 6 MB | Yes |
| Identity `GetWorkloadAccessToken*`, `GetResourceOauth2Token` | 200 TPS each | Yes |
| Memory `CreateEvent` | 200 TPS; **5 TPS per actor per session** with conversational payloads, 10 without (not adjustable) | Partly |
| Memory `RetrieveMemoryRecords` | 30 TPS | Yes |
| Memory LTM extraction | 150,000 tokens/min | Yes |

- **Aug 2026 restructure.** Per-API and per-endpoint limits became shared account pools. New
  sessions used to be 400 per minute *per endpoint* for containers. They are now 25 TPS for the
  *whole account*, so many-agent accounts can hit this sooner than before. Burst session
  creation (fan-out, load tests, cron at :00) is the usual trigger.
- A **harness is a Runtime** and draws on the same pools. Its per-invocation caps are separate:
  `maxIterations` (default 75), `timeoutSeconds` (3600), `maxTokens` (no default; set one).
- Cross-account invocations count against the **runtime owner's** quotas.
- Instances also consume your EC2, EBS, VPC and Auto Scaling quotas.
- Raise adjustable quotas in the Service Quotas console; for anything not listed there, open a
  Support case. Ask before launch, with peak TPS and session-creation numbers.

## Throttling and retry behaviour

- `ThrottlingException` (429) and `ServiceQuotaExceededException`: back off with jitter. Don't
  retry a non-idempotent agent turn blindly (`agent-production` → reliability).
- `RetryableConflictException` (409, "Session operation in progress, please retry") happens
  while a session is provisioning or tearing down. The AWS SDKs auto-retry the HTTP APIs, but
  **not** the WebSocket APIs, so retry those yourself.
- Gateway rate-limit rejections return `retryAfter` (seconds) in the body. Honour it. Gateway
  rate limiting **fails open** when its backing service has errors, so it is not a security
  control.
- Session exhaustion shows up as `ServiceQuotaExceededException` or `maxVms` errors during
  bursts. Check `ActiveSessionCount` and the `/ping` behaviour (runbook below).

## Cost model

Prices from the live pricing page, 2026-11-12 (Region not stated; check yours):

| Item | Price | Driver |
|---|---|---|
| Runtime V1 (and Code Interpreter, Browser) | $0.0895/vCPU-h, $0.00945/GB-h | Per second; CPU free during I/O wait if nothing else runs; **memory billed for the whole session, including idle until the idle timeout** |
| Runtime V2 | $0.1276/vCPU-h, $0.0169/GB-h consumption | Idle memory reclaimed after 120 s. A committed baseline ($0.0997 / $0.0132) is listed as "launching by October 2026" and still shows that wording; confirm before planning on it |
| Runtime Instances | EC2 On-Demand + **12%** management fee (**7.8%** for G-series GPU) | Per instance-hour from boot, ~1 min minimum; Savings Plans and RIs cover EC2, not the fee; EBS extra |
| Model inference | Bedrock (or provider) rates | Usually the largest line. A **harness adds ~3,000 input tokens per model call** for built-in tool definitions |
| Gateway | $0.005/1K calls; search $0.025/1K; $0.02 per 100 tools indexed/month | `tools/list` and ping count as calls |
| Web Search / Web Answer | $7 / 1K queries; $10 / 1K calls | |
| Memory STM | $1.00/GB ingested, $0.20/GB retrieved, $0.10/GB-month | Each event billed at a 12 KB minimum and 64 KB maximum (since 2026-10-06) |
| Memory LTM | $0.75/1K records/month (built-in), $0.25/1K (override or self-managed, plus your model); $0.50/1K retrievals | |
| Identity | $0.010/1K tokens or keys | Free when used through Runtime or Gateway |
| Policy | $0.000025/authorization; NL authoring $0.13/1K tokens | First 100 temporal policies per engine incur no extra authorization charge |
| Evaluations | Built-in $0.0024/1K in, $0.012/1K out; custom $1.50/1K evaluations + your model; batch 25% off | Input = whole session context, so long sessions × many evaluators add up fast |
| Optimization | Recommendations free (pay for evaluations); A/B pays for underlying use | |
| Observability | CloudWatch rates (pricing example: spans $0.35/GB, logs $0.50/GB) | Content capture roughly doubles volume |

Levers:

- Shorten `idleRuntimeSessionTimeout` (memory keeps billing until it fires) or move to V2.
- Call `StopRuntimeSession` when a conversation ends.
- Trim the harness tool set (`allowedTools`).
- Sample online evaluations (1–10%).
- Set log retention.
- Tag resources and activate cost-allocation tags. Usage metrics and logs explain usage but are
  not the bill; use Cost Explorer or CUR.

## Troubleshooting runbook

| Symptom / message | Likely cause | Fix |
|---|---|---|
| `ValidationException: This runtime is not MMDSv2-enabled` | Since 2026-06-30, invocations require MMDSv2 | `UpdateAgentRuntime` with `metadataConfiguration={"requireMMDSV2": true}` (and in IaC) |
| `RuntimeClientError` 424 "Runtime initialization time exceeded. Please make sure that initialization completes in 30s." | Container didn't become ready: crash on import (missing deps in a code zip, which is **not** pip-installed), slow start-up work, wrong port or path, macOS-built `opentelemetry-instrument` shebang | Read the runtime log group for the traceback. Serve on 8080 at `POST /invocations` and `GET /ping`. Move heavy init (MCP connects, model warm-up) out of start-up. Vendor arm64 deps |
| 424 with `Failed to mount overlay: No such file or directory` | Image has more than 53 layers **and** a non-numeric `USER` | `USER 1000`, or squash or multi-stage the image |
| `RuntimeClientError` 403 | Startup failure, execution role, or bad or expired bearer token | Check `[runtime-logs]`, the role trust (`bedrock-agentcore.amazonaws.com`), and the token |
| "Received error (500) from runtime" | Exception in your code | Stack trace in runtime logs |
| 422 from runtime | Payload shape mismatch (e.g. `{"input":{"prompt":…}}` vs `{"prompt":…}`) | Align client and entrypoint; replay the payload locally |
| `exec /bin/sh: exec format error`; "Your artifact contains binary files that are incompatible with Linux ARM64." | x86 image or wheels | `docker buildx --platform linux/arm64` or CodeBuild Graviton; `uv pip install --python-platform aarch64-manylinux2014 --only-binary=:all:` |
| "The specified entrypoint could not be found or accessed in your artifact." | `entryPoint` path not in the zip | `unzip -l`; fix the path |
| `ValidationException` saying the image identifier does not exist, on create or update (unverified: no AWS doc or public issue found; reasoned from the `containerUri` format) | `containerUri` (`<acct>.dkr.ecr.<region>.amazonaws.com/<repo>:<tag>` or `@<digest>`) names an image that isn't there yet: pushed to another account, Region or repo, tag typo, IaC created the runtime before the push finished, or an ECR lifecycle rule expired the tag | `aws ecr describe-images --registry-id <acct> --region <region> --repository-name <repo> --image-ids imageTag=<tag>` with the exact URI parts; push before deploy (explicit dependency); pin a digest; exclude deployed tags from lifecycle expiry |
| `Unknown service: 'bedrock-agent-core-runtime'` | Old boto3 | boto3 ≥1.39.8 (use current) |
| `AccessDeniedException` on `CreateAgentRuntime` | Caller lacks `bedrock-agentcore:CreateAgentRuntime`, or the role can't be assumed | Fix the caller policy or the role trust |
| Direct code deploy `AccessDenied` | Caller lacks `s3:GetObject` (and `kms:Decrypt` for a CMK) on the zip | Grant to the **caller** of create or update |
| 401/403 with a JWT; "Authorization method mismatch" | `iss` ≠ discovery URL issuer; `aud` not in `allowedAudience`; `client_id` not in `allowedClients`; expired token; SigV4 call to an OAuth agent or the reverse | Decode the token and compare claim by claim (`identity.md`) |
| `get_workload_access_token()` returns `None`; `GetWorkloadAccessToken*` AccessDenied | Plain FastAPI app bypasses `BedrockAgentCoreApp` context; work runs in a thread without the contextvar; role lacks `bedrock-agentcore:GetWorkloadAccessToken*` / `GetResource*` | Use `BedrockAgentCoreApp` or set the context yourself; copy context into threads; fix the role (`identity.md`) |
| Long task killed at ~15 min idle | `/ping` reported `Healthy` | Report `HealthyBusy` while background work runs (the SDK's `add_async_task` does this) |
| Sessions never released; quota exhausted, `maxVms` | Hand-rolled `/ping` sets `time_of_last_update` to now on every ping | Update it only when status changes (or use the SDK ping). Stopgap: `StopRuntimeSession` |
| New code not taking effect | Existing sessions keep their original artifact | New `runtimeSessionId`; pin endpoints to versions |
| No spans / empty traces | Transaction Search off; Lambda caller `Sampled=0`; no ADOT (TS); spans not flushed before freeze; ADOT older than 0.18 with unified destination | `observability.md` §1, §5, §6 |
| Metrics vanished after the V2 move | Cross-account calls publish to the owner's account | Look in the owner account |
| `409 RetryableConflictException` | Session mid-provision or teardown | Back off and retry (yourself for WebSocket) |
| Gateway tool call returns "An internal error occurred. Please retry later." | Real error hidden | Set `exceptionLevel: DEBUG` in dev only; the message names the cause (e.g. Lambda invoke denied) |
| Gateway call hangs with no response under concurrency (open issue) | Service-side hang reported with Lambda targets | Client-side timeouts and retries on every MCP call |
| Evaluation "Provided input has no spans to evaluate…" | Spans lack `gen_ai.*` / OpenInference attributes or a recognised scope | Use a supported instrumentor (`observability.md` §2) |
| Capacity provider `CREATE_FAILED` (Instances) | Operator role missing EC2 / ASG permissions | `GetCapacityProvider` → `statusReason` |

Evaluation-specific failures (`LogEventMissingException`, `AgentSpanMappingException`, empty
scores): see `evaluations-and-optimization.md`.

## Checklist

- [ ] Alarms above exist in IaC per environment, routed to on-call.
- [ ] Quota headroom checked against peak TPS and **session-creation** rate; increases filed.
- [ ] Clients back off on 429 and 409, honour `retryAfter`, and set timeouts on gateway calls.
- [ ] MMDSv2 required on every runtime; images arm64; numeric `USER`.
- [ ] `idleRuntimeSessionTimeout` and `maxLifetime` set deliberately; sessions stopped at
      conversation end.
- [ ] Budgets and cost-allocation tags on; harness `maxTokens` / `maxIterations` set.
- [ ] The runbook is linked from the agent's on-call doc (`agent-production` → incident-response).
