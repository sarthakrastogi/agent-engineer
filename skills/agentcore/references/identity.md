# AgentCore Identity: inbound auth, outbound credentials, OBO

Identity answers two questions: who may call this Runtime, Gateway or harness (inbound), and
which credentials the agent uses to call tools (outbound). Trust and approval design lives
in `agent-guardrails`. Gateway target wiring lives in `gateway.md`. Cedar authorisation lives
in `policy-and-guardrails.md`.

## Pick the inbound auth

| Caller | Use |
|---|---|
| AWS workloads (Lambda, ECS, Step Functions) and no per-user downstream credentials | IAM SigV4 (the default) |
| End users, or tools that need the user's own token (3LO cache, OBO) | `customJWTAuthorizer` (`CUSTOM_JWT`) |
| Both kinds of caller on one Runtime | Two versions or endpoints, one per type. A Runtime accepts one type, never both |
| A Gateway in front of a Runtime | Lock the Runtime to the gateway (see below) |

- **SigV4 callers carry no end-user identity.** A harness or Runtime invoked with SigV4 does
  not propagate a user into tool calls. User-scoped token vault entries and OBO work only with
  a Bearer JWT. The `X-Amzn-Bedrock-AgentCore-Runtime-User-Id` header is the one exception,
  and it is unverified (see Spoofing).
- Plan the authorizer type at create. Practitioners report it can't be changed in place
  (unverified: `UpdateGateway` still takes `authorizerType`); the config inside it can change.
- Lock the Runtime behind a gateway. IAM Runtime: resource policy allowing the gateway role and
  denying others (`ArnNotEquals aws:PrincipalArn`); pin the role's trust with `aws:SourceArn` and
  `aws:SourceAccount`. JWT Runtime: `allowedWorkloadConfiguration.hostingEnvironments[].arn`.

## The JWT authorizer (same shape on Runtime, Gateway and harness)

```json
{"customJWTAuthorizer": {
  "discoveryUrl": "<https issuer: login.microsoftonline.com/<tenant>/v2.0>/.well-known/openid-configuration",
  "allowedAudience": ["<app-id>", "api://<app-id>"],
  "customClaims": [{"inboundTokenClaimName": "roles", "inboundTokenClaimValueType": "STRING_ARRAY",
    "authorizingClaimMatchValue": {"claimMatchOperator": "CONTAINS",
      "claimMatchValue": {"matchValueString": "Agent.Invoke"}}}]
}}
```

| Field | Checked against | Notes |
|---|---|---|
| `discoveryUrl` | `iss` (issuer in the metadata must equal the token `iss`) | Must end `/.well-known/openid-configuration` or `/.well-known/oauth-authorization-server` |
| `allowedAudience` | `aud` (any match) | |
| `allowedClients` | the literal **`client_id`** claim | Not `azp`, not `appid` |
| `allowedScopes` | the **`scope`** claim | If set, at least one must match. Not `scp` |
| `customClaims` | any named claim | `STRING` uses `EQUALS`; `STRING_ARRAY` uses `CONTAINS` / `CONTAINS_ANY` |
| `allowedWorkloadConfiguration` | identity chain | Runtime only. Admits only the listed Gateway ARNs or workload identity names |
| `privateEndpoint` (+ `privateEndpointOverrides`, max 5) | | For an IdP inside a VPC, reached through VPC Lattice. Needs the SLR `AWSServiceRoleForBedrockAgentCoreIdentity` |

Set at least one of audience, clients, scopes or custom claims. Every field you set is
enforced. `advertisedScopeMapping` (Gateway) changes only the scope advertised in PRM and
`WWW-Authenticate`; it does not change validation.

### IdP specifics

| IdP | Do | Trap |
|---|---|---|
| **Entra ID** | Use `allowedAudience` with the app ID. List both `<app-id>` and `api://<app-id>` | Entra tokens have no `client_id` (v2 uses `azp`, v1 uses `appid`), so `allowedClients` rejects them. Delegated scopes arrive in `scp`, not `scope`, so `allowedScopes` never matches. Leave both empty |
| Entra v1 vs v2 | Read the `ver` claim. v1 uses `.../<tenant>/.well-known/...` and v2 uses `.../<tenant>/v2.0/.well-known/...` | A mismatch fails the `iss` check. For v2 access tokens, set `accessTokenAcceptedVersion=2` in the manifest and expose an API scope. For v1, request `<app-id>/.default` |
| Entra, restricting the calling app | Add a `customClaims` rule: `azp` `EQUALS` the client app ID (v2), or `roles` `CONTAINS` an app role | Docs: Entra is supported only for tokens "that do not have any custom claims" |
| **Cognito** | Access tokens carry `client_id` and no `aud`, so use `allowedClients`. For M2M, also set `allowedScopes` to the resource-server scopes | The default token lifetime is 60 min |
| **Okta** | Use `allowedAudience` (the auth-server audience) | Okta omits `client_id` unless you add a custom claim `client_id = app.clientId` |

- Gateway JWT challenges: 401 is no token or a bad token; 403 `insufficient_scope` is a valid
  token that fails the scope check. Both responses carry `resource_metadata`, which points at
  `/.well-known/oauth-protected-resource`.
- Gateway behind CloudFront or a custom domain: set `domainOverride`. The proxy must overwrite
  `x-amzn-bedrock-agentcore-gateway-domain` itself.
- `AUTHENTICATE_ONLY` is SigV4 authentication with no authorisation. A JWT-only caller gets
  `IncompleteSignatureException`. `NONE` means the gateway does no auth at all. Both are safe
  only with a policy engine or an interceptor (`gateway.md`).
- Reading claims inside the agent: add `Authorization` to the request header allowlist
  (`--request-header-allowlist Authorization`), then decode it without verifying. The Runtime
  has already verified the token.
- CloudTrail logs the JWT `sub` for gateway JWT auth. Use a GUID or pairwise `sub`, never an
  email address.

## Workload identity and workload access tokens (WAT)

Runtime and Gateway create a workload identity automatically. It lives in the directory
`...:workload-identity-directory/default/workload-identity/<name>`. Create one yourself
(`CreateWorkloadIdentity`) only for self-hosted agents.

| API | Called by | Use when |
|---|---|---|
| `GetWorkloadAccessTokenForJWT` | Runtime or Gateway, automatically on JWT inbound. You call it for self-hosted agents | You have the user's JWT. It validates the signature, issuer and expiry, and binds the token to `iss`+`sub`. Production default |
| `GetWorkloadAccessTokenForUserId` | Runtime, when the User-Id header arrives with SigV4. You call it for self-hosted agents | You don't have a JWT. `userId` is an **opaque string** of 1–128 chars, never verified |
| `GetWorkloadAccessToken` | Self-hosted agents | Agent-only access (M2M, API keys) |

- The WAT reaches your code as the `WorkloadAccessToken` request header.
  `BedrockAgentCoreApp` places it in `BedrockAgentCoreContext`. A plain FastAPI app must read
  the header itself. The ContextVar is per-thread, so pass the token explicitly to worker
  threads (SDK #484).
- SigV4 invoke without the User-Id header delivers no WAT. The SDK decorators then raise
  "Workload access token has not been set ... specify the
  X-Amzn-Bedrock-AgentCore-Runtime-User-Id header".
- Service-managed identities can't mint their own WAT. The error is "WorkloadIdentity is
  linked to a service and cannot retrieve an access token by the caller". On the userId path
  with more than one IdP, prefix the IdP: `cognito+user123`.

## Pick the outbound credential

| Need | Provider and flow | SDK / Gateway setting |
|---|---|---|
| The agent's own access to an API (no user) | OAuth2 client credentials (2LO) | `auth_flow="M2M"` / `grantType: CLIENT_CREDENTIALS` |
| The user's own SaaS data (Google, GitHub, Slack) after consent | OAuth2 authorization code (3LO) with session binding | `USER_FEDERATION` / `AUTHORIZATION_CODE` + `defaultReturnUrl` |
| An API trusting the same IdP as inbound, acting as the user without consent | OBO token exchange | `ON_BEHALF_OF_TOKEN_EXCHANGE` / `TOKEN_EXCHANGE` |
| A static key | API key provider | `@requires_api_key` / `API_KEY` (header or query parameter) |
| No shared client secret | `PRIVATE_KEY_JWT` on `CustomOauth2`. KMS signs; RS256/PS256/ES256; key in the same Region | `privateKeyJwtConfig` |
| A secret you govern (rotation, CMK) | `clientSecretSource` / `apiKeySecretSource: EXTERNAL` + `{secretId, jsonKey}` | CLI: `--client-secret-reference` |
| An AWS-signed JWT for an OIDC-trusting service | STS `GetWebIdentityToken` | `@requires_iam_access_token(audience=[...])` |

```python
from bedrock_agentcore.identity.auth import requires_access_token, requires_api_key

@requires_access_token(provider_name="github-provider", scopes=["repo"],
    auth_flow="USER_FEDERATION", on_auth_url=stream_url_to_client,
    callback_url=CALLBACK_URL)   # your HTTPS route; must be in allowedResourceOauth2ReturnUrls
async def list_repos(*, access_token: str): ...

@requires_api_key(provider_name="tavily")
def search(q: str, *, api_key: str): ...
```

- Providers: `CreateOauth2CredentialProvider` (25 vendors; enum casing is `MicrosoftOauth2`,
  `CustomOauth2`) and `CreateApiKeyCredentialProvider`. Quota: 50 of each per account per Region.
  Leaked providers can exhaust it (CLI #1673).
- CLI: `agentcore add credentials oauth|api-key` (project). Secrets come from `file://` or
  `-` only; inline values are rejected. The deployed name is `<project>_<target>_<name>`, so
  read `AGENTCORE_CREDENTIAL_<NAME>_NAME` instead of hard-coding it.
- The token vault caches tokens per (workload identity, user). A JWT user (`iss`+`sub`) and
  the same person sent as a userId string are **different keys**.

### 3LO with session binding

1. Create the provider and register its unique `callbackUrl` at the IdP. The URL exists only
   after create, so create with placeholder credentials and update them later.
2. Add your app's callback page to the workload identity:
   `update-workload-identity --allowed-resource-oauth2-return-urls <your HTTPS callback>`.
   For a Runtime or Gateway, the identity name is the runtime or gateway ID.
3. The agent calls `GetResourceOauth2Token`. You get an `authorizationUrl` and a
   `sessionUri`. Send the URL to the user.
4. After consent, the browser lands on your callback with `session_id`. Check that the
   logged-in user is the one who started the flow. Then call
   `CompleteResourceTokenAuth(sessionUri, userIdentifier)`, passing that user's JWT or userId.
5. The agent retries or polls, and the token is now in the vault.

- The URL and session expire after **10 minutes**. Pass `customState` for CSRF. `agentcore dev` hosts no callback.
- No app of your own, or an IDE client that can't show URLs? Use the **consent portal**
  (Sep 2026, Gateway only). It needs a JWT-inbound gateway, an OIDC primary IdP that issues
  JWT access tokens and ID tokens, and a credential provider for the same issuer with
  `openid` permitted. Register `<portalUrl>/callback` with no trailing slash. Set each 3LO
  target's `defaultReturnUrl` to `<portalUrl>/connect/callback`. Existing targets need
  updating too. The connections list caches for 5 min. Users see the target and provider
  names. There is no Terraform resource (hashicorp #49882 open); use CloudFormation
  `AWS::BedrockAgentCore::ConsentPortal` or the CLI.

### On-behalf-of token exchange (GA 2026-04-30)

OBO exchanges the inbound user token for a new token with the audience of the downstream
API. The new token carries both the user and the agent identity. The IdP decides whether to
grant it.

- Prerequisites: JWT inbound (OBO uses the WAT from `GetWorkloadAccessTokenForJWT`), an OAuth2
  provider with `onBehalfOfTokenExchangeConfig`, and an IdP that permits the exchange. Without
  the config block, calls fail with "OnBehalfOfTokenExchangeConfig is required in the OBO
  flow".
- Modes:
  - `TOKEN_EXCHANGE` (RFC 8693, `subject_token` = the inbound JWT). `actorTokenContent` is
    `M2M` (a client-credentials token, optionally scoped by `actorTokenScopes`),
    `AWS_IAM_ID_TOKEN_JWT` (needs `iam:EnableOutboundWebIdentityFederation`) or `NONE`.
  - `JWT_AUTHORIZATION_GRANT` (RFC 7523 §2.1, jwt-bearer `assertion`).
- **Entra:** vendor `MicrosoftOauth2` comes preconfigured for `JWT_AUTHORIZATION_GRANT` and
  adds `requested_token_use=on_behalf_of`. Entra's own OBO rules apply: the inbound token's
  `aud` must be the app whose client ID the provider holds, and that app needs delegated
  permission to the downstream API.
- **Okta:** use `CustomOauth2`, `TOKEN_EXCHANGE`, `actorTokenContent: NONE`. Pass `audience`
  plus `subject_token_type` in `customParameters`.
- Gateway: target `grantType: TOKEN_EXCHANGE`. AWS recommends OBO over `JWT_PASSTHROUGH` in
  production. Runtime (HTTP) targets support client credentials, gateway role, caller IAM or
  passthrough, but not OBO or 3LO (`gateway.md`).
- Terraform: the provider-side `on_behalf_of_token_exchange_config` ships from 6.65.0. Check
  that your version's `gateway_target` accepts `TOKEN_EXCHANGE` (hashicorp #48486).

## Spoofing: the User-Id header and other caller-supplied identity

`X-Amzn-Bedrock-AgentCore-Runtime-User-Id` goes through `GetWorkloadAccessTokenForUserId`,
and AgentCore never verifies it. Any principal holding
`bedrock-agentcore:InvokeAgentRuntimeForUser` can name any user. It then receives that user's
cached 3LO tokens from the vault.

- Grant `InvokeAgentRuntimeForUser` only to a trusted backend, scoped to specific runtime
  ARNs. The backend must derive the value from the authenticated principal, never from a
  client field. Log principal → userId and alarm on `runtimeUserId` in CloudTrail.
- If JWTs are available, explicitly **Deny** `InvokeAgentRuntimeForUser` and
  `GetWorkloadAccessTokenForUserId`.
- `BedrockAgentCoreFullAccess` grants `GetWorkloadAccessTokenForUserId`. Never attach it to
  callers or to execution roles.
- The CLI `agentcore runtime invoke --user-id` defaults to `"default"`, so every tester shares
  one vault identity.
- The same trap appears elsewhere. Treat each of these as untrusted unless your code derives
  it from the validated token:
  - harness `actorId`, which the caller can override per invoke;
  - Memory `actorId` in your own code;
  - headers an interceptor forwards.

## Least-privilege IAM

- Runtimes created on or after 2025-10-13 use the SLR `AWSServiceRoleForBedrockAgentCoreRuntimeIdentity`
  for WAT minting. The deployer needs `iam:CreateServiceLinkedRole`. Older runtimes need
  `GetWorkloadAccessToken*` on the execution role.
- Execution role for outbound access. Scope every `Resource`; there's no extra binding
  between identity and provider:

```json
{"Effect": "Allow", "Action": ["bedrock-agentcore:GetResourceOauth2Token"],
 "Resource": ["arn:aws:bedrock-agentcore:REGION:ACCT:workload-identity-directory/default",
  "arn:aws:bedrock-agentcore:REGION:ACCT:workload-identity-directory/default/workload-identity/<name>",
  "arn:aws:bedrock-agentcore:REGION:ACCT:token-vault/default",
  "arn:aws:bedrock-agentcore:REGION:ACCT:token-vault/default/oauth2credentialprovider/<provider>"]}
```

  Do the same for `GetResourceApiKey` with `apikeycredentialprovider/<provider>`. Add
  `secretsmanager:GetSecretValue` on the provider's secret: the service-managed
  `secret:bedrock-agentcore-identity!*` or your `EXTERNAL` ARN (SDK #484). Without it, calls
  fail with AccessDenied.
- The CLI default role can invoke every model. Before prod, review
  `agentcore/cdk/cdk.out/`, then pass `--role-arn`.

## Common errors

| Symptom | Cause / fix |
|---|---|
| 403 `insufficient_scope` from an Entra token | `allowedScopes` or `allowedClients` is set (no `scope`/`client_id` claim to match). Use audience only, plus `customClaims` |
| 401 `invalid_token` / iss mismatch | v1 vs v2 discovery URL, or the wrong tenant. Decode and compare `iss` to the metadata `issuer` |
| "Authorization method mismatch (OAuth or SigV4)" | Bearer sent to a SigV4 Runtime, or an interceptor overwrote the SigV4 `Authorization` header |
| `ValidationException: Error parsing ClientCredentials response` | The IdP rejected the request, but AgentCore hides its error body (SDK #81). Test the token call with curl |
| 3LO fails with a generic `Invalid request` | AgentCore always uses PAR (SDK #111). Use an IdP with a PAR endpoint, or an intermediary |
| PKCE IdP rejects the code exchange | `code_verifier` is not sent (SDK #158, open) |
| Consent portal `invalid_scope`, or the callback is rejected | `openid` is not permitted on the IdP app, or the callback URL has a trailing slash |

## Checklist

- [ ] Inbound type chosen per caller. Any Runtime behind a gateway is locked to it.
- [ ] Entra: audience-only, correct `ver`, a `customClaims` rule on `azp` or `roles`, no
      `allowedClients` / `allowedScopes`.
- [ ] Per-user tools use JWT inbound. User-Id header and `ForUserId` denied where JWTs exist.
- [ ] Execution role scoped to its identity, providers, secrets; trust pinned with `SourceArn`.
