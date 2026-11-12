---
name: agent-guardrails
description: >-
  Secures LLM agents against prompt injection, data exfiltration and excessive agency:
  threat model, lethal-trifecta check, injection-resistant architecture, least privilege,
  sandboxing, human approvals, input/output guards, MCP trust, red-teaming. Use when an
  agent reads untrusted content (web, email, docs, tool results) or gets write, send, pay,
  delete or code-execution tools; on prompt injection, jailbreaks, permissions, approvals,
  PII leaks or third-party MCP servers; and before shipping an agent that has either. Tool schemas
  belong to agent-tools; PII in traces to agent-observability.
license: MIT
metadata:
  version: 0.4.0
---

# Agent guardrails

Goal: a fully hijacked model can cause only harm the user explicitly accepted. Injection
is unsolved, so the boundary is architecture and permissions enforced in code; classifiers
and prompt wording are extra layers. Re-run on every new tool, data source or MCP server.
A chat-only bot with no tools or private data needs only `references/input-output-guards.md`.

## Core rules

1. **Lethal trifecta:** private data + untrusted content + an exfil channel in one context
   = data theft. Remove or isolate a leg.
2. **Assume injection succeeds;** the hijacked model must still be unable to reach a
   consequential sink. "Ignore instructions in documents" is not a control.
3. **Authorization in code.** The model proposes; the executor checks identity,
   arguments and policy.
4. **Least privilege:** fewest, granular tools (no shell or open fetch); credentials scoped
   to user and task, short-lived, no wildcards.
5. **Every tool result, document, memory and MCP response is untrusted.** So is model
   output: encode or parameterise before shell, SQL, HTML, markdown or URLs.
6. **Human-gate irreversible, external or uncapped actions** — only those; approval
   fatigue is a vulnerability.
7. **A 95% detector is a failing grade** against an attacker who retries. No classifier
   alone guards a sink.
8. **Secrets never enter the context;** PII only when the task needs it.
9. **Every attack found becomes an eval case** run on every release.

## Workflow

1. **Inventory from code:** input sources (user, retrieval, web, email, files, tool/MCP
   results, memory, other agents), each tool's effect (read/write/send/execute/spend) and
   credential, data sensitivity. Ask the user: who the users are, data classes, worst
   acceptable outcome, compliance regime.
2. **Trifecta per context window** and for the whole system (`references/threat-model.md`).
3. **Rate every tool** low/medium/high (`references/approvals-and-permissions.md`).
4. **Pick the architectural defence** (`references/injection-resistant-patterns.md`).
5. **Lock permissions:** scoped credentials, authz in code, rate and spend caps, sandbox,
   MCP review.
6. **Approvals on high-risk calls;** show exact arguments, execute exactly what was approved.
7. **Layer guards;** block before side effects; handle PII and secrets.
8. **Red-team:** attack success *and* benign utility; hand the set to `agent-evals` as a
   regression suite (`references/red-teaming.md`).
9. **Threat notes** into `design.md`; the user accepts residual risks by name.
10. **Next:** `agent-observability` to trace guard decisions, approvals and sinks;
    `agent-production` for rollout.

## Decision rules

| Situation | Do |
|---|---|
| All three trifecta legs | Cut a leg — usually exfil (no open fetch, allowlisted domains, no auto-rendered images) — or a pattern below |
| Fixed set of actions | Action-selector: model never sees tool output |
| Plan knowable from the trusted request | Plan-then-execute |
| Facts from many untrusted docs | Map-reduce, quarantined readers returning constrained values |
| Open-ended reasoning over untrusted data + actions | Dual LLM or provenance policy on sinks (CaMeL-style) |
| Irreversible, external send, permission change, over-cap money | Approval + code cap, or remove the tool. Capped reversible refund: Medium |
| Runs generated code | Sandbox: filesystem *and* network isolation, egress allowlist |
| Third-party MCP server | Review, pin, scope credentials; descriptions untrusted |
| Output rendered in a UI | Escape HTML, block/proxy images, allowlist link domains |
| Multi-tenant data | Tenant from the session, enforced in the data layer |

## Anti-patterns

- System prompt or a classifier as the boundary while the trifecta is open.
- The model deciding authorization ("only delete if the user is an admin").
- Shell or arbitrary fetch "for flexibility".
- Approving a prose summary instead of exact arguments; prompting on every read.
- Rendering markdown images/links from model output.
- Trusting or auto-updating MCP tool descriptions.
- Guards in parallel with tool execution.
- Red-teaming once; ASR without benign utility.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

**Risks & guardrails** in `agent-engineering/design.md`:

```markdown
## Risks & guardrails
Trifecta: private data YES (CRM) · untrusted YES (email) · exfil NO (no send; links allowlisted)
| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| LLM01 indirect injection: email body → `issue_refund` | High | High | plan-then-execute; $200 + daily per-user caps in code |
| LLM02 PII in traces | Med | Med | redact in collector (see observability.md) |
Residual risk accepted by: <name>, <date>
```

Plus: tool risk table (tool, effect, tier, control) under Architecture → Tools;
`datasets/attacks.jsonl` (listed in `eval-plan.md`); an `experiment-log.md` entry per
defence with ASR and utility deltas.

## References

- `references/threat-model.md` — entry points, OWASP IDs, exfil channels, trifecta
  worksheet, threat notes. Read at step 2.
- `references/injection-resistant-patterns.md` — six patterns, fit and cost. Read when the
  trifecta check fails.
- `references/approvals-and-permissions.md` — risk tiers, authz executor, credentials,
  sandboxing, HITL, MCP trust. Read before adding write tools.
- `references/input-output-guards.md` — guard layers, small detectors, output handling,
  PII, secrets. Read when adding guards.
- `references/red-teaming.md` — attack set, payloads, grading, metrics. Read before launch
  and on any tool, prompt or model change.
