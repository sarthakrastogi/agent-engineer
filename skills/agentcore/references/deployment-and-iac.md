# Deployment and IaC on AgentCore

Release discipline (versioned release units, canaries, eval-gated rollout) is in
`agent-production`; this file maps it onto AgentCore tooling. Security controls for what you
deploy: `policy-and-guardrails.md`. Project layout and CLI install: `choosing-a-path.md`.

## Pick the IaC

| Situation | Use |
|---|---|
| New project, one team, CDK acceptable | `@aws/agentcore` CLI v1 (`agentcore deploy`, CDK underneath) |
| Existing CDK estate, or you need what the CLI schema can't express | CDK L2 `aws-cdk-lib/aws-bedrockagentcore` (stable since May 2026; Policy since v2.269.0) |
| CloudFormation-only org, StackSets, Service Catalog | `AWS::BedrockAgentCore::*` (27 types) |
| Terraform estate | `hashicorp/aws` `aws_bedrockagentcore_*` (gaps below as of 6.73.0, 2026-11-11); fill gaps with CFN or API calls |
| Policies with guardrail/temporal conditions, per-policy `LOG_ONLY` | CFN `AWS::BedrockAgentCore::Policy` (`Definition.Policy`, `EnforcementMode`) or the API |

Don't mix owners: a resource deployed by the CLI stack and then edited with Terraform or the
console drifts, and the next `agentcore deploy` reverts it. Gateway rules created with
`agentcore gateway rule` / A/B commands are live-only and not managed by deploy.

## CLI v1 deploy model

- `agentcore/agentcore.json` (schema `"version": 2`) declares resources; `aws-targets.json` is an
  array of `{name, account, region}`; one CloudFormation stack per target:
  `AgentCore-<project>-<target>`. First deploy creates target `default` from current creds.
- `agentcore deploy --target prod` (no confirmation prompt; `--yes` only confirms deleting a stack
  when the project is empty). Run it with credentials for that target's account.
- `--skip-bootstrap` when CDK bootstrap is managed elsewhere (StackSets); the CDK
  `cfn-exec-role` is what creates your IAM roles.
- No `--dry-run`/`--diff` in v1. `agentcore build` synthesises only (`agentcore/cdk/cdk.out/`);
  review the template there. Diff: `cd agentcore/cdk && npm run cdk -- diff`.
- Customise in `agentcore/cdk/lib/cdk-stack.ts` after `this.application` is created:
  `this.application.runtime('agent').grantReadWrite(table)`, `.addEnvironmentVariable(...)`.
  CDK can't add grants to an imported `executionRoleArn` role; its owner must. Resources added
  here are in the stack but not in `agentcore status`.
- **Unknown fields in `agentcore.json` are dropped silently** (e.g. `platformVersion`, issue
  #2674): set unsupported properties via `cdk-stack.ts` overrides.
- Deploy enables CloudWatch Transaction Search (`agentcore config transactionSearch false` opts out).
- Container builds: the CDK app uploads the source and **AWS CodeBuild builds and pushes to ECR
  per target**; no local Docker needed. A new runtime is CodeZip until you add a `Dockerfile`.

CI job (non-interactive):

```sh
npm install -g @aws/agentcore@1.0.2        # pin; samples still pin 0.x
agentcore build                             # synth gate; inspect cdk.out in review
(cd agentcore/cdk && npm run cdk -- diff)   # human-readable change set
agentcore deploy --target "$TARGET"         # creds for $TARGET's account via OIDC role
agentcore status --target "$TARGET"
```

## CDK and CloudFormation

- CDK: `new agentcore.Runtime(this, 'R', { runtimeName, agentRuntimeArtifact:
  agentcore.AgentRuntimeArtifact.fromEcrRepository(repo, 'v1.0.0') })`; `runtime.addEndpoint
  ('production', { version: '2' })`. Deploy role needs `iam:CreateServiceLinkedRole`.
- `PolicyEngine`/`Policy` stayed alpha at the May 2026 graduation and moved to stable
  `aws-cdk-lib` in v2.269.0 (2026-09-10, aws/aws-cdk#38687). Older versions: use the alpha package.
- CDK `Policy` emits only `Definition.Cedar` and has no `enforcementMode` (#39530): use `CfnPolicy`
  for guardrail/temporal policies. `PolicyEngine.fromPolicyEngineAttributes` breaks `Policy` (#39690).
- Several L2s don't set `node.defaultChild`, so `applyRemovalPolicy` fails (#38262, #38327).
- Each Runtime's observability delivery creates `AWS::Logs::ResourcePolicy` /
  `AWS::XRay::ResourcePolicy`; CloudWatch Logs allows 10 per account/Region. Many runtimes:
  `manageDeliveryResourcePolicy: false` + `/aws/vendedlogs/` log groups.

CloudFormation `AWS::BedrockAgentCore::` (27): `Runtime`, `RuntimeEndpoint`,
`CapacityProvider`, `Harness`, `HarnessEndpoint`, `Memory`, `BrowserCustom`, `BrowserProfile`,
`CodeInterpreterCustom`, `WorkloadIdentity`, `OAuth2CredentialProvider`,
`ApiKeyCredentialProvider`, `ConsentPortal`, `Gateway`, `GatewayTarget`, `GatewayRule`,
`GatewayRateLimit`, `PolicyEngine`, `Policy`, `ResourcePolicy`, `Evaluator`,
`OnlineEvaluationConfig`, `ConfigurationBundle`, `Dataset`, `PaymentManager`,
`PaymentConnector`, `PaymentCredentialProvider`. Plus `AWS::AgentRegistry::Registry`,
`AWS::AgentRegistry::RegistryRecord`. None for A/B tests, batch evaluations or the token-vault
CMK. `Gateway` has `WafConfiguration`; `Runtime` has `PlatformVersion` but no MMDS setting.

## Terraform

21 resources: `agent_runtime`, `agent_runtime_endpoint`, `harness`, `memory`,
`memory_strategy`, `browser`, `browser_profile`, `code_interpreter`,
`api_key_credential_provider`, `oauth2_credential_provider`, `workload_identity`,
`token_vault_cmk`, `gateway`, `gateway_target`, `gateway_rule`, `policy`, `policy_engine`,
`resource_policy`, `evaluator`, `online_evaluation_config`, `registry` (all `aws_bedrockagentcore_`).

- **Registry:** `aws_bedrockagentcore_registry` is deprecated (old namespace supported until
  2026-09-17); use `aws_agentregistry_registry` (6.64.0+). Records: no resource.
- **Missing (PRs open):** capacity provider (#49868), harness endpoint (#48791), gateway rate
  limit (#49429), gateway WAF (#48710), consent portal (#49947), configuration bundle (#48833),
  dataset (#48846), payments (#48732, #48735), harness `hook` (#50663), Runtime Instances
  (#49869), runtime `metadata_configuration` (#48874), policy `definition.policy` +
  `enforcement_mode` (#48648, #48711). Bridge with `aws_cloudformation_stack` or a `terraform_data`
  + AWS CLI step.
- **Bugs to expect:** `aws_bedrockagentcore_harness` with memory referenced by ARN fails every
  apply with "inconsistent result after apply" (`retrieval_config` 0→2; #49594, fix PR #50099);
  parallel endpoint creates on one runtime hit `ConflictException` (#50352: chain them with
  `depends_on`); runtime auto-creates its log group outside Terraform (#47629); VPC ENIs outlive
  the runtime and block security-group deletion (#47399).
- `platform_version` (`V1`/`V2`) on `agent_runtime` since 6.68.0.

## Container images

- **linux/arm64 only** (`exec format error` = wrong arch). Serve on 8080, `POST /invocations`
  (protocol details: `runtime.md`). Harness custom containers: arm64, entrypoint overridden.
- Own pipeline: `docker buildx build --platform linux/arm64 -t $REPO:$GIT_SHA --push .` on an
  arm64 CodeBuild image or with QEMU; scan in ECR; deploy by immutable tag or digest. Enable ECR
  tag immutability: AgentCore re-pulls images, so a moved tag can change what a version runs.
- The CLI always builds in CodeBuild per target; you can't promote one pre-built image across
  targets (#503, #1475). For strict build-once, deploy the Runtime with CDK/CFN/Terraform
  pointing at the image URI.
- CLI container builds create their own CodeBuild and Lambda roles; you can't supply them (#2698).
- CodeZip limits: 250 MB zipped / 750 MB unzipped; runtimes listed in `runtime.md`.

## Versions, endpoints, promotion, rollback

- Every `UpdateAgentRuntime` (image, env vars, network, protocol, authorizer, platform) creates
  an immutable version; `DEFAULT` follows the latest. Named endpoints pin a version. Quotas:
  1,000 versions and 10 endpoints per runtime (adjustable): frequent CI deploys burn versions.
- **Callers in qa/prod must pass a `qualifier`** (endpoint name), never `DEFAULT`.

| Pattern | How |
|---|---|
| Account per environment (recommended) | One `aws-targets.json` entry per account; deploy the same commit to dev → qa → prod; each env has its own runtime IDs and versions |
| One account, staged endpoints | Deploy updates the runtime; `staging` endpoint → new version; tests; repoint `prod` |
| Canary / A-B between versions | Gateway target rules or `agentcore eval ab-test` (`evaluations-and-optimization.md`) |

- Pin with the CLI: `agentcore add runtime-endpoint --runtime agent --name prod --version 3`,
  change `version` in `agentcore.json` and deploy to promote. Out of band:
  `aws bedrock-agentcore-control update-agent-runtime-endpoint --agent-runtime-id <id>
  --endpoint-name prod --agent-runtime-version 2`.
- **Rollback = repoint the endpoint to the previous version** (seconds, no rebuild). Rolling
  back the IaC instead creates yet another version. Record version ↔ git SHA in your release log.
- Endpoint names, ARNs and IDs differ per account: pass them to callers via SSM/outputs, not code.
- Harness has the same model (`harness.md`); Gateway, Memory and Policy have no versions: stage
  them with separate dev/qa/prod resources.

## VPC mode

- Runtime, Code Interpreter, Browser, harness: `networkMode: VPC` with subnets + security groups.
  ENIs come from SLR `AWSServiceRoleForBedrockAgentCoreNetwork`, are shared per subnet/SG combo
  and linger up to 8 h after deletion (stacks fail deleting the SG: retain SGs or own them in a
  separate stack).
- Subnets must be in the supported AZ IDs for the Region (`aws ec2 describe-subnets --query
  'Subnets[0].AvailabilityZoneId'`); ≥2 AZs. Public subnets give **no** internet: private
  subnets + NAT if the agent needs it (Browser does).
- Endpoints for a no-internet VPC:

| Endpoint | Why |
|---|---|
| `ecr.dkr`, `ecr.api` (interface) + `s3` (gateway) | image pull; without them sessions time out at start (harness included) |
| `logs` | CloudWatch Logs |
| `bedrock-runtime` | model calls |
| `bedrock-agentcore`, `bedrock-agentcore.gateway` | Memory, Identity, tools, Gateway calls from your code |
| `sts`, `secretsmanager`, `kms`, `xray` | if your code or ADOT uses them (not listed by AgentCore docs) |

- Scope the S3 gateway endpoint policy: `prod-<region>-starport-layer-bucket/*` (ECR layers),
  `acr-code-*-<region>-an` with `aws:PrincipalServiceName = bedrock-agentcore.amazonaws.com`
  (CodeZip), `acr-storage-*-<region>-an` (session storage).
- Harness VPC mode also needs the private-ECR pull permissions the CLI does not add.
- Enforce with SCP keys `bedrock-agentcore:subnets` / `securityGroups` on `CreateAgentRuntime`,
  `UpdateAgentRuntime`, `CreateCodeInterpreter`, `CreateBrowser`, gateway targets.
- Gateway/Identity/Runtime **egress to private resources** goes through VPC Lattice targets
  (`gateway.md`); the CLI can't declare gateway-target private endpoints yet (#2773).

## PrivateLink to the AgentCore APIs

- Interface endpoints: `com.amazonaws.<region>.bedrock-agentcore` (data plane: Runtime, Memory,
  tools, Identity, Web Search), `.bedrock-agentcore-control`, `.bedrock-agentcore.gateway`
  (private DNS `*.gateway.bedrock-agentcore.<region>.amazonaws.com`); Registry has its own pair.
  All primitives, including Policy and Identity control planes, are supported.
- Endpoint policies filter IAM principals only; OAuth callers need `"Principal": "*"`. Restrict
  with `aws:SourceVpce` in resource policies.

## MMDSv2

- Since 2026-06-30 invocations fail (`ValidationException: This runtime is not MMDSv2-enabled`)
  unless `metadataConfiguration.requireMMDSV2 = true`. It is settable only through
  `UpdateAgentRuntime`; neither CFN nor Terraform exposes it. Per the Terraform PR (#48874,
  unverified in AWS docs) runtimes created after 2026-02-15 have it on by default and reject `false`.
- Fix old runtimes: `update_agent_runtime(agentRuntimeId=..., metadataConfiguration={'requireMMDSV2': True})`
  (creates a new version); add a CI check with `GetAgentRuntime`.

## Tagging

- Taggable (≤50 tags): runtime, runtime endpoint, capacity provider, code interpreter, browser,
  browser profile, gateway, workload identity, credential providers, token vault, memory,
  evaluator, online eval, policy engine, config bundle, batch eval, recommendation, A/B test.
  Harness isn't in that list but `CreateHarness` accepts tags and propagates them. Policies and
  gateway targets take no tags.
- CLI: `--tags k=v` on `add runtime`, `add gateway`, `add policy-engine`. Use `aws:ResourceTag/*`
  on invoke actions for ABAC and activate cost-allocation tags (`operations.md`).

## CLI issues that bite (aws/agentcore-cli)

| Issue | Effect | Workaround |
|---|---|---|
| #2431 open | `deploy` ignores `HTTP(S)_PROXY` in some SDK clients | run from a host with direct AWS egress or VPC endpoints |
| #2246 open (#2292) | no permissions boundary on generated roles; fails with `NoStack` | pass pre-made roles (`--role-arn`); a CDK `PermissionsBoundary.of(...)` in `cdk-stack.ts` may work (unverified) |
| #2569 open | multi-target project: synth fails on gateway credentials for other targets | keep one target per project copy/branch |
| #2772 open | `migrate` of a deployed v0 project with a Gateway fails (Stack Refactor) | redeploy fresh in v1 |
| #542 open | no resource-based policies in the CLI | `CfnResourcePolicy` in `cdk-stack.ts` or `PutResourcePolicy` |
| #2535 open | `deploy` always deploys every runtime in the project | split projects by release cadence |

## Gotchas

- Runtime name, harness name (≤40), policy-engine name (≤48, no hyphens) include project + target
  when the CLI deploys them; long names fail at deploy.
- `.env.local` holds project secrets: never commit; rotate by editing it and redeploying.
- `ROLLBACK_FAILED` can hang the deploy TUI (#1610): check the stack in CloudFormation.
- Runtime control-plane mutations share 50 TPS per account: serialise large multi-runtime deploys.
- The docs' versioning example passes `agentRuntimeVersion='v2.1'` on the data-plane client;
  versions are integers as strings and the call is on `bedrock-agentcore-control`.

## Checklist

- [ ] One account per environment; `aws-targets.json` committed; CLI version pinned in CI.
- [ ] `cdk diff` reviewed before prod; synthesised IAM checked (CLI role allows all models).
- [ ] arm64 images with immutable tags; image digest and runtime version logged per release.
- [ ] qa/prod callers use a named endpoint; rollback runbook = repoint endpoint.
- [ ] VPC endpoints + S3 endpoint policy in place; SGs outside the runtime's delete path.
- [ ] MMDSv2 verified on every runtime; Transaction Search on.
- [ ] Terraform gaps bridged explicitly (CFN stack / CLI step), not by console clicks.
