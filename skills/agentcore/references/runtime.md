# AgentCore Runtime (code agents)

Runtime hosts your own agent or tool server in a per-session microVM (or on EC2 Instances).
Use it when you write the agent loop yourself; for a config-only agent see `harness.md`, and
for the decision see `choosing-a-path.md`. MCP servers on Runtime: `mcp-servers.md`.

## Service contract

| Protocol (`serverProtocol`) | Default port | Paths | Use |
|---|---|---|---|
| `HTTP` | 8080 | `POST /invocations` (JSON or SSE), `GET /ping`, `/ws` | Agents, custom APIs |
| `MCP` | 8000 | `POST /mcp`, `GET /ping` (recommended) | Tool servers |
| `A2A` | 9000 | `POST /` JSON-RPC, `/.well-known/agent-card.json` | Agent-to-agent (`multi-agent-and-registry.md`) |
| `AGUI` | 8080 | `/invocations` (SSE), `/ws` | Interactive UIs (CopilotKit) |

- Bind `0.0.0.0`; image or native binaries must be **linux/arm64** (microVMs).
- `protocolConfiguration.port` (8000–12000; microVMs only) and `.path` override defaults; `/ws`
  stays `/ws`. Reserved: 1144, 1514, 1571, 2049, 4317–4318, 8443, 48620, 48640, 53530–53532.
- `/ping` returns HTTP 200 `{"status":"Healthy"|"HealthyBusy","time_of_last_update":<unix s>}`.
  `HealthyBusy` keeps the session alive past the idle timeout. Send `time_of_last_update` only
  when the status changes, or omit it: a value that moves on every ping stops idle sessions
  from ever being reaped and exhausts the session quota.
- HTTP errors are native, named in `x-amzn-ErrorType`. 424 `RuntimeClientError` = container
  error or never ready: read `/aws/bedrock-agentcore/runtimes/<id>-<endpoint>/runtime-logs`. 409
  `RetryableConflictException` = session provisioning/teardown: retry with backoff (SDKs retry HTTP APIs, not WebSocket).

## Minimal agent (Python)

```python
from bedrock_agentcore.runtime import BedrockAgentCoreApp
from strands import Agent

app = BedrockAgentCoreApp()
agent = Agent()                       # startup work at module scope

@app.entrypoint
def invoke(payload, context):         # context.session_id, context.request.headers
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        return {"error": "'prompt' must be a non-empty string"}
    return {"result": agent(prompt).message}

if __name__ == "__main__":
    app.run()                         # 8080; pass host="0.0.0.0" in a hand-written image
```

```bash
agentcore create --name MyProject --template agent-python-strands   # CLI v1 (choosing-a-path.md); CodeZip
cd MyProject && agentcore dev       # local server; --mode browser opens Agent Inspector
agentcore deploy                    # CDK under the hood; agentcore status --json for the ARN
agentcore invoke --runtime agent --session-id "$(uuidgen)-$(uuidgen)" --payload '{"prompt":"hi"}'
```

Bring your own code: `agentcore add runtime --name X --language python|typescript --protocol ...`
(starts as CodeZip; a `Dockerfile` in `app/<name>/` makes it a container build).

## `BedrockAgentCoreApp` essentials

- **Python** (`bedrock-agentcore`): `@app.entrypoint` handler gets `(payload)` or
  `(payload, context)`. Sync handlers run in a thread pool and async ones on a worker loop, so
  `/ping` stays responsive. Return a generator to stream SSE, or a Starlette `Response` to
  set a status code.
- Background work: `@app.async_task` (async functions only), or `add_async_task(name)` /
  `complete_async_task(id)`: the SDK reports `HealthyBusy` while they run (custom: `@app.ping`).
  They drive `/ping` only and store no results: keep your own registry, poll with the **same session ID**.
- `BedrockAgentCoreApp(debug=True)` lets any caller flip ping state via the payload: off in prod.
- **TypeScript** (`bedrock-agentcore`, 0.x): `new BedrockAgentCoreApp({ invocationHandler:
  { requestSchema, process }, pingHandler, websocketHandler }).run()`; local curl must send
  `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` or `/invocations` returns 400.
- Validate the payload with a schema (`prompt` must be a string). Strip caller-supplied
  `toolUse` blocks: the payload is attacker-controlled (`policy-and-guardrails.md`).

## Container vs direct code deploy

| If… | Then |
|---|---|
| Package ≤ 250 MB zipped / 750 MB unzipped, standard deps | Direct code (`CodeZip`): faster updates, AWS patches the language runtime |
| Needs system packages, git/npm in shell, > 250 MB, existing image CI | Container in ECR (≤ 2 GB); you rebuild for patches |
| Python 3.10 or 3.11 | Move to `PYTHON_3_12`+ now: deprecated 2026-06-30, updates blocked from 2026-08-31 |

- Code runtimes: `PYTHON_3_12`–`PYTHON_3_14`, `NODE_22`; `codeConfiguration = {code:{s3:{bucket,prefix}}, runtime, entryPoint:[...]}`.
- Zips are scanned for non-arm64 `.so`/`.node` files ("incompatible with Linux ARM64"). Build with
  `uv pip install --python-platform aarch64-manylinux2014 --python-version 3.13 --only-binary=:all: --target=deployment_package -r pyproject.toml`
  or `npm install --arch=arm64 --platform=linux`.
- `entryPoint: ["opentelemetry-instrument","main.py"]` fails creation unless ADOT is in the zip
  (Node: `@aws/aws-distro-opentelemetry-node-autoinstrumentation`).
- Containers: `docker buildx build --platform linux/arm64`; > 53 layers + non-numeric `USER` = 424 "Failed to mount overlay" (use `USER 1000`).

## Sessions

- One microVM per `runtimeSessionId` (33–256 chars; the CLI accepts 33–100). Generate one per user
  conversation and reuse it. Without it every call can land on a fresh microVM (cold start, quota).
- **AgentCore does not bind sessions to users.** Your backend maps users to session IDs and caps them.
- States: Active → Idle → Stopped (idle timeout, max lifetime, `StopRuntimeSession`, failed health).
  The next call with the same ID boots a **new** microVM with empty memory and disk. Persist
  state in Memory (`memory.md`) or session storage.
- `lifecycleConfiguration`: `idleRuntimeSessionTimeout` (default 900 s) ≤ `maxLifetime`
  (default 28800 s). Both are 60–28800 s on microVMs and up to 1209600 s (14 days) on Instances.
- Running sessions keep the **old code** after `UpdateAgentRuntime`: test with a new session ID.
  `StopRuntimeSession` when a conversation ends (frees quota).
- V2 session context keys `aws:AgentCoreRuntimeSourceSid` / `...SourceArn` scope S3/DynamoDB per
  session (create clients in the handler from a new `boto3.Session()`); they don't prove the call came from the microVM.

## File systems (`filesystemConfigurations`, ≤ 5, mount under `/mnt/<one-level>`)

| Type | Notes |
|---|---|
| `sessionStorage` (Preview, microVMs) | 1 GB per session, survives stop/resume, no VPC. **Reset after 14 idle days and on every runtime version update.** Not billed in preview |
| `s3FilesAccessPoint` / `efsAccessPoint` | Shared across sessions, VPC required, SGs must allow TCP 2049, role needs `s3files:`/`elasticfilesystem:` `ClientMount` (+`ClientWrite`) |
| `capacityProviderVolume` (Instances) | EBS volume, kept across stops, billed while stopped |
| FUSE / loop / NFS in your container | V2 only, container runs as root |

Session storage stores POSIX permissions but doesn't enforce them; EFS / S3 Files are full POSIX.

## Platform V1 vs V2 (`platformVersion`)

V2 restores each new session from a snapshot taken at the first healthy `/ping`. That gives
consistent cold starts, memory reclaimed after 120 s idle, 250 concurrent connections per
session (V1: 16), the `OverheadLatency` metric, session context keys and FUSE. V2 runs in 10
Regions. Omit `platformVersion` on create and you get V1.

- V2 create and update take **minutes** (V1: seconds). `/ping` must be healthy within **120 s**
  or creation fails. Poll `GetAgentRuntime` until `READY` / `*_FAILED`; earlier update or delete
  gives `ConflictException`.
- **Snapshot trap:** everything computed at import is cloned into every session. Generate
  UUIDs, tokens, keys, nonces, timestamps and `random` seeds in the handler. In Node,
  `crypto.randomUUID()`, `randomBytes`, `getRandomValues` and the `uuid` package can repeat
  across instances **even inside the handler**, so read `/dev/urandom` instead. Hostname is
  `localhost` and PID is 1 everywhere. `time.monotonic()` doesn't advance across a restore.
- Load weights and config, build and warm SDK clients at startup. Don't cache credentials, the
  Gateway tool catalog or anything that changes without a redeploy. Don't bind fixed source ports.
- Per-request child processes (stdio MCP servers) start cold after a restore and can exceed
  the Strands MCP client's 30 s start timeout: raise it or start them at startup.
- "Runtime version" (1, 2, 3…, one per update) is not "platform version" (V1/V2).

## Versions and endpoints

Every update creates an immutable version. `DEFAULT` follows the latest; custom endpoints
(`agentcore add runtime-endpoint --runtime X --name prod --version N`) stay put until moved.
Quotas: 1,000 versions, 10 endpoints per agent. Invoke with `qualifier=<name>`. Cross-account
callers need resource policies on **both** runtime and endpoint (`deployment-and-iac.md`).

## MMDSv2 (hard failure since 2026-06-30)

Invocations (incl. commands, shells, WebSocket, `GetAgentCard`) of runtimes without MMDSv2 fail
with `ValidationException` "This runtime is not MMDSv2-enabled".

- `metadataConfiguration.requireMMDSV2` exists on `UpdateAgentRuntime` / `GetAgentRuntime`
  but **not** on `CreateAgentRuntime`; CloudFormation and Terraform expose no such property.
- New runtimes appear to get `requireMMDSV2: true` by default: never-updated version-1 runtimes
  created by CDK (Apr 2026) and Terraform (Jul 2026) return it in `GetAgentRuntime` (agentcore-cli
  test fixtures). Undocumented, and older runtimes may lack it, so verify after every create:
  `aws bedrock-agentcore-control get-agent-runtime --agent-runtime-id ID --query metadataConfiguration`.
- Absent or false: `UpdateAgentRuntime` with `metadataConfiguration={"requireMMDSV2": true}`,
  passing the full current config (the API requires `agentRuntimeArtifact`, `roleArn`), then
  repoint custom endpoints to the new version.
- Code in the VM can read the execution-role credentials from MMDS, so scope that role tightly.

**Environment variables:** 4 KB total per agent (quota, V1 and V2) though the API allows 50 keys
of 5000 chars. No secrets (`identity.md`). `UNIFIED_TRACES_DESTINATION_ENABLED=true` puts spans
in the agent's own log group (`observability.md`).

## Invoking

```python
import boto3, json, uuid
rt = boto3.client("bedrock-agentcore")               # boto3 >= 1.39.8
resp = rt.invoke_agent_runtime(agentRuntimeArn=ARN, qualifier="DEFAULT",
    runtimeSessionId=f"{user_id}-{uuid.uuid4()}",   # >= 33 chars
    payload=json.dumps({"prompt": "hi"}).encode())
body = b"".join(resp["response"])                   # stream for SSE
```

- Inbound auth is **either** SigV4 **or** JWT (`customJWTAuthorizer`) per runtime, never
  both. With JWT you cannot use the AWS SDK; POST to
  HTTPS `bedrock-agentcore.<region>.amazonaws.com/runtimes/<url-encoded-ARN>/invocations?qualifier=DEFAULT`
  with `Authorization: Bearer`. `iss` must match the discovery document; `client_id` is checked
  against `allowedClients` and `aud` against `allowedAudience` when set (both if both). `identity.md`.
- **Header passthrough** (`requestHeaderConfiguration.requestHeaderAllowlist`, ≤ 20, values
  ≤ 4 KB): any non-restricted header, `X-Amzn-Bedrock-AgentCore-Runtime-Custom-*`, and
  `Authorization` (JWT runtimes only). Other `x-amz-*`/`x-amzn-*` headers, plus `Host`,
  `User-Agent`, `Origin`, `Cookie` and `X-Forwarded-*`, are blocked. Read them from
  `context.request.headers`. From boto3, add them in a
  `before-sign.bedrock-agentcore.InvokeAgentRuntime` event hook.
- **`X-Amzn-Bedrock-AgentCore-Runtime-User-Id`** sets the user for outbound OAuth
  (`GetWorkloadAccessTokenForUserId`) and is **not** verified: any principal with
  `InvokeAgentRuntimeForUser` can act as any user. Derive it server-side, deny the action where unused, prefer JWT.
- Gateway as the only way in: SigV4 runtimes get a resource policy allowing only the gateway
  role; JWT runtimes `allowedWorkloadConfiguration.hostingEnvironments[].arn` (`gateway.md`).

## Streaming, WebSocket, WebRTC, shells

- **SSE**: return a generator. Chunk ≤ 10 MB, stream ≤ 60 min, sync ≤ 15 min, payload ≤ 100 MB.
- **WebSocket**: `wss://bedrock-agentcore.<region>.amazonaws.com/runtimes/<ARN>/ws`; frames ≤ 64 KB,
  ≤ 250 frames/s, 60 min max. Auth: SigV4 presigned URL (`AgentCoreRuntimeClient.generate_presigned_url`),
  SigV4 headers or OAuth; browsers send `Sec-WebSocket-Protocol: base64UrlBearerAuthorization.<b64url
  token>, base64UrlBearerAuthorization`.
- **WebRTC** (voice): VPC mode + TURN relay (KVS managed, third-party, coturn) + outbound UDP.
- **`InvokeAgentRuntimeCommand`** (`agentcore runtime exec`): one-shot bash in the session's
  container. Command ≤ 64 KB, timeout 1–3600 s, streamed `contentDelta`, then `contentStop.exitCode`.
  A non-zero exit is not an API error. No state between commands, no git or npm unless your
  image has them. Agents created before 2026-03-17 must be redeployed.
- **`InvokeAgentRuntimeCommandShell`** (`agentcore runtime shell`): persistent PTY over WebSocket;
  reconnect with session ID + `shellId` (256 KB replay); up to 10 shells. Grant command/shell
  actions to operators only: they see every credential in the VM.
- **Session endpoints** (`allowedPorts`, 8000–12000): reach dev servers in a session; microVMs, public ingress only.

## Instances (EC2 capacity providers)

Use for sessions > 8 h (≤ 14 days), GPUs (g4dn, g5, g6, g6e, gr6, g6f, gr6f, g7e, inf2), x86_64,
or several agents on one machine (same `runtimeSessionId`, ≤ 20 agents). Capacity providers are
immutable except the description; deleting one deletes its sessions and volumes. Compute type is
fixed at create. First call waits for EC2 boot; your EC2/EBS/VPC/ASG quotas apply;
`CREATE_FAILED` → `GetCapacityProvider.statusReason`.

## Frameworks

Any framework goes inside the entrypoint. In-process state (Strands `Agent`, LangGraph
`MemorySaver`, ADK `InMemorySessionService`) dies with the microVM: use Memory or a durable
checkpointer. Pin MCP/A2A SDK majors; unpinned upgrades break at import.

## Quotas and pricing (2026-11-12)

- Active sessions 5,000 (us-east-1, us-west-2) / 2,500 elsewhere; new sessions **25 TPS per
  account, shared**; data plane 1,000 TPS shared; control plane 50 mutate / 150 Get / 25 List
  TPS (fixed); 2 vCPU / 8 GB per microVM session. Cross-account calls count against the owner.
- Billed per second (1 s minimum, peak memory, 128 MB floor); CPU not billed in I/O wait if
  nothing else runs. V1 $0.0895 vCPU-h / $0.00945 GB-h; V2 $0.1276 / $0.0169; Instances = EC2
  + 12% fee (7.8% G-series). Background threads and `HealthyBusy` loops cost money.

## Gotchas

- Python SDK `app.run()` binds `127.0.0.1` unless `/.dockerenv` or `DOCKER_CONTAINER` exists.
- Startup crashes (`ModuleNotFoundError`) surface as invoke-time 424 or timeouts. Run the image
  locally (`docker run -p 8080:8080`, curl `/ping` and `/invocations`) before deploying.
- Empty traces: flush before returning; Lambda callers need active tracing (`Sampled=0`) (`observability.md`).

## Checklist

- [ ] arm64, `0.0.0.0`, right port/path, `/ping` per contract; Python ≥ 3.12 or `NODE_22`; SDK majors pinned
- [ ] `GetAgentRuntime.metadataConfiguration.requireMMDSV2 == true` after every create
- [ ] One session ID per user conversation, mapped server-side; `StopRuntimeSession` on end
- [ ] Idle timeout and max lifetime set; background work reports `HealthyBusy`
- [ ] V2: no randomness or time captured at import; `/ping` healthy < 120 s; deploy waits for `READY`
- [ ] Inbound auth chosen; user-id header denied unless needed; gateway lock-in if fronted
- [ ] Role least-privilege, `aws:SourceArn` / `aws:SourceAccount` in trust; endpoint promoted after a fresh-session test
