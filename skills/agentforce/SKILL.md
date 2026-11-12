---
name: agentforce
description: >-
  Builds, wires, tests, secures and operates Salesforce Agentforce agents in the new
  Agentforce Builder: Agent Script (.agent), subagents, actions (Flow, Apex, prompt
  templates, Integration Procedures, External Services, MCP), Prompt Builder, Data Cloud /
  Data 360 retrievers, Custom Lightning Types, channels (messaging, web, Slack, voice, Agent
  API), Trust Layer and guardrails, trace SQL, testing and the `sf agent` CLI. Use for a
  Salesforce DX project with agent metadata, or when the user mentions Agentforce, Agent
  Script, Einstein, Prompt Builder, Data Cloud, OmniStudio or `sf agent`. Generic method
  (evals, prompts, RAG) stays in the agent-* skills.
license: MIT
metadata:
  version: 0.4.0
---

# Agentforce

An Agentforce agent is a compiled Agent Script file plus the platform pieces it calls.
Put what must never vary in script logic and code; leave the model only the choices that
need judgement.

## Core rules

1. **Pick the agent type first.** `AgentforceServiceAgent` (customers; runs as one
   Einstein Agent User in `access.default_agent_user`; messaging, web, voice, email) vs
   `AgentforceEmployeeAgent` (staff; runs as the logged-in user; Lightning panel, mobile,
   Slack). It fixes identity, channels and which constructs are legal.
2. **Code decides, prose suggests.** `if`, `run`, `set`, `transition to` and
   `available when` are deterministic; `|` instruction text is only a hint to the model.
   Verification, ordering and permission checks go in logic, never in instructions.
3. **Every turn enters at `start_agent`.** Logic runs top to bottom before the model, so
   put deterministic transitions (verification gates) first.
4. **The model can only call what `reasoning.actions` lists and `available when` allows.**
   It sees stored values only through `{!@variables.x}` templates.
5. **Trusted values flow by binding, not by the model.** Bind identity with
   `with customer_id = @variables.verified_customer_id`; gate flags (`*_verified`,
   `*_confirmed`) are set only from deterministic action outputs, never `@utils.setVariables`.
6. **Every targeted action declares `outputs:`.** Input and output names match the flow
   variable or `@InvocableVariable` exactly. Hide IDs and PII with `filter_from_agent: True`.
7. **Actions are narrow, typed and fail with a message.** Flows have fault paths; Apex
   returns `isSuccess` / `errorMessage` instead of throwing; writes re-check ownership and
   are idempotent. Don't rely on `require_user_confirmation`.
8. **The Trust Layer is not authorization.** It masks (not in agent prompts), scores
   toxicity and logs. Least-privilege permission sets on the agent user, script gates and
   checks inside the action code decide access.
9. **Turn on session tracing and Audit & Feedback before launch.** There is no backfill;
   production debugging is SQL over the `AiAgent*__dlm` tables.
10. **Validate, preview, test, then publish.** Deploy only stages a draft; publish creates
    an inactive version; activation is a release step that needs a human. `sf agent test run`
    exits 0 on failed assertions, so gate CI on parsed results.
11. **Test every real channel.** Preview skips channels, routing, linked variables, CLTs.
12. **Budget Flex Credits.** Actions cost credits, test runs included; logic is free.

## Workflow

1. **Survey.** `aiAuthoringBundles/*/*.agent`, legacy `genAiPlannerBundles` / `bots`,
   flows, invocables, templates, IPs; agent type; target org (`IsSandbox`).
2. **Scope with `agent-design`.** Success criteria, the smallest shape (one `start_agent`
   for one domain; a router only for several real domains), human handoff points.
3. **Write the script** per `agent-script-language.md` and `architecture-patterns.md`:
   variables (mutable vs linked), gates, subagents, transitions, escalation.
4. **Build each action** (table below, `actions.md`); describe every action and input for
   the planner (`agent-tools`).
5. **Ground** with Prompt Builder and Data 360 (`prompt-builder.md`, `data-cloud.md`);
   measure retrieval with `agent-rag`.
6. **Secure** per `trust-layer-and-guardrails.md`; untrusted content + private data + an
   outbound action → `agent-guardrails` lethal-trifecta check.
7. **Connect channels** and rich UI (`connections-and-channels.md`, `lightning-components.md`).
8. **Validate and test** per `coding-assistant-tooling.md` and `testing-and-evals.md`:
   `sf agent validate` → preview → test suite in a sandbox; failures feed `agent-evals`.
9. **Release** per `dev-lifecycle-and-cli.md`: publish, human approval, activate,
   watch traces (`observability-and-trace-sql.md`), keep the previous version for rollback.

## Which action target?

| Need | Target |
|---|---|
| Record reads/writes, logic an admin will maintain | Autolaunched Flow (`flow://`) |
| Complex logic, bulk queries, callouts, strict typing | Apex `@InvocableMethod` (`apex://`) |
| Generated text (summary, email, classification) with grounding | Prompt template (`prompt://`) |
| OmniStudio org, multi-source fetch + transform, cached reads | Integration Procedure |
| External REST API with an OpenAPI spec | External Service via Named Credential |
| An existing external MCP server | Agentforce MCP client |
| Answers from knowledge articles or documents | Data Library / retriever |
| Unified profile, insights, segments | Data 360 actions, data graphs |
| Rich input or output card in the chat | Custom Lightning Type + LWC |
| Another agent owns the job | Delegation, A2A, Agent Fabric |

## Anti-patterns

- Business rules ("verify first", "never refund over $500") only in `|` instructions.
- An LLM-set `confirmed` or `verified` flag used as an authorization gate.
- One subagent per object or API instead of per user goal; routers for single-domain agents.
- A model-supplied record ID passed straight to a write action.
- Using the auto-generated `NextGen_*` permission set, or an admin as the agent user.
- Testing only in Builder preview; running test suites against production.
- Activating from a coding agent without human approval.
- Hand-writing template `versionIdentifier`s.
- New automation on the retired Salesforce DX MCP server (use `sf` + sf-skills).

## Outputs

The `.agent` bundle plus the flows, Apex, templates, IPs, CLTs and permission sets it
needs; test spec and results. Record agent type, subagents, actions (target, risk),
channels and gates in `agent-engineering/design.md` if the project keeps one, else the PR.

## References

Read only what the task needs. "(verify in your org)" marks thinly sourced facts;
"ADLC" is Salesforce's `agentforce-adlc` skills repo.

| File | Read when |
|---|---|
| `agent-script-language.md` | Writing script: blocks, variables, expressions, annotated agent, errors |
| `actions.md` | Adding or debugging any action: targets, bindings, chaining, gating |
| `architecture-patterns.md` | Choosing a shape; models; migrating legacy agents |
| `connections-and-channels.md` | Messaging, web, Slack, voice, Agent API, handoff |
| `flows-and-apex.md` | Flow or Apex actions; flows/Apex that call agents or models |
| `prompt-builder.md` | Prompt templates, grounding, versions |
| `data-cloud.md` | Data 360, search indexes, retrievers, Data Library |
| `integration-procedures.md` | OmniStudio IPs and Data Mappers as actions |
| `external-integration-and-multi-agent.md` | Credentials, External Services, MuleSoft, MCP, A2A |
| `lightning-components.md` | Custom Lightning Types, LWCs, agent surfaces |
| `trust-layer-and-guardrails.md` | Trust Layer, identity, injection, red-team, launch checklist |
| `observability-and-trace-sql.md` | Trace data model, SQL recipes, analytics, alerts |
| `testing-and-evals.md` | Preview, test specs, CI gate, test sets |
| `dev-lifecycle-and-cli.md` | Metadata, `sf agent` commands, versions, CI/CD |
| `coding-assistant-tooling.md` | LSP, offline validation, build loop, approvals |
| `gotchas-and-limits.md` | Limits, Flex Credits, error messages → fixes |
