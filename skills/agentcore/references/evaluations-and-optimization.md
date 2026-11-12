# AgentCore Evaluations and Optimization

AgentCore Evaluations scores agent traces with LLM judges, code evaluators and trajectory
matchers. Optimization adds three pieces on top: prompt and tool-description recommendations,
configuration bundles, and Gateway A/B tests.

Decide what to measure first (`agent-evals` → error-analysis, graders-and-judges). This file
covers how to run those evaluations on AgentCore.

## Status (2026-11-12)

| Capability | Status |
|---|---|
| Evaluations: on-demand, online, built-in / custom / code-based evaluators | GA 2026-03-31 |
| Batch evaluation, Recommendations, A/B testing | GA 2026-06-17 |
| Dataset evaluation (dataset runners, `CreateDataset`…) | **Preview** |
| User simulation | Shipped with dataset runners; treat as Preview |
| Third-party evaluators (DeepEval, AutoEval) | Available (Aug 2026) |
| Insights (old preview) | **Retired.** New jobs ended 2026-10-16; recurring reports stopped after 2026-10-30. Use `Builtin.FailureAnalysis` in batch evaluation |

## Prerequisite: the spans must be readable

Every mode reads spans from CloudWatch. Before writing any evaluator, check all of these:

1. Transaction Search is on (`observability.md` §1).
2. The agent emits a supported scope with `gen_ai.*` or OpenInference attributes
   (`observability.md` §2). Otherwise you get "Provided input has no spans to evaluate".
3. Content capture is on. Judges need prompts, responses and tool input and output.
4. `session.id` is set, so session-level evaluators get a whole conversation.
5. You know the `service.name` (Runtime default `<agent-name>.<endpoint>`, often
   `MyAgent.DEFAULT`) and the log group. Every API filters by both.
6. Wait 2–5 minutes after a session before evaluating it. Online evaluation waits for the
   session to go idle (default 15 min) before scoring it.

Errors such as `LogEventMissingException` or `AgentSpanMappingException`, or no scores at all,
are almost always a telemetry problem. AWS publishes a diagnostic skill for coding assistants
(devguide `diagnose-evaluation-issues`).

## Evaluator catalogue

| Kind | IDs / config | Level | Notes |
|---|---|---|---|
| Built-in LLM judges | `Builtin.Coherence`, `Conciseness`, `Correctness`, `Faithfulness`, `Harmfulness`, `Helpfulness`, `InstructionFollowing`, `Refusal`, `ResponseRelevance`, `Stereotyping` | Trace | Model and prompt fixed by AWS, not editable |
| | `Builtin.GoalSuccessRate` | Session | With ground-truth `assertions` it checks each assertion |
| | `Builtin.ToolSelectionAccuracy`, `Builtin.ToolParameterAccuracy` | Tool call | |
| | `Builtin.SkillSelectionAccuracy`, `Builtin.SkillInstructionFollowing` | Tool call (skill loads only) | Added Aug 2026; the pricing page's "13 built-in evaluators" are the rows above |
| Trajectory matchers | `Builtin.TrajectoryExactOrderMatch`, `…InOrderMatch`, `…AnyOrderMatch` | Session | Programmatic, no LLM; need `expectedTrajectory.toolNames` |
| Insight | `Builtin.FailureAnalysis` | Session + cross-session clusters | **Batch only**; returns `insightResults` (pattern, root cause, fix) |
| Third-party managed | `ThirdParty.DeepEval.<Metric>` (e.g. `TaskCompletion`, `PIILeakage`, `ToolUse`), `ThirdParty.AutoEval.<Metric>` | Varies | AWS makes "no claims about their quality" |
| Custom LLM judge | `CreateEvaluator` with `llmAsAJudge` | `TOOL_CALL`, `TRACE` or `SESSION` | Your model (`bedrockEvaluatorModelConfig`, or `responsesEvaluatorModelConfig` for Mantle), your instructions and `ratingScale` |
| Custom multi-dimension | `ratingScale.multiDimensional` | as above | Up to 50 dimensions in one judge call; **not usable in A/B tests or recommendations** |
| Custom derived | `evaluatorConfig.derived.baseEvaluatorId` + your model | inherits | Built-in or third-party prompt on your model and account |
| Code-based | Lambda ARN, `lambdaTimeoutInSeconds` 1–300 (default 60) | any | Input ≤6 MB (spans may be truncated). Return `{"label": "PASS", "value": 1.0, "explanation": "..."}`; `label` is required |

Custom judge instructions must contain at least one placeholder for the level:

- Session level: `{context}`, `{available_tools}`.
- Trace level: `{context}`, `{assistant_turn}`.
- Tool level: `{available_tools}`, `{context}`, `{tool_turn}`, plus the skill placeholders.

Ground-truth placeholders are `{expected_response}` (trace), `{assertions}`,
`{expected_tool_trajectory}` and `{actual_tool_trajectory}` (session). An evaluator that uses
`assertions`, `expected_response` or `expected_tool_trajectory` **cannot be attached to online
evaluation**.

```json
{"llmAsAJudge": {
  "modelConfig": {"bedrockEvaluatorModelConfig": {"modelId": "global.anthropic.claude-sonnet-4-5-20250929-v1:0",
                  "inferenceConfig": {"maxTokens": 500, "temperature": 0.0}}},
  "instructions": "Did the reply resolve the refund request within policy? Context: {context}\nReply: {assistant_turn}",
  "ratingScale": {"numerical": [{"value": 1, "label": "Pass", "definition": "Resolved within policy"},
                                {"value": 0, "label": "Fail", "definition": "Unresolved or off-policy"}]}}}
```

Prefer binary pass/fail per failure mode over 1–5 scales (`agent-evals` → graders-and-judges).
Use a **numerical** scale wherever the score will feed an A/B test.

### Validate judges before trusting them

The built-in judges are generic, and you cannot tune their prompts. A `Helpfulness` score of
0.8 says nothing about your failure modes until you have checked it.

- Label 50–100 real sessions by hand.
- Measure TPR and TNR for each judge you rely on.
- Keep only the judges that agree with the human labels, and replace the rest with a custom or
  code evaluator.

The method is in `agent-evals` → `references/judge-validation.md`. Re-validate after any
evaluator or model change. AWS says it keeps improving the built-ins, so re-check their
agreement with the labels on a schedule.

## Evaluation modes

| Mode | Use for | API | CLI |
|---|---|---|---|
| On-demand | Dev loop and CI on specific sessions or traces; trying out a new evaluator | `Evaluate` (data plane). **One evaluator per call**; you pass `evaluationInput.sessionSpans` (fetched from CloudWatch yourself), optional `evaluationTarget` `{traceIds}` / `{spanIds}` and `evaluationReferenceInputs` | `agentcore eval ondemand evaluate --agent <runtime-or-harness-id> --evaluators … [--session-ids …] [--ground-truth …]` |
| Online | Continuous quality on sampled production traffic | `CreateOnlineEvaluationConfig` (control plane): `rule.samplingConfig.samplingPercentage` (0.01–100), up to 5 filters, `dataSourceConfig.cloudWatchLogs` (`logGroupNames` or 1–5 `logGroupNamePrefixes` + `serviceNames`) or an agent endpoint, ≤25 evaluators, `evaluationExecutionRoleArn`, `enableOnCreate` (required) | `agentcore add online-eval --name N --agent X --evaluators … --sampling-rate 5` then `deploy`; or `agentcore eval online-eval create/pause/resume` |
| Batch | Baselines, before/after comparisons, regression sets, failure clustering | `StartBatchEvaluation` (data plane; the quotas page calls it `CreateBatchEvaluation`), `GetBatchEvaluation`, `ListBatchEvaluations`, `StopBatchEvaluation`, `DeleteBatchEvaluation`. Up to 500 sessions/job, 10 evaluators/job, 5 active jobs | `agentcore eval batch-evaluation evaluate --agent X --start-time … --end-time … --evaluators … [--ground-truth …]` |
| Dataset (**Preview**) | Run a fixed scenario set against the agent, then score it | SDK `OnDemandEvaluationDatasetRunner` / `BatchEvaluationRunner`; control plane `CreateDataset`, `CreateDatasetVersion`, `AddDatasetExamples`… | `agentcore eval dataset …`; `agentcore eval batch-evaluation simulate --runtime-id … --dataset file.jsonl` |
| User simulation | Multi-turn tests with an LLM-played user | `pip install 'bedrock-agentcore[simulation]'`; `actor_profile` {`context`, `goal`, `traits`}, `max_turns`; actor model billed to you | via the dataset runners |

- `agentcore eval ondemand|online-eval create|batch-evaluation evaluate --agent` accept a harness
  ID (the CLI resolves its runtime); `agentcore add online-eval --agent` takes a project Runtime name.
- Online configurations lock their custom evaluators while `ENABLED`. To change one, clone it
  into a new evaluator, then swap it in.
- Config and evaluator names: use underscores, not hyphens.
- Ground truth: `expectedResponse` (per trace, keyed by `traceId`), `assertions` (session) and
  `expectedTrajectory` (session). Fields an evaluator doesn't use are reported in
  `ignoredReferenceInputFields`.
- A CI gate uses the on-demand or batch runner against a versioned dataset, with a threshold per
  evaluator (`agent-evals` → ci-and-online-evals). On-demand calls count against the 1,200
  evaluations/min built-in quota (200 in Mumbai and Singapore).

## Results

- Online results go to `/aws/bedrock-agentcore/evaluations/results/<config-id>` as OTel GenAI
  evaluation events, parented to the original span where possible.
  - Set `outputConfig.cloudWatchConfig.resultDestination=SOURCE_LOG_GROUP` to write them next
    to the traces instead.
  - Set `logGroupName` to use a custom dedicated group.
- Scores are also CloudWatch metrics in `Bedrock-AgentCore/Evaluations` (override with
  `metricsNamespace`; it must not start with `AWS/`). Alarm on these (`operations.md`).
- Batch: `GetBatchEvaluation` returns aggregates and `insightResults`; per-session detail goes
  to CloudWatch Logs.
- Logs Insights query:
  `fields attributes.gen_ai.evaluation.name, attributes.gen_ai.evaluation.score.value | filter …`.
- The console view is CloudWatch → GenAI Observability → Bedrock AgentCore → agent → **Evaluations**.

## Optimization loop

1. **Recommendations.** `StartRecommendation` with type `SYSTEM_PROMPT_RECOMMENDATION` or
   `TOOL_DESCRIPTION_RECOMMENDATION`.
   - Inputs: the current prompt or tool descriptions (inline text or a configuration bundle
     path), traces from `cloudwatchLogs.logGroupArns` (**ARNs**, unlike batch, which takes
     names), `serviceNames`, `startTime` and `endTime`, and one target evaluator.
   - Limits: 20 sessions sampled, prompts up to 20,000 characters.
   - The output is LLM-written. Read the diff, then check it with a batch evaluation on a
     held-out set before any live test. Optimizing against an unvalidated judge overfits to
     that judge.
2. **Configuration bundles** (optional). `CreateConfigurationBundle` creates versioned,
   immutable key-value config keyed by resource ARN, with branches and `parentVersionIds`.
   - Names follow `[a-zA-Z][a-zA-Z0-9_]{0,99}`, so no hyphens. Payload is at most 5 MB.
   - Read the bundle in code with `BedrockAgentCoreContext.get_config_bundle()`
     (`bedrock-agentcore` SDK 1.8 or later), for example in a Strands `BeforeModelCallEvent`
     hook.
   - The bundle reference arrives in W3C baggage (`aws.agentcore.configbundle_arn`,
     `aws.agentcore.configbundle_version`).
3. **A/B test.** The Gateway splits live traffic and online evaluation scores each arm.
4. **Promote.** Stop the test, then route the winner with a Gateway rule
   (`aws bedrock-agentcore-control create-gateway-rule` / `update-gateway-rule`). `agentcore
   deploy` does **not** manage that rule, so capture it in IaC.

### How A/B traffic splitting works

- The Gateway assigns each **runtime session ID** to control or treatment by weight (treatment
  1–99%, default 50). Assignment is sticky per session.
- The SDK's `BaggageSpanProcessor` (SDK ≥1.8) stamps every span with the experiment ARN and
  variant. Online evaluation then attributes scores to each arm.
- For each evaluator the service reports the mean, absolute and percent change, p-value,
  confidence interval and a significance flag (p < 0.05). Polling the results doesn't affect
  validity.
- Limits:
  - 2 variants per test.
  - **1 A/B test per gateway.**
  - 20 active tests per account.
  - Evaluators must return numbers (numerical LLM scales, or code evaluators that return
    `value`). Multi-dimension evaluators are rejected.

| Variant type | When | Setup |
|---|---|---|
| Config-bundle | Prompt, model ID or tool-description change only | One runtime behind a Gateway Runtime target, two bundle versions, one shared online config. `agentcore eval ab-test config-based run --gateway G --control '{"config-bundle":…,"bundle-version":…}' --treatment … --online-eval <id>` |
| Target-based | Code, framework or model-stack change | Two Gateway targets (separate runtimes or endpoints), **one online config per target**. `agentcore eval ab-test target-based run --gateway G --control '{"gateway-target":"a","online-eval":"<id>"}' --treatment …` |
| External agent | Lambda, EKS, ECS or non-AWS agent | HTTP passthrough target + gateway tracing + ADOT on the agent (`ab-testing-3p-agents`) |

- Runtime targets can't be added to an MCP-protocol gateway. Use a gateway with no protocol
  type set.
- Calls must go **through the Gateway**. Traffic that invokes the runtime directly is
  unassigned.
- Anyone who can invoke the runtime directly can also send their own `baggage` header and pick
  any bundle version. Lock runtimes behind the gateway (`policy-and-guardrails.md`).
- **Harness:** the harness overview claims A/B support, but none of the A/B, bundle or
  optimization pages describe a harness variant. There is no documented Gateway target for a
  harness endpoint, and bundle variants need agent code that calls `get_config_bundle()`.
  Treat harness A/B as **unverified**. To compare harness versions:
  - run batch evaluations on each version's sessions (with a dataset or simulation for equal
    inputs), and roll out with endpoint versions (`harness.md`); or
  - export to Strands code on Runtime, where A/B is documented.
- A small p-value shows the *judge scores* differ between arms. That counts only as far as the
  judge has been validated. Also check cost, latency and error rate per arm. A/B billing is the
  Gateway, Runtime and evaluation usage underneath it.

## Cost and limits

- Built-in evaluators cost $0.0024/1K input and $0.012/1K output tokens.
  - Input is the full session or trace context: about 15K tokens per evaluation in AWS's
    example.
  - Cost scales as sessions × evaluators.
- Custom evaluators cost $1.50 per 1,000 evaluations plus your model. Batch evaluation is 25%
  cheaper. Recommendations are free apart from the evaluations they consume.
- Sample online evaluation at 1–10%. Put expensive session-level judges on a lower sample than
  cheap code checks. Use filters to target risky intents.
- Per-evaluation caps: 20,000 spans, 200 MB, 200,000 input tokens. Built-in throughput:
  1M input tokens/min (adjustable).
- **Data residency.** Requests in ap-south-2, ap-southeast-5, ap-northeast-2 and ap-southeast-7
  run built-in evaluators on **global** cross-Region inference, which can leave your opted-in
  Regions. Use custom or derived evaluators on your own in-Region model where that matters.

## Gotchas

- Online evaluation with `logGroupNamePrefixes` misses agents still on the shared `aws/spans`
  destination. Set `UNIFIED_TRACES_DESTINATION_ENABLED=true`.
- A ground-truth custom evaluator in an online config is rejected at creation.
- `Builtin.FailureAnalysis` returns nothing outside batch. The old `Builtin.Insight.*` IDs
  belong to the retired insights API.
- The A/B CLI auto-creates `AgentCoreABTest-<name>-<hash>`, and `ab-test delete` leaves the role
  behind.
- Disabling ADOT for a third-party backend (`observability.md` §8) turns off every evaluation
  mode for that agent.

## Checklist

- [ ] A real session evaluates on-demand without span errors before any online config exists.
- [ ] The failure taxonomy came first; each evaluator maps to one failure mode.
- [ ] Every judge in use was validated against human labels (TPR/TNR recorded).
- [ ] An online config samples production; score metrics have alarms.
- [ ] A versioned dataset and batch or on-demand gate run in CI before release.
- [ ] Recommendations are checked offline before any A/B test. A/B evaluators are numeric. The
      winning route is codified in IaC.
- [ ] No dependency on Insights jobs; failure clustering runs through batch
      `Builtin.FailureAnalysis`.
