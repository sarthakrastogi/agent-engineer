# AgentCore harness

A harness is a managed agent loop (Strands Harness underneath) declared as one resource:
model, prompt, tools, skills, memory, limits. Each session gets its own microVM with a
filesystem and shell, backed by a managed Runtime. GA since 17 June 2026 (What's New; the
release notes file it under July). No harness charge: you pay Runtime, model, Memory, tools.
Choosing harness vs code: `choosing-a-path.md`.

## API surface

- Control (`bedrock-agentcore-control`): `CreateHarness`, `GetHarness`, `UpdateHarness`,
  `DeleteHarness`, `ListHarnesses`, `ListHarnessVersions`, `Create/Get/Update/Delete/ListHarnessEndpoint(s)`.
- Data (`bedrock-agentcore`): `InvokeHarness` (event stream); shells via Runtime's
  `InvokeAgentRuntimeCommand` / `...CommandShell`. CloudTrail logs all of it as `AWS::BedrockAgentCore::Runtime`.
- Callers need both actions: `InvokeHarness` + `InvokeAgentRuntime`; `CreateHarness` also needs
  `CreateAgentRuntime` + `CreateMemory` (same pattern for update/delete/endpoints).
- `CreateHarness` fields: `harnessName` (`[a-zA-Z][a-zA-Z0-9_]{0,39}`: no hyphens),
  `executionRoleArn` (required), `model`, `systemPrompt`, `tools`, `allowedTools`, `skills`,
  `memory`, `truncation`, `maxIterations`, `maxTokens`, `timeoutSeconds`, `environment`,
  `environmentArtifact`, `environmentVariables`, `authorizerConfiguration`, `hooks` (≤20),
  `tags`, `clientToken`. Poll `GetHarness` until `status: READY`; read `failureReason` otherwise.
- `UpdateHarness` is PATCH: omitted fields are kept; `tools`, `skills`, `allowedTools`, `hooks`,
  `environmentVariables`, filesystem mounts are **replaced whole**; `memory`,
  `authorizerConfiguration`, `environmentArtifact` take an `optionalValue` wrapper. Every
  update creates a new immutable version and moves `DEFAULT`.

## Project files (CLI v1)

`app/<name>/harness.yaml` + `system-prompt.md` (layout: `choosing-a-path.md`). Minimal valid
file from the CLI docs; YAML uses API field names and nesting, not the `--model` JSON keys.
Production template: `assets/harness.yaml`.

```yaml
name: assistant
model:
  bedrockModelConfig:
    modelId: global.anthropic.claude-sonnet-5-5
memory:
  managedMemoryConfiguration: {}
```

- Edit, then `agentcore deploy`; a local edit does not touch the deployed harness. `agentcore dev`
  for a harness deploys too (nothing runs locally). Project + target + harness name ≤ 40 chars.
- `systemPrompt[0].text` is literal text; values ending `.md` / `.txt` are rejected. Put the
  prompt in `system-prompt.md`. Blank prompts are rejected.
- No `agentcore add tool` / `add skill` in v1: edit the whole `tools` / `skills` list. Unknown
  root fields and duplicate keys fail the read. Memory defaults differ by path (see Memory).
- `executionRoleArn` omitted: deploy creates a role. Supplied: CDK can't modify it; add model,
  tool, skill-source and storage permissions yourself.

## Model

| Config key | Credentials | Options |
|---|---|---|
| `bedrockModelConfig` | execution role | `apiFormat`: `converse_stream` (default, Claude), `responses` / `chat_completions` (bedrock-mantle models, e.g. `openai.gpt-oss-120b`) |
| `openAiModelConfig` | `apiKeyArn` (Identity API-key provider) | `apiFormat` `responses` (default) / `chat_completions`; `apiBase` via API (CLI YAML allows `apiBase` only on LiteLLM) |
| `geminiModelConfig` | `apiKeyArn` | `topK` |
| `liteLlmModelConfig` | `apiKeyArn` (`bedrock/` prefix uses the role) | `apiBase`, `additionalParams`; CLI-authored configs must route via a LiteLLM gateway (`litellm_proxy/`) |

- **Pin the model.** API default is "current recommended model" (now `global.anthropic.claude-opus-5`);
  the CLI writes `global.anthropic.claude-sonnet-5-5`. Omitted model, prompt, truncation and
  `allowedTools: ["*"]` all drift as AWS updates managed defaults.
- `maxTokens` inside the model config caps each model call; top-level `maxTokens` caps total
  output tokens across the invocation (CreateHarness API, CLI docs, `max_output_tokens_exceeded`).
  Only the InvokeHarness API reference calls the override "per iteration": treat it as per invocation.
- Claude Sonnet 5 / 5.5 reject `temperature`; leave it unset.
- Keys: `agentcore add credentials api-key --name k --api-key file://key.txt`, then `apiKeyCredentialName: k`.
- Prompt caching is automatic on `converse_stream`; an explicit `cachePoint` in `additionalParams` replaces it.
- Bedrock Guardrails: `additionalParams.guardrailConfig` (`converse_stream` only) plus
  `bedrock:ApplyGuardrail` on the role; stop reason `guardrail_intervened`.

- Omitted `systemPrompt` = AWS's managed default ("You are an agent. Keep working until the task
  is fully resolved..."), which can change. Write your own: `agent-prompting`.

## Tools

| `type` | `config` key | Runs |
|---|---|---|
| `remote_mcp` | `remoteMcp: {url, headers}` | From the session microVM to any reachable URL |
| `agentcore_gateway` | `agentCoreGateway: {gatewayArn, outboundAuth}` | All tools on the gateway; `outboundAuth` one of `awsIam` / `none` / `oauth` (`CLIENT_CREDENTIALS` or `USER_FEDERATION`) |
| `agentcore_browser` | `agentCoreBrowser: {browserArn?}` | AgentCore Browser |
| `agentcore_code_interpreter` | `agentCoreCodeInterpreter: {codeInterpreterArn?}` | AgentCore Code Interpreter |
| `inline_function` | `inlineFunction: {description, inputSchema}` | Your client: stream ends `stopReason: tool_use`, you send `toolResult` with the same `runtimeSessionId` |

Built-ins (filtered by `allowedTools`): `shell`, `read`, `write`, `edit` (`file_operations` is
now a compatibility selector for all three), `todo_write`, `retrieve_context` (offloads tool
results over ~1,500 tokens to `/tmp`), `subagent` (child gets built-in, MCP and Gateway tools,
not Browser / Code Interpreter / inline functions; shares limits and hooks), `web_fetch`.

```yaml
allowedTools:            # explicit list = fixed capability boundary
  - "@builtin/read"
  - "@builtin/todo_write"
  - "@company_tools"       # all tools from the tool entry named company_tools
  - "@research/search"
```

- `["*"]` (the CLI default) or omitted = every configured tool plus the current recommended
  built-ins, including `shell` and `write`, and grows when AWS adds tools.
- Never `allowedTools: []` as deny-all: the CDK mapper drops an empty list.
- `allowedTools` filters model tool choice only, not `InvokeAgentRuntimeCommand` (`harness exec`,
  runs as root) or `...CommandShell` (`harness shell`): deny both actions to non-operators in IAM.
- The built-in set adds ~3,000 input tokens to every model call, used or not.
- MCP header secrets: `"${arn:aws:bedrock-agentcore:...:apikeycredentialprovider/name}"`, not
  raw tokens. For OAuth and rotation, put the server behind Gateway (`gateway.md`).
- Web search / Web Answer / KBs reach the harness only as Gateway targets. Design: `agent-tools`.

## Memory and context

- **Defaults by path** (managed memory bills). API `CreateHarness` without `memory`: managed
  memory **on** (`agentcore harness create` passes API JSON, so the same; inferred).
  `agentcore add harness` without `--memory` scaffolds `memory: {managedMemoryConfiguration: {}}`
  (on). A `harness.yaml` with **no `memory:` key = disabled**; deleting the key turns memory off
  on the next deploy. `UpdateHarness` without `memory`: unchanged.
- Disable: `memory: {disabled: {}}` (API update: `{"optionalValue": {"disabled": {}}}`, CLI
  `--memory '{"mode":"disabled"}'`). BYO: `agentCoreMemoryConfiguration: {arn | name, retrievalConfig}`.
- Managed strategies: `SEMANTIC`, `SUMMARIZATION`, `USER_PREFERENCE`, `EPISODIC`;
  `eventExpiryDuration` in days. You can't choose the extraction model (use BYO with
  override strategies) or change its KMS key later.
- Pass `actorId` per invoke to isolate users; retrieval defaults `topK=10`, `relevanceScore=0.2`.
  `DeleteHarness` deletes managed memory unless `deleteManagedMemory=false`.
- Truncation: service default `summarization` (`summaryRatio` 0.3, `preserveRecentMessages` 10,
  uses the harness model); the CLI scaffold writes `sliding_window` with `messagesCount: 40`;
  `none` means you manage it. Details: `memory.md`, `agent-context`.

## Limits, environment, network, auth

| Setting | Default | Notes |
|---|---|---|
| `maxIterations` | 75 | Stop reason `max_iterations_exceeded` |
| `timeoutSeconds` | 3600 | `timeout_exceeded` |
| `maxTokens` | none | `max_output_tokens_exceeded` |
| `idleRuntimeSessionTimeout` | 900 | 60–28800 s; memory bills until idle timeout |
| `maxLifetime` | 28800 | ≤ 8 h per microVM session |

- `environmentVariables`: plaintext, ≤50 keys; stay under 4 KB total (Runtime quota; API
  allows 5,000-char values, CDK 2,048). No secrets.
- Custom image (`environmentArtifact.containerConfiguration.containerUri` or `dockerfile`):
  `linux/arm64`; needs `/bin/bash`, `/bin/sh`, `sleep`, `base64`, `cat`, `dirname`, `env`, `ls`,
  `mkdir`, `rm`, plus `tar` for fetched skills; writable `/tmp`. ENTRYPOINT/CMD are ignored;
  start daemons with `harness exec`.
- VPC: `environment.agentCoreRuntimeEnvironment.networkConfiguration` (`networkMode: VPC`,
  subnets, security groups). Needs interface endpoints `ecr.dkr`, `ecr.api`,
  `bedrock-runtime` and the S3 gateway endpoint, plus the private-ECR pull statement on the
  role; otherwise sessions fail on image-pull timeout. Git skills, remote MCP and public
  models need NAT. Mounts: session storage (no VPC), ≤2 EFS + ≤2 S3 Files under `/mnt/<dir>` (VPC).
- Inbound: IAM SigV4 by default; `customJWTAuthorizer` (discovery URL ending
  `/.well-known/openid-configuration`, plus at least one of `allowedAudience` /
  `allowedClients` / `allowedScopes` / `customClaims`). **SigV4 callers get no per-user
  identity downstream**: OBO and user-scoped tokens need JWT inbound (`identity.md`). CLI: use
  `agentcore harness invoke --id <id> --bearer-token`; `invoke --harness` rejects it (#2741).

## Lifecycle hooks

Create/update only (no invoke override; the list is replaced whole). Config keys are camelCase;
the payload `event` field is snake_case (`before_tool_call`).

| Event | Lambda `deny` effect |
|---|---|
| `beforeInvocation` | Stops before the loop (`hook_stopped`); sees only the request's messages |
| `beforeToolCall` | Skips that tool call; loop continues |
| `afterToolCall` | Keeps the result, stops the invocation |
| `afterInvocation` | Reported only; output already streamed |

```python
def lambda_handler(event, _ctx):  # event: hookEventId, name, event, harnessArn, sessionId, context
    ctx = event["context"]
    if ctx.get("truncated"):                 # context > 64 KiB was trimmed
        return {"decision": "deny", "reason": "context truncated"}
    return {"decision": "allow"}             # reason optional, <= 1,024 chars
```

- Lambda target: `timeoutSeconds` 1–900 (default 60), `failureMode` default `deny`. Same-event
  hooks run concurrently; any deny wins. SNS / EventBridge are fire-and-forget.
- **Deadline:** `file_operations` was split into `read` / `write` / `edit`. Harnesses created
  before 12 Nov 2026 with tool-call hooks get duplicate events (new name + deprecated
  `file_operations`) until **12 Dec 2026**, then only the new names. Update matchers now.
- An `afterToolCall` for an inline function proves only that the client sent a result.
- Long hooks idle the stream: set the client `read_timeout` above the hook timeout and OS TCP
  keepalive < 350 s behind NAT / NLB. Terraform has no hook block yet.

## Invoke

```python
import boto3, uuid
from botocore.config import Config
rt = boto3.client("bedrock-agentcore", config=Config(read_timeout=900))   # > longest hook/tool wait
resp = rt.invoke_harness(
    harnessArn=HARNESS_ARN, qualifier="prod",             # omit qualifier = DEFAULT
    runtimeSessionId=str(uuid.uuid4()),                  # 33-100 chars; reuse to continue
    actorId=user_id,
    messages=[{"role": "user", "content": [{"text": question}]}])
for ev in resp["stream"]:
    if "contentBlockDelta" in ev: ...                    # text / toolUse / reasoningContent
    elif "messageStop" in ev: stop = ev["messageStop"]["stopReason"]
    elif "runtimeClientError" in ev: raise RuntimeError(ev["runtimeClientError"])
```

- Read the stream to the end: `metadata` (usage, cache tokens) and `hookEvent`s (including
  `after_invocation`) can follow `messageStop`. A final `stopReason: tool_use` means an inline
  function is waiting for you.
- Overridable per call: `model`, `systemPrompt`, `tools`, `skills`, `allowedTools`,
  `maxIterations`, `maxTokens`, `timeoutSeconds`, `actorId`. Not: memory, hooks, environment,
  authorizer. Invoke-time `skills` are appended to the harness list (same name: invoke wins, per
  the skills guide); the other fields replace the harness value for that call.
- **Overrides are an attack surface.** Any caller who passes auth can swap the model,
  `apiBase` or `additionalParams` (LiteLLM `aws_bedrock_runtime_endpoint` redirects signed
  requests, `aws_role_name` attempts AssumeRole, OpenAI `extra_headers` overwrites
  `Authorization`), point `skills` / MCP URLs at their content, or widen `allowedTools`. Put
  your own service in front; strip or allowlist these fields; deny `sts:AssumeRole` on the role.
  The harness itself rejects a caller-supplied `toolUse` in the final message.
- `StopRuntimeSession` returns 200 but does not cancel the in-flight model call (billed to
  `maxTokens`; issue #2424, open). Keep `maxTokens` tight.
- Internal Bedrock read timeout is 120 s, not configurable (issue #1303, open).

## Versions, endpoints, Step Functions

- V1 on create; each update = new version. `DEFAULT` tracks latest; named endpoints
  (`[a-zA-Z][a-zA-Z0-9_]{0,47}`) pin `targetVersion`. Rollback = repoint the endpoint.
  Quotas (Runtime's, inherited): 1,000 versions, 10 endpoints. Production passes a `qualifier`.
  `agentcore harness endpoint create --id <id> --name prod --target-version 3`, then
  `endpoint update --id <id> --qualifier prod --target-version 4`.
- Invoking a named endpoint needs IAM on both the harness ARN and
  `.../harness/<id>/harness-endpoint/<name>`. Terraform has no endpoint resource yet.
- Step Functions: `arn:aws:states:::bedrockagentcore:invokeHarness`, Request Response only,
  PascalCase arguments, returns only the final assistant text. **The task caps at 15 minutes,
  and the harness keeps running (and billing) after the task times out or the execution is
  stopped.** Set the harness `timeoutSeconds` ≤ 900. Don't use inline functions here: there is
  no callback pattern to return the result.

## Cost

- Runtime microVM: CPU billed only when consumed (not during model / tool I/O wait), memory every
  second until the session ends, so a shorter idle timeout saves money. List prices V1 $0.0895
  vCPU-h / $0.00945 GB-h, V2 $0.1276 / $0.0169 (harness platform version undocumented).
- Tokens include prompt, history, memory, skill metadata and all allowed tool schemas. Memory,
  Gateway, Browser, Code Interpreter, CloudWatch bill separately. Tags reach managed resources only.

## Checklist

- [ ] Model, system prompt, truncation, explicit `allowedTools`; limits and idle timeout set.
- [ ] Memory deliberately on (with `actorId`) or `disabled`.
- [ ] Callers go through your service, which strips model / skills / tools overrides.
- [ ] Command/shell actions denied to non-operators; JWT inbound if tools need user identity.
- [ ] Hook Lambdas match `read` / `write` / `edit` before 12 Dec 2026.
- [ ] Production invokes a named endpoint; rollback rehearsed; Step Functions: `timeoutSeconds` ≤ 900.
