# Changelog

## 0.4.0 — unreleased

- New `agentforce` skill for Salesforce Agentforce agents built in the new Agentforce
  Builder: Agent Script language, actions and target schemes, architecture patterns,
  connections and channels (messaging, web, Slack, voice, Agent API), Flow and Apex
  actions, Prompt Builder, Data 360 grounding, OmniStudio Integration Procedures, external
  integration and multi-agent (MCP, A2A, Agent Fabric), Custom Lightning Types, Trust Layer
  and guardrails, observability with trace SQL, testing, the `sf agent` lifecycle,
  coding-assistant tooling, and limits. 16 reference files, sourced from the open-source
  Agent Script compiler and docs, Salesforce's official recipes and skills repos, and
  Trailhead; thinly sourced facts are marked "(verify in your org)". Unevaluated: no A/B
  run yet.
- Hook: a Salesforce DX project with agent, planner, prompt-template or bot metadata counts
  as an LLM project; edits to `.agent`, prompt-template, planner-bundle and test-definition
  files count as behaviour changes; `sf agent test run|resume` counts as running evals;
  Agentforce wording hints the `agentforce` skill.

## 0.3.0 — unreleased

From the first behavioural A/B (`evals/ab/`), where v0.2.0 scored the same as no plugin:
- New `prompt-submit` hook (Claude Code, Codex): in an LLM project, names the agent-* skill(s)
  a request needs ("add a tool that issues refunds" → `agent-guardrails`, `agent-tools`).
  Skills loaded in only 4 of 12 imperative tasks without it.
- Eval gate: running a script written this session no longer counts as running the evals;
  the model was disarming the gate with its own mock "eval" that printed predicted scores.
- Gate and reminder text: mocked, simulated or predicted results and offline unit tests
  are named as non-evidence; evidence goes in the reply, not new files. The reminder no
  longer asks for `experiment-log.md`, which drove report-file sprawl.
- `agent-evals` rule 11 and the router's Evidence section: only a real model run is a
  result; don't write summary files the user didn't ask for.
- `evals/ab/`: the A/B harness, six tasks with pre-registered rubrics (two held out from
  tuning), blinded grading and a report.
- `agent-accuracy` intake: find and read logged failures in the repo before fixing. With the
  skill hint, runs loaded the skill but skipped the chat logs that v0.2.0 runs had read
  (unmeasured).
- Results (blinded auditor grading, Claude Code + Opus 5.5): 53% vs 34% of the rubric with vs
  without the plugin, +19 points (95% CI +14 to +25); +10 (+2 to +19) on held-out tasks.
  See docs/README.md.

## 0.2.0 — unreleased

Fixes:
- Eval gate never armed on a wording-only prompt edit (`"concise"` → `"friendly"`), the most
  common kind. The post-write hook now also reads the lines around the edit on disk.
- Anthropic's `system=` kwarg, `system:` (AI SDK / JSON) and `SYSTEM = …` constants now count
  as prompt edits.
- Any command containing "eval" counted as an eval run, so `python retrieval.py`,
  `cat evals/README.md` or `ls evals` silently disarmed the gate. Read-only commands no
  longer count, and "eval" must be a delimited token.
- Session-start profile was silent for projects with no dependency manifest; it now falls
  back to imports in top-level source files.
- NotebookEdit writes were invisible to the hooks.
- Routing eval ran inside the pack repo, where the model read AGENTS.md and the skill files
  directly instead of loading skills. It now runs each case in a copy of `evals/fixture/`.

Changes:
- Session-start note is a project profile, no longer an order to load `agent-engineer` first
  in every LLM-adjacent repo.
- One evidence rule everywhere: propose the smallest eval for a non-trivial change; if the
  user declines or it's cosmetic, make it and say it's unevaluated. Replaces the conflicting
  "no eval, no change" rules in `agent-prompting` and `agent-accuracy`.
- `agent-engineering/` is opt-in for multi-session projects; otherwise evidence goes in the
  reply or PR. Templates moved into the skills that own them.
- Router skill slimmed to the stack map, shortcuts and conventions, and narrowed to
  ambiguous or broad requests.
- Removed the `agent-architect` subagent (design needs the user in the loop). The other
  subagents preload their skill via `skills:` instead of copying its procedure.
- Cut generic material and duplicated facts from references (each fact has one home);
  removed `vector-indexes.md` and `before-after-examples.md` (merged), the GPT-4.1 quirks.
- Routing eval: negative cases inside an LLM repo, a breadth metric, `--jobs`, `CLAUDE_BIN`.

## 0.1.0 — unreleased

- First draft: router skill + 10 lifecycle skills (design, tools, prompting, context, rag,
  evals, observability, accuracy, guardrails, production).
- Subagents: agent-architect, trace-analyst, eval-engineer, rag-diagnostician, agent-reviewer.
- Hooks: session-start profile, secret-guard, behaviour-change reminder, eval-gate.
- Harness support: Claude Code, Codex CLI, Cursor, OpenCode (experimental), Gemini CLI.
