# MCP servers on AgentCore Runtime

How to host a remote MCP server on Runtime and connect clients to it. Tool design (names,
schemas, errors, result size) is the same as anywhere else: see `agent-tools` →
`references/mcp-servers.md`. MCP trust and auth rules: `agent-guardrails`. Runtime basics
(sessions, V2, MMDSv2, quotas): `runtime.md`. Aggregating many servers behind one endpoint:
`gateway.md`.

## Contract

- `protocolConfiguration.serverProtocol = "MCP"` (CLI `--protocol MCP`). Without it the runtime
  is HTTP and you lose MCP behaviour such as JSON-RPC error envelopes.
- Streamable HTTP on `0.0.0.0:8000/mcp`, arm64. Port and path can be changed with
  `protocolConfiguration`.
- Implement `GET /ping` at the root of the serving port, returning 2xx
  `{"status":"Healthy"}` (or `HealthyBusy` during background work). Without it the platform
  falls back to JSON-RPC `ping` POSTs on your MCP path. Servers on protocol 2026-07-28 may
  reject those, which leaves the platform only a weak health signal.
- The platform forwards `Mcp-Session-Id` and `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` to
  your server. **Do not reject requests carrying a session ID your server didn't mint.**
- The remote URL is HTTPS
  `bedrock-agentcore.<region>.amazonaws.com/runtimes/<URL-encoded ARN>/invocations?qualifier=DEFAULT`
  (`:` → `%3A`, `/` → `%2F`). Clients must send `Accept: application/json, text/event-stream`,
  otherwise they get 406 / -32011.

## Minimal server (MCP Python SDK v2)

```python
# main.py   pin: mcp>=2.3,<3   (v1 API: `from mcp.server.fastmcp import FastMCP`, pin mcp>=1.30,<2)
from mcp.server import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse

mcp = MCPServer()

@mcp.tool()
def add_numbers(a: int, b: int) -> int:
    """Add two integers."""
    return a + b

@mcp.custom_route("/ping", methods=["GET"])
async def ping(request: Request) -> JSONResponse:
    return JSONResponse({"status": "Healthy"})

if __name__ == "__main__":
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8000, stateless_http=True)
```

```bash
agentcore create --name McpProject --template empty && cd McpProject
agentcore add runtime --name Tools --language python --protocol MCP \
  --authorizer-type CUSTOM_JWT \
  --authorizer-configuration '{"customJwtAuthorizer":{"discoveryUrl":"<issuer>/.well-known/openid-configuration","allowedAudience":["<api-id>"]}}' \
  --request-header-allowlist Authorization
cp ../main.py app/Tools/main.py
(cd app/Tools && uv init --bare && uv add "mcp>=2.3,<3" "aws-opentelemetry-distro~=0.21.0")
agentcore deploy && agentcore status --json    # Runtime "arn" field
```

Or start from the `mcp-python-fastmcp` template. For a container build, write a Dockerfile with
`FROM --platform=linux/arm64 python:3.12-slim` and `EXPOSE 8000`, then `docker buildx build --platform linux/arm64`.

## Stateless vs stateful

| If… | Then |
|---|---|
| Plain tools, any protocol version | Stateless (`stateless_http=True`). Keep state in your own store and pass explicit state handles in tool results |
| Elicitation, sampling or progress notifications on protocol ≤ 2025-11-25 | Stateful (`stateless_http=False`). The client sends `initialize` without a session ID and echoes the returned `Mcp-Session-Id` |
| Same features on protocol 2026-07-28 | Stay stateless; they use multi round-trip requests (MRTR) |
| Long jobs | Return a job ID and report `HealthyBusy` from `/ping` while it runs, or implement the MCP Tasks extension (2026-07-28) and expose the server through Gateway, which supports it (`gateway.md`) |

Stateful sessions live in the microVM. When the session stops (idle timeout, 8 h maximum,
`StopRuntimeSession`, health failure), state is gone and the client gets 404 and must
re-initialise. A session also keeps running the code version it started on, so test a
redeploy on a new session.

## Session routing (microVM stickiness)

- **≤ 2025-11-25**: routing uses `Mcp-Session-Id` only. The platform returns one; the client
  must echo it on every request.
- **2026-07-28+**: routing uses `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`. `Mcp-Session-Id`
  is still accepted and wins if both are present.
- **Trap:** the MCP Python SDK 2.x client in its default `mode="auto"` negotiates 2026-07-28 via
  `server/discover` and never echoes a session ID. Every request then cold-starts a new microVM
  (one report measured 31 s versus 5.6 s) and burns the 25 TPS new-session quota. Set
  `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` (≥ 33 chars, one per user conversation) as a
  default header on the client's HTTP client.
- Map MCP sessions to users yourself; the platform doesn't (see `runtime.md`, Sessions).

## Inbound auth

| Client | Inbound auth | How |
|---|---|---|
| Your backend or agent in AWS | SigV4 (default) | `InvokeAgentRuntime`, or the MCP client signing requests with `mcp-proxy-for-aws` as a library |
| Claude Code, Cursor, Kiro or other local client with AWS credentials | SigV4 | Local stdio proxy: `uvx mcp-proxy-for-aws-cli@latest <runtime MCP URL> --service bedrock-agentcore --region <r> [--profile p]` |
| IDE or chat client for end users with an IdP | JWT (`customJWTAuthorizer`) | Client OAuth via the 401 `WWW-Authenticate: Bearer resource_metadata=".../.well-known/oauth-protected-resource?qualifier=..."` |
| Many servers, central policy, per-tool authz | Gateway in front | `gateway.md` |

- A runtime takes SigV4 **or** JWT, never both. You can run two endpoints or versions, or put
  a gateway in front.
- JWT: set `allowedAudience` and/or `allowedClients`, plus `allowedScopes` and `customClaims`
  where your IdP supports them. With Dynamic Client Registration (DCR), client IDs are unknown
  in advance, so validate audience. Use `advertisedScopeMapping` when the scope clients request
  differs from the scope in the token. Allowlist `Authorization` only if the server must read
  the token, and never pass it downstream; use Identity token exchange instead (`identity.md`).
- Auth0 with DCR must receive the `audience` parameter, or it issues opaque or JWE tokens that
  Runtime rejects. The MCP Python SDK's OAuth requests send no `User-Agent`, which some WAF
  rules block.
- Authorise each tool call against the user in the validated token, never against a tool
  argument (`agent-guardrails`).

## Fronting with Gateway vs exposing directly

| Expose the runtime directly when | Put a Gateway in front when |
|---|---|
| One server, one or a few known clients | Several servers or targets should look like one MCP endpoint |
| Client auth fits SigV4 or one JWT authorizer | You need Cedar policy, guardrails, interceptors, rate limits or tool search |
| You want the fewest hops | You need outbound OAuth or token exchange per target, or audited central access |

- MCP-server target: encode the URL. Supported protocol versions are 2026-07-28, 2025-11-25,
  2025-06-18 and 2025-03-26. Outbound auth can be IAM (Runtime verifies SigV4), OAuth
  (2LO, 3LO, token exchange) or an API key.
- In DEFAULT listing mode, call `SynchronizeGatewayTargets` after any change to tools,
  prompts or resources; the gateway serves its cached catalog until you do.
- On ≤ 2025-11-25, enable gateway MCP sessions or allow `Mcp-Session-Id` as both request and
  response header in the target's `metadataConfiguration`. Otherwise the gateway
  re-initialises on every call.
- Then lock the runtime to the gateway: for SigV4, a resource policy denying every principal
  except the gateway role; for JWT, `allowedWorkloadConfiguration.hostingEnvironments`.

## Errors clients see

Runtime wraps its errors in JSON-RPC. On **≤ 2025-11-25** most errors arrive as **HTTP 200**
with an `error` body (only 401, 403 and 406 differ), so inspect the body. On **2026-07-28+**
HTTP status reflects the error.

| Exception | JSON-RPC | Notes |
|---|---|---|
| Throttling / ServiceQuotaExceeded | -32003 | Back off |
| RetryableConflict | -32005 | Session provisioning: retry. MCP clients don't auto-retry |
| RuntimeClientError | -32010 | Your server failed (424 on new versions): check runtime-logs |
| AccessDenied | -32002 → -20001 (new) | |
| ResourceNotFound | -32004 → -32602 (new) | |

Status codes from your own server pass through only for 200 and 202 (legacy) or
200/202/400/401/403/404 (new). Any other status becomes 200 (legacy) or 424 (new).

## Testing

1. Locally: `python main.py`, then `npx @modelcontextprotocol/inspector` (UI on
   `localhost:6274`), connect to `localhost:8000/mcp` (plain HTTP), and list and call every tool.
2. Deployed: in the Inspector, set Streamable HTTP and the encoded runtime URL above, and add
   header `Authorization: Bearer <token>` (JWT runtimes) or go through the SigV4 proxy.
   `agentcore invoke --runtime Tools --payload '<JSON-RPC>'` also accepts `--mcp-method`,
   `--mcp-session-id`, `--mcp-protocol-version` and `--bearer-token`.
3. Repeat the calls with one fixed session ID and check `ActiveSessionCount` /
   `AgentCore.Runtime.Invoke` spans show **one** session, not one per request.
4. Test expired token, wrong audience, missing `Accept` and a stopped session (stateful mode:
   expect 404, then re-initialise).
5. Then run the evals from `agent-tools` with the real consuming agent.

## Gotchas

- **Unpinned `mcp`** now resolves to 2.x: `mcp.server.fastmcp` raises `ModuleNotFoundError`
  and the runtime never becomes healthy. Clients importing `streamablehttp_client` fail. Pin
  the major on both sides.
- **Host and DNS-rebinding guards**: Runtime proxies to your container over `127.0.0.1` but
  keeps `Host: bedrock-agentcore.<region>.amazonaws.com`. SDKs that 403 non-localhost `Host`
  on loopback requests (mcp-go by default) fail with "Received error (403) from runtime" while
  `/ping` passes. Allow that host or disable the localhost guard behind Runtime.
- **Session managers that only accept their own IDs** (some Go and TS servers) break on
  platform-injected or changed `Mcp-Session-Id` values. Run stateless, or adopt the incoming ID.
- After moving a server from SDK 1.x to 2.x (protocol 2026-07-28), some clients (for example
  Claude connectors) have failed to connect. Re-test every target client before you switch.
- **V2 snapshot**: build tool registries at startup, but create per-request IDs and fetch
  credentials in the handler (`runtime.md`).
- Each new session counts toward the active-session quota (5,000 or 2,500). Clients that lose
  stickiness exhaust it under load.

## Checklist

- [ ] `serverProtocol=MCP`; `0.0.0.0:8000/mcp`; arm64; `GET /ping` implemented
- [ ] `mcp` major pinned on server and test clients; protocol version chosen deliberately
- [ ] Stateless unless legacy-protocol elicitation, sampling or progress is needed
- [ ] Clients send a stable session header; verified one session per conversation
- [ ] Inbound auth matches the clients (SigV4 proxy or JWT with audience, scopes and claims)
- [ ] Per-tool authorisation from token identity; no token passthrough downstream
- [ ] If fronted by Gateway: target synced, sessions or headers configured, runtime locked to the gateway
- [ ] MMDSv2 verified on the runtime (`runtime.md`)
