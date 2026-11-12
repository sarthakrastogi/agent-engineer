# AgentCore observability

AgentCore telemetry is OpenTelemetry delivered to CloudWatch. The vendor-neutral span model,
semconv and content-capture policy live in `agent-observability`; this file maps them onto
AgentCore. Metrics, alarms and the error runbook are in `operations.md`.

## What you get without doing anything

| Resource | Metrics | Spans | Logs |
|---|---|---|---|
| Runtime agent / harness | Yes | Needs Transaction Search + ADOT in the agent | stdout/stderr yes; `APPLICATION_LOGS`/`USAGE_LOGS` need a log delivery |
| Gateway | Yes | Yes, after tracing is enabled on the gateway | Needs a log delivery |
| Memory | Yes | Needs tracing enabled on the memory | Needs a log delivery |
| Code Interpreter / Browser | Yes | Needs tracing enabled | Needs a log delivery |
| Identity | Yes | Enable on the runtime or gateway **Identity** tab | Needs a log delivery |
| Policy | Yes | Needs enablement | Yes |

Metrics arrive in `AWS/Bedrock-AgentCore` for every resource. Spans and the GenAI
Observability dashboard need the one-time Transaction Search setup. The `agentcore` CLI does
not create vended-log deliveries; create them yourself (below).

## 1. Enable CloudWatch Transaction Search (once per account and Region)

`agentcore deploy` does this unless you ran `agentcore config transactionSearch false`. For
IaC or non-CLI accounts:

```bash
aws logs put-resource-policy --policy-name TransactionSearchXRayAccess --policy-document '{
 "Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"xray.amazonaws.com"},
 "Action":"logs:PutLogEvents","Resource":["arn:aws:logs:REGION:ACCT:log-group:aws/spans:*",
 "arn:aws:logs:REGION:ACCT:log-group:/aws/application-signals/data:*"],
 "Condition":{"ArnLike":{"aws:SourceArn":"arn:aws:xray:REGION:ACCT:*"},
 "StringEquals":{"aws:SourceAccount":"ACCT"}}}]}'
aws xray update-trace-segment-destination --destination CloudWatchLogs
# optional: index more than the free 1% for trace summaries
aws xray update-indexing-rule --name Default --rule '{"Probabilistic":{"DesiredSamplingPercentage":10}}'
```

Allow about 10 minutes before spans appear. Evaluations, A/B tests and recommendations all fail
or see nothing without it.

## 2. Instrument the agent

**Python on Runtime:** add `aws-opentelemetry-distro>=0.18.0` (use 0.21+; the docs pin
`~=0.21.0`) and start the process under the auto-instrumenter. The CLI's Python templates do
this already.

```dockerfile
CMD ["opentelemetry-instrument", "python", "main.py"]
```

- Direct code deploy: `entryPoint: ["opentelemetry-instrument", "main.py"]`, with ADOT vendored
  as arm64 wheels in the zip.
- **TypeScript:** `agentcore deploy` currently adds no OTel wiring for TS agents, so spans are
  silently dropped. Add `@aws/aws-distro-opentelemetry-node-autoinstrumentation` and use
  `["opentelemetry-instrument", "app.js"]` (or your Docker `CMD`).
- **Harness:** traced automatically, nothing to add.

Framework instrumentation. The AgentCore Evaluations service identifies spans by the scope name
in this table:

| Framework | Install | Scope name the service expects |
|---|---|---|
| Strands (Py) | `strands-agents[otel]` (native OTel) | `strands.telemetry.tracer` |
| Strands (TS) | `@strands-agents/sdk >= 1.5.0` | `strands-agents` |
| LangGraph (Py) | `opentelemetry-instrumentation-langchain>=0.55.0` (OpenLLMetry) **or** `openinference-instrumentation-langchain>=0.1.62` | `opentelemetry.instrumentation.langchain` / `openinference.instrumentation.langchain` |
| OpenAI Agents, LlamaIndex, Google ADK, Claude Agent SDK, Vercel AI | See the supported-frameworks page for each | per page |
| Anything else | Emit OTel GenAI (`gen_ai.operation.name` = `invoke_agent`/`execute_tool`/`chat`) or OpenInference (`openinference.span.kind`) | `opentelemetry.instrumentation.*` or `openinference.instrumentation.*` |

Pick **one** instrumentor per library. ADOT loads its own `aws_*` instrumentors unless a
third-party one for the same library is installed (`AWS_GENAI_INSTRUMENTATION=auto|enabled|disabled`).
Hand-rolled spans around raw `bedrock-runtime.converse()` calls without `gen_ai.*`
attributes are rejected by evaluations with "Provided input has no spans to evaluate".

## 3. Where the data lands

| Data | Location |
|---|---|
| stdout/stderr | `/aws/bedrock-agentcore/runtimes/<agent_id>-<endpoint>/[runtime-logs] <UUID>` |
| OTel structured logs (and split-mode content) | same group, stream `otel-rt-logs` |
| Spans, unified mode | same group, stream `spans` |
| Spans, split mode | shared `aws/spans` log group |
| Gateway / Memory vended logs | `/aws/vendedlogs/bedrock-agentcore/{gateway|memory}/APPLICATION_LOGS/<id>` (default) |
| Evaluation results | `/aws/bedrock-agentcore/evaluations/results/<config-id>` |

**Unified vs split spans.** Agents created on or after **2026-07-20** send spans to their own
log group by default; older agents keep `aws/spans`. Override with
`UNIFIED_TRACES_DESTINATION_ENABLED=true|false` on the runtime. Unified needs all three of:
Transaction Search, `logs:PutResourcePolicy` on the agent's log group in the **execution
role**, and ADOT 0.18.0 or later (older ADOT silently ignores the setting). Switching doesn't
move old spans. Prefer unified: one group to encrypt, restrict and export, and online
evaluations by `logGroupNamePrefixes` require it.

## 4. Vended logs and tracing for non-runtime resources

The console's **Log delivery** and **Tracing** panes work for small setups. For IaC, use
CloudWatch Logs deliveries (the same calls exist in Terraform and CloudFormation):

```python
logs.put_delivery_source(name=f"{rid}-logs", logType="APPLICATION_LOGS", resourceArn=arn)
logs.put_delivery_source(name=f"{rid}-traces", logType="TRACES", resourceArn=arn)
dst = logs.put_delivery_destination(name=f"{rid}-cwl", deliveryDestinationType="CWL",
        deliveryDestinationConfiguration={"destinationResourceArn": log_group_arn})
xr = logs.put_delivery_destination(name=f"{rid}-xray", deliveryDestinationType="XRAY")
logs.create_delivery(deliverySourceName=f"{rid}-logs", deliveryDestinationArn=dst["deliveryDestination"]["arn"])
logs.create_delivery(deliverySourceName=f"{rid}-traces", deliveryDestinationArn=xr["deliveryDestination"]["arn"])
```

The log group name must start with `/aws/vendedlogs/`. Use `logType="USAGE_LOGS"` on a runtime
for per-session CPU and memory at 1-second granularity. The destination can be CWL, S3 or
Firehose.

## 5. Correlation: sessions, traces, users

- The session ID is the conversation key. Runtime injects `session.id` into spans when you
  invoke with `runtimeSessionId` (the `X-Amzn-Bedrock-AgentCore-Runtime-Session-Id` header).
  Outside Runtime, set it yourself:
  `ctx = baggage.set_baggage("session.id", sid); token = context.attach(ctx)`.
- Pass `traceparent` (or `X-Amzn-Trace-Id`) from your front end to join its trace. Built-in tool
  and Identity APIs also accept these headers.
- **Lambda callers:** if the Lambda's trace is `Sampled=0`, Runtime emits **no spans** for that
  invocation. Turn on X-Ray active tracing on the calling Lambda.
- Runtime's spans carry no end-user identity. Add `enduser.id` (or a pseudonymous ID) yourself.
- Label stages with `OTEL_RESOURCE_ATTRIBUTES=deployment.environment.name=prod,team=x` and
  `OTEL_SERVICE_NAME`. **Until 2026-11-16 setting `OTEL_RESOURCE_ATTRIBUTES` replaces the
  Runtime's own attributes** (`service.name`, `aws.log.group.names`, `cloud.resource_id`…),
  so include those yourself; from that date the two are merged. Env vars belong to a runtime *version*, so promoting a version from staging to
  prod keeps its staging label. Use one runtime per stage, or a new version per stage.
- The default `service.name` is `<agent-name>.<endpoint-name>`. Online evaluation, batch
  evaluation and recommendations filter on it (`serviceNames`), so don't change it casually.

## 6. Flush before the microVM freezes

Runtime freezes the microVM as soon as `/invocations` returns. `BatchSpanProcessor` exports on
a 5 s timer, so spans can be lost; one practitioner report saw 100% empty traces (SDK issue
#629, open). If traces are missing or partial, call
`trace.get_tracer_provider().force_flush()` before returning, and again after a stream drains.

## 7. Content capture and PII

- With `AGENT_OBSERVABILITY_ENABLED=true`, ADOT defaults
  `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true`. Prompts, completions and tool
  arguments and results are recorded **by default**.
- **Split mode:** ADOT moves content off the span into event records in the agent's log group.
  Outside Runtime, if that logs pipeline is disabled, the content is discarded. Set
  `AWS_GENAI_CONTENT_EXTRACTION_OPT_OUT=true` to keep content on the span.
- **Evaluations, recommendations and A/B scoring read this content.** Turning capture off
  breaks them. The practical choice is usually to keep capture on, then restrict and mask:
  - KMS-encrypt the agent's log group and give it a short retention.
  - Grant read on the log groups to a few principals only. Unified mode makes this per agent.
  - Attach a CloudWatch Logs data-protection policy to mask PII (charged by CloudWatch).
- Other places content lands:
  - Runtime `APPLICATION_LOGS` include `request_payload`.
  - Gateway vended logs include MCP request and response bodies.
  - Identity logs include request and response payloads.
- Never put secrets in `OTEL_RESOURCE_ATTRIBUTES` or `OTEL_SERVICE_NAME`; anyone who can read
  the span log group can read them.
- Redaction design and sampling rules: `agent-observability` → `references/content-capture-pii-sampling.md`.

## 8. Exporting to a third party (Langfuse, Datadog, Arize, Braintrust…)

| Goal | Do |
|---|---|
| CloudWatch only | Default ADOT path |
| Third party only | `DISABLE_ADOT_OBSERVABILITY=true` on the runtime + your own OTLP exporter (e.g. `StrandsTelemetry().setup_otlp_exporter()` reading `OTEL_EXPORTER_OTLP_ENDPOINT` / `OTEL_EXPORTER_OTLP_HEADERS`) |
| Both | Keep ADOT on and add a second span processor/exporter in code (supported in principle; recipe not documented, verify) |

`DISABLE_ADOT_OBSERVABILITY=true` removes AgentCore's injected OTel env vars. You then **lose
GenAI Observability spans, AgentCore Evaluations, recommendations and A/B scoring** for that
agent; service metrics remain. Working samples for each vendor are in
`awslabs/agentcore-samples/03-integrations/3p-observability/`.

The samples put vendor secret keys in runtime env vars. Don't do that: env vars are readable via
`GetAgentRuntime` and baked into versions. Fetch the key from Secrets Manager or an Identity
API-key provider at start-up.

## 9. Agents hosted outside AgentCore (EKS, ECS, Lambda, on-prem)

Enable Transaction Search, create a log group (and a stream for spans), run under
`opentelemetry-instrument` (Lambda: ADOT layer + `AWS_LAMBDA_EXEC_WRAPPER=/opt/otel-instrument`),
and set:

```bash
AGENT_OBSERVABILITY_ENABLED=true
AWS_GENAI_CONTENT_EXTRACTION_OPT_OUT=true
OTEL_PYTHON_DISTRO=aws_distro
OTEL_PYTHON_CONFIGURATOR=aws_configurator
OTEL_RESOURCE_ATTRIBUTES=service.name=my_agent
OTEL_EXPORTER_OTLP_PROTOCOL=http/protobuf
OTEL_TRACES_EXPORTER=otlp
OTEL_EXPORTER_OTLP_LOGS_HEADERS=x-aws-log-group=/my/agent,x-aws-log-stream=runtime-logs,x-aws-metric-namespace=bedrock-agentcore
OTEL_EXPORTER_OTLP_TRACES_HEADERS=x-aws-log-group=/my/agent,x-aws-log-stream=spans
# Lambda layer only: OTEL_AWS_APPLICATION_SIGNALS_ENABLED=false OTEL_LOGS_EXPORTER=otlp OTEL_METRICS_EXPORTER=awsemf
```

Sending spans to your own group via `OTEL_EXPORTER_OTLP_TRACES_HEADERS` also needs a Logs
resource policy that lets `xray.amazonaws.com` call `logs:PutLogEvents` on it. Without that
policy, delivery fails silently. Set session baggage (§5). The agent then shows in the same
dashboard and can be evaluated and A/B tested.

## 10. Viewing

- **AgentCore console → Observability** and **CloudWatch → GenAI Observability → Bedrock
  AgentCore** show the same data, with Overview, Sessions, Traces and Spans tabs.
- From the terminal:
  - `agentcore logs runtime --name X --since 1h --level error`
  - `agentcore traces runtime list --name X`
  - `agentcore traces runtime get <trace-id>` (harness equivalents exist)
- Cross-account monitoring (Apr 2026): a monitoring account can link up to 100,000 log groups,
  and a source account can share with up to 5 monitoring accounts. On V2, cross-account
  invocations put runtime metrics in the **owner's** account.

## Gotchas

- Transaction Search off means no traces, no evaluations and no A/B, with no error raised at
  deploy time.
- Memory and gateway spans need tracing switched on per resource; Transaction Search alone is
  not enough.
- Gateway application logs don't record policy denials. Use the Policy metrics and spans.
- Deploying a Python venv built on macOS can ship an `opentelemetry-instrument` script whose
  shebang points at the local path. The runtime then fails with "Runtime initialization time
  exceeded". Build in Linux arm64 (CodeBuild or container).
- CPU and memory usage metrics lag by up to 60 minutes, so don't page on them.
- GenAI semconv is still moving. ADOT plans to deprecate `AWS_GENAI_CONTENT_EXTRACTION_OPT_OUT`
  and keep content on span attributes. Pin ADOT and re-check traces after upgrades.

## Checklist

- [ ] Transaction Search enabled in every account and Region you deploy to (IaC, not a click).
- [ ] ADOT 0.18 or later; Python entry point under `opentelemetry-instrument`; TS wired by hand.
- [ ] Unified span destination; execution role has `logs:PutResourcePolicy`.
- [ ] Log deliveries and tracing enabled for gateway, memory, tools and identity.
- [ ] Session ID set per conversation; Lambda callers use active tracing.
- [ ] Stage and team resource attributes set, and not breaking `service.name` filters.
- [ ] Content capture decision recorded; log groups KMS-encrypted, retention set, access
      restricted, PII masked.
- [ ] A real trace inspected: root `invoke_agent`, `chat` and `execute_tool` children, token
      usage present (`agent-observability` health checks).
