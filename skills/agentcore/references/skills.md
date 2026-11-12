# Skills on AgentCore

A skill is a directory with a `SKILL.md` (AgentSkills.io format) and optional `scripts/`,
`references/`, `assets/`. Tools give capabilities; skills say when and how to use them.
This file covers packaging, attaching, loading and trusting skills on AgentCore. For writing
the instructions themselves, use `agent-prompting`. Harness config generally: `harness.md`.

## Format

```markdown
---
name: refund-policy
description: Use when answering questions about refund eligibility.
---

# Refund Policy

Read policy/eligibility.md before answering.
Ask for the purchase date if it is missing.
```

- `name`: 1–64 chars, lowercase letters, digits, single hyphens; must equal the directory name.
- `description`: 1–1024 chars, says what and **when**; it is the only text the model sees
  before loading, so it decides selection.
- Optional: `license`, `compatibility` (≤500 chars), `metadata` (string map), `allowed-tools`
  (experimental). Strands treats `allowed-tools` as informational: it does not restrict tools.
  Assume the harness doesn't either (unverified); restrict with `allowedTools` (`harness.md`).
- Reference supporting files by relative path from `SKILL.md`; they load only when read.

## How the harness loads skills

- **Progressive disclosure.** Metadata (~100 tokens per skill) goes into the system prompt;
  full instructions load through a tool call when the model picks the skill. Resource files
  are read with the file tools; scripts run only if the agent runs them with `shell`.
- Adding a skill grants no AWS permissions and runs nothing by itself.
- **Fetched once per session**, on the first invocation, copied into the microVM (`/tmp` for
  Git / S3 / AWS sources) and reused for later turns. New session = fresh fetch. Test a skill
  change in a new `runtimeSessionId`.
- Every fetch failure fails the invocation; skills are never silently skipped.
- Custom container with Git / S3 / AWS skills needs `tar` and a writable `/tmp`, else
  `Unable to copy configured skills to the customer container`. `amazonlinux:2023` lacks `tar`.

## Skill sources

| Source | API shape (`harness.yaml`) | `--skills` JSON (CLI flag) | Needs |
|---|---|---|---|
| AWS catalog | `awsSkills: {paths: [glob]}`; `{}` = all | same | Nothing; bundled in the managed runtime |
| Git (HTTPS) | `git: {url, path, auth: {credentialArn \| credentialName (CLI), username}}` | `{"gitUrl", "path", "auth": {"credentialName"}}` | Egress (NAT in VPC); clone ≤ 60 s; PAT in an Identity API-key provider |
| S3 | `s3: {uri: s3://bucket/prefix/}` | `{"s3Uri": ...}` | Role `s3:GetObject` + `s3:ListBucket` (+ KMS); ≤ 1 GB; works via S3 VPC endpoint |
| Path | `path: /opt/skills/x` (inside the microVM) | `{"path": ...}` | Baked into the image or installed with `harness exec` before the first invoke |

```yaml
skills:
  - awsSkills:
      paths: [core-skills/aws-cdk, specialized-skills/operations-skills/*]
  - s3:
      uri: s3://acme-agent-skills/refund-policy/1.4.0/
  - git:
      url: <HTTPS URL of the repo, e.g. github.com/acme/agent-skills.git>
      path: skills/support
      auth:
        credentialName: github-token     # CLI project credential; see gotcha below
        username: oauth2
```

- The YAML uses API names (`git.url`, `s3.uri`); the `--skills` flag uses `gitUrl` / `s3Uri`.
  Don't copy flag JSON into YAML or SDK calls.
- Private Git in a CLI project: use `auth.credentialName` (from
  `agentcore add credentials api-key`); the CDK loader does not resolve `auth.credentialArn`.
  Raw API / Terraform callers use `credentialArn`.
- AWS catalog (`github.com/aws/agent-toolkit-for-aws/tree/main/skills`): `core-skills/*`
  (e.g. `aws-cdk`, `aws-iam`, `amazon-bedrock`), `specialized-skills/{analytics,operations,storage}-skills/*`.
  Paths are relative, no `..`; a glob that matches nothing fails the invocation. Each
  `awsSkills` entry adds metadata for every matched skill: select narrowly.
- Path skills: `path` is a directory in the running environment, not your laptop; listing it
  in YAML uploads nothing. Session install: `agentcore harness exec --id <id> --session-id $S
  --command "..."`, then invoke with the same `$S`.
- The `agentcore add harness` wizard does not configure skills; pass `--skills` or edit YAML.

## Attaching, overriding, versioning

- Set defaults with `CreateHarness` / `UpdateHarness` (`skills` replaces the whole list) or
  `harness.yaml` + `agentcore deploy`. Each change creates a new harness version.
- Per-call: `invoke_harness(skills=[...])` (SDK only; v1 `agentcore invoke` has no skills flag).
  Invoke-time skills are **appended** after the harness's skills; on a name clash the invoke-time
  skill wins (harness-skills guide; the API reference's generic "overrides" wording is less
  specific). A caller who can set `skills` can load arbitrary instructions and scripts: strip it.
- **A pinned harness version does not pin skill content.** Versions store the source
  reference, and content is fetched at session start. The Git source has no branch, tag or
  commit field and `url` forbids `#` and `@`, so a Git skill loads whatever the repo's default
  branch holds then (inferred; no ref option is documented). S3 has no version-id field. To pin:
  1. S3 with an immutable, versioned prefix per release (`.../refund-policy/1.4.0/`), writes
     restricted to CI. Promote by changing the URI, which makes a new harness version, so
     endpoints and rollback cover skill content too. Recommended.
  2. Bake into a custom image (path skill) in an ECR repo with immutable tags.
  3. Git only for repos you control with protected default branches; treat every merge as a
     production deploy of every harness that uses it.
- AWS catalog skills are AWS-managed content that changes with the runtime; you can't pin
  them (unverified whether AWS versions them). Use them for exploration; copy what you rely
  on into your own S3 prefix after review.
- Export to code: S3 and Git skills carry over; AWS skills are omitted; path skills block the
  export (`choosing-a-path.md`).

## Skills in code agents on Runtime

Strands has native support (the harness uses it):

```python
from strands import Agent, AgentSkills
from strands.vended_tools import file_editor, shell

skills = AgentSkills(skills="./skills/", strict=True)   # parent dir: every child with SKILL.md
agent = Agent(plugins=[skills], tools=[file_editor, shell])
```

- `AgentSkills` injects an `<available_skills>` XML block and a `skills` tool; it does not
  read resources or run scripts, so give it file / shell tools (sandbox them: `agent-guardrails`).
  `strict=True` fails on validation issues instead of warning. Sources: directories, parent
  directories, HTTPS URLs or `Skill(...)` objects; HTTPS sources have the same supply-chain
  risk as harness Git skills.
- Strands Harness `create_harness()` scans `./.agent/skills` by default; `skills=None` turns
  it off. TypeScript: `AgentSkills` from `@strands-agents/sdk/vended-plugins/skills`.
- Ship skills inside the CodeZip / image, or mount them (S3 Files / EFS, `runtime.md`).
  Don't fetch from the internet at start-up.
- Other frameworks: no AgentCore helper. Read `SKILL.md` frontmatter yourself, put name +
  description in the prompt, expose a load-skill tool that returns the body.
- Bedrock Managed Agents (Preview) reads skills only from filesystem `capability_directories`.

## Evaluating skills

- `Builtin.SkillSelectionAccuracy` (right skill for the task?) and
  `Builtin.SkillInstructionFollowing` (steps followed?) run per skill invocation in on-demand,
  batch and online evals (`evaluations-and-optimization.md`).
- Detection: a file read of a path ending `/SKILL.md` with valid frontmatter, or the native
  skill tool (Strands, LangGraph Deep Agents, Google ADK, Claude Agent SDK). No results when
  the agent loaded skills means the trace lacks the signal: run the eval diagnostic skill.
- After editing a skill's `description`, re-run selection evals: it changes routing for all
  other skills too. Method: `agent-evals`.
- Catalogue shared skills in AWS Agent Registry (record type `SKILL`) with approval
  workflow (`multi-agent-and-registry.md`).

## Trust: skills are supply chain

The harness treats skill content as trusted and does not inspect or sanitise it. A skill is
prompt plus code running with the execution role and every allowed tool.

- Review `SKILL.md` and every script like code; require PR review on the source repo.
- S3: dedicated bucket, Block Public Access, bucket policy allowing writes only from the CI
  role, SSE-KMS, versioning on, read-only `s3:GetObject` / `s3:ListBucket` for the harness role
  scoped to the skills prefix.
- Git: read-only, repo-scoped token in an Identity API-key provider, never in env vars or URLs.
  Third-party repos (including public skill collections): mirror a reviewed commit into your
  own S3 prefix; never point production at someone else's default branch.
- Never forward caller-supplied `skills`; if you must, allowlist exact S3 prefixes / repos.
- A skill that tells the agent to fetch URLs or run downloaded code turns it into an injection
  path; combine with the lethal-trifecta check in `agent-guardrails`.
- Keep `allowedTools` narrow: a malicious or wrong skill can only use tools the harness allows.

## Gotchas

- Skill changes don't reach live sessions; long `maxLifetime` (up to 8 h) keeps stale skills.
- Git clone timeout (60 s) or missing NAT in VPC mode fails every first invocation of a session.
- Many skills = many descriptions in every model call; prune skills the agent never selects.
- `name` not matching the directory: Strands warns by default; validate in CI.

## Checklist

- [ ] Every skill passes frontmatter validation; `name` = directory name; description says when.
- [ ] Production sources are private S3 versioned prefixes (or image digests), not floating Git.
- [ ] Harness role has read-only access to exactly the skill prefixes it needs.
- [ ] Caller-supplied `skills` stripped or allowlisted.
- [ ] Skill edits tested in a new session and covered by skill evals before promotion.
