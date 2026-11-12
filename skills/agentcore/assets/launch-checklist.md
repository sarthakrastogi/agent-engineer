# <Agent name> on AgentCore: launch checklist

Walk this with the user before the first real traffic, alongside the vendor-neutral
`agent-production` → `assets/launch-checklist.md`. Each line names the reference that covers
it. An unchecked box is a launch decision to make on purpose; record each "no" and who
accepted it. AgentCore changes monthly: re-check anything marked unverified in the references.

## Build
- [ ] `agentcore --version` prints 1.x; starter toolkit uninstalled; CLI version pinned in CI (`choosing-a-path.md`)
- [ ] Path chosen per agent (harness unless a code-only need applies); Classic agents mapped, no v0 commands
- [ ] Harness: model, system prompt, truncation and an explicit `allowedTools` list pinned; limits set (`harness.md`)
- [ ] Harness memory deliberately on (with `actorId`) or `disabled`; idle timeout and max lifetime set
- [ ] Runtime: arm64, `0.0.0.0`, right port and path, `/ping` per contract, SDK majors pinned (`runtime.md`)
- [ ] V2 runtimes: no IDs, randomness or time captured at startup; `/ping` healthy in < 120 s
- [ ] MCP servers: `mcp` major pinned; clients send a stable session header (`mcp-servers.md`)
- [ ] Skills: private, immutable sources (versioned S3 prefix or image), tested in a new session (`skills.md`)

## Identity and access
- [ ] Identity map written: caller → agent → tool → backend, credential named on each hop (`identity.md`)
- [ ] JWT inbound wherever tools, Policy or memory must know the user; Entra uses audience + `customClaims`
- [ ] User-Id header, `InvokeAgentRuntimeForUser` and `GetWorkloadAccessTokenForUserId` denied where JWTs exist
- [ ] Downstream calls use OBO or 3LO tokens, not token passthrough; 3LO callback verifies the browser user
- [ ] `actorId` and session IDs derived from the validated token, never the payload (`memory.md`)
- [ ] Execution roles least-privilege; trust policies carry `aws:SourceArn` / `aws:SourceAccount`

## Safety
- [ ] `agent-guardrails` threat model done; trifecta mitigations in place before write tools are wired
- [ ] Callers reach the harness through your service, which strips `model`, `skills`, `tools`, `allowedTools` overrides
- [ ] `InvokeAgentRuntimeCommand` / `...CommandShell` denied to non-operators
- [ ] Tools needing control go through a Gateway; runtimes behind it are locked to it (`gateway.md`)
- [ ] Policy engine rolled out `LOG_ONLY` → no decision flips → `ENFORCE`; generated Cedar reviewed and committed (`policy-and-guardrails.md`)
- [ ] `UpdateGateway` / `UpdatePolicy` / `Delete*` limited to the deploy role; CloudTrail alarm on them
- [ ] Gateway: inbound auth set (the CLI default is none), identity headers set by an interceptor, rate limits with `*` defaults, WAF, DEBUG off
- [ ] Browser, Code Interpreter and search: network mode justified, domain allowlists, sessions stopped (`built-in-tools.md`)

## Observability and quality
- [ ] Transaction Search on in every account and Region (IaC); ADOT ≥ 0.18 (`observability.md`)
- [ ] A real trace inspected: `invoke_agent` root with `chat` and `execute_tool` children and token counts
- [ ] Content-capture decision recorded; log groups KMS-encrypted, retention set, read access tight
- [ ] Every evaluator used as a gate validated against human labels; online evaluation samples production (`evaluations-and-optimization.md`)
- [ ] A versioned dataset and a batch or on-demand gate run in CI before release

## Ship and run
- [ ] One account per environment; `aws-targets.json` committed; `cdk diff` reviewed before prod (`deployment-and-iac.md`)
- [ ] Terraform gaps bridged explicitly (CloudFormation stack or CLI step), not console clicks
- [ ] VPC mode: ECR, S3 and model endpoints in place; egress for Git skills, remote MCP and public models
- [ ] `requireMMDSV2` true on every runtime (`GetAgentRuntime`)
- [ ] Production callers use a named endpoint; rollback = repoint, rehearsed once
- [ ] Alarms in IaC routed to on-call; quota headroom checked for peak TPS and session-creation rate (`operations.md`)
- [ ] Budgets and cost-allocation tags on; harness `maxTokens` / `maxIterations` sized
- [ ] Hook Lambdas match `read` / `write` / `edit` (deadline 12 Dec 2026); Step Functions callers use `timeoutSeconds` ≤ 900
