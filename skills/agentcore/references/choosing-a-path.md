# Choosing a path on AgentCore

Four ways to run an agent on AWS as of November 2026. Pick one per agent, not per project:
a CLI project can hold harnesses and code Runtimes side by side. Architecture choices
(single agent vs workflow vs multi-agent) come first: `agent-design`.

## Decision table

| If… | Then |
|---|---|
| One agent loop: model + prompt + tools (MCP, Gateway, Browser, Code Interpreter) + memory + skills | **Harness** (`harness.md`) |
| You need a specific framework, graph/workflow orchestration, custom middleware, bidirectional streaming, or per-stage prompts | **Code on Runtime** (`runtime.md`) |
| You started on a harness and hit one of the rows above | `agentcore export harness`, then own the code (below) |
| Existing Bedrock Agents (Classic) agent | Migrate to harness by default; to code if it uses a custom orchestrator, multi-agent routing or stage prompt overrides |
| New AWS account, want Bedrock Agents Classic | Not possible: `CreateAgent` / `InvokeInlineAgent` return 403 for accounts with no Bedrock Agents use in the past 12 months |
| You want OpenAI's own agent loop (Codex exec server) with OpenAI models on Bedrock | Bedrock Managed Agents powered by OpenAI (**Preview**, below) |
| Hosting an MCP server, not an agent | `mcp-servers.md` |
| Workload in China Regions | Code on Runtime; the CLI blocks harnesses, Memory and connector targets in `cn-north-1` / `cn-northwest-1` |

## Harness: fits and limits

Fits (all config, no code): model choice and mid-session provider switching, built-in shell
and file tools, skills, managed memory, Gateway / remote MCP / Browser / Code Interpreter,
inline (client-side) functions for approvals and return-of-control, truncation, execution
limits, custom ARM64 container, VPC, EFS / S3 Files mounts, IAM or JWT inbound auth,
lifecycle hooks, versions and endpoints, Step Functions task.

Not supported (AgentCore "harness vs Runtime" grid): choice of framework, bidirectional
streaming, non-agent-loop patterns (graph, workflow). Multi-agent is limited to the built-in
`subagent` tool and agent-as-tool over MCP / Gateway. No separate harness charge; you pay for
the Runtime, model, Memory and tools it uses.

## Code on Runtime: framework choice

The Runtime is framework-agnostic: wrap the loop with `BedrockAgentCoreApp` from the
`bedrock-agentcore` Python SDK (or the TypeScript SDK) and deploy CodeZip or an ARM64 container.

| Framework | CLI v1 `--template` | Notes |
|---|---|---|
| Strands (Python) | agent-python-strands, -container, a2a-python-strands, agui-python-strands | Memory wired, model picker in the wizard; same engine as the harness |
| Strands (TypeScript) | agent-typescript-strands | No LiteLLM; use an OpenAI-compatible endpoint |
| LangChain / LangGraph | agent-python-langchain | No Memory scaffolding |
| Vercel AI SDK | agent-typescript-vercel | TypeScript only |
| Google ADK, OpenAI Agents SDK, CrewAI, LlamaIndex, Claude Agent SDK, custom | none: agent-python-minimal or `agentcore add runtime --language python --protocol HTTP` | v0's ADK and OpenAI Agents templates were dropped in v1 |
| MCP server | mcp-python-fastmcp | See `mcp-servers.md` |

- Before picking CrewAI, check evals: AgentCore Evaluations parses Strands, LangGraph, OpenAI
  Agents, Vercel AI, LlamaIndex, Google ADK and Claude Agent SDK telemetry. Anything else
  goes through generic OTel mapping (`evaluations-and-optimization.md`).
- Non-interactive `agentcore create --template` uses the template's default model provider; run
  the wizard to pick another.

## Bedrock Agents Classic migration

Status: renamed "Bedrock Agents Classic"; closed to new customers from **30 July 2026**.
Maintenance mode, no end-of-life date, no new features, model catalogue frozen at that date.
Accounts with Bedrock Agents use in the past 12 months are allowlisted per account; there is
no exception process. Existing agents, KBs, Guardrails, APIs and IaC keep working.

| Classic | Harness equivalent |
|---|---|
| Action group (OpenAPI / function schema + Lambda) | Gateway target (Lambda / OpenAPI) attached as an `agentcore_gateway` tool |
| Knowledge base on the agent | Gateway-fronted KB (Managed Knowledge Base or a KB tool behind Gateway); no native KB field |
| `AMAZON.UserInput`, return of control | `inline_function` tool; your client answers and resumes |
| `AMAZON.CodeInterpreter` | `agentcore_code_interpreter` tool |
| Agent instruction | `systemPrompt` (`system-prompt.md`) |
| Stage prompt overrides (pre-processing, KB generation, post-processing) | Not replicated; move to code |
| Guardrail on the agent | `bedrockModelConfig.additionalParams.guardrailConfig`, and Policy guardrails at Gateway (`policy-and-guardrails.md`) |
| Session TTL, memory | `lifecycleConfiguration`, managed memory with `actorId` |
| Supervisor / routing multi-agent, custom orchestrator | Code on Runtime (`multi-agent-and-registry.md`) |

Routes:
1. **To harness (recommended by AWS):** the `amazon-bedrock` skill in
   `github.com/aws/agent-toolkit-for-aws` (`core-skills/amazon-bedrock`) inventories the agent,
   checks eligibility, writes a plan, pauses for approval and drives the AgentCore CLI. It never
   modifies the source agent.
2. **To code:** `agentcore add runtime --name <n> --agent-id <id> --agent-alias-id <alias>
   --framework strands|langgraph`. The alias must point at a prepared version, not DRAFT.
   Review the generated code and redeploy action groups behind Gateway.

Do not copy the CLI commands on the Classic migration page: `agentcore add tool`, `--model-id`,
`--tools agentcore-browser,...` and `--actor-id` are v0 syntax, and its example name
`my-research-agent` is not a valid harness name (letters, digits, underscores only).

## Bedrock Managed Agents powered by OpenAI (Preview)

A Bedrock product, not AgentCore: sessions on the `bedrock-mantle` endpoint
(`/openai/v1/agents/sessions`) in `us-east-1`, `us-east-2`, `us-west-2`; OpenAI models only;
tools and commands run in an execution environment you supply (your host, or an AgentCore
Runtime built from the `environment-python-bma` template) through `codex exec-server`.
Preview limits: no subagents, no built-in long-term memory, no cross-Region inference
profiles, text input, no customer-managed KMS key. Skills are filesystem-only.
Use it only when you specifically want OpenAI's agent loop. To use OpenAI models with AWS's
loop, a harness takes `openAiModelConfig` or `bedrockModelConfig` with `apiFormat: responses`.

## Graduating harness to code

Export when you need something the harness can't express, or want reviewable source. Don't
export just to "own it" if the harness already does the job: export loses features.

```sh
agentcore export harness --name my_agent          # harness in this project
agentcore export harness --arn <harness-arn> --project-name ExportedAgent   # outside a project
agentcore build && agentcore deploy               # after reading EXPORT_NOTES.md
```

Carried over: model and key references, remote MCP and inline-function tools, `shell` / file
tools, a project memory referenced **by name**, limits, truncation, S3 and Git skills, mounts,
authorizer. Output is Python, Strands, always CodeZip.

Dropped or refused (listed in `EXPORT_NOTES.md`; read it every time):
- Gateway, Browser and Code Interpreter tools; wire them yourself. (CLI 0.31 did export
  IAM-auth Gateway tools; 1.x regressed: issue #2766, closed 9 Nov, after the 1.0.2 release.)
- AWS catalog skills (omitted); path skills (export refuses the harness).
- Managed memory and memory referenced by ARN; add `agentcore add memory`, reference it by
  name, export again.
- Custom container / Dockerfile. Hooks are not in the carried list: re-implement them.
- Behaviour drift: the exported agent is a plain Strands agent with hand-written tools, not
  the managed Strands Harness built-ins (`subagent`, `web_fetch`, offloading) (issue #2485,
  open). Re-run your evals on the export before switching traffic.

Export by ARN outside a project creates a project whose default target is the source account
and Region; edit `aws-targets.json` before deploying elsewhere.

## Install the CLI (v1)

```sh
pip uninstall bedrock-agentcore-starter-toolkit   # deprecated Python CLI; also provides `agentcore`
npm install -g @aws/agentcore                      # Node.js 20.12+
agentcore --version                                # must print 1.x
```

- If `--version` errors or prints 0.x, another `agentcore` is earlier on `PATH` (old toolkit,
  often on Windows, or a pinned 0.x npm install). Uninstall, open a new terminal, re-check with
  `which -a agentcore` / `where agentcore`. The npm preinstall warning is invisible on npm 8+.
- Update with `agentcore update`. Convert v0 projects with `agentcore migrate`
  (`harness.json` becomes `harness.yaml`).
- Never follow starter-toolkit tutorials (`agentcore configure` / `launch`). Samples in
  `awslabs/agentcore-samples` may pin `@aws/agentcore@0.30.0`; translate them to v1.
- Requires Python 3.10+ and `uv` for Python templates. No local Docker needed: container
  builds run in CodeBuild.

## Project layout

```sh
agentcore create --name SupportBot --template empty   # explicit; avoids wizard defaults
cd SupportBot
agentcore add harness --name assistant                # or: agentcore add runtime ...
agentcore deploy
```

```text
SupportBot/
  agentcore/
    agentcore.json      # project spec (schema version 2): runtimes, harnesses, memory, gateways, credentials
    aws-targets.json    # deployment targets
    .env.local          # local secrets (API keys); never commit
    cdk/                # generated CDK app (@aws/agentcore-cdk); lib/cdk-stack.ts is editable
  app/
    assistant/
      harness.yaml
      system-prompt.md
```

`agentcore create --name X` with no template is documented both as "creates a harness
project" and as defaulting to `empty`; pass `--template` explicitly. With a harness project
of ≤15 characters, the harness takes the project name; longer names are shortened with a hash.

`aws-targets.json` is an array. The first `agentcore deploy` creates `default` from your current
account and Region. Add others before use; names are letters and digits only:

```json
[{"name": "default", "account": "111122223333", "region": "us-west-2"},
 {"name": "prod", "account": "444455556666", "region": "us-west-2"}]
```

`agentcore deploy --target prod`. `agentcore build` only synthesises; it is not a diff. For a
diff: `cd agentcore/cdk && npm run cdk -- diff`. Promotion strategy: `deployment-and-iac.md`.

## Checklist

- [ ] Path chosen per agent with the decision table; harness unless a "Then code" row applies.
- [ ] Classic agents: eligibility checked, mapping table applied, no v0 commands copied.
- [ ] Starter toolkit uninstalled; `agentcore --version` prints 1.x.
- [ ] `--template` passed explicitly; `.env.local` in `.gitignore`.
- [ ] `aws-targets.json` has one entry per environment account/Region.
- [ ] Before any export: `EXPORT_NOTES.md` read, dropped features rewired, evals re-run.
