---
name: agentcore
description: >-
  Builds, secures, ships and operates agents on Amazon Bedrock AgentCore: the harness
  (config-based managed agent, "agent builder"), code agents and MCP servers on Runtime, the
  @aws/agentcore CLI, Gateway, Identity (JWT, OAuth, on-behalf-of), Memory, Code Interpreter,
  Browser, Web Search, Policy (Cedar) and Bedrock Guardrails, Observability, Evaluations,
  Registry, and CDK/CloudFormation/Terraform. Use when code or the request mentions
  AgentCore, bedrock-agentcore, harness.yaml, agentcore.json, InvokeAgentRuntime or
  InvokeHarness, or migrates Bedrock Agents Classic. Not for vendor-neutral design, prompts,
  tool design, evals or tracing (the agent-* skills; this one maps them onto AgentCore).
license: MIT
metadata:
  version: 0.4.0
---

# AgentCore

Map agent-engineering decisions onto AgentCore services and avoid their traps. Facts are as
of November 2026; AgentCore ships monthly, so check `--help`, the API reference and release
notes before relying on a name, limit or price. They beat blogs, samples and these notes.

## Core rules

1. **CLI v1 only.** `npm install -g @aws/agentcore`; `agentcore --version` must print 1.x.
   The Python starter toolkit (`agentcore configure` / `launch`) is deprecated and shadows the
   binary. Translate v0 samples and tutorials; don't copy them.
2. **Harness by default, code when needed.** One agent loop with tools, skills and memory →
   harness. A framework, graph, custom middleware or bidirectional streaming → code on
   Runtime. `agentcore export harness` drops Gateway, Browser, Code Interpreter, AWS skills and
   managed memory; re-run evals on the export.
3. **Pin what has managed defaults.** Model, system prompt, truncation, an explicit
   `allowedTools` list, limits, memory on/off (omitted = on via the API, off in `harness.yaml`).
   Omitted values follow AWS defaults that change.
4. **Callers are untrusted.** `InvokeHarness` overrides (`model`, `apiBase`, `skills`,
   `tools`, `allowedTools`) and Runtime payloads are caller-controlled. Front them with your
   service or Gateway, allowlist fields, lock direct invoke with resource policies.
5. **SigV4 carries no end user.** Tools, Policy and memory that must know the user need JWT
   inbound plus OAuth 3LO or on-behalf-of outbound. The `...-Runtime-User-Id` header is
   unverified: never grant `InvokeAgentRuntimeForUser` broadly.
6. **Per-user isolation is your code's job** unless Memory sits behind Gateway + Policy.
   Derive `actorId` and session IDs from the validated token, never the payload: the Memory
   data plane lets the role read every actor.
7. **Policy governs Gateway traffic only**, default deny. Review every generated Cedar
   statement by hand; restrict `UpdateGateway` / `UpdatePolicy` to the deploy role: anyone
   holding them can switch enforcement to log-only or detach the engine.
8. **Production calls a named endpoint.** `DEFAULT` moves on every deploy. Roll back by
   repointing the endpoint at the previous version.
9. **Runtime basics bite.** ARM64 images; confirm `requireMMDSV2` with `GetAgentRuntime` after
   every create (new runtimes appear to default on; invokes fail without it);
   on V2, never generate IDs, randomness or time at startup (snapshots copy them).
10. **Quotas are shared per account.** 25 new sessions/s (adjustable) across every agent;
    reuse session IDs (MCP SDK 2.x clients don't echo them: send the Runtime session header).
11. **Spans are a prerequisite**, not an extra: Transaction Search on, ADOT ≥ 0.18, flush
    before the microVM freezes. Content is captured by default, so plan KMS, retention and access.
    Evaluations and A/B testing read the CloudWatch spans: keep them flowing if you also export.
12. **Built-in evaluators are unvalidated judges.** Check them against human labels before
    gating on them (`agent-evals`).

## Workflow

1. Inventory: `agentcore/agentcore.json`, `app/*/harness.yaml`, `bedrock_agentcore` imports,
   IaC (`AWS::BedrockAgentCore::*`, `aws_bedrockagentcore_*`), how callers invoke. Note the
   CLI version and the Region.
2. Scope the agent with `agent-design`. Then pick the path: `choosing-a-path.md`.
3. Build it: `harness.md` (+ `skills.md`) or `runtime.md`; an MCP server: `mcp-servers.md`.
4. Tools: shared, authenticated or policy-governed → `gateway.md`; sandboxed code, browsing,
   search → `built-in-tools.md`; other agents → `multi-agent-and-registry.md`. Tool design
   itself: `agent-tools`.
5. Identity end to end, caller → agent → tool → backend, naming the credential on each hop:
   `identity.md`. Then state: `memory.md`.
6. Threat model with `agent-guardrails`; implement it with `policy-and-guardrails.md`.
7. Observability before the first real traffic: `observability.md`. Then evals:
   `evaluations-and-optimization.md`.
8. Ship: `deployment-and-iac.md`. Run: `operations.md` (alarms, quotas, cost, runbook).
9. Before launch, walk `assets/launch-checklist.md`. Report unverified items as unverified.

## Decision rules

| If… | Then |
|---|---|
| New agent, one loop, standard tools | Harness from `assets/harness.yaml` |
| Bedrock Agents Classic agent | Harness; code if it uses stage prompts or a custom orchestrator |
| Tools shared by agents, OAuth to backends, per-tool rules | Gateway target + Policy |
| A tool must act as the signed-in user | JWT inbound + OBO / 3LO outbound |
| Agent reads web, email or documents and has write tools | `agent-guardrails` trifecta check first |
| Long-running or async work | Runtime async tasks, `HealthyBusy` ping, same session ID to poll |
| Harness called from Step Functions | Harness `timeoutSeconds` ≤ 900; it outlives the task |
| Terraform shop | Read the gaps in `deployment-and-iac.md` first |
| Unknown error message | `operations.md` runbook, then release notes and GitHub issues |

## Anti-patterns

- Copying starter-toolkit or v0 CLI steps (`configure`, `launch`, `add tool`, `--model-id`).
- `InvokeHarness` or `InvokeAgentRuntime` exposed straight to browsers or partners.
- Natural-language policy output marked `VALID` treated as reviewed.
- Rebuilding images per environment and calling it promotion.
- Secrets in `environmentVariables` or MCP headers instead of Identity credential providers.
- Floating skill sources (a Git default branch, a mutable S3 prefix).

## Outputs

Code, YAML and IaC in the user's repo. The identity map and threat model go in
`agent-engineering/design.md` if the project keeps one (see `agent-engineer`), otherwise in
the reply or PR, with unverified AgentCore facts flagged.

## References

All in `references/`, named in the workflow step that needs them: `choosing-a-path.md`,
`harness.md`, `skills.md`, `runtime.md`, `mcp-servers.md`, `gateway.md`,
`built-in-tools.md`, `multi-agent-and-registry.md`, `identity.md`, `memory.md`,
`policy-and-guardrails.md`, `deployment-and-iac.md`, `observability.md`, `operations.md`,
`evaluations-and-optimization.md`. Assets: `assets/harness.yaml` (production-pinned
template), `assets/launch-checklist.md`.
