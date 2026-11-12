# Policy and guardrails on AgentCore

Threat model and approvals: `agent-guardrails`. This file maps them onto AgentCore controls.

## Which control does what

| Need | AgentCore control |
|---|---|
| Deterministic "who may call which tool with which arguments" | Policy engine attached to a Gateway (Cedar) |
| "Only after approval", "max N per session", running totals | Temporal policy (Dogwood `when temporal`) |
| Block prompt injection / PII / harmful text on a tool call or response | Guardrail condition in a policy (`when guardrails`, `suppressOutput`) |
| Filter what the model itself reads and writes | Bedrock Guardrails on the model call (harness `guardrailConfig`, Strands `guardrail_id`, `ApplyGuardrail`) |
| Who may invoke a runtime, gateway, memory | IAM + resource-based policy (`PutResourcePolicy`) |
| Org-wide config rules (IdP, VPC, KMS, target types, policy mode) | SCP with `bedrock-agentcore:*` condition keys |
| Stop the agent reaching the internet / exfil hosts | VPC mode + security groups + endpoints (`deployment-and-iac.md`) |

**Policy only sees Gateway traffic.** Not covered: tools coded inside a Runtime agent, harness
built-ins (`shell`, `read`/`write`/`edit`, browser, code interpreter), remote MCP servers a
harness reaches by URL, direct Bedrock/Memory/SDK calls, `InvokeAgentRuntimeCommand`. Route
anything that needs policy through a Gateway target (MCP, Lambda, OpenAPI, Runtime, inference,
HTTP proxy, Memory connector) and lock the bypass paths with IAM (below).

## Policy model

- **Engine** (`CreatePolicyEngine`) holds policies; attach it with `policyEngineConfiguration
  {arn, mode}` on `CreateGateway`/`UpdateGateway`. One engine can serve several gateways.
  Name: `[A-Za-z][A-Za-z0-9_]*`, ≤48 chars, **no hyphens**; CLI deploys `<project>_<target>_<name>`.
- `policyEngineId` (name + 10-char suffix) for policy APIs; `policyEngineArn` for the gateway.
- Semantics: **default deny, forbid wins, at least one permit required.** An attached engine
  with zero policies denies every tool call.
- Gateway execution role needs `bedrock-agentcore:AuthorizeAction`,
  `PartiallyAuthorizeActions` (filters `tools/list` to what the caller may call) and
  `GetPolicyEngine`. Missing → attach fails with `InternalServerException` and all calls deny.
  CLI-created gateway roles include them; a `--role-arn` role does not.

Request shape the engine sees:

| Element | Value |
|---|---|
| principal | `AgentCore::OAuthUser::"<jwt sub>"` (JWT claims as tags) or `AgentCore::IamEntity` (`principal.id` = `arn:aws:sts::<acct>:assumed-role/<role>`, no tags) |
| action | MCP `<Target>___<tool>`; Runtime `<Target>___POST:/invocations`; HTTP/inference `<Target>___<METHOD>:<uri>` |
| resource | `AgentCore::Gateway::"<gateway-arn>"` |
| context | `context.input.*` (tool args); `context.output.*` only under `suppressOutput`; `context.system.now` (UTC; on the time-based examples page, though the schema-constraints page still says only `context.input`) |

The schema is generated from the gateway's tool definitions: JSON `integer`→`Long`,
`number`→`Decimal` (≤4 places, truncated), `array`→`Set` (order lost). No regex (`like` with
`*` only), no wildcard actions (group tools by Gateway target: `action in
AgentCore::Action::"ReadToolsTarget"`), no custom entity types. Naming a specific action
requires `resource ==` a specific gateway ARN. Schema ≤400 KB per engine: many large tools may
force splitting engines.

```cedar
permit (
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"RefundTool___process_refund",
  resource == AgentCore::Gateway::"arn:aws:bedrock-agentcore:us-west-2:111122223333:gateway/refund-gw-a1b2c3d4e5"
)
when {
  principal.hasTag("scope") && principal.getTag("scope") like "*refund:write*" &&
  context.input.amount < 500
};
```

Per-user rules need JWT inbound (`identity.md`): an `awsIam` caller arrives as its execution role.

## Authoring and review

- **Write Dogwood** (Cedar superset; every Cedar policy is valid Dogwood). Plain Cedar covers
  request-only rules; `when temporal {…}` and `when guardrails {…}` need Dogwood.
- **Natural language:** `agentcore gateway policy generate --gateway-id <id> --prompt "..."`
  or `StartPolicyGeneration` → `GetPolicyGeneration` → `ListPolicyGenerationAssets`. Needs a
  deployed gateway + engine; $0.13 per 1K input tokens; inference may leave the Region but
  stays in the geography (US/EU/APAC). Authorization: $0.000025/request; first 100 temporal
  policies per engine add no authorization charge (pricing page, 2026-11-12).
- **Review every generated statement by hand.** `VALID` means schema-correct, not intended: the
  docs' own refund example ("customer service agents … within the last 30 days") came back
  `VALID` with no principal or date check. Reject `ALLOW_ALL`, `DENY_ALL`, `ALLOW_NONE`,
  `DENY_NONE`, `NOT_TRANSLATABLE`, `INVALID`. Commit the reviewed Cedar, not the prompt.
- `validationMode`: `FAIL_ON_ANY_FINDINGS` (default) runs automated reasoning over the **whole
  engine**; `IGNORE_ALL_FINDINGS` runs schema checks only. Guardrail-only `forbid`s look
  unconditional to the analyser and are refused as Overly Restrictive unless a companion
  `permit` exists or you use `IGNORE_ALL_FINDINGS`.
- `CreatePolicy` is async (202 `CREATING` → maybe `CREATE_FAILED` + `statusReasons`): use the
  `policy_active` waiter. A bad policy fails the whole `agentcore deploy`.
- Creating policies needs `bedrock-agentcore:InvokeGateway` on the gateway (validation calls
  it). `ManageResourceScopedPolicy` gates gateway-specific policies; `ManageAdminPolicy` gates
  wildcard (`gateway/*`) ones: withhold `ManageAdminPolicy` from app teams.

## Guardrails inside policies (What's New 2026-06-17)

```cedar
forbid (principal,
  action == AgentCore::Action::"Agent___POST:/invocations",
  resource == AgentCore::Gateway::"<gateway-arn>")
when guardrails {
  BedrockGuardrails::PromptAttack(["PROMPT_INJECTION"], [context.input.prompt])
    ["PROMPT_INJECTION"].confidenceScore.greaterThan(decimal("0.6"))
};
suppressOutput (principal, action == AgentCore::Action::"Crm___get_customer",
  resource == AgentCore::Gateway::"<gateway-arn>")
when guardrails {
  BedrockGuardrails::SensitiveInformation(["US_SOCIAL_SECURITY_NUMBER"], [context.output.text]).count() > 0
};
```

- Types: `ContentFilter` (`VIOLENCE` `HATE` `SEXUAL` `MISCONDUCT` `INSULTS`), `PromptAttack`
  (`JAILBREAK` `PROMPT_INJECTION` `PROMPT_LEAKAGE`), `SensitiveInformation` (fixed entity list;
  no `ACCOUNT_NUMBER`: use `US_BANK_ACCOUNT_NUMBER` / `INTERNATIONAL_BANK_ACCOUNT_NUMBER`).
- Scores are discrete {0, 0.2, …, 1.0} and non-deterministic near the threshold. Defaults if NL authoring picks: 0.2 content, 0.4
  prompt attack, 0.2 PII. `SensitiveInformation` needs `count()`/`categories()`/`max…`/`min…`.
- `permit`/`forbid` read only `context.input.*`; `suppressOutput` reads only `context.output.*`
  and only guardrail conditions. `suppressOutput` hides the response; **the tool still ran**, so
  never rely on it to stop a side effect.
- Prefer a `permit … when guardrails { … lessThanOrEqual(…) }`: a lone `forbid` grants nothing.
- `$`-JSONPath data paths are **not validated**: if none resolves, requests fail closed
  (`request missing field(s) required for guardrail evaluation`); if one of several fails to
  resolve, that check is **silently skipped**. Test each path with content it must catch.
- Gateway role needs `bedrock:InvokeGuardrailChecks` (FAS). Regions (7): us-east-1, us-east-2,
  us-west-2, ap-southeast-2, ap-northeast-1, eu-west-2, eu-north-1.

## Temporal policies

```cedar
permit (principal, action == AgentCore::Action::"Pay___disburse",
  resource == AgentCore::Gateway::"<gateway-arn>")
when temporal {
  formerly within 1h AgentCore::Action::"Pay___approve_claim"::response {
    eventResource: resource, input.claimId: context.input.claimId }
};
```

- Operators: `formerly within`, `since within`, `count`, `sum`. Quotas: 20 temporal policies
  per engine, 3 operators per policy, 24 h max window. Not in ap-south-2, ap-southeast-5,
  ap-southeast-7, eu-south-1, us-west-1.
- The caller supplies `x-amzn-bedrock-agentcore-policy-session-id` (`[A-Za-z0-9-]{1,128}`); with
  any temporal policy on the engine, requests without it fail. **A new session ID resets
  counts**: temporal rate limits are per session, not per user; use Gateway rate limits for
  account-wide caps (`gateway.md`).
- Sessions are bound to the authenticated principal; on `authorizerType NONE` gateways all
  callers sharing an ID share history.
- Adding/updating a temporal policy invalidates open sessions (next call 409
  `ConflictException`); deploy policy changes off-peak and handle 409 by starting a session.
- IAM-inbound gateways: role needs `bedrock-agentcore:GetWorkloadAccessToken` on its workload
  identity. Same account and Region only; the WAT propagates Gateway → Runtime → Gateway.
- Denied prior actions are recorded as `error`, so a `::response` condition never matches them.

## Enforcement modes and testing

| Engine mode (gateway) | Policy `enforcementMode` | Effect |
|---|---|---|
| `ENFORCE` | `ACTIVE` | enforced |
| `ENFORCE` | `LOG_ONLY` | logged only; other policies enforce |
| `LOG_ONLY` | any | nothing enforced, every call succeeds |

- Roll out: attach new engine `LOG_ONLY` (`--attach-mode log-only`; **CLI default is enforce**,
  which denies everything until permits exist) → add policies → watch
  `LogOnlyDecisionFlips` (sustained zero = safe), `LogOnlyMatches`, `ConfidenceScore` in
  `AWS/Bedrock-AgentCore` → `ENFORCE`. New guardrail on a live engine: add as `LOG_ONLY`,
  calibrate on labelled traffic (`agent-evals`), then `UpdatePolicy` to `ACTIVE`. CI: replay
  allow/deny tool calls against a non-prod gateway; assert the 403s.

## Locking down the bypasses

`UpdateGateway` can set `mode` to `LOG_ONLY` or `policyEngineConfiguration` to null; `UpdatePolicy`
can demote a policy to `LOG_ONLY`. Restrict these and the deletes to the deploy role:

```json
{"Version": "2012-10-17", "Statement": [
{"Sid": "OnlyDeployerChangesPolicy", "Effect": "Deny",
 "Action": ["bedrock-agentcore:UpdateGateway", "bedrock-agentcore:DeleteGateway",
   "bedrock-agentcore:UpdatePolicy", "bedrock-agentcore:DeletePolicy",
   "bedrock-agentcore:UpdatePolicyEngine", "bedrock-agentcore:DeletePolicyEngine"],
 "Resource": "*",
 "Condition": {"ArnNotLike": {"aws:PrincipalArn": "arn:aws:iam::*:role/agentcore-deployer"}}},
{"Sid": "GatewaysMustEnforce", "Effect": "Deny",
 "Action": ["bedrock-agentcore:CreateGateway", "bedrock-agentcore:UpdateGateway"],
 "Resource": "*",
 "Condition": {"StringNotEquals": {"bedrock-agentcore:PolicyEngineMode": "ENFORCE"}}}]}
```

The second statement (also denies a missing engine, since an absent key fails
`StringNotEquals`) relies on `bedrock-agentcore:PolicyEngineMode`. Docs disagree (checked
2026-11-12): the Gateway condition-key page lists it on Create/UpdateGateway; the Service
Authorization Reference defines the key but its CreateGateway/UpdateGateway rows list only tag
keys; the enforcement-modes page says "No separate action or condition key protects the `mode`
field beyond `bedrock-agentcore:UpdateGateway`". Treat it as unverified: test in a sandbox, keep
the first statement anyway, and alarm on CloudTrail `UpdateGateway`/`UpdatePolicy` events.

Other bypass paths:

- **Direct runtime calls skip the gateway.** SigV4 runtime: resource policy allowing only the
  gateway role plus `Deny` for every other `aws:PrincipalArn`; cover `InvokeAgentRuntime`,
  `InvokeAgentRuntimeForUser`, `InvokeAgentRuntimeWithWebSocketStream*`,
  `InvokeAgentRuntimeCommand`, `InvokeAgentRuntimeCommandShell`, not just the first. JWT runtime:
  `allowedWorkloadConfiguration` on the authorizer. Lock the gateway role's trust policy with
  `aws:SourceArn` = gateway ARN, or anyone who can assume it is "the gateway".
- Deny `InvokeAgentRuntimeCommand*` to non-operators (`allowedTools` doesn't cover the shell),
  and `InvokeAgentRuntimeForUser` / `GetWorkloadAccessTokenForUserId` where a JWT is available.
- Validate Runtime payloads: `prompt` must be a string; strip `toolUse` blocks (Runtime, unlike
  the harness, doesn't reject a final `toolUse`).

## Harness per-call overrides

Any caller who passes harness auth can override `model` (`apiBase`, `additionalParams`),
`systemPrompt`, `tools`/MCP URLs, `skills` and `allowedTools` per call (`harness.md`); no IAM
condition key covers these fields. Never expose `InvokeHarness` to untrusted callers: front it
with a service that builds the request from an allowlist. IAM backstops: execution role allows
`bedrock:InvokeModel*` only on pinned model/profile ARNs and only when `bedrock:GuardrailIdentifier`
equals your `guardrail-arn:version` (a caller-supplied `model` drops `guardrailConfig`); no
`sts:AssumeRole`; skill buckets readable only by that role; SG egress limited to expected endpoints.

## Bedrock Guardrails on the model call

- Harness: `bedrockModelConfig.additionalParams.guardrailConfig {guardrailIdentifier,
  guardrailVersion, trace}`, `converse_stream` only, role needs `bedrock:ApplyGuardrail`; stop
  reason `guardrail_intervened`. Guardrail in the model's Region.
- Strands: `BedrockModel(guardrail_id=…, guardrail_version=…, guardrail_trace=…)`. Others: call
  `ApplyGuardrail` on input and on tool results before they reach the model.
- Gateway inference targets: Bedrock guardrail params pass through only on passthrough routes;
  translated routes reject them (400). Use Policy guardrails there; they work across providers.

## IAM, SCP and network baseline

- Execution roles: trust `bedrock-agentcore.amazonaws.com` with `aws:SourceAccount`/`aws:SourceArn`;
  the CLI-created runtime role allows **all** foundation models: replace it in prod (`--role-arn`).
  MMDS exposes its credentials to any code in the microVM.
- SCP keys: `bedrock-agentcore:DiscoveryUrl`, `GatewayAuthorizerType` (deny `NONE`), `KmsKeyArn`,
  `PolicyEngineArn`, `PolicyEngineMode` (see above), `McpTargetConfigurationType`, `CredentialProviderType`,
  `PrivateEndpointType`, `RuntimeAuthorizerType`, `subnets`/`securityGroups` (runtime, tools, targets).
- Resource policies (Runtime + endpoint, Gateway, Memory): exact-ARN `Resource`; cross-account
  needs an allow on **both** runtime and endpoint; OAuth resources need `"Principal": "*"`
  narrowed with `aws:SourceVpce`/`aws:SourceVpc`.

## Gotchas

- Docs show `agentcore add policy --source`/`--form-*`; CLI v1.0.2 (also current `main`) has
  `--engine --name --statement` (inline, `file://`, `-`), `--validation-mode`, `--enforcement-mode`,
  `--authorization-phase initiate|return-output`. Trust `--help`.
- Terraform/CDK `Policy` emit only `definition.cedar`, no `enforcementMode` (`deployment-and-iac.md`).

## Checklist

- [ ] Every tool needing control goes through a Gateway; direct runtime and shell paths denied.
- [ ] Engine rolled out `LOG_ONLY` → zero `LogOnlyDecisionFlips` → `ENFORCE`.
- [ ] Generated Cedar reviewed and committed; `FAIL_ON_ANY_FINDINGS` unless justified.
- [ ] `UpdateGateway`/`UpdatePolicy`/`Delete*` limited to the deployer; CloudTrail alarm on them.
