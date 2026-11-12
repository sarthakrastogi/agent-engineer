---
name: agent-observability
description: >-
  Vendor-neutral tracing and monitoring for LLM agents and RAG: span model (agent, LLM,
  tool, retrieval, sub-agent) on OpenTelemetry GenAI or OpenInference, OTLP export, content
  capture and PII redaction in traces, sampling, IDs, metrics, alerts, user feedback, and
  verifying instrumentation. Use when asked to add tracing/monitoring, "what is my agent
  doing?", tool calls or traces are missing/flat, tracking tokens or cost, capturing
  feedback, or error analysis needs traces. Not for judges or datasets (agent-evals), SLOs
  and cost/latency fixes (agent-production), or stopping PII leaks (agent-guardrails).
license: MIT
metadata:
  version: 0.4.0
---

# Agent observability

Goal: one trace per turn showing what the agent saw, did and cost, readable by any OTLP
backend, verified on real traces.

Just need to see what it does now, in dev? *Debug-grade tracing* in
`references/instrumentation-setup.md`.

## Core rules

1. **Instrument with the first working version**, before evals or launch.
2. **OTel + OTLP via a Collector.** Vendor SDKs and backends are swappable options.
3. **One trace per turn, one span per model call.** Root agent span; tool, retrieval and
   sub-agent spans as children; turns grouped by conversation ID.
4. **Instrumentors first, then manual spans for what they miss:** root, tool execution,
   retrieval, rerank, guardrails, approval gates.
5. **GenAI semconv is not stable.** Set `OTEL_SEMCONV_STABILITY_OPT_IN`, record the
   version in `observability.md`, expect renames.
6. **Content capture is the user's decision.** On in dev; in production store content
   externally with a reference, or redact before export. Never record secrets.
7. **Version everything on the trace:** release, agent, prompt, model.
8. **Never sample away errors or negative feedback.** Low volume: keep 100%.
9. **Not done until a real trace passes the health checks** and every detector has been
   fed a known-bad input.

## Workflow

1. **Survey** LLM clients, frameworks, services, existing OTel/vendor SDKs, and
   `observability.md`. Ask: backend constraints, PII policy, retention, residency, volume.
2. **Pick the schema** — one per system (`references/span-model-and-semconv.md`).
3. **Wire it:** provider → instrumentors → clients; OTLP to a Collector; flush on exit
   (`references/instrumentation-setup.md`).
4. **Add manual spans** and propagate context across threads, queues, MCP and agents.
5. **Set IDs and versions;** return the trace ID to the client for feedback.
6. **Decide capture, redaction, sampling** with the user
   (`references/content-capture-pii-sampling.md`).
7. **Verify:** smoke-check one trace, add the span-tree test, run fleet and sabotage checks
   (`references/instrumentation-health.md`).
8. **Metrics, alerts, feedback, eval links** (`references/metrics-dashboards-alerts.md`).
9. **Record the schema** from `assets/observability.md` (in `agent-engineering/` if the
   project keeps one).
10. **Next:** `agent-evals` error analysis on 30–50 traces (delegate to `trace-analyst`);
    `agent-production` for SLOs and paging.

## Decision rules

| If | Then |
|---|---|
| App already has OTel | Add an exporter to the existing provider; never a second provider |
| Backend is Phoenix/Arize, or only OpenInference instruments the framework | OpenInference |
| Otherwise | OTel GenAI semconv with the opt-in |
| Tool calls visible but not results | Manual `execute_tool` spans |
| MCP spans start new traces | Propagate `traceparent` in `params._meta` |
| Sub-agents | `invoke_agent` spans with distinct names, not tool spans |
| Production with PII | External content store + reference, or Collector redaction |
| Must keep errors and slow runs | Tail sampling in the Collector, whole traces |
| Script, CLI, serverless, eval runner | `force_flush()` / `shutdown()` before exit |
| Feedback UI | Return trace ID with the answer; attach scores to that span |
| Tool returns empty/`null`/truncated without error | Set `app.tool.result_anomalous`; track abstain rate |
| Sensitive tool behind a gate | Gate emits a span; sensitive call without one = P0 alert |

## Anti-patterns

- Tracing only LLM calls: no root, no tool results, no retrieval.
- Vendor SDK as the only layer.
- Prompts logged to stdout instead of spans.
- Every function argument (configs, API keys) recorded as trace input.
- IDs in span names; huge static system prompts on every span.
- Head-sampling production to 1% and losing every rare failure.
- Dashboards of averages with no link to the failure taxonomy.
- Alerting on absolute judge scores.
- "Spans show up" treated as done.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- Tracing setup, manual spans and a span-tree test in the user's code.
- Collector config (export, redaction, sampling) if used.
- `agent-engineering/observability.md`: schema and semconv version, span hierarchy,
  attributes, PII handling, sampling, metrics with targets and alerts, feedback loop,
  verification results.

## References

- `references/span-model-and-semconv.md` — span tree, names, attributes, OpenInference
  mapping, metrics, eval event. Read when choosing a schema or naming spans.
- `references/instrumentation-setup.md` — debug-grade setup, framework entry points, init
  order, manual spans, Collector, propagation, IDs, flush. Read when wiring tracing.
- `references/content-capture-pii-sampling.md` — capture modes, what not to record,
  redaction, sampling. Read before production or with sensitive data.
- `references/metrics-dashboards-alerts.md` — readiness test, metrics, cost, alerts,
  feedback, traces ↔ evals ↔ datasets. Read when setting up monitoring.
- `references/instrumentation-health.md` — smoke checklist, no-trace triage, span-tree
  test, fleet and sabotage checks. Read after any instrumentation change.
