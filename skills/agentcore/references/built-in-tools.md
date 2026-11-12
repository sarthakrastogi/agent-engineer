# AgentCore built-in tools: Code Interpreter, Browser, Web Search, Web Answer

Managed, per-session microVM tools. They give an agent code execution, a real browser and
web search without hosting anything. All are high-risk: code execution and web content
are the classic injection and exfiltration paths. Threat model and approvals live in
`agent-guardrails` (`threat-model.md`, `injection-resistant-patterns.md`,
`approvals-and-permissions.md`); this file is the AgentCore mapping.

## Pick the tool

| Need | Use |
|---|---|
| Compute, data analysis, file transforms, charts | Code Interpreter |
| Interact with a site (login, forms, JS-heavy pages, downloads) | Browser |
| Current facts, ranked results, agent is an MCP client | Web Search connector on Gateway (`gateway.md`) |
| Same, from your own code or pipeline (no MCP) | `InvokeWebSearch` API |
| One cited answer per sub-question, minimal tokens | Web Answer connector (us-east-1) |
| Read one known URL | Plain HTTP fetch in your tool; a browser session is overkill |

## Code Interpreter

**Resources.** AWS-managed `aws.codeinterpreter.v1`, or a custom interpreter
(`CreateCodeInterpreter`) when you need an execution role, a network mode, file-system mounts
or a custom root CA. Data plane: `StartCodeInterpreterSession`, `InvokeCodeInterpreter`,
`GetCodeInterpreterSession`, `ListCodeInterpreterSessions`, `StopCodeInterpreterSession`.

**Network modes** (`networkConfiguration.networkMode`):

| Mode | Reaches | Use when |
|---|---|---|
| `SANDBOX` | S3 only ("limited external network access") | Default choice; no public internet, so no `pip install` from PyPI (inferred) |
| `PUBLIC` | Public internet | Code must fetch packages or public data; highest exfiltration risk |
| `VPC` (`vpcConfig.subnets`, `securityGroups`) | Your VPC; internet only via NAT | Private DBs/APIs; private subnets, public subnets give no internet |

**Sessions.** One microVM per session (2 vCPU / 8 GB, 10 GB disk, not adjustable). Timeout
default 900 s, up to 8 h (`sessionTimeoutSeconds`). Variables and files persist across calls
within a session; `executeCode` takes `clearContext`. Sessions end on timeout or stop; files go.
Session data TTL is 30 days. Always stop sessions (or use `code_session(...)` context manager).

**Invoke operations** (`name` in `InvokeCodeInterpreter`): `executeCode` (`language`:
`python`, `javascript`, `typescript`; `runtime` `python`, `nodejs`, `deno`, JS/TS default
`deno`; `nodejs` = CJS for JS, ESM for TS), `executeCommand`,
`startCommandExecution` / `getTask` / `stopTask` (async, up to 8 h), `readFiles`, `writeFiles`,
`listFiles`, `removeFiles`. Results stream back as events.

```python
from bedrock_agentcore.tools.code_interpreter_client import code_session
with code_session("us-west-2") as ci:      # managed interpreter
    r = ci.invoke("executeCode", {"language": "python", "code": "print(2**64)"})
```
Strands: `from strands_tools.code_interpreter import AgentCoreCodeInterpreter`;
`Agent(tools=[AgentCoreCodeInterpreter(region=...).code_interpreter])`.

**File I/O.** Inline payload up to 100 MB per request; larger (up to 5 GB) via S3 from terminal
commands using the interpreter's execution role. Or mount S3 Files / EFS access points
(`filesystemConfigurations`, VPC required, TCP 2049 to the mount target). No managed session
storage, unlike Runtime.

**IAM.** The caller needs the data-plane actions above on `code-interpreter/*` (or the specific
ARN). The custom interpreter's **execution role is usable by any code the model writes**: grant
only the S3 prefixes it needs, nothing else. Trust `bedrock-agentcore.amazonaws.com` with an
`aws:SourceAccount` condition.

**Limits and price.** 1,000 concurrent sessions/account, `InvokeCodeInterpreter` 30 TPS,
`Start...Session` 30 TPS, sync request 15 min. $0.0895 per vCPU-hour + $0.00945 per GB-hour,
I/O wait not billed.

## Browser

**Resources.** AWS-managed `aws.browser.v1`, or `CreateBrowser` for recording, Web Bot Auth,
VPC, extensions, enterprise policies, root CA. Network modes: `PUBLIC` or `VPC` (`vpcConfig`; VPC needs NAT
for the internet). 1 vCPU / 4 GB, 10 GB disk per session; timeout default 900 s, max 8 h.

**Two control paths per session:**
- **CDP automation stream (WebSocket):** Playwright `connect_over_cdp`, browser-use, Nova Act,
  Strands `strands_tools.browser.AgentCoreBrowser`. One automation stream per session.
  ```python
  from bedrock_agentcore.tools.browser_client import browser_session
  with browser_session("us-west-2") as client:
      ws_url, headers = client.generate_ws_headers()
      browser = await playwright.chromium.connect_over_cdp(ws_url, headers=headers)
      page = browser.contexts[0].pages[0]          # stay in the default context
  ```
- **`InvokeBrowser` (OS level, Apr 2026):** `mouseClick`/`Move`/`Drag`/`Scroll`, `keyType`,
  `keyPress`, `keyShortcut`, `screenshot` (full desktop, PNG). For native dialogs, JS alerts
  that block CDP, OS menus. 5 TPS per account. Default viewport 1456x819; coordinates must be
  strictly inside it.

**Live view and human takeover.** Live view is DCV via a SigV4-presigned URL
(`generateLiveViewUrl`, `BrowserLiveView` component; set `remoteWidth/Height` to the session
viewport). For credentials the agent must not see: `UpdateBrowserStream` with
`automationStreamUpdate.streamStatus: DISABLED`, let the human type, then `ENABLED`.

**Profiles** (`CreateBrowserProfile`, `SaveBrowserSessionProfile`): persist cookies and
localStorage across sessions. 50 MB each, 100 per account, S3-billed since 2026-04-15. Saving
overwrites; active sessions don't see updates; expired cookies vanish. A profile is a bearer
credential for every site in it: scope IAM on `browser-profile` resources tightly.

**Proxies.** Up to 5 per session with domain patterns; auth from a Secrets Manager secret
(`username`/`password`), caller needs `secretsmanager:GetSecretValue`. Proxy must support
HTTP CONNECT. Use for stable egress IPs.

**Extensions and policies.** Chromium extension ZIPs from same-account S3 (10 MB, 10 per
session). Chrome enterprise policies from S3: managed (on `CreateBrowser`, can't be overridden)
or recommended (per `StartBrowserSession`). Use managed policies for URL allow/block lists and
`PasswordManagerEnabled: false`.

**Recording.** Custom browsers only; writes DOM changes, actions, console and network events
to your S3 bucket via the execution role. Recordings contain whatever the page showed, including
PII and tokens: encrypt, restrict, set lifecycle rules.

**Web Bot Auth (Preview).** `browserSigning: {"enabled": true}` at `CreateBrowser`; signs
requests per the draft IETF protocol so participating WAF vendors let the agent through with
fewer CAPTCHAs. Execution role needs only the trust policy. Site support varies.

**Limits.** 1,000 concurrent sessions, `StartBrowserSession` 30 TPS, `InvokeBrowser` 5 TPS,
`SaveBrowserSessionProfile` 10 TPS. Same vCPU/GB-hour rates as Code Interpreter.

## Web Search API and Web Answer

- `InvokeWebSearch` (GA 2026-11-10): `client("bedrock-agentcore").invoke_web_search(
  webSearchId="web-search.v1", searchQuery=..., maxResults=..., filters={...})`; CLI
  `aws bedrock-agentcore invoke-web-search`. IAM only: `bedrock-agentcore:InvokeWebSearch` on
  `arn:aws:bedrock-agentcore:<region>:aws:tool/web-search.v1`. No resources to create.
- Query 1-200 chars; `maxResults` 1-25 (**default 25**, connector default 10); filters
  `domainFilter.include/exclude` (<=100 each, root domain matches subdomains) and
  `publishedDateFilter.publishedAfter/publishedBefore` (connector uses `from`/`to`). Unknown
  top-level fields are a `ValidationException`. No pagination.
- 6 Regions (us-east-1, us-east-2, us-west-2, ap-southeast-2, ap-northeast-1, eu-west-1), no
  GovCloud (the connector has GovCloud). 10 TPS. $7 per 1,000 queries. PrivateLink supported.
- Web Answer: Gateway connector only, us-east-1 (geo cross-Region inference), query up to
  30,000 chars, returns markdown with numbered `[[N]]` citation-link markers plus `structuredContent.answer[].citations`.
  ~5 s server latency in the doc example. $10 per 1,000 calls.
- Both: you must show the returned citations and links to end users; no bulk storage of results.

## Injection and exfiltration

Web pages, search snippets and browser DOM are untrusted input; code the model writes is
untrusted code. Map the `agent-guardrails` rules onto these tools:
- Don't give one agent untrusted web input, private data and an outbound channel at once
  (`PUBLIC` Code Interpreter, a browser, a write tool). Split into a reader and an actor, or
  remove the channel.
- Prefer `SANDBOX`/`VPC` interpreters; treat even `SANDBOX` S3 access as a channel and scope the
  execution role to one prefix.
- Constrain browsers with managed enterprise policies (URL allowlist), Gateway Policy on any
  write tools the agent holds, and human takeover for credentials.
- Use target-level domain include lists on search connectors; the agent can't relax them.

## Gotchas

- Session Replay and Web Bot Auth depend on extensions in the **default** context;
  `browser.new_context()` silently loses them.
- `InvokeBrowser` `keyType` is ASCII-only (non-ASCII skipped); unknown key names return SUCCESS
  and do nothing; key names must be lowercase.
- Live view "Take control"/"Release" broke harness browser automation after SSO
  (sdk-python #518, open): interact without the toggle, or reconnect.
- Profiles restored cookies but not localStorage in one report (sdk-python #647, open): verify.
- Old SDK `download_file` broke on binary files (#243, fixed): pin a current `bedrock-agentcore`.
- Profiles fail on concurrent save to the same profile or session; serialise saves.
- Sessions bill until timeout: always stop them; set short timeouts for interactive use.
- Managed `aws.codeinterpreter.v1` network mode is not stated in the docs (unverified):
  create a custom `SANDBOX` interpreter when it matters.

## Checklist

- [ ] Custom interpreter/browser where you need role, network mode, recording or policies
- [ ] Network mode chosen deliberately; `PUBLIC` justified in the threat model
- [ ] Execution role scoped to one S3 prefix; no wildcard AWS access
- [ ] Sessions stopped in `finally`; timeouts sized to the task
- [ ] Human takeover (stream DISABLED) for credentials; profiles and recordings locked down
- [ ] Domain allowlists on search and browser; citations shown to users
- [ ] Web/browser content handled per `agent-guardrails` before it can drive write tools
