---
name: agent-prompting
description: >-
  Write, review and version agent system prompts: structure, altitude, tool-use policy,
  output format, few-shot examples, structured outputs with validation, reasoning settings
  and per-model quirks. Use when the user writes or edits a system prompt, prompt template
  or examples; says "review my prompt", "the agent ignores my instructions", "JSON is
  invalid", "should I use CRITICAL / MUST", "turn this SOP into a prompt"; or re-tunes a
  prompt for a new model. Not for context over long runs or memory (agent-context),
  retrieval (agent-rag), tool schemas and descriptions (agent-tools), or the model
  migration process (agent-production).
license: MIT
metadata:
  version: 0.4.0
---

# Agent prompting

Goal: fewer, clearer instructions that fix observed failures, versioned like code. Length
and emphasis are not goals.

## Core rules

1. **Evidence before "better".** For a non-trivial change, propose the smallest eval that
   would show it worked (`agent-evals` → "Minimal eval"; ~20 real cases is enough while
   effects are large) and run it before and after. If the user declines, or the change is
   cosmetic, make it and say plainly that it's unevaluated.
2. **Start minimal on the strongest model.** Add a line only for a failure category seen
   in traces, never for one bad trace.
3. **Right altitude:** heuristics with reasons, not if/else trees and not platitudes. If a
   capable newcomer would be confused, the model will be too.
4. **Say what to do and why.** "Output is read aloud by TTS, so no ellipses" beats "NEVER
   use ellipses".
5. **No contradictions; at most one emphasised line.** "CRITICAL: You MUST" makes newer
   models over-trigger. Write "Use X when …".
6. **Must-always rules go in code** (schema, enum, hook, permission check). The prompt only
   explains them.
7. **Machine-read output uses structured outputs** and is still validated in code.
8. **Examples: 3–5 diverse, canonical cases**, never from the eval dev/test split.
9. **Prompts are files:** versioned, reviewed, and the version recorded on every trace.

## Workflow

1. **Gather.** Read `design.md`, `failure-taxonomy.md` and recent traces. Establish the
   model, the tools and who consumes the output (human, code, another agent). Ask the user
   for edge-case policy and tone.
2. **Baseline.** Run the eval on the current prompt and record the score.
3. **Structure** with the skeleton in `references/system-prompt-structure.md`.
4. **Tool policy.** Cross-tool "which tool when" and the action mode (act vs suggest) go in
   the prompt; per-tool detail goes in tool descriptions (`agent-tools`).
5. **Output.** If code reads it: schema, structured outputs or strict tools, validation
   with bounded retry (`references/examples-and-structured-output.md`).
6. **Reasoning.** Set thinking/effort natively. Prompt for planning only at low/minimal
   effort or for policy-heavy tool chains.
7. **Examples** only when format or borderline behaviour still fails after the
   instructions are clear.
8. **Model pass** with `references/model-quirks.md`.
9. **Prune** with `references/prompt-review-checklist.md`: delete any line whose removal
   wouldn't cause a mistake.
10. **Measure.** Re-run evals, check other categories for regressions, bump the prompt
    version, log change / hypothesis / delta / decision.
11. **Next.** Failures persist on a clean prompt → `agent-accuracy`. Long runs or
    multi-turn state → `agent-context`.

## Decision rules

| If | Then |
|---|---|
| Code parses the output | Structured outputs / strict tool schema, not "respond in JSON" prose. Validate anyway. |
| Classification or routing label | Enum in a schema or enum-typed tool argument, with an `other` value. |
| Format or style drifts | 3–5 diverse examples before more rules. |
| Agent ignores a rule | Look for a contradiction, vague altitude or buried position first. Must always hold → code. |
| Agent over-uses a tool or behaviour | Remove emphasis; state when to use it and when not. |
| SOP or policy doc is the spec | Numbered routine, one action per step, branches for missing information. |
| Prompt has grown nested if/else | Split: routing workflow, or one template with policy variables (`agent-design`). |
| Long documents (20K+ tokens) | Documents first, question last, each in `<document>` tags, "quote relevant passages first". |
| Reasoning needed | Native thinking/effort. A "think" tool only for policy-heavy sequential tool chains. |
| Switching models | Strip over-prompting, sweep effort fresh, re-run evals (`agent-production`). |
| One bad trace | Add it to the dataset; patch only when the category recurs. |

## Anti-patterns

- A laundry list of edge cases instead of a heuristic plus a few canonical examples.
- Prompt strings scattered through code and concatenated at runtime, so no one can say
  which prompt produced a trace.
- Tool schemas pasted into prompt text instead of the API `tools` field.
- Last-turn prefill to force JSON on Claude 4.6+ (returns 400).
- Asking for step-by-step reasoning inside tool arguments. Ask for "a short explanation".
- Running an automated prompt optimiser before the evals are trustworthy.
- Reusing a prompt tuned for one model family on another without re-running evals.

## Outputs

Write these to `agent-engineering/` only if the project keeps one (see `agent-engineer` → *Project record (optional)*); otherwise put them in your reply or the PR description.

- Prompt files in the user's repo (e.g. `prompts/<agent>/system.md`), versioned and loaded
  by name.
- Prompt name and version on every LLM span (`gen_ai.prompt.name` / `.version`, or
  OpenInference `llm.prompt_template.version`); see `agent-observability`.
- One `experiment-log.md` entry per prompt change, with the eval delta.

## References

- `references/system-prompt-structure.md` — section order, altitude, skeleton, tool
  steering, reasoning, prompts as code. Read when drafting or restructuring.
- `references/examples-and-structured-output.md` — choosing examples, structured output,
  schema design, validation and retry. Read when output feeds code or format drifts.
- `references/model-quirks.md` — per-model behaviour (Claude, GPT-5). Read before
  targeting or migrating to a model.
- `references/prompt-review-checklist.md` — pass/fail review checklist and report format.
  Read when asked to review or improve a prompt.
